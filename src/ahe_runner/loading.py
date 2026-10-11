"""Safe project-local plugin loading."""

from __future__ import annotations

import importlib
import sys
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
from types import ModuleType
from typing import Any

from .adapter import BenchmarkAdapterBase
from .config import ResolvedConfig
from .errors import ConfigurationError


def load_adapter(config: ResolvedConfig) -> BenchmarkAdapterBase:
    reference = config.document.project.adapter
    module_name, separator, class_name = reference.partition(":")
    if not separator or not module_name or not class_name:
        raise ConfigurationError("project.adapter must use the form module:ClassName")
    with project_import_path(config.project_root):
        module = _import_fresh(module_name)
        value = getattr(module, class_name, None)
    if not isinstance(value, type) or not issubclass(value, BenchmarkAdapterBase):
        raise ConfigurationError(
            f"{reference} is not a BenchmarkAdapterBase subclass"
        )
    return value(
        project_root=config.project_root,
        settings=config.document.project.adapter_settings,
    )


@contextmanager
def project_import_path(project_root: Path) -> Iterator[None]:
    value = str(project_root)
    sys.path.insert(0, value)
    try:
        yield
    finally:
        with suppress(ValueError):
            sys.path.remove(value)


def load_symbol(reference: str, *, project_root: Path) -> Any:
    module_name, separator, symbol_name = reference.partition(":")
    if not separator:
        raise ConfigurationError(f"invalid symbol reference: {reference}")
    with project_import_path(project_root):
        module = _import_fresh(module_name)
        try:
            return getattr(module, symbol_name)
        except AttributeError as exc:
            raise ConfigurationError(
                f"module {module_name!r} has no symbol {symbol_name!r}"
            ) from exc


def _import_fresh(name: str) -> ModuleType:
    importlib.invalidate_caches()
    return importlib.import_module(name)
