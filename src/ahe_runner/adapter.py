"""Typed benchmark-adapter boundary."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .config import ModelSettings, TransportSettings


class AdapterModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SplitName(StrEnum):
    TRAIN = "train"
    VALIDATION = "validation"
    TEST = "test"


class AdapterIdentity(AdapterModel):
    name: str
    version: str
    benchmark_name: str
    benchmark_version: str
    protocol_description: str
    native_metric_description: str
    supervision_policy: str


class SplitDefinition(AdapterModel):
    name: SplitName
    identity: str
    task_ids: list[str]
    metadata: dict[str, Any] = Field(default_factory=dict)


class MetricValue(AdapterModel):
    name: str
    value: float
    higher_is_better: bool = True


class TaskState(AdapterModel):
    task_key: str
    state: str
    score: float
    task_id: str | None = None
    successes: int | None = None
    trials: int | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)


class MetricSummary(AdapterModel):
    primary: MetricValue
    metrics: dict[str, Any]
    task_count: int = Field(ge=0)
    rollout_count: int = Field(ge=0)
    task_states: list[TaskState] = Field(default_factory=list)
    resource_totals: dict[str, int | float] = Field(default_factory=dict)


class EvaluationResult(AdapterModel):
    schema_version: int = 1
    evaluation_id: str
    checkpoint: str
    split: SplitName
    summary: MetricSummary
    artifact_hashes: dict[str, str]
    metadata: dict[str, Any] = Field(default_factory=dict)


class EvidenceBundle(AdapterModel):
    schema_version: int = 1
    checkpoint: str
    manifest_path: str
    manifest_sha256: str
    task_count: int = Field(ge=0)
    supervision_policy: str


class ValidationResult(AdapterModel):
    valid: bool
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class TransitionSummary(AdapterModel):
    primary_delta: float
    improved_task_keys: list[str] = Field(default_factory=list)
    regressed_task_keys: list[str] = Field(default_factory=list)
    unchanged_task_keys: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class MetaPromptContext(AdapterModel):
    evaluated_agent_role: str
    benchmark_objective: str
    evidence_description: str
    failure_taxonomy: list[str]
    protected_protocol: list[str]
    web_research_scope: str
    source_exploration_scope: str
    task_id_leakage_terms: list[str] = Field(default_factory=list)


@dataclass(frozen=True, slots=True)
class EvaluationRequest:
    checkpoint: str
    harness_dir: Path
    split: SplitDefinition
    trials: int
    root_seed: int
    output_dir: Path
    model: ModelSettings
    transport: TransportSettings
    api_key: str
    max_concurrency: int


@dataclass(frozen=True, slots=True)
class VerificationRequest:
    evaluation: EvaluationRequest


@dataclass(frozen=True, slots=True)
class EvidenceRequest:
    checkpoint: str
    evaluation_dir: Path
    output_dir: Path


@dataclass(frozen=True, slots=True)
class CandidateValidationRequest:
    checkpoint: str
    parent_checkpoint: str
    harness_dir: Path
    parent_harness_dir: Path
    training_task_ids: tuple[str, ...]


class BenchmarkAdapter(ABC):
    """Protected benchmark integration implemented by the operator."""

    def __init__(
        self,
        *,
        project_root: Path,
        settings: dict[str, Any],
    ) -> None:
        self.project_root = project_root
        self.settings = settings

    @abstractmethod
    def identity(self) -> AdapterIdentity:
        """Return pinned benchmark and protocol identity."""

    @abstractmethod
    def split(self, name: SplitName) -> SplitDefinition:
        """Return one immutable split definition."""

    @abstractmethod
    def evaluate(self, request: EvaluationRequest) -> EvaluationResult:
        """Run or resume one protected benchmark evaluation."""

    @abstractmethod
    def verify_evaluation(
        self,
        request: VerificationRequest,
    ) -> EvaluationResult:
        """Verify a completed result set and return its native summary."""

    @abstractmethod
    def materialize_training_evidence(
        self,
        request: EvidenceRequest,
    ) -> EvidenceBundle:
        """Create the standard train-only evidence bundle."""

    @abstractmethod
    def validate_candidate(
        self,
        request: CandidateValidationRequest,
    ) -> ValidationResult:
        """Perform benchmark-specific checks without held-out tasks."""

    @abstractmethod
    def compare(
        self,
        before: MetricSummary,
        after: MetricSummary,
    ) -> TransitionSummary:
        """Compare two training summaries using native semantics."""

    @abstractmethod
    def selection_key(self, summary: MetricSummary) -> tuple[Any, ...]:
        """Return a deterministic key for held-out checkpoint selection."""

    @abstractmethod
    def meta_prompt_context(self) -> MetaPromptContext:
        """Describe benchmark-specific translation for the meta-agents."""
