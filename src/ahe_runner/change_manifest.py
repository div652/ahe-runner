"""Typed hypotheses for one combined-candidate AHE evaluation."""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .errors import IntegrityError

ComponentClass = Literal[
    "system_prompt",
    "tool_description",
    "tool_implementation",
    "middleware",
    "skill",
    "sub_agent",
    "memory",
]
ChangeType = Literal["new", "improvement", "rollback"]


class ChangeRecord(BaseModel):
    """One logical edit and its falsifiable task-level expectations."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^chg-[A-Za-z0-9][A-Za-z0-9._-]*$")
    type: ChangeType
    component: ComponentClass
    paths: list[str] = Field(min_length=1)
    evidence: list[str] = Field(min_length=1)
    failure_pattern: str
    mechanism: str
    rationale: str
    predicted_fixes: list[str] = Field(default_factory=list)
    risk_tasks: list[str] = Field(default_factory=list)

    @field_validator("failure_pattern", "mechanism", "rationale")
    @classmethod
    def require_explanation(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be empty")
        return normalized

    @field_validator("paths")
    @classmethod
    def validate_paths(cls, values: list[str]) -> list[str]:
        normalized = [_component_path(value) for value in values]
        _require_unique(normalized, label="change paths")
        return normalized

    @field_validator("evidence", "predicted_fixes", "risk_tasks")
    @classmethod
    def validate_string_lists(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values]
        if any(not value for value in normalized):
            raise ValueError("list entries must not be empty")
        _require_unique(normalized, label="list entries")
        return normalized

    @model_validator(mode="after")
    def predictions_and_risks_do_not_conflict(self) -> ChangeRecord:
        overlap = set(self.predicted_fixes) & set(self.risk_tasks)
        if overlap:
            raise ValueError(
                "predicted_fixes and risk_tasks overlap: " + ", ".join(sorted(overlap))
            )
        return self


class ChangeManifest(BaseModel):
    """Complete logical coverage for all component files changed in one round."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[2] = 2
    checkpoint: str = Field(pattern=r"^C[1-9][0-9]*$")
    parent_checkpoint: str = Field(pattern=r"^C[0-9]+$")
    objective: str
    changes: list[ChangeRecord] = Field(min_length=1)
    validation: list[str] = Field(min_length=1)

    @field_validator("objective")
    @classmethod
    def require_objective(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("objective must not be empty")
        return normalized

    @field_validator("validation")
    @classmethod
    def validate_validation_steps(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values]
        if any(not value for value in normalized):
            raise ValueError("validation entries must not be empty")
        _require_unique(normalized, label="validation entries")
        return normalized

    @model_validator(mode="after")
    def validate_change_ids(self) -> ChangeManifest:
        _require_unique(
            [change.id for change in self.changes],
            label="change IDs",
        )
        return self

    @property
    def declared_paths(self) -> set[str]:
        return {path for change in self.changes for path in change.paths}


def load_change_manifest(
    path: Path,
    *,
    checkpoint: str | None = None,
    parent_checkpoint: str | None = None,
    changed_paths: list[str] | None = None,
) -> ChangeManifest:
    """Load and validate one manifest against its round and complete Git diff."""

    if not path.is_file():
        raise IntegrityError("Evolve did not create change_manifest.json")
    try:
        manifest = ChangeManifest.model_validate_json(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise IntegrityError(f"invalid change_manifest.json: {exc}") from exc
    if checkpoint is not None and manifest.checkpoint != checkpoint:
        raise IntegrityError(f"change manifest checkpoint must be {checkpoint!r}")
    if (
        parent_checkpoint is not None
        and manifest.parent_checkpoint != parent_checkpoint
    ):
        raise IntegrityError(
            f"change manifest parent_checkpoint must be {parent_checkpoint!r}"
        )
    if changed_paths is not None:
        actual = set(changed_paths) - {"change_manifest.json"}
        declared = manifest.declared_paths
        missing = sorted(actual - declared)
        extra = sorted(declared - actual)
        if missing or extra:
            details: list[str] = []
            if missing:
                details.append("undeclared changed paths: " + ", ".join(missing))
            if extra:
                details.append("declared but unchanged paths: " + ", ".join(extra))
            raise IntegrityError(
                "change manifest must cover the complete component diff; "
                + "; ".join(details)
            )
    return manifest


def _component_path(value: str) -> str:
    normalized = value.strip()
    if not normalized or "\\" in normalized:
        raise ValueError("component path must be a non-empty POSIX relative path")
    path = PurePosixPath(normalized)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("component path must stay inside the harness")
    result = path.as_posix()
    if result == "change_manifest.json" or ".git" in path.parts:
        raise ValueError("component path names a protected bookkeeping path")
    return result


def _require_unique(values: list[str], *, label: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{label} must be unique")
