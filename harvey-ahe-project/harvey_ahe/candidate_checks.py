"""Static, side-effect-free checks for evolved Harvey harness candidates."""

from __future__ import annotations

import ast
import importlib.machinery
import importlib.util
import os
import re
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import yaml

# Imported by protected evaluation code while AHE's harness import scope is
# active (AHE-003). Listed explicitly so the guard does not depend on install
# layout or meta-path finders.
PROTECTED_MODULE_NAMES = frozenset(
    {"ahe_runner", "benchmark_adapter", "harvey_ahe", "lab_core", "nexau"}
)
# Harness code runs on the host, beside protected code, because AHE V2 has no
# benchmark-environment seam (AHE-005). These bans keep evolved tools on the
# sandbox handle instead of the host network, processes, or grader.
HARNESS_IMPORTABLE_PROTECTED = frozenset({"harvey_ahe.workspace"})
BANNED_IMPORT_ROOTS = frozenset(
    {
        "ahe_runner",
        "aiohttp",
        "benchmark_adapter",
        "builtins",
        "ctypes",
        "ftplib",
        "http",
        "httpx",
        "importlib",
        "lab_core",
        "multiprocessing",
        "pty",
        "requests",
        "smtplib",
        "socket",
        "ssl",
        "subprocess",
        "urllib",
        "urllib3",
        "websockets",
    }
)
BANNED_CALL_NAMES = frozenset({"__import__", "compile", "eval", "exec"})
BANNED_OS_ATTRIBUTES = re.compile(
    r"^(system|popen|fork|forkpty|exec\w*|spawn\w*|posix_spawn\w*)$"
)
BANNED_TEXT = (
    "task.json",
    "match_criteria",
    "LAB_ROOT",
    "harvey-labs",
    "scores_judge",
    "scores_dual",
)
MAX_AGENT_ITERATIONS = 200
REQUIRED_TOOL_NAME = "finish"
_TEXT_SUFFIXES = frozenset({".md", ".txt", ".yaml", ".yml", ".json", ".py"})
_IMPORT_SUFFIXES = tuple(
    sorted(importlib.machinery.all_suffixes(), key=len, reverse=True)
)


def top_level_import_names(harness_dir: Path) -> dict[str, str]:
    """Map each root-level importable name to the harness entry providing it.

    Mirrors the shape that ``HarnessImportScope`` places at ``sys.path[0]``:
    root-level files with a Python import suffix and root-level folders that
    contain such a file anywhere beneath them. Hidden entries cannot be
    imported as ordinary names and are skipped.
    """
    root = harness_dir.resolve()
    names: dict[str, str] = {}
    for path in sorted(root.iterdir()):
        if path.name.startswith("."):
            continue
        if path.is_file() and not path.is_symlink():
            name = _module_name(path.name)
            if name is not None:
                names.setdefault(name, path.name)
        elif path.is_dir() and not path.is_symlink() and _contains_importable(path):
            names.setdefault(path.name, f"{path.name}/")
    return names


def module_shadowing_errors(harness_dir: Path) -> list[str]:
    """Reject top-level harness names that could replace protected imports."""
    root = harness_dir.resolve()
    errors: list[str] = []
    for name, entry in sorted(top_level_import_names(root).items()):
        reason = _collision_reason(name, root)
        if reason is not None:
            errors.append(
                f"harness entry {entry!r} would shadow {reason} module {name!r} "
                "during evaluation; move it under a declared component folder "
                "or rename it (AHE-003)"
            )
    return errors


def harvey_candidate_errors(
    harness_dir: Path,
    *,
    training_task_ids: Iterable[str],
) -> list[str]:
    """All Harvey-specific static checks for one candidate harness."""
    root = harness_dir.resolve()
    errors = module_shadowing_errors(root)
    errors.extend(harness_import_errors(root))
    errors.extend(banned_text_errors(root))
    errors.extend(training_slug_errors(root, training_task_ids))
    errors.extend(agent_config_errors(root))
    return errors


def harness_import_errors(harness_dir: Path) -> list[str]:
    """Reject host-escape imports and calls in harness Python (best effort)."""
    root = harness_dir.resolve()
    errors: list[str] = []
    for path in _harness_files(root, {".py"}):
        relative = path.relative_to(root).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
        except (SyntaxError, UnicodeDecodeError) as exc:
            errors.append(f"{relative}: cannot be parsed: {exc}")
            continue
        for node in ast.walk(tree):
            for module in _imported_modules(node):
                reason = _banned_import_reason(module)
                if reason is not None:
                    errors.append(
                        f"{relative}:{getattr(node, 'lineno', 0)}: import of "
                        f"{module!r} is not "
                        f"allowed in the harness ({reason})"
                    )
            call = _banned_call(node)
            if call is not None:
                errors.append(
                    f"{relative}:{getattr(node, 'lineno', 0)}: call to {call!r} is "
                    "not allowed in the harness; use the sandbox handle"
                )
    return errors


def banned_text_errors(harness_dir: Path) -> list[str]:
    root = harness_dir.resolve()
    errors: list[str] = []
    for path in _harness_files(root, {".py", ".yaml", ".yml"}):
        text = _read_text(path)
        if text is None:
            continue
        for value in BANNED_TEXT:
            if value.casefold() in text.casefold():
                errors.append(
                    f"{path.relative_to(root).as_posix()}: references protected "
                    f"benchmark term {value!r}"
                )
    return errors


