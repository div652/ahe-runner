"""Pluggable evolution algorithms (see base.py)."""

from __future__ import annotations

import importlib
import inspect
from pathlib import Path

from ..config import ResolvedConfig
from ..errors import ConfigurationError
from ..loading import load_symbol
from .base import (
    AlgorithmIdentity,
    EvolutionAlgorithm,
    EvolutionOutcome,
    SelectionPolicy,
)
from .kernel import EvolutionKernel

BUILT_IN = {
    "ahe": "ahe_runner.algorithms.ahe:AheAlgorithm",
    "rrsi": "ahe_runner.algorithms.rrsi:RrsiAlgorithm",
}


def load_algorithm(config: ResolvedConfig) -> EvolutionAlgorithm:
    """Instantiate ``algorithm.name`` with its validated settings."""

    section = config.document.algorithm
    reference = BUILT_IN.get(section.name, section.name)
    if reference == section.name and ":" not in reference:
        raise ConfigurationError(
            f"unknown algorithm {section.name!r}; use one of "
            f"{sorted(BUILT_IN)} or 'module.path:Class'"
        )
    if reference in BUILT_IN.values():
        module_name, _, attribute = reference.partition(":")
        cls = getattr(importlib.import_module(module_name), attribute)
    else:
        cls = load_symbol(reference, project_root=config.project_root)
    if not inspect.isclass(cls) or not issubclass(cls, EvolutionAlgorithm):
        raise ConfigurationError(
            f"algorithm {section.name!r} is not an EvolutionAlgorithm subclass"
        )
    try:
        settings = cls.settings_model.model_validate(section.settings)
    except Exception as exc:
        raise ConfigurationError(f"invalid algorithm.settings: {exc}") from exc
    unknown_roles = sorted(set(section.role_supplements) - set(cls.roles))
    if unknown_roles:
        raise ConfigurationError(
            f"algorithm.role_supplements names unknown roles {unknown_roles}; "
            f"{section.name!r} has roles {list(cls.roles)}"
        )
    return cls(settings)


def algorithm_source(algorithm: EvolutionAlgorithm) -> Path | None:
    source = inspect.getsourcefile(type(algorithm))
    return Path(source).resolve() if source else None


__all__ = [
    "BUILT_IN",
    "AlgorithmIdentity",
    "EvolutionAlgorithm",
    "EvolutionKernel",
    "EvolutionOutcome",
    "SelectionPolicy",
    "algorithm_source",
    "load_algorithm",
]
