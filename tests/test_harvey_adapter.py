"""Offline end-to-end checks for the protected Harvey adapter.

The real adapter loads the real pinned LAB tasks. Only podman (sandbox and
image lookup), the evaluated agent, and the two judge models are faked.
"""

from __future__ import annotations

import json
import shutil
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import yaml
from harvey_ahe import adapter as adapter_module
from harvey_ahe.adapter import (
    PRIMARY_METRIC,
    HarveyAdapter,
    HarveyAdapterSettings,
    _AgentRun,
    _final_text,
    _run_session,
    _termination_reason,
)
from harvey_ahe.grading import JUDGE_MAX_TOKENS, dual_summary
from harvey_ahe.tasks import PinnedTask, leaf_slug, load_pinned_task
from harvey_ahe.workspace import RESOURCE_KEY, WorkspaceHandle
from lab_core.sandbox.sandbox import PodmanError
from openai import APIStatusError, APITimeoutError

from ahe_runner.adapter import (
    CandidateValidationRequest,
    EvaluationRequest,
    EvidenceRequest,
    MetricSummary,
    MetricValue,
    SplitName,
    TaskState,
    VerificationRequest,
)
from ahe_runner.child_process import portable_exception
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


def test_judge_call_cap_builds_one_shared_semaphore(scratch_root: Path) -> None:
    capped = HarveyAdapter(
        project_root=PROJECT_ROOT,
        settings=_settings(scratch_root, judge_max_concurrent_calls=3),
    )
    slots = capped._judge_call_slots
    assert isinstance(slots, threading.BoundedSemaphore)
    assert all(slots.acquire(blocking=False) for _ in range(3))
    assert not slots.acquire(blocking=False)
    for _ in range(3):
        slots.release()
    uncapped = HarveyAdapter(
        project_root=PROJECT_ROOT, settings=_settings(scratch_root)
    )
    assert uncapped._judge_call_slots is None


def _summary(scores: dict[str, float]) -> MetricSummary:
    return MetricSummary(
        primary=MetricValue(name=PRIMARY_METRIC, value=sum(scores.values()) / 4),
        metrics={},
        task_count=len(scores),
        rollout_count=len(scores),
        task_states=[
            TaskState(task_key=key, state="scored", score=score)
            for key, score in scores.items()
        ],
    )


def test_compare_applies_one_global_attribution_threshold(
    scratch_root: Path,
) -> None:
    before = _summary({"a": 0.30, "b": 0.30, "c": 0.90, "d": 0.50})
    after = _summary({"a": 0.40, "b": 0.35, "c": 0.80, "d": 0.45})
    thresholded = HarveyAdapter(
        project_root=PROJECT_ROOT,
        settings=_settings(scratch_root, attribution_min_task_delta=0.08),
    )
    transition = thresholded.compare(before, after)
    assert transition.improved_task_keys == ["a"]
    assert transition.regressed_task_keys == ["c"]
    assert transition.unchanged_task_keys == ["b", "d"]
    assert transition.metadata["attribution_min_task_delta"] == 0.08
    with pytest.raises(ValueError):
        adapter_module.HarveyAdapterSettings.model_validate(
            _settings(scratch_root, attribution_min_task_delta=-0.1)
        )


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
        ({"judge_max_concurrent_calls": 0}, "judge_max_concurrent_calls"),
        ({"judge_max_concurrent_calls": 101}, "judge_max_concurrent_calls"),
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


