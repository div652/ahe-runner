"""Executable conformance checks for the AHE harness ABI V2."""

from __future__ import annotations

import importlib
import importlib.metadata
import inspect
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml
from jsonschema import SchemaError
from jsonschema.validators import validator_for
from nexau.archs.main_sub.execution.hooks import Middleware

from .harness_imports import HarnessImportScope

_HOOK_FIELDS = (
    "middlewares",
    "before_model_hooks",
    "after_model_hooks",
    "before_tool_hooks",
    "after_tool_hooks",
)
_INJECTED_TOOL_PARAMETERS = frozenset(
    {"agent_state", "global_storage", "sandbox"}
)


def validate_v2_conformance(
    *,
    harness_dir: Path,
    manifest: Any,
    component_matches: Mapping[str, list[str]],
) -> dict[str, Any]:
    """Validate active runtime attachment for every V2 component file."""

    root = harness_dir.resolve()
    errors: list[str] = []
    warnings: list[str] = []
    registrations: dict[str, set[str]] = {
        name: set() for name in component_matches
    }
    tool_contracts: list[dict[str, Any]] = []
    import_contracts: list[dict[str, Any]] = []

    try:
        installed_version = importlib.metadata.version(manifest.framework.name)
    except importlib.metadata.PackageNotFoundError:
        installed_version = None
    if installed_version != manifest.framework.version:
        errors.append(
            "installed NexAU version does not match harness manifest: "
            f"expected {manifest.framework.version!r}, "
            f"received {installed_version!r}"
        )

    attachment_mode = (
        manifest.attachments.long_term_memory
        if manifest.attachments is not None
        else None
    )
    if attachment_mode == "system_prompt_suffix":
        registrations["long_term_memory"].update(
            component_matches["long_term_memory"]
        )
    else:
        errors.append(
            "V2 long-term memory must use attachment mode "
            "'system_prompt_suffix'"
        )

    records = _collect_agent_tree(
        root=root,
        root_config=manifest.agent_config,
        errors=errors,
        registrations=registrations,
    )
    for record in records:
        config_path = record["path"]
        config = record["config"]
        label = record["label"]
        if config.get("tool_call_mode", "openai") != "openai":
            errors.append(
                f"{label}.tool_call_mode must be 'openai' for ABI V2"
            )
        _collect_system_prompt(
            root=root,
            config_path=config_path,
            config=config,
            label=label,
            errors=errors,
            registrations=registrations,
        )
        _collect_tools(
            root=root,
            config_path=config_path,
            config=config,
            label=label,
            errors=errors,
            registrations=registrations,
            tool_contracts=tool_contracts,
            import_contracts=import_contracts,
        )
        _collect_skills(
            root=root,
            config_path=config_path,
            config=config,
            label=label,
            errors=errors,
            registrations=registrations,
        )
        _collect_middleware(
            config=config,
            label=label,
            errors=errors,
            import_contracts=import_contracts,
        )

    with HarnessImportScope(root):
        for contract in import_contracts:
            _resolve_import_contract(
                root=root,
                contract=contract,
                errors=errors,
                registrations=registrations,
            )

    for name, declared_paths in component_matches.items():
        declared = set(declared_paths)
        active = registrations[name]
        for path in sorted(declared - active):
            errors.append(
                f"declared {name} component is not runtime-attached: {path}"
            )
        for path in sorted(active - declared):
            errors.append(
                f"runtime-attached {name} component is not declared: {path}"
            )

    return {
        "schema_version": 1,
        "abi_version": manifest.abi_version,
        "installed_framework_version": installed_version,
        "attachment_mode": attachment_mode,
        "errors": errors,
        "warnings": warnings,
        "registrations": {
            name: sorted(paths) for name, paths in registrations.items()
        },
        "tool_contracts": tool_contracts,
        "agent_configs": [
            record["path"].relative_to(root).as_posix() for record in records
        ],
    }


