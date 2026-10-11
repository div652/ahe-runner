"""Runner-owned verification for evaluation request, coverage, and artifacts."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from .adapter import EvaluationRequest, EvaluationResult
from .errors import IntegrityError
from .io import read_json, sha256_file, sha256_json, tree_hashes

_RESULT_FILES = frozenset({"adapter_result.json", "evaluation_result.json"})


@dataclass(frozen=True, slots=True)
class VerifiedEvaluationUnit:
    unit_id: str
    task_id: str
    task_key: str
    trial: int
    seed: int
    result_path: str
    result_sha256: str
    trace_path: str
    trace_sha256: str
    metadata_path: str | None = None
    metadata_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class VerifiedEvaluation:
    request_sha256: str
    units: tuple[VerifiedEvaluationUnit, ...]


def evaluation_request_payload(request: EvaluationRequest) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "checkpoint": request.checkpoint,
        "harness_sha256": sha256_json(tree_hashes(request.harness_dir)),
        "split": request.split.model_dump(mode="json"),
        "trials": request.trials,
        "root_seed": request.root_seed,
        "model": request.model.model_dump(mode="json"),
        "transport": request.transport.model_dump(mode="json"),
        "max_concurrency": request.max_concurrency,
    }


def evaluation_request_sha256(request: EvaluationRequest) -> str:
    return sha256_json(evaluation_request_payload(request))


def verify_evaluation_output(
    request: EvaluationRequest,
    result: EvaluationResult,
) -> VerifiedEvaluation:
    request_sha = evaluation_request_sha256(request)
    if result.request_sha256 != request_sha:
        raise IntegrityError("evaluation request identity mismatch")
    if result.checkpoint != request.checkpoint:
        raise IntegrityError("evaluation checkpoint identity mismatch")
    if result.split != request.split.name:
        raise IntegrityError("evaluation split identity mismatch")

    manifest_path = request.output_dir / "run_manifest.json"
    if not manifest_path.is_file():
        raise IntegrityError("evaluation run_manifest.json is missing")
    manifest = read_json(manifest_path)
    if manifest.get("schema_version") != 1:
        raise IntegrityError("unsupported evaluation run manifest")
    if manifest.get("run_id") != result.evaluation_id:
        raise IntegrityError("evaluation run identity mismatch")
    if manifest.get("request_sha256") != request_sha:
        raise IntegrityError("evaluation run manifest request mismatch")
    semantic_inputs = manifest.get("semantic_inputs")
    if not isinstance(semantic_inputs, dict) or manifest.get(
        "semantic_inputs_sha256"
    ) != sha256_json(semantic_inputs):
        raise IntegrityError("evaluation semantic-input hash mismatch")
    if manifest.get("task_ids") != request.split.task_ids:
        raise IntegrityError("evaluation task coverage does not match the split")
    if manifest.get("trials") != request.trials:
        raise IntegrityError("evaluation trial count does not match the request")
    trial_seeds = manifest.get("trial_seeds")
    if not isinstance(trial_seeds, list) or len(trial_seeds) != request.trials:
        raise IntegrityError("evaluation trial seeds are incomplete")

    units = _verify_units(
        output_dir=request.output_dir,
        run_id=result.evaluation_id,
        task_ids=request.split.task_ids,
        trials=request.trials,
        trial_seeds=trial_seeds,
    )
    _verify_summary(request, result, units)
    _verify_artifact_map(request.output_dir, result.artifact_hashes)
    return VerifiedEvaluation(request_sha256=request_sha, units=tuple(units))


def _verify_units(
    *,
    output_dir: Path,
    run_id: str,
    task_ids: list[str],
    trials: int,
    trial_seeds: list[Any],
) -> list[VerifiedEvaluationUnit]:
    units_dir = output_dir / "units"
    if not units_dir.is_dir():
        raise IntegrityError("evaluation units directory is missing")
    directories = sorted(path for path in units_dir.iterdir() if path.is_dir())
    expected_count = len(task_ids) * trials
    if len(directories) != expected_count:
        raise IntegrityError(
            f"evaluation unit coverage mismatch: expected {expected_count}, "
            f"found {len(directories)}"
        )
    expected_pairs = {
        (task_id, trial) for task_id in task_ids for trial in range(trials)
    }
    observed_pairs: set[tuple[str, int]] = set()
    task_keys: dict[str, str] = {}
    key_owners: dict[str, str] = {}
    verified: list[VerifiedEvaluationUnit] = []
    for directory in directories:
        unit = read_json(directory / "unit.json")
        unit_id = str(unit.get("unit_id", ""))
        task_id = str(unit.get("task_id", ""))
        task_key = str(unit.get("task_key", ""))
        trial = unit.get("trial")
        seed = unit.get("seed")
        if unit_id != directory.name or unit.get("run_id") != run_id:
            raise IntegrityError(f"evaluation unit identity mismatch: {directory}")
        if (
            task_id not in task_ids
            or not task_key
            or isinstance(trial, bool)
            or not isinstance(trial, int)
            or not 0 <= trial < trials
            or isinstance(seed, bool)
            or not isinstance(seed, int)
            or seed != trial_seeds[trial]
        ):
            raise IntegrityError(f"evaluation unit request mismatch: {directory}")
        pair = (task_id, trial)
        if pair in observed_pairs:
            raise IntegrityError(f"duplicate evaluation unit: {pair}")
        observed_pairs.add(pair)
        previous_key = task_keys.setdefault(task_id, task_key)
        if previous_key != task_key:
            raise IntegrityError(f"task key changed across trials: {task_id}")
        previous_owner = key_owners.setdefault(task_key, task_id)
        if previous_owner != task_id:
            raise IntegrityError(f"task key is shared by multiple tasks: {task_key}")

        completion = read_json(directory / "complete.json")
        attempt = completion.get("attempt")
        if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 1:
            raise IntegrityError(f"invalid evaluation attempt: {directory}")
        result_path, result_hash = _completion_artifact(
            directory,
            completion,
            path_key="result_path",
            hash_key="result_sha256",
        )
        trace_path, trace_hash = _completion_artifact(
            directory,
            completion,
            path_key="trace_path",
            hash_key="trace_sha256",
        )
        metadata_path: str | None = None
        metadata_hash: str | None = None
        if "metadata_path" in completion or "metadata_sha256" in completion:
            metadata_path, metadata_hash = _completion_artifact(
                directory,
                completion,
                path_key="metadata_path",
                hash_key="metadata_sha256",
            )
        verified.append(
            VerifiedEvaluationUnit(
                unit_id=unit_id,
                task_id=task_id,
                task_key=task_key,
                trial=trial,
                seed=seed,
                result_path=result_path,
                result_sha256=result_hash,
                trace_path=trace_path,
                trace_sha256=trace_hash,
                metadata_path=metadata_path,
                metadata_sha256=metadata_hash,
            )
        )
    if observed_pairs != expected_pairs:
        raise IntegrityError("evaluation task/trial coverage is incomplete")
    return verified


def _completion_artifact(
    unit_dir: Path,
    completion: dict[str, Any],
    *,
    path_key: str,
    hash_key: str,
) -> tuple[str, str]:
    value = completion.get(path_key)
    digest = completion.get(hash_key)
    if not isinstance(value, str) or not isinstance(digest, str):
        raise IntegrityError(f"evaluation completion omits {path_key}/{hash_key}")
    path = Path(value).resolve()
    try:
        relative = path.relative_to(unit_dir.resolve()).as_posix()
    except ValueError as exc:
        raise IntegrityError(f"evaluation artifact escapes its unit: {path}") from exc
    if not path.is_file() or sha256_file(path) != digest:
        raise IntegrityError(f"evaluation artifact hash mismatch: {path}")
    return f"units/{unit_dir.name}/{relative}", digest


def _verify_summary(
    request: EvaluationRequest,
    result: EvaluationResult,
    units: list[VerifiedEvaluationUnit],
) -> None:
    summary = result.summary
    expected_tasks = set(request.split.task_ids)
    if summary.task_count != len(expected_tasks):
        raise IntegrityError("evaluation summary task_count mismatch")
    if summary.rollout_count != len(units):
        raise IntegrityError("evaluation summary rollout_count mismatch")
    if not math.isfinite(summary.primary.value):
        raise IntegrityError("evaluation primary metric is not finite")
    primary_metric = summary.metrics.get(summary.primary.name)
    if (
        isinstance(primary_metric, bool)
        or not isinstance(primary_metric, (int, float))
        or float(primary_metric) != summary.primary.value
    ):
        raise IntegrityError("evaluation primary metric is inconsistent")
    states_by_id: dict[str, Any] = {}
    state_keys: set[str] = set()
    unit_keys = {unit.task_id: unit.task_key for unit in units}
    for state in summary.task_states:
        if state.task_id is None or state.task_id in states_by_id:
            raise IntegrityError("evaluation task states require unique task IDs")
        if state.task_key in state_keys:
            raise IntegrityError("evaluation task states require unique task keys")
        if state.task_key != unit_keys.get(state.task_id):
            raise IntegrityError("evaluation task state key mismatch")
        if state.trials != request.trials:
            raise IntegrityError("evaluation task state trial coverage mismatch")
        if state.successes is not None and not 0 <= state.successes <= request.trials:
            raise IntegrityError("evaluation task state successes are invalid")
        if not math.isfinite(state.score):
            raise IntegrityError("evaluation task score is not finite")
        states_by_id[state.task_id] = state
        state_keys.add(state.task_key)
    if set(states_by_id) != expected_tasks:
        raise IntegrityError("evaluation task states do not cover the requested split")


def _verify_artifact_map(
    output_dir: Path,
    artifact_hashes: dict[str, str],
) -> None:
    normalized: dict[str, str] = {}
    for relative, digest in artifact_hashes.items():
        path = PurePosixPath(relative)
        if (
            not relative
            or "\\" in relative
            or path.is_absolute()
            or any(part in {"", ".", ".."} for part in path.parts)
            or path.as_posix() in _RESULT_FILES
        ):
            raise IntegrityError(f"invalid evaluation artifact path: {relative!r}")
        normalized[path.as_posix()] = digest
    actual = {
        relative: digest
        for relative, digest in tree_hashes(output_dir).items()
        if relative not in _RESULT_FILES
    }
    if normalized != actual:
        missing = sorted(set(actual) - set(normalized))
        extra = sorted(set(normalized) - set(actual))
        mismatched = sorted(
            path
            for path in set(actual) & set(normalized)
            if actual[path] != normalized[path]
        )
        raise IntegrityError(
            "evaluation artifact coverage/hash mismatch; "
            f"missing={missing[:5]}, extra={extra[:5]}, "
            f"mismatched={mismatched[:5]}"
        )