def test_provider_context_rejection_is_a_graded_context_overflow(
    tmp_path: Path,
) -> None:
    """LAB grades a run the provider rejects as too long; so does the adapter."""
    provider = RuntimeError(
        "Error code: 500 - {'error': {'message': 'Message exceeds token limit.', "
        "'type': 'server_error'}}"
    )

    class _Failing:
        def __init__(self, exc: Exception) -> None:
            self.exc = exc
            self.closed = False

        def run(self, agent_input: AgentInput) -> AgentOutput:
            raise self.exc

        def close(self) -> None:
            self.closed = True

    def run_with(exc: Exception) -> tuple[Any, _Failing]:
        session = _Failing(exc)
        runtime = type("R", (), {"open_session": lambda self, request: session})()
        result = _run_session(
            runtime=runtime,
            session_id="s",
            artifact_dir=tmp_path,
            working_directory=tmp_path,
            handle=object(),  # type: ignore[arg-type]
            instructions="task",
        )
        return result, session

    wrapped = RuntimeError(f"Error in agent execution: {provider}")
    wrapped.__cause__ = InfrastructureError(str(provider))
    (output, error, overflow), session = run_with(wrapped)
    assert session.closed and output is None and error is None
    assert overflow is not None and "Message exceeds token limit." in overflow
    run = _AgentRun(
        output=None,
        error=error,
        finished=False,
        finish_summary=None,
        wall_clock_seconds=0.0,
        context_overflow=overflow,
    )
    assert _termination_reason(run) == "context_overflow"
    assert _final_text(run).startswith("[Error: Prompt too long")

    for text in ("prompt is too long: 1000 > 900", "context_length_exceeded"):
        (_, error, overflow), _ = run_with(ValueError(text))
        assert error is None and overflow is not None

    other = RuntimeError(
        "Error code: 500 - {'error': {'message': 'Model request timed out', "
        "'type': 'server_error'}}"
    )
    with pytest.raises(RuntimeError, match="timed out"):
        run_with(other)
    (_, error, overflow), _ = run_with(ValueError("bad output"))
    assert error == "ValueError: bad output" and overflow is None


def _api_error(status: int, message: str) -> APIStatusError:
    request = httpx.Request("POST", "http://127.0.0.1:9/v1/responses")
    return APIStatusError(
        f"Error code: {status} - {message}",
        response=httpx.Response(status, request=request),
        body=None,
    )


@pytest.mark.parametrize("status", [400, 403, 404])
def test_api_rejection_policy(tmp_path: Path, status: int) -> None:
    """grade keeps the partial run as agent_error; rerun makes it infrastructure."""

    def run_with(exc: Exception, *, rerun: bool) -> Any:
        class _Failing:
            def run(self, agent_input: AgentInput) -> AgentOutput:
                raise exc

            def close(self) -> None:
                return None

        runtime = type("R", (), {"open_session": lambda self, request: _Failing()})()
        return _run_session(
            runtime=runtime,
            session_id="s",
            artifact_dir=tmp_path,
            working_directory=tmp_path,
            handle=object(),  # type: ignore[arg-type]
            instructions="task",
            rerun_api_rejections=rerun,
        )

    # NexAU wraps the provider error and keeps it as the cause.
    rejected = _chained(
        RuntimeError("Error in agent execution: unknown parameter"),
        _api_error(status, "unknown parameter 'input[2].async_'"),
    )
    output, error, overflow = run_with(rejected, rerun=False)
    assert output is None and overflow is None
    assert error is not None and error.startswith("RuntimeError: Error in agent")

    with pytest.raises(InfrastructureError, match=f"HTTP {status}.*rerun") as caught:
        run_with(rejected, rerun=True)
    assert type(portable_exception(caught.value)) is InfrastructureError

    # A context-length rejection stays a graded context overflow.
    too_long = _chained(
        RuntimeError("Error in agent execution"),
        _api_error(400, "context_length_exceeded"),
    )
    _, error, overflow = run_with(too_long, rerun=True)
    assert error is None and overflow is not None
    # Errors that are not API rejections are unaffected by the policy.
    _, error, _ = run_with(ValueError("bad output"), rerun=True)
    assert error == "ValueError: bad output"


def test_api_rejection_policy_setting() -> None:
    settings = yaml.safe_load(
        (PROJECT_ROOT / "ahe.yaml").read_text("utf-8")
    )["project"]["adapter_settings"]
    assert HarveyAdapterSettings.model_validate(settings).api_rejection_policy == (
        "grade"
    )
    rerun = HarveyAdapterSettings.model_validate(
        {**settings, "api_rejection_policy": "rerun"}
    )
    assert rerun.api_rejection_policy == "rerun"
    with pytest.raises(ValueError, match="api_rejection_policy"):
        HarveyAdapterSettings.model_validate(
            {**settings, "api_rejection_policy": "retry"}
        )


