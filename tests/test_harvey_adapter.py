"""Offline end-to-end checks for the protected Harvey adapter.

The real adapter loads the real pinned LAB tasks. Only podman (sandbox and
image lookup), the evaluated agent, and the two judge models are faked.
"""

from __future__ import annotations

import json
import shutil
import threading
from pathlib import Path
from typing import Any

import pytest
import yaml
from harvey_ahe import adapter as adapter_module
from harvey_ahe.adapter import (
    PRIMARY_METRIC,
    HarveyAdapter,
    _AgentRun,
    _termination_reason,
)
from harvey_ahe.grading import JUDGE_MAX_TOKENS, dual_summary
from harvey_ahe.tasks import PinnedTask, leaf_slug, load_pinned_task
from harvey_ahe.workspace import RESOURCE_KEY, WorkspaceHandle

from ahe_runner.adapter import (
    CandidateValidationRequest,
    EvaluationRequest,
    EvidenceRequest,
    SplitName,
    VerificationRequest,
)
from ahe_runner.config import ModelSettings, TransportSettings
from ahe_runner.errors import ConfigurationError, InfrastructureError, IntegrityError
from ahe_runner.evaluation import verify_evaluation_output
from ahe_runner.evidence import verify_evidence_bundle
from ahe_runner.runtime import AgentInput, AgentOutput, AgentSessionRequest

from .harvey_fakes import HARNESS_DIR, LAB_ROOT, PROJECT_ROOT, FakeSandbox

JUDGE_A = "judge-model-a"
JUDGE_B = "judge-model-b"
HELPER = "helper-model"
FAKE_IMAGE_ID = "sha256:fake-lab-sandbox"

# Per training task (split order): agent behavior, failing criteria per judge.
SCRIPT: tuple[tuple[str, tuple[int, int]], ...] = (
    ("finish", (0, 0)),
    ("finish", (0, 1)),
    ("agent_error", (3, 3)),
    ("max_turns", (2, 1)),
    ("context", (1, 1)),
    ("infra_once", (0, 0)),
)


def _settings(scratch_root: Path, **overrides: Any) -> dict[str, Any]:
    settings: dict[str, Any] = {
        "lab_root": str(LAB_ROOT),
        "split_file": "splits.json",
        "judges": [
            {"model": JUDGE_A, "max_tokens": JUDGE_MAX_TOKENS, "temperature": 0.0},
            {"model": JUDGE_B, "max_tokens": JUDGE_MAX_TOKENS, "temperature": 0.0},
        ],
        "helper": {"model": HELPER, "max_tokens": 1024, "temperature": 0.0},
        "scratch_root": str(scratch_root),
    }
    settings.update(overrides)
    return settings


@pytest.fixture(scope="module")
def scratch_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("harvey-scratch")


@pytest.fixture(scope="module")
def adapter(scratch_root: Path) -> HarveyAdapter:
    return HarveyAdapter(project_root=PROJECT_ROOT, settings=_settings(scratch_root))


# Settings, splits, and task pins


def test_shipped_ahe_yaml_settings_are_accepted() -> None:
    document = yaml.safe_load((PROJECT_ROOT / "ahe.yaml").read_text("utf-8"))
    settings = document["project"]["adapter_settings"]
    config = adapter_module.HarveyAdapterSettings.model_validate(settings)
    assert config.judges[0].model != config.judges[1].model
    assert (PROJECT_ROOT / config.lab_root).resolve() == LAB_ROOT


@pytest.mark.parametrize(
    ("override", "message"),
    [
        (
            {
                "judges": [
                    {"model": JUDGE_A, "max_tokens": JUDGE_MAX_TOKENS},
                    {"model": JUDGE_A, "max_tokens": JUDGE_MAX_TOKENS},
                ]
            },
            "two distinct judge models",
        ),
        (
            {
                "judges": [
                    {"model": JUDGE_A, "max_tokens": 1000, "temperature": 0.0},
                    {"model": JUDGE_B, "max_tokens": JUDGE_MAX_TOKENS},
                ]
            },
            "max_tokens",
        ),
        ({"shell_timeout": 601}, "shell_timeout"),
        ({"grading_parallel": 0}, "grading_parallel"),
        ({"grading_parallel": 101}, "grading_parallel"),
        ({"unexpected": True}, "unexpected"),
    ],
)
def test_invalid_settings_are_configuration_errors(
    tmp_path: Path,
    override: dict[str, Any],
    message: str,
) -> None:
    with pytest.raises(ConfigurationError, match=message):
        HarveyAdapter(
            project_root=PROJECT_ROOT, settings=_settings(tmp_path, **override)
        )