def training_slug_errors(harness_dir: Path, task_ids: Iterable[str]) -> list[str]:
    """Reject training task slugs (AHE already rejects complete task IDs)."""
    root = harness_dir.resolve()
    slugs = sorted({str(task_id).rsplit("/", 1)[-1] for task_id in task_ids})
    errors: list[str] = []
    for path in _harness_files(root, set(_TEXT_SUFFIXES)):
        text = _read_text(path)
        if text is None:
            continue
        for slug in slugs:
            if re.search(rf"(?<![A-Za-z0-9]){re.escape(slug)}(?![A-Za-z0-9])", text):
                errors.append(
                    f"{path.relative_to(root).as_posix()}: hardcodes training "
                    f"task slug {slug!r}"
                )
    return errors


def agent_config_errors(harness_dir: Path) -> list[str]:
    root = harness_dir.resolve()
    config = _load_yaml(root / "agent.yaml")
    if config is None:
        return ["agent.yaml must contain a mapping"]
    errors: list[str] = []
    tools = config.get("tools") or []
    names = {str(entry.get("name")) for entry in tools if isinstance(entry, Mapping)}
    if REQUIRED_TOOL_NAME not in names:
        errors.append(f"agent.yaml must register the {REQUIRED_TOOL_NAME!r} tool")
    pending = [(root / "agent.yaml", config)]
    seen: set[Path] = set()
    while pending:
        path, document = pending.pop()
        if path in seen:
            continue
        seen.add(path)
        for entry in document.get("sub_agents") or []:
            if not isinstance(entry, Mapping) or not isinstance(
                entry.get("config_path"), str
            ):
                continue
            child_path = (path.parent / entry["config_path"]).resolve()
            child = _load_yaml(child_path)
            label = _relative_label(child_path, root)
            if child is None:
                errors.append(f"{label}: sub-agent config must contain a mapping")
                continue
            iterations = child.get("max_iterations", 100)
            if (
                isinstance(iterations, bool)
                or not isinstance(iterations, int)
                or iterations > MAX_AGENT_ITERATIONS
            ):
                errors.append(
                    f"{label}: sub-agent max_iterations must be an integer "
                    f"<= {MAX_AGENT_ITERATIONS}"
                )
            pending.append((child_path, child))
    return errors


def _harness_files(root: Path, suffixes: set[str]) -> list[Path]:
    files: list[Path] = []
    for current, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = sorted(name for name in dirs if name != "__pycache__")
        for name in sorted(names):
            path = Path(current) / name
            if path.suffix.lower() in suffixes and not path.is_symlink():
                files.append(path)
    return files


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _load_yaml(path: Path) -> dict[str, Any] | None:
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return None
    return document if isinstance(document, dict) else None


def _relative_label(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def _imported_modules(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
        names = [
            f"{node.module}.{alias.name}" for alias in node.names if alias.name != "*"
        ]
        return names or [node.module]
    return []


def _banned_import_reason(module: str) -> str | None:
    parts = module.split(".")
    root = parts[0]
    if root == "harvey_ahe":
        if ".".join(parts[:2]) in HARNESS_IMPORTABLE_PROTECTED:
            return None
        return "only harvey_ahe.workspace is harness-importable"
    if root in BANNED_IMPORT_ROOTS:
        return "host network, process, grader, or dynamic-import access"
    if root == "os" and len(parts) == 2 and BANNED_OS_ATTRIBUTES.match(parts[1]):
        return "host process execution"
    return None


def _banned_call(node: ast.AST) -> str | None:
    if not isinstance(node, ast.Call):
        return None
    function = node.func
    if isinstance(function, ast.Name) and function.id in BANNED_CALL_NAMES:
        return function.id
    if (
        isinstance(function, ast.Attribute)
        and isinstance(function.value, ast.Name)
        and function.value.id == "os"
        and BANNED_OS_ATTRIBUTES.match(function.attr)
    ):
        return f"os.{function.attr}"
    return None


def _module_name(filename: str) -> str | None:
    for suffix in _IMPORT_SUFFIXES:
        if filename.endswith(suffix) and len(filename) > len(suffix):
            return filename[: -len(suffix)]
    return None


def _contains_importable(directory: Path) -> bool:
    for _current, _dirs, files in os.walk(directory, followlinks=False):
        if any(_module_name(filename) is not None for filename in files):
            return True
    return False


def _collision_reason(name: str, harness_root: Path) -> str | None:
    if name in PROTECTED_MODULE_NAMES:
        return "a protected integration"
    if name in sys.stdlib_module_names or name in sys.builtin_module_names:
        return "a standard-library"
    loaded = sys.modules.get(name)
    if loaded is not None and not _module_inside(loaded, harness_root):
        return "an already-loaded"
    try:
        spec = importlib.util.find_spec(name)
    except ValueError:
        return "an already-loaded"
    except ImportError:
        return None
    if spec is None or _spec_inside(spec, harness_root):
        return None
    return "an installed"


def _module_inside(module: object, root: Path) -> bool:
    spec = getattr(module, "__spec__", None)
    if spec is not None:
        return _spec_inside(spec, root)
    location = getattr(module, "__file__", None)
    return isinstance(location, str) and _inside(Path(location), root)


def _spec_inside(spec: importlib.machinery.ModuleSpec, root: Path) -> bool:
    locations = [
        Path(value)
        for value in ([spec.origin] if spec.origin and spec.has_location else [])
        + list(spec.submodule_search_locations or [])
    ]
    return bool(locations) and all(_inside(path, root) for path in locations)


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root)
    except (OSError, ValueError):
        return False
    return True
