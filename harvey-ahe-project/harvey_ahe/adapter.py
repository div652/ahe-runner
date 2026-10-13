"""Protected AHE V2 adapter for the Harvey LAB benchmark.

One evaluation unit is one LAB task trial:

1. copy the pinned task documents into a private scratch directory;
2. start one LAB podman sandbox (``--network=none``) over that directory;
3. bind the sandbox to the session (AHE-005/006) and run the NexAU session;
4. stop the sandbox and copy regular output files into the unit directory;
5. grade the copy with LAB's two rubric judges (``grading.py``).

Verification recomputes every score from the stored per-judge criterion
rows and never calls a model.
"""

from __future__ import annotations

import json
import os
import random
import shutil
import subprocess
import tempfile
import threading
import time
from collections import Counter
from collections.abc import Iterable, Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import lab_core.evaluation.scoring as lab_scoring
from lab_core.evaluation.scoring import pandoc_version
from lab_core.sandbox.sandbox import PodmanError, Sandbox
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ahe_runner.adapter import (
    AdapterIdentity,
    BenchmarkAdapterV2,
    BenchmarkInterface,
    CandidateValidationRequest,
    EvaluationRequest,
    EvaluationResult,
    EvaluationTrialFact,
    EvidenceBundle,
    EvidenceRequest,
    MetaPromptContext,
    MetricSummary,
    MetricValue,
    SplitDefinition,
    SplitName,
    TaskState,
    TransitionSummary,
    ValidationResult,
    VerificationRequest,
)
from ahe_runner.config import ModelSettings
from ahe_runner.errors import (
    CandidateValidationError,
    ConfigurationError,
    InfrastructureError,
    IntegrityError,
)
from ahe_runner.evaluation import evaluation_request_sha256
from ahe_runner.evaluation_store import EvaluationStore, EvaluationUnit
from ahe_runner.io import (
    atomic_write_json,
    atomic_write_text,
    read_json,
    sha256_file,
    tree_hashes,
    tree_sha256,
)
from ahe_runner.meta.model_client import is_infrastructure_exception
from ahe_runner.runtime import (
    AgentInput,
    AgentInputMessage,
    AgentOutput,
    AgentSessionRequest,
    EvaluatedAgentRuntime,
)

from .candidate_checks import harvey_candidate_errors
from .grading import JUDGE_MAX_TOKENS, dual_summary, grade_with_retries
from .tasks import (
    HarveyTask,
    copy_documents,
    eligibility_errors,
    leaf_slug,
    load_pinned_task,
    load_split_file,
)
from .workspace import MAX_EXEC_TIMEOUT_SECONDS, RESOURCE_KEY, WorkspaceHandle

ADAPTER_VERSION = "2"
RESULT_SCHEMA_VERSION = 1
PRIMARY_METRIC = "mean_criterion_pass_rate"
ALL_PASS_METRIC = "mean_dual_all_pass_rate"
SCORE_FILES = ("scores_judge-1.json", "scores_judge-2.json", "scores_dual.json")
HEALTH_CHECK_TIMEOUT_SECONDS = 30
MAX_WORKSPACE_LISTING = 500
LEAKAGE_TERMS = ("harvey-labs", "harvey_labs", "harvey labs", "harveyai")
_MAX_TURN_MARKERS = (
    "[Error: Maximum iteration limit reached",
    "[Note: Maximum iteration limit reached",
)
_CONTEXT_MARKERS = (
    "[Error: Prompt too long",
    "[Error: Insufficient tokens for response",
)
_FATAL_ERRORS = (IntegrityError, ConfigurationError, CandidateValidationError)


class HarveyAdapterSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lab_root: Path
    split_file: Path = Path("splits.json")
    judges: tuple[ModelSettings, ModelSettings]
    helper: ModelSettings
    judge_max_prompt_chars: int = Field(default=240_000, gt=0)
    sandbox_image: str = "lab-sandbox:latest"
    shell_timeout: int = Field(default=60, ge=1, le=MAX_EXEC_TIMEOUT_SECONDS)
    scratch_root: Path | None = None
    grading_parallel: int = Field(default=1, ge=1, le=100)
    judge_max_concurrent_calls: int | None = Field(default=None, ge=1, le=100)
    grading_attempts: int = Field(default=2, ge=1, le=5)

    @model_validator(mode="after")
    def validate_judges(self) -> HarveyAdapterSettings:
        if self.judges[0].model == self.judges[1].model:
            raise ValueError("Harvey dual grading requires two distinct judge models")
        for judge in self.judges:
            if judge.max_tokens != JUDGE_MAX_TOKENS or judge.temperature != 0.0:
                raise ValueError(
                    f"LAB judges require max_tokens={JUDGE_MAX_TOKENS} and "
                    "temperature=0.0"
                )
        if not self.sandbox_image.strip():
            raise ValueError("sandbox_image must be non-empty")
        return self


@dataclass(frozen=True, slots=True)
class _Environment:
    image_id: str
    pandoc_version: str


@dataclass(frozen=True, slots=True)
class _AgentRun:
    output: AgentOutput | None
    error: str | None
    finished: bool
    finish_summary: str | None
    wall_clock_seconds: float


