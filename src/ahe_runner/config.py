"""Strict campaign configuration with resolved local paths."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

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


class MetaSettings(StrictModel):
    backend: Literal["nexau", "scripted"] = "nexau"
    explore: ExploreSettings = Field(default_factory=ExploreSettings)
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
    return ResolvedConfig(
        config_path=config_path,
        project_root=project_root,
        document=document,
        artifacts_dir=artifacts_dir,
        harness_dir=harness_dir,
        harness_manifest_path=manifest,
    )


def _resolve(base: Path, value: str) -> Path:
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = base / candidate
    return candidate.resolve()
