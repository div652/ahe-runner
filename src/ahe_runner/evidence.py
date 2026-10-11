"""Validation and loading for adapter-produced training evidence."""

from __future__ import annotations

import math
from pathlib import Path, PurePosixPath
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from .adapter import (
    EvaluationRequest,
    EvaluationResult,
    EvidenceBundle,
    MetricSummary,
)
from .errors import IntegrityError
from .evaluation import verify_evaluation_output
from .io import read_json, sha256_file


class EvidenceModel(BaseModel):
    model_config = ConfigDict(extra="allow")


class EvidenceTask(EvidenceModel):
    task_key: str
    task_id: str | None = None
    state: str
    detail_path: str
    detail_sha256: str
    trial_count: int = Field(gt=0)


class EvidenceArtifact(EvidenceModel):
    kind: str
    path: str
    sha256: str

    @field_validator("kind", "path", "sha256")
    @classmethod
    def require_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("evidence artifact fields must not be empty")
        return normalized


class EvidenceTrial(EvidenceModel):
    unit_id: str
    trial: int = Field(ge=0)
    seed: int
    outcome: str
    score: float | None = None
    termination_reason: str
    artifacts: list[EvidenceArtifact] = Field(min_length=2)

    @field_validator("unit_id", "outcome", "termination_reason")
    @classmethod
    def require_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("evidence trial fields must not be empty")
        return normalized

    @field_validator("score")
    @classmethod
    def require_finite_score(cls, value: float | None) -> float | None:
        if value is not None and not math.isfinite(value):
            raise ValueError("evidence trial score must be finite")
        return value

    @model_validator(mode="after")
    def require_trace_and_result(self) -> EvidenceTrial:
        kinds = {artifact.kind for artifact in self.artifacts}
        if "trace" not in kinds:
            raise ValueError("evidence trial requires a trace artifact")
        if not kinds & {"result", "evaluator"}:
            raise ValueError("evidence trial requires a result or evaluator artifact")
        return self


class EvidenceTaskDetail(EvidenceModel):
    schema_version: int
    task_key: str
    task_id: str | None = None
    state: str
    trials: list[EvidenceTrial] = Field(min_length=1)


class EvidenceManifest(EvidenceModel):
    schema_version: int
    checkpoint: str
    supervision_policy: str
    true_task_ids_exposed: bool
    source_evaluation: str
    source_evaluation_id: str
    source_request_sha256: str
    source_evaluation_sha256: str
    metrics: MetricSummary
    tasks: list[EvidenceTask] = Field(default_factory=list)


