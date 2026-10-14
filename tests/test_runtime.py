from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar

import httpx
import pytest
import yaml
from openai import OpenAI
from openai.types.chat import ChatCompletion
from pydantic import ValidationError

import ahe_runner.meta.preflight as preflight_module
import ahe_runner.runtime as runtime_module
from ahe_runner.adapter import (
    BenchmarkInterface,
    BenchmarkToolSpec,
    EvaluationRequest,
    SplitDefinition,
    SplitName,
)
from ahe_runner.config import load_config
from ahe_runner.errors import InfrastructureError, IntegrityError
from ahe_runner.evaluation import evaluation_request_sha256
from ahe_runner.harness import load_harness_manifest
from ahe_runner.io import read_json, sha256_file
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
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))
        self.instances.append(self)

    def _create(self, **kwargs: Any) -> ChatCompletion:
        self.calls.append(kwargs)
        if not self.responses:
            raise AssertionError("fake model response queue is empty")
        return self.responses.pop(0)


class _PreflightEndpoint:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.model_ids = ["example-evaluated-model"]
        self.response_model: str | None = None
        self.accept_nonexistent = False
        self.catalog_status = 200

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.method == "GET":
            assert request.url.path.endswith("/models")
            if self.catalog_status != 200:
                return httpx.Response(
                    self.catalog_status,
                    json={"error": {"message": "catalog temporarily unavailable"}},
                )
            return httpx.Response(
                200,
                json={
                    "object": "list",
                    "data": [
                        {
                            "id": model,
                            "object": "model",
                            "created": 1,
                            "owned_by": "offline-test",
                        }
                        for model in self.model_ids
                    ],
                },
            )
        assert request.url.path.endswith("/chat/completions")
        payload = json.loads(request.content)
        model = payload["model"]
        if model.startswith("ahe-negative-control-") and not self.accept_nonexistent:
            return httpx.Response(
                404,
                json={
                    "error": {
                        "message": f"Unknown model {model}",
                        "type": "invalid_request_error",
                        "code": "model_not_found",
                    }
                },
            )
        return httpx.Response(
            200,
            json=_completion(
                model=self.response_model or model,
                content="OK",
            ).model_dump(mode="json"),
        )


@pytest.fixture
def preflight_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[_PreflightEndpoint]:
    endpoint = _PreflightEndpoint()
    clients: list[OpenAI] = []

    def make_client(**kwargs: Any) -> OpenAI:
        client = OpenAI(
            **kwargs,
            http_client=httpx.Client(transport=httpx.MockTransport(endpoint.handle)),
        )
        clients.append(client)
        return client

    monkeypatch.setattr(preflight_module, "OpenAI", make_client)
    yield endpoint
    for client in clients:
        client.close()


@pytest.fixture
def runtime_factory(
    tmp_path: Path,
    preflight_endpoint: _PreflightEndpoint,
) -> Iterator[Callable[[], EvaluatedAgentRuntime]]:
    config = load_config(make_example_project(tmp_path))
    manifest = load_harness_manifest(config.harness_manifest_path)
    instances: list[EvaluatedAgentRuntime] = []

    def create_runtime() -> EvaluatedAgentRuntime:
        runtime = EvaluatedAgentRuntime(
            harness_dir=config.harness_dir,
            manifest=manifest,
            benchmark_interface=BenchmarkInterface(description="Preflight fixture."),
            model=config.document.models.evaluated.model_copy(
                update={"max_tokens": 128, "reasoning_effort": "high"}
            ),
            transport=config.document.transport.model_copy(
                update={"model_catalog": "required", "negative_control": "required"}
            ),
            api_key=config.api_key,
            artifact_root=tmp_path / "runtime-artifacts",
        )
        instances.append(runtime)
        return runtime

    yield create_runtime
    for runtime in reversed(instances):
        runtime.close()


