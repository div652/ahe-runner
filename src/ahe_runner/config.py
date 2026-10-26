"""Strict campaign configuration with resolved local paths."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .errors import ConfigurationError


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TokenPrices(StrictModel):
    """Provider prices per million tokens of each usage category."""

    input_per_million: float = Field(ge=0)
    # Prompt tokens read from the provider's cache; default: the input price.
    cached_input_per_million: float | None = Field(default=None, ge=0)
    # Prompt tokens written to the provider's cache; default: the input price.
    cache_write_per_million: float | None = Field(default=None, ge=0)
    # Includes reasoning tokens, which providers bill as output.
    output_per_million: float = Field(ge=0)


class LongContextPrices(TokenPrices):
    # The tier applies to a call whose prompt exceeds this many tokens.
    above_prompt_tokens: int = Field(gt=0)


class ModelPricing(TokenPrices):
    """Prices used to turn recorded token usage into cost (see cost.py)."""

    currency: str = "USD"
    long_context: LongContextPrices | None = None


class ModelSettings(StrictModel):
    model: str
    max_tokens: int = Field(gt=0)
    reasoning_effort: str | None = None
    # None sends no temperature, for models that reject the parameter.
    temperature: float | None = Field(default=0.2, ge=0.0, le=2.0)
    # Wire API for this model when it differs from transport.api_mode, for a
    # gateway that serves model families on different APIs.
    api_mode: Literal["chat_completions", "responses"] | None = None
    # Prices for cost accounting. They never reach the provider.
    pricing: ModelPricing | None = None

    @field_validator("model")
    @classmethod
    def validate_exact_model(cls, value: str) -> str:
        model = value.strip()
        if not model:
            raise ValueError("model must be non-empty")
        if model.startswith(("openai/", "copilot/")):
            raise ValueError("model IDs must be exact and unprefixed")
        return model

    @field_validator("reasoning_effort", mode="before")
    @classmethod
    def normalize_reasoning(cls, value: Any) -> str | None:
        if value in {None, "", "provider-default"}:
            return None
        return str(value)


class TransportSettings(StrictModel):
    base_url: str
    api_key_env: str | None = None
    # Operator command that prints a bearer token (raw, or JSON with an expiry)
    # for identity-based authentication; mutually exclusive with api_key_env.
    token_command: str | None = None
    token_refresh_margin_seconds: float = Field(default=300.0, gt=0)
    api_mode: Literal["chat_completions", "responses"] = "chat_completions"
    stream: Literal[False] = False
    request_timeout_seconds: float = Field(default=300.0, gt=0)
    observable_model_policy: Literal["exact"] = "exact"
    model_catalog: Literal["required", "optional", "disabled"] = "optional"
    negative_control: Literal["required", "best_effort", "disabled"] = "best_effort"
    # Model calls in flight at once across every recorded client in the
    # process for this endpoint; extra calls wait for a slot. None: no queue.
    max_concurrent_requests: int | None = Field(default=None, ge=1)
    # Extra attempts for a model call that timed out before any response.
    # Every failed attempt remains in the call trace. 0 preserves fail-fast.
    hung_call_retries: int = Field(default=0, ge=0, le=5)
    # Extra attempts for HTTP 429, a 5xx status, or a connection error, with
    # exponential back-off and proportional jitter, never shorter than the
    # server's Retry-After. Every failed attempt remains in the call trace.
    transient_retries: int = Field(default=0, ge=0, le=20)
    transient_max_wait_seconds: float = Field(default=120.0, gt=0, le=600)
    # A 403 naming one of these markers means the provider blocked this
    # client: every process on the host stops sending to this endpoint for
    # provider_block_seconds, and the stage ends as infrastructure.
    provider_block_markers: list[str] = Field(
        default_factory=lambda: ["PrincipalBlocked", "retry-flood"]
    )
    provider_block_seconds: float = Field(default=1800.0, gt=0)
    # Header carrying a stable per-session client ID, for providers that
    # isolate retry protection per workload. None: no header.
    client_id_header: str | None = None
    # Admission control: per model, every process on the host shares this
    # token budget per 60 s window (estimated prompt plus output cap) and
    # adapts it to refusals. None: off.
    token_budget_per_minute: int | None = Field(default=None, gt=0)
    token_budget_chars_per_token: float = Field(default=3.0, gt=0, le=10)
    token_budget_cut: float = Field(default=0.7, gt=0, le=1)
    token_budget_floor: float = Field(default=0.3, gt=0, le=1)
    token_budget_recover_seconds: float = Field(default=600.0, gt=0, le=3600)
    # Extra error phrases that identify a refused model in the preflight's
    # negative control, for gateways that refuse without naming the model.
    model_rejection_phrases: list[str] = Field(default_factory=list)

    @field_validator("base_url")
    @classmethod
    def normalize_base_url(cls, value: str) -> str:
        normalized = value.rstrip("/")
        if not normalized.startswith(("http://", "https://")):
            raise ValueError("transport.base_url must be HTTP(S)")
        return normalized

    @field_validator("token_command", "client_id_header")
    @classmethod
    def validate_optional_text(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("value must be non-empty when set")
        return value.strip() if value is not None else None

    @model_validator(mode="after")
    def validate_authentication(self) -> TransportSettings:
        if self.token_command is not None and self.api_key_env is not None:
            raise ValueError(
                "transport.token_command and transport.api_key_env are exclusive"
            )
        return self


class CampaignSettings(StrictModel):
    name: str
    artifacts_dir: str
    rounds: int = Field(default=10, ge=0)
    root_seed: int = 42


class ProjectSettings(StrictModel):
    harness_dir: str
    harness_manifest: str
    adapter: str
    adapter_settings: dict[str, Any] = Field(default_factory=dict)
    adapter_dependency_paths: list[str] = Field(default_factory=list)
    meta_agent_source_roots: list[str] = Field(default_factory=list)


class ModelsSettings(StrictModel):
    evaluated: ModelSettings
    meta: ModelSettings


class EvaluationSettings(StrictModel):
    train_trials: int = Field(default=2, gt=0)
    validation_trials: int = Field(default=2, gt=0)
    test_trials: int = Field(default=3, gt=0)
    max_concurrency: int = Field(default=8, gt=0)
    # Outcome of a provider refusing one agent request mid-trial (HTTP 400/422,
    # not context length): grade the partial work, or rerun the trial.
    provider_rejection_policy: Literal["grade", "rerun"] = "grade"


class DebuggerSettings(StrictModel):
    max_concurrency: int = Field(default=8, gt=0)
    max_iterations: int = Field(default=80, gt=0)


class RecoverySettings(StrictModel):
    maximum_stage_attempts: int = Field(default=3, gt=0)
    archive_incomplete_attempts: bool = True


class ExploreSettings(StrictModel):
    source_enabled: bool = True
    web_enabled: bool = True
    web_search_backend: Literal["ddgs", "disabled"] = "ddgs"
    research_seed_urls: list[str] = Field(default_factory=list)

    @field_validator("research_seed_urls")
    @classmethod
    def validate_research_seed_urls(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("research_seed_urls must not contain duplicates")
        for value in values:
            if not value.startswith(("http://", "https://")):
                raise ValueError("research_seed_urls must use HTTP(S)")
        return values


class RoleSupplementSettings(StrictModel):
    source: str | None = None
    web: str | None = None
    debugger: str | None = None
    evolve: str | None = None


class MetaRoleModelSettings(StrictModel):
    """Per-role overrides of models.meta (the original AHE sets these per agent)."""

    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, gt=0)


class MetaRolesSettings(StrictModel):
    debugger: MetaRoleModelSettings = Field(default_factory=MetaRoleModelSettings)
    evolve: MetaRoleModelSettings = Field(default_factory=MetaRoleModelSettings)
    explorers: MetaRoleModelSettings = Field(default_factory=MetaRoleModelSettings)


class MetaShellSettings(StrictModel):
    """Sandbox for the meta agents' run_shell_command tool."""

    backend: Literal["disabled", "docker"] = "disabled"
    image: str | None = None
    docker_binary: str = "docker"
    timeout_seconds: float = Field(default=300.0, gt=0)
    memory: str = "2g"
    cpus: float = Field(default=2.0, gt=0)
    pids_limit: int = Field(default=256, gt=0)
    tmpfs_size: str = "1g"

    @field_validator("image")
    @classmethod
    def validate_image(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("meta.shell.image must be non-empty when set")
        return value

    @model_validator(mode="after")
    def require_image_for_docker(self) -> MetaShellSettings:
        if self.backend == "docker" and self.image is None:
            raise ValueError("meta.shell.backend=docker requires meta.shell.image")
        return self


class MetaSettings(StrictModel):
    backend: Literal["nexau", "scripted"] = "nexau"
    explore: ExploreSettings = Field(default_factory=ExploreSettings)
    role_supplements: RoleSupplementSettings = Field(
        default_factory=RoleSupplementSettings
    )
    roles: MetaRolesSettings = Field(default_factory=MetaRolesSettings)
    shell: MetaShellSettings = Field(default_factory=MetaShellSettings)
    deprecated_metric_terms: list[str] = Field(default_factory=list)
    max_concurrent_model_calls: int = Field(default=2, gt=0)
    max_context_tokens: int = Field(default=200_000, gt=0)
    token_counter: str | None = None
    context_counting_unit: str = (
        "NexAU fallback estimate (serialized characters divided by four)"
    )
    provider_max_input_tokens: int | None = Field(default=None, gt=0)
    max_running_subagents: int = Field(default=5, gt=0)
    evolve_max_iterations: int = Field(default=500, gt=0)
    explore_max_iterations: int = Field(default=200, gt=0)

    @field_validator("token_counter")
    @classmethod
    def validate_token_counter(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        module, separator, attribute = normalized.partition(":")
        if not separator or not module or not attribute:
            raise ValueError("meta.token_counter must use 'module.path:callable_name'")
        return normalized

    @field_validator("context_counting_unit")
    @classmethod
    def validate_context_counting_unit(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("meta.context_counting_unit must be non-empty")
        return normalized

    @model_validator(mode="after")
    def validate_context_limit(self) -> MetaSettings:
        provider_limit = self.provider_max_input_tokens
        if provider_limit is not None and self.max_context_tokens > provider_limit:
            raise ValueError(
                "meta.max_context_tokens cannot exceed meta.provider_max_input_tokens"
            )
        return self

    @field_validator("deprecated_metric_terms")
    @classmethod
    def validate_deprecated_metric_terms(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for value in values:
            term = value.strip()
            if not term:
                raise ValueError("deprecated metric terms must be non-empty")
            key = term.casefold()
            if key in seen:
                continue
            seen.add(key)
            normalized.append(term)
        return normalized


class AlgorithmConfig(StrictModel):
    """The evolution algorithm and its settings (see algorithms/)."""

    # A built-in name ("ahe", "rrsi") or "module.path:Class" for an algorithm
    # supplied by the operator project.
    name: str = "ahe"
    # Validated by the algorithm's own settings model.
    settings: dict[str, Any] = Field(default_factory=dict)
    # Operator supplements appended to the algorithm's role prompts, by role.
    role_supplements: dict[str, str] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("algorithm.name must be non-empty")
        return normalized


class SelectionSettings(StrictModel):
    # How the shipped checkpoint is chosen after evolution closes:
    # "validation" evaluates the algorithm's nominees (and C0) on the
    # validation split; "algorithm" takes the algorithm's own pick without a
    # validation evaluation. None: the algorithm's default.
    policy: Literal["validation", "algorithm"] | None = None


class ConfigDocument(StrictModel):
    schema_version: Literal[1]
    campaign: CampaignSettings
    project: ProjectSettings
    models: ModelsSettings
    transport: TransportSettings
    evaluation: EvaluationSettings = Field(default_factory=EvaluationSettings)
    debugger: DebuggerSettings = Field(default_factory=DebuggerSettings)
    recovery: RecoverySettings = Field(default_factory=RecoverySettings)
    meta: MetaSettings = Field(default_factory=MetaSettings)
    algorithm: AlgorithmConfig = Field(default_factory=AlgorithmConfig)
    selection: SelectionSettings = Field(default_factory=SelectionSettings)


# Settings that decide how the campaign runs but cannot change a completed
# result (AHE-042): concurrency, pacing, retries, timeouts, preflight
# strictness, credentials, and recovery. They are recorded but kept out of the
# campaign and evaluation identities, so they may change on resume; every
# change is appended to the campaign amendments ledger.
EXECUTION_SETTING_PATHS: tuple[str, ...] = (
    "transport.api_key_env",
    "transport.token_command",
    "transport.token_refresh_margin_seconds",
    "transport.request_timeout_seconds",
    "transport.model_catalog",
    "transport.negative_control",
    "transport.max_concurrent_requests",
    "transport.hung_call_retries",
    "transport.transient_retries",
    "transport.transient_max_wait_seconds",
    "transport.provider_block_markers",
    "transport.provider_block_seconds",
    "transport.client_id_header",
    "transport.token_budget_per_minute",
    "transport.token_budget_chars_per_token",
    "transport.token_budget_cut",
    "transport.token_budget_floor",
    "transport.token_budget_recover_seconds",
    "transport.model_rejection_phrases",
    "evaluation.max_concurrency",
    "debugger.max_concurrency",
    "meta.max_concurrent_model_calls",
    "recovery",
)
# Result-affecting fields added after the 0.8.0 identity schema. Each is left
# out of identities while at this default, so adding a field never changes
# the identity of a campaign that does not use it. A new result-affecting
# field must be listed here; a new execution field in the list above.
LATER_RESULT_DEFAULTS: dict[str, Any] = {
    "models.evaluated.api_mode": None,
    "models.meta.api_mode": None,
    "evaluation.provider_rejection_policy": "grade",
    "models.evaluated.pricing": None,
    "models.meta.pricing": None,
    "algorithm": {"name": "ahe", "settings": {}, "role_supplements": {}},
    "selection": {"policy": None},
}


def identity_document(document: ConfigDocument) -> dict[str, Any]:
    """The configuration as it enters campaign identity."""

    value = document.model_dump(mode="json")
    for path in EXECUTION_SETTING_PATHS:
        _pop_path(value, path)
    for path, default in LATER_RESULT_DEFAULTS.items():
        if _get_path(value, path, _MISSING) == default:
            _pop_path(value, path)
    return value


def execution_settings(document: ConfigDocument) -> dict[str, Any]:
    """The execution-only settings, by dotted path."""

    value = document.model_dump(mode="json")
    return {
        path: _get_path(value, path, None)
        for path in EXECUTION_SETTING_PATHS
        if _get_path(value, path, _MISSING) is not _MISSING
    }


def model_identity(model: ModelSettings) -> dict[str, Any]:
    """Model fields that can change what the provider receives."""

    value = model.model_dump(mode="json")
    if value.get("api_mode") is None:
        value.pop("api_mode", None)
    value.pop("pricing", None)
    return value


def transport_identity(transport: TransportSettings) -> dict[str, Any]:
    """Transport fields that can change what the model receives."""

    value = transport.model_dump(mode="json")
    for path in EXECUTION_SETTING_PATHS:
        prefix, _, field = path.partition(".")
        if prefix == "transport":
            value.pop(field, None)
    return value


_MISSING = object()


def _get_path(value: dict[str, Any], path: str, default: Any) -> Any:
    current: Any = value
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return default
        current = current[part]
    return current


def _pop_path(value: dict[str, Any], path: str) -> None:
    *parents, last = path.split(".")
    current: Any = value
    for part in parents:
        if not isinstance(current, dict) or part not in current:
            return
        current = current[part]
    if isinstance(current, dict):
        current.pop(last, None)


@dataclass(frozen=True, slots=True)
class ResolvedConfig:
    config_path: Path
    project_root: Path
    document: ConfigDocument
    artifacts_dir: Path
    harness_dir: Path
    harness_manifest_path: Path
    adapter_dependency_paths: tuple[Path, ...]
    meta_agent_source_roots: tuple[Path, ...]
    role_supplements: tuple[tuple[str, Path], ...]
    algorithm_role_supplements: tuple[tuple[str, Path], ...] = ()

    @property
    def campaign_dir(self) -> Path:
        return self.artifacts_dir

    @property
    def api_key(self) -> str:
        name = self.document.transport.api_key_env
        if name is None:
            return "local-placeholder"
        value = os.environ.get(name)
        if not value:
            raise ConfigurationError(
                f"required API key environment variable is unset: {name}"
            )
        return value

    def immutable_snapshot(self) -> dict[str, Any]:
        """Result-affecting configuration; execution settings are excluded."""

        document = identity_document(self.document)
        return {
            "schema_version": 2,
            "config_path": str(self.config_path),
            "project_root": str(self.project_root),
            "artifacts_dir": str(self.artifacts_dir),
            "harness_dir": str(self.harness_dir),
            "harness_manifest": str(self.harness_manifest_path),
            "document": document,
        }

    def role_supplement(self, role: str) -> Path | None:
        return dict(self.role_supplements).get(role)


def load_config(path: Path) -> ResolvedConfig:
    config_path = path.resolve()
    raw = os.path.expandvars(config_path.read_text(encoding="utf-8"))
    if "${" in raw:
        raise ConfigurationError("configuration contains unresolved ${...} values")
    value = yaml.safe_load(raw)
    if not isinstance(value, dict):
        raise ConfigurationError("configuration must contain a YAML object")
    try:
        document = ConfigDocument.model_validate(value)
    except Exception as exc:
        raise ConfigurationError(str(exc)) from exc
    project_root = config_path.parent.resolve()
    artifacts_dir = _resolve(project_root, document.campaign.artifacts_dir)
    harness_dir = _resolve(project_root, document.project.harness_dir)
    manifest = _resolve(project_root, document.project.harness_manifest)
    if not harness_dir.is_dir():
        raise ConfigurationError(f"harness directory does not exist: {harness_dir}")
    if not manifest.is_file():
        raise ConfigurationError(f"harness manifest does not exist: {manifest}")
    if artifacts_dir == project_root or artifacts_dir == Path.home().resolve():
        raise ConfigurationError("campaign artifacts directory is too broad")
    adapter_dependency_paths = _resolve_declared_paths(
        project_root,
        document.project.adapter_dependency_paths,
        artifacts_dir=artifacts_dir,
        label="project.adapter_dependency_paths",
    )
    meta_agent_source_roots = _resolve_declared_paths(
        project_root,
        document.project.meta_agent_source_roots,
        artifacts_dir=artifacts_dir,
        label="project.meta_agent_source_roots",
    )
    role_supplements = tuple(
        (
            role,
            _resolve_declared_file(
                project_root,
                value,
                artifacts_dir=artifacts_dir,
                label=f"meta.role_supplements.{role}",
            ),
        )
        for role, value in document.meta.role_supplements.model_dump().items()
        if value is not None
    )
    algorithm_role_supplements = tuple(
        (
            role,
            _resolve_declared_file(
                project_root,
                value,
                artifacts_dir=artifacts_dir,
                label=f"algorithm.role_supplements.{role}",
            ),
        )
        for role, value in sorted(document.algorithm.role_supplements.items())
    )
    return ResolvedConfig(
        config_path=config_path,
        project_root=project_root,
        document=document,
        artifacts_dir=artifacts_dir,
        harness_dir=harness_dir,
        harness_manifest_path=manifest,
        adapter_dependency_paths=adapter_dependency_paths,
        meta_agent_source_roots=meta_agent_source_roots,
        role_supplements=role_supplements,
        algorithm_role_supplements=algorithm_role_supplements,
    )


def _resolve(base: Path, value: str) -> Path:
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = base / candidate
    return candidate.resolve()


def _resolve_declared_paths(
    base: Path,
    values: list[str],
    *,
    artifacts_dir: Path,
    label: str,
) -> tuple[Path, ...]:
    resolved = tuple(_resolve(base, value) for value in values)
    if len(resolved) != len(set(resolved)):
        raise ConfigurationError(f"{label} resolves to duplicate paths")
    for path in resolved:
        if not path.exists():
            raise ConfigurationError(f"{label} path does not exist: {path}")
        _validate_protected_input_path(
            path,
            artifacts_dir=artifacts_dir,
            label=label,
        )
    return resolved


def _resolve_declared_file(
    base: Path,
    value: str,
    *,
    artifacts_dir: Path,
    label: str,
) -> Path:
    path = _resolve(base, value)
    if not path.is_file():
        raise ConfigurationError(f"{label} is not a file: {path}")
    _validate_protected_input_path(
        path,
        artifacts_dir=artifacts_dir,
        label=label,
    )
    return path


def _validate_protected_input_path(
    path: Path,
    *,
    artifacts_dir: Path,
    label: str,
) -> None:
    if path == artifacts_dir or path.is_relative_to(artifacts_dir):
        raise ConfigurationError(f"{label} must stay outside campaign artifacts")
    if path.is_dir() and artifacts_dir.is_relative_to(path):
        raise ConfigurationError(
            f"{label} directory must not contain campaign artifacts: {path}"
        )
