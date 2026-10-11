from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar

import pytest
from openai.types.chat import ChatCompletion

import ahe_runner.runtime as runtime_module
from ahe_runner.adapter import BenchmarkInterface, BenchmarkToolSpec
from ahe_runner.config import load_config
from ahe_runner.errors import IntegrityError
from ahe_runner.harness import load_harness_manifest
from ahe_runner.runtime import (
    AgentInput,
    AgentInputMessage,
    AgentSessionRequest,
    EvaluatedAgentRuntime,
)

from .conftest import make_example_project


class FakeRecordedClient:
    scenarios: ClassVar[list[list[ChatCompletion]]] = []
    instances: ClassVar[list[FakeRecordedClient]] = []

    def __init__(self, **_kwargs: Any) -> None:
        self.responses = self.scenarios.pop(0)
        self.calls: list[dict[str, Any]] = []
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self._create)
        )
        self.instances.append(self)

    def _create(self, **kwargs: Any) -> ChatCompletion:
        self.calls.append(kwargs)
        if not self.responses:
            raise AssertionError("fake model response queue is empty")
        return self.responses.pop(0)


def test_runtime_intercepts_protected_actions_and_preserves_sessions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = make_example_project(tmp_path)
    config = load_config(config_path)
    manifest = load_harness_manifest(config.harness_manifest_path)
    protected_tool = BenchmarkToolSpec(
        name="lookup_account",
        description="Read one account from the protected benchmark environment.",
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {"account_id": {"type": "string"}},
            "required": ["account_id"],
        },
    )
    interface = BenchmarkInterface(
        description="Protected account lookup benchmark.",
        input_kinds=("prompt", "environment"),
        tool_delivery="structured",
        protected_tools=(protected_tool,),
    )
    FakeRecordedClient.instances = []
    FakeRecordedClient.scenarios = [
        [
            _completion(
                model=config.document.models.evaluated.model,
                tool_calls=[
                    _tool_call(
                        "lookup_account",
                        {"account_id": "A-17"},
                        "call-protected",
                    )
                ],
            ),
            _completion(
                model=config.document.models.evaluated.model,
                content="The protected result was accepted.",
            ),
        ],
        [
            _completion(
                model=config.document.models.evaluated.model,
                tool_calls=[
                    _tool_call(
                        "record_note",
                        {"note": "independent internal note"},
                        "call-internal",
                    )
                ],
            ),
            _completion(
                model=config.document.models.evaluated.model,
                content="Internal tool completed.",
            ),
        ],
        [
            _completion(
                model=config.document.models.evaluated.model,
                tool_calls=[
                    _tool_call(
                        "lookup_account",
                        {"account_id": "A-99"},
                        "call-mixed-protected",
                    ),
                    _tool_call(
                        "record_note",
                        {"note": "must not run"},
                        "call-mixed-internal",
                    ),
                ],
            )
        ],
    ]
    monkeypatch.setattr(
        runtime_module,
        "RecordedExactModelClient",
        FakeRecordedClient,
    )

    artifact_root = tmp_path / "runtime-artifacts"
    runtime = EvaluatedAgentRuntime(
        harness_dir=config.harness_dir,
        manifest=manifest,
        benchmark_interface=interface,
        model=config.document.models.evaluated,
        transport=config.document.transport,
        api_key=config.api_key,
        artifact_root=artifact_root,
    )
    try:
        protected_session = runtime.open_session(
            AgentSessionRequest(
                session_id="protected",
                artifact_dir=artifact_root / "protected",
                benchmark_tools=(protected_tool,),
            )
        )
        root_config = protected_session._agent.config
        assert root_config.llm_config.model == config.document.models.evaluated.model
        assert {
            sub_agent.llm_config.model
            for sub_agent in root_config.sub_agents.values()
        } == {config.document.models.evaluated.model}

        first = protected_session.run(
            AgentInput(
                messages=(
                    AgentInputMessage(
                        role="user",
                        content="Look up account A-17.",
                    ),
                )
            )
        )
        assert first.final_text == ""
        assert [action.model_dump() for action in first.external_actions] == [
            {
                "tool_call_id": "call-protected",
                "name": "lookup_account",
                "arguments": {"account_id": "A-17"},
            }
        ]
        first_call = FakeRecordedClient.instances[0].calls[0]
        tool_names = {
            tool["function"]["name"] for tool in first_call["tools"]
        }
        assert "lookup_account" in tool_names
        serialized_messages = json.dumps(first_call["messages"])
        assert "lookup_account" not in serialized_messages
        assert "Record only" in serialized_messages

        with pytest.raises(IntegrityError, match="exactly match"):
            protected_session.run(
                AgentInput(
                    messages=(
                        AgentInputMessage(
                            role="tool",
                            tool_call_id="wrong-call-id",
                            content={"status": "ok"},
                        ),
                    )
                )
            )

        second = protected_session.run(
            AgentInput(
                messages=(
                    AgentInputMessage(
                        role="tool",
                        tool_call_id="call-protected",
                        content={"account_id": "A-17", "status": "active"},
                    ),
                )
            )
        )
        assert second.final_text == "The protected result was accepted."
        second_call_messages = FakeRecordedClient.instances[0].calls[1][
            "messages"
        ]
        assert any(
            message.get("role") == "tool"
            and message.get("tool_call_id") == "call-protected"
            for message in second_call_messages
        )
        protected_session.close()

        internal_session = runtime.open_session(
            AgentSessionRequest(
                session_id="internal",
                artifact_dir=artifact_root / "internal",
                benchmark_tools=(protected_tool,),
            )
        )
        internal = internal_session.run(
            AgentInput(
                messages=(
                    AgentInputMessage(
                        role="user",
                        content="Record an independent note.",
                    ),
                )
            )
        )
        assert internal.final_text == "Internal tool completed."
        assert internal.external_actions == ()
        internal_messages = json.dumps(FakeRecordedClient.instances[1].calls)
        assert "Look up account A-17" not in internal_messages
        assert "independent internal note" in internal_messages
        internal_session.close()

        mixed_session = runtime.open_session(
            AgentSessionRequest(
                session_id="mixed",
                artifact_dir=artifact_root / "mixed",
                benchmark_tools=(protected_tool,),
            )
        )
        with pytest.raises(IntegrityError, match="mixed protected"):
            mixed_session.run(
                AgentInput(
                    messages=(
                        AgentInputMessage(
                            role="user",
                            content="Attempt an invalid mixed call.",
                        ),
                    )
                )
            )
        mixed_session.close()
    finally:
        runtime.close()


def _completion(
    *,
    model: str,
    content: str = "",
    tool_calls: list[dict[str, Any]] | None = None,
) -> ChatCompletion:
    return ChatCompletion.model_validate(
        {
            "id": "completion-test",
            "object": "chat.completion",
            "created": 1,
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "tool_calls" if tool_calls else "stop",
                    "message": {
                        "role": "assistant",
                        "content": content,
                        "tool_calls": tool_calls,
                    },
                }
            ],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
            },
        }
    )


def _tool_call(
    name: str,
    arguments: dict[str, Any],
    call_id: str,
) -> dict[str, Any]:
    return {
        "id": call_id,
        "type": "function",
        "function": {
            "name": name,
            "arguments": json.dumps(arguments),
        },
    }
