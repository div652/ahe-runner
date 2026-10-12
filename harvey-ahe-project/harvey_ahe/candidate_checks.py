"""Static, side-effect-free checks for evolved Harvey harness candidates."""

from __future__ import annotations

import importlib.machinery
import importlib.util
import os
import sys
from pathlib import Path

# Imported by protected evaluation code while AHE's harness import scope is
# active (AHE-003). Listed explicitly so the guard does not depend on install
# layout or meta-path finders.
PROTECTED_MODULE_NAMES = frozenset(
    {"ahe_runner", "benchmark_adapter", "harvey_ahe", "lab_core", "nexau"}
)
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
        for value in (
            [spec.origin] if spec.origin and spec.has_location else []
        )
        + list(spec.submodule_search_locations or [])
    ]
    return bool(locations) and all(_inside(path, root) for path in locations)


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root)
    except (OSError, ValueError):
        return False
    return True
