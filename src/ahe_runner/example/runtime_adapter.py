"""Tiny runtime-backed benchmark with one protected interactive action."""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from ahe_runner.adapter import (
    AdapterIdentity,
    BenchmarkAdapterV2,
    BenchmarkInterface,
    BenchmarkToolSpec,
    CandidateValidationRequest,
    EvaluationRequest,
    EvaluationResult,
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
from ahe_runner.evaluation_store import EvaluationStore, EvaluationUnit
from ahe_runner.io import (
    atomic_write_json,
    read_json,
    sha256_file,
    tree_hashes,
)
from ahe_runner.runtime import (
    AgentInput,
    AgentInputMessage,
    AgentOutput,
    AgentSessionRequest,
    EvaluatedAgentRuntime,
)

LOOKUP_CODE = BenchmarkToolSpec(
    name="lookup_code",
    description=(
        "Read one code from the protected benchmark environment. "
        "The benchmark, not the harness, executes this action."
    ),
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "label": {
                "type": "string",
                "description": "The public label to look up.",
            }
        },
        "required": ["label"],
    },
)

_PROTECTED_CODES = {
    "alpha": "BLUE-7",
    "beta": "GREEN-4",
    "gamma": "AMBER-9",
}


@dataclass(frozen=True, slots=True)
class _Task:
    kind: Literal["direct", "protected_action"]
    prompt: str
    expected_text: str
    lookup_label: str | None = None


_TASKS = {
    SplitName.TRAIN: {
        "train-direct-ready": _Task(
            kind="direct",
            prompt="Return the uppercase form of the word ready and nothing else.",
            expected_text="READY",
        ),
        "train-protected-alpha": _Task(
            kind="protected_action",
            prompt=(
                "Use the lookup_code action for label alpha. After the protected "
                "result arrives, reply with CODE: followed by the returned code "
                "and nothing else."
            ),
            expected_text="CODE: BLUE-7",
            lookup_label="alpha",
        ),
    },
    SplitName.VALIDATION: {
        "validation-direct-steady": _Task(
            kind="direct",
            prompt="Return the uppercase form of the word steady and nothing else.",
            expected_text="STEADY",
        ),
        "validation-protected-beta": _Task(
            kind="protected_action",
            prompt=(
                "Use the lookup_code action for label beta. After the protected "
                "result arrives, reply with CODE: followed by the returned code "
                "and nothing else."
            ),
            expected_text="CODE: GREEN-4",
            lookup_label="beta",
        ),
    },
    SplitName.TEST: {
        "test-direct-complete": _Task(
            kind="direct",
            prompt="Return the uppercase form of the word complete and nothing else.",
            expected_text="COMPLETE",
        ),
        "test-protected-gamma": _Task(
            kind="protected_action",
            prompt=(
                "Use the lookup_code action for label gamma. After the protected "
                "result arrives, reply with CODE: followed by the returned code "
                "and nothing else."
            ),
            expected_text="CODE: AMBER-9",
            lookup_label="gamma",
        ),
    },
}