def test_long_term_memory_attachment_skips_empty_files(tmp_path: Path) -> None:
    config = load_config(make_example_project(tmp_path))
    manifest = load_harness_manifest(config.harness_manifest_path)
    memory = config.harness_dir / "LongTermMEMORY.md"
    assert memory.read_text(encoding="utf-8") == ""
    agent_config = SimpleNamespace(system_prompt_suffix=None)

    runtime_module._attach_long_term_memory(
        agent_config,
        harness_dir=config.harness_dir,
        manifest=manifest,
    )

    assert agent_config.system_prompt_suffix is None


def test_runtime_intercepts_protected_actions_and_preserves_sessions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    preflight_endpoint: _PreflightEndpoint,
) -> None:
    config_path = make_example_project(tmp_path)
    config = load_config(config_path)
    manifest = load_harness_manifest(config.harness_manifest_path)
    (config.harness_dir / "LongTermMEMORY.md").write_text(
        "Record only reusable procedures.\n",
        encoding="utf-8",
    )
    middleware_dir = config.harness_dir / "middleware"
    middleware_dir.mkdir(exist_ok=True)
    (middleware_dir / "force_continue.py").write_text(
        "from nexau.archs.main_sub.execution.hooks import HookResult, Middleware\n"
        "\n"
        "\n"
        "class ForceProtectedContinue(Middleware):\n"
        "    def after_model(self, hook_input):\n"
        "        parsed = hook_input.parsed_response\n"
        "        if parsed and any(\n"
        "            call.tool_name == 'lookup_account'\n"
        "            for call in parsed.tool_calls\n"
        "        ):\n"
        "            return HookResult.with_modifications(force_continue=True)\n"
        "        return HookResult.no_changes()\n",
        encoding="utf-8",
    )
    (middleware_dir / "legacy_continue.py").write_text(
        "from nexau.archs.main_sub.execution.hooks import HookResult\n"
        "\n"
        "\n"
        "class LegacyProtectedContinue:\n"
        "    def __call__(self, hook_input):\n"
        "        parsed = hook_input.parsed_response\n"
        "        if parsed and any(\n"
        "            call.tool_name == 'lookup_account'\n"
        "            for call in parsed.tool_calls\n"
        "        ):\n"
        "            return HookResult.with_modifications(force_continue=True)\n"
        "        return HookResult.no_changes()\n",
        encoding="utf-8",
    )
    agent_path = config.harness_dir / "agent.yaml"
    agent_config = yaml.safe_load(agent_path.read_text(encoding="utf-8"))
    agent_config["middlewares"] = [
        {"import": "middleware.force_continue:ForceProtectedContinue"}
    ]
    agent_config["after_model_hooks"] = [
        {"import": "middleware.legacy_continue:LegacyProtectedContinue"}
    ]
    agent_config["retry_attempts"] = 5
    agent_config["llm_config"]["reasoning"] = {"effort": "high"}
    agent_path.write_text(
        yaml.safe_dump(agent_config, sort_keys=False),
        encoding="utf-8",
    )
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
    protected_workspace = tmp_path / "protected-workspace"
    internal_workspace = tmp_path / "internal-workspace"
    mixed_workspace = tmp_path / "mixed-workspace"
    protected_workspace.mkdir()
    internal_workspace.mkdir()
    mixed_workspace.mkdir()
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
                working_directory=protected_workspace,
                benchmark_tools=(protected_tool,),
            )
        )
        root_config = protected_session._agent.config
        assert root_config.llm_config.model == config.document.models.evaluated.model
        assert root_config.retry_attempts == 1
        assert "reasoning" not in root_config.llm_config.extra_params
        assert {
            sub_agent.llm_config.model for sub_agent in root_config.sub_agents.values()
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
        tool_names = {tool["function"]["name"] for tool in first_call["tools"]}
        assert "lookup_account" in tool_names
        serialized_messages = json.dumps(first_call["messages"])
        assert "lookup_account" not in serialized_messages
        assert "Record only" in serialized_messages
        system_message = next(
            message["content"]
            for message in first_call["messages"]
            if message["role"] == "system"
        )
        base_prompt = (
            (config.harness_dir / "systemprompt.md").read_text(encoding="utf-8").strip()
        )
        assert system_message == (
            base_prompt
            + "\n\n# Persistent harness memory\n\n"
            + "## Persistent harness memory: LongTermMEMORY.md\n\n"
            + "Record only reusable procedures."
        )
        assert len(FakeRecordedClient.instances[0].calls) == 1

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
        second_call_messages = FakeRecordedClient.instances[0].calls[1]["messages"]
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
                working_directory=internal_workspace,
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
        assert (
            internal_workspace / ".skills" / "evidence-first" / "SKILL.md"
        ).is_file()
        assert not (Path.cwd() / ".skills").exists()
        internal_session.close()

        mixed_session = runtime.open_session(
            AgentSessionRequest(
                session_id="mixed",
                artifact_dir=artifact_root / "mixed",
                working_directory=mixed_workspace,
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
        assert len(preflight_endpoint.requests) == 3
    finally:
        runtime.close()


def test_runtime_honors_manifest_root_agent_config(
    tmp_path: Path,
    preflight_endpoint: _PreflightEndpoint,
) -> None:
    config_path = make_example_project(tmp_path)
    config = load_config(config_path)
    original = config.harness_dir / "agent.yaml"
    renamed = config.harness_dir / "root-agent.yaml"
    original.rename(renamed)
    manifest_path = config.harness_manifest_path
    manifest_value = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    manifest_value["agent_config"] = "root-agent.yaml"
    manifest_value["required_files"] = [
        "root-agent.yaml" if value == "agent.yaml" else value
        for value in manifest_value["required_files"]
    ]
    manifest_path.write_text(
        yaml.safe_dump(manifest_value, sort_keys=False),
        encoding="utf-8",
    )
    manifest = load_harness_manifest(manifest_path)
    artifact_root = tmp_path / "runtime-artifacts"
    runtime = EvaluatedAgentRuntime(
        harness_dir=config.harness_dir,
        manifest=manifest,
        benchmark_interface=BenchmarkInterface(
            description="No protected actions.",
        ),
        model=config.document.models.evaluated,
        transport=config.document.transport,
        api_key=config.api_key,
        artifact_root=artifact_root,
    )
    try:
        session = runtime.open_session(
            AgentSessionRequest(
                session_id="renamed-root",
                artifact_dir=artifact_root / "renamed-root",
            )
        )
        assert session._agent.config.name == "example_agent"
        session.close()
    finally:
        runtime.close()


def test_evaluated_preflight_is_lazy_and_shared_by_sessions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    runtime_factory: Callable[[], EvaluatedAgentRuntime],
    preflight_endpoint: _PreflightEndpoint,
) -> None:
    runtime = runtime_factory()
    artifact_root = tmp_path / "runtime-artifacts"
    assert preflight_endpoint.requests == []
    assert not (artifact_root / "model-preflights").exists()
    FakeRecordedClient.instances = []
    FakeRecordedClient.scenarios = [[], []]
    monkeypatch.setattr(runtime_module, "RecordedExactModelClient", FakeRecordedClient)

    for session_id in ("first", "second"):
        runtime.open_session(
            AgentSessionRequest(
                session_id=session_id,
                artifact_dir=artifact_root / session_id,
            )
        )

    assert len(preflight_endpoint.requests) == 3
    positive = json.loads(preflight_endpoint.requests[1].content)
    negative = json.loads(preflight_endpoint.requests[2].content)
    assert positive["model"] == "example-evaluated-model"
    assert positive["max_tokens"] == 64
    assert positive["reasoning"] == {"effort": "high"}
    assert positive["stream"] is False
    assert negative["model"].startswith("ahe-negative-control-")
    assert len(FakeRecordedClient.instances) == 2
    manifests = [
        read_json(artifact_root / session_id / "runtime_manifest.json")
        for session_id in ("first", "second")
    ]
    assert manifests[0]["model_preflight"] == manifests[1]["model_preflight"]
    proof = manifests[0]["model_preflight"]
    proof_path = artifact_root / proof["path"]
    assert sha256_file(proof_path) == proof["sha256"]
    report = read_json(proof_path)
    assert report["status"] == "completed"
    assert report["requested_model"] == "example-evaluated-model"
    assert report["positive_probe"]["passed"] is True
    assert report["negative_control"]["passed"] is True
    assert "api_key" not in report


@pytest.mark.parametrize(
    ("failure", "error_type", "message"),
    [
        ("catalog", IntegrityError, "absent from"),
        ("positive", IntegrityError, "positive probe returned"),
        ("negative", IntegrityError, "unexpectedly generated"),
        ("infrastructure", InfrastructureError, "temporarily unavailable"),
    ],
)
def test_failed_evaluated_preflight_blocks_clients_and_is_not_retried(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    runtime_factory: Callable[[], EvaluatedAgentRuntime],
    preflight_endpoint: _PreflightEndpoint,
    failure: str,
    error_type: type[Exception],
    message: str,
) -> None:
    if failure == "catalog":
        preflight_endpoint.model_ids = []
    elif failure == "positive":
        preflight_endpoint.response_model = "wrong-model"
    elif failure == "negative":
        preflight_endpoint.accept_nonexistent = True
    else:
        preflight_endpoint.catalog_status = 503
    runtime = runtime_factory()
    FakeRecordedClient.instances = []
    FakeRecordedClient.scenarios = []
    monkeypatch.setattr(runtime_module, "RecordedExactModelClient", FakeRecordedClient)

    def unexpected_agent(**_kwargs: Any) -> None:
        pytest.fail("Agent must not be constructed after preflight failure")

    monkeypatch.setattr(runtime_module, "Agent", unexpected_agent)
    request = AgentSessionRequest(
        session_id="blocked",
        artifact_dir=tmp_path / "runtime-artifacts" / "blocked",
    )
    with pytest.raises(error_type, match=message) as first:
        runtime.open_session(request)
    proofs = list(
        (tmp_path / "runtime-artifacts").glob(
            "model-preflights/attempt-*/model-preflight.json"
        )
    )
    assert len(proofs) == 1
    original_proof = proofs[0].read_bytes()
    original_requests = len(preflight_endpoint.requests)
    with pytest.raises(error_type, match=message) as second:
        runtime.open_session(request)
    assert second.value is first.value
    assert len(preflight_endpoint.requests) == original_requests
    assert proofs[0].read_bytes() == original_proof
    assert FakeRecordedClient.instances == []
    assert not (request.artifact_dir / "runtime_manifest.json").exists()


def test_session_resources_are_bound_to_their_own_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    runtime_factory: Callable[[], EvaluatedAgentRuntime],
) -> None:
    runtime = runtime_factory()
    artifact_root = tmp_path / "runtime-artifacts"
    FakeRecordedClient.instances = []
    FakeRecordedClient.scenarios = [[], []]
    monkeypatch.setattr(runtime_module, "RecordedExactModelClient", FakeRecordedClient)
    resources = {name: object() for name in ("first", "second")}
    sessions = {
        name: runtime.open_session(
            AgentSessionRequest(
                session_id=name,
                artifact_dir=artifact_root / name,
                session_resources={"trial_workspace": resource},
            )
        )
        for name, resource in resources.items()
    }
    try:
        storages = {}
        for name, session in sessions.items():
            agent = session._agent
            storage = agent.global_storage
            assert storage.get("trial_workspace") is resources[name]
            # Sub-agents are created with this same storage object.
            assert agent.executor.subagent_manager.global_storage is storage
            storages[name] = storage
        assert storages["first"] is not storages["second"]
    finally:
        for session in sessions.values():
            session.close()


@pytest.mark.parametrize(
    "key", ["", "_private", "not-an-identifier", "tracer", "skill_registry"]
)
def test_session_resources_reject_reserved_or_invalid_keys(
    tmp_path: Path,
    key: str,
) -> None:
    with pytest.raises(ValidationError, match="session resource"):
        AgentSessionRequest(
            session_id="resource-keys",
            artifact_dir=tmp_path,
            session_resources={key: object()},
        )


def test_concurrent_sessions_share_one_evaluated_preflight(
    runtime_factory: Callable[[], EvaluatedAgentRuntime],
    preflight_endpoint: _PreflightEndpoint,
) -> None:
    runtime = runtime_factory()
    with ThreadPoolExecutor(max_workers=4) as pool:
        proofs = list(pool.map(lambda _: runtime._ensure_model_preflight(), range(8)))
    assert len(set(proofs)) == 1
    assert len(preflight_endpoint.requests) == 3


def test_new_evaluation_attempt_preserves_failed_preflight_and_request_identity(
    tmp_path: Path,
    runtime_factory: Callable[[], EvaluatedAgentRuntime],
    preflight_endpoint: _PreflightEndpoint,
) -> None:
    config = load_config(tmp_path / "project" / "ahe.yaml")
    request = EvaluationRequest(
        checkpoint="C0",
        harness_dir=config.harness_dir,
        split=SplitDefinition(
            name=SplitName.TRAIN,
            identity="preflight-identity-fixture",
            task_ids=["one-task"],
        ),
        trials=1,
        root_seed=42,
        output_dir=tmp_path / "evaluation",
        model=config.document.models.evaluated.model_copy(
            update={"max_tokens": 128, "reasoning_effort": "high"}
        ),
        transport=config.document.transport.model_copy(
            update={"model_catalog": "required", "negative_control": "required"}
        ),
        api_key=config.api_key,
        max_concurrency=2,
    )
    original_identity = evaluation_request_sha256(request)
    preflight_endpoint.catalog_status = 503
    failed = runtime_factory()
    with pytest.raises(InfrastructureError):
        failed._ensure_model_preflight()
    failed.close()
    preflight_root = tmp_path / "runtime-artifacts" / "model-preflights"
    failed_proof = next(preflight_root.glob("attempt-*/model-preflight.json"))
    failed_contents = failed_proof.read_bytes()

    preflight_endpoint.catalog_status = 200
    successful = runtime_factory()
    successful_proof = successful._ensure_model_preflight()
    assert successful_proof != failed_proof
    assert failed_proof.read_bytes() == failed_contents
    assert read_json(failed_proof)["status"] == "infrastructure_failed"
    assert read_json(successful_proof)["status"] == "completed"
    assert len(list(preflight_root.glob("attempt-*/model-preflight.json"))) == 2
    assert evaluation_request_sha256(request) == original_identity


def test_evaluated_preflight_respects_disabled_controls(
    tmp_path: Path,
    preflight_endpoint: _PreflightEndpoint,
) -> None:
    config = load_config(make_example_project(tmp_path))
    runtime = EvaluatedAgentRuntime(
        harness_dir=config.harness_dir,
        manifest=load_harness_manifest(config.harness_manifest_path),
        benchmark_interface=BenchmarkInterface(description="Disabled controls."),
        model=config.document.models.evaluated,
        transport=config.document.transport.model_copy(
            update={"model_catalog": "disabled", "negative_control": "disabled"}
        ),
        api_key=config.api_key,
        artifact_root=tmp_path / "runtime-artifacts",
    )
    try:
        proof = runtime._ensure_model_preflight()
        assert len(preflight_endpoint.requests) == 1
        assert preflight_endpoint.requests[0].method == "POST"
        report = read_json(proof)
        assert report["advertised_model_ids"] is None
        assert report["negative_control"] is None
        assert report["positive_probe"]["passed"] is True
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
