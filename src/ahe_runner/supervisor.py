"""Unattended resume loop for campaigns that stop on infrastructure (AHE-038).

Each resume runs ``ahe run`` in a fresh process, so process-level resources
such as request slots and spawned-trial budgets are always restored. Only an
infrastructure stop is resumed. Blocked stages, configuration and integrity
errors, and provider blocks end the loop for an operator.
"""

from __future__ import annotations

import json
import random
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import load_config
from .errors import PROVIDER_BLOCKED_MARKER, ConfigurationError
from .io import read_json

RESUMABLE_ERRORS = frozenset({"StageInterruptedError", "InfrastructureError"})
_STDERR_TAIL_CHARACTERS = 65536


def supervise(
    config_path: Path,
    *,
    max_consecutive_failures: int = 5,
    initial_backoff_seconds: float = 60.0,
    max_backoff_seconds: float = 1800.0,
    sleep: Callable[[float], None] = time.sleep,
    launch: Callable[[list[str]], tuple[int, str, str]] | None = None,
) -> dict[str, Any]:
    """Run a campaign to completion, resuming after infrastructure stops."""

    if max_consecutive_failures < 1:
        raise ConfigurationError("max_consecutive_failures must be at least 1")
    if not 0 < initial_backoff_seconds <= max_backoff_seconds:
        raise ConfigurationError(
            "backoff must satisfy 0 < initial_backoff_seconds <= max_backoff_seconds"
        )
    config = load_config(config_path)
    log_path = config.campaign_dir / "reports" / "supervisor.jsonl"
    command = [
        sys.executable,
        "-m",
        "ahe_runner",
        "run",
        "--config",
        str(config.config_path),
    ]
    launch = launch or _launch
    failures = 0
    launches = 0
    while True:
        before = _completed_stage_count(config.campaign_dir)
        started = datetime.now(UTC).isoformat()
        returncode, stdout, stderr = launch(command)
        launches += 1
        error = _last_json_object(stderr)
        after = _completed_stage_count(config.campaign_dir)
        progressed = after > before
        failures = 0 if progressed else failures
        record: dict[str, Any] = {
            "started_at": started,
            "completed_at": datetime.now(UTC).isoformat(),
            "launch": launches,
            "returncode": returncode,
            "completed_stages_before": before,
            "completed_stages_after": after,
            "error_type": (error or {}).get("type"),
            "error": (error or {}).get("message"),
        }
        if returncode == 0:
            record["decision"] = "completed"
            _append(log_path, record)
            status = _last_json_object(stdout) or {}
            return {"status": "completed", "launches": launches, "campaign": status}
        message = str((error or {}).get("message") or stderr[-2000:])
        if PROVIDER_BLOCKED_MARKER in message:
            decision = "stop_provider_block"
        elif error is not None and error.get("type") not in RESUMABLE_ERRORS:
            decision = "stop_operator_required"
        else:
            # A known infrastructure stop, or a process that died without its
            # error report (for example killed): resume recovers both.
            failures += 1
            decision = (
                "stop_repeated_failures"
                if failures >= max_consecutive_failures
                else "resume"
            )
        record["decision"] = decision
        record["consecutive_failures"] = failures
        if decision != "resume":
            _append(log_path, record)
            return {
                "status": decision,
                "launches": launches,
                "consecutive_failures": failures,
                "error_type": record["error_type"],
                "error": message,
                "log": str(log_path),
            }
        base = min(max_backoff_seconds, initial_backoff_seconds * 2 ** (failures - 1))
        delay = base + random.uniform(0.0, base * 0.25)
        record["backoff_seconds"] = round(delay, 3)
        _append(log_path, record)
        sleep(delay)


def _launch(command: list[str]) -> tuple[int, str, str]:
    """Run one campaign process; stream its stderr while keeping the tail.

    Standard output goes to a temporary file, not a pipe: the final status can
    exceed a pipe buffer, and the child must never block on it while stderr
    is being read.
    """

    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as stdout:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=subprocess.PIPE,
            text=True,
        )
        assert process.stderr is not None
        tail = ""
        for line in process.stderr:
            sys.stderr.write(line)
            tail = (tail + line)[-_STDERR_TAIL_CHARACTERS:]
        returncode = process.wait()
        stdout.seek(0)
        return returncode, stdout.read(), tail


def _last_json_object(text: str) -> dict[str, Any] | None:
    """The last top-level JSON object the CLI printed, if any."""

    lines = text.splitlines()
    for index in range(len(lines) - 1, -1, -1):
        if lines[index] == "{":
            try:
                value = json.loads("\n".join(lines[index:]))
            except ValueError:
                continue
            return value if isinstance(value, dict) else None
    return None


def _completed_stage_count(campaign_dir: Path) -> int:
    root = campaign_dir / "stages"
    if not root.is_dir():
        return 0
    count = 0
    for path in root.glob("*/stage.json"):
        try:
            count += read_json(path).get("status") == "completed"
        except (OSError, ValueError):
            continue
    return count


def _append(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")
