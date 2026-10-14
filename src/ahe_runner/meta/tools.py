"""Restricted filesystem, Git, validation, and research tools for meta-agents."""

from __future__ import annotations

import difflib
import ipaddress
import json
import re
import shutil
import socket
import subprocess
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urljoin, urlsplit

import yaml
from nexau.archs.main_sub.agent_state import AgentState

from ahe_runner.adapter import CandidateValidationRequest
from ahe_runner.change_manifest import load_change_manifest
from ahe_runner.config import load_config
from ahe_runner.debugger import validate_debugger_diagnosis
from ahe_runner.harness import load_harness_manifest, validate_harness
from ahe_runner.io import (
    atomic_write_json,
    read_json,
    sha256_file,
    sha256_json,
    tree_hashes,
)
from ahe_runner.loading import load_adapter
from ahe_runner.workspace import verify_checkpoint

_MAX_READ_BYTES = 160_000
_DEFAULT_READ_BYTES = 64_000
_EVOLVE_RESEARCH_BUDGET_BEFORE_COMMIT = 36


def submit_debugger_diagnosis(
    primary_failure_class: str,
    earliest_failure: str,
    evidence: list[dict[str, str]],
    evidence_coverage: dict[str, Any],
    paired_comparison: dict[str, Any],
    root_cause: str,
    contributing_factors: list[str],
    recommended_changes: list[dict[str, str]],
    recurrent_pattern_key: str,
    confidence: float,
    falsifier: str,
    agent_state: AgentState | None = None,
) -> dict[str, Any]:
    if str(_context_value(agent_state, "meta_role")) != "debugger":
        return _completion_error("submit_debugger_diagnosis is debugger-only")
    task = dict(_context_value(agent_state, "debugger_task"))
    expected_task_id = task.get("task_id")
    payload = {
        "schema_version": 3,
        "task_id": expected_task_id,
        "primary_failure_class": primary_failure_class,
        "earliest_failure": earliest_failure,
        "evidence": _canonical_citations(agent_state, evidence),
        "evidence_coverage": _canonical_coverage(agent_state, evidence_coverage),
        "paired_comparison": _canonical_paired(agent_state, paired_comparison),
        "root_cause": root_cause,
        "contributing_factors": contributing_factors,
        "recommended_changes": recommended_changes,
        "recurrent_pattern_key": recurrent_pattern_key or None,
        "confidence": confidence,
        "falsifier": falsifier,
    }
    try:
        diagnosis = validate_debugger_diagnosis(
            payload,
            expected_task_id=expected_task_id,
        )
    except Exception as exc:
        return _completion_error(
            f"invalid debugger diagnosis: {type(exc).__name__}: {exc}"
        )
    accesses = _debugger_accesses(agent_state)
    accessed = {str(item["artifact"]) for item in accesses}
    claimed = set(diagnosis.evidence_coverage.artifacts_read)
    primary = str(_context_value(agent_state, "debugger_primary_artifact"))
    if primary not in claimed:
        return _completion_error(
            "evidence_coverage.artifacts_read must include the primary task detail: "
            + primary
        )
    if not claimed <= accessed:
        return _completion_error(
            "evidence_coverage.artifacts_read includes files with no recorded "
            "read_file or search_file_content lines: "
            + ", ".join(sorted(claimed - accessed))
            + _accessed_hint(accesses)
        )
    citations = [item.citation for item in diagnosis.evidence] + [
        item.citation
        for item in (
            *diagnosis.paired_comparison.passing_evidence,
            *diagnosis.paired_comparison.failing_evidence,
        )
    ]
    uncovered = [
        _citation_label(citation)
        for citation in citations
        if not _citation_is_covered(citation, accesses)
    ]
    if uncovered:
        return _completion_error(
            "these citations are not covered by lines you read with read_file "
            "or saw as search_file_content hits (line ranges only; shell output "
            "does not count): " + ", ".join(uncovered) + _accessed_hint(accesses)
        )
    citation_artifacts = {citation.artifact for citation in citations}
    if not citation_artifacts <= claimed:
        return _completion_error(
            "evidence_coverage.artifacts_read omits cited artifacts: "
            + ", ".join(sorted(citation_artifacts - claimed))
        )
    trial_classes_value = _context_value(agent_state, "debugger_trial_classes")
    trial_classes = (
        {
            str(value)
            for value in trial_classes_value
            if str(value) in {"passing", "failing", "other"}
        }
        if isinstance(trial_classes_value, (list, tuple, set))
        else set()
    )
    paired_available = {"passing", "failing"} <= trial_classes
    paired = diagnosis.paired_comparison
    if paired_available and paired.status != "performed":
        return _completion_error(
            "a passing and failing trial are available; paired comparison is required"
        )
    if not paired_available and paired.status != "unavailable":
        return _completion_error(
            "paired comparison cannot be performed without verified passing and "
            "failing trials"
        )
    artifact_classes_value = _context_value(
        agent_state,
        "debugger_artifact_classes",
    )
    artifact_classes = (
        {str(key): str(value) for key, value in artifact_classes_value.items()}
        if isinstance(artifact_classes_value, Mapping)
        else {}
    )
    for expected_class, items in (
        ("passing", paired.passing_evidence),
        ("failing", paired.failing_evidence),
    ):
        mismatched = sorted(
            {
                item.citation.artifact
                for item in items
                if artifact_classes.get(item.citation.artifact) != expected_class
            }
        )
        if mismatched:
            return _completion_error(
                f"{expected_class} paired evidence does not cite verified "
                f"{expected_class} trial artifacts: " + ", ".join(mismatched)
            )
    output_dir = Path(str(_context_value(agent_state, "meta_output_dir"))).resolve()
    path = output_dir / "submitted_diagnosis.json"
    atomic_write_json(path, diagnosis.model_dump(mode="json"))
    _storage_set(agent_state, "debugger_diagnosis", diagnosis.model_dump(mode="json"))
    _storage_set(agent_state, "task_complete", True)
    return {
        "ok": True,
        "task_key": str(_context_value(agent_state, "debugger_task_key")),
        "diagnosis_sha256": sha256_file(path),
    }


