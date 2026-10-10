"""Validation and loading for adapter-produced training evidence."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .adapter import EvidenceBundle, MetricSummary
from .errors import IntegrityError
from .io import read_json, sha256_file


class EvidenceModel(BaseModel):
    model_config = ConfigDict(extra="allow")


class EvidenceTask(EvidenceModel):
    task_key: str
    task_id: str | None = None
    state: str
    detail_path: str
    detail_sha256: str


class EvidenceManifest(EvidenceModel):
    schema_version: int
    checkpoint: str
    supervision_policy: str
    true_task_ids_exposed: bool
    source_evaluation: str
    source_evaluation_sha256: str
    metrics: MetricSummary
    tasks: list[EvidenceTask] = Field(default_factory=list)


def verify_evidence_bundle(
    bundle: EvidenceBundle,
    *,
    evidence_dir: Path,
    expected_checkpoint: str,
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
    manifest = EvidenceManifest.model_validate(value)
    if manifest.schema_version != 1:
        raise IntegrityError("unsupported evidence schema")
    if manifest.checkpoint != expected_checkpoint:
        raise IntegrityError("evidence checkpoint identity mismatch")
    if len(manifest.tasks) != bundle.task_count:
        raise IntegrityError("evidence task count mismatch")
    if manifest.supervision_policy != bundle.supervision_policy:
        raise IntegrityError("evidence supervision policy mismatch")
    seen: set[str] = set()
    for task in manifest.tasks:
        if task.task_key in seen:
            raise IntegrityError(f"duplicate evidence task key: {task.task_key}")
        seen.add(task.task_key)
        detail = (root / task.detail_path).resolve()
        try:
            detail.relative_to(root)
        except ValueError as exc:
            raise IntegrityError("evidence task path escapes output root") from exc
        if not detail.is_file() or sha256_file(detail) != task.detail_sha256:
            raise IntegrityError(f"evidence task hash mismatch: {task.task_key}")
    return manifest


def load_evidence_manifest(path: Path) -> EvidenceManifest:
    return EvidenceManifest.model_validate(read_json(path))


def load_task_detail(
    evidence_dir: Path,
    task: EvidenceTask,
) -> dict[str, Any]:
    path = (evidence_dir / task.detail_path).resolve()
    return read_json(path)