@dataclass(frozen=True, slots=True)
class _Trial:
    unit: dict[str, Any]
    result_path: Path
    trace_path: Path
    attempt_dir: Path
    result: dict[str, Any]
    score_paths: tuple[Path, ...]
    judge_scores: tuple[dict[str, Any], dict[str, Any]]
    dual: dict[str, Any]

    @property
    def task_id(self) -> str:
        return str(self.unit["task_id"])

    @property
    def task_key(self) -> str:
        return str(self.unit["task_key"])

    @property
    def trial(self) -> int:
        return int(self.unit["trial"])

    @property
    def score(self) -> float:
        return float(self.dual["dual_criterion_pass"])

    @property
    def both_pass(self) -> bool:
        return bool(self.dual["all_pass"])


class HarveyAdapter(BenchmarkAdapterV2):
    """Run evolved NexAU harnesses on pinned Harvey LAB tasks."""

    def __init__(self, *, project_root: Path, settings: dict[str, Any]) -> None:
        super().__init__(project_root=project_root, settings=settings)
        try:
            self.config = HarveyAdapterSettings.model_validate(settings)
        except ValueError as exc:
            raise ConfigurationError(f"invalid Harvey adapter settings: {exc}") from exc
        self.lab_root = _resolve(project_root, self.config.lab_root)
        # Shared by every trial this adapter grades, so the cap is process-wide.
        self._judge_call_slots = (
            None
            if self.config.judge_max_concurrent_calls is None
            else threading.BoundedSemaphore(self.config.judge_max_concurrent_calls)
        )
        self.scratch_root = (
            None
            if self.config.scratch_root is None
            else _resolve(project_root, self.config.scratch_root)
        )
        self.split_file = load_split_file(
            _resolve(project_root, self.config.split_file)
        )
        self._tasks: dict[str, HarveyTask] = {}
        errors: list[str] = []
        for split in self.split_file.splits.values():
            for pinned in split:
                task = load_pinned_task(self.lab_root, pinned)
                errors.extend(eligibility_errors(task))
                self._tasks[task.task_id] = task
        if errors:
            raise ConfigurationError("ineligible Harvey tasks: " + "; ".join(errors))
        self.lab_core_sha256 = tree_sha256(
            Path(lab_scoring.__file__).resolve().parents[1]
        )

    def identity(self) -> AdapterIdentity:
        return AdapterIdentity(
            name="harvey-lab-ahe",
            version=ADAPTER_VERSION,
            benchmark_name="harvey-lab",
            benchmark_version=self.split_file.lab_commit,
            protocol_description=(
                "Each trial runs the evaluated NexAU harness on one LAB task in a "
                "fresh network-less LAB podman sandbox. The task instructions are "
                "the first user message; /workspace/output is graded by LAB's two "
                "rubric judges."
            ),
            native_metric_description=(
                "Mean per-trial rubric criterion pass rate, averaged over LAB's "
                "two judges. LAB's dual all-pass rate (each judge contributes 0.5 "
                "when every criterion passes) is reported as "
                "mean_dual_all_pass_rate and breaks ties."
            ),
            supervision_policy="dense",
        )

    def split(self, name: SplitName) -> SplitDefinition:
        return SplitDefinition(
            name=name,
            identity=f"{self.split_file.identity}:{name.value}",
            task_ids=[task.task_id for task in self.split_file.splits[name]],
            metadata={
                "lab_commit": self.split_file.lab_commit,
                "selection_policy": self.split_file.selection_policy,
                "split_file_sha256": self.split_file.sha256,
            },
        )

    def benchmark_interface(self) -> BenchmarkInterface:
        return BenchmarkInterface(
            description=(
                "Harvey LAB legal tasks: instructions as the first user message, "
                "read-only matter documents and writable output in a per-trial "
                "podman sandbox, graded by two rubric judges."
            ),
            input_kinds=("prompt", "data", "environment"),
            tool_delivery="none",
            notes=(
                "The harness owns every tool; tools reach the trial sandbox only "
                "through harvey_ahe.workspace.handle_for(agent_state).",
                "Only files under /workspace/output are graded.",
                "Documents are mounted read-only at /workspace/documents.",
            ),
        )

    def evaluate(
        self,
        request: EvaluationRequest,
        runtime: EvaluatedAgentRuntime,
    ) -> EvaluationResult:
        self._check_split(request)
        environment = self._environment()
        store = EvaluationStore(
            output_dir=request.output_dir,
            request_sha256=evaluation_request_sha256(request),
            semantic_inputs=self._semantic_inputs(request, environment),
            task_ids=request.split.task_ids,
            trials=request.trials,
            trial_seeds=_trial_seeds(request.root_seed, request.trials),
        )
        failures = self._run_pending(store, request, runtime, environment)
        if failures:
            raise InfrastructureError(
                f"{len(failures)} Harvey unit(s) hit infrastructure failures; "
                "completed units are kept and a resume retries the rest: "
                + " | ".join(failures[:5])
            )
        payload = self._result(request)
        store.finalize(payload.model_dump(mode="json"))
        atomic_write_json(
            request.output_dir / "adapter_result.json",
            payload.model_dump(mode="json"),
        )
        return payload

    def verify_evaluation(self, request: VerificationRequest) -> EvaluationResult:
        self._check_split(request.evaluation)
        return self._result(request.evaluation)

    def materialize_training_evidence(self, request: EvidenceRequest) -> EvidenceBundle:
        evaluation_dir = request.evaluation_dir.resolve()
        source = evaluation_dir / "adapter_result.json"
        evaluation = EvaluationResult.model_validate(read_json(source))
        if evaluation.split != SplitName.TRAIN:
            raise IntegrityError("Harvey evidence may only use the training split")
        task_ids = [str(state.task_id) for state in evaluation.summary.task_states]
        trials = self._load_trials(evaluation_dir, task_ids)
        facts = {fact.unit_id: fact for fact in evaluation.trial_facts}
        request.output_dir.mkdir(parents=True, exist_ok=True)
        task_rows: list[dict[str, Any]] = []
        overview = [
            f"# {request.checkpoint} Harvey LAB training evidence",
            "",
            f"Primary {PRIMARY_METRIC}: {evaluation.summary.primary.value:.4f}",
            "",
            "| task | state | mean criterion pass rate |",
            "|---|---|---|",
        ]
        for state in evaluation.summary.task_states:
            rows = [trial for trial in trials if trial.task_key == state.task_key]
            detail = {
                "schema_version": 3,
                "task_key": state.task_key,
                "task_id": state.task_id,
                "state": state.state,
                "metrics": state.metrics,
                "trials": [
                    _evidence_trial(
                        trial,
                        facts[str(trial.unit["unit_id"])],
                        evaluation_dir,
                    )
                    for trial in sorted(rows, key=lambda row: row.trial)
                ],
            }
            detail_path = request.output_dir / "tasks" / f"{state.task_key}.json"
            atomic_write_json(detail_path, detail)
            task_rows.append(
                {
                    "task_key": state.task_key,
                    "task_id": state.task_id,
                    "state": state.state,
                    "detail_path": detail_path.relative_to(
                        request.output_dir
                    ).as_posix(),
                    "detail_sha256": sha256_file(detail_path),
                    "trial_count": len(rows),
                }
            )
            overview.append(f"| {state.task_id} | {state.state} | {state.score:.3f} |")
        manifest = {
            "schema_version": 3,
            "checkpoint": request.checkpoint,
            "supervision_policy": "dense",
            "true_task_ids_exposed": True,
            "source_evaluation": str(source),
            "source_evaluation_id": evaluation.evaluation_id,
            "source_request_sha256": evaluation.request_sha256,
            "source_evaluation_sha256": sha256_file(source),
            "metrics": evaluation.summary.model_dump(mode="json"),
            "tasks": task_rows,
        }
        manifest_path = request.output_dir / "evidence_manifest.json"
        atomic_write_json(manifest_path, manifest)
        atomic_write_text(
            request.output_dir / "overview.md", "\n".join(overview) + "\n"
        )
        atomic_write_json(
            request.output_dir / "metrics.json",
            evaluation.summary.model_dump(mode="json"),
        )
        return EvidenceBundle(
            checkpoint=request.checkpoint,
            manifest_path=str(manifest_path),
            manifest_sha256=sha256_file(manifest_path),
            task_count=len(task_rows),
            supervision_policy="dense",
        )

    def validate_candidate(
        self,
        request: CandidateValidationRequest,
    ) -> ValidationResult:
        errors = harvey_candidate_errors(
            request.harness_dir,
            training_task_ids=request.training_task_ids,
        )
        return ValidationResult(valid=not errors, errors=errors)

    def compare(self, before: MetricSummary, after: MetricSummary) -> TransitionSummary:
        old = {row.task_key: row for row in before.task_states}
        new = {row.task_key: row for row in after.task_states}
        shared = old.keys() & new.keys()
        improved = sorted(key for key in shared if new[key].score > old[key].score)
        regressed = sorted(key for key in shared if new[key].score < old[key].score)
        return TransitionSummary(
            primary_delta=after.primary.value - before.primary.value,
            improved_task_keys=improved,
            regressed_task_keys=regressed,
            unchanged_task_keys=sorted(shared - set(improved) - set(regressed)),
            metadata={
                f"{ALL_PASS_METRIC}_delta": float(
                    after.metrics.get(ALL_PASS_METRIC, 0.0)
                )
                - float(before.metrics.get(ALL_PASS_METRIC, 0.0)),
            },
        )

    def selection_key(self, summary: MetricSummary) -> tuple[Any, ...]:
        return (
            summary.primary.value,
            float(summary.metrics.get(ALL_PASS_METRIC, 0.0)),
        )

    def meta_prompt_context(self) -> MetaPromptContext:
        training = [task.task_id for task in self.split_file.splits[SplitName.TRAIN]]
        return MetaPromptContext(
            evaluated_agent_role=(
                "legal-work agent that reads matter documents in a sandboxed "
                "/workspace and writes deliverable files (memos, redlines, "
                "spreadsheets, slides) to /workspace/output"
            ),
            benchmark_objective=(
                "Maximize the rubric criterion pass rate: the share of rubric "
                "criteria each of LAB's two judges marks as passed, averaged over "
                "both judges and all trials. LAB's dual all-pass rate (a judge "
                "passes every criterion) is also reported and breaks ties."
            ),
            evidence_description=(
                "Each training trial records the task instructions, the full NexAU "
                "message trace (tool calls and results), termination reason, "
                "finish summary, output and workspace file listings with hashes, "
                "the graded deliverable copies, and both judges' per-criterion "
                "verdicts and reasoning."
            ),
            failure_taxonomy=[
                "missing or misnamed deliverable file",
                "wrong deliverable format (for example text instead of .docx)",
                "substantive legal issue missed or mis-analysed",
                "factual or citation error relative to the documents",
                "documents not fully read (parse failures, truncated reads)",
                "turn or context limit reached before finishing",
                "finished before every deliverable was complete",
                "judges disagree (one all-pass, one not)",
            ],
            protected_protocol=[
                "Tasks, documents, rubrics, the LAB sandbox, and grading are "
                "adapter-owned.",
                "Harness tools reach the trial sandbox only through "
                "harvey_ahe.workspace.handle_for(agent_state); host network, "
                "subprocess, dynamic-import, and grader imports are rejected.",
                "The finish stop tool, tool_call_mode, model routing, and "
                "max_iterations=200 are protected.",
                "Only files under /workspace/output are graded.",
                "Validation and test tasks remain unavailable during evolution.",
            ],
            web_research_scope=(
                "Agent techniques for long legal-document review and drafting: "
                "reading strategies, issue-spotting checklists, producing .docx, "
                ".xlsx, and .pptx with python-docx, openpyxl, and python-pptx, and "
                "self-verification before finishing. Never search for the "
                "benchmark's tasks, rubrics, or solutions."
            ),
            source_exploration_scope=(
                "The seed harness, NexAU tool/middleware/sub-agent/skill "
                "attachment, and benchmark_public/ADAPTER.md (sandbox handle API "
                "and LAB tool fidelity)."
            ),
            task_id_leakage_terms=[
                *LEAKAGE_TERMS,
                *sorted({leaf_slug(task_id) for task_id in training}),
            ],
        )

    # Evaluation internals

    def _check_split(self, request: EvaluationRequest) -> None:
        if request.split != self.split(request.split.name):
            raise ConfigurationError("evaluation split differs from splits.json")

    def _environment(self) -> _Environment:
        version = pandoc_version()
        if version is None:
            raise ConfigurationError(
                "pandoc is required by LAB grading; install pandoc 3.5 or later"
            )
        return _Environment(
            image_id=_podman_image_id(self.config.sandbox_image),
            pandoc_version=version,
        )

    def _semantic_inputs(
        self,
        request: EvaluationRequest,
        environment: _Environment,
    ) -> dict[str, Any]:
        settings = self.config.model_dump(
            mode="json",
            include={
                "judges",
                "helper",
                "judge_max_prompt_chars",
                "sandbox_image",
                "shell_timeout",
            },
        )
        return {
            "adapter": self.identity().model_dump(mode="json"),
            "adapter_settings": settings,
            "checkpoint": request.checkpoint,
            "split": request.split.model_dump(mode="json"),
            "trials": request.trials,
            "root_seed": request.root_seed,
            "model": request.model.model_dump(mode="json"),
            "task_pins": [
                {
                    "task_id": task_id,
                    "task_json_sha256": self._tasks[task_id].task_json_sha256,
                    "documents_sha256": self._tasks[task_id].documents_sha256,
                }
                for task_id in request.split.task_ids
            ],
            "sandbox_image_id": environment.image_id,
            "lab_core_sha256": self.lab_core_sha256,
            "pandoc_version": environment.pandoc_version,
        }

    def _run_pending(
        self,
        store: EvaluationStore,
        request: EvaluationRequest,
        runtime: EvaluatedAgentRuntime,
        environment: _Environment,
    ) -> list[str]:
        pending = store.pending_units()
        if not pending:
            return []
        failures: list[str] = []
        pool = ThreadPoolExecutor(
            max_workers=max(1, min(request.max_concurrency, len(pending))),
            thread_name_prefix="harvey-unit",
        )
        try:
            futures = [
                pool.submit(
                    self._run_unit,
                    store=store,
                    unit=unit,
                    request=request,
                    runtime=runtime,
                    environment=environment,
                )
                for unit in pending
            ]
            for future in as_completed(futures):
                failure = future.result()
                if failure is not None:
                    failures.append(failure)
        except BaseException:
            pool.shutdown(wait=True, cancel_futures=True)
            raise
        pool.shutdown(wait=True)
        return sorted(failures)

    def _run_unit(
        self,
        *,
        store: EvaluationStore,
        unit: EvaluationUnit,
        request: EvaluationRequest,
        runtime: EvaluatedAgentRuntime,
        environment: _Environment,
    ) -> str | None:
        """Complete one unit, or return a recorded infrastructure failure."""
        attempt, attempt_dir = store.begin_attempt(unit)
        try:
            self._execute_unit(
                store=store,
                unit=unit,
                attempt=attempt,
                attempt_dir=attempt_dir,
                request=request,
                runtime=runtime,
                environment=environment,
            )
        except Exception as exc:
            _raise_if_fatal(exc)
            if not _is_infrastructure(exc):
                raise
            store.record_infrastructure_failure(
                unit,
                attempt=attempt,
                error={
                    "task_id": unit.task_id,
                    "trial": unit.trial,
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                },
            )
            return f"{unit.task_id}#{unit.trial}: {type(exc).__name__}: {exc}"
        return None

    def _execute_unit(
        self,
        *,
        store: EvaluationStore,
        unit: EvaluationUnit,
        attempt: int,
        attempt_dir: Path,
        request: EvaluationRequest,
        runtime: EvaluatedAgentRuntime,
        environment: _Environment,
    ) -> None:
        task = self._tasks[unit.task_id]
        if self.scratch_root is not None:
            self.scratch_root.mkdir(parents=True, exist_ok=True)
        scratch = Path(tempfile.mkdtemp(prefix="harvey-ahe-", dir=self.scratch_root))
        try:
            runtime_dir = (
                request.output_dir / "runtime" / unit.unit_id / f"attempt-{attempt}"
            )
            run = self._run_in_sandbox(
                scratch=scratch,
                task=task,
                unit=unit,
                attempt=attempt,
                runtime=runtime,
                runtime_dir=runtime_dir,
                environment=environment,
            )
            run_dir = attempt_dir / "run"
            output_files, skipped = _copy_outputs(
                scratch / "output", run_dir / "output"
            )
            grading_dir, dual = grade_with_retries(
                attempts=self.config.grading_attempts,
                grading_root=attempt_dir / "grading",
                task_id=task.task_id,
                task_config=task.config,
                run_dir=run_dir,
                judges=self.config.judges,
                helper=self.config.helper,
                transport=request.transport,
                api_key=request.api_key,
                max_prompt_chars=self.config.judge_max_prompt_chars,
                parallel=self.config.grading_parallel,
                call_slots=self._judge_call_slots,
            )
            messages = _trace_messages(run.output)
            tool_counts, assistant_turns = _tool_counts(messages)
            termination = _termination_reason(run)
            result = {
                "schema_version": RESULT_SCHEMA_VERSION,
                "unit_id": unit.unit_id,
                "task_id": unit.task_id,
                "task_key": unit.task_key,
                "trial": unit.trial,
                "seed": unit.seed,
                "attempt": attempt,
                "provenance": {
                    "adapter": f"harvey-lab-ahe/{ADAPTER_VERSION}",
                    "task_json_sha256": task.task_json_sha256,
                    "documents_sha256": task.documents_sha256,
                    "split_file_sha256": self.split_file.sha256,
                    "sandbox_image": self.config.sandbox_image,
                    "sandbox_image_id": environment.image_id,
                    "lab_core_sha256": self.lab_core_sha256,
                    "pandoc_version": environment.pandoc_version,
                    "runtime_dir": runtime_dir.relative_to(
                        request.output_dir
                    ).as_posix(),
                },
                "termination_reason": termination,
                "agent_error": run.error,
                "final_text": "" if run.output is None else run.output.final_text,
                "finish_called": run.finished,
                "finish_summary": run.finish_summary,
                "tool_call_counts": tool_counts,
                "tool_calls": sum(tool_counts.values()),
                "assistant_turns": assistant_turns,
                "wall_clock_seconds": round(run.wall_clock_seconds, 3),
                "output_files": output_files,
                "skipped_output_entries": skipped,
                "workspace_files": _listing(scratch / "workspace"),
                "grading": {
                    "directory": grading_dir.relative_to(attempt_dir).as_posix(),
                    "scores": {
                        name: sha256_file(grading_dir / name) for name in SCORE_FILES
                    },
                },
                "score": dual["dual_criterion_pass"],
                "dual": dual,
            }
            trace_path = attempt_dir / "trace.jsonl"
            events: list[dict[str, Any]] = [
                {"event_type": "user_input", "content": task.instructions},
                *(
                    {"event_type": "message", "index": index, "message": message}
                    for index, message in enumerate(messages)
                ),
                {
                    "event_type": "termination",
                    "termination_reason": termination,
                    "agent_error": run.error,
                    "finish_summary": run.finish_summary,
                },
            ]
            atomic_write_text(
                trace_path,
                "".join(
                    json.dumps(event, sort_keys=True, default=str) + "\n"
                    for event in events
                ),
            )
            result_path = attempt_dir / "result.json"
            atomic_write_json(result_path, result)
            store.complete_unit(
                unit,
                attempt=attempt,
                result_path=result_path,
                trace_path=trace_path,
            )
        finally:
            shutil.rmtree(scratch, ignore_errors=True)

    def _run_in_sandbox(
        self,
        *,
        scratch: Path,
        task: HarveyTask,
        unit: EvaluationUnit,
        attempt: int,
        runtime: EvaluatedAgentRuntime,
        runtime_dir: Path,
        environment: _Environment,
    ) -> _AgentRun:
        copy_documents(task, scratch / "documents")
        for name in ("workspace", "output", "nexau_cwd"):
            (scratch / name).mkdir()
        sandbox = Sandbox(
            documents_dir=scratch / "documents",
            output_dir=scratch / "output",
            workspace_dir=scratch / "workspace",
            image=environment.image_id,
            default_timeout=self.config.shell_timeout,
        )
        handle = WorkspaceHandle(sandbox, shell_timeout=self.config.shell_timeout)
        try:
            try:
                sandbox.start()
            except PodmanError as exc:
                raise InfrastructureError(
                    f"LAB sandbox failed to start: {exc}"
                ) from exc
            started = time.monotonic()
            output, error = _run_session(
                runtime=runtime,
                session_id=f"{unit.unit_id}-{attempt}",
                artifact_dir=runtime_dir,
                working_directory=scratch / "nexau_cwd",
                handle=handle,
                instructions=task.instructions,
            )
            elapsed = time.monotonic() - started
            health = sandbox.exec("true", timeout=HEALTH_CHECK_TIMEOUT_SECONDS)
            if health.returncode != 0:
                raise InfrastructureError(
                    "LAB sandbox was unhealthy after the agent run "
                    f"(exit {health.returncode}): {health.stderr.strip()[:500]}"
                )
            return _AgentRun(
                output=output,
                error=error,
                finished=handle.finished,
                finish_summary=handle.finish_summary,
                wall_clock_seconds=elapsed,
            )
        finally:
            handle.revoke()
            sandbox.stop()

    # Recomputation from stored artifacts (shared by evaluate and verify)

    def _result(self, request: EvaluationRequest) -> EvaluationResult:
        output_dir = request.output_dir.resolve()
        manifest = read_json(output_dir / "run_manifest.json")
        trials = self._load_trials(output_dir, request.split.task_ids)
        return EvaluationResult(
            evaluation_id=str(manifest["run_id"]),
            request_sha256=evaluation_request_sha256(request),
            checkpoint=request.checkpoint,
            split=request.split.name,
            summary=_summarize(trials, request.split.task_ids),
            trial_facts=[_trial_fact(trial) for trial in trials],
            artifact_hashes=_artifact_hashes(output_dir),
            metadata={
                "semantic_inputs_sha256": str(manifest["semantic_inputs_sha256"]),
                "judges": [judge.model for judge in self.config.judges],
            },
        )

    def _load_trials(self, output_dir: Path, task_ids: Sequence[str]) -> list[_Trial]:
        units_dir = output_dir.resolve() / "units"
        if not units_dir.is_dir():
            raise IntegrityError("Harvey evaluation has no units directory")
        expected_judges = [f"copilot/{judge.model}" for judge in self.config.judges]
        trials: list[_Trial] = []
        for unit_dir in sorted(path for path in units_dir.iterdir() if path.is_dir()):
            unit = read_json(unit_dir / "unit.json")
            completion = read_json(unit_dir / "complete.json")
            result_path = _inside(Path(str(completion["result_path"])), unit_dir)
            trace_path = _inside(Path(str(completion["trace_path"])), unit_dir)
            result = read_json(result_path)
            for key in ("unit_id", "task_id", "task_key", "trial", "seed"):
                if result.get(key) != unit.get(key):
                    raise IntegrityError(f"Harvey result {key} mismatch: {unit_dir}")
            if result.get("schema_version") != RESULT_SCHEMA_VERSION:
                raise IntegrityError(f"unsupported Harvey result schema: {unit_dir}")
            if unit["task_id"] not in task_ids:
                raise IntegrityError(f"Harvey unit outside the split: {unit_dir}")
            attempt_dir = result_path.parent
            grading = result["grading"]
            grading_dir = _inside(attempt_dir / str(grading["directory"]), attempt_dir)
            score_paths = tuple(grading_dir / name for name in SCORE_FILES)
            for path in score_paths:
                if (
                    not path.is_file()
                    or sha256_file(path) != grading["scores"][path.name]
                ):
                    raise IntegrityError(f"Harvey judge score hash mismatch: {path}")
            judge_scores = (read_json(score_paths[0]), read_json(score_paths[1]))
            for expected, scores in zip(expected_judges, judge_scores, strict=True):
                if scores.get("judge_model") != expected:
                    raise IntegrityError(f"Harvey judge identity mismatch: {unit_dir}")
                if scores.get("task") != unit["task_id"]:
                    raise IntegrityError(f"Harvey judge task mismatch: {unit_dir}")
            dual = dual_summary(judge_scores)
            if result.get("score") != dual["dual_criterion_pass"]:
                raise IntegrityError(
                    f"stored Harvey score disagrees with judge rows: {unit_dir}"
                )
            trials.append(
                _Trial(
                    unit=unit,
                    result_path=result_path,
                    trace_path=trace_path,
                    attempt_dir=attempt_dir,
                    result=result,
                    score_paths=score_paths,
                    judge_scores=judge_scores,
                    dual=dual,
                )
            )
        order = {task_id: index for index, task_id in enumerate(task_ids)}
        trials.sort(key=lambda trial: (order[trial.task_id], trial.trial))
        return trials