def test_trial_process_passes_the_rerun_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    class _Runtime:
        def close(self) -> None:
            return None

    monkeypatch.setattr(
        adapter_module.EvaluatedAgentRuntime,
        "from_process_spec",
        lambda _spec: _Runtime(),
    )
    monkeypatch.setattr(
        adapter_module, "_run_trial_agent", lambda **kwargs: seen.update(kwargs)
    )
    fields: dict[str, Any] = {
        name: None for name in adapter_module._TrialPayload.__dataclass_fields__
    }
    fields["runtime_spec"] = SimpleNamespace(
        transport=TransportSettings(base_url="http://127.0.0.1:9/v1")
    )
    for flag in (False, True):
        fields["rerun_api_rejections"] = flag
        payload = adapter_module._TrialPayload(**fields)
        adapter_module._trial_process_entry(payload, None)
        assert seen["rerun_api_rejections"] is flag


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
    def __init__(self, adapter: HarveyAdapter, label: str = "copilot") -> None:
        self.label = label
        train = adapter.split(SplitName.TRAIN).task_ids
        self.index = {task_id: index for index, task_id in enumerate(train)}
        self.outputs: dict[str, list[str]] = {}
        self.criteria_counts: dict[str, int] = {}
        self.lock = threading.Lock()
        self.call_slots = adapter._judge_call_slots

    def __call__(
        self,
        *,
        attempts: int,
        grading_root: Path,
        task_id: str,
        task_config: dict[str, Any],
        run_dir: Path,
        judges: tuple[ModelSettings, ...],
        api_key: str,
        **_kwargs: Any,
    ) -> tuple[Path, dict[str, Any]]:
        assert attempts == 2
        assert api_key == "offline-key"
        assert _kwargs["call_slots"] is self.call_slots
        with self.lock:
            self.criteria_counts[task_id] = len(task_config["criteria"])
            self.outputs[task_id] = sorted(
                path.relative_to(run_dir / "output").as_posix()
                for path in (run_dir / "output").rglob("*")
            )
        grading_dir = grading_root / "grading-1"
        grading_dir.mkdir(parents=True)
        failing = SCRIPT[self.index[task_id]][1][: len(judges)]
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
                "judge_model": f"{self.label}/{judge.model}",
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
    train_ids = adapter.split(SplitName.TRAIN).task_ids
    trial_rates = [
        sum(
            (grader.criteria_counts[task_id] - fails) / grader.criteria_counts[task_id]
            for fails in SCRIPT[index][1]
        )
        / 2
        for index, task_id in enumerate(train_ids)
    ]
    assert summary.primary.name == PRIMARY_METRIC == "mean_criterion_pass_rate"
    assert summary.primary.value == pytest.approx(sum(trial_rates) / 6)
    assert summary.metrics[PRIMARY_METRIC] == summary.primary.value
    assert [state.score for state in summary.task_states] == pytest.approx(trial_rates)
    assert [fact.score for fact in result.trial_facts] == pytest.approx(trial_rates)
    assert summary.metrics["mean_dual_all_pass_rate"] == pytest.approx(2.5 / 6)
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

    assert ok["score"] == pytest.approx(trial_rates[0])
    assert adapter.selection_key(summary) == (
        summary.primary.value,
        pytest.approx(2.5 / 6),
    )
    better = summary.model_copy(
        update={
            "task_states": [
                state.model_copy(update={"score": state.score + 0.01})
                if position == 1
                else state
                for position, state in enumerate(summary.task_states)
            ]
        }
    )
    transition = adapter.compare(summary, better)
    assert transition.improved_task_keys == [summary.task_states[1].task_key]
    assert transition.regressed_task_keys == []
    assert transition.metadata == {
        "mean_dual_all_pass_rate_delta": 0.0,
        "attribution_min_task_delta": 0.0,
    }

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

    ok_detail = next(
        json.loads(path.read_text())
        for path in (evidence_dir / "tasks").glob("*.json")
        if any(
            view["kind"] == "readable_deliverable"
            for item in json.loads(path.read_text())["trials"]
            for view in item["readable_artifacts"]
        )
    )
    views = {
        view["kind"]: view
        for item in ok_detail["trials"]
        for view in item["readable_artifacts"]
    }
    trace_view = evidence_dir / views["readable_trace"]["path"]
    assert views["readable_trace"]["path"].startswith("readable/")
    assert views["readable_trace"]["source"].startswith("raw_evaluation:units/")
    assert trace_view.read_text().startswith("# Agent trace (readable view)")
    deliverable_view = evidence_dir / views["readable_deliverable"]["path"]
    assert views["readable_deliverable"]["source"] == "response.md"
    assert deliverable_view.name == "response.md.md"
    assert "# Deliverable response.md (text view)" in deliverable_view.read_text()

    original = trace_view.read_text()
    trace_view.write_text(original + "tampered\n", encoding="utf-8")
    with pytest.raises(IntegrityError, match="readable evidence hash mismatch"):
        verify_evidence_bundle(
            bundle,
            evidence_dir=evidence_dir,
            expected_checkpoint="C0",
            evaluation_dir=request.output_dir,
            source_evaluation=result,
            source_request=request,
        )
    trace_view.write_text(original, encoding="utf-8")

    score_path = next(
        request.output_dir.glob("units/*/attempt-1/grading/*/scores_judge-1.json")
    )
    score_path.write_text(score_path.read_text() + " ", encoding="utf-8")
    with pytest.raises(IntegrityError, match="score hash mismatch"):
        adapter.verify_evaluation(VerificationRequest(evaluation=request))


