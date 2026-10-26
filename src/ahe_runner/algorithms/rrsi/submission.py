"""Validation and persistence of the RRSI roles' deliverables.

Each function is used by the role's NexAU stop tool and by its scripted
stand-in, so both paths enforce the same contract. A function returns
``{"ok": True, ...}`` after writing its deliverable, or ``{"ok": False,
"status": "error", "errors": [...]}`` so the agent can fix and resubmit.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

from ...adapter import CandidateValidationRequest
from ...change_manifest import load_change_manifest
from ...config import load_config
from ...harness import load_harness_manifest, matched_component_paths, validate_harness
from ...io import atomic_write_json, read_json, sha256_json, tree_hashes
from ...loading import load_adapter
from ..kernel import COMPONENT_CLASS_BY_GROUP
from .components import normalize_tag, primary_class

CANDIDATE_FILE = "candidate.json"
REPORT_FILE = "analysis_report.json"
DIGEST_FILE = "digest.json"
REVIEW_FILE = "review.json"
DIGEST_MAX_CHARS = 6000
_EDIT_TEXT_FIELDS = (
    "hypothesis",
    "targets_mode",
    "why_not_lower_lever",
    "trigger_condition",
    "retroactive_check",
    "regression_risk",
)
_LENS_KEYS = {
    "failure": ("blocker", "narrative", "evidence", "needed_instead"),
    "capability_gap": ("wanted", "why_couldnt", "evidence"),
    "success": ("habits", "risk_if_removed"),
}
_REPORT_KEYS = ("failure_modes", "capability_gaps", "success_habits")


def error(*problems: str) -> dict[str, Any]:
    return {"ok": False, "status": "error", "errors": list(problems)}


def submit_report(output_dir: Path, report_json: str) -> dict[str, Any]:
    try:
        report = json.loads(report_json)
    except ValueError as exc:
        return error(f"report_json is not valid JSON: {exc}")
    if not isinstance(report, dict):
        return error("the report must be a JSON object")
    problems = [
        f"{key} must be a list"
        for key in _REPORT_KEYS
        if not isinstance(report.get(key), list)
    ]
    if problems:
        return error(*problems)
    payload = {key: report[key] for key in _REPORT_KEYS}
    payload["failure_modes"] = sorted(
        payload["failure_modes"],
        key=lambda mode: (
            -int(mode.get("n_tasks") or 0) if isinstance(mode, dict) else 0
        ),
    )
    atomic_write_json(output_dir / REPORT_FILE, payload)
    return {"ok": True, "failure_modes": len(payload["failure_modes"])}


def submit_digest(
    output_dir: Path,
    *,
    task: str,
    lens: str,
    digest_json: str,
) -> dict[str, Any]:
    if len(digest_json) > DIGEST_MAX_CHARS:
        return error(
            f"the digest is {len(digest_json)} characters; the limit is "
            f"{DIGEST_MAX_CHARS}. Shorten it and submit again."
        )
    try:
        digest = json.loads(digest_json)
    except ValueError as exc:
        return error(f"digest_json is not valid JSON: {exc}")
    if not isinstance(digest, dict):
        return error("the digest must be a JSON object")
    missing = [key for key in _LENS_KEYS[lens] if key not in digest]
    if missing:
        return error(f"the {lens} digest is missing {missing}")
    payload = {**digest, "task": task, "lens": lens}
    atomic_write_json(output_dir / DIGEST_FILE, payload)
    return {"ok": True}


def submit_review(
    output_dir: Path,
    *,
    verdict: str,
    reasons: list[str],
    risk_notes: list[str],
) -> dict[str, Any]:
    if verdict not in {"accept", "reject"}:
        return error("verdict must be accept or reject")
    if verdict == "reject" and not [reason for reason in reasons if reason.strip()]:
        return error("a rejection must give at least one reason")
    atomic_write_json(
        output_dir / REVIEW_FILE,
        {"verdict": verdict, "reasons": reasons, "risk_notes": risk_notes},
    )
    return {"ok": True, "verdict": verdict}


def submit_candidate(
    *,
    context: dict[str, Any],
    summary: str,
    edits: list[dict[str, Any]],
) -> dict[str, Any]:
    """Check the declared edits, write change_manifest.json, commit, validate.

    ``context`` keys: workspace_dir, rrsi_base_commit, rrsi_edit_budget,
    rrsi_reserved_untried, checkpoint, parent_checkpoint, parent_harness_dir,
    harness_manifest_path, project_root, config_path, training_task_ids,
    parent_runtime_owned_hashes, meta_output_dir.
    """

    workspace = Path(str(context["workspace_dir"])).resolve()
    output_dir = Path(str(context["meta_output_dir"])).resolve()
    base = str(context["rrsi_base_commit"])
    budget = int(context["rrsi_edit_budget"])
    reserved = list(context.get("rrsi_reserved_untried") or [])
    problems: list[str] = []
    if not summary.strip():
        problems.append("summary must not be empty")
    if not edits:
        problems.append("declare at least one edit")
    if len(edits) > budget:
        problems.append(
            f"{len(edits)} edits exceed this round's edit budget b_t = {budget}; "
            "drop or merge edits"
        )
    ids = [str(edit.get("id", "")).strip() for edit in edits]
    if len(set(ids)) != len(ids) or not all(ids):
        problems.append("edit ids must be present and unique")
    for edit in edits:
        empty = [key for key in _EDIT_TEXT_FIELDS if not str(edit.get(key, "")).strip()]
        if empty:
            problems.append(f"edit {edit.get('id')!r} has empty {empty}")
        if not edit.get("evidence"):
            problems.append(f"edit {edit.get('id')!r} cites no evidence")
    _git(workspace, "add", "--all")
    changes = _changed_files(workspace, base, context)
    changed_paths = {row["path"] for row in changes}
    if not changed_paths:
        problems.append(
            "the workspace has no harness change; implement the edits first"
        )
    declared: dict[str, str] = {}
    for edit in edits:
        for path in edit.get("paths") or []:
            normalized = str(path).strip().removeprefix("workspace:").lstrip("/")
            if normalized in declared:
                problems.append(f"{normalized} is declared by two edits")
            declared[normalized] = str(edit.get("id"))
    undeclared = sorted(changed_paths - set(declared))
    unchanged = sorted(set(declared) - changed_paths)
    if undeclared:
        problems.append(f"changed files not covered by any edit: {undeclared}")
    if unchanged:
        problems.append(f"declared files that are unchanged: {unchanged}")
    if problems:
        return error(*problems)

    by_path = {row["path"]: row for row in changes}
    records: list[dict[str, Any]] = []
    retags: list[str] = []
    for edit in edits:
        paths = [
            str(path).strip().removeprefix("workspace:").lstrip("/")
            for path in edit["paths"]
        ]
        rows = [by_path[path] for path in paths]
        tag, reason = normalize_tag(edit.get("component"), rows)
        if reason:
            retags.append(f"edit {edit['id']}: {reason}")
        records.append({**edit, "paths": paths, "component": tag, "files": rows})
    if reserved and not any(record["component"] in reserved for record in records):
        return error(
            "this variant holds a RESERVED EXPLORATION SLOT: at least one edit must "
            f"be on a never-exercised component from {reserved}, judged by the "
            "files it changes",
            *retags,
        )

    manifest = {
        "schema_version": 2,
        "checkpoint": str(context["checkpoint"]),
        "parent_checkpoint": str(context["parent_checkpoint"]),
        "objective": summary.strip(),
        "changes": [_change_record(record) for record in records],
        "validation": ["rrsi_submit_candidate: runner and adapter validation"],
    }
    manifest_path = workspace / "change_manifest.json"
    atomic_write_json(manifest_path, manifest)
    try:
        load_change_manifest(
            manifest_path,
            checkpoint=manifest["checkpoint"],
            parent_checkpoint=manifest["parent_checkpoint"],
            changed_paths=sorted(changed_paths),
        )
    except Exception as exc:
        return error(f"the declared edits do not form a valid change manifest: {exc}")
    _git(workspace, "add", "--all")
    if _git(workspace, "status", "--porcelain").strip():
        _git(
            workspace,
            "commit",
            "--quiet",
            "-m",
            f"RRSI candidate {context['checkpoint']}: {summary.strip()[:200]}",
        )
    head = _git(workspace, "rev-parse", "HEAD").strip()
    validation = validate_workspace(context, workspace)
    atomic_write_json(output_dir / "candidate_validation.json", validation)
    if not validation["valid"]:
        return error(
            "candidate validation failed; fix the harness and submit again",
            *validation["errors"][:20],
        )
    payload = {
        "schema_version": 1,
        "checkpoint": str(context["checkpoint"]),
        "parent_checkpoint": str(context["parent_checkpoint"]),
        "base_commit": base,
        "head": head,
        "tree_sha256": sha256_json(tree_hashes(workspace)),
        "summary": summary.strip(),
        "edits": [
            {key: value for key, value in record.items() if key != "files"}
            | {"files": record["files"]}
            for record in records
        ],
        "retags": retags,
    }
    atomic_write_json(output_dir / CANDIDATE_FILE, payload)
    return {"ok": True, "head": head, "retags": retags}


def validate_workspace(context: dict[str, Any], workspace: Path) -> dict[str, Any]:
    """The runner's generic and the adapter's candidate validation."""

    errors: list[str] = []
    try:
        report = validate_harness(
            workspace,
            load_harness_manifest(Path(str(context["harness_manifest_path"]))),
            project_root=Path(str(context["project_root"])),
            training_task_ids=tuple(context.get("training_task_ids") or ()),
            parent_runtime_owned_hashes=dict(
                context.get("parent_runtime_owned_hashes") or {}
            ),
            run_nexau_smoke=True,
        )
        errors.extend(report.get("errors") or [])
        adapter = load_adapter(load_config(Path(str(context["config_path"]))))
        adapter_result = adapter.validate_candidate(
            CandidateValidationRequest(
                checkpoint=str(context["checkpoint"]),
                parent_checkpoint=str(context["parent_checkpoint"]),
                harness_dir=workspace,
                parent_harness_dir=Path(str(context["parent_harness_dir"])),
                training_task_ids=tuple(context.get("training_task_ids") or ()),
            )
        )
        errors.extend(adapter_result.errors)
        valid = bool(report.get("valid")) and adapter_result.valid
    except Exception as exc:
        return {"valid": False, "errors": [f"{type(exc).__name__}: {exc}"]}
    return {"valid": valid and not errors, "errors": errors}


def _change_record(record: dict[str, Any]) -> dict[str, Any]:
    classes = [row["component"] for row in record["files"]]
    component = primary_class(classes)
    if component == "other":
        component = "tool_implementation"
    identifier = re.sub(r"[^A-Za-z0-9._-]", "-", str(record["id"])).strip("-")
    rationale = (
        f"Why not a lower lever: {record['why_not_lower_lever']} "
        f"Trigger: {record['trigger_condition']} "
        f"Retroactive check: {record['retroactive_check']} "
        f"Regression risk: {record['regression_risk']}"
    )
    return {
        "id": f"chg-{identifier or 'edit'}",
        "type": record["type"],
        "component": component,
        "paths": record["paths"],
        "evidence": [str(item) for item in dict.fromkeys(record["evidence"])],
        "failure_pattern": record["targets_mode"],
        "mechanism": record["hypothesis"],
        "rationale": rationale,
        "predicted_fixes": [
            str(item) for item in dict.fromkeys(record.get("predicted_affected") or [])
        ],
        "risk_tasks": [],
        "tag": record["component"],
    }


def _changed_files(
    workspace: Path,
    base: str,
    context: dict[str, Any],
) -> list[dict[str, str]]:
    """Files staged in ``workspace`` that differ from ``base``, with classes."""

    output = _git(workspace, "diff", "--cached", "--name-status", "--no-renames", base)
    manifest = load_harness_manifest(Path(str(context["harness_manifest_path"])))
    parent = Path(str(context["parent_harness_dir"]))
    classes: dict[str, str] = {}
    for root in (workspace, parent):
        for group, patterns in manifest.components.model_dump().items():
            for path in matched_component_paths(root, patterns):
                classes.setdefault(
                    path.relative_to(root).as_posix(),
                    COMPONENT_CLASS_BY_GROUP[group],
                )
    rows = []
    for line in output.splitlines():
        status, _, path = line.partition("\t")
        if not path or path == "change_manifest.json":
            continue
        component = classes.get(path)
        if component is None:
            component = "agent_config" if path == manifest.agent_config else "other"
        rows.append(
            {
                "path": path,
                "change": {"A": "added", "D": "deleted"}.get(status[:1], "modified"),
                "component": component,
            }
        )
    return rows


def _git(workspace: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(workspace), *args],
        check=False,
        text=True,
        capture_output=True,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            completed.stderr.strip() or f"git {' '.join(args)} failed in {workspace}"
        )
    return completed.stdout


def load_candidate(output_dir: Path) -> dict[str, Any] | None:
    path = output_dir / CANDIDATE_FILE
    return read_json(path) if path.is_file() else None