def test_splits_are_disjoint_pinned_and_sized(adapter: HarveyAdapter) -> None:
    sizes = {name: len(adapter.split(name).task_ids) for name in SplitName}
    assert sizes == {SplitName.TRAIN: 6, SplitName.VALIDATION: 3, SplitName.TEST: 3}
    all_ids = [i for name in SplitName for i in adapter.split(name).task_ids]
    assert len(all_ids) == len(set(all_ids)) == 12
    areas = {task_id.split("/", 1)[0] for task_id in all_ids}
    assert len(areas) == 12
    metadata = adapter.split(SplitName.TRAIN).metadata
    assert metadata["lab_commit"] == adapter.split_file.lab_commit


def test_changed_task_content_is_rejected_against_pins(adapter: HarveyAdapter) -> None:
    pinned = adapter.split_file.splits[SplitName.TRAIN][0]
    load_pinned_task(LAB_ROOT, pinned)
    with pytest.raises(ConfigurationError, match=r"differs from splits\.json pins"):
        load_pinned_task(
            LAB_ROOT,
            PinnedTask(pinned.task_id, "0" * 64, pinned.documents_sha256),
        )


def test_meta_prompt_context_lists_only_training_slugs(adapter: HarveyAdapter) -> None:
    terms = set(adapter.meta_prompt_context().task_id_leakage_terms)
    for task_id in adapter.split(SplitName.TRAIN).task_ids:
        assert leaf_slug(task_id) in terms
    for name in (SplitName.VALIDATION, SplitName.TEST):
        for task_id in adapter.split(name).task_ids:
            assert leaf_slug(task_id) not in terms


# Termination classification


@pytest.mark.parametrize(
    ("error", "finished", "final_text", "expected"),
    [
        ("ValueError: x", True, "", "agent_error"),
        (None, True, "[Error: Maximum iteration limit reached]", "finish_tool"),
        (
            None,
            False,
            "[Note: Maximum iteration limit reached (200)]",
            "max_turns_exceeded",
        ),
        (None, False, "[Error: Prompt too long for the model]", "context_overflow"),
        (None, False, "[Error: Insufficient tokens for response]", "context_overflow"),
        (None, False, "Here is my memo.", "no_tool_calls"),
    ],
)
def test_termination_reason(
    error: str | None,
    finished: bool,
    final_text: str,
    expected: str,
) -> None:
    output = AgentOutput(final_text=final_text, messages=(), trace=())
    run = _AgentRun(
        output=output,
        error=error,
        finished=finished,
        finish_summary=None,
        wall_clock_seconds=0.0,
    )
    assert _termination_reason(run) == expected


# Candidate validation


def _candidate_request(
    adapter: HarveyAdapter, harness: Path
) -> CandidateValidationRequest:
    return CandidateValidationRequest(
        checkpoint="C1",
        parent_checkpoint="C0",
        harness_dir=harness,
        parent_harness_dir=HARNESS_DIR,
        training_task_ids=tuple(adapter.split(SplitName.TRAIN).task_ids),
    )


def test_seed_harness_passes_candidate_checks(adapter: HarveyAdapter) -> None:
    result = adapter.validate_candidate(_candidate_request(adapter, HARNESS_DIR))
    assert result.valid, result.errors


@pytest.mark.parametrize(
    ("relative", "text", "message"),
    [
        ("tools/net.py", "import socket\n", "import of 'socket'"),
        ("tools/proc.py", "import os\nos.system('id')\n", "os.system"),
        ("tools/grader.py", "from harvey_ahe import grading\n", "harvey_ahe.workspace"),
        ("tools/peek.py", "PATH = 'task.json'\n", "task.json"),
    ],
)
def test_candidate_checks_reject_escape_hatches(
    adapter: HarveyAdapter,
    tmp_path: Path,
    relative: str,
    text: str,
    message: str,
) -> None:
    harness = tmp_path / "harness"
    shutil.copytree(HARNESS_DIR, harness, ignore=shutil.ignore_patterns("__pycache__"))
    (harness / relative).write_text(text, encoding="utf-8")
    result = adapter.validate_candidate(_candidate_request(adapter, harness))
    assert not result.valid
    assert any(message in error for error in result.errors), result.errors


