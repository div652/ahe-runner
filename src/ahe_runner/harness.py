"""Generic validation for file-backed NexAU harnesses."""

from __future__ import annotations

import fnmatch
import json
import subprocess
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from .errors import CandidateValidationError, ConfigurationError
from .io import sha256_json, tree_hashes


class HarnessModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FrameworkDeclaration(HarnessModel):
    name: str
    version: str


class ComponentGlobs(HarnessModel):
    system_prompt: list[str]
    tool_descriptions: list[str] = Field(default_factory=list)
    tool_implementations: list[str] = Field(default_factory=list)
    middleware: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    sub_agents: list[str] = Field(default_factory=list)
    long_term_memory: list[str]


class HarnessLimits(HarnessModel):
    maximum_file_bytes: int = Field(default=1_000_000, gt=0)
    maximum_tree_bytes: int = Field(default=8_000_000, gt=0)


class HarnessManifest(HarnessModel):
    schema_version: int
    framework: FrameworkDeclaration
    agent_config: str
    components: ComponentGlobs
    runtime_owned: list[str] = Field(default_factory=list)
    required_files: list[str]
    protected_agent_fields: dict[str, Any] = Field(default_factory=dict)
    limits: HarnessLimits = Field(default_factory=HarnessLimits)


def load_harness_manifest(path: Path) -> HarnessManifest:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ConfigurationError("harness manifest must contain a YAML object")
    try:
        manifest = HarnessManifest.model_validate(value)
    except Exception as exc:
        raise ConfigurationError(str(exc)) from exc
    if manifest.schema_version != 1:
        raise ConfigurationError("unsupported harness manifest schema")
    if manifest.framework.name != "nexau":
        raise ConfigurationError("version 1 supports only NexAU harnesses")
    return manifest