def git_status(agent_state: AgentState | None = None) -> str:
    workspace = _workspace(agent_state)
    return _json({"ok": True, "status": _git(workspace, "status", "--short")})


def git_diff(
    staged: bool = False,
    agent_state: AgentState | None = None,
) -> str:
    workspace = _workspace(agent_state)
    args = ["diff"]
    if staged:
        args.append("--cached")
    return _json({"ok": True, "diff": _git(workspace, *args)})


def git_commit(
    message: str,
    agent_state: AgentState | None = None,
) -> str:
    workspace = _workspace(agent_state)
    if not message.strip():
        return _json({"ok": False, "error": "commit message cannot be empty"})
    _git(workspace, "add", "-A")
    status = _git(workspace, "status", "--short")
    if not status.strip():
        return _json({"ok": False, "error": "nothing to commit"})
    _git(workspace, "commit", "-m", message.strip())
    head = _git(workspace, "rev-parse", "HEAD").strip()
    progress, key = _progress(agent_state)
    progress["research_calls_since_commit"] = 0
    progress["mutations_since_commit"] = 0
    progress["commits_created"] = int(progress.get("commits_created", 0)) + 1
    _store_progress(agent_state, key, progress)
    return _json({"ok": True, "head": head, "message": message.strip()})


def checkpoint_log(agent_state: AgentState | None = None) -> str:
    blocked = _research_gate(agent_state)
    if blocked:
        return blocked
    root, maximum = _checkpoint_access(agent_state)
    checkpoints: list[dict[str, Any]] = []
    paths = [
        path
        for path in root.glob("C[0-9]*")
        if re.fullmatch(r"C(?:0|[1-9][0-9]*)", path.name)
    ]
    for path in sorted(paths, key=lambda item: _checkpoint_index(item.name)):
        index = _checkpoint_index(path.name)
        if index > maximum or not path.is_dir():
            continue
        manifest = verify_checkpoint(path)
        source = dict(manifest.get("source") or {})
        change_manifest = source.get("change_manifest")
        checkpoints.append(
            {
                "checkpoint": path.name,
                "parent_checkpoint": manifest.get("parent_checkpoint"),
                "harness_sha256": manifest.get("harness_sha256"),
                "workspace_commit": manifest.get("workspace_commit"),
                "changed_paths": source.get("changed_paths", []),
                "change_manifest": change_manifest,
            }
        )
    return _json(
        {
            "ok": True,
            "current_parent_checkpoint": f"C{maximum}",
            "checkpoints": checkpoints,
        }
    )


