"""Runner-owned NexAU runtime for evaluated V2 benchmark sessions."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Literal

from nexau import Agent
from nexau.archs.main_sub.config import AgentConfig
from nexau.archs.main_sub.execution.hooks import (
    AfterModelHookInput,
    HookResult,
    Middleware,
    ToolCallFn,
    ToolCallParams,
)
from nexau.archs.main_sub.execution.parse_structures import ParsedResponse
from nexau.archs.tool import Tool
from nexau.core.messages import Message, Role, TextBlock, ToolResultBlock
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .adapter import BenchmarkInterface, BenchmarkToolSpec
from .config import ModelSettings, TransportSettings
from .errors import ConfigurationError, IntegrityError
from .harness import HarnessManifest, matched_component_paths
from .harness_imports import HarnessImportScope
from .io import atomic_write_json, tree_sha256
from .meta.model_client import RecordedExactModelClient


class RuntimeModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AgentSessionRequest(RuntimeModel):
    """Create one isolated evaluated-agent trial."""

    session_id: str
    artifact_dir: Path
    working_directory: Path | None = None
    benchmark_tools: tuple[BenchmarkToolSpec, ...] = ()
    context: dict[str, Any] = Field(default_factory=dict)

    @field_validator("session_id")
    @classmethod
    def validate_session_id(cls, value: str) -> str:
        if not value or value in {".", ".."}:
            raise ValueError("session_id must be non-empty")
        if any(character in value for character in ("/", "\\", "\x00")):
            raise ValueError("session_id must be a single path-safe segment")
        return value


class AgentInputMessage(RuntimeModel):
    """One user message or protected benchmark action result."""

    role: Literal["user", "tool"] = "user"
    content: Any
    tool_call_id: str | None = None
    is_error: bool = False

    @model_validator(mode="after")
    def validate_role_fields(self) -> AgentInputMessage:
        if self.role == "tool" and not self.tool_call_id:
            raise ValueError("tool messages require tool_call_id")
        if self.role == "user" and self.tool_call_id is not None:
            raise ValueError("user messages cannot set tool_call_id")
        if self.role == "user" and self.is_error:
            raise ValueError("is_error is valid only for tool messages")
        return self


class AgentInput(RuntimeModel):
    messages: tuple[AgentInputMessage, ...]
    context: dict[str, Any] = Field(default_factory=dict)

    @field_validator("messages")
    @classmethod
    def validate_messages(
        cls,
        value: tuple[AgentInputMessage, ...],
    ) -> tuple[AgentInputMessage, ...]:
        if not value:
            raise ValueError("messages must contain at least one message")
        return value


class BenchmarkActionCall(RuntimeModel):
    """Protected structured call that the adapter must execute."""

    tool_call_id: str
    name: str
    arguments: dict[str, Any]


class AgentOutput(RuntimeModel):
    final_text: str
    external_actions: tuple[BenchmarkActionCall, ...] = ()
    messages: tuple[dict[str, Any], ...]
    trace: tuple[dict[str, Any], ...]


class EvaluatedAgentSession:
    """One stateful NexAU Agent instance and its preserved history."""

    def __init__(
        self,
        *,
        agent: Agent,
        client: RecordedExactModelClient,
        boundary: _ProtectedBenchmarkBoundary,
        base_context: Mapping[str, Any],
        session_id: str,
        artifact_dir: Path,
        on_close: Callable[[str], None],
    ) -> None:
        self._agent = agent
        self._client = client
        self._boundary = boundary
        self._base_context = dict(base_context)
        self._session_id = session_id
        self._artifact_dir = artifact_dir
        self._on_close = on_close
        self._outstanding_actions: dict[str, BenchmarkActionCall] = {}
        self._closed = False

    @property
    def session_id(self) -> str:
        return self._session_id

    def run(self, request: AgentInput) -> AgentOutput:
        if self._closed:
            raise RuntimeError(f"session {self._session_id!r} is closed")
        messages = self._validated_messages(request.messages)
        context = {**self._base_context, **request.context}
        try:
            result = self._agent.run(
                message=messages,
                context=context,
                custom_llm_client_provider=lambda _agent_name: self._client,
            )
        except Exception:
            if self._boundary.violation is not None:
                raise IntegrityError(self._boundary.violation) from None
            raise
        if self._boundary.violation is not None:
            raise IntegrityError(self._boundary.violation)

        actions = self._boundary.take_actions()
        self._outstanding_actions = {
            action.tool_call_id: action for action in actions
        }
        output = AgentOutput(
            final_text=str(result),
            external_actions=actions,
            messages=tuple(_model_dump(message) for message in self._agent.history),
            trace=tuple(_model_dump(message) for message in self._agent.full_trace),
        )
        atomic_write_json(
            self._artifact_dir / "latest_output.json",
            output.model_dump(mode="json"),
        )
        return output

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._agent.sync_cleanup()
        finally:
            self._on_close(self._session_id)

    def _validated_messages(
        self,
        messages: Sequence[AgentInputMessage],
    ) -> list[Message]:
        tool_messages = [message for message in messages if message.role == "tool"]
        user_messages = [message for message in messages if message.role == "user"]
        if self._outstanding_actions:
            expected_ids = set(self._outstanding_actions)
            provided_ids = {
                str(message.tool_call_id)
                for message in tool_messages
                if message.tool_call_id is not None
            }
            if len(provided_ids) != len(tool_messages):
                raise IntegrityError("duplicate benchmark action result IDs")
            if provided_ids != expected_ids:
                raise IntegrityError(
                    "benchmark action results must exactly match outstanding call IDs; "
                    f"expected {sorted(expected_ids)}, received {sorted(provided_ids)}"
                )
            if user_messages:
                raise IntegrityError(
                    "resolve all outstanding benchmark actions before sending user input"
                )
            self._outstanding_actions.clear()
        elif tool_messages:
            raise IntegrityError("received a benchmark action result with no pending call")

        result: list[Message] = []
        for message in messages:
            if message.role == "user":
                result.append(Message.user(_text_content(message.content)))
            else:
                result.append(
                    Message(
                        role=Role.TOOL,
                        content=[
                            ToolResultBlock(
                                tool_use_id=str(message.tool_call_id),
                                content=_text_content(message.content),
                                is_error=message.is_error,
                            )
                        ],
                    )
                )
        return result


class EvaluatedAgentRuntime:
    """Factory for isolated evaluated-agent sessions from one harness checkpoint."""

    def __init__(
        self,
        *,
        harness_dir: Path,
        manifest: HarnessManifest,
        benchmark_interface: BenchmarkInterface,
        model: ModelSettings,
        transport: TransportSettings,
        api_key: str,
        artifact_root: Path,
    ) -> None:
        self._harness_dir = harness_dir.resolve()
        self._manifest = manifest
        self._benchmark_interface = benchmark_interface
        self._model = model
        self._transport = transport
        self._api_key = api_key
        self._artifact_root = artifact_root.resolve()
        self._artifact_root.mkdir(parents=True, exist_ok=True)
        self._sessions: dict[str, EvaluatedAgentSession] = {}
        self._closed = False
        self._import_scope = HarnessImportScope(self._harness_dir)
        self._import_scope.__enter__()

    def open_session(self, request: AgentSessionRequest) -> EvaluatedAgentSession:
        if self._closed:
            raise RuntimeError("evaluated-agent runtime is closed")
        if request.session_id in self._sessions:
            raise ConfigurationError(
                f"session_id {request.session_id!r} is already open"
            )
        artifact_dir = request.artifact_dir.resolve()
        try:
            artifact_dir.relative_to(self._artifact_root)
        except ValueError as exc:
            raise IntegrityError(
                f"session artifact directory escapes evaluation root: {artifact_dir}"
            ) from exc
        artifact_dir.mkdir(parents=True, exist_ok=True)
        self._validate_benchmark_tools(request.benchmark_tools)

        config = AgentConfig.from_yaml(self._harness_dir / "agent.yaml")
        _override_model_settings(
            config,
            model=self._model,
            transport=self._transport,
            api_key=self._api_key,
        )
        _attach_long_term_memory(
            config,
            harness_dir=self._harness_dir,
            manifest=self._manifest,
        )
        if request.benchmark_tools and config.tool_call_mode != "openai":
            raise ConfigurationError(
                "protected benchmark tools require NexAU tool_call_mode 'openai' "
                "so schemas stay in the structured API tools field"
            )
        boundary = _ProtectedBenchmarkBoundary(request.benchmark_tools)
        harness_tool_names = {tool.name for tool in config.tools}
        benchmark_tool_names = {tool.name for tool in request.benchmark_tools}
        collisions = sorted(harness_tool_names & benchmark_tool_names)
        if collisions:
            raise ConfigurationError(
                "protected benchmark tool names collide with harness tools: "
                + ", ".join(collisions)
            )
        config.tools.extend(_runtime_tools(request.benchmark_tools))
        config.middlewares = [boundary, *(config.middlewares or [])]

        client = RecordedExactModelClient(
            transport=self._transport,
            api_key=self._api_key,
            settings=self._model,
            log_dir=artifact_dir / "model_calls",
        )
        agent = Agent(config=config)
        base_context = dict(request.context)
        if request.working_directory is not None:
            base_context["working_directory"] = str(
                request.working_directory.resolve()
            )
        session = EvaluatedAgentSession(
            agent=agent,
            client=client,
            boundary=boundary,
            base_context=base_context,
            session_id=request.session_id,
            artifact_dir=artifact_dir,
            on_close=self._sessions.pop,
        )
        self._sessions[request.session_id] = session
        atomic_write_json(
            artifact_dir / "runtime_manifest.json",
            {
                "session_id": request.session_id,
                "harness_sha256": tree_sha256(self._harness_dir),
                "model": self._model.model_dump(mode="json"),
                "transport": {
                    "api_mode": self._transport.api_mode,
                    "stream": self._transport.stream,
                    "observable_model_policy": (
                        self._transport.observable_model_policy
                    ),
                },
                "benchmark_tools": [
                    tool.model_dump(mode="json") for tool in request.benchmark_tools
                ],
                "runtime_overlay": True,
            },
        )
        return session

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            for session in list(self._sessions.values()):
                session.close()
        finally:
            self._import_scope.__exit__(None, None, None)

    def _validate_benchmark_tools(
        self,
        tools: Sequence[BenchmarkToolSpec],
    ) -> None:
        requested = {tool.name: tool for tool in tools}
        if len(requested) != len(tools):
            raise ConfigurationError("benchmark tool names must be unique per session")
        declared = {
            tool.name: tool for tool in self._benchmark_interface.protected_tools
        }
        if self._benchmark_interface.tool_delivery != "structured" and requested:
            raise ConfigurationError(
                "this benchmark interface does not declare structured tools"
            )
        unknown = sorted(set(requested) - set(declared))
        if unknown:
            raise ConfigurationError(
                "session requested undeclared protected benchmark tools: "
                + ", ".join(unknown)
            )
        mismatched = sorted(
            name for name, tool in requested.items() if tool != declared[name]
        )
        if mismatched:
            raise ConfigurationError(
                "session benchmark tool declarations differ from the frozen "
                "benchmark interface: "
                + ", ".join(mismatched)
            )


class _ProtectedBenchmarkBoundary(Middleware):
    """Capture protected calls after candidate middleware and before execution."""

    def __init__(self, tools: Sequence[BenchmarkToolSpec]) -> None:
        self.name = "ahe_protected_benchmark_boundary"
        self._tool_names = frozenset(tool.name for tool in tools)
        self._actions: tuple[BenchmarkActionCall, ...] = ()
        self.violation: str | None = None

    def after_model(self, hook_input: AfterModelHookInput) -> HookResult:
        parsed = hook_input.parsed_response
        if parsed is None:
            return HookResult.no_changes()
        protected_calls = [
            call for call in parsed.tool_calls if call.tool_name in self._tool_names
        ]
        if not protected_calls:
            return HookResult.no_changes()
        other_calls = [
            call for call in parsed.tool_calls if call.tool_name not in self._tool_names
        ]
        if other_calls or parsed.sub_agent_calls or parsed.batch_agent_calls:
            self.violation = (
                "a model response mixed protected benchmark actions with harness "
                "tools or sub-agent calls; mixed execution is prohibited"
            )
            return HookResult.with_modifications(
                parsed_response=_without_calls(parsed)
            )
        try:
            actions = tuple(
                BenchmarkActionCall(
                    tool_call_id=str(call.tool_call_id),
                    name=call.tool_name,
                    arguments=dict(call.parameters),
                )
                for call in protected_calls
            )
        except Exception as exc:
            self.violation = f"invalid protected benchmark action: {exc}"
            return HookResult.with_modifications(
                parsed_response=_without_calls(parsed)
            )
        call_ids = [action.tool_call_id for action in actions]
        if len(call_ids) != len(set(call_ids)):
            self.violation = "protected benchmark action call IDs must be unique"
            return HookResult.with_modifications(
                parsed_response=_without_calls(parsed)
            )
        self._actions = actions
        return HookResult.with_modifications(parsed_response=_without_calls(parsed))

    def wrap_tool_call(
        self,
        params: ToolCallParams,
        call_next: ToolCallFn,
    ) -> Any:
        if params.tool_name in self._tool_names:
            self.violation = (
                f"protected benchmark tool {params.tool_name!r} reached the NexAU "
                "tool executor"
            )
            raise IntegrityError(self.violation)
        return call_next(params)

    def take_actions(self) -> tuple[BenchmarkActionCall, ...]:
        actions = self._actions
        self._actions = ()
        return actions


def _runtime_tools(tools: Sequence[BenchmarkToolSpec]) -> list[Tool]:
    return [
        Tool(
            name=tool.name,
            description=tool.description,
            input_schema=tool.input_schema,
            implementation=_blocked_runtime_tool,
        )
        for tool in tools
    ]


def _blocked_runtime_tool(**_kwargs: Any) -> str:
    raise IntegrityError(
        "protected benchmark actions must be executed by the benchmark adapter"
    )


def _without_calls(parsed: ParsedResponse) -> ParsedResponse:
    return ParsedResponse(
        original_response=parsed.original_response,
        tool_calls=[],
        sub_agent_calls=[],
        batch_agent_calls=[],
        is_parallel_tools=parsed.is_parallel_tools,
        is_parallel_sub_agents=parsed.is_parallel_sub_agents,
        model_response=parsed.model_response,
    )


def _override_model_settings(
    config: AgentConfig,
    *,
    model: ModelSettings,
    transport: TransportSettings,
    api_key: str,
) -> None:
    if config.llm_config is None:
        raise ConfigurationError(f"agent {config.name!r} has no llm_config")
    updates: dict[str, Any] = {
        "model": model.model,
        "base_url": transport.base_url,
        "api_key": api_key,
        "max_tokens": model.max_tokens,
        "temperature": model.temperature,
        "stream": False,
        "api_type": "openai_chat_completion",
    }
    if model.reasoning_effort is not None:
        updates["reasoning"] = {"effort": model.reasoning_effort}
    config.llm_config.update(**updates)
    for sub_agent in (config.sub_agents or {}).values():
        _override_model_settings(
            sub_agent,
            model=model,
            transport=transport,
            api_key=api_key,
        )


def _attach_long_term_memory(
    config: AgentConfig,
    *,
    harness_dir: Path,
    manifest: HarnessManifest,
) -> None:
    if (
        manifest.schema_version == 2
        and (
            manifest.attachments is None
            or manifest.attachments.long_term_memory != "system_prompt_suffix"
        )
    ):
        raise ConfigurationError(
            "V2 long-term memory requires system_prompt_suffix attachment"
        )
    paths = matched_component_paths(
        harness_dir,
        manifest.components.long_term_memory,
    )
    if not paths:
        return
    sections: list[str] = []
    for path in paths:
        relative_path = path.relative_to(harness_dir).as_posix()
        sections.append(
            f"## Persistent harness memory: {relative_path}\n\n"
            f"{path.read_text(encoding='utf-8').strip()}"
        )
    memory = "\n\n".join(sections).strip()
    if not memory:
        return
    existing = (config.system_prompt_suffix or "").strip()
    config.system_prompt_suffix = "\n\n".join(
        section
        for section in (
            existing,
            "# Persistent harness memory\n\n" + memory,
        )
        if section
    )


def _text_content(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, sort_keys=True, default=str)


def _model_dump(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        payload = value.model_dump(mode="json")
        if isinstance(payload, dict):
            return payload
    raise TypeError(f"expected a serializable NexAU message, got {type(value)!r}")