def _responses_settings(scratch_root: Path) -> dict[str, Any]:
    return _settings(
        scratch_root,
        judge_api="openai_responses",
        judges=[
            {
                "model": JUDGE_A,
                "max_tokens": 8192,
                "reasoning_effort": "high",
                "temperature": None,
            }
        ],
        helper=None,
    )


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"judges": [{"model": JUDGE_A, "max_tokens": JUDGE_MAX_TOKENS}]}, "two"),
        ({"helper": None}, "helper"),
        ({"judges": []}, "judges"),
    ],
)
def test_copilot_judges_keep_dual_requirements(
    tmp_path: Path, override: dict[str, Any], message: str
) -> None:
    with pytest.raises(ConfigurationError, match=message):
        HarveyAdapter(
            project_root=PROJECT_ROOT, settings=_settings(tmp_path, **override)
        )


def test_single_responses_judge_evaluates_and_verifies(
    scratch_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    single = HarveyAdapter(
        project_root=PROJECT_ROOT, settings=_responses_settings(scratch_root)
    )
    grader = _Grader(single, label="openai")
    monkeypatch.setattr(adapter_module, "pandoc_version", lambda: "3.6")
    monkeypatch.setattr(
        adapter_module, "_podman_image_id", lambda _image: FAKE_IMAGE_ID
    )
    monkeypatch.setattr(adapter_module, "Sandbox", lambda **kw: FakeSandbox(**kw))
    monkeypatch.setattr(adapter_module, "grade_with_retries", grader)
    request = EvaluationRequest(
        checkpoint="C0",
        harness_dir=HARNESS_DIR,
        split=single.split(SplitName.TRAIN),
        trials=1,
        root_seed=7,
        output_dir=tmp_path / "evaluation",
        model=ModelSettings(model="evaluated-model", max_tokens=64000),
        transport=TransportSettings(base_url="http://127.0.0.1:9/v1"),
        api_key="offline-key",
        max_concurrency=3,
    )
    runtime = _Runtime(single)
    with pytest.raises(InfrastructureError):
        single.evaluate(request, runtime)  # type: ignore[arg-type]
    result = single.evaluate(request, runtime)  # type: ignore[arg-type]

    train_ids = single.split(SplitName.TRAIN).task_ids
    rates = [
        (grader.criteria_counts[task_id] - SCRIPT[index][1][0])
        / grader.criteria_counts[task_id]
        for index, task_id in enumerate(train_ids)
    ]
    metrics = result.summary.metrics
    assert result.summary.primary.value == pytest.approx(sum(rates) / 6)
    assert metrics["judge_1_all_pass_rate"] == pytest.approx(3 / 6)
    assert "judge_2_all_pass_rate" not in metrics
    assert metrics["mean_dual_all_pass_rate"] == pytest.approx(3 / 6)
    assert metrics["both_judges_all_pass_rate"] == pytest.approx(3 / 6)
    assert metrics["judge_disagreement_count"] == 0
    assert "judges_split" not in {fact.outcome for fact in result.trial_facts}
    assert result.metadata["judges"] == [JUDGE_A]
    first = json.loads(
        next(request.output_dir.glob("units/*/attempt-1/result.json")).read_text()
    )
    assert sorted(first["grading"]["scores"]) == [
        "scores_dual.json",
        "scores_judge-1.json",
    ]
    assert single.verify_evaluation(VerificationRequest(evaluation=request)) == result

    environment = adapter_module._Environment(
        image_id=FAKE_IMAGE_ID, pandoc_version="3.6"
    )
    inputs = single._semantic_inputs(request, environment)["adapter_settings"]
    assert inputs["judge_api"] == "openai_responses"
    assert inputs["helper"] is None
    dual = HarveyAdapter(project_root=PROJECT_ROOT, settings=_settings(scratch_root))
    assert "judge_api" not in dual._semantic_inputs(request, environment)[
        "adapter_settings"
    ]

    score_path = next(
        request.output_dir.glob("units/*/attempt-1/grading/*/scores_judge-1.json")
    )
    scores = json.loads(score_path.read_text())
    scores["judge_model"] = f"copilot/{JUDGE_A}"
    score_path.write_text(json.dumps(scores), encoding="utf-8")
    with pytest.raises(IntegrityError, match="score hash mismatch"):
        single.verify_evaluation(VerificationRequest(evaluation=request))


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


# Trial processes (AHE-025)


def _isolated_adapter(scratch_root: Path) -> HarveyAdapter:
    return HarveyAdapter(
        project_root=PROJECT_ROOT,
        settings=_settings(scratch_root, trial_processes=True),
    )


def test_trial_processes_enter_semantic_inputs_only_when_set(
    adapter: HarveyAdapter, scratch_root: Path, tmp_path: Path
) -> None:
    isolated = _isolated_adapter(scratch_root)
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
        max_concurrency=1,
    )
    environment = adapter_module._Environment(
        image_id=FAKE_IMAGE_ID, pandoc_version="3.6"
    )
    plain = adapter._semantic_inputs(request, environment)["adapter_settings"]
    assert "trial_processes" not in plain
    settings = isolated._semantic_inputs(request, environment)["adapter_settings"]
    assert settings == {**plain, "trial_processes": True}