def checkpoint_diff(
    checkpoint: str,
    relative_path: str = "",
    max_bytes: int = _DEFAULT_READ_BYTES,
    agent_state: AgentState | None = None,
) -> str:
    blocked = _research_gate(agent_state)
    if blocked:
        return blocked
    if max_bytes < 1 or max_bytes > _MAX_READ_BYTES:
        return _json(
            {
                "ok": False,
                "error": f"max_bytes must be between 1 and {_MAX_READ_BYTES}",
            }
        )
    historical = _checkpoint_harness(agent_state, checkpoint)
    workspace = _workspace(agent_state)
    if not relative_path:
        historical_hashes = tree_hashes(historical)
        current_hashes = tree_hashes(workspace)
        historical_paths = set(historical_hashes)
        current_paths = set(current_hashes)
        return _json(
            {
                "ok": True,
                "checkpoint": checkpoint,
                "comparison": "historical_checkpoint_to_current_workspace",
                "added_in_workspace": sorted(current_paths - historical_paths),
                "removed_from_workspace": sorted(historical_paths - current_paths),
                "modified": sorted(
                    path
                    for path in historical_paths & current_paths
                    if historical_hashes[path] != current_hashes[path]
                ),
            }
        )
    relative = _historical_component_path(relative_path)
    source = historical / relative
    target = _workspace_component_path(workspace, relative)
    if source.is_dir() or target.is_dir():
        return _json(
            {
                "ok": False,
                "error": "checkpoint_diff accepts a file path, not a directory",
            }
        )
    source_bytes = source.read_bytes() if source.is_file() else b""
    target_bytes = target.read_bytes() if target.is_file() else b""
    binary = b"\x00" in source_bytes or b"\x00" in target_bytes
    if binary:
        return _json(
            {
                "ok": True,
                "checkpoint": checkpoint,
                "path": relative,
                "binary": True,
                "historical_exists": source.is_file(),
                "workspace_exists": target.is_file(),
                "historical_sha256": sha256_file(source) if source.is_file() else None,
                "workspace_sha256": sha256_file(target) if target.is_file() else None,
            }
        )
    diff = "".join(
        difflib.unified_diff(
            source_bytes.decode("utf-8", errors="replace").splitlines(keepends=True),
            target_bytes.decode("utf-8", errors="replace").splitlines(keepends=True),
            fromfile=f"{checkpoint}/{relative}",
            tofile=f"workspace/{relative}",
        )
    )
    encoded = diff.encode("utf-8")
    truncated = len(encoded) > max_bytes
    content = encoded[:max_bytes].decode("utf-8", errors="replace")
    return _json(
        {
            "ok": True,
            "checkpoint": checkpoint,
            "path": relative,
            "binary": False,
            "historical_exists": source.is_file(),
            "workspace_exists": target.is_file(),
            "diff": content,
            "bytes_returned": len(content.encode("utf-8")),
            "truncated": truncated,
            "full_diff_bytes": len(encoded),
        }
    )


def restore_checkpoint_paths(
    checkpoint: str,
    paths: list[str],
    rationale: str,
    agent_state: AgentState | None = None,
) -> str:
    historical = _checkpoint_harness(agent_state, checkpoint)
    workspace = _workspace(agent_state)
    normalized = [_historical_component_path(path) for path in paths]
    if not normalized:
        return _json({"ok": False, "error": "paths must not be empty"})
    if len(normalized) != len(set(normalized)):
        return _json({"ok": False, "error": "paths must be unique"})
    reason = rationale.strip()
    if not reason:
        return _json({"ok": False, "error": "rationale must not be empty"})
    candidates: list[tuple[str, Path, Path]] = []
    for relative in normalized:
        source = historical / relative
        target = _workspace_component_path(workspace, relative)
        if source.is_dir() or target.is_dir():
            return _json(
                {
                    "ok": False,
                    "error": f"restore path must name a file: {relative}",
                }
            )
        candidates.append((relative, source, target))
    actions: list[dict[str, Any]] = []
    changed = False
    for relative, source, target in candidates:
        before = sha256_file(target) if target.is_file() else None
        if source.is_file():
            source_hash = sha256_file(source)
            if before == source_hash:
                action = "unchanged"
                after = before
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
                action = "restored"
                after = sha256_file(target)
                changed = True
        elif target.is_file():
            target.unlink()
            action = "deleted"
            after = None
            changed = True
        else:
            action = "unchanged_absent"
            after = None
        actions.append(
            {
                "path": relative,
                "action": action,
                "before_sha256": before,
                "after_sha256": after,
            }
        )
    if changed:
        _record_mutation(agent_state)
    event = {
        "checkpoint": checkpoint,
        "paths": normalized,
        "rationale": reason,
        "actions": actions,
    }
    _append_restore_event(agent_state, event)
    return _json({"ok": True, "changed": changed, **event})


