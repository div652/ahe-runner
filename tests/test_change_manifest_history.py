from __future__ import annotations

from pathlib import Path

import pytest

from ahe_runner.adapter import TransitionSummary
from ahe_runner.change_manifest import load_change_manifest
from ahe_runner.errors import IntegrityError
from ahe_runner.history import write_transition
from ahe_runner.io import atomic_write_json, read_json


def _manifest() -> dict:
    return {
        "schema_version": 2,
        "checkpoint": "C2",
        "parent_checkpoint": "C1",
        "objective": "Improve two independent recurrent behaviors.",
        "changes": [
            {
                "id": "chg-verify",
                "type": "improvement",
                "component": "system_prompt",
                "paths": ["systemprompt.md"],
                "evidence": ["debugger/task-a:verification-gap"],
                "failure_pattern": "actions occur before verification",
                "mechanism": "require verification before consequential actions",
                "rationale": "the failure is a parent decision policy",
                "predicted_fixes": ["task-a"],
                "risk_tasks": ["task-c"],
            },
            {
                "id": "chg-tool-errors",
                "type": "improvement",
                "component": "tool_description",
                "paths": ["tool_descriptions/record_note.tool.yaml"],
                "evidence": ["debugger/task-b:argument-gap"],
                "failure_pattern": "tool arguments omit required context",
                "mechanism": "make argument preconditions explicit",
                "rationale": "the schema description is ambiguous",
                "predicted_fixes": ["task-b"],
                "risk_tasks": [],
            },
        ],
        "validation": ["runner generic validation"],
    }


def test_change_manifest_requires_complete_diff_coverage(tmp_path: Path) -> None:
    path = tmp_path / "change_manifest.json"
    atomic_write_json(path, _manifest())

    manifest = load_change_manifest(
        path,
        checkpoint="C2",
        parent_checkpoint="C1",
        changed_paths=[
            "systemprompt.md",
            "tool_descriptions/record_note.tool.yaml",
            "change_manifest.json",
        ],
    )
    assert manifest.declared_paths == {
        "systemprompt.md",
        "tool_descriptions/record_note.tool.yaml",
    }

    with pytest.raises(IntegrityError, match="undeclared changed paths"):
        load_change_manifest(
            path,
            checkpoint="C2",
            parent_checkpoint="C1",
            changed_paths=[
                "systemprompt.md",
                "tool_descriptions/record_note.tool.yaml",
                "middleware/new_policy.py",
                "change_manifest.json",
            ],
        )


def test_change_manifest_rejects_untyped_logical_changes(tmp_path: Path) -> None:
    value = _manifest()
    value["changes"] = [{}]
    path = tmp_path / "change_manifest.json"
    atomic_write_json(path, value)

    with pytest.raises(IntegrityError, match="invalid change_manifest.json"):
        load_change_manifest(path)


def test_transition_attributes_each_change_against_combined_candidate(
    tmp_path: Path,
) -> None:
    path = write_transition(
        output_dir=tmp_path,
        transition=TransitionSummary(
            primary_delta=0.1,
            improved_task_keys=["task-a", "task-extra"],
            regressed_task_keys=["task-c", "task-surprise"],
            unchanged_task_keys=["task-b"],
        ),
        change_manifest=_manifest(),
    )
    payload = read_json(path)

    observations = {row["change_id"]: row for row in payload["change_observations"]}
    assert observations["chg-verify"]["verdict"] == "MIXED"
    assert observations["chg-verify"]["predicted_fixes_observed"] == ["task-a"]
    assert observations["chg-verify"]["risks_observed"] == ["task-c"]
    assert observations["chg-tool-errors"]["verdict"] == "INEFFECTIVE"
    assert payload["unattributed_improvements"] == ["task-extra"]
    assert payload["unanticipated_regressions"] == ["task-surprise"]
    assert payload["attribution_kind"] == "combined_candidate_prediction_matching"