def test_candidate_checks_reject_training_slug(
    adapter: HarveyAdapter,
    tmp_path: Path,
) -> None:
    harness = tmp_path / "harness"
    shutil.copytree(HARNESS_DIR, harness, ignore=shutil.ignore_patterns("__pycache__"))
    slug = leaf_slug(adapter.split(SplitName.TRAIN).task_ids[0])
    with (harness / "systemprompt.md").open("a", encoding="utf-8") as handle:
        handle.write(f"\nFor {slug} remember the cap.\n")
    result = adapter.validate_candidate(_candidate_request(adapter, harness))
    assert any("training task slug" in error for error in result.errors)


# Meta-agent supplements are rendered as Jinja by NexAU (AHE-009)


@pytest.mark.parametrize("role", ["source", "web", "debugger", "evolve"])
def test_role_supplements_render_unchanged(role: str) -> None:
    from nexau.archs.main_sub.prompt_handler import PromptHandler

    path = PROJECT_ROOT / "meta_supplements" / f"{role}.md"
    text = path.read_text(encoding="utf-8")
    rendered = PromptHandler()._process_jinja_prompt(str(path), {"date": "x"})
    assert rendered.strip() == text.strip()


# evaluate -> resume -> verify -> evidence, with podman, agent, and judges faked


class _Session:
    def __init__(self, runtime: _Runtime, request: AgentSessionRequest) -> None:
        self.runtime = runtime
        self.request = request
        self.closed = False

    def run(self, agent_input: AgentInput) -> AgentOutput:
        handle = self.request.session_resources[RESOURCE_KEY]
        assert isinstance(handle, WorkspaceHandle) and not handle.revoked
        instructions = str(agent_input.messages[0].content)
        task_index = self.runtime.task_index[instructions]
        behavior = SCRIPT[task_index][0]
        with self.runtime.lock:
            self.runtime.handles.append(handle)
            attempts = self.runtime.attempts.setdefault(task_index, 0) + 1
            self.runtime.attempts[task_index] = attempts
        if behavior == "infra_once" and attempts == 1:
            raise InfrastructureError("evaluated model endpoint unavailable")
        if behavior == "agent_error":
            raise ValueError("model produced malformed output")
        assert handle.exists("/workspace/documents")
        handle.write_file("/workspace/output/response.md", f"memo {task_index}")
        handle.write_file("/workspace/scratch/notes.txt", "notes")
        handle.to_host("/workspace/output/link.md").symlink_to(
            handle.to_host("/workspace/documents")
        )
        final_text = "Done."
        if behavior in {"finish", "infra_once"}:
            handle.mark_finished(f"summary {task_index}")
        elif behavior == "max_turns":
            final_text = "[Error: Maximum iteration limit reached (200)]"
        elif behavior == "context":
            final_text = "[Error: Prompt too long: 210000 tokens]"
        assistant = {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "working"},
                {"type": "tool_use", "id": "c1", "name": "read", "input": {}},
                {"type": "tool_use", "id": "c2", "name": "write", "input": {}},
            ],
        }
        trace = (
            {"role": "user", "content": [{"type": "text", "text": instructions}]},
            assistant,
            {"role": "assistant", "content": [{"type": "text", "text": final_text}]},
        )
        return AgentOutput(final_text=final_text, messages=trace, trace=trace)

    def close(self) -> None:
        self.closed = True


class _Runtime:
    def __init__(self, adapter: HarveyAdapter) -> None:
        train = adapter.split(SplitName.TRAIN).task_ids
        self.task_index = {
            adapter._tasks[task_id].instructions: index
            for index, task_id in enumerate(train)
        }
        assert len(self.task_index) == len(train)
        self.lock = threading.Lock()
        self.sessions: list[_Session] = []
        self.handles: list[WorkspaceHandle] = []
        self.attempts: dict[int, int] = {}

    def open_session(self, request: AgentSessionRequest) -> _Session:
        assert set(request.session_resources) == {RESOURCE_KEY}
        assert request.working_directory is not None
        assert request.working_directory.is_dir()
        session = _Session(self, request)
        with self.lock:
            self.sessions.append(session)
        return session


