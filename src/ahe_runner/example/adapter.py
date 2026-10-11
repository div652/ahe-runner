"""Deterministic benchmark adapter for installation and recovery checks."""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

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
from ahe_runner.evaluation import evaluation_request_sha256
from ahe_runner.evaluation_store import EvaluationStore
from ahe_runner.io import (
    atomic_write_json,
    read_json,
    sha256_file,
    tree_hashes,
)
from ahe_runner.runtime import EvaluatedAgentRuntime

_TASKS = {
    SplitName.TRAIN: {
        "train-baseline": None,
        "train-verify-result": "verify tool results",
        "train-track-state": "track confirmed state",
        "train-stop-loop": "stop repeated actions",
    },
    SplitName.VALIDATION: {
        "validation-confirm-actions": "verify tool results",
        "validation-state-grounding": "track confirmed state",
        "validation-loop-recovery": "stop repeated actions",
    },
    SplitName.TEST: {
        "test-confirmation": "verify tool results",
        "test-state": "track confirmed state",
        "test-loop": "stop repeated actions",
        "test-completion": "completion criteria",
    },
}


class ExampleAdapter(BenchmarkAdapterV2):
    def identity(self) -> AdapterIdentity:
        return AdapterIdentity(
            name="ahe-runner-example",
            version="2",
            benchmark_name="deterministic-reliability-example",
            benchmark_version="1",
            protocol_description=(
                "Synthetic tasks check whether general reliability mechanisms "
                "are present in the system prompt."
            ),
            native_metric_description="Mean binary task-trial success.",
            supervision_policy="dense",
        )

    def split(self, name: SplitName) -> SplitDefinition:
        return SplitDefinition(
            name=name,
            identity=f"example-{name.value}-v2",
            task_ids=list(_TASKS[name]),
        )

    def benchmark_interface(self) -> BenchmarkInterface:
        return BenchmarkInterface(
            description=(
                "Synthetic prompt-inspection tasks with no benchmark-owned "
                "structured actions."
            ),
            input_kinds=("prompt",),
            tool_delivery="none",
            notes=(
                "The deterministic example does not spend evaluated-model calls; "
                "production V2 adapters open sessions through the supplied runtime.",
            ),
        )

    def evaluate(
        self,
        request: EvaluationRequest,
        runtime: EvaluatedAgentRuntime,
    ) -> EvaluationResult:
        del runtime
        seeds = _trial_seeds(request.root_seed, request.trials)
        semantic_inputs = {
            "adapter": self.identity().model_dump(mode="json"),
            "checkpoint": request.checkpoint,
            "harness_sha256": _harness_prompt_hash(request.harness_dir),
            "split": request.split.model_dump(mode="json"),
            "trials": request.trials,
            "root_seed": request.root_seed,
            "model": request.model.model_dump(mode="json"),
        }
        store = EvaluationStore(
            output_dir=request.output_dir,
            request_sha256=evaluation_request_sha256(request),
            semantic_inputs=semantic_inputs,
            task_ids=request.split.task_ids,
            trials=request.trials,
            trial_seeds=seeds,
        )
        prompt = (
            (request.harness_dir / "systemprompt.md")
            .read_text(encoding="utf-8")
            .casefold()
        )
        for unit in store.pending_units():
            attempt, attempt_dir = store.begin_attempt(unit)
            required = _TASKS[request.split.name][unit.task_id]
            reward = 1.0 if required is None or required in prompt else 0.0
            result = {
                "unit_id": unit.unit_id,
                "task_id": unit.task_id,
                "task_key": unit.task_key,
                "trial": unit.trial,
                "seed": unit.seed,
                "reward": reward,
                "required_mechanism": required,
                "termination_reason": "complete",
            }
            result_path = attempt_dir / "result.json"
            trace_path = attempt_dir / "trace.jsonl"
            atomic_write_json(result_path, result)
            trace_path.write_text(
                json.dumps(
                    {
                        "sequence": 1,
                        "event_type": "synthetic_evaluation",
                        **result,
                    },
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            store.complete_unit(
                unit,
                attempt=attempt,
                result_path=result_path,
                trace_path=trace_path,
            )
        summary = _summarize(store)
        payload = EvaluationResult(
            evaluation_id=store.run_id,
            request_sha256=evaluation_request_sha256(request),
            checkpoint=request.checkpoint,
            split=request.split.name,
            summary=summary,
            trial_facts=_trial_facts(request.output_dir),
            artifact_hashes=_evaluation_artifact_hashes(request.output_dir),
        )
        store.finalize(payload.model_dump(mode="json"))
        atomic_write_json(
            request.output_dir / "adapter_result.json",
            payload.model_dump(mode="json"),
        )
        return payload

    def verify_evaluation(
        self,
        request: VerificationRequest,
    ) -> EvaluationResult:
        evaluation = request.evaluation
        manifest = read_json(evaluation.output_dir / "run_manifest.json")
        return EvaluationResult(
            evaluation_id=str(manifest["run_id"]),
            request_sha256=evaluation_request_sha256(evaluation),
            checkpoint=evaluation.checkpoint,
            split=evaluation.split.name,
            summary=_summarize_output(evaluation.output_dir),
            trial_facts=_trial_facts(evaluation.output_dir),
            artifact_hashes=_evaluation_artifact_hashes(evaluation.output_dir),
        )

    def materialize_training_evidence(
        self,
        request: EvidenceRequest,
    ) -> EvidenceBundle:
        evaluation = EvaluationResult.model_validate(
            read_json(request.evaluation_dir / "adapter_result.json")
        )
        request.output_dir.mkdir(parents=True, exist_ok=True)
        task_rows: list[dict[str, Any]] = []
        for state in evaluation.summary.task_states:
            source_units = []
            for unit_dir in sorted((request.evaluation_dir / "units").iterdir()):
                unit = read_json(unit_dir / "unit.json")
                if unit["task_key"] != state.task_key:
                    continue
                completion = read_json(unit_dir / "complete.json")
                result_path = Path(completion["result_path"])
                result = read_json(result_path)
                trace_path = Path(completion["trace_path"])
                source_units.append(
                    {
                        "unit_id": unit["unit_id"],
                        "trial": unit["trial"],
                        "seed": unit["seed"],
                        "outcome": "pass" if result["reward"] else "fail",
                        "score": result["reward"],
                        "termination_reason": result["termination_reason"],
                        "diagnostic_class": (
                            "passing" if result["reward"] else "failing"
                        ),
                        "artifacts": [
                            {
                                "kind": "result",
                                "path": result_path.relative_to(
                                    request.evaluation_dir
                                ).as_posix(),
                                "sha256": sha256_file(result_path),
                            },
                            {
                                "kind": "trace",
                                "path": trace_path.relative_to(
                                    request.evaluation_dir
                                ).as_posix(),
                                "sha256": sha256_file(trace_path),
                            },
                        ],
                        "native_result": result,
                    }
                )
            source_units.sort(key=lambda row: int(row["trial"]))
            detail = {
                "schema_version": 3,
                "task_key": state.task_key,
                "task_id": state.task_id,
                "state": state.state,
                "trials": source_units,
            }
            detail_path = request.output_dir / "tasks" / f"{state.task_key}.json"
            atomic_write_json(detail_path, detail)
            task_rows.append(
                {
                    "task_key": state.task_key,
                    "task_id": state.task_id,
                    "state": state.state,
                    "detail_path": str(detail_path.relative_to(request.output_dir)),
                    "detail_sha256": sha256_file(detail_path),
                    "trial_count": len(source_units),
                }
            )
        source_result = request.evaluation_dir / "adapter_result.json"
        manifest = {
            "schema_version": 3,
            "checkpoint": request.checkpoint,
            "supervision_policy": "dense",
            "true_task_ids_exposed": True,
            "source_evaluation": str(source_result),
            "source_evaluation_id": evaluation.evaluation_id,
            "source_request_sha256": evaluation.request_sha256,
            "source_evaluation_sha256": sha256_file(source_result),
            "metrics": evaluation.summary.model_dump(mode="json"),
            "tasks": task_rows,
        }
        manifest_path = request.output_dir / "evidence_manifest.json"
        atomic_write_json(manifest_path, manifest)
        (request.output_dir / "overview.md").write_text(
            f"# {request.checkpoint} example training evidence\n\n"
            f"Primary score: {evaluation.summary.primary.value:.4f}\n",
            encoding="utf-8",
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
        del request
        return ValidationResult(valid=True)

    def compare(
        self,
        before: MetricSummary,
        after: MetricSummary,
    ) -> TransitionSummary:
        old = {row.task_key: row for row in before.task_states}
        new = {row.task_key: row for row in after.task_states}
        improved = [
            key for key in old.keys() & new.keys() if new[key].score > old[key].score
        ]
        regressed = [
            key for key in old.keys() & new.keys() if new[key].score < old[key].score
        ]
        unchanged = sorted((old.keys() & new.keys()) - set(improved) - set(regressed))
        return TransitionSummary(
            primary_delta=after.primary.value - before.primary.value,
            improved_task_keys=sorted(improved),
            regressed_task_keys=sorted(regressed),
            unchanged_task_keys=unchanged,
        )

    def selection_key(self, summary: MetricSummary) -> tuple[Any, ...]:
        return (summary.primary.value, -summary.resource_totals.get("calls", 0))

    def meta_prompt_context(self) -> MetaPromptContext:
        return MetaPromptContext(
            evaluated_agent_role="synthetic reliability agent",
            benchmark_objective="Maximize mean binary task-trial success.",
            evidence_description=(
                "Each task states which general reliability mechanism is absent."
            ),
            failure_taxonomy=["missing general reliability mechanism"],
            protected_protocol=[
                "Task split and scoring remain outside the candidate harness."
            ],
            web_research_scope="General tool-agent reliability mechanisms.",
            source_exploration_scope="NexAU component attachment and validation.",
            task_id_leakage_terms=["train-", "validation-", "test-"],
        )


def _trial_seeds(root_seed: int, trials: int) -> list[int]:
    generator = random.Random(root_seed)
    return [generator.randint(0, 1_000_000) for _ in range(trials)]


def _harness_prompt_hash(harness_dir: Path) -> str:
    return sha256_file(harness_dir / "systemprompt.md")


def _summarize(store: EvaluationStore) -> MetricSummary:
    return _summarize_output(store.output_dir)


def _summarize_output(output_dir: Path) -> MetricSummary:
    by_task: dict[str, list[dict[str, Any]]] = {}
    for unit_dir in sorted((output_dir / "units").iterdir()):
        unit = read_json(unit_dir / "unit.json")
        completion = read_json(unit_dir / "complete.json")
        result = read_json(Path(completion["result_path"]))
        if result["task_id"] != unit["task_id"]:
            raise ValueError("result task identity does not match unit identity")
        by_task.setdefault(str(unit["task_id"]), []).append(result)
    states: list[TaskState] = []
    total_successes = 0
    total_rollouts = 0
    for task_id, rows in sorted(by_task.items()):
        successes = sum(int(float(row["reward"])) for row in rows)
        trials = len(rows)
        total_successes += successes
        total_rollouts += trials
        states.append(
            TaskState(
                task_key=rows[0]["task_key"],
                task_id=task_id,
                state=f"{successes}/{trials}",
                score=successes / trials,
                successes=successes,
                trials=trials,
            )
        )
    value = total_successes / total_rollouts
    return MetricSummary(
        primary=MetricValue(name="mean_success", value=value),
        metrics={"mean_success": value, "successes": total_successes},
        task_count=len(states),
        rollout_count=total_rollouts,
        task_states=states,
        resource_totals={"calls": total_rollouts},
    )


def _trial_facts(output_dir: Path) -> list[EvaluationTrialFact]:
    facts: list[EvaluationTrialFact] = []
    for unit_dir in sorted((output_dir / "units").iterdir()):
        unit = read_json(unit_dir / "unit.json")
        completion = read_json(unit_dir / "complete.json")
        result = read_json(Path(completion["result_path"]))
        reward = float(result["reward"])
        facts.append(
            EvaluationTrialFact(
                unit_id=str(unit["unit_id"]),
                outcome="pass" if reward else "fail",
                score=reward,
                termination_reason=str(result["termination_reason"]),
                diagnostic_class="passing" if reward else "failing",
            )
        )
    return facts


def _evaluation_artifact_hashes(output_dir: Path) -> dict[str, str]:
    return {
        path: digest
        for path, digest in tree_hashes(output_dir).items()
        if path not in {"adapter_result.json", "evaluation_result.json"}
    }
