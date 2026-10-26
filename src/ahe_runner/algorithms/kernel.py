"""Runner services available to evolution algorithms.

Every method that does work runs as a durable, hash-verified stage, so an
algorithm that calls the same method with the same inputs after a restart
gets the recorded result instead of repeating the work.

Algorithms see only the training split. Validation and test tasks, their
evaluations, and their scores stay with the closing lifecycle.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..adapter import (
    BenchmarkInterface,
    EvaluationResult,
    MetaPromptContext,
    MetricSummary,
    SplitDefinition,
    SplitName,
    TransitionSummary,
)
from ..cost import evaluation_cost
from ..errors import IntegrityError
from ..harness import HarnessManifest, matched_component_paths
from ..io import atomic_write_json, read_json, sha256_json
from ..meta import RoleSpec
from ..state import StageContext, StageResult
from ..workspace import git_output, prepare_evolve_workspace, verify_checkpoint

if TYPE_CHECKING:
    from ..config import ResolvedConfig
    from ..controller import CampaignController

_LABEL_LEDGER = "allocation.json"
# Harness-manifest component groups and their change-manifest class names.
COMPONENT_CLASS_BY_GROUP = {
    "system_prompt": "system_prompt",
    "tool_descriptions": "tool_description",
    "tool_implementations": "tool_implementation",
    "middleware": "middleware",
    "skills": "skill",
    "sub_agents": "sub_agent",
    "long_term_memory": "memory",
}


class EvolutionKernel:
    def __init__(self, controller: CampaignController, namespace: str) -> None:
        self._controller = controller
        self.namespace = namespace

    # -- campaign facts -------------------------------------------------

    @property
    def config(self) -> ResolvedConfig:
        return self._controller.config

    @property
    def campaign_dir(self) -> Path:
        return self._controller.config.campaign_dir

    @property
    def algorithm_dir(self) -> Path:
        """Folder for the algorithm's own durable artifacts."""

        path = self.campaign_dir / "algorithm"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def rounds(self) -> int:
        return self._controller.config.document.campaign.rounds

    @property
    def train_split(self) -> SplitDefinition:
        return self._controller.splits[SplitName.TRAIN]

    @property
    def train_trials(self) -> int:
        return self._controller.config.document.evaluation.train_trials

    @property
    def harness_manifest(self) -> HarnessManifest:
        return self._controller.harness_manifest

    @property
    def prompt_context(self) -> MetaPromptContext:
        return self._controller.prompt_context

    @property
    def benchmark_interface(self) -> BenchmarkInterface:
        return self._controller.benchmark_interface

    def role_supplement(self, role: str) -> Path | None:
        return dict(self._controller.config.algorithm_role_supplements).get(role)

    def compare(self, before: MetricSummary, after: MetricSummary) -> TransitionSummary:
        return self._controller.adapter.compare(before, after)

    # -- stages ---------------------------------------------------------

    def stage(
        self,
        name: str,
        *,
        semantic_inputs: dict[str, Any],
        action: Callable[[StageContext], StageResult],
        retryable: bool = True,
    ) -> dict[str, Any]:
        """Run one durable stage named ``<namespace>.<name>``; return its data."""

        result = self._controller.stage_store.run(
            name=f"{self.namespace}.{name}",
            semantic_inputs=semantic_inputs,
            action=action,
            retryable=retryable,
        )
        self._controller._write_state()
        return result.data

    # -- checkpoints ------------------------------------------------------

    def checkpoint_dir(self, label: str) -> Path:
        return self.campaign_dir / "checkpoints" / label

    def checkpoint_manifest(self, label: str) -> dict[str, Any]:
        return verify_checkpoint(self.checkpoint_dir(label))

    def allocate_checkpoint(self, key: str) -> str:
        """The checkpoint label reserved for ``key``; stable across restarts.

        Labels are allocated in order of first request. A reserved label
        whose candidate is never frozen stays unused, so labels can skip.
        """

        ledger_path = self.campaign_dir / "checkpoints" / _LABEL_LEDGER
        ledger = read_json(ledger_path) if ledger_path.is_file() else {}
        allocations: dict[str, str] = dict(ledger.get("allocations") or {})
        if key in allocations:
            return allocations[key]
        used = {
            int(path.name[1:])
            for path in (self.campaign_dir / "checkpoints").glob("C*")
            if path.name[1:].isdecimal()
        }
        used |= {int(label[1:]) for label in allocations.values()}
        label = f"C{max(used, default=0) + 1}"
        allocations[key] = label
        atomic_write_json(
            ledger_path,
            {"schema_version": 1, "allocations": allocations},
        )
        return label

    def candidate_workspace(
        self,
        *,
        label: str,
        parent: str,
        attempt: int,
        recovery_dir: Path,
    ) -> Path:
        """A Git workspace seeded from ``parent`` for candidate ``label``.

        Attempt N > 1 continues from the last commit of attempt N - 1.
        """

        verify_checkpoint(self.checkpoint_dir(parent))
        return prepare_evolve_workspace(
            workspaces_dir=self.campaign_dir / "workspaces",
            checkpoint=label,
            attempt=attempt,
            parent_harness_dir=self.checkpoint_dir(parent) / "harness",
            recovery_output_dir=recovery_dir,
        )

    def validate_candidate(
        self,
        *,
        label: str,
        parent: str,
        workspace: Path,
    ) -> dict[str, Any]:
        """Generic and adapter validation of a committed candidate.

        Unlike AHE's round validation, an invalid candidate is reported, not
        raised, so the algorithm can drop it.
        """

        return self._controller._ensure_candidate_validation_for(
            label=label,
            parent=parent,
            workspace=workspace,
            stage_name=f"{self.namespace}.{label}.validate",
            raise_invalid=False,
        )

    def freeze_candidate(
        self,
        *,
        label: str,
        parent: str,
        workspace: Path,
        validation: dict[str, Any],
    ) -> dict[str, Any]:
        if not validation.get("valid"):
            raise IntegrityError(f"cannot freeze invalid candidate {label}")
        return self._controller._ensure_checkpoint_for(
            label=label,
            parent=parent,
            workspace=workspace,
            validation=validation,
            stage_name=f"{self.namespace}.{label}.freeze",
        )

    def component_changes(self, parent: str, label: str) -> list[dict[str, Any]]:
        """Files that differ between two checkpoints, with their change-manifest
        component class from the harness manifest ("agent_config" for the
        root agent configuration, "other" for any other file)."""

        before = verify_checkpoint(self.checkpoint_dir(parent))["harness_files"]
        after = verify_checkpoint(self.checkpoint_dir(label))["harness_files"]
        return component_diff(
            before,
            after,
            self.harness_manifest,
            self.checkpoint_dir(label) / "harness",
            self.checkpoint_dir(parent) / "harness",
        )

    # -- evaluation, evidence, cost -----------------------------------------

    def evaluate_train(self, label: str, *, repeat: int = 1) -> EvaluationResult:
        """Training evaluation of a checkpoint. ``repeat`` > 1 runs an
        independent re-evaluation of the same checkpoint with other seeds."""

        return self._controller._ensure_evaluation(
            _index(label),
            SplitName.TRAIN,
            repeat=repeat,
        )

    def train_evaluation_dir(self, label: str, *, repeat: int = 1) -> Path:
        return self._controller._evaluation_dir(
            _index(label),
            SplitName.TRAIN,
            repeat=repeat,
        )

    def evidence(self, label: str) -> Path:
        """The verified training-evidence bundle of a checkpoint's evaluation."""

        return self._controller._ensure_evidence(_index(label))

    def cost(self, label: str, *, repeat: int = 1) -> dict[str, Any]:
        """Priced evaluated-agent cost per trial of a training evaluation."""

        evaluation = self.evaluate_train(label, repeat=repeat)
        evaluation_dir = self.train_evaluation_dir(label, repeat=repeat)
        pricing = self.config.document.models.evaluated.pricing
        suffix = "" if repeat == 1 else f"-repeat-{repeat}"
        output = self.campaign_dir / "costs" / f"{label}-train{suffix}.json"

        def action(context: StageContext) -> StageResult:
            del context
            payload = {
                "checkpoint": label,
                "evaluation_id": evaluation.evaluation_id,
                **evaluation_cost(evaluation_dir, pricing),
            }
            atomic_write_json(output, payload)
            return StageResult(outputs=(output,), data=payload)

        result = self._controller.stage_store.run(
            name=f"cost.{label}.train{suffix}",
            semantic_inputs={
                "evaluation_id": evaluation.evaluation_id,
                "pricing": pricing.model_dump(mode="json") if pricing else None,
            },
            retryable=False,
            action=action,
        )
        return result.data

    # -- meta-agents ------------------------------------------------------

    def run_role(self, spec: RoleSpec) -> str:
        return self._controller.meta.run_role(spec)

    def git_output(self, repository: Path, *args: str) -> str:
        return git_output(repository, *args)


def component_diff(
    before: dict[str, str],
    after: dict[str, str],
    manifest: HarnessManifest,
    after_root: Path,
    before_root: Path,
) -> list[dict[str, Any]]:
    classes: dict[str, str] = {}
    globs = manifest.components.model_dump()
    for root in (after_root, before_root):
        for group, patterns in globs.items():
            for path in matched_component_paths(root, patterns):
                classes.setdefault(
                    path.relative_to(root).as_posix(),
                    COMPONENT_CLASS_BY_GROUP[group],
                )
    agent_config = manifest.agent_config
    rows = []
    for path in sorted(set(before) | set(after)):
        if before.get(path) == after.get(path):
            continue
        kind = (
            "added"
            if path not in before
            else "deleted"
            if path not in after
            else "modified"
        )
        component = classes.get(path)
        if component is None and path == agent_config:
            component = "agent_config"
        rows.append(
            {
                "path": path,
                "change": kind,
                "component": component or "other",
            }
        )
    return rows


def _index(label: str) -> int:
    if not label.startswith("C") or not label[1:].isdecimal():
        raise ValueError(f"invalid checkpoint label: {label!r}")
    return int(label[1:])


def stable_hash(value: Any) -> str:
    return sha256_json(value)