def validate_candidate(agent_state: AgentState | None = None) -> str:
    workspace = _workspace(agent_state)
    manifest_path = Path(
        str(_context_value(agent_state, "harness_manifest_path"))
    ).resolve()
    project_root = Path(str(_context_value(agent_state, "project_root"))).resolve()
    config_path = Path(str(_context_value(agent_state, "config_path"))).resolve()
    parent_harness_dir = Path(
        str(_context_value(agent_state, "parent_harness_dir"))
    ).resolve()
    output_dir = Path(str(_context_value(agent_state, "meta_output_dir"))).resolve()
    training_ids = tuple(
        str(item) for item in (_context_value(agent_state, "training_task_ids") or [])
    )
    parent_hashes = {
        str(key): str(value)
        for key, value in dict(
            _context_value(agent_state, "parent_runtime_owned_hashes") or {}
        ).items()
    }
    try:
        report = validate_harness(
            workspace,
            load_harness_manifest(manifest_path),
            project_root=project_root,
            training_task_ids=training_ids,
            parent_runtime_owned_hashes=parent_hashes,
            run_nexau_smoke=True,
        )
        adapter = load_adapter(load_config(config_path))
        adapter_result = adapter.validate_candidate(
            CandidateValidationRequest(
                checkpoint=str(_context_value(agent_state, "checkpoint")),
                parent_checkpoint=str(_context_value(agent_state, "parent_checkpoint")),
                harness_dir=workspace,
                parent_harness_dir=parent_harness_dir,
                training_task_ids=training_ids,
            )
        )
        if not adapter_result.valid:
            raise ValueError(
                "adapter candidate validation failed: "
                + "; ".join(adapter_result.errors)
            )
    except Exception as exc:
        payload = {
            "ok": False,
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
        atomic_write_json(output_dir / "candidate_validation.json", payload)
        _storage_set(agent_state, "candidate_validation", payload)
        return _json(payload)
    payload = {
        "ok": True,
        "head": _git(workspace, "rev-parse", "HEAD").strip(),
        "tree_sha256": sha256_json(tree_hashes(workspace)),
        "report": report,
        "adapter": adapter_result.model_dump(mode="json"),
    }
    atomic_write_json(output_dir / "candidate_validation.json", payload)
    _storage_set(agent_state, "candidate_validation", payload)
    return _json(payload)


def web_search(
    query: str,
    max_results: int = 8,
    agent_state: AgentState | None = None,
) -> str:
    blocked = _research_gate(agent_state)
    if blocked:
        return blocked
    leak = _leakage_violation(agent_state, query)
    if leak:
        return _json({"ok": False, "error": leak})
    max_results = min(max(max_results, 1), 12)
    try:
        from ddgs import DDGS

        results = list(DDGS().text(query, max_results=max_results))
    except Exception as exc:
        return _json(
            {
                "ok": False,
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
        )
    return _json({"ok": True, "query": query, "results": results})


def web_read(
    url: str,
    agent_state: AgentState | None = None,
) -> str:
    blocked = _research_gate(agent_state)
    if blocked:
        return blocked
    leak = _leakage_violation(agent_state, url)
    if leak:
        return _json({"ok": False, "error": leak})
    try:
        import httpx
        from bs4 import BeautifulSoup

        current_url = url
        with httpx.Client(
            timeout=20,
            follow_redirects=False,
            headers={"User-Agent": "ahe-runner/0.1 research"},
        ) as client:
            for _ in range(6):
                _require_public_url(current_url)
                response = client.get(current_url)
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        raise ValueError("redirect response omitted Location")
                    current_url = urljoin(current_url, location)
                    continue
                response.raise_for_status()
                break
            else:
                raise ValueError("too many redirects")
    except Exception as exc:
        return _json(
            {
                "ok": False,
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
        )
    soup = BeautifulSoup(response.text, "html.parser")
    for node in soup(["script", "style", "nav", "footer", "noscript"]):
        node.decompose()
    text = "\n".join(
        line.strip() for line in soup.get_text("\n").splitlines() if line.strip()
    )
    return _json(
        {
            "ok": True,
            "url": str(response.url),
            "status_code": response.status_code,
            "content": text[:24_000],
            "truncated": len(text) > 24_000,
        }
    )


def complete_task(
    final_summary: str,
    agent_state: AgentState | None = None,
) -> dict[str, Any]:
    if not final_summary.strip():
        return _completion_error("final_summary cannot be empty")
    role = str(_context_value(agent_state, "meta_role"))
    if role in {"source_explorer", "web_explorer"}:
        target = Path(
            str(_context_value(agent_state, "required_deliverable"))
        ).resolve()
        problems = _validate_skill(target, role)
        if problems:
            return _completion_error(errors=problems)
    elif role == "evolve":
        workspace = _workspace(agent_state)
        status = _git(workspace, "status", "--short")
        validation = _storage_get(agent_state, "candidate_validation")
        manifest_path = workspace / "change_manifest.json"
        problems: list[str] = []
        if status.strip():
            problems.append("workspace has uncommitted changes")
        if not manifest_path.is_file():
            problems.append("change_manifest.json is missing")
        else:
            try:
                root_commit = _git(
                    workspace,
                    "rev-list",
                    "--max-parents=0",
                    "HEAD",
                ).strip()
                changed_paths = [
                    line
                    for line in _git(
                        workspace,
                        "diff",
                        "--name-only",
                        f"{root_commit}..HEAD",
                    ).splitlines()
                    if line
                ]
                load_change_manifest(
                    manifest_path,
                    checkpoint=str(_context_value(agent_state, "checkpoint")),
                    parent_checkpoint=str(
                        _context_value(agent_state, "parent_checkpoint")
                    ),
                    changed_paths=changed_paths,
                )
            except Exception as exc:
                problems.append(f"change_manifest.json is invalid: {exc}")
        if not isinstance(validation, dict) or validation.get("ok") is not True:
            problems.append("validate_candidate must pass after the final commit")
        elif (
            validation.get("head")
            != _git(
                workspace,
                "rev-parse",
                "HEAD",
            ).strip()
        ):
            problems.append("validate_candidate must be rerun after the final commit")
        if problems:
            return _completion_error(errors=problems)
    else:
        return _completion_error(f"unsupported meta role: {role}")
    _storage_set(agent_state, "task_complete", True)
    _storage_set(agent_state, "final_summary", final_summary.strip())
    return {"ok": True, "summary": final_summary.strip()}


def _completion_error(
    error: str | None = None,
    *,
    errors: list[str] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"status": "error", "ok": False}
    if error is not None:
        payload["error"] = error
    if errors is not None:
        payload["errors"] = errors
    return payload


def _validate_skill(path: Path, role: str) -> list[str]:
    problems: list[str] = []
    if not path.is_file():
        return [f"required deliverable does not exist: {path}"]
    text = path.read_text(encoding="utf-8")
    if len(text) < 3_000:
        problems.append("SKILL.md is too short to be a useful operational guide")
    if not text.startswith("---\n"):
        problems.append("SKILL.md must start with YAML frontmatter")
    else:
        end = text.find("\n---", 4)
        if end < 0:
            problems.append("SKILL.md frontmatter is not closed")
        else:
            try:
                frontmatter = yaml.safe_load(text[4:end]) or {}
            except yaml.YAMLError as exc:
                problems.append(f"SKILL.md frontmatter is invalid: {exc}")
            else:
                for field in ("name", "description"):
                    if not frontmatter.get(field):
                        problems.append(f"SKILL.md frontmatter requires {field}")
    lowered = text.casefold()
    required = (
        ("architecture", "configuration", "tools", "failure")
        if role == "source_explorer"
        else ("sources", "patterns", "evidence", "recommendations")
    )
    for term in required:
        if term not in lowered:
            problems.append(f"SKILL.md must cover {term}")
    return problems


def _workspace(agent_state: AgentState | None) -> Path:
    return Path(str(_context_value(agent_state, "workspace_dir"))).resolve()


def _checkpoint_access(
    agent_state: AgentState | None,
) -> tuple[Path, int]:
    root = Path(str(_context_value(agent_state, "checkpoint_root"))).resolve()
    maximum = _checkpoint_index(str(_context_value(agent_state, "parent_checkpoint")))
    return root, maximum


def _checkpoint_harness(
    agent_state: AgentState | None,
    checkpoint: str,
) -> Path:
    root, maximum = _checkpoint_access(agent_state)
    index = _checkpoint_index(checkpoint)
    if index > maximum:
        raise ValueError(
            f"checkpoint {checkpoint} is newer than the current parent C{maximum}"
        )
    checkpoint_dir = (root / checkpoint).resolve()
    try:
        checkpoint_dir.relative_to(root)
    except ValueError as exc:
        raise ValueError("checkpoint path escapes the checkpoint root") from exc
    manifest = verify_checkpoint(checkpoint_dir)
    if manifest.get("checkpoint") != checkpoint:
        raise ValueError(f"checkpoint manifest identity mismatch for {checkpoint_dir}")
    return checkpoint_dir / "harness"


def _checkpoint_index(value: str) -> int:
    if not re.fullmatch(r"C(?:0|[1-9][0-9]*)", value):
        raise ValueError(f"invalid checkpoint label: {value!r}")
    return int(value[1:])


def _historical_component_path(value: str) -> str:
    normalized = value.strip()
    if not normalized or "\\" in normalized:
        raise ValueError("path must be a non-empty POSIX relative file path")
    path = PurePosixPath(normalized)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("path must stay inside the harness")
    if ".git" in path.parts or path.as_posix() == "change_manifest.json":
        raise ValueError("path names protected bookkeeping state")
    return path.as_posix()


def _workspace_component_path(workspace: Path, relative: str) -> Path:
    target = workspace / relative
    current = target
    while current != workspace:
        if current.is_symlink():
            raise ValueError(f"workspace path traverses a symlink: {relative}")
        if current != target and current.exists() and not current.is_dir():
            raise ValueError(f"workspace parent path is not a directory: {relative}")
        current = current.parent
    resolved = target.resolve(strict=False)
    try:
        resolved.relative_to(workspace.resolve())
    except ValueError as exc:
        raise ValueError("workspace path escapes the candidate root") from exc
    return target


def _append_restore_event(
    agent_state: AgentState | None,
    event: dict[str, Any],
) -> None:
    output_dir = Path(str(_context_value(agent_state, "meta_output_dir"))).resolve()
    path = output_dir / "historical_restores.json"
    payload = read_json(path) if path.is_file() else {"schema_version": 1, "events": []}
    events = payload.get("events")
    if not isinstance(events, list):
        raise TypeError("historical restore log is invalid")
    events.append(event)
    payload["events"] = events
    atomic_write_json(path, payload)


def _git(workspace: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(workspace), *args],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stdout.strip() or f"git {' '.join(args)} failed")
    return completed.stdout


def _json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True)


def _progress(
    agent_state: AgentState | None,
) -> tuple[dict[str, Any], str]:
    key = _storage_key(agent_state, "progress")
    value = _storage_get(
        agent_state,
        "progress",
        {
            "research_calls_since_commit": 0,
            "mutations_since_commit": 0,
            "commits_created": 0,
        },
    )
    if not isinstance(value, dict):
        raise TypeError("ahe_progress storage is invalid")
    return dict(value), key


def _research_gate(agent_state: AgentState | None) -> str | None:
    role = str(_context_value(agent_state, "meta_role"))
    progress, key = _progress(agent_state)
    calls = int(progress.get("research_calls_since_commit", 0))
    if role in {"source_explorer", "web_explorer"}:
        writes = int(progress.get("deliverable_writes", 0))
        if writes < 1:
            return _json(
                {
                    "ok": False,
                    "error": (
                        "durable-deliverable gate: create a substantial "
                        "SKILL.md scaffold with write_file before research"
                    ),
                }
            )
        if calls >= 24:
            return _json(
                {
                    "ok": False,
                    "error": (
                        "durable-deliverable gate: update the required SKILL.md "
                        "with newly learned evidence before further research"
                    ),
                }
            )
    progress["research_calls_since_commit"] = calls + 1
    _store_progress(agent_state, key, progress)
    if (
        role == "evolve"
        and int(progress.get("research_calls_since_commit", 0))
        > _EVOLVE_RESEARCH_BUDGET_BEFORE_COMMIT
        and int(progress.get("mutations_since_commit", 0)) > 0
    ):
        return _json(
            {
                "ok": False,
                "error": (
                    "durable-progress gate reached: inspect git_diff, commit the "
                    "current coherent improvement, then continue research"
                ),
            }
        )
    return None


def _record_mutation(agent_state: AgentState | None) -> None:
    progress, key = _progress(agent_state)
    progress["mutations_since_commit"] = (
        int(progress.get("mutations_since_commit", 0)) + 1
    )
    _store_progress(agent_state, key, progress)


def _record_debugger_access(
    agent_state: AgentState | None,
    *,
    artifact: str,
    mode: str,
    start: int,
    end: int,
) -> None:
    if str(_context_value(agent_state, "meta_role")) != "debugger":
        return
    if end <= start and mode == "bytes":
        return
    if end < start and mode == "lines":
        return
    accesses = _debugger_accesses(agent_state)
    record = {
        "artifact": artifact,
        "mode": mode,
        "start": start,
        "end": end,
    }
    if record not in accesses:
        accesses.append(record)
    _storage_set(
        agent_state,
        "debugger_artifact_accesses",
        sorted(
            accesses,
            key=lambda item: (
                str(item["artifact"]),
                str(item["mode"]),
                int(item["start"]),
                int(item["end"]),
            ),
        ),
    )


def _debugger_accesses(
    agent_state: AgentState | None,
) -> list[dict[str, str | int]]:
    value = _storage_get(agent_state, "debugger_artifact_accesses", [])
    if not isinstance(value, list):
        return []
    accesses: list[dict[str, str | int]] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        artifact = str(item.get("artifact", "")).strip()
        mode = str(item.get("mode", ""))
        start = item.get("start")
        end = item.get("end")
        if (
            not artifact
            or mode not in {"lines", "bytes"}
            or isinstance(start, bool)
            or not isinstance(start, int)
            or isinstance(end, bool)
            or not isinstance(end, int)
        ):
            continue
        accesses.append(
            {
                "artifact": artifact,
                "mode": mode,
                "start": start,
                "end": end,
            }
        )
    return accesses


def _citation_is_covered(
    citation: Any,
    accesses: list[dict[str, str | int]],
) -> bool:
    """Line citations are covered by the union of recorded line reads."""

    if citation.line_start is None:
        # AHE-016: reads are recorded as line ranges only.
        return False
    intervals = sorted(
        (int(access["start"]), int(access["end"]))
        for access in accesses
        if access["artifact"] == citation.artifact and access["mode"] == "lines"
    )
    needed = int(citation.line_start)
    end = int(citation.line_end)
    for start, stop in intervals:
        if start > needed:
            break
        needed = max(needed, stop + 1)
        if needed > end:
            return True
    return needed > end


def _accessed_hint(accesses: list[dict[str, str | int]]) -> str:
    ranges: dict[str, list[str]] = {}
    for access in accesses:
        if access["mode"] != "lines":
            continue
        ranges.setdefault(str(access["artifact"]), []).append(
            f"{access['start']}-{access['end']}"
        )
    if not ranges:
        return ". No evidence lines are recorded yet."
    shown = "; ".join(
        f"{artifact} lines {', '.join(values[:12])}"
        + (" ..." if len(values) > 12 else "")
        for artifact, values in sorted(ranges.items())
    )
    return f". Recorded reads: {shown}"


def _canonical_artifact(agent_state: AgentState | None, value: Any) -> Any:
    if not isinstance(value, str):
        return value
    from .guarded_tools import canonical_artifact

    return canonical_artifact(agent_state, value)


def _canonical_citations(agent_state: AgentState | None, items: Any) -> Any:
    if not isinstance(items, list):
        return items
    result: list[Any] = []
    for item in items:
        if isinstance(item, Mapping) and isinstance(item.get("citation"), Mapping):
            citation = dict(item["citation"])
            citation["artifact"] = _canonical_artifact(
                agent_state, citation.get("artifact")
            )
            result.append({**item, "citation": citation})
        else:
            result.append(item)
    return result


def _canonical_coverage(agent_state: AgentState | None, coverage: Any) -> Any:
    if not isinstance(coverage, Mapping):
        return coverage
    artifacts = coverage.get("artifacts_read")
    if not isinstance(artifacts, list):
        return coverage
    return {
        **coverage,
        "artifacts_read": [
            _canonical_artifact(agent_state, item) for item in artifacts
        ],
    }


def _canonical_paired(agent_state: AgentState | None, paired: Any) -> Any:
    if not isinstance(paired, Mapping):
        return paired
    result = dict(paired)
    for key in ("passing_evidence", "failing_evidence"):
        if key in result:
            result[key] = _canonical_citations(agent_state, result[key])
    return result


def _citation_label(citation: Any) -> str:
    if citation.line_start is not None:
        return f"{citation.artifact}#lines={citation.line_start}-{citation.line_end}"
    return f"{citation.artifact}#bytes={citation.start_byte}-{citation.end_byte}"


def _deliverable_write_error(
    agent_state: AgentState | None,
    path: Path,
    content: str,
) -> str | None:
    role = str(_context_value(agent_state, "meta_role"))
    if role not in {"source_explorer", "web_explorer"}:
        return None
    target = Path(str(_context_value(agent_state, "required_deliverable"))).resolve()
    if path != target:
        return None
    progress, _ = _progress(agent_state)
    writes = int(progress.get("deliverable_writes", 0))
    previous = int(progress.get("deliverable_characters", 0))
    required = 1_500 if writes < 1 else previous
    if previous < 6_000 and writes >= 1:
        required = min(6_000, previous + 750)
    if len(content) < required:
        return _json(
            {
                "ok": False,
                "error": (
                    f"durable-deliverable gate: SKILL.md must contain at least "
                    f"{required} characters at this checkpoint"
                ),
            }
        )
    return None


def _record_deliverable_write(
    agent_state: AgentState | None,
    path: Path,
    content: str,
) -> None:
    role = str(_context_value(agent_state, "meta_role"))
    if role not in {"source_explorer", "web_explorer"}:
        return
    target = Path(str(_context_value(agent_state, "required_deliverable"))).resolve()
    if path != target:
        return
    progress, key = _progress(agent_state)
    progress["deliverable_writes"] = int(progress.get("deliverable_writes", 0)) + 1
    progress["deliverable_characters"] = len(content)
    progress["research_calls_since_commit"] = 0
    _store_progress(agent_state, key, progress)


def _leakage_violation(
    agent_state: AgentState | None,
    text: str,
) -> str | None:
    lowered = text.casefold()
    for forbidden in _context_value(agent_state, "web_leakage_terms") or []:
        value = str(forbidden).strip()
        if value and value.casefold() in lowered:
            return (
                "query contains a protected benchmark identifier or memorization "
                f"term: {value!r}"
            )
    return None


def _context_value(agent_state: AgentState | None, key: str) -> Any:
    if agent_state is None:
        raise RuntimeError("NexAU did not provide agent state")
    value = agent_state.get_context_value(key)
    if value is None:
        raise RuntimeError(f"required meta-agent context is missing: {key}")
    return value


def _storage_key(agent_state: AgentState | None, name: str) -> str:
    if agent_state is None:
        raise RuntimeError("NexAU did not provide agent state")
    agent_id = getattr(agent_state, "agent_id", agent_state.agent_name)
    return f"ahe-runner:{agent_id}:{name}"


def _storage_get(
    agent_state: AgentState | None,
    name: str,
    default: Any = None,
) -> Any:
    if agent_state is None:
        raise RuntimeError("NexAU did not provide agent state")
    return agent_state.global_storage.get(_storage_key(agent_state, name), default)


def _storage_set(
    agent_state: AgentState | None,
    name: str,
    value: Any,
) -> None:
    if agent_state is None:
        raise RuntimeError("NexAU did not provide agent state")
    agent_state.global_storage.set(_storage_key(agent_state, name), value)


def _store_progress(
    agent_state: AgentState | None,
    key: str,
    value: dict[str, Any],
) -> None:
    if agent_state is None:
        raise RuntimeError("NexAU did not provide agent state")
    agent_state.global_storage.set(key, value)


def _require_public_url(url: str) -> None:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("only HTTP(S) URLs are allowed")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("URLs containing credentials are prohibited")
    if not parsed.hostname:
        raise ValueError("URL hostname is required")
    addresses = {
        item[4][0]
        for item in socket.getaddrinfo(
            parsed.hostname,
            parsed.port or (443 if parsed.scheme == "https" else 80),
            type=socket.SOCK_STREAM,
        )
    }
    if not addresses:
        raise ValueError("URL hostname did not resolve")
    for value in addresses:
        address = ipaddress.ip_address(value)
        if not address.is_global:
            raise ValueError(f"URL resolves to non-public address {address.compressed}")
