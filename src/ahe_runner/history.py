"""Train-only history and task stability for Evolve."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .adapter import EvaluationResult, TransitionSummary
from .change_manifest import ChangeManifest
from .io import atomic_write_json, read_json


def materialize_history(
    *,
    campaign_dir: Path,
    through_round: int,
) -> Path:
    rows: list[dict[str, Any]] = []
    trajectories: dict[str, list[dict[str, Any]]] = {}
    for index in range(through_round + 1):
        checkpoint = f"C{index}"
        evaluation_path = (
            campaign_dir / "evaluations" / f"{checkpoint}-train" / "adapter_result.json"
        )
        evaluation = EvaluationResult.model_validate(read_json(evaluation_path))
        row: dict[str, Any] = {
            "checkpoint": checkpoint,
            "primary_metric": evaluation.summary.primary.model_dump(mode="json"),
            "metrics": evaluation.summary.metrics,
            "task_count": evaluation.summary.task_count,
            "rollout_count": evaluation.summary.rollout_count,
            "resource_totals": evaluation.summary.resource_totals,
        }
        if index > 0:
            transition_path = (
                campaign_dir / "attribution" / checkpoint / "transition.json"
            )
            if transition_path.is_file():
                row["transition_from_parent"] = read_json(transition_path)
            manifest_path = (
                campaign_dir
                / "checkpoints"
                / checkpoint
                / "harness"
                / "change_manifest.json"
            )
            if manifest_path.is_file():
                row["change_manifest"] = read_json(manifest_path)
        rows.append(row)
        for task in evaluation.summary.task_states:
            trajectories.setdefault(task.task_key, []).append(
                {
                    "checkpoint": checkpoint,
                    "state": task.state,
                    "score": task.score,
                }
            )

    history = {
        "schema_version": 1,
        "scope": "evolution-training-only",
        "through_checkpoint": f"C{through_round}",
        "checkpoints": rows,
        "task_stability": _task_stability(trajectories),
    }
    output = campaign_dir / "history"
    atomic_write_json(output / "history.json", history)
    (output / "history.md").write_text(_render_history(history), encoding="utf-8")
    return output


def write_transition(
    *,
    output_dir: Path,
    transition: TransitionSummary,
    change_manifest: ChangeManifest | dict[str, Any],
    task_aliases: Mapping[str, str] | None = None,
) -> Path:
    """Write per-change attribution.

    ``task_aliases`` maps every task reference Evolve may cite (debugger task
    keys, task ids, evaluation task keys) to the evaluation task key used by
    the transition. References without an alias are matched verbatim.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = ChangeManifest.model_validate(change_manifest)
    aliases = dict(task_aliases or {})
    known_keys = set(aliases.values())
    unresolved: set[str] = set()

    def resolve(references: list[str]) -> set[str]:
        resolved: set[str] = set()
        for reference in references:
            key = aliases.get(reference, reference)
            if known_keys and key not in known_keys:
                unresolved.add(reference)
            resolved.add(key)
        return resolved

    improved = set(transition.improved_task_keys)
    regressed = set(transition.regressed_task_keys)
    all_predicted: set[str] = set()
    all_risks: set[str] = set()
    observations: list[dict[str, Any]] = []
    for change in manifest.changes:
        predicted = resolve(change.predicted_fixes)
        risks = resolve(change.risk_tasks)
        all_predicted.update(predicted)
        all_risks.update(risks)
        fixes_observed = predicted & improved
        fixes_missing = predicted - improved
        risks_observed = risks & regressed
        if risks_observed and not fixes_observed:
            verdict = "HARMFUL"
        elif risks_observed and fixes_observed:
            verdict = "MIXED"
        elif predicted and fixes_observed == predicted:
            verdict = "EFFECTIVE"
        elif fixes_observed:
            verdict = "PARTIALLY_EFFECTIVE"
        else:
            verdict = "INEFFECTIVE"
        observations.append(
            {
                "change_id": change.id,
                "type": change.type,
                "component": change.component,
                "paths": change.paths,
                "predicted_fixes": change.predicted_fixes,
                "predicted_fix_task_keys": sorted(predicted),
                "predicted_fixes_observed": sorted(fixes_observed),
                "predicted_fixes_missing": sorted(fixes_missing),
                "risk_tasks": change.risk_tasks,
                "risk_task_keys": sorted(risks),
                "risks_observed": sorted(risks_observed),
                "hit_rate": (
                    f"{len(fixes_observed)}/{len(predicted)}" if predicted else "0/0"
                ),
                "verdict": verdict,
                "interpretation": (
                    "Heuristic observation from the combined candidate; "
                    "this is not an isolated causal measurement."
                ),
            }
        )
    classification = _transition_classification(
        improved=improved,
        regressed=regressed,
        predicted=all_predicted,
    )
    payload = {
        "schema_version": 2,
        "classification": classification,
        "attribution_kind": "combined_candidate_prediction_matching",
        "change_observations": observations,
        "unattributed_improvements": sorted(improved - all_predicted),
        "unanticipated_regressions": sorted(regressed - all_risks),
        "unresolved_task_references": sorted(unresolved),
        "transition": transition.model_dump(mode="json"),
    }
    path = output_dir / "transition.json"
    atomic_write_json(path, payload)
    return path


def _transition_classification(
    *,
    improved: set[str],
    regressed: set[str],
    predicted: set[str],
) -> str:
    if regressed and not improved:
        return "harmful"
    if improved and regressed:
        return "mixed"
    if predicted and predicted <= improved:
        return "effective"
    if improved:
        return "partially_effective"
    return "ineffective"


def _task_stability(
    trajectories: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    classes: dict[str, list[str]] = {
        "stable": [],
        "unstable": [],
        "insufficient_history": [],
    }
    for task_key, rows in sorted(trajectories.items()):
        states = [str(row["state"]) for row in rows]
        if len(states) < 2:
            category = "insufficient_history"
        elif len(set(states)) == 1:
            category = "stable"
        else:
            category = "unstable"
        classes[category].append(task_key)
    return {"classes": classes, "trajectories": trajectories}


def _render_history(history: dict[str, Any]) -> str:
    lines = [
        "# AHE evolution history",
        "",
        (
            "This file contains completed evolution-training evidence only. "
            "Validation and final-test evidence is excluded."
        ),
        "",
        "| Checkpoint | Primary metric | Tasks | Rollouts |",
        "|---|---:|---:|---:|",
    ]
    for row in history["checkpoints"]:
        primary = row["primary_metric"]
        lines.append(
            f"| {row['checkpoint']} | {primary['name']}={primary['value']:.6f} | "
            f"{row['task_count']} | {row['rollout_count']} |"
        )
    lines.extend(["", "## Task stability", ""])
    for category, task_keys in history["task_stability"]["classes"].items():
        lines.append(f"- **{category}:** {len(task_keys)}")
    lines.extend(
        [
            "",
            "Read `history.json` for native metrics, transitions, manifests, and "
            "task trajectories.",
        ]
    )
    return "\n".join(lines) + "\n"
