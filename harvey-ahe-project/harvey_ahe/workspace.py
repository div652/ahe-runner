"""Per-session link from evaluated harness tools to their LAB sandbox.

The adapter starts one LAB podman sandbox per trial, wraps it in a narrow
:class:`WorkspaceHandle`, and binds that handle to the trial's own session
through ``AgentSessionRequest.session_resources`` under ``RESOURCE_KEY``
(AHE-005, AHE-006). The runtime stores it in that session Agent's NexAU
GlobalStorage, which the session's sub-agents share and no other session can
reach. There is no process-wide lookup table. Harness tools obtain the handle
with :func:`handle_for`. This is the only protected module that harness code
may import.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Protocol

from lab_core.sandbox.sandbox import (
    DOCUMENTS_PATH,
    OUTPUT_PATH,
    WORKSPACE_PATH,
    ExecResult,
    Sandbox,
)

RESOURCE_KEY = "harvey_workspace"
MAX_EXEC_TIMEOUT_SECONDS = 600

__all__ = [
    "DOCUMENTS_PATH",
    "MAX_EXEC_TIMEOUT_SECONDS",
    "OUTPUT_PATH",
    "RESOURCE_KEY",
    "WORKSPACE_PATH",
    "ExecResult",
    "WorkspaceHandle",
    "handle_for",
]


class SandboxLike(Protocol):
    documents_dir: Path
    output_dir: Path
    workspace_dir: Path

    def exec(
        self,
        command: str,
        *,
        cwd: str = ...,
        timeout: int | None = ...,
        env: dict[str, str] | None = ...,
    ) -> ExecResult: ...

    def read_file(self, path: str) -> bytes: ...

    def write_file(self, path: str, content: bytes | str) -> None: ...

    def exists(self, path: str) -> bool: ...

    def _to_host(self, sb_path: str) -> Path: ...


class WorkspaceHandle:
    """Narrow per-trial view of one LAB sandbox for harness tool code."""

    DOCUMENTS_PATH = DOCUMENTS_PATH
    OUTPUT_PATH = OUTPUT_PATH
    WORKSPACE_PATH = WORKSPACE_PATH

    def __init__(self, sandbox: SandboxLike, *, shell_timeout: int) -> None:
        if shell_timeout < 1 or shell_timeout > MAX_EXEC_TIMEOUT_SECONDS:
            raise ValueError(
                f"shell_timeout must be between 1 and {MAX_EXEC_TIMEOUT_SECONDS}"
            )
        self._sandbox: SandboxLike | None = sandbox
        self._lock = threading.Lock()
        self._finished = False
        self._finish_summary: str | None = None
        self.shell_timeout = shell_timeout

    def exec(
        self,
        command: str,
        *,
        cwd: str = WORKSPACE_PATH,
        timeout: int | None = None,
    ) -> ExecResult:
        """Run ``bash -lc command`` in a fresh ``podman exec`` (no shell state)."""
        effective = self.shell_timeout if timeout is None else timeout
        if (
            isinstance(effective, bool)
            or not isinstance(effective, int)
            or not 1 <= effective <= MAX_EXEC_TIMEOUT_SECONDS
        ):
            raise ValueError(
                f"timeout must be an integer between 1 and {MAX_EXEC_TIMEOUT_SECONDS}"
            )
        return self._active().exec(command, cwd=cwd, timeout=effective)

    def read_file(self, path: str) -> bytes:
        return self._active().read_file(path)

    def write_file(self, path: str, content: bytes | str) -> None:
        self._active().write_file(path, content)

    def exists(self, path: str) -> bool:
        return self._active().exists(path)

    def to_host(self, path: str) -> Path:
        """Host path behind a sandbox path; rejects mount escapes."""
        return self._active()._to_host(path)

    def revoke(self) -> None:
        """Detach the sandbox; later tool calls fail instead of reaching it."""
        with self._lock:
            self._sandbox = None

    @property
    def revoked(self) -> bool:
        with self._lock:
            return self._sandbox is None

    def _active(self) -> SandboxLike:
        with self._lock:
            sandbox = self._sandbox
        if sandbox is None:
            raise LookupError("the Harvey workspace for this trial is closed")
        return sandbox

    @staticmethod
    def assert_sandbox_path(path: str) -> None:
        Sandbox.assert_sandbox_path(path)

    @staticmethod
    def is_writable(path: str) -> bool:
        return Sandbox.is_writable(path)

    def mark_finished(self, summary: str | None) -> None:
        """Latch an accepted ``finish`` call (telemetry only; never graded)."""
        with self._lock:
            self._finished = True
            self._finish_summary = summary or None

    @property
    def finished(self) -> bool:
        with self._lock:
            return self._finished

    @property
    def finish_summary(self) -> str | None:
        with self._lock:
            return self._finish_summary


def handle_for(agent_state: Any) -> WorkspaceHandle:
    """Return the workspace bound to the calling NexAU session."""
    if agent_state is None:
        raise LookupError("NexAU did not provide agent_state to the tool")
    handle = agent_state.get_global_value(RESOURCE_KEY)
    if not isinstance(handle, WorkspaceHandle):
        raise LookupError(
            f"this session has no {RESOURCE_KEY!r} resource; "
            "the Harvey workspace is unavailable"
        )
    return handle
