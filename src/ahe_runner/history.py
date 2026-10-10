"""Train-only history and task stability for Evolve."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .adapter import EvaluationResult, TransitionSummary
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
    change_manifest: dict[str, Any],
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    predicted = {
        key
        for row in change_manifest.get("predicted_effects", [])
        if isinstance(row, dict)
        for key in row.get("task_keys", [])
    }
    improved = set(transition.improved_task_keys)
    regressed = set(transition.regressed_task_keys)
    if regressed and not improved:
        classification = "harmful"
    elif predicted and predicted <= improved and not regressed:
        classification = "effective"
    elif improved and regressed:
        classification = "mixed"
    elif improved:
        classification = "partially_effective"
    else:
        classification = "ineffective"
    payload = {
        "schema_version": 1,
        "classification": classification,
        "predicted_task_keys": sorted(predicted),
        "predicted_improvements_observed": sorted(predicted & improved),
        "predicted_improvements_missing": sorted(predicted - improved),
        "transition": transition.model_dump(mode="json"),
    }
    path = output_dir / "transition.json"
    atomic_write_json(path, payload)
    return path


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