class _Grader:
    def __init__(self, adapter: HarveyAdapter) -> None:
        train = adapter.split(SplitName.TRAIN).task_ids
        self.index = {task_id: index for index, task_id in enumerate(train)}
        self.outputs: dict[str, list[str]] = {}
        self.lock = threading.Lock()

    def __call__(
        self,
        *,
        attempts: int,
        grading_root: Path,
        task_id: str,
        task_config: dict[str, Any],
        run_dir: Path,
        judges: tuple[ModelSettings, ModelSettings],
        api_key: str,
        **_kwargs: Any,
    ) -> tuple[Path, dict[str, Any]]:
        assert attempts == 2
        assert api_key == "offline-key"
        with self.lock:
            self.outputs[task_id] = sorted(
                path.relative_to(run_dir / "output").as_posix()
                for path in (run_dir / "output").rglob("*")
            )
        grading_dir = grading_root / "grading-1"
        grading_dir.mkdir(parents=True)
        failing = SCRIPT[self.index[task_id]][1]
        per_judge: list[dict[str, Any]] = []
        for number, (judge, fails) in enumerate(zip(judges, failing, strict=True), 1):
            rows = [
                {
                    "criterion_id": str(criterion.get("id", position)),
                    "verdict": "fail" if position < fails else "pass",
                    "reasoning": "offline verdict",
                }
                for position, criterion in enumerate(task_config["criteria"])
            ]
            passed = sum(row["verdict"] == "pass" for row in rows)
            scores = {
                "task": task_id,
                "judge_index": number,
                "judge_model": f"copilot/{judge.model}",
                "all_pass": passed == len(rows),
                "n_criteria": len(rows),
                "n_passed": passed,
                "criteria_results": rows,
            }
            (grading_dir / f"scores_judge-{number}.json").write_text(
                json.dumps(scores), encoding="utf-8"
            )
            per_judge.append(scores)
        dual = {
            "task": task_id,
            "judges": [scores["judge_model"] for scores in per_judge],
            **dual_summary(per_judge),
        }
        (grading_dir / "scores_dual.json").write_text(json.dumps(dual), "utf-8")
        return grading_dir, dual


