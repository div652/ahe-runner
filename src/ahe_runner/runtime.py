"""Runner-owned NexAU runtime for evaluated V2 benchmark sessions."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from nexau import Agent
from nexau.archs.main_sub.agent_context import GlobalStorage
from nexau.archs.main_sub.config import AgentConfig
from nexau.archs.main_sub.context_value import ContextValue
from nexau.archs.main_sub.execution.hooks import (
    AfterModelHookInput,
    FunctionMiddleware,
    HookResult,
    Middleware,
    ToolCallFn,
    ToolCallParams,
)
from nexau.archs.main_sub.execution.parse_structures import ParsedResponse
from nexau.archs.sandbox import LocalSandboxConfig
from nexau.archs.tool import Tool
from nexau.core.messages import Message, Role, ToolResultBlock
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .adapter import BenchmarkInterface, BenchmarkToolSpec
from .config import ModelSettings, TransportSettings
from .errors import ConfigurationError, InfrastructureError, IntegrityError
from .harness import HarnessManifest, matched_component_paths
from .harness_imports import HarnessImportScope
from .io import atomic_write_json, sha256_file, tree_sha256
from .loading import load_symbol
from .meta.model_client import RecordedExactModelClient
from .meta.preflight import run_model_preflight

_NEXAU_RESERVED_STORAGE_KEYS = frozenset(
    {"tracer", "skill_registry", "parallel_execution_id"}
)


class RuntimeModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AgentSessionRequest(RuntimeModel):
    """Create one isolated evaluated-agent trial."""

    session_id: str
    artifact_dir: Path
    working_directory: Path | None = None
    benchmark_tools: tuple[BenchmarkToolSpec, ...] = ()
    context: dict[str, str] = Field(default_factory=dict)
    # Adapter-owned objects bound to this session only. They are placed in the
    # session Agent's own NexAU GlobalStorage, which its sub-agents share and no
    # other session can reach. Unlike `context`, routing never passes through
    # NexAU's process-wide current-context slot.
    session_resources: dict[str, Any] = Field(default_factory=dict)

    @field_validator("session_resources")
    @classmethod
    def validate_session_resources(cls, value: dict[str, Any]) -> dict[str, Any]:
        for key in value:
            if not key.isidentifier() or key.startswith("_"):
                raise ValueError(
                    "session resource keys must be public Python identifiers"
                )
            if key in _NEXAU_RESERVED_STORAGE_KEYS:
                raise ValueError(f"session resource key {key!r} is reserved by NexAU")
        return value

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
    context: dict[str, str] = Field(default_factory=dict)

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


class ToolTiming(RuntimeModel):
    sequence: int = Field(gt=0)
    session_id: str
    agent_name: str
    tool_name: str
    tool_call_id: str
    started_at: str
    completed_at: str
    wall_time_seconds: float = Field(ge=0)
    status: Literal["completed", "error"]
    error_type: str | None = None
    parallel_execution_id: str | None = None


class AgentOutput(RuntimeModel):
    final_text: str
    external_actions: tuple[BenchmarkActionCall, ...] = ()
    messages: tuple[dict[str, Any], ...]
    trace: tuple[dict[str, Any], ...]
    tool_timings: tuple[ToolTiming, ...] = ()


class EvaluatedAgentSession:
    """One stateful NexAU Agent instance and its preserved history."""

    def __init__(
        self,
        *,
        agent: Agent,
        client: RecordedExactModelClient,
        boundary: _ProtectedBenchmarkBoundary,
        tool_timing: _ToolTimingRecorder,
        base_context: Mapping[str, Any],
        session_id: str,
        artifact_dir: Path,
        on_close: Callable[[str], None],
    ) -> None:
        self._agent = agent
        self._client = client
        self._boundary = boundary
        self._tool_timing = tool_timing
        self._base_context = dict(base_context)
        self._session_id = session_id
        self._artifact_dir = artifact_dir
        self._on_close = on_close
        self._outstanding_actions: dict[str, BenchmarkActionCall] = {}
        self._last_tool_timing_sequence = 0
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
                variables=ContextValue(template=context),
                custom_llm_client_provider=lambda _agent_name: self._client,
            )
        except Exception:
            if self._boundary.violation is not None:
                raise IntegrityError(self._boundary.violation) from None
            raise
        if self._boundary.violation is not None:
            raise IntegrityError(self._boundary.violation)

        actions = self._boundary.take_actions()
        self._outstanding_actions = {action.tool_call_id: action for action in actions}
        tool_timings = self._tool_timing.after(self._last_tool_timing_sequence)
        if tool_timings:
            self._last_tool_timing_sequence = max(
                event.sequence for event in tool_timings
            )
        output = AgentOutput(
            final_text=str(result),
            external_actions=actions,
            messages=tuple(_model_dump(message) for message in self._agent.history),
            trace=tuple(_model_dump(message) for message in self._agent.full_trace),
            tool_timings=tool_timings,
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
                    "resolve all outstanding benchmark actions before sending "
                    "user input"
                )
            self._outstanding_actions.clear()
        elif tool_messages:
            raise IntegrityError(
                "received a benchmark action result with no pending call"
            )

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


class RuntimeProcessSpec(RuntimeModel):
    """Ephemeral recipe for rebuilding a runtime in a spawned trial process."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    harness_dir: Path
    manifest: HarnessManifest
    benchmark_interface: BenchmarkInterface
    model: ModelSettings
    transport: TransportSettings
    api_key: str = Field(repr=False, exclude=True)
    artifact_root: Path
    project_root: Path
    model_preflight_path: Path
    parent_runtime_pid: int


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
        project_root: Path | None = None,
        model_preflight_path: Path | None = None,
        parent_runtime_pid: int | None = None,
    ) -> None:
        self._harness_dir = harness_dir.resolve()
        self._manifest = manifest
        self._benchmark_interface = benchmark_interface
        self._model = model
        self._transport = transport
        self._api_key = api_key
        self._artifact_root = artifact_root.resolve()
        self._project_root = (
            project_root.resolve()
            if project_root is not None
            else self._harness_dir.parent
        )
        self._artifact_root.mkdir(parents=True, exist_ok=True)
        self._sessions: dict[str, EvaluatedAgentSession] = {}
        self._closed = False
        self._preflight_lock = threading.Lock()
        self._preflight_path: Path | None = None
        self._preflight_error: Exception | None = None
        self._parent_runtime_pid = parent_runtime_pid
        if model_preflight_path is not None:
            path = model_preflight_path.resolve()
            try:
                path.relative_to(self._artifact_root / "model-preflights")
            except ValueError as exc:
                raise IntegrityError(
                    f"model preflight is outside this evaluation: {path}"
                ) from exc
            if not path.is_file():
                raise IntegrityError(f"model preflight is missing: {path}")
            self._preflight_path = path
        self._import_scope = HarnessImportScope(self._harness_dir)
        self._import_scope.__enter__()

    @classmethod
    def from_process_spec(cls, spec: RuntimeProcessSpec) -> EvaluatedAgentRuntime:
        return cls(
            harness_dir=spec.harness_dir,
            manifest=spec.manifest,
            benchmark_interface=spec.benchmark_interface,
            model=spec.model,
            transport=spec.transport,
            api_key=spec.api_key,
            artifact_root=spec.artifact_root,
            project_root=spec.project_root,
            model_preflight_path=spec.model_preflight_path,
            parent_runtime_pid=spec.parent_runtime_pid,
        )

    def process_spec(self) -> RuntimeProcessSpec:
        """Run one parent preflight and return an ephemeral child recipe."""

        if self._closed:
            raise RuntimeError("evaluated-agent runtime is closed")
        return RuntimeProcessSpec(
            harness_dir=self._harness_dir,
            manifest=self._manifest,
            benchmark_interface=self._benchmark_interface,
            model=self._model,
            transport=self._transport,
            api_key=self._api_key,
            artifact_root=self._artifact_root,
            project_root=self._project_root,
            model_preflight_path=self._ensure_model_preflight(),
            parent_runtime_pid=os.getpid(),
        )

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
        working_directory = _session_working_directory(
            request.working_directory,
            artifact_dir,
        )
        self._validate_benchmark_tools(request.benchmark_tools)

        context_policy = self._manifest.context_accounting
        if context_policy is not None and context_policy.token_counter is not None:
            token_counter = load_symbol(
                context_policy.token_counter,
                project_root=self._project_root,
            )
            if not callable(token_counter):
                raise ConfigurationError(
                    "context_accounting.token_counter must resolve to a callable"
                )
        config = AgentConfig.from_yaml(self._harness_dir / self._manifest.agent_config)
        _override_model_settings(
            config,
            model=self._model,
            transport=self._transport,
            api_key=self._api_key,
            working_directory=working_directory,
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
        tool_timing = _ToolTimingRecorder(
            path=artifact_dir / "tool_timings.json",
            session_id=request.session_id,
        )
        harness_tool_names = {tool.name for tool in config.tools}
        benchmark_tool_names = {tool.name for tool in request.benchmark_tools}
        collisions = sorted(harness_tool_names & benchmark_tool_names)
        if collisions:
            raise ConfigurationError(
                "protected benchmark tool names collide with harness tools: "
                + ", ".join(collisions)
            )
        config.tools.extend(_runtime_tools(request.benchmark_tools))
        candidate_middlewares = [
            _CandidateMiddlewareGuard(middleware, boundary)
            for middleware in (config.middlewares or [])
        ]
        legacy_after_model_middlewares = [
            _CandidateMiddlewareGuard(
                FunctionMiddleware(
                    after_model_hook=hook,
                    name=f"after_model::{_hook_name(hook)}",
                ),
                boundary,
            )
            for hook in (config.after_model_hooks or [])
        ]
        config.after_model_hooks = []
        _attach_tool_timing_to_subagents(config, tool_timing)
        config.middlewares = [
            tool_timing,
            boundary,
            *candidate_middlewares,
            *legacy_after_model_middlewares,
            _ProtectedActionCapture(boundary),
        ]

        preflight_path = self._ensure_model_preflight()
        client = RecordedExactModelClient(
            transport=self._transport,
            api_key=self._api_key,
            settings=self._model,
            log_dir=artifact_dir / "model_calls",
        )
        if request.session_resources:
            storage = GlobalStorage()
            storage.update(dict(request.session_resources))
            agent = Agent(config=config, global_storage=storage)
            for key, value in request.session_resources.items():
                if agent.global_storage.get(key) is not value:
                    raise IntegrityError(
                        f"session resource {key!r} was replaced while the agent "
                        "was initialized"
                    )
        else:
            agent = Agent(config=config)
        base_context = {
            **request.context,
            "working_directory": str(working_directory),
        }
        session = EvaluatedAgentSession(
            agent=agent,
            client=client,
            boundary=boundary,
            tool_timing=tool_timing,
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
                "working_directory": str(working_directory),
                "harness_sha256": tree_sha256(self._harness_dir),
                "model": self._model.model_dump(mode="json"),
                "transport": {
                    "api_mode": self._transport.api_mode,
                    "stream": self._transport.stream,
                    "request_timeout_seconds": (
                        self._transport.request_timeout_seconds
                    ),
                    "max_concurrent_requests": (
                        self._transport.max_concurrent_requests
                    ),
                    "hung_call_retries": self._transport.hung_call_retries,
                    "observable_model_policy": (
                        self._transport.observable_model_policy
                    ),
                },
                "context_accounting": (
                    self._manifest.context_accounting.model_dump(mode="json")
                    if self._manifest.context_accounting is not None
                    else None
                ),
                "execution_process": {
                    "mode": (
                        "spawned_child"
                        if self._parent_runtime_pid is not None
                        else "in_process"
                    ),
                    "pid": os.getpid(),
                    "parent_runtime_pid": self._parent_runtime_pid,
                },
                "tool_timing": {
                    "schema_version": 1,
                    "path": "tool_timings.json",
                },
                "benchmark_tools": [
                    tool.model_dump(mode="json") for tool in request.benchmark_tools
                ],
                "runtime_overlay": True,
                "model_preflight": {
                    "path": preflight_path.relative_to(self._artifact_root).as_posix(),
                    "sha256": sha256_file(preflight_path),
                },
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

    def _ensure_model_preflight(self) -> Path:
        with self._preflight_lock:
            if self._preflight_error is not None:
                raise self._preflight_error
            if self._preflight_path is not None:
                return self._preflight_path
            try:
                preflight_root = self._artifact_root / "model-preflights"
                preflight_root.mkdir(parents=True, exist_ok=True)
                if preflight_root.is_symlink():
                    raise IntegrityError("model preflight directory is a symlink")
                attempt_dir = Path(
                    tempfile.mkdtemp(prefix="attempt-", dir=preflight_root)
                )
                preflight_path = attempt_dir / "model-preflight.json"
                run_model_preflight(
                    transport=self._transport,
                    api_key=self._api_key,
                    settings=self._model,
                    output_path=preflight_path,
                )
            except Exception as exc:
                # Share failures too, so concurrent sessions cannot retry the probe.
                self._preflight_error = exc
                raise
            self._preflight_path = preflight_path
            return preflight_path

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
                "benchmark interface: " + ", ".join(mismatched)
            )


class _ProtectedBenchmarkBoundary(Middleware):
    """Capture protected calls and block them from NexAU tool execution."""

    def __init__(self, tools: Sequence[BenchmarkToolSpec]) -> None:
        self.name = "ahe_protected_benchmark_boundary"
        self._tool_names = frozenset(tool.name for tool in tools)
        self._actions: tuple[BenchmarkActionCall, ...] = ()
        self._intercepted = False
        self.violation: str | None = None

    def capture(self, hook_input: AfterModelHookInput) -> HookResult:
        self._intercepted = False
        parsed = hook_input.parsed_response
        if parsed is None:
            return HookResult.no_changes()
        protected_calls = [
            call for call in parsed.tool_calls if call.tool_name in self._tool_names
        ]
        if not protected_calls:
            return HookResult.no_changes()
        self._intercepted = True
        other_calls = [
            call for call in parsed.tool_calls if call.tool_name not in self._tool_names
        ]
        if other_calls or parsed.sub_agent_calls or parsed.batch_agent_calls:
            self.violation = (
                "a model response mixed protected benchmark actions with harness "
                "tools or sub-agent calls; mixed execution is prohibited"
            )
            return HookResult.with_modifications(parsed_response=_without_calls(parsed))
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
            return HookResult.with_modifications(parsed_response=_without_calls(parsed))
        call_ids = [action.tool_call_id for action in actions]
        if len(call_ids) != len(set(call_ids)):
            self.violation = "protected benchmark action call IDs must be unique"
            return HookResult.with_modifications(parsed_response=_without_calls(parsed))
        self._actions = actions
        return HookResult.with_modifications(parsed_response=_without_calls(parsed))

    @property
    def intercepted(self) -> bool:
        return self._intercepted

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


class _ProtectedActionCapture(Middleware):
    """Run before candidate after-model hooks and establish a trusted yield."""

    def __init__(self, boundary: _ProtectedBenchmarkBoundary) -> None:
        self.name = "ahe_protected_action_capture"
        self._boundary = boundary

    def after_model(self, hook_input: AfterModelHookInput) -> HookResult:
        return self._boundary.capture(hook_input)


class _ToolTimingRecorder(Middleware):
    """Protected wall-time evidence for every harness-owned tool call."""

    def __init__(self, *, path: Path, session_id: str) -> None:
        self.name = "ahe_runtime_tool_timing"
        self._path = path
        self._session_id = session_id
        self._lock = threading.Lock()
        self._next_sequence = 1
        self._events: list[ToolTiming] = []
        atomic_write_json(
            self._path,
            {"schema_version": 1, "events": []},
        )

    def wrap_tool_call(
        self,
        params: ToolCallParams,
        call_next: ToolCallFn,
    ) -> Any:
        with self._lock:
            sequence = self._next_sequence
            self._next_sequence += 1
        started_at = datetime.now(UTC).isoformat()
        started = time.monotonic()
        status: Literal["completed", "error"] = "completed"
        error_type: str | None = None
        caught_error: BaseException | None = None
        try:
            return call_next(params)
        except BaseException as exc:
            status = "error"
            error_type = type(exc).__name__
            caught_error = exc
            raise
        finally:
            completed_at = datetime.now(UTC).isoformat()
            parallel_value = params.execution_params.get("parallel_execution_id")
            event = ToolTiming(
                sequence=sequence,
                session_id=self._session_id,
                agent_name=str(params.agent_state.agent_name),
                tool_name=params.tool_name,
                tool_call_id=params.tool_call_id,
                started_at=started_at,
                completed_at=completed_at,
                wall_time_seconds=round(time.monotonic() - started, 6),
                status=status,
                error_type=error_type,
                parallel_execution_id=(
                    str(parallel_value) if parallel_value is not None else None
                ),
            )
            try:
                self._append(event)
            except Exception as exc:
                if caught_error is not None:
                    caught_error.add_note(f"tool timing persistence failed: {exc}")
                else:
                    raise InfrastructureError(
                        f"tool timing persistence failed: {exc}"
                    ) from exc

    def after(self, sequence: int) -> tuple[ToolTiming, ...]:
        with self._lock:
            return tuple(event for event in self._events if event.sequence > sequence)

    def _append(self, event: ToolTiming) -> None:
        with self._lock:
            self._events.append(event)
            self._events.sort(key=lambda item: item.sequence)
            atomic_write_json(
                self._path,
                {
                    "schema_version": 1,
                    "events": [item.model_dump(mode="json") for item in self._events],
                },
            )


class _CandidateMiddlewareGuard:
    """Delegate candidate middleware except after a protected action is captured."""

    def __init__(
        self,
        middleware: Any,
        boundary: _ProtectedBenchmarkBoundary,
    ) -> None:
        object.__setattr__(self, "_middleware", middleware)
        object.__setattr__(self, "_boundary", boundary)
        object.__setattr__(
            self,
            "name",
            getattr(
                middleware,
                "name",
                middleware.__class__.__name__,
            ),
        )

    def after_model(self, hook_input: AfterModelHookInput) -> HookResult:
        if self._boundary.intercepted:
            return HookResult.no_changes()
        return self._middleware.after_model(hook_input)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._middleware, name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name in {"_middleware", "_boundary", "name"}:
            object.__setattr__(self, name, value)
            return
        middleware = object.__getattribute__(self, "_middleware")
        if hasattr(middleware, name):
            setattr(middleware, name, value)
            return
        object.__setattr__(self, name, value)


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


def _attach_tool_timing_to_subagents(
    config: AgentConfig,
    recorder: _ToolTimingRecorder,
) -> None:
    for sub_agent in (config.sub_agents or {}).values():
        sub_agent.middlewares = [recorder, *(sub_agent.middlewares or [])]
        _attach_tool_timing_to_subagents(sub_agent, recorder)


def _blocked_runtime_tool(**_kwargs: Any) -> str:
    raise IntegrityError(
        "protected benchmark actions must be executed by the benchmark adapter"
    )


def _hook_name(hook: Callable[..., Any]) -> str:
    return str(getattr(hook, "__name__", hook.__class__.__name__))


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
    working_directory: Path,
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
        "max_retries": 0,
    }
    config.retry_attempts = 1
    config.llm_config.extra_params.pop("reasoning", None)
    extra_body = dict(config.llm_config.extra_params.get("extra_body") or {})
    extra_body.pop("reasoning", None)
    if extra_body:
        config.llm_config.extra_params["extra_body"] = extra_body
    else:
        config.llm_config.extra_params.pop("extra_body", None)
    if model.reasoning_effort is not None:
        updates["reasoning"] = {"effort": model.reasoning_effort}
    config.llm_config.update(**updates)
    if config.sandbox_config is None:
        config.sandbox_config = LocalSandboxConfig(work_dir=str(working_directory))
    elif isinstance(config.sandbox_config, LocalSandboxConfig):
        config.sandbox_config = config.sandbox_config.model_copy(
            update={"work_dir": str(working_directory)}
        )
    for sub_agent in (config.sub_agents or {}).values():
        _override_model_settings(
            sub_agent,
            model=model,
            transport=transport,
            api_key=api_key,
            working_directory=working_directory,
        )


def _session_working_directory(
    requested: Path | None,
    artifact_dir: Path,
) -> Path:
    if requested is None:
        working_directory = artifact_dir / "workspace"
        working_directory.mkdir(parents=True, exist_ok=True)
        return working_directory.resolve()
    working_directory = requested.resolve()
    if not working_directory.is_dir():
        raise IntegrityError(
            f"session working_directory is not a directory: {working_directory}"
        )
    return working_directory


def _attach_long_term_memory(
    config: AgentConfig,
    *,
    harness_dir: Path,
    manifest: HarnessManifest,
) -> None:
    if manifest.schema_version == 2 and (
        manifest.attachments is None
        or manifest.attachments.long_term_memory != "system_prompt_suffix"
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
        content = path.read_text(encoding="utf-8").strip()
        if not content:
            continue
        relative_path = path.relative_to(harness_dir).as_posix()
        sections.append(f"## Persistent harness memory: {relative_path}\n\n{content}")
    if not sections:
        return
    memory = "\n\n".join(sections)
    existing = (config.system_prompt_suffix or "").strip()
    config.system_prompt_suffix = "\n\n" + "\n\n".join(
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
