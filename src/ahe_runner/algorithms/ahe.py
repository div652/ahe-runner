"""AHE: the Agentic Harness Engineering evolution loop.

One global Evolve update per round turns checkpoint C(i-1) into C(i):

    evaluate C(i-1) on train -> evidence -> per-task Debugger -> Evolve
    -> candidate validation -> freeze C(i) -> evaluate C(i) -> attribution

Explore runs once, before round 1. Every checkpoint is a nominee for
validation selection. Stage names are those of the original controller, so
campaign layouts are unchanged.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict

from ..adapter import EvaluationResult, SplitName
from ..change_manifest import load_change_manifest
from ..config import model_identity
from ..history import materialize_history, write_transition
from ..io import atomic_write_json, read_json, sha256_file, sha256_json
from ..state import StageContext, StageResult
from ..workspace import prepare_evolve_workspace, verify_checkpoint
from .base import AlgorithmIdentity, EvolutionAlgorithm, EvolutionOutcome

if TYPE_CHECKING:
    from ..controller import CampaignController
    from .kernel import EvolutionKernel


class AheSettings(BaseModel):
    """AHE is configured through the campaign's meta and debugger sections."""

    model_config = ConfigDict(extra="forbid")


class AheAlgorithm(EvolutionAlgorithm):
    settings_model = AheSettings
    default_selection_policy = "validation"
    supports_round_extension = True

    def identity(self) -> AlgorithmIdentity:
        return AlgorithmIdentity(
            name="ahe",
            version="1",
            description=(
                "Agentic Harness Engineering: Explore, per-task Debugger, and "
                "one global Evolve update per round"
            ),
        )

    def evolve(self, kernel: EvolutionKernel) -> EvolutionOutcome:
        controller = kernel._controller
        rounds = kernel.rounds
        controller._ensure_training_evaluation(0)
        controller._ensure_evidence(0)
        ensure_exploration(controller)
        for index in range(1, rounds + 1):
            run_round(controller, index)
        controller._ensure_training_evaluation(rounds)
        return EvolutionOutcome(
            pick=f"C{rounds}",
            nominees=[f"C{index}" for index in range(rounds + 1)],
            summary=f"{rounds} AHE rounds; every checkpoint is a nominee",
        )


def run_round(controller: CampaignController, index: int) -> dict[str, Any]:
    rounds = controller.config.document.campaign.rounds
    if not 1 <= index <= rounds:
        raise ValueError(f"round must be between 1 and {rounds}")
    controller.prepare()
    controller._ensure_training_evaluation(index - 1)
    evidence_dir = controller._ensure_evidence(index - 1)
    ensure_exploration(controller)
    debugger_dir = _ensure_debugger(controller, index, evidence_dir)
    history_dir = materialize_history(
        campaign_dir=controller.config.campaign_dir,
        through_round=index - 1,
    )
    attribution_dir = (
        controller.config.campaign_dir / "attribution" / f"C{index - 1}"
        if index > 1
        else None
    )
    evolve_result = _ensure_evolve(
        controller,
        index=index,
        evidence_dir=evidence_dir,
        debugger_dir=debugger_dir,
        history_dir=history_dir,
        attribution_dir=attribution_dir,
    )
    workspace = Path(str(evolve_result["workspace"]))
    validation = controller._ensure_candidate_validation_for(
        label=f"C{index}",
        parent=f"C{index - 1}",
        workspace=workspace,
        stage_name=f"round.{index}.validate",
        raise_invalid=True,
        report_path=(
            controller.config.campaign_dir / "evolve" / f"C{index}" / "validation.json"
        ),
    )
    controller._ensure_checkpoint_for(
        label=f"C{index}",
        parent=f"C{index - 1}",
        workspace=workspace,
        validation=validation,
        stage_name=f"round.{index}.freeze",
    )
    controller._ensure_training_evaluation(index)
    controller._ensure_evidence(index)
    _ensure_attribution(controller, index)
    controller._write_state()
    return {
        "checkpoint": f"C{index}",
        "evaluation": str(controller._evaluation_dir(index, SplitName.TRAIN)),
    }


