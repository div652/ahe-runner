"""Deterministic stand-ins for the RRSI roles (the scripted meta backend).

They exercise the real contracts: the analyst dispatches digesters through
the runner callback, and every role submits through submission.py. The
proposer adds one general reliability rule to the system prompt per edit,
which moves the bundled example benchmark.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from ...io import read_json
from ...meta import dispatch
from ...meta.base import RoleSpec
from . import submission

RULES = (
    "Verify tool results before claiming that an action succeeded.",
    "Track confirmed state separately from unknown or inferred state.",
    "Stop repeated actions and change strategy when an action has no effect.",
    "Check completion criteria before ending the interaction.",
)


def analyst(spec: RoleSpec) -> str:
    evidence = spec.read_roots["evidence"]
    manifest = read_json(evidence / "evidence_manifest.json")
    requests = []
    for task in manifest.get("tasks") or []:
        detail = read_json(evidence / str(task["detail_path"]))
        failing = any(
            trial.get("diagnostic_class") != "passing" for trial in detail["trials"]
        )
        requests.append(
            {
                "task": task["task_key"],
                "lens": "failure" if failing else "success",
                "questions": [],
            }
        )
    digests = dispatch.call(str(spec.context["rrsi_dispatch_token"]), requests[:8])
    failing = [d["task"] for d in digests if d.get("lens") == "failure"]
    report = {
        "failure_modes": [
            {
                "mode": "missing_reliability_rule",
                "n_tasks": len(failing),
                "affected_tasks": failing,
                "description": "A general reliability procedure is absent.",
                "needed_instead": "Make the procedure explicit.",
                "representative_evidence": [],
            }
        ]
        if failing
        else [],
        "capability_gaps": [],
        "success_habits": [],
    }
    result = submission.submit_report(spec.runtime_dir, json.dumps(report))
    return json.dumps(result)


def digester(spec: RoleSpec) -> str:
    lens = str(spec.context["rrsi_lens"])
    digest: dict[str, Any] = {
        "failure": {
            "blocker": "the required rule is not in the system prompt",
            "narrative": "The synthetic evaluator found no matching rule.",
            "evidence": [{"where": "result.json", "quote": "reward 0"}],
            "needed_instead": "state the rule",
        },
        "capability_gap": {
            "wanted": "apply a reliability rule",
            "why_couldnt": "the harness does not state it",
            "evidence": [],
        },
        "success": {"habits": [], "risk_if_removed": "none"},
    }[lens]
    result = submission.submit_digest(
        spec.runtime_dir,
        task=str(spec.context["rrsi_task"]),
        lens=lens,
        digest_json=json.dumps(digest),
    )
    return json.dumps(result)


def proposer(spec: RoleSpec) -> str:
    workspace = Path(str(spec.context["workspace_dir"]))
    prompt = workspace / "systemprompt.md"
    text = prompt.read_text(encoding="utf-8")
    missing = [rule for rule in RULES if rule not in text]
    offset = "ABCDEFGH".index(str(spec.context.get("rrsi_variant", "A")))
    budget = int(spec.context["rrsi_edit_budget"])
    chosen = missing[offset : offset + min(budget, 1)] or missing[:1] or [RULES[0]]
    for rule in chosen:
        text = text.rstrip() + f"\n- {rule}\n"
    prompt.write_text(text, encoding="utf-8")
    reserved = list(spec.context.get("rrsi_reserved_untried") or [])
    component = "prompt" if not reserved or "prompt" in reserved else reserved[0]
    edits = [
        {
            "id": "E1",
            "type": "improvement",
            "component": component,
            "paths": ["systemprompt.md"],
            "hypothesis": f"Stating '{chosen[0]}' makes the policy apply it.",
            "targets_mode": "missing_reliability_rule",
            "why_not_lower_lever": "The rule is a when-to-act instruction.",
            "trigger_condition": "every step that reports tool results",
            "predicted_affected": [],
            "retroactive_check": "Failing tasks lacked exactly this rule.",
            "regression_risk": "Slightly longer prompt.",
            "evidence": ["analysis:missing_reliability_rule"],
        }
    ]
    context = {**spec.context, "meta_output_dir": str(spec.runtime_dir)}
    result = submission.submit_candidate(
        context=context,
        summary=f"Add reliability rule: {chosen[0]}",
        edits=edits,
    )
    if not result.get("ok"):
        subprocess.run(["git", "-C", str(workspace), "reset", "--hard"], check=True)
    return json.dumps(result)


def critic(spec: RoleSpec) -> str:
    diff = (spec.read_roots["candidate"] / "diff.patch").read_text(encoding="utf-8")
    leaked = [
        task
        for task in spec.context.get("rrsi_training_task_ids") or []
        if task in diff
    ]
    result = submission.submit_review(
        spec.runtime_dir,
        verdict="reject" if leaked else "accept",
        reasons=[f"names training task {task}" for task in leaked],
        risk_notes=[],
    )
    return json.dumps(result)