def validate_harness(
    harness_dir: Path,
    manifest: HarnessManifest,
    *,
    project_root: Path,
    training_task_ids: Iterable[str] = (),
    parent_runtime_owned_hashes: Mapping[str, str] | None = None,
    run_nexau_smoke: bool = True,
) -> dict[str, Any]:
    root = harness_dir.resolve()
    errors: list[str] = []
    warnings = [
        (
            "Host-imported candidate middleware is statically inspected but is "
            "not isolated against hostile Python by an operating-system sandbox."
        )
    ]
    if not root.is_dir():
        raise FileNotFoundError(root)

    files: list[Path] = []
    total_bytes = 0
    for path in sorted(root.rglob("*")):
        if ".git" in path.relative_to(root).parts:
            continue
        if path.is_symlink():
            errors.append(f"symlink is prohibited: {path.relative_to(root)}")
            continue
        if path.is_file():
            files.append(path)
            size = path.stat().st_size
            total_bytes += size
            if size > manifest.limits.maximum_file_bytes:
                errors.append(
                    f"file exceeds maximum size: {path.relative_to(root)}"
                )
    if total_bytes > manifest.limits.maximum_tree_bytes:
        errors.append("harness exceeds maximum tree size")

    relative_files = {path.relative_to(root).as_posix() for path in files}
    for required in manifest.required_files:
        if required not in relative_files:
            errors.append(f"missing required file: {required}")

    component_matches = _component_matches(relative_files, manifest.components)
    for name in ("system_prompt", "long_term_memory"):
        if not component_matches[name]:
            errors.append(f"component class {name} matches no files")

    agent_path = _safe_relative(root, manifest.agent_config, errors, "agent_config")
    agent_config: dict[str, Any] = {}
    if agent_path is not None and agent_path.is_file():
        try:
            value = yaml.safe_load(agent_path.read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                raise TypeError("agent config must contain a YAML object")
            agent_config = value
        except Exception as exc:
            errors.append(f"agent config parse failed: {type(exc).__name__}: {exc}")
    if agent_config:
        errors.extend(_validate_agent_references(root, agent_config))
        errors.extend(
            _validate_protected_fields(
                agent_config,
                manifest.protected_agent_fields,
            )
        )

    for path in files:
        if path.suffix == ".py":
            try:
                compile(
                    path.read_text(encoding="utf-8"),
                    str(path),
                    "exec",
                    dont_inherit=True,
                )
            except (SyntaxError, UnicodeDecodeError) as exc:
                errors.append(
                    f"Python compilation failed for {path.relative_to(root)}: {exc}"
                )

    errors.extend(_reject_task_memorization(files, training_task_ids))

    file_hashes = tree_hashes(root)
    runtime_owned = _match_patterns(
        set(file_hashes),
        manifest.runtime_owned,
    )
    if parent_runtime_owned_hashes is not None:
        actual = {path: file_hashes[path] for path in sorted(runtime_owned)}
        if actual != dict(parent_runtime_owned_hashes):
            errors.append("runtime-owned harness files changed from the parent")

    smoke: dict[str, Any] | None = None
    if run_nexau_smoke and not errors:
        smoke = _smoke_load_nexau_config(
            agent_path=agent_path,
            project_root=project_root,
        )
        if smoke.get("ok") is not True:
            errors.append(f"NexAU config load failed: {smoke.get('error')}")

    report = {
        "schema_version": 1,
        "valid": not errors,
        "harness_dir": str(root),
        "framework": manifest.framework.model_dump(mode="json"),
        "errors": errors,
        "warnings": warnings,
        "file_count": len(file_hashes),
        "total_bytes": total_bytes,
        "harness_files": file_hashes,
        "harness_sha256": sha256_json(file_hashes),
        "components": component_matches,
        "runtime_owned_hashes": {
            path: file_hashes[path] for path in sorted(runtime_owned)
        },
        "nexau_smoke": smoke,
    }
    if errors:
        raise CandidateValidationError(report)
    return report


def _component_matches(
    relative_files: set[str],
    components: ComponentGlobs,
) -> dict[str, list[str]]:
    return {
        name: sorted(_match_patterns(relative_files, patterns))
        for name, patterns in components.model_dump(mode="python").items()
    }


def _match_patterns(files: set[str], patterns: Iterable[str]) -> set[str]:
    matched: set[str] = set()
    for pattern in patterns:
        matched.update(path for path in files if _glob_match(path, pattern))
    return matched


def _glob_match(path: str, pattern: str) -> bool:
    if fnmatch.fnmatch(path, pattern):
        return True
    if "**/" in pattern:
        return fnmatch.fnmatch(path, pattern.replace("**/", ""))
    return False


def _safe_relative(
    root: Path,
    value: str,
    errors: list[str],
    label: str,
) -> Path | None:
    candidate = Path(value)
    if candidate.is_absolute():
        errors.append(f"{label} must be relative to the harness")
        return None
    resolved = (root / candidate).resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        errors.append(f"{label} escapes the harness: {value}")
        return None
    if resolved.is_symlink():
        errors.append(f"{label} is a symlink: {value}")
        return None
    if not resolved.exists():
        errors.append(f"{label} does not exist: {value}")
    return resolved


def _validate_agent_references(
    root: Path,
    config: Mapping[str, Any],
    *,
    label: str = "agent",
) -> list[str]:
    errors: list[str] = []
    system_prompt = config.get("system_prompt")
    if isinstance(system_prompt, str) and config.get("system_prompt_type") in {
        "file",
        "jinja",
    }:
        _safe_relative(root, system_prompt, errors, f"{label}.system_prompt")

    tools = config.get("tools", [])
    if not isinstance(tools, list):
        errors.append(f"{label}.tools must be a list")
    else:
        for index, tool in enumerate(tools):
            if not isinstance(tool, Mapping):
                errors.append(f"{label}.tools[{index}] must be an object")
                continue
            yaml_path = tool.get("yaml_path")
            if not isinstance(yaml_path, str):
                errors.append(f"{label}.tools[{index}].yaml_path is required")
            else:
                _safe_relative(
                    root,
                    yaml_path,
                    errors,
                    f"{label}.tools[{index}].yaml_path",
                )
            extra = tool.get("extra_kwargs")
            if isinstance(extra, Mapping):
                implementation = extra.get("implementation_path")
                if isinstance(implementation, str):
                    _safe_relative(
                        root,
                        implementation,
                        errors,
                        f"{label}.tools[{index}].implementation_path",
                    )

    skills = config.get("skills", [])
    if not isinstance(skills, list):
        errors.append(f"{label}.skills must be a list")
    else:
        for index, skill in enumerate(skills):
            value = skill if isinstance(skill, str) else None
            if value is None:
                errors.append(f"{label}.skills[{index}] must be a path string")
            else:
                _safe_relative(root, value, errors, f"{label}.skills[{index}]")

    sub_agents = config.get("sub_agents", [])
    if not isinstance(sub_agents, list):
        errors.append(f"{label}.sub_agents must be a list")
    else:
        for index, sub_agent in enumerate(sub_agents):
            value = (
                sub_agent.get("config_path")
                if isinstance(sub_agent, Mapping)
                else None
            )
            if not isinstance(value, str):
                errors.append(
                    f"{label}.sub_agents[{index}].config_path is required"
                )
            else:
                _safe_relative(
                    root,
                    value,
                    errors,
                    f"{label}.sub_agents[{index}].config_path",
                )
    return errors


def _validate_protected_fields(
    config: Mapping[str, Any],
    protected: Mapping[str, Any],
) -> list[str]:
    errors: list[str] = []
    for path, expected in protected.items():
        value: Any = config
        for part in path.split("."):
            if not isinstance(value, Mapping) or part not in value:
                value = None
                break
            value = value[part]
        if value != expected:
            errors.append(
                f"protected agent field {path} must remain {expected!r}; "
                f"received {value!r}"
            )
    return errors


def _reject_task_memorization(
    files: Iterable[Path],
    training_task_ids: Iterable[str],
) -> list[str]:
    identifiers = tuple(str(value) for value in training_task_ids if str(value))
    if not identifiers:
        return []
    errors: list[str] = []
    for path in files:
        if path.suffix.lower() not in {
            ".md",
            ".txt",
            ".yaml",
            ".yml",
            ".json",
            ".py",
        }:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        match = next((identifier for identifier in identifiers if identifier in text), None)
        if match is not None:
            errors.append(
                f"candidate hardcodes a complete training task ID in {path}: {match}"
            )
    return errors


def _smoke_load_nexau_config(
    *,
    agent_path: Path | None,
    project_root: Path,
) -> dict[str, Any]:
    if agent_path is None:
        return {"ok": False, "error": "agent path is unavailable"}
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "ahe_runner.harness_smoke",
            str(agent_path),
        ],
        cwd=project_root,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    if completed.returncode != 0:
        return {
            "ok": False,
            "error": completed.stderr.strip() or completed.stdout.strip(),
        }
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        return {"ok": False, "error": f"invalid smoke output: {exc}"}
    return value if isinstance(value, dict) else {"ok": False, "error": "bad output"}