def _run_session(
    *,
    runtime: EvaluatedAgentRuntime,
    session_id: str,
    artifact_dir: Path,
    working_directory: Path,
    handle: WorkspaceHandle,
    instructions: str,
) -> tuple[AgentOutput | None, str | None]:
    session = runtime.open_session(
        AgentSessionRequest(
            session_id=session_id,
            artifact_dir=artifact_dir,
            working_directory=working_directory,
            session_resources={RESOURCE_KEY: handle},
        )
    )
    try:
        try:
            output = session.run(
                AgentInput(
                    messages=(AgentInputMessage(role="user", content=instructions),)
                )
            )
        except Exception as exc:
            _raise_if_fatal(exc)
            if _is_infrastructure(exc):
                raise
            return None, f"{type(exc).__name__}: {exc}"
        return output, None
    finally:
        session.close()


def _summarize(trials: Sequence[_Trial], task_ids: Sequence[str]) -> MetricSummary:
    if not trials:
        raise IntegrityError("Harvey evaluation has no completed trials")
    states: list[TaskState] = []
    for task_id in task_ids:
        rows = [trial for trial in trials if trial.task_id == task_id]
        if not rows:
            raise IntegrityError(f"Harvey task has no completed trials: {task_id}")
        both = sum(row.both_pass for row in rows)
        states.append(
            TaskState(
                task_key=rows[0].task_key,
                task_id=task_id,
                state=f"{both}/{len(rows)} trials all-pass for both judges",
                score=_mean(row.score for row in rows),
                successes=both,
                trials=len(rows),
                metrics={
                    "mean_criterion_pass_rate": _mean(
                        float(row.dual["dual_criterion_pass"]) for row in rows
                    ),
                    ALL_PASS_METRIC: _mean(
                        float(row.dual["dual_all_pass_rate"]) for row in rows
                    ),
                    "judge_disagreements": sum(
                        not row.dual["judges_agree"] for row in rows
                    ),
                    "termination_reasons": dict(
                        sorted(
                            Counter(
                                str(row.result["termination_reason"]) for row in rows
                            ).items()
                        )
                    ),
                    "tool_calls": sum(int(row.result["tool_calls"]) for row in rows),
                },
            )
        )
    primary = _mean(trial.score for trial in trials)
    passed = sum(sum(int(value) for value in t.dual["n_passed"]) for t in trials)
    criteria = sum(sum(int(value) for value in t.dual["n_criteria"]) for t in trials)
    judge_pass = [
        _mean(float(bool(trial.judge_scores[index]["all_pass"])) for trial in trials)
        for index in range(2)
    ]
    both = sum(trial.both_pass for trial in trials)
    return MetricSummary(
        primary=MetricValue(name=PRIMARY_METRIC, value=primary),
        metrics={
            PRIMARY_METRIC: primary,
            ALL_PASS_METRIC: _mean(
                float(trial.dual["dual_all_pass_rate"]) for trial in trials
            ),
            "both_judges_all_pass_rate": both / len(trials),
            "judge_1_all_pass_rate": judge_pass[0],
            "judge_2_all_pass_rate": judge_pass[1],
            "judge_disagreement_count": sum(
                not trial.dual["judges_agree"] for trial in trials
            ),
            "pooled_criterion_pass_rate": passed / criteria if criteria else 0.0,
            "judge_input_limit_failures": sum(
                int(trial.dual["n_judge_input_limit_failures"]) for trial in trials
            ),
            "termination_counts": dict(
                sorted(
                    Counter(
                        str(trial.result["termination_reason"]) for trial in trials
                    ).items()
                )
            ),
            "successes": both,
        },
        task_count=len(states),
        rollout_count=len(trials),
        task_states=states,
        resource_totals={
            "tool_calls": sum(int(trial.result["tool_calls"]) for trial in trials),
            "assistant_turns": sum(
                int(trial.result["assistant_turns"]) for trial in trials
            ),
            "agent_errors": sum(
                trial.result["termination_reason"] == "agent_error" for trial in trials
            ),
        },
    )


