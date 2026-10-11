"""Typed structured output for the train-only AHE Debugger."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class DebuggerModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DebuggerCitation(DebuggerModel):
    artifact: str
    line_start: int | None = Field(default=None, ge=1)
    line_end: int | None = Field(default=None, ge=1)
    start_byte: int | None = Field(default=None, ge=0)
    end_byte: int | None = Field(default=None, ge=1)

    @field_validator("artifact")
    @classmethod
    def require_artifact(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized or ":" not in normalized:
            raise ValueError("citation artifact must use root:relative/path")
        return normalized

    @model_validator(mode="after")
    def require_one_range(self) -> DebuggerCitation:
        has_lines = self.line_start is not None or self.line_end is not None
        has_bytes = self.start_byte is not None or self.end_byte is not None
        if has_lines == has_bytes:
            raise ValueError("citation requires exactly one line or byte range")
        if has_lines:
            if self.line_start is None or self.line_end is None:
                raise ValueError("line citations require line_start and line_end")
            if self.line_end < self.line_start:
                raise ValueError("line_end must be greater than or equal to line_start")
        else:
            if self.start_byte is None or self.end_byte is None:
                raise ValueError("byte citations require start_byte and end_byte")
            if self.end_byte <= self.start_byte:
                raise ValueError("end_byte must be greater than start_byte")
        return self


class DebuggerEvidence(DebuggerModel):
    citation: DebuggerCitation
    observation: str
    implication: str

    @field_validator("observation", "implication")
    @classmethod
    def require_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("evidence fields must not be empty")
        return normalized


class DebuggerRecommendation(DebuggerModel):
    component_class: Literal[
        "system_prompt",
        "tool_description",
        "tool_implementation",
        "middleware",
        "skill",
        "sub_agent",
        "memory",
    ]
    change: str
    expected_effect: str
    risk: str
    verification: str

    @field_validator("change", "expected_effect", "risk", "verification")
    @classmethod
    def require_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("recommended-change fields must not be empty")
        return normalized


class DebuggerEvidenceCoverage(DebuggerModel):
    artifacts_read: list[str] = Field(min_length=1)
    limitations: list[str] = Field(default_factory=list)

    @field_validator("artifacts_read", "limitations")
    @classmethod
    def normalize_lists(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values]
        if any(not value for value in normalized):
            raise ValueError("coverage entries must not be empty")
        if len(normalized) != len(set(normalized)):
            raise ValueError("coverage entries must be unique")
        return normalized


class DebuggerPairedEvidence(DebuggerModel):
    citation: DebuggerCitation
    observation: str

    @field_validator("observation")
    @classmethod
    def require_observation(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("paired evidence observation must not be empty")
        return normalized


class DebuggerPairedComparison(DebuggerModel):
    status: Literal["performed", "unavailable"]
    passing_evidence: list[DebuggerPairedEvidence] = Field(default_factory=list)
    failing_evidence: list[DebuggerPairedEvidence] = Field(default_factory=list)
    earliest_divergence: str
    limitation: str

    @model_validator(mode="after")
    def validate_status_contract(self) -> DebuggerPairedComparison:
        divergence = self.earliest_divergence.strip()
        limitation = self.limitation.strip()
        if self.status == "performed":
            if not self.passing_evidence or not self.failing_evidence or not divergence:
                raise ValueError(
                    "performed comparison requires passing evidence, failing "
                    "evidence, and an earliest divergence"
                )
        elif not limitation:
            raise ValueError("unavailable comparison requires an explicit limitation")
        self.earliest_divergence = divergence
        self.limitation = limitation
        return self


class DebuggerDiagnosis(DebuggerModel):
    schema_version: Literal[3] = 3
    task_id: str | None
    primary_failure_class: Literal[
        "reasoning",
        "prompt",
        "tool_description",
        "tool_implementation",
        "middleware",
        "skill",
        "sub_agent",
        "memory",
        "stopping",
        "framework_or_adapter",
        "environment",
        "none",
    ]
    earliest_failure: str
    evidence: list[DebuggerEvidence] = Field(min_length=1)
    evidence_coverage: DebuggerEvidenceCoverage
    paired_comparison: DebuggerPairedComparison
    root_cause: str
    contributing_factors: list[str] = Field(default_factory=list)
    recommended_changes: list[DebuggerRecommendation] = Field(default_factory=list)
    recurrent_pattern_key: str | None
    confidence: float = Field(ge=0, le=1)
    falsifier: str

    @field_validator("earliest_failure", "root_cause", "falsifier")
    @classmethod
    def require_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("diagnosis fields must not be empty")
        return normalized

    @field_validator("contributing_factors")
    @classmethod
    def normalize_factors(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values]
        if any(not value for value in normalized):
            raise ValueError("contributing factors must not be empty")
        return normalized

    @field_validator("recurrent_pattern_key")
    @classmethod
    def normalize_pattern(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


def validate_debugger_diagnosis(
    payload: dict,
    *,
    expected_task_id: str | None,
) -> DebuggerDiagnosis:
    diagnosis = DebuggerDiagnosis.model_validate(payload)
    if diagnosis.task_id != expected_task_id:
        raise ValueError(
            f"task_id must be {expected_task_id!r}, got {diagnosis.task_id!r}"
        )
    return diagnosis
