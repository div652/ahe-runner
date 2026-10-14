"""Strict campaign configuration with resolved local paths."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .errors import ConfigurationError
from .io import sha256_file


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ModelSettings(StrictModel):
    model: str
    max_tokens: int = Field(gt=0)
    reasoning_effort: str | None = None
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)

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
    api_mode: Literal["chat_completions"] = "chat_completions"
    stream: Literal[False] = False
    request_timeout_seconds: float = Field(default=300.0, gt=0)
    observable_model_policy: Literal["exact"] = "exact"
    model_catalog: Literal["required", "optional", "disabled"] = "optional"
    negative_control: Literal["required", "best_effort", "disabled"] = "best_effort"
    # Model calls in flight at once across every recorded client in the
    # process for this endpoint; extra calls wait for a slot. None: no queue.
    max_concurrent_requests: int | None = Field(default=None, ge=1)
    # Extra attempts for a model call that hung (a request timeout fired before
    # any response). The identical request is resent and every failed attempt
    # is kept in the call directory. 0: a hung call fails as before.
    hung_call_retries: int = Field(default=0, ge=0, le=5)

    @field_validator("base_url")
    @classmethod
    def normalize_base_url(cls, value: str) -> str:
        normalized = value.rstrip("/")
        if not normalized.startswith(("http://", "https://")):
            raise ValueError("transport.base_url must be HTTP(S)")
        return normalized


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
    max_concurrent_model_calls: int = Field(default=2, gt=0)
    max_context_tokens: int = Field(default=200_000, gt=0)
    max_running_subagents: int = Field(default=5, gt=0)
    evolve_max_iterations: int = Field(default=500, gt=0)
    explore_max_iterations: int = Field(default=200, gt=0)


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
        document = self.document.model_dump(mode="json")
        return {
            "schema_version": 1,
            "config_path": str(self.config_path),
            "config_sha256": sha256_file(self.config_path),
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