def _trial_fact(trial: _Trial) -> EvaluationTrialFact:
    return EvaluationTrialFact(
        unit_id=str(trial.unit["unit_id"]),
        outcome=_outcome(trial.dual),
        score=trial.score,
        termination_reason=str(trial.result["termination_reason"]),
        diagnostic_class="passing" if trial.both_pass else "failing",
    )


def _outcome(dual: dict[str, Any]) -> str:
    if dual["all_pass"]:
        return "all_pass"
    if not dual["judges_agree"]:
        return "judges_split"
    return "not_all_pass"


def _evidence_trial(
    trial: _Trial,
    fact: EvaluationTrialFact,
    evaluation_dir: Path,
) -> dict[str, Any]:
    def artifact(kind: str, path: Path) -> dict[str, str]:
        return {
            "kind": kind,
            "path": path.resolve().relative_to(evaluation_dir).as_posix(),
            "sha256": sha256_file(path),
        }

    output_root = trial.attempt_dir / "run" / "output"
    artifacts = [
        artifact("result", trial.result_path),
        artifact("trace", trial.trace_path),
        *(artifact("evaluator", path) for path in trial.score_paths),
        *(
            artifact("deliverable", output_root / str(row["path"]))
            for row in trial.result["output_files"]
        ),
    ]
    return {
        "unit_id": fact.unit_id,
        "trial": trial.trial,
        "seed": int(trial.unit["seed"]),
        "outcome": fact.outcome,
        "score": fact.score,
        "termination_reason": fact.termination_reason,
        "diagnostic_class": fact.diagnostic_class,
        "artifacts": artifacts,
        "native_result": trial.result,
        "judges": [
            {
                "judge_model": scores["judge_model"],
                "all_pass": scores["all_pass"],
                "n_passed": scores["n_passed"],
                "n_criteria": scores["n_criteria"],
                "criteria_results": scores["criteria_results"],
            }
            for scores in trial.judge_scores
        ],
    }


