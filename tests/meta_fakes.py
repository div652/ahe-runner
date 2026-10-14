"""Small stand-ins for NexAU runtime objects used by meta-tool tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from nexau.archs.sandbox.local_sandbox import LocalSandbox


class FakeStorage:
    def __init__(self) -> None:
        self.values: dict[str, object] = {}

    def get(self, key: str, default: Any = None) -> Any:
        return self.values.get(key, default)

    def set(self, key: str, value: object) -> None:
        self.values[key] = value


class FakeAgentState:
    """Enough of ``AgentState`` for the guarded tools and the recorder."""

    def __init__(
        self,
        context: dict[str, object],
        *,
        work_dir: Path | None = None,
    ) -> None:
        self.agent_id = "meta-test"
        self.agent_name = "meta-test"
        self.global_storage = FakeStorage()
        self.context = context
        self._sandbox = LocalSandbox(
            sandbox_id="meta-test",
            work_dir=str(work_dir) if work_dir is not None else None,
        )

    def get_context_value(self, key: str) -> Any:
        return self.context.get(key)

    def get_sandbox(self) -> LocalSandbox:
        return self._sandbox