class RuntimeExampleAdapter(BenchmarkAdapterV2):
    """Small live example that exercises the public evaluated-session API."""

    def identity(self) -> AdapterIdentity:
        return AdapterIdentity(
            name="ahe-runner-runtime-example",
            version="1",
            benchmark_name="runtime-session-and-protected-action-example",
            benchmark_version="1",
            protocol_description=(
                "One direct response task and one benchmark-owned protected "
                "lookup action are executed through EvaluatedAgentRuntime."
            ),
            native_metric_description="Mean exact task-trial success.",
            supervision_policy="dense",
        )

    def split(self, name: SplitName) -> SplitDefinition:
        return SplitDefinition(
            name=name,
            identity=f"runtime-example-{name.value}-v1",
            task_ids=list(_TASKS[name]),
        )

    def benchmark_interface(self) -> BenchmarkInterface:
        return BenchmarkInterface(
            description=(
                "Tiny text benchmark with one runtime-injected, adapter-owned "
                "lookup action."
            ),
            input_kinds=("prompt", "environment"),
            tool_delivery="structured",
            protected_tools=(LOOKUP_CODE,),
            notes=(
                "lookup_code is added only to the in-memory evaluated-agent config.",
                "The adapter executes lookup_code and returns exact call-ID results.",
            ),
        )

    def evaluate(
        self,
        request: EvaluationRequest,
        runtime: EvaluatedAgentRuntime,
    ) -> EvaluationResult:
        seeds = _trial_seeds(request.root_seed, request.trials)
        store = EvaluationStore(
            output_dir=request.output_dir,
            request_sha256=evaluation_request_sha256(request),
            semantic_inputs={
                "adapter": self.identity().model_dump(mode="json"),
                "checkpoint": request.checkpoint,
                "split": request.split.model_dump(mode="json"),
                "trials": request.trials,
                "root_seed": request.root_seed,
                "model": request.model.model_dump(mode="json"),
            },
            task_ids=request.split.task_ids,
            trials=request.trials,
            trial_seeds=seeds,
        )
        for unit in store.pending_units():
            attempt, attempt_dir = store.begin_attempt(unit)
            result = _run_unit(
                runtime=runtime,
                request=request,
                unit=unit,
                attempt=attempt,
                attempt_dir=attempt_dir,
            )
            result_path = attempt_dir / "result.json"
            trace_path = attempt_dir / "trace.jsonl"
            trace_path.write_text(
                "".join(
                    json.dumps(event, sort_keys=True) + "\n"
                    for event in result.pop("_trace_events")
                ),
                encoding="utf-8",
            )
            atomic_write_json(result_path, result)
            store.complete_unit(
                unit,
                attempt=attempt,
                result_path=result_path,
                trace_path=trace_path,
            )
        summary = _summarize_output(request.output_dir, request.split.name)
        payload = EvaluationResult(
            evaluation_id=store.run_id,
            request_sha256=evaluation_request_sha256(request),
            checkpoint=request.checkpoint,
            split=request.split.name,
            summary=summary,
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
            summary=_summarize_output(
                evaluation.output_dir,
                evaluation.split.name,
            ),
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
            trials = _evidence_trials(
                evaluation_dir=request.evaluation_dir,
                task_key=state.task_key,
            )
            detail = {
                "schema_version": 2,
                "task_key": state.task_key,
                "task_id": state.task_id,
                "state": state.state,
                "trials": trials,
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
                    "trial_count": len(trials),
                }
            )
        source_result = request.evaluation_dir / "adapter_result.json"
        manifest = {
            "schema_version": 2,
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
            f"# {request.checkpoint} runtime example evidence\n\n"
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
        return (
            summary.primary.value,
            -summary.resource_totals.get("model_activations", 0),
        )

    def meta_prompt_context(self) -> MetaPromptContext:
        return MetaPromptContext(
            evaluated_agent_role=(
                "text agent that must answer directly or use a protected lookup"
            ),
            benchmark_objective=(
                "Maximize exact response success while using protected actions "
                "only when the task requires them."
            ),
            evidence_description=(
                "Each trial records the user prompt, model activations, protected "
                "action arguments/results, final text, and exact-match outcome."
            ),
            failure_taxonomy=[
                "wrong direct response",
                "missing protected action",
                "wrong protected action arguments",
                "incorrect action-result grounding",
                "repeated protected actions",
            ],
            protected_protocol=[
                "lookup_code schema and execution remain adapter-owned.",
                "The harness must not simulate or redefine lookup_code.",
                "Validation and test prompts remain unavailable during evolution.",
            ],
            web_research_scope=(
                "Reliable structured-tool use, call-result grounding, and concise "
                "completion behavior."
            ),
            source_exploration_scope=(
                "NexAU structured-tool attachment, session continuation, and "
                "evolvable harness component registration."
            ),
            task_id_leakage_terms=["train-", "validation-", "test-"],
        )


def _run_unit(
    *,
    runtime: EvaluatedAgentRuntime,
    request: EvaluationRequest,
    unit: EvaluationUnit,
    attempt: int,
    attempt_dir: Path,
) -> dict[str, Any]:
    task = _TASKS[request.split.name][unit.task_id]
    runtime_dir = request.output_dir / "runtime" / unit.unit_id / f"attempt-{attempt}"
    session = runtime.open_session(
        AgentSessionRequest(
            session_id=f"{unit.unit_id}-{attempt}",
            artifact_dir=runtime_dir,
            benchmark_tools=(LOOKUP_CODE,),
            context={
                "task_id": unit.task_id,
                "trial_seed": str(unit.seed),
            },
        )
    )
    trace_events: list[dict[str, Any]] = [
        {
            "event_type": "user_input",
            "task_id": unit.task_id,
            "trial": unit.trial,
            "content": task.prompt,
        }
    ]
    action_records: list[dict[str, Any]] = []
    try:
        output = session.run(
            AgentInput(messages=(AgentInputMessage(role="user", content=task.prompt),))
        )
        activations = 1
        trace_events.append(_output_event(activations, output))
        while output.external_actions and activations < 4:
            result_messages: list[AgentInputMessage] = []
            for action in output.external_actions:
                action_result, is_error = _execute_action(
                    action.name,
                    action.arguments,
                )
                action_records.append(
                    {
                        "tool_call_id": action.tool_call_id,
                        "name": action.name,
                        "arguments": action.arguments,
                        "result": action_result,
                        "is_error": is_error,
                    }
                )
                trace_events.append(
                    {
                        "event_type": "protected_action_result",
                        **action_records[-1],
                    }
                )
                result_messages.append(
                    AgentInputMessage(
                        role="tool",
                        tool_call_id=action.tool_call_id,
                        content=action_result,
                        is_error=is_error,
                    )
                )
            output = session.run(AgentInput(messages=tuple(result_messages)))
            activations += 1
            trace_events.append(_output_event(activations, output))
        termination_reason = "action_limit" if output.external_actions else "complete"
        reward = _score_task(
            task,
            final_text=output.final_text,
            actions=action_records,
            termination_reason=termination_reason,
        )
        return {
            "unit_id": unit.unit_id,
            "task_id": unit.task_id,
            "task_key": unit.task_key,
            "trial": unit.trial,
            "seed": unit.seed,
            "reward": reward,
            "final_text": output.final_text,
            "protected_actions": action_records,
            "activation_count": activations,
            "termination_reason": termination_reason,
            "_trace_events": trace_events,
        }
    finally:
        session.close()


def _execute_action(
    name: str,
    arguments: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    if name != LOOKUP_CODE.name:
        return {"status": "error", "error": f"unsupported action: {name}"}, True
    label = arguments.get("label")
    if not isinstance(label, str) or label not in _PROTECTED_CODES:
        return {"status": "error", "error": "unknown code label"}, True
    return {
        "status": "ok",
        "label": label,
        "code": _PROTECTED_CODES[label],
    }, False


def _output_event(sequence: int, output: AgentOutput) -> dict[str, Any]:
    return {
        "event_type": "agent_output",
        "sequence": sequence,
        "final_text": output.final_text,
        "external_actions": [
            action.model_dump(mode="json") for action in output.external_actions
        ],
    }


def _score_task(
    task: _Task,
    *,
    final_text: str,
    actions: list[dict[str, Any]],
    termination_reason: str,
) -> float:
    if termination_reason != "complete" or final_text.strip() != task.expected_text:
        return 0.0
    if task.kind == "direct":
        return 1.0
    expected_actions = [
        action
        for action in actions
        if action["name"] == LOOKUP_CODE.name
        and action["arguments"] == {"label": task.lookup_label}
        and action["result"].get("status") == "ok"
        and action["result"].get("code") == _PROTECTED_CODES[task.lookup_label or ""]
        and action["is_error"] is False
    ]
    return 1.0 if len(actions) == 1 and len(expected_actions) == 1 else 0.0


def _summarize_output(
    output_dir: Path,
    split_name: SplitName,
) -> MetricSummary:
    rows_by_task: dict[str, list[dict[str, Any]]] = {}
    for unit_dir in sorted((output_dir / "units").iterdir()):
        unit = read_json(unit_dir / "unit.json")
        completion = read_json(unit_dir / "complete.json")
        result = read_json(Path(completion["result_path"]))
        if (
            result["unit_id"] != unit["unit_id"]
            or result["task_id"] != unit["task_id"]
            or result["task_key"] != unit["task_key"]
            or result["trial"] != unit["trial"]
            or result["seed"] != unit["seed"]
        ):
            raise ValueError("runtime example result identity does not match its unit")
        task = _TASKS[split_name][str(unit["task_id"])]
        recomputed = _score_task(
            task,
            final_text=str(result["final_text"]),
            actions=list(result["protected_actions"]),
            termination_reason=str(result["termination_reason"]),
        )
        rows_by_task.setdefault(str(unit["task_id"]), []).append(
            {**result, "reward": recomputed}
        )
    states: list[TaskState] = []
    total_successes = 0
    total_rollouts = 0
    total_activations = 0
    for task_id, rows in sorted(rows_by_task.items()):
        successes = sum(int(row["reward"]) for row in rows)
        trials = len(rows)
        total_successes += successes
        total_rollouts += trials
        total_activations += sum(int(row["activation_count"]) for row in rows)
        states.append(
            TaskState(
                task_key=str(rows[0]["task_key"]),
                task_id=task_id,
                state=f"{successes}/{trials}",
                score=successes / trials,
                successes=successes,
                trials=trials,
                metrics={
                    "model_activations": sum(
                        int(row["activation_count"]) for row in rows
                    )
                },
            )
        )
    value = total_successes / total_rollouts
    return MetricSummary(
        primary=MetricValue(name="mean_exact_success", value=value),
        metrics={
            "mean_exact_success": value,
            "successes": total_successes,
        },
        task_count=len(states),
        rollout_count=total_rollouts,
        task_states=states,
        resource_totals={"model_activations": total_activations},
    )


def _evidence_trials(
    *,
    evaluation_dir: Path,
    task_key: str,
) -> list[dict[str, Any]]:
    trials: list[dict[str, Any]] = []
    for unit_dir in sorted((evaluation_dir / "units").iterdir()):
        unit = read_json(unit_dir / "unit.json")
        if unit["task_key"] != task_key:
            continue
        completion = read_json(unit_dir / "complete.json")
        result_path = Path(completion["result_path"])
        trace_path = Path(completion["trace_path"])
        result = read_json(result_path)
        task = _TASKS[SplitName.TRAIN][str(unit["task_id"])]
        reward = _score_task(
            task,
            final_text=str(result["final_text"]),
            actions=list(result["protected_actions"]),
            termination_reason=str(result["termination_reason"]),
        )
        trials.append(
            {
                "unit_id": unit["unit_id"],
                "trial": unit["trial"],
                "seed": unit["seed"],
                "outcome": "pass" if reward else "fail",
                "score": reward,
                "termination_reason": result["termination_reason"],
                "artifacts": [
                    {
                        "kind": "result",
                        "path": result_path.relative_to(evaluation_dir).as_posix(),
                        "sha256": sha256_file(result_path),
                    },
                    {
                        "kind": "trace",
                        "path": trace_path.relative_to(evaluation_dir).as_posix(),
                        "sha256": sha256_file(trace_path),
                    },
                ],
                "native_result": result,
            }
        )
    trials.sort(key=lambda row: int(row["trial"]))
    return trials


def _trial_seeds(root_seed: int, trials: int) -> list[int]:
    generator = random.Random(root_seed)
    return [generator.randint(0, 1_000_000) for _ in range(trials)]


def _evaluation_artifact_hashes(output_dir: Path) -> dict[str, str]:
    return {
        path: digest
        for path, digest in tree_hashes(output_dir).items()
        if path not in {"adapter_result.json", "evaluation_result.json"}
    }