def ensure_exploration(controller: CampaignController) -> Path:
    output_dir = controller.config.campaign_dir / "explore_skills"
    baseline = verify_checkpoint(controller._checkpoint_dir(0))

    def action(context: StageContext) -> StageResult:
        manifest = controller.meta.ensure_exploration(
            seed_harness=controller._checkpoint_dir(0) / "harness",
            output_dir=output_dir,
            attempt_dir=context.attempt_dir,
        )
        path = output_dir / "skills_manifest.json"
        if not path.is_file():
            atomic_write_json(path, manifest)
        return StageResult(outputs=(output_dir,), data=manifest)

    controller.stage_store.run(
        name="exploration",
        semantic_inputs={
            "baseline_harness_sha256": baseline["harness_sha256"],
            "meta_model": model_identity(controller.config.document.models.meta),
            **meta_stage_inputs(controller, "explorers"),
            "source_enabled": controller.config.document.meta.explore.source_enabled,
            "web_enabled": controller.config.document.meta.explore.web_enabled,
            "prompt_context": controller.prompt_context.model_dump(mode="json"),
        },
        action=action,
    )
    controller._write_state()
    return output_dir


def meta_stage_inputs(controller: CampaignController, role: str) -> dict[str, Any]:
    """Meta settings that change a stage's result (AHE-016), when set."""

    meta = controller.config.document.meta
    inputs: dict[str, Any] = {}
    overrides = getattr(meta.roles, role).model_dump(exclude_none=True)
    if overrides:
        inputs["meta_role_overrides"] = overrides
    if meta.shell.backend != "disabled":
        inputs["meta_shell"] = meta.shell.model_dump(mode="json")
    return inputs


def _ensure_debugger(
    controller: CampaignController,
    index: int,
    evidence_dir: Path,
) -> Path:
    checkpoint = f"C{index - 1}"
    output_dir = controller.config.campaign_dir / "debugger" / f"C{index}"
    evidence_sha = sha256_file(evidence_dir / "evidence_manifest.json")

    def action(context: StageContext) -> StageResult:
        result = controller.meta.run_debugger(
            checkpoint=checkpoint,
            evidence_dir=evidence_dir,
            output_dir=output_dir,
            attempt_dir=context.attempt_dir,
        )
        return StageResult(outputs=(output_dir,), data=result)

    controller.stage_store.run(
        name=f"round.{index}.debugger",
        semantic_inputs={
            "parent_checkpoint": checkpoint,
            "evidence_manifest_sha256": evidence_sha,
            "meta_model": model_identity(controller.config.document.models.meta),
            **meta_stage_inputs(controller, "debugger"),
            "prompt_context": controller.prompt_context.model_dump(mode="json"),
        },
        action=action,
    )
    controller._write_state()
    return output_dir