def _termination_reason(run: _AgentRun) -> str:
    if run.error is not None:
        return "agent_error"
    if run.finished:
        return "finish_tool"
    final_text = "" if run.output is None else run.output.final_text
    if any(marker in final_text for marker in _MAX_TURN_MARKERS):
        return "max_turns_exceeded"
    if any(marker in final_text for marker in _CONTEXT_MARKERS):
        return "context_overflow"
    return "no_tool_calls"


def _trace_messages(output: AgentOutput | None) -> list[dict[str, Any]]:
    if output is None:
        return []
    return list(output.trace or output.messages)


def _tool_counts(messages: Sequence[dict[str, Any]]) -> tuple[dict[str, int], int]:
    counts: Counter[str] = Counter()
    turns = 0
    for message in messages:
        if message.get("role") != "assistant":
            continue
        turns += 1
        for block in message.get("content") or []:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                counts[str(block.get("name"))] += 1
    return dict(sorted(counts.items())), turns


def _copy_outputs(
    source: Path,
    destination: Path,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Copy regular files only; symlinks and special files are skipped."""
    destination.mkdir(parents=True, exist_ok=False)
    files: list[dict[str, Any]] = []
    skipped: list[str] = []
    for path, relative in _walk(source, skipped):
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target, follow_symlinks=False)
        files.append(
            {
                "path": relative,
                "bytes": target.stat().st_size,
                "sha256": sha256_file(target),
            }
        )
    return files, skipped


def _listing(root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path, relative in _walk(root, []):
        if len(rows) >= MAX_WORKSPACE_LISTING:
            rows.append({"path": "...", "bytes": None, "truncated": True})
            break
        rows.append({"path": relative, "bytes": path.stat().st_size})
    return rows


def _walk(root: Path, skipped: list[str]) -> Iterator[tuple[Path, str]]:
    for current, dirs, names in os.walk(root, followlinks=False):
        base = Path(current)
        kept: list[str] = []
        for name in sorted(dirs):
            if (base / name).is_symlink():
                skipped.append((base / name).relative_to(root).as_posix())
            else:
                kept.append(name)
        dirs[:] = kept
        for name in sorted(names):
            path = base / name
            relative = path.relative_to(root).as_posix()
            if path.is_symlink() or not path.is_file():
                skipped.append(relative)
                continue
            yield path, relative


def _podman_image_id(image: str) -> str:
    try:
        completed = subprocess.run(
            ["podman", "image", "inspect", "--format", "{{.Id}}", image],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except FileNotFoundError as exc:
        raise ConfigurationError("podman is required for the LAB sandbox") from exc
    except subprocess.TimeoutExpired as exc:
        raise InfrastructureError("podman image inspect timed out") from exc
    image_id = completed.stdout.strip()
    if completed.returncode != 0 or not image_id:
        raise ConfigurationError(
            f"LAB sandbox image {image!r} is not available locally; build or pull "
            "it first (see HARVEY_AHE_RUNBOOK.md)"
        )
    return image_id


def _cause_chain(exc: BaseException) -> Iterator[BaseException]:
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def _raise_if_fatal(exc: BaseException) -> None:
    for item in _cause_chain(exc):
        if isinstance(item, _FATAL_ERRORS):
            raise item


def _is_infrastructure(exc: BaseException) -> bool:
    return any(
        isinstance(item, (InfrastructureError, PodmanError))
        for item in _cause_chain(exc)
    ) or is_infrastructure_exception(exc)


def _inside(path: Path, root: Path) -> Path:
    resolved = path.resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise IntegrityError(f"Harvey artifact escapes {root}: {path}") from exc
    return resolved


def _artifact_hashes(output_dir: Path) -> dict[str, str]:
    return {
        path: digest
        for path, digest in tree_hashes(output_dir).items()
        if path not in {"adapter_result.json", "evaluation_result.json"}
    }


def _trial_seeds(root_seed: int, trials: int) -> list[int]:
    generator = random.Random(root_seed)
    return [generator.randint(0, 1_000_000) for _ in range(trials)]


def _mean(values: Iterable[float]) -> float:
    items = list(values)
    return sum(items) / len(items) if items else 0.0


def _resolve(project_root: Path, path: Path) -> Path:
    expanded = path.expanduser()
    return (expanded if expanded.is_absolute() else project_root / expanded).resolve()
