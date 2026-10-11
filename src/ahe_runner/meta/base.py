"""Meta-agent backend interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ahe_runner.adapter import BenchmarkInterface, MetaPromptContext
from ahe_runner.config import ResolvedConfig
from ahe_runner.harness import HarnessManifest


@dataclass(frozen=True, slots=True)
class MetaBackendContext:
    config: ResolvedConfig
    prompt_context: MetaPromptContext
    benchmark_interface: BenchmarkInterface
    harness_manifest: HarnessManifest
    method_reference: Path


class MetaBackend(ABC):
    def __init__(self, context: MetaBackendContext) -> None:
        self.context = context

    @abstractmethod
    def ensure_exploration(
        self,
        *,
        seed_harness: Path,
        output_dir: Path,
        attempt_dir: Path,
    ) -> dict[str, Any]:
        """Create verified Source and Web Explore skills."""

    @abstractmethod
    def run_debugger(
        self,
        *,
        checkpoint: str,
        evidence_dir: Path,
        output_dir: Path,
        attempt_dir: Path,
    ) -> dict[str, Any]:
        """Distill every training task into reusable diagnosis."""

    @abstractmethod
    def run_evolve(
        self,
        *,
        round_index: int,
        workspace: Path,
        evidence_dir: Path,
        raw_evaluation_dir: Path,
        debugger_dir: Path,
        history_dir: Path,
        attribution_dir: Path | None,
        explore_skills_dir: Path,
        recovery_dir: Path | None,
        output_dir: Path,
    ) -> dict[str, Any]:
        """Continue one logical global Evolve stage."""