def verify_evidence_bundle(
    bundle: EvidenceBundle,
    *,
    evidence_dir: Path,
    expected_checkpoint: str,
    evaluation_dir: Path,
    source_evaluation: EvaluationResult,
    source_request: EvaluationRequest,
) -> EvidenceManifest:
    root = evidence_dir.resolve()
    manifest_path = Path(bundle.manifest_path).resolve()
    try:
        manifest_path.relative_to(root)
    except ValueError as exc:
        raise IntegrityError("evidence manifest escapes its output directory") from exc
    if sha256_file(manifest_path) != bundle.manifest_sha256:
        raise IntegrityError("evidence manifest hash mismatch")
    value = read_json(manifest_path)
    try:
        manifest = EvidenceManifest.model_validate(value)
    except ValidationError as exc:
        raise IntegrityError(f"invalid evidence manifest: {exc}") from exc
    if bundle.schema_version != 2 or manifest.schema_version != 2:
        raise IntegrityError("unsupported evidence schema")
    if manifest.checkpoint != expected_checkpoint:
        raise IntegrityError("evidence checkpoint identity mismatch")
    if len(manifest.tasks) != bundle.task_count:
        raise IntegrityError("evidence task count mismatch")
    if manifest.supervision_policy != bundle.supervision_policy:
        raise IntegrityError("evidence supervision policy mismatch")
    source = (root / manifest.source_evaluation).resolve()
    expected_source = (evaluation_dir / "adapter_result.json").resolve()
    if source != expected_source:
        raise IntegrityError("evidence source evaluation path mismatch")
    if not source.is_file() or sha256_file(source) != manifest.source_evaluation_sha256:
        raise IntegrityError("evidence source evaluation hash mismatch")
    try:
        actual_source = EvaluationResult.model_validate(read_json(source))
    except ValidationError as exc:
        raise IntegrityError(f"invalid evidence source evaluation: {exc}") from exc
    if actual_source != source_evaluation:
        raise IntegrityError("evidence source evaluation content mismatch")
    verified_evaluation = verify_evaluation_output(
        source_request,
        source_evaluation,
    )
    if manifest.source_evaluation_id != source_evaluation.evaluation_id:
        raise IntegrityError("evidence source evaluation identity mismatch")
    if manifest.source_request_sha256 != source_evaluation.request_sha256:
        raise IntegrityError("evidence source request identity mismatch")
    if manifest.metrics != source_evaluation.summary:
        raise IntegrityError("evidence metrics are stale or inconsistent")
    source_states = {
        state.task_key: state for state in source_evaluation.summary.task_states
    }
    if set(source_states) != {task.task_key for task in manifest.tasks}:
        raise IntegrityError("evidence tasks do not cover the source evaluation")
    units = {(unit.task_key, unit.trial): unit for unit in verified_evaluation.units}
    seen: set[str] = set()
    for task in manifest.tasks:
        if task.task_key in seen:
            raise IntegrityError(f"duplicate evidence task key: {task.task_key}")
        seen.add(task.task_key)
        detail = _relative_path(
            root,
            task.detail_path,
            label="evidence task",
        )
        if not detail.is_file() or sha256_file(detail) != task.detail_sha256:
            raise IntegrityError(f"evidence task hash mismatch: {task.task_key}")
        try:
            detail_value = EvidenceTaskDetail.model_validate(read_json(detail))
        except ValidationError as exc:
            raise IntegrityError(
                f"invalid evidence task detail {task.task_key}: {exc}"
            ) from exc
        if detail_value.schema_version != 2:
            raise IntegrityError("unsupported evidence task detail schema")
        state = source_states[task.task_key]
        expected_task_id = state.task_id if manifest.true_task_ids_exposed else None
        if (
            detail_value.task_key != task.task_key
            or detail_value.task_id != task.task_id
            or detail_value.state != task.state
            or task.task_id != expected_task_id
            or task.state != state.state
        ):
            raise IntegrityError(f"evidence task identity mismatch: {task.task_key}")
        if task.trial_count != state.trials or len(detail_value.trials) != state.trials:
            raise IntegrityError(f"evidence trial coverage mismatch: {task.task_key}")
        observed_trials: set[int] = set()
        for trial in detail_value.trials:
            if trial.trial in observed_trials:
                raise IntegrityError(
                    f"duplicate evidence trial: {task.task_key}/{trial.trial}"
                )
            observed_trials.add(trial.trial)
            unit = units.get((task.task_key, trial.trial))
            if unit is None or trial.unit_id != unit.unit_id or trial.seed != unit.seed:
                raise IntegrityError(
                    f"evidence trial lineage mismatch: {task.task_key}/{trial.trial}"
                )
            declared_artifacts: dict[tuple[str, str], str] = {}
            for artifact in trial.artifacts:
                artifact_path = _evaluation_artifact_path(
                    evaluation_dir,
                    artifact.path,
                )
                if (
                    not artifact_path.is_file()
                    or sha256_file(artifact_path) != artifact.sha256
                ):
                    raise IntegrityError(
                        f"evidence artifact hash mismatch: {artifact.path}"
                    )
                key = (artifact.path, artifact.sha256)
                if key in declared_artifacts:
                    raise IntegrityError(
                        f"duplicate evidence artifact: {artifact.path}"
                    )
                declared_artifacts[key] = artifact.kind
            result_key = (unit.result_path, unit.result_sha256)
            trace_key = (unit.trace_path, unit.trace_sha256)
            if declared_artifacts.get(trace_key) != "trace":
                raise IntegrityError(
                    f"evidence trial omits its trace artifact: "
                    f"{task.task_key}/{trial.trial}"
                )
            if declared_artifacts.get(result_key) not in {"result", "evaluator"}:
                raise IntegrityError(
                    f"evidence trial omits its result artifact: "
                    f"{task.task_key}/{trial.trial}"
                )
            required = {result_key, trace_key}
            if unit.metadata_path is not None and unit.metadata_sha256 is not None:
                required.add((unit.metadata_path, unit.metadata_sha256))
            if not required <= set(declared_artifacts):
                raise IntegrityError(
                    f"evidence trial omits required artifacts: "
                    f"{task.task_key}/{trial.trial}"
                )
        if observed_trials != set(range(task.trial_count)):
            raise IntegrityError(
                f"evidence trial indices are incomplete: {task.task_key}"
            )
    return manifest


def load_evidence_manifest(path: Path) -> EvidenceManifest:
    return EvidenceManifest.model_validate(read_json(path))


def load_task_detail(
    evidence_dir: Path,
    task: EvidenceTask,
) -> dict[str, Any]:
    path = _relative_path(
        evidence_dir.resolve(),
        task.detail_path,
        label="evidence task",
    )
    return EvidenceTaskDetail.model_validate(read_json(path)).model_dump(mode="json")


def _evaluation_artifact_path(evaluation_dir: Path, relative: str) -> Path:
    return _relative_path(
        evaluation_dir.resolve(),
        relative,
        label="evidence artifact",
    )


def _relative_path(root: Path, relative: str, *, label: str) -> Path:
    candidate = PurePosixPath(relative)
    if (
        not relative
        or "\\" in relative
        or candidate.is_absolute()
        or any(part in {"", ".", ".."} for part in candidate.parts)
    ):
        raise IntegrityError(f"{label} path escapes its root")
    path = (root / Path(*candidate.parts)).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise IntegrityError(f"{label} path escapes its root") from exc
    return path