def test_evaluate_resume_verify_and_evidence(
    adapter: HarveyAdapter,
    scratch_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sandboxes: list[FakeSandbox] = []

    def make_sandbox(**kwargs: Any) -> FakeSandbox:
        assert kwargs["image"] == FAKE_IMAGE_ID
        sandbox = FakeSandbox(**kwargs)
        sandboxes.append(sandbox)
        return sandbox

    grader = _Grader(adapter)
    monkeypatch.setattr(adapter_module, "pandoc_version", lambda: "3.6")
    monkeypatch.setattr(
        adapter_module, "_podman_image_id", lambda _image: FAKE_IMAGE_ID
    )
    monkeypatch.setattr(adapter_module, "Sandbox", make_sandbox)
    monkeypatch.setattr(adapter_module, "grade_with_retries", grader)
    request = EvaluationRequest(
        checkpoint="C0",
        harness_dir=HARNESS_DIR,
        split=adapter.split(SplitName.TRAIN),
        trials=1,
        root_seed=7,
        output_dir=tmp_path / "evaluation",
        model=ModelSettings(model="evaluated-model", max_tokens=64000),
        transport=TransportSettings(base_url="http://127.0.0.1:9/v1"),
        api_key="offline-key",
        max_concurrency=3,
    )
    runtime = _Runtime(adapter)

    with pytest.raises(InfrastructureError, match="1 Harvey unit"):
        adapter.evaluate(request, runtime)  # type: ignore[arg-type]
    failures = list(
        request.output_dir.glob("units/*/attempt-1/infrastructure_error.json")
    )
    assert len(failures) == 1
    assert len(runtime.sessions) == 6

    result = adapter.evaluate(request, runtime)  # type: ignore[arg-type]
    assert len(runtime.sessions) == 7
    assert runtime.attempts == {0: 1, 1: 1, 2: 1, 3: 1, 4: 1, 5: 2}
    assert all(session.closed for session in runtime.sessions)
    assert all(handle.revoked for handle in runtime.handles)
    assert len(sandboxes) == 7
    assert all(sandbox.events == ["start", "stop"] for sandbox in sandboxes)
    # The infrastructure failure skips the post-run health check; nothing else does.
    assert sum(not sandbox.calls for sandbox in sandboxes) == 1
    assert all(
        sandbox.calls[-1].command == "true" for sandbox in sandboxes if sandbox.calls
    )
    assert list(scratch_root.iterdir()) == []

    summary = result.summary
    assert summary.primary.name == PRIMARY_METRIC
    assert summary.primary.value == pytest.approx(2.5 / 6)
    assert summary.metrics["both_judges_all_pass_rate"] == pytest.approx(2 / 6)
    assert summary.metrics["judge_1_all_pass_rate"] == pytest.approx(3 / 6)
    assert summary.metrics["judge_2_all_pass_rate"] == pytest.approx(2 / 6)
    assert summary.metrics["judge_disagreement_count"] == 1
    assert summary.metrics["termination_counts"] == {
        "agent_error": 1,
        "context_overflow": 1,
        "finish_tool": 3,
        "max_turns_exceeded": 1,
    }
    assert summary.resource_totals["agent_errors"] == 1
    assert summary.resource_totals["tool_calls"] == 10
    assert [fact.outcome for fact in result.trial_facts] == [
        "all_pass",
        "judges_split",
        "not_all_pass",
        "not_all_pass",
        "not_all_pass",
        "all_pass",
    ]

    graded = grader.outputs
    train = adapter.split(SplitName.TRAIN).task_ids
    assert graded[train[2]] == []
    assert graded[train[0]] == ["response.md"]
    first = json.loads(
        next(request.output_dir.glob("units/*/attempt-1/result.json")).read_text()
    )
    assert first["provenance"]["sandbox_image_id"] == FAKE_IMAGE_ID

    results = {
        row["task_id"]: row
        for row in (
            json.loads(path.read_text())
            for path in request.output_dir.glob("units/*/attempt-*/result.json")
        )
    }
    ok = results[train[0]]
    assert ok["skipped_output_entries"] == ["link.md"]
    assert [row["path"] for row in ok["output_files"]] == ["response.md"]
    assert ok["workspace_files"] == [{"path": "scratch/notes.txt", "bytes": 5}]
    assert ok["tool_call_counts"] == {"read": 1, "write": 1}
    assert ok["finish_summary"] == "summary 0"
    assert results[train[2]]["agent_error"] == (
        "ValueError: model produced malformed output"
    )
    assert results[train[5]]["attempt"] == 2

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
    assert bundle.task_count == 6
    detail = json.loads(next((evidence_dir / "tasks").glob("*.json")).read_text())
    trial = detail["trials"][0]
    assert {artifact["kind"] for artifact in trial["artifacts"]} >= {
        "result",
        "trace",
        "evaluator",
    }
    assert len(trial["judges"]) == 2
    assert trial["judges"][0]["criteria_results"]

    score_path = next(
        request.output_dir.glob("units/*/attempt-1/grading/*/scores_judge-1.json")
    )
    score_path.write_text(score_path.read_text() + " ", encoding="utf-8")
    with pytest.raises(IntegrityError, match="score hash mismatch"):
        adapter.verify_evaluation(VerificationRequest(evaluation=request))


def test_evidence_refuses_non_training_split(
    adapter: HarveyAdapter,
    tmp_path: Path,
) -> None:
    from ahe_runner.adapter import EvaluationResult, MetricSummary, MetricValue

    evaluation_dir = tmp_path / "evaluation"
    evaluation_dir.mkdir()
    payload = EvaluationResult(
        evaluation_id="e",
        request_sha256="0" * 64,
        checkpoint="C0",
        split=SplitName.VALIDATION,
        summary=MetricSummary(
            primary=MetricValue(name=PRIMARY_METRIC, value=0.0),
            metrics={},
            task_count=0,
            rollout_count=0,
        ),
        trial_facts=[],
        artifact_hashes={},
    )
    (evaluation_dir / "adapter_result.json").write_text(
        payload.model_dump_json(), encoding="utf-8"
    )
    with pytest.raises(IntegrityError, match="training split"):
        adapter.materialize_training_evidence(
            EvidenceRequest(
                checkpoint="C0",
                evaluation_dir=evaluation_dir,
                output_dir=tmp_path / "evidence",
            )
        )
