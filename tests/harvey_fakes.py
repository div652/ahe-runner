"""Offline fakes shared by the Harvey adapter, seed-tool, and runtime tests.

``FakeSandbox`` subclasses LAB's real ``Sandbox`` so path rules, read-only
documents, and host-file access are LAB's own code; only podman (``start``,
``stop``, ``exec``) is replaced.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import lab_core
from lab_core.sandbox.sandbox import WORKSPACE_PATH, ExecResult, PodmanError, Sandbox

PROJECT_ROOT = Path(__file__).resolve().parents[1] / "harvey-ahe-project"
HARNESS_DIR = PROJECT_ROOT / "harness"
LAB_ROOT = Path(lab_core.__file__).resolve().parents[1]


@dataclass(frozen=True, slots=True)
class ExecCall:
    command: str
    cwd: str
    timeout: int | None


ExecHandler = Callable[["FakeSandbox", ExecCall], ExecResult]


def default_exec(_sandbox: FakeSandbox, call: ExecCall) -> ExecResult:
    if call.command == "true":
        return ExecResult(stdout="", stderr="", returncode=0)
    return ExecResult(
        stdout="",
        stderr=f"fake sandbox has no handler for {call.command!r}",
        returncode=127,
    )


class FakeSandbox(Sandbox):
    def __init__(
        self,
        documents_dir: Path | str,
        output_dir: Path | str,
        workspace_dir: Path | str,
        *,
        exec_handler: ExecHandler = default_exec,
        **kwargs: Any,
    ) -> None:
        super().__init__(documents_dir, output_dir, workspace_dir, **kwargs)
        self.exec_handler = exec_handler
        self.calls: list[ExecCall] = []
        self.events: list[str] = []

    def start(self) -> None:
        for directory in (self.documents_dir, self.output_dir, self.workspace_dir):
            directory.mkdir(parents=True, exist_ok=True)
        self._started = True
        self.events.append("start")

    def stop(self) -> None:
        self._started = False
        self.events.append("stop")

    def exec(
        self,
        command: str,
        *,
        cwd: str = WORKSPACE_PATH,
        timeout: int | None = None,
        env: dict[str, str] | None = None,
    ) -> ExecResult:
        if not self._started:
            raise PodmanError("fake sandbox is not running")
        call = ExecCall(command=command, cwd=cwd, timeout=timeout)
        self.calls.append(call)
        return self.exec_handler(self, call)


def started_sandbox(
    root: Path,
    *,
    exec_handler: ExecHandler = default_exec,
    documents: dict[str, str] | None = None,
) -> FakeSandbox:
    """A running fake sandbox over ``root/{documents,output,workspace}``."""
    for name, text in (documents or {}).items():
        path = root / "documents" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    sandbox = FakeSandbox(
        root / "documents",
        root / "output",
        root / "workspace",
        exec_handler=exec_handler,
    )
    sandbox.start()
    return sandbox


class FakeAgentState:
    """The two NexAU ``AgentState`` methods the seed tools use."""

    def __init__(self, values: dict[str, Any] | None = None) -> None:
        self.values = dict(values or {})

    def get_global_value(self, key: str, default: Any = None) -> Any:
        return self.values.get(key, default)

    def set_global_value(self, key: str, value: Any) -> None:
        self.values[key] = value
