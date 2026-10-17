#!/usr/bin/env python3
"""Restart the model host's VS Code window before its renderer runs out of memory.

Meant for ``AHE_PRE_STAGE_COMMAND`` (AHE-026, runbook section 5v): the runner
runs it before every stage attempt, when none of the campaign's own model calls
is in flight. On 2026-10-16 the renderer of the ``copilot-host`` VS Code window
crashed with a V8 out-of-memory error after about 46 hours, which closed the GH
Copilot Server on port 3141 and blocked campaign 12.

The script measures the resident memory of that window's renderer process and
sends one small completion with one tool to ``--probe-model`` (tried twice).

- If the window has no renderer process (it crashed; seen on 2026-10-17 during
  a 2.7-hour evaluation that started at 2161 MB), restart at once.
- If the probe fails, the server is broken for every client: restart at once.
- Else, below ``--soft-mb`` (1500 MB since 2026-10-17, from 2500): exit 0.
- At or above it: wait up to ``--drain-seconds`` for other clients' connections
  to port 3141 to finish, then restart. If they do not finish, restart only at
  or above ``--hard-mb``; otherwise exit 0 and check again before the next stage.

A restart kills the window's main process (by the PID found below), reopens the
tmux window ``copilot-host:code`` with the command of
``~/copilot-host/start-copilot-host.sh`` and working directory
``~/copilot-host`` (VS Code must never inherit a folder that may be deleted:
its extension host then fails every request with "process.cwd failed"), waits
for Copilot Chat to load its models, runs "GH Copilot Server: Start Server"
with xdotool, and checks the listener, ``/v1/models`` and one small completion.
Any failed step exits non-zero, which the runner records as an infrastructure
error without using an attempt. Every decision is appended as one JSON line to
``--log``.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

HOST_DIR = Path.home() / "copilot-host"
CODE_EXE = HOST_DIR / "VSCode-linux-x64" / "code"
USER_DATA = HOST_DIR / "user-data"
SESSION = "copilot-host"
WINDOW = "code"
DISPLAY = ":99"
PORT = 3141
LAUNCH_COMMAND = (
    "env -u VSCODE_IPC_HOOK_CLI -u VSCODE_IPC_HOOK -u VSCODE_GIT_IPC_HANDLE "
    f"-u ELECTRON_RUN_AS_NODE DISPLAY={DISPLAY} {CODE_EXE} "
    f"--user-data-dir {USER_DATA} --extensions-dir {HOST_DIR / 'extensions'} "
    f"--password-store=basic --disable-gpu {HOST_DIR / 'workspace'} "
    f"2>&1 | tee -a {HOST_DIR / 'logs' / 'code.log'}"
)


class GuardError(RuntimeError):
    pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--soft-mb", type=int, default=1500)
    parser.add_argument("--hard-mb", type=int, default=3300)
    parser.add_argument("--drain-seconds", type=int, default=600)
    parser.add_argument("--probe-model", default="gpt-5.6-sol")
    parser.add_argument("--log", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="measure only")
    mode.add_argument("--force", action="store_true", help="restart now")
    args = parser.parse_args(argv)

    def log(event: str, **fields: object) -> None:
        row = {
            "time": datetime.now(UTC).isoformat(timespec="seconds"),
            "stage": os.environ.get("AHE_STAGE_NAME"),
            "event": event,
            **fields,
        }
        line = json.dumps(row, sort_keys=True)
        print(f"copilot-host guard: {line}", flush=True)
        args.log.parent.mkdir(parents=True, exist_ok=True)
        with args.log.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    try:
        # A crashed window leaves the main process without a renderer and
        # nothing on the port; restart it like an unhealthy one.
        main_pid, renderer_pid, renderer_mb = _measure(require_renderer=False)
        crashed = renderer_pid is None
        clients = _client_connections()
        state = {
            "main_pid": main_pid,
            "renderer_pid": renderer_pid,
            "renderer_rss_mb": renderer_mb,
            "clients": clients,
            "soft_mb": args.soft_mb,
            "hard_mb": args.hard_mb,
        }
        if args.check:
            log("ok" if not crashed else "renderer_missing", **state)
            return 0
        healthy_candidate = not args.force and not crashed
        probe_error = _probe_error(args.probe_model) if healthy_candidate else None
        if (
            probe_error is None
            and healthy_candidate
            and renderer_mb is not None
            and renderer_mb < args.soft_mb
        ):
            log("ok", **state, probe_model=args.probe_model)
            return 0
        if probe_error is None and healthy_candidate and renderer_mb is not None:
            deadline = time.monotonic() + args.drain_seconds
            while clients and time.monotonic() < deadline:
                time.sleep(10)
                clients = _client_connections()
            if clients and renderer_mb < args.hard_mb:
                log("deferred_clients_connected", **{**state, "clients": clients})
                return 0
        log(
            "restarting",
            **{**state, "clients": clients, "forced": args.force},
            probe_error=probe_error,
            renderer_missing=crashed,
        )
        started = time.monotonic()
        _restart(main_pid)
        new_main, new_renderer, new_mb = _measure()
        reply = _verify(args.probe_model)
        log(
            "restarted",
            seconds=round(time.monotonic() - started),
            main_pid=new_main,
            renderer_pid=new_renderer,
            renderer_rss_mb=new_mb,
            probe_model=reply,
        )
        return 0
    except GuardError as exc:
        log("failed", error=str(exc))
        return 1


def _processes() -> list[dict[str, object]]:
    rows = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdecimal():
            continue
        try:
            if entry.stat().st_uid != os.getuid():
                continue
            # Chromium rewrites child command lines as one space-separated
            # string, and sandboxed children hide /proc/<pid>/exe, so match on
            # the program path in argv[0].
            argv = (entry / "cmdline").read_bytes().replace(b"\0", b" ").split()
            if not argv or argv[0] != os.fsencode(CODE_EXE):
                continue
            rss_kb = 0
            for line in (entry / "status").read_text().splitlines():
                if line.startswith("VmRSS:"):
                    rss_kb = int(line.split()[1])
        except (OSError, ValueError):
            continue
        args = [part.decode(errors="replace") for part in argv if part]
        kind = next(
            (
                part.removeprefix("--type=")
                for part in args
                if part.startswith("--type=")
            ),
            None,
        )
        rows.append(
            {"pid": int(entry.name), "type": kind, "args": args, "rss_kb": rss_kb}
        )
    return rows


def _measure(*, require_renderer: bool = True) -> tuple[int, int | None, int | None]:
    rows = _processes()
    mains = [
        row for row in rows if row["type"] is None and str(USER_DATA) in row["args"]
    ]
    if len(mains) != 1:
        raise GuardError(
            f"expected one copilot-host VS Code main process, found {len(mains)}"
        )
    renderers = [row for row in rows if row["type"] == "renderer"]
    if not renderers:
        if not require_renderer:
            return int(mains[0]["pid"]), None, None
        raise GuardError("copilot-host VS Code has no renderer process")
    largest = max(renderers, key=lambda row: int(row["rss_kb"]))
    return int(mains[0]["pid"]), int(largest["pid"]), int(largest["rss_kb"]) // 1024


def _client_connections() -> int:
    output = _run(["ss", "-Htn", "state", "established", f"( sport = :{PORT} )"])
    return len([line for line in output.splitlines() if line.strip()])


def _restart(main_pid: int) -> None:
    before = {path.name for path in (USER_DATA / "logs").iterdir()}
    os.kill(main_pid, signal.SIGKILL)
    _wait(lambda: not _processes(), 60, "old VS Code processes did not exit")
    _wait(lambda: _listener_exe() is None, 30, f"port {PORT} is still in use")
    _wait(
        lambda: (
            WINDOW
            not in _run(
                ["tmux", "list-windows", "-t", SESSION, "-F", "#{window_name}"]
            ).split()
        ),
        30,
        f"tmux window {SESSION}:{WINDOW} did not close",
    )
    _run(
        [
            "tmux",
            "new-window",
            "-d",
            "-c",
            str(HOST_DIR),
            "-t",
            f"{SESSION}:1",
            "-n",
            WINDOW,
            LAUNCH_COMMAND,
        ]
    )

    def chat_log() -> Path | None:
        new = sorted({path.name for path in (USER_DATA / "logs").iterdir()} - before)
        if not new:
            return None
        return (
            USER_DATA
            / "logs"
            / new[-1]
            / "window1"
            / "exthost"
            / "GitHub.copilot-chat"
            / "GitHub Copilot Chat.log"
        )

    def models_loaded() -> bool:
        path = chat_log()
        return bool(
            path
            and path.is_file()
            and "Fetched model metadata" in path.read_text(errors="replace")
        )

    _wait(models_loaded, 240, "Copilot Chat did not load its models")
    time.sleep(5)
    environment = {**os.environ, "DISPLAY": DISPLAY}
    window = ""

    def find_window() -> bool:
        nonlocal window
        found = subprocess.run(
            ["xdotool", "search", "--onlyvisible", "--name", "Visual Studio Code"],
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        ).stdout.split()
        window = found[0] if found else ""
        return bool(window)

    _wait(find_window, 60, "VS Code window not found on the virtual display")
    for _attempt in range(3):
        for command in (
            ["xdotool", "windowactivate", "--sync", window],
            ["xdotool", "key", "--window", window, "Escape", "F1"],
            ["sleep", "1.5"],
            ["xdotool", "type", "--delay", "40", "GH Copilot Server: Start Server"],
            ["sleep", "1.5"],
            ["xdotool", "key", "Return"],
        ):
            # windowactivate warns about _NET_ACTIVE_WINDOW under bare Xvfb but works.
            subprocess.run(command, env=environment, capture_output=True, check=False)
        try:
            _wait(lambda: _listener_exe() == CODE_EXE, 30, "")
            return
        except GuardError:
            continue
    raise GuardError(f"GH Copilot Server did not start listening on port {PORT}")


def _listener_exe() -> Path | None:
    output = _run(["ss", "-Hltnp", f"( sport = :{PORT} )"])
    for part in output.split("pid=")[1:]:
        pid = part.split(",", 1)[0]
        try:
            return Path(os.readlink(f"/proc/{pid}/exe"))
        except OSError:
            return Path("unknown")
    return None


def _probe_error(model: str) -> str | None:
    """Return None if the endpoint answers a small completion, else the error."""
    error = None
    for _ in range(2):
        try:
            _verify(model, timeout=90)
            return None
        except (GuardError, OSError, ValueError) as exc:
            error = str(exc)[:300]
    return error


def _verify(model: str, timeout: float = 300) -> str:
    base = f"http://127.0.0.1:{PORT}/v1"
    with urllib.request.urlopen(f"{base}/models", timeout=30) as response:
        ids = {row["id"] for row in json.load(response).get("data", [])}
    if model not in ids:
        raise GuardError(
            f"{model} is not in /v1/models after restart ({len(ids)} models)"
        )
    body = json.dumps(
        {
            "model": model,
            "messages": [{"role": "user", "content": "Reply with OK only."}],
            # Requests with tools take a different path in the extension; on
            # 2026-10-16 only they failed ("process.cwd failed").
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "noop",
                        "description": "Does nothing.",
                        "parameters": {
                            "type": "object",
                            "properties": {},
                            "additionalProperties": False,
                        },
                    },
                }
            ],
            "reasoning": {"effort": "low"},
            "max_tokens": 256,
            "stream": False,
        }
    ).encode()
    request = urllib.request.Request(
        f"{base}/chat/completions",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            reply = json.load(response)
    except OSError as exc:
        raise GuardError(f"probe completion failed: {exc}") from exc
    if reply.get("model") != model:
        raise GuardError(f"probe answered as {reply.get('model')!r}, not {model!r}")
    return model


def _wait(condition, seconds: float, message: str) -> None:  # type: ignore[no-untyped-def]
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(2)
    raise GuardError(message or "timed out")


def _run(command: list[str]) -> str:
    try:
        return subprocess.run(
            command, capture_output=True, text=True, check=True, timeout=60
        ).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        raise GuardError(f"{command[0]} failed: {exc}") from exc


if __name__ == "__main__":
    sys.exit(main())