def _collect_agent_tree(
    *,
    root: Path,
    root_config: str,
    errors: list[str],
    registrations: dict[str, set[str]],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    visiting: set[Path] = set()
    visited: set[Path] = set()

    def visit(path: Path, label: str, expected_name: str | None = None) -> None:
        resolved = path.resolve()
        if resolved in visiting:
            errors.append(f"sub-agent configuration cycle detected at {path}")
            return
        if resolved in visited:
            return
        if not _inside(root, resolved) or not resolved.is_file():
            errors.append(f"{label} config is unavailable inside harness: {path}")
            return
        visiting.add(resolved)
        try:
            value = yaml.safe_load(resolved.read_text(encoding="utf-8"))
        except Exception as exc:
            errors.append(
                f"{label} config parse failed: {type(exc).__name__}: {exc}"
            )
            visiting.remove(resolved)
            return
        if not isinstance(value, dict):
            errors.append(f"{label} config must contain a YAML object")
            visiting.remove(resolved)
            return
        actual_name = value.get("name")
        if expected_name is not None and actual_name != expected_name:
            errors.append(
                f"{label} name must be {expected_name!r}; received {actual_name!r}"
            )
        records.append({"path": resolved, "label": label, "config": value})
        sub_agents = value.get("sub_agents", [])
        if not isinstance(sub_agents, list):
            errors.append(f"{label}.sub_agents must be a list")
            sub_agents = []
        names: set[str] = set()
        for index, entry in enumerate(sub_agents):
            entry_label = f"{label}.sub_agents[{index}]"
            if not isinstance(entry, Mapping):
                errors.append(f"{entry_label} must be an object")
                continue
            name = entry.get("name")
            config_path = entry.get("config_path")
            if not isinstance(name, str) or not name:
                errors.append(f"{entry_label}.name is required")
                continue
            if name in names:
                errors.append(f"{label} registers duplicate sub-agent name {name!r}")
            names.add(name)
            if not isinstance(config_path, str) or not config_path:
                errors.append(f"{entry_label}.config_path is required")
                continue
            if ":" in config_path:
                errors.append(
                    f"{entry_label}.config_path must be a visible harness file"
                )
                continue
            sub_path = _resolve_harness_path(
                root=root,
                base=resolved.parent,
                value=config_path,
                label=f"{entry_label}.config_path",
                errors=errors,
                require_file=True,
            )
            if sub_path is None:
                continue
            registrations["sub_agents"].add(
                sub_path.relative_to(root).as_posix()
            )
            visit(sub_path, f"sub-agent {name!r}", expected_name=name)
        visiting.remove(resolved)
        visited.add(resolved)

    root_path = _resolve_harness_path(
        root=root,
        base=root,
        value=root_config,
        label="agent_config",
        errors=errors,
        require_file=True,
    )
    if root_path is not None:
        visit(root_path, "agent")
    return records


def _collect_system_prompt(
    *,
    root: Path,
    config_path: Path,
    config: Mapping[str, Any],
    label: str,
    errors: list[str],
    registrations: dict[str, set[str]],
) -> None:
    prompt_type = config.get("system_prompt_type", "string")
    prompt = config.get("system_prompt")
    if prompt_type not in {"file", "jinja"} or not isinstance(prompt, str):
        errors.append(
            f"{label}.system_prompt must reference a file for ABI V2"
        )
        return
    path = _resolve_harness_path(
        root=root,
        base=config_path.parent,
        value=prompt,
        label=f"{label}.system_prompt",
        errors=errors,
        require_file=True,
    )
    if path is not None:
        registrations["system_prompt"].add(path.relative_to(root).as_posix())


def _collect_tools(
    *,
    root: Path,
    config_path: Path,
    config: Mapping[str, Any],
    label: str,
    errors: list[str],
    registrations: dict[str, set[str]],
    tool_contracts: list[dict[str, Any]],
    import_contracts: list[dict[str, Any]],
) -> None:
    tools = config.get("tools", [])
    if not isinstance(tools, list):
        errors.append(f"{label}.tools must be a list")
        return
    names: set[str] = set()
    for index, entry in enumerate(tools):
        entry_label = f"{label}.tools[{index}]"
        if not isinstance(entry, Mapping):
            errors.append(f"{entry_label} must be an object")
            continue
        name = entry.get("name")
        yaml_path = entry.get("yaml_path")
        if not isinstance(name, str) or not name:
            errors.append(f"{entry_label}.name is required")
            continue
        if name in names:
            errors.append(f"{label} registers duplicate tool name {name!r}")
        names.add(name)
        if not isinstance(yaml_path, str) or not yaml_path:
            errors.append(f"{entry_label}.yaml_path is required")
            continue
        path = _resolve_harness_path(
            root=root,
            base=config_path.parent,
            value=yaml_path,
            label=f"{entry_label}.yaml_path",
            errors=errors,
            require_file=True,
        )
        if path is None:
            continue
        relative = path.relative_to(root).as_posix()
        registrations["tool_descriptions"].add(relative)
        try:
            tool_yaml = yaml.safe_load(path.read_text(encoding="utf-8"))
        except Exception as exc:
            errors.append(
                f"{entry_label} tool YAML parse failed: "
                f"{type(exc).__name__}: {exc}"
            )
            continue
        if not isinstance(tool_yaml, dict):
            errors.append(f"{entry_label} tool YAML must contain an object")
            continue
        if tool_yaml.get("name") != name:
            errors.append(
                f"{entry_label} name differs from tool YAML name "
                f"{tool_yaml.get('name')!r}"
            )
        description = tool_yaml.get("description")
        if not isinstance(description, str) or len(description.strip()) < 20:
            errors.append(f"{entry_label} description is not substantive")
        schema = tool_yaml.get("input_schema")
        _validate_tool_schema(schema, entry_label, errors)
        lazy = bool(entry.get("lazy", tool_yaml.get("lazy", False)))
        if lazy:
            errors.append(
                f"{entry_label} cannot be lazy because checkpoint-local imports "
                "must resolve when the session opens"
            )
        binding = entry.get("binding") or tool_yaml.get("binding")
        if not isinstance(binding, str) or not binding:
            errors.append(f"{entry_label} requires a visible Python binding")
            continue
        extra_kwargs = entry.get("extra_kwargs", {})
        if not isinstance(extra_kwargs, Mapping):
            errors.append(f"{entry_label}.extra_kwargs must be an object")
            extra_kwargs = {}
        contract = {
            "kind": "tool",
            "label": entry_label,
            "reference": binding,
            "schema": schema,
            "extra_kwargs": sorted(str(key) for key in extra_kwargs),
            "tool_name": name,
        }
        import_contracts.append(contract)
        tool_contracts.append(
            {
                "name": name,
                "description_path": relative,
                "binding": binding,
            }
        )


def _collect_skills(
    *,
    root: Path,
    config_path: Path,
    config: Mapping[str, Any],
    label: str,
    errors: list[str],
    registrations: dict[str, set[str]],
) -> None:
    skills = config.get("skills", [])
    if not isinstance(skills, list):
        errors.append(f"{label}.skills must be a list")
        return
    names: set[str] = set()
    for index, value in enumerate(skills):
        entry_label = f"{label}.skills[{index}]"
        if not isinstance(value, str) or not value:
            errors.append(f"{entry_label} must be a directory path")
            continue
        folder = _resolve_harness_path(
            root=root,
            base=config_path.parent,
            value=value,
            label=entry_label,
            errors=errors,
            require_file=False,
        )
        if folder is None or not folder.is_dir():
            errors.append(f"{entry_label} must resolve to a directory")
            continue
        skill_path = folder / "SKILL.md"
        if not skill_path.is_file():
            errors.append(f"{entry_label} is missing SKILL.md")
            continue
        relative = skill_path.relative_to(root).as_posix()
        registrations["skills"].add(relative)
        metadata, body = _skill_document(skill_path, errors)
        name = metadata.get("name")
        description = metadata.get("description")
        if not isinstance(name, str) or not name.strip():
            errors.append(f"{entry_label} skill name is required")
        elif name in names:
            errors.append(f"{label} registers duplicate skill name {name!r}")
        else:
            names.add(name)
        if not isinstance(description, str) or len(description.strip()) < 20:
            errors.append(f"{entry_label} skill description is not substantive")
        if len(body.strip()) < 200:
            errors.append(f"{entry_label} skill body is not substantive")


def _collect_middleware(
    *,
    config: Mapping[str, Any],
    label: str,
    errors: list[str],
    import_contracts: list[dict[str, Any]],
) -> None:
    for field in _HOOK_FIELDS:
        entries = config.get(field, [])
        if entries is None:
            continue
        if not isinstance(entries, list):
            errors.append(f"{label}.{field} must be a list")
            continue
        for index, entry in enumerate(entries):
            entry_label = f"{label}.{field}[{index}]"
            if isinstance(entry, str):
                reference = entry
                params: Mapping[str, Any] = {}
            elif isinstance(entry, Mapping):
                reference = entry.get("import")
                params = entry.get("params", {})
                if not isinstance(params, Mapping):
                    errors.append(f"{entry_label}.params must be an object")
                    params = {}
            else:
                errors.append(f"{entry_label} must be an import or object")
                continue
            if not isinstance(reference, str) or not reference:
                errors.append(f"{entry_label}.import is required")
                continue
            import_contracts.append(
                {
                    "kind": "middleware",
                    "field": field,
                    "label": entry_label,
                    "reference": reference,
                    "params": dict(params),
                }
            )


def _resolve_import_contract(
    *,
    root: Path,
    contract: Mapping[str, Any],
    errors: list[str],
    registrations: dict[str, set[str]],
) -> None:
    label = str(contract["label"])
    reference = str(contract["reference"])
    module_name, separator, symbol_name = reference.rpartition(":")
    if not separator or not module_name or not symbol_name:
        errors.append(f"{label} has invalid import reference {reference!r}")
        return
    try:
        module = importlib.import_module(module_name)
        symbol = getattr(module, symbol_name)
    except Exception as exc:
        errors.append(
            f"{label} import failed for {reference!r}: "
            f"{type(exc).__name__}: {exc}"
        )
        return
    source_value = inspect.getsourcefile(symbol)
    if source_value is None:
        errors.append(f"{label} import has no visible source file: {reference!r}")
        return
    source = Path(source_value).resolve()
    if not _inside(root, source):
        errors.append(
            f"{label} import must resolve inside the harness: {reference!r}"
        )
        return
    relative = source.relative_to(root).as_posix()

    if contract["kind"] == "tool":
        registrations["tool_implementations"].add(relative)
        _validate_tool_signature(symbol, contract, errors)
        return

    registrations["middleware"].add(relative)
    if contract.get("field") == "middlewares":
        if not inspect.isclass(symbol) or not issubclass(symbol, Middleware):
            errors.append(
                f"{label} must import a Middleware subclass, received {symbol!r}"
            )
            return
    elif not callable(symbol):
        errors.append(f"{label} hook import is not callable")
        return
    params = contract.get("params", {})
    try:
        inspect.signature(symbol).bind(**params)
    except (TypeError, ValueError) as exc:
        errors.append(f"{label} constructor parameters do not match: {exc}")


def _validate_tool_schema(
    schema: Any,
    label: str,
    errors: list[str],
) -> None:
    if not isinstance(schema, dict):
        errors.append(f"{label}.input_schema must be an object")
        return
    try:
        validator_for(schema).check_schema(schema)
    except SchemaError as exc:
        errors.append(f"{label}.input_schema is invalid JSON Schema: {exc.message}")
        return
    if schema.get("type") != "object":
        errors.append(f"{label}.input_schema.type must be 'object'")
    if schema.get("additionalProperties") is not False:
        errors.append(
            f"{label}.input_schema.additionalProperties must be false"
        )
    properties = schema.get("properties", {})
    required = schema.get("required", [])
    if not isinstance(properties, dict):
        errors.append(f"{label}.input_schema.properties must be an object")
        return
    if not isinstance(required, list) or not all(
        isinstance(value, str) for value in required
    ):
        errors.append(f"{label}.input_schema.required must be a string list")
        return
    unknown = sorted(set(required) - set(properties))
    if unknown:
        errors.append(
            f"{label}.input_schema.required contains unknown properties: {unknown}"
        )


def _validate_tool_signature(
    symbol: Any,
    contract: Mapping[str, Any],
    errors: list[str],
) -> None:
    label = str(contract["label"])
    if inspect.isclass(symbol) or not callable(symbol):
        errors.append(f"{label} binding must be a function")
        return
    try:
        signature = inspect.signature(symbol)
    except (TypeError, ValueError) as exc:
        errors.append(f"{label} binding signature is unavailable: {exc}")
        return
    parameters = signature.parameters
    for parameter in parameters.values():
        if parameter.kind in {
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        }:
            errors.append(
                f"{label} binding must use explicit keyword-compatible parameters"
            )
            return
    schema = contract.get("schema")
    properties = (
        set(schema.get("properties", {})) if isinstance(schema, dict) else set()
    )
    extra_kwargs = set(contract.get("extra_kwargs", []))
    exposed = (
        set(parameters)
        - _INJECTED_TOOL_PARAMETERS
        - extra_kwargs
    )
    if exposed != properties:
        errors.append(
            f"{label} schema properties {sorted(properties)} do not match "
            f"callable parameters {sorted(exposed)}"
        )
    callable_required = {
        name
        for name, parameter in parameters.items()
        if name in exposed and parameter.default is inspect.Parameter.empty
    }
    schema_required = (
        set(schema.get("required", [])) if isinstance(schema, dict) else set()
    )
    if callable_required != schema_required:
        errors.append(
            f"{label} required schema properties {sorted(schema_required)} do not "
            f"match required callable parameters {sorted(callable_required)}"
        )


def _skill_document(
    path: Path,
    errors: list[str],
) -> tuple[dict[str, Any], str]:
    content = path.read_text(encoding="utf-8")
    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        errors.append(f"skill lacks YAML frontmatter: {path}")
        return {}, ""
    end = next(
        (
            index
            for index, line in enumerate(lines[1:], start=1)
            if line.strip() == "---"
        ),
        None,
    )
    if end is None:
        errors.append(f"skill lacks closing YAML frontmatter: {path}")
        return {}, ""
    try:
        metadata = yaml.safe_load("\n".join(lines[1:end]))
    except yaml.YAMLError as exc:
        errors.append(f"skill frontmatter is invalid in {path}: {exc}")
        return {}, ""
    if not isinstance(metadata, dict):
        errors.append(f"skill frontmatter must be an object: {path}")
        metadata = {}
    return metadata, "\n".join(lines[end + 1 :])


def _resolve_harness_path(
    *,
    root: Path,
    base: Path,
    value: str,
    label: str,
    errors: list[str],
    require_file: bool,
) -> Path | None:
    candidate = Path(value)
    if candidate.is_absolute():
        errors.append(f"{label} must be relative to its agent config")
        return None
    resolved = (base / candidate).resolve()
    if not _inside(root, resolved):
        errors.append(f"{label} escapes the harness: {value}")
        return None
    if require_file and not resolved.is_file():
        errors.append(f"{label} is not a file: {value}")
        return None
    if not require_file and not resolved.exists():
        errors.append(f"{label} does not exist: {value}")
        return None
    return resolved


def _inside(root: Path, path: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True
