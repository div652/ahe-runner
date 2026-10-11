"""Temporary import isolation for file-backed harness Python modules."""

from __future__ import annotations

import importlib
import sys
from contextlib import suppress
from pathlib import Path
from types import ModuleType


class HarnessImportScope:
    """Make one harness root importable without leaking its modules afterward."""

    def __init__(self, harness_dir: Path) -> None:
        self._root = harness_dir.resolve()
        self._path = str(self._root)
        self._module_roots = _module_roots(self._root)
        self._saved_modules: dict[str, ModuleType] = {}
        self._active = False

    def __enter__(self) -> HarnessImportScope:
        if self._active:
            raise RuntimeError("harness import scope is already active")
        self._active = True
        self._saved_modules = _remove_modules(self._module_roots)
        sys.path.insert(0, self._path)
        importlib.invalidate_caches()
        return self

    def __exit__(self, *_exc_info: object) -> None:
        if not self._active:
            return
        self._active = False
        _remove_modules(self._module_roots)
        with suppress(ValueError):
            sys.path.remove(self._path)
        sys.modules.update(self._saved_modules)
        self._saved_modules = {}
        importlib.invalidate_caches()


def _module_roots(root: Path) -> frozenset[str]:
    names: set[str] = set()
    for path in root.iterdir():
        if path.name.startswith("."):
            continue
        if path.is_file() and path.suffix == ".py":
            names.add(path.stem)
        elif path.is_dir() and any(path.rglob("*.py")):
            names.add(path.name)
    return frozenset(names)


def _remove_modules(roots: frozenset[str]) -> dict[str, ModuleType]:
    removed: dict[str, ModuleType] = {}
    for name in tuple(sys.modules):
        if name.partition(".")[0] not in roots:
            continue
        module = sys.modules.pop(name, None)
        if module is not None:
            removed[name] = module
    return removed
