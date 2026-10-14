"""LAB task loading, split pinning, and eligibility for the Harvey adapter.

Mirrors ``lab_core.harness.run.load_task`` without importing ``run.py`` (which
parses CLI arguments at import time) and without reading ``LAB_ROOT``.
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lab_core.evaluation.run_eval import validate_task_config

from ahe_runner.adapter import SplitName
from ahe_runner.errors import ConfigurationError, IntegrityError
from ahe_runner.io import sha256_file, tree_sha256

SPLIT_SCHEMA_VERSION = 1
_SEGMENT = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
MAX_DOCUMENT_FILES = 10
MAX_DOCUMENT_BYTES = 5_000_000
MIN_CRITERIA = 20
MAX_CRITERIA = 40


@dataclass(frozen=True, slots=True)
class HarveyTask:
    task_id: str
    task_dir: Path
    documents_dir: Path
    config: dict[str, Any]
    instructions: str
    task_json_sha256: str
    documents_sha256: str


@dataclass(frozen=True, slots=True)
class PinnedTask:
    task_id: str
    task_json_sha256: str
    documents_sha256: str


@dataclass(frozen=True, slots=True)
class SplitFile:
    identity: str
    lab_commit: str
    selection_policy: str
    splits: dict[SplitName, tuple[PinnedTask, ...]]
    sha256: str


def task_path(lab_root: Path, task_id: str) -> Path:
    parts = task_id.split("/")
    if len(parts) < 2 or any(not _SEGMENT.fullmatch(part) for part in parts):
        raise ConfigurationError(f"invalid LAB task id: {task_id!r}")
    tasks_root = (lab_root / "tasks").resolve()
    path = tasks_root.joinpath(*parts).resolve()
    try:
        path.relative_to(tasks_root)
    except ValueError as exc:
        raise ConfigurationError(f"LAB task escapes tasks/: {task_id!r}") from exc
    return path


def load_task(lab_root: Path, task_id: str) -> HarveyTask:
    task_dir = task_path(lab_root, task_id)
    config_path = task_dir / "task.json"
    if not config_path.is_file():
        raise ConfigurationError(f"task.json not found: {config_path}")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    try:
        validate_task_config(config=config, task_path=config_path)
    except ValueError as exc:
        raise ConfigurationError(str(exc)) from exc
    if config.get("docs_dir"):
        raise ConfigurationError(
            f"{task_id}: shared docs_dir tasks are outside this adapter's scope"
        )
    documents_dir = task_dir / "documents"
    if not documents_dir.is_dir():
        raise ConfigurationError(f"documents directory not found: {documents_dir}")
    instructions = config.get("instructions")
    if not instructions:
        instructions_path = task_dir / "instructions.md"
        if not instructions_path.is_file():
            raise ConfigurationError(f"{task_id}: no instructions found")
        instructions = instructions_path.read_text(encoding="utf-8")
    return HarveyTask(
        task_id=task_id,
        task_dir=task_dir,
        documents_dir=documents_dir,
        config=config,
        instructions=str(instructions),
        task_json_sha256=sha256_file(config_path),
        documents_sha256=tree_sha256(documents_dir),
    )


def eligibility_errors(task: HarveyTask) -> list[str]:
    """Selection-policy filter used to build ``splits.json``."""
    errors: list[str] = []
    if not str(task.config.get("instructions") or "").strip():
        errors.append("instructions must be inline in task.json")
    deliverables = task.config.get("deliverables")
    if not isinstance(deliverables, dict) or not deliverables:
        errors.append("task.json must declare top-level deliverables")
    files = [path for path in task.documents_dir.rglob("*") if path.is_file()]
    if not 1 <= len(files) <= MAX_DOCUMENT_FILES:
        errors.append(f"documents/ must hold 1-{MAX_DOCUMENT_FILES} files")
    if sum(path.stat().st_size for path in files) > MAX_DOCUMENT_BYTES:
        errors.append(f"documents/ must total at most {MAX_DOCUMENT_BYTES} bytes")
    criteria = task.config.get("criteria")
    count = len(criteria) if isinstance(criteria, list) else 0
    if not MIN_CRITERIA <= count <= MAX_CRITERIA:
        errors.append(f"task must have {MIN_CRITERIA}-{MAX_CRITERIA} criteria")
    return [f"{task.task_id}: {error}" for error in errors]


def load_split_file(path: Path) -> SplitFile:
    if not path.is_file():
        raise ConfigurationError(f"Harvey split file not found: {path}")
    document = json.loads(path.read_text(encoding="utf-8"))
    if document.get("schema_version") != SPLIT_SCHEMA_VERSION:
        raise ConfigurationError("unsupported Harvey split file schema_version")
    for key in ("identity", "lab_commit", "selection_policy"):
        if not isinstance(document.get(key), str) or not document[key].strip():
            raise ConfigurationError(f"Harvey split file requires {key!r}")
    raw_splits = document.get("splits")
    if not isinstance(raw_splits, dict) or set(raw_splits) != {
        name.value for name in SplitName
    }:
        raise ConfigurationError("Harvey split file needs train/validation/test")
    splits: dict[SplitName, tuple[PinnedTask, ...]] = {}
    seen: set[str] = set()
    for name in SplitName:
        rows = raw_splits[name.value]
        if not isinstance(rows, list) or not rows:
            raise ConfigurationError(f"Harvey split {name.value!r} must be non-empty")
        pinned: list[PinnedTask] = []
        for row in rows:
            try:
                task = PinnedTask(
                    task_id=str(row["task_id"]),
                    task_json_sha256=str(row["task_json_sha256"]),
                    documents_sha256=str(row["documents_sha256"]),
                )
            except (KeyError, TypeError) as exc:
                raise ConfigurationError(
                    f"malformed Harvey split row: {row!r}"
                ) from exc
            if task.task_id in seen:
                raise ConfigurationError(f"task appears in two splits: {task.task_id}")
            seen.add(task.task_id)
            pinned.append(task)
        splits[name] = tuple(pinned)
    return SplitFile(
        identity=str(document["identity"]),
        lab_commit=str(document["lab_commit"]),
        selection_policy=str(document["selection_policy"]),
        splits=splits,
        sha256=sha256_file(path),
    )


def load_pinned_task(lab_root: Path, pinned: PinnedTask) -> HarveyTask:
    task = load_task(lab_root, pinned.task_id)
    if (
        task.task_json_sha256 != pinned.task_json_sha256
        or task.documents_sha256 != pinned.documents_sha256
    ):
        raise ConfigurationError(
            f"{pinned.task_id}: LAB task content differs from splits.json pins"
        )
    return task


def copy_documents(task: HarveyTask, destination: Path) -> None:
    """Copy task documents (preserving mtimes, which ``glob`` sorts by)."""
    shutil.copytree(task.documents_dir, destination, symlinks=False)
    if tree_sha256(destination) != task.documents_sha256:
        raise IntegrityError(f"{task.task_id}: document copy hash mismatch")


def leaf_slug(task_id: str) -> str:
    return task_id.rsplit("/", 1)[-1]