def test_trial_processes_need_the_ahe_runtime(
    scratch_root: Path, tmp_path: Path
) -> None:
    isolated = _isolated_adapter(scratch_root)
    task = isolated._tasks[isolated.split(SplitName.TRAIN).task_ids[0]]
    with pytest.raises(ConfigurationError, match="trial_processes"):
        isolated._run_agent(
            scratch=tmp_path,
            task=task,
            unit=None,  # type: ignore[arg-type]
            attempt=1,
            runtime=object(),  # type: ignore[arg-type]
            runtime_dir=tmp_path / "runtime",
            environment=adapter_module._Environment("img", "p"),
            harness_dir=HARNESS_DIR,
        )


def _chained(outer: BaseException, inner: BaseException) -> BaseException:
    outer.__cause__ = inner
    return outer


_REQUEST = httpx.Request("POST", "http://127.0.0.1:9/v1/chat/completions")


@pytest.mark.parametrize(
    ("raised", "expected", "match"),
    [
        (
            _chained(RuntimeError("tool wrapper"), APITimeoutError(request=_REQUEST)),
            InfrastructureError,
            "RuntimeError: tool wrapper",
        ),
        (
            _chained(RuntimeError("wrapper"), IntegrityError("corpus changed")),
            IntegrityError,
            "corpus changed",
        ),
        (PodmanError("podman run failed"), PodmanError, "podman run failed"),
        (ValueError("harness bug"), ValueError, "harness bug"),
    ],
)
def test_trial_process_errors_are_classified_before_pickling(
    monkeypatch: pytest.MonkeyPatch,
    raised: BaseException,
    expected: type[BaseException],
    match: str,
) -> None:
    def fail(_spec: Any) -> None:
        raise raised

    monkeypatch.setattr(adapter_module.EvaluatedAgentRuntime, "from_process_spec", fail)
    payload = SimpleNamespace(
        runtime_spec=SimpleNamespace(
            transport=TransportSettings(base_url="http://127.0.0.1:9/v1")
        )
    )
    with pytest.raises(expected, match=match) as caught:
        adapter_module._trial_process_entry(payload, None)  # type: ignore[arg-type]
    # What crosses the process boundary must survive pickling as itself.
    assert type(portable_exception(caught.value)) is expected
