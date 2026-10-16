"""Hash-verified, attempt-preserving campaign stages."""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
import traceback
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .errors import (
    ConfigurationError,
    InfrastructureError,
    IntegrityError,
    StageBlockedError,
    StageInterruptedError,
)
from .io import (
    atomic_write_json,
    path_fingerprint,
    read_json,
    sha256_json,
    verify_path_fingerprint,
)


@dataclass(frozen=True, slots=True)
class StageContext:
    name: str
    stage_id: str
    stage_dir: Path
    attempt: int
    attempt_dir: Path
    previous_attempt_dir: Path | None


@dataclass(frozen=True, slots=True)
class StageResult:
    outputs: tuple[Path, ...]
    data: dict[str, Any]


class StageStore:
    def __init__(
        self,
        *,
        campaign_dir: Path,
        maximum_attempts: int,
        pre_stage_command: str | None = None,
        pre_stage_timeout_seconds: float = 1200.0,
    ) -> None:
        self.campaign_dir = campaign_dir.resolve()
        self.maximum_attempts = maximum_attempts
        self.pre_stage_command = pre_stage_command
        self.pre_stage_timeout_seconds = pre_stage_timeout_seconds
        self.root = self.campaign_dir / "stages"

    def run(
        self,
        *,
        name: str,
        semantic_inputs: Mapping[str, Any],
        action: Callable[[StageContext], StageResult],
        retryable: bool = True,
    ) -> StageResult:
        stage_dir = self.root / _stage_directory_name(name)
        stage_path = stage_dir / "stage.json"
        input_payload = dict(semantic_inputs)
        input_sha256 = sha256_json(input_payload)
        stage_id = sha256_json({"name": name, "semantic_inputs_sha256": input_sha256})[
            :24
        ]
        stage = self._load_or_create(
            stage_path=stage_path,
            name=name,
            stage_id=stage_id,
            semantic_inputs=input_payload,
            semantic_inputs_sha256=input_sha256,
        )
        if stage["status"] == "completed":
            for fingerprint in stage["outputs"]:
                verify_path_fingerprint(
                    fingerprint,
                    relative_to=self.campaign_dir,
                )
            return StageResult(
                outputs=tuple(
                    self.campaign_dir / str(row["path"]) for row in stage["outputs"]
                ),
                data=dict(stage.get("data") or {}),
            )
        if stage["status"] == "blocked":
            raise StageBlockedError(
                name,
                str(stage.get("last_error") or "stage is blocked"),
            )

        attempts = list(stage.get("attempts") or [])
        if attempts and attempts[-1].get("status") == "running":
            stale = dict(attempts[-1])
            stale["status"] = "interrupted"
            stale["completed_at"] = datetime.now(UTC).isoformat()
            stale["error"] = {
                "type": "AbandonedStageAttempt",
                "message": (
                    "The prior process ended before recording stage completion."
                ),
            }
            attempts[-1] = stale
            stage["attempts"] = attempts
            stage["status"] = "interrupted"
            atomic_write_json(stage_path, stage)

        attempt = len(attempts) + 1
        allowed_attempts = self._allowed_attempts(stage)
        if attempt > allowed_attempts:
            stage["status"] = "blocked"
            stage["last_error"] = f"stage exhausted {allowed_attempts} attempts"
            atomic_write_json(stage_path, stage)
            raise StageBlockedError(name, stage["last_error"])
        self._run_pre_stage_command(name)

        attempt_dir = stage_dir / "attempts" / f"{attempt:04d}"
        attempt_dir.mkdir(parents=True, exist_ok=False)
        previous_attempt_dir = (
            stage_dir / "attempts" / f"{attempt - 1:04d}" if attempt > 1 else None
        )
        attempt_record = {
            "attempt": attempt,
            "status": "running",
            "started_at": datetime.now(UTC).isoformat(),
            "attempt_dir": str(attempt_dir.relative_to(self.campaign_dir)),
        }
        if self.pre_stage_command:
            attempt_record["pre_stage_command"] = self.pre_stage_command
        attempts.append(attempt_record)
        stage["attempts"] = attempts
        stage["retryable"] = retryable
        stage["status"] = "running"
        stage["updated_at"] = datetime.now(UTC).isoformat()
        atomic_write_json(stage_path, stage)
        atomic_write_json(attempt_dir / "attempt.json", attempt_record)

        context = StageContext(
            name=name,
            stage_id=stage_id,
            stage_dir=stage_dir,
            attempt=attempt,
            attempt_dir=attempt_dir,
            previous_attempt_dir=previous_attempt_dir,
        )
        try:
            result = action(context)
            outputs = [
                path_fingerprint(path, relative_to=self.campaign_dir)
                for path in result.outputs
            ]
        except Exception as exc:
            error = {
                "type": type(exc).__name__,
                "message": str(exc),
                "traceback": traceback.format_exc(),
            }
            atomic_write_json(attempt_dir / "error.json", error)
            attempts = list(stage["attempts"])
            attempts[-1] = {
                **attempts[-1],
                "status": "infrastructure_failed"
                if isinstance(exc, InfrastructureError)
                else "failed",
                "completed_at": datetime.now(UTC).isoformat(),
                "error": error,
            }
            stage["attempts"] = attempts
            stage["last_error"] = error
            can_retry = (
                retryable
                and isinstance(exc, InfrastructureError)
                and attempt < allowed_attempts
            )
            stage["status"] = "interrupted" if can_retry else "blocked"
            stage["updated_at"] = datetime.now(UTC).isoformat()
            atomic_write_json(stage_path, stage)
            atomic_write_json(attempt_dir / "attempt.json", attempts[-1])
            if can_retry:
                raise StageInterruptedError(
                    name,
                    attempt,
                    (
                        f"stage {name} attempt {attempt} failed due to "
                        f"infrastructure: {exc}. Run resume to continue."
                    ),
                ) from exc
            raise StageBlockedError(name, str(exc)) from exc

        completed_attempt = {
            **stage["attempts"][-1],
            "status": "completed",
            "completed_at": datetime.now(UTC).isoformat(),
            "outputs": outputs,
            "data": result.data,
        }
        stage["attempts"][-1] = completed_attempt
        stage["status"] = "completed"
        stage["completed_at"] = datetime.now(UTC).isoformat()
        stage["updated_at"] = stage["completed_at"]
        stage["outputs"] = outputs
        stage["data"] = result.data
        atomic_write_json(attempt_dir / "attempt.json", completed_attempt)
        atomic_write_json(stage_path, stage)
        return result

    def blocked_retry_plan(self, name: str) -> dict[str, Any]:
        """Check that a blocked stage may be retried and describe the retry.

        Only a retryable stage whose every attempt ended in an infrastructure
        failure (or was abandoned by a killed process) qualifies. A stage with
        any completed attempt or any other failure is refused, so valid
        results are never rerun.
        """
        stage_path = self._stage_path(name)
        if not stage_path.is_file():
            raise ConfigurationError(f"no stage named {name!r} in this campaign")
        stage = read_json(stage_path)
        if stage.get("status") != "blocked":
            raise ConfigurationError(
                f"stage {name} is {stage.get('status')!r}, not 'blocked'"
            )
        attempts = list(stage.get("attempts") or [])
        if not attempts:
            raise IntegrityError(f"blocked stage {name} has no attempts")
        refused = [
            int(row.get("attempt", index))
            for index, row in enumerate(attempts, start=1)
            if not _failed_on_infrastructure(row)
        ]
        if refused:
            raise IntegrityError(
                f"stage {name} attempts {refused} did not fail on infrastructure; "
                "only infrastructure-blocked stages can be retried"
            )
        retryable = stage.get("retryable")
        if retryable is None:
            # Records written before `retryable` was stored: an earlier attempt
            # that failed on infrastructure without blocking the stage shows
            # the runner treated it as retryable.
            retryable = any(
                row.get("status") == "infrastructure_failed" for row in attempts[:-1]
            )
        if not retryable:
            raise IntegrityError(f"stage {name} is not retryable by design")
        last_error = stage.get("last_error")
        return {
            "stage": name,
            "attempts": len(attempts),
            "allowed_attempts": self._allowed_attempts(stage),
            "attempt_statuses": [row.get("status") for row in attempts],
            "last_error_type": last_error.get("type")
            if isinstance(last_error, dict)
            else None,
        }

    def reopen_blocked(
        self,
        name: str,
        *,
        allowed_attempts: int,
        override: Mapping[str, Any],
    ) -> None:
        """Mark a blocked stage interrupted with a larger attempt allowance."""
        plan = self.blocked_retry_plan(name)
        if allowed_attempts <= plan["attempts"]:
            raise ConfigurationError(
                f"stage {name} already has {plan['attempts']} attempts; the new "
                f"allowance must be larger, not {allowed_attempts}"
            )
        stage_path = self._stage_path(name)
        stage = read_json(stage_path)
        stage["operator_overrides"] = [
            *list(stage.get("operator_overrides") or []),
            {
                **dict(override),
                "previous_stage_state": "blocked",
                "previous_allowed_attempts": plan["allowed_attempts"],
                "allowed_attempts": allowed_attempts,
                "attempts_before": plan["attempts"],
            },
        ]
        stage["attempt_allowance"] = allowed_attempts
        stage["status"] = "interrupted"
        stage["updated_at"] = datetime.now(UTC).isoformat()
        atomic_write_json(stage_path, stage)

    def status(self) -> list[dict[str, Any]]:
        if not self.root.is_dir():
            return []
        rows: list[dict[str, Any]] = []
        for path in sorted(self.root.glob("*/stage.json")):
            rows.append(read_json(path))
        return rows

    def _stage_path(self, name: str) -> Path:
        return self.root / _stage_directory_name(name) / "stage.json"

    def _allowed_attempts(self, stage: Mapping[str, Any]) -> int:
        return max(self.maximum_attempts, int(stage.get("attempt_allowance") or 0))

    def _run_pre_stage_command(self, name: str) -> None:
        """Run the operator's pre-stage command; a failure consumes no attempt."""
        if not self.pre_stage_command:
            return
        environment = {
            **os.environ,
            "AHE_STAGE_NAME": name,
            "AHE_CAMPAIGN_DIR": str(self.campaign_dir),
        }
        try:
            completed = subprocess.run(
                shlex.split(self.pre_stage_command),
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=sys.stderr,
                stderr=sys.stderr,
                timeout=self.pre_stage_timeout_seconds,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise InfrastructureError(
                f"pre-stage command failed before stage {name}: {exc}"
            ) from exc
        if completed.returncode != 0:
            raise InfrastructureError(
                f"pre-stage command exited {completed.returncode} before stage {name}"
            )

    def _load_or_create(
        self,
        *,
        stage_path: Path,
        name: str,
        stage_id: str,
        semantic_inputs: dict[str, Any],
        semantic_inputs_sha256: str,
    ) -> dict[str, Any]:
        if stage_path.is_file():
            stage = read_json(stage_path)
            expected = {
                "name": name,
                "stage_id": stage_id,
                "semantic_inputs": semantic_inputs,
                "semantic_inputs_sha256": semantic_inputs_sha256,
            }
            actual = {key: stage.get(key) for key in expected}
            if actual != expected:
                raise IntegrityError(
                    f"stage semantic inputs changed after creation: {name}"
                )
            return stage
        stage_path.parent.mkdir(parents=True, exist_ok=True)
        stage = {
            "schema_version": 1,
            "name": name,
            "stage_id": stage_id,
            "semantic_inputs": semantic_inputs,
            "semantic_inputs_sha256": semantic_inputs_sha256,
            "status": "pending",
            "created_at": datetime.now(UTC).isoformat(),
            "updated_at": datetime.now(UTC).isoformat(),
            "attempts": [],
            "outputs": [],
            "data": {},
        }
        atomic_write_json(stage_path, stage)
        return stage


def _failed_on_infrastructure(attempt: Mapping[str, Any]) -> bool:
    if attempt.get("status") == "infrastructure_failed":
        return True
    error = attempt.get("error")
    return (
        attempt.get("status") == "interrupted"
        and isinstance(error, dict)
        and error.get("type") == "AbandonedStageAttempt"
    )


def _stage_directory_name(name: str) -> str:
    normalized = "".join(
        character if character.isalnum() or character in "._-" else "-"
        for character in name
    )
    if not normalized:
        raise ValueError("stage name is empty")
    return normalized
