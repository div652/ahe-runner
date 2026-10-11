from __future__ import annotations

import inspect
import json
from pathlib import Path

from ahe_runner.adapter import (
    EvaluationRequest,
    EvidenceRequest,
    SplitName,
    VerificationRequest,
)
from ahe_runner.config import load_config
from ahe_runner.controller import CampaignController
from ahe_runner.evaluation import verify_evaluation_output
from ahe_runner.evidence import verify_evidence_bundle
from ahe_runner.example.runtime_adapter import RuntimeExampleAdapter
from ahe_runner.io import atomic_write_json
from ahe_runner.loading import load_adapter
from ahe_runner.runtime import (
    AgentInput,
    AgentOutput,
    AgentSessionRequest,
    BenchmarkActionCall,
)

from .conftest import make_example_project


def test_generated_runtime_example_config_loads_public_adapter(
    tmp_path: Path,
) -> None:
    offline_config = make_example_project(tmp_path)
    runtime_config = load_config(offline_config.parent / "ahe.runtime.yaml")
    assert isinstance(load_adapter(runtime_config), RuntimeExampleAdapter)
    assert runtime_config.document.meta.backend == "nexau"
    assert runtime_config.document.meta.explore.web_search_backend == "ddgs"
    controller = CampaignController(runtime_config)
    adapter_source = Path(inspect.getsourcefile(type(controller.adapter)) or "")
    assert "GREEN-4" in adapter_source.read_text(encoding="utf-8")
    assert adapter_source not in controller.meta._safe_source_roots.values()  # type: ignore[attr-defined]
    public_source = next(
        iter(controller.meta._safe_source_roots.values())  # type: ignore[attr-defined]
    )
    assert "GREEN-4" not in public_source.read_text(encoding="utf-8")


class _FakeSession:
    def __init__(self, request: AgentSessionRequest) -> None:
        self.request = request
        self.closed = False
        self.calls: list[AgentInput] = []

    def run(self, request: AgentInput) -> AgentOutput:
        self.calls.append(request)
        task_id = self.request.context["task_id"]
        if task_id == "train-direct-ready":
            return AgentOutput(
                final_text="READY",
                messages=(),
                trace=(),
            )
        if len(self.calls) == 1:
            return AgentOutput(
                final_text="",
                external_actions=(
                    BenchmarkActionCall(
                        tool_call_id="lookup-1",
                        name="lookup_code",
                        arguments={"label": "alpha"},
                    ),
                ),
                messages=(),
                trace=(),
            )
        tool_message = request.messages[0]
        assert tool_message.role == "tool"
        assert tool_message.tool_call_id == "lookup-1"
        assert tool_message.content == {
            "status": "ok",
            "label": "alpha",
            "code": "BLUE-7",
        }
        return AgentOutput(
            final_text="CODE: BLUE-7",
            messages=(),
            trace=(),
        )

    def close(self) -> None:
        self.closed = True


class _FakeRuntime:
    def __init__(self) -> None:
        self.requests: list[AgentSessionRequest] = []
        self.sessions: list[_FakeSession] = []

    def open_session(self, request: AgentSessionRequest) -> _FakeSession:
        request.artifact_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_json(
            request.artifact_dir / "runtime_manifest.json",
            {
                "session_id": request.session_id,
                "benchmark_tools": [
                    tool.model_dump(mode="json") for tool in request.benchmark_tools
                ],
                "context": request.context,
            },
        )
        session = _FakeSession(request)
        self.requests.append(request)
        self.sessions.append(session)
        return session


def test_runtime_example_uses_public_session_and_protected_action_api(
    tmp_path: Path,
) -> None:
    config = load_config(make_example_project(tmp_path))
    adapter = RuntimeExampleAdapter()
    request = EvaluationRequest(
        checkpoint="C0",
        harness_dir=config.harness_dir,
        split=adapter.split(SplitName.TRAIN),
        trials=1,
        root_seed=42,
        output_dir=tmp_path / "evaluation",
        model=config.document.models.evaluated,
        transport=config.document.transport,
        api_key=config.api_key,
        max_concurrency=2,
    )
    runtime = _FakeRuntime()

    result = adapter.evaluate(request, runtime)  # type: ignore[arg-type]
    assert result.summary.primary.value == 1.0
    assert result.summary.rollout_count == 2
    assert result.summary.resource_totals["model_activations"] == 3
    assert all(session.closed for session in runtime.sessions)
    protected = next(
        row
        for row in runtime.requests
        if row.context["task_id"] == "train-protected-alpha"
    )
    assert [tool.name for tool in protected.benchmark_tools] == ["lookup_code"]

    verified = adapter.verify_evaluation(VerificationRequest(evaluation=request))
    assert verified == result
    verify_evaluation_output(request, verified)

    evidence_dir = tmp_path / "evidence"
    bundle = adapter.materialize_training_evidence(
        EvidenceRequest(
            checkpoint="C0",
            evaluation_dir=request.output_dir,
            output_dir=evidence_dir,
        )
    )
    verify_evidence_bundle(
        bundle,
        evidence_dir=evidence_dir,
        expected_checkpoint="C0",
        evaluation_dir=request.output_dir,
        source_evaluation=result,
        source_request=request,
    )
    traces = "\n".join(
        path.read_text(encoding="utf-8")
        for path in request.output_dir.glob("units/*/attempt-*/trace.jsonl")
    )
    assert '"event_type": "protected_action_result"' in traces
    assert '"code": "BLUE-7"' in traces
    assert (
        json.loads(
            (evidence_dir / "evidence_manifest.json").read_text(encoding="utf-8")
        )["schema_version"]
        == 2
    )
