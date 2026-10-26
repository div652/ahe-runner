"""Meta-agent backend interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from ahe_runner.adapter import BenchmarkInterface, MetaPromptContext
from ahe_runner.config import ModelSettings, ResolvedConfig
from ahe_runner.harness import HarnessManifest


@dataclass(frozen=True, slots=True)
class MetaBackendContext:
    config: ResolvedConfig
    prompt_context: MetaPromptContext
    benchmark_interface: BenchmarkInterface
    harness_manifest: HarnessManifest
    method_reference: Path


@dataclass(frozen=True, slots=True)
class RoleTool:
    """A tool an algorithm supplies for one of its roles."""

    name: str
    description_path: Path  # NexAU *.tool.yaml
    binding: str  # "module.path:function"


@dataclass(frozen=True, slots=True)
class RoleSpec:
    """One run of an algorithm-defined meta-agent role.

    The backend supplies model routing, call recording, path-guarded file
    tools, long-output handling, and context compaction; the algorithm
    supplies the prompt, the query, the tools, and the roots the role may
    read and write.
    """

    role: str  # algorithm role key, for logs and supplements
    agent_name: str
    description: str
    prompt_path: Path
    query: str
    runtime_dir: Path
    max_iterations: int
    default_root: str
    read_roots: dict[str, Path] = field(default_factory=dict)
    write_roots: dict[str, Path] = field(default_factory=dict)
    # Runner tools by name (read_file, write_file, git_commit, ...), plus
    # algorithm tools.
    tools: tuple[str, ...] = ()
    algorithm_tools: tuple[RoleTool, ...] = ()
    stop_tools: tuple[str, ...] = ()
    skills: tuple[Path, ...] = ()
    supplement: Path | None = None
    # Extra values tools read from the agent context.
    context: dict[str, Any] = field(default_factory=dict)
    # "reader" uses the Debugger's output limits, "writer" the Evolve Agent's.
    profile: Literal["reader", "writer"] = "reader"
    settings: ModelSettings | None = None  # None: models.meta
    # Run the endpoint preflight first. A role started from inside another
    # role's stage (a sub-role) can rely on that stage's preflight.
    preflight: bool = True
    # Deterministic stand-in used by the scripted backend (tests, examples).
    scripted: Callable[[RoleSpec], str] | None = None


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

    def run_role(self, spec: RoleSpec) -> str:
        """Run one algorithm-defined role; return its final response."""

        raise NotImplementedError(
            f"{type(self).__name__} does not run algorithm-defined roles"
        )