def _ensure_evolve(
    controller: CampaignController,
    *,
    index: int,
    evidence_dir: Path,
    debugger_dir: Path,
    history_dir: Path,
    attribution_dir: Path | None,
) -> dict[str, Any]:
    campaign_dir = controller.config.campaign_dir
    parent = controller._checkpoint_dir(index - 1)
    parent_manifest = verify_checkpoint(parent)
    output_root = campaign_dir / "evolve" / f"C{index}"

    def action(context: StageContext) -> StageResult:
        workspace = prepare_evolve_workspace(
            workspaces_dir=campaign_dir / "workspaces",
            checkpoint=f"C{index}",
            attempt=context.attempt,
            parent_harness_dir=parent / "harness",
            recovery_output_dir=context.attempt_dir / "recovery",
        )
        recovery_dir = context.attempt_dir / "recovery" if context.attempt > 1 else None
        meta_output = output_root / f"attempt-{context.attempt:04d}"
        result = controller.meta.run_evolve(
            round_index=index,
            workspace=workspace,
            evidence_dir=evidence_dir,
            raw_evaluation_dir=controller._evaluation_dir(index - 1, SplitName.TRAIN),
            debugger_dir=debugger_dir,
            history_dir=history_dir,
            attribution_dir=attribution_dir,
            explore_skills_dir=campaign_dir / "explore_skills",
            recovery_dir=recovery_dir,
            output_dir=meta_output,
        )
        return StageResult(
            outputs=(workspace, meta_output),
            data={
                "workspace": str(workspace),
                "meta_output": str(meta_output),
                "result": result,
            },
        )

    result = controller.stage_store.run(
        name=f"round.{index}.evolve",
        semantic_inputs={
            "round": index,
            "parent_checkpoint": f"C{index - 1}",
            "parent_harness_sha256": parent_manifest["harness_sha256"],
            "evidence_sha256": sha256_file(evidence_dir / "evidence_manifest.json"),
            "debugger_sha256": sha256_file(debugger_dir / "overview.json"),
            "meta_model": model_identity(controller.config.document.models.meta),
            **meta_stage_inputs(controller, "evolve"),
            "explore_skills_sha256": sha256_file(
                campaign_dir / "explore_skills" / "skills_manifest.json"
            ),
        },
        action=action,
    )
    controller._write_state()
    return result.data


def _ensure_attribution(controller: CampaignController, index: int) -> Path:
    before = EvaluationResult.model_validate(
        read_json(
            controller._evaluation_dir(index - 1, SplitName.TRAIN)
            / "adapter_result.json"
        )
    )
    after = EvaluationResult.model_validate(
        read_json(
            controller._evaluation_dir(index, SplitName.TRAIN) / "adapter_result.json"
        )
    )
    change_manifest = load_change_manifest(
        controller._checkpoint_dir(index) / "harness" / "change_manifest.json"
    )
    output_dir = controller.config.campaign_dir / "attribution" / f"C{index}"
    task_aliases = attribution_task_aliases(controller, index, before, after)

    def action(context: StageContext) -> StageResult:
        del context
        transition = controller.adapter.compare(before.summary, after.summary)
        path = write_transition(
            output_dir=output_dir,
            transition=transition,
            change_manifest=change_manifest,
            task_aliases=task_aliases,
        )
        return StageResult(
            outputs=(path,),
            data={"transition": transition.model_dump(mode="json")},
        )

    controller.stage_store.run(
        name=f"round.{index}.attribute",
        semantic_inputs={
            "before_evaluation": before.evaluation_id,
            "after_evaluation": after.evaluation_id,
            "change_manifest_sha256": sha256_json(
                change_manifest.model_dump(mode="json")
            ),
            "task_aliases_sha256": sha256_json(task_aliases),
        },
        retryable=False,
        action=action,
    )
    return output_dir


def attribution_task_aliases(
    controller: CampaignController,
    index: int,
    before: EvaluationResult,
    after: EvaluationResult,
) -> dict[str, str]:
    """Map debugger keys, task ids and task keys to evaluation task keys."""

    aliases: dict[str, str] = {}
    key_by_task_id: dict[str, str] = {}
    for task in (*before.summary.task_states, *after.summary.task_states):
        aliases[task.task_key] = task.task_key
        if task.task_id:
            key_by_task_id[task.task_id] = task.task_key
            aliases[task.task_id] = task.task_key
    summary_path = (
        controller.config.campaign_dir / "debugger" / f"C{index}" / "summary.json"
    )
    if summary_path.is_file():
        for row in read_json(summary_path).get("tasks") or []:
            debugger_key = row.get("task_key")
            task_key = key_by_task_id.get(row.get("task_id") or "")
            if debugger_key and task_key:
                aliases[str(debugger_key)] = task_key
    return aliases
