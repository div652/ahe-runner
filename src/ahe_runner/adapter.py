"""Typed benchmark-adapter boundaries."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .config import ModelSettings, TransportSettings

if TYPE_CHECKING:
    from .runtime import EvaluatedAgentRuntime


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


class BenchmarkToolSpec(AdapterModel):
    """Protected structured action exposed by the benchmark at runtime."""

    name: str
    description: str
    input_schema: dict[str, Any]

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if not value or len(value) > 64:
            raise ValueError("tool name must contain between 1 and 64 characters")
        if not all(character.isalnum() or character in "_-" for character in value):
            raise ValueError(
                "tool name may contain only letters, numbers, underscores, and hyphens"
            )
        return value

    @field_validator("input_schema")
    @classmethod
    def validate_input_schema(cls, value: dict[str, Any]) -> dict[str, Any]:
        if value.get("type") != "object":
            raise ValueError("benchmark tool input_schema.type must be 'object'")
        properties = value.get("properties")
        if properties is not None and not isinstance(properties, dict):
            raise ValueError("benchmark tool input_schema.properties must be an object")
        return value


class BenchmarkInterface(AdapterModel):
    """Immutable benchmark inputs and protected action surface."""

    description: str
    input_kinds: tuple[
        Literal["prompt", "data", "repository", "environment", "legacy"],
        ...,
    ] = ("prompt",)
    tool_delivery: Literal["none", "prompt", "structured", "legacy"] = "none"
    protected_tools: tuple[BenchmarkToolSpec, ...] = ()
    notes: tuple[str, ...] = ()

    @field_validator("input_kinds")
    @classmethod
    def validate_input_kinds(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        if not value:
            raise ValueError("input_kinds must contain at least one input kind")
        if len(value) != len(set(value)):
            raise ValueError("input_kinds must not contain duplicates")
        return value

    @model_validator(mode="after")
    def validate_tool_delivery(self) -> BenchmarkInterface:
        names = [tool.name for tool in self.protected_tools]
        if len(names) != len(set(names)):
            raise ValueError("protected benchmark tool names must be unique")
        if self.tool_delivery == "structured" and not self.protected_tools:
            raise ValueError(
                "structured tool delivery requires at least one protected tool"
            )
        if self.tool_delivery != "structured" and self.protected_tools:
            raise ValueError(
                "protected_tools are valid only when tool_delivery is 'structured'"
            )
        return self


class SplitDefinition(AdapterModel):
    name: SplitName
    identity: str
    task_ids: list[str]
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("task_ids")
    @classmethod
    def validate_task_ids(cls, values: list[str]) -> list[str]:
        if not values:
            raise ValueError("split task_ids must not be empty")
        if any(not value.strip() for value in values):
            raise ValueError("split task_ids must not contain empty values")
        if len(values) != len(set(values)):
            raise ValueError("split task_ids must be unique")
        return values


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
    request_sha256: str
    checkpoint: str
    split: SplitName
    summary: MetricSummary
    artifact_hashes: dict[str, str]
    metadata: dict[str, Any] = Field(default_factory=dict)


class EvidenceBundle(AdapterModel):
    schema_version: int = 2
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


class BenchmarkAdapterBase(ABC):
    """Shared protected benchmark integration implemented by the operator."""

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


class BenchmarkAdapter(BenchmarkAdapterBase):
    """Legacy V1 adapter that constructs the evaluated agent itself."""

    @abstractmethod
    def evaluate(self, request: EvaluationRequest) -> EvaluationResult:
        """Run or resume one protected benchmark evaluation."""


class BenchmarkAdapterV2(BenchmarkAdapterBase):
    """V2 adapter that evaluates through the runner-owned NexAU runtime."""

    @abstractmethod
    def benchmark_interface(self) -> BenchmarkInterface:
        """Declare immutable benchmark inputs and protected runtime actions."""

    @abstractmethod
    def evaluate(
        self,
        request: EvaluationRequest,
        runtime: EvaluatedAgentRuntime,
    ) -> EvaluationResult:
        """Run or resume one evaluation using the supplied NexAU runtime."""


def benchmark_interface_for(adapter: BenchmarkAdapterBase) -> BenchmarkInterface:
    """Return the immutable interface declaration for any adapter generation."""

    if isinstance(adapter, BenchmarkAdapterV2):
        return adapter.benchmark_interface()
    return BenchmarkInterface(
        description=(
            "Legacy V1 adapter owns evaluated-agent construction and its benchmark "
            "interaction surface."
        ),
        input_kinds=("legacy",),
        tool_delivery="legacy",
        notes=(
            "Migrate to BenchmarkAdapterV2 to expose a runner-owned NexAU runtime.",
        ),
    )


def adapter_api_generation(adapter: BenchmarkAdapterBase) -> Literal["v1", "v2"]:
    if isinstance(adapter, BenchmarkAdapterV2):
        return "v2"
    return "v1"
