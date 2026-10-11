"""Restricted filesystem, Git, validation, and research tools for meta-agents."""

from __future__ import annotations

import ipaddress
import json
import re
import socket
import subprocess
from pathlib import Path
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

_MAX_READ_BYTES = 160_000
_DEFAULT_READ_BYTES = 64_000
_MAX_SEARCH_MATCHES = 300
_MAX_LIST_ENTRIES = 5_000
_EVOLVE_RESEARCH_BUDGET_BEFORE_COMMIT = 36


def list_files(
    root: str,
    relative_path: str = ".",
    recursive: bool = False,
    agent_state: AgentState | None = None,
) -> str:
    blocked = _research_gate(agent_state)
    if blocked:
        return blocked
    base = _resolve_virtual(agent_state, root, relative_path, write=False)
    if not base.exists():
        return _json({"ok": False, "error": f"path does not exist: {relative_path}"})
    glob_path = Path(file_glob)
    if glob_path.is_absolute() or ".." in glob_path.parts:
        return _json({"ok": False, "error": "file_glob must stay within the root"})
    if base.is_file():
        return _json({"ok": True, "entries": [_entry(base, base.parent)]})
    iterator = base.rglob("*") if recursive else base.iterdir()
    entries: list[dict[str, Any]] = []
    for path in sorted(iterator, key=lambda item: item.as_posix()):
        if ".git" in path.parts:
            continue
        entries.append(_entry(path, base))
        if len(entries) >= _MAX_LIST_ENTRIES:
            return _json(
                {
                    "ok": True,
                    "entries": entries,
                    "truncated": True,
                    "limit": _MAX_LIST_ENTRIES,
                }
            )
    return _json({"ok": True, "entries": entries, "truncated": False})


def read_file(
    root: str,
    relative_path: str,
    start_line: int = 1,
    end_line: int = 400,
    start_byte: int | None = None,
    end_byte: int | None = None,
    max_bytes: int = _DEFAULT_READ_BYTES,
    agent_state: AgentState | None = None,
) -> str:
    blocked = _research_gate(agent_state)
    if blocked:
        return blocked
    path = _resolve_virtual(agent_state, root, relative_path, write=False)
    if not path.is_file():
        return _json({"ok": False, "error": f"not a file: {relative_path}"})
    if max_bytes < 1 or max_bytes > _MAX_READ_BYTES:
        return _json(
            {
                "ok": False,
                "error": f"max_bytes must be between 1 and {_MAX_READ_BYTES}",
            }
        )
    file_size = path.stat().st_size
    if start_byte is not None or end_byte is not None:
        if (
            start_byte is None
            or end_byte is None
            or start_byte < 0
            or end_byte <= start_byte
        ):
            return _json(
                {
                    "ok": False,
                    "error": ("byte reads require 0 <= start_byte < end_byte"),
                }
            )
        requested = end_byte - start_byte
        if requested > max_bytes:
            return _json(
                {
                    "ok": False,
                    "error": "requested byte range exceeds max_bytes",
                    "requested_bytes": requested,
                    "max_bytes": max_bytes,
                }
            )
        with path.open("rb") as handle:
            handle.seek(start_byte)
            content_bytes = handle.read(min(requested, max(file_size - start_byte, 0)))
        _record_debugger_artifact(agent_state, root, relative_path)
        return _json(
            {
                "ok": True,
                "mode": "bytes",
                "path": relative_path,
                "start_byte": start_byte,
                "end_byte": start_byte + len(content_bytes),
                "requested_end_byte": end_byte,
                "file_size": file_size,
                "bytes_returned": len(content_bytes),
                "has_more": start_byte + len(content_bytes) < file_size,
                "content": content_bytes.decode("utf-8", errors="replace"),
                "sha256": sha256_file(path),
            }
        )
    if start_line < 1 or end_line < start_line:
        return _json({"ok": False, "error": "invalid line range"})
    selected: list[str] = []
    bytes_returned = 0
    last_line = start_line - 1
    lines_seen = 0
    total_lines: int | None = None
    content_truncated = False
    has_more_after_range = False
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line_number, line in enumerate(handle, start=1):
            lines_seen = line_number
            if line_number < start_line:
                continue
            if line_number > end_line:
                has_more_after_range = True
                break
            encoded = line.encode("utf-8")
            remaining = max_bytes - bytes_returned
            if len(encoded) > remaining:
                if remaining:
                    selected.append(
                        encoded[:remaining].decode("utf-8", errors="replace")
                    )
                    bytes_returned += remaining
                last_line = line_number
                content_truncated = True
                has_more_after_range = True
                break
            selected.append(line)
            bytes_returned += len(encoded)
            last_line = line_number
        else:
            total_lines = lines_seen
    _record_debugger_artifact(agent_state, root, relative_path)
    return _json(
        {
            "ok": True,
            "mode": "lines",
            "path": relative_path,
            "start_line": start_line,
            "end_line": last_line,
            "requested_end_line": end_line,
            "total_lines": total_lines,
            "file_size": file_size,
            "bytes_returned": bytes_returned,
            "content_truncated": content_truncated,
            "has_more_after_range": has_more_after_range,
            "content": "".join(selected),
            "sha256": sha256_file(path),
        }
    )


def search_text(
    root: str,
    query: str,
    relative_path: str = ".",
    file_glob: str = "**/*",
    case_sensitive: bool = False,
    regex: bool = False,
    agent_state: AgentState | None = None,
) -> str:
    blocked = _research_gate(agent_state)
    if blocked:
        return blocked
    base = _resolve_virtual(agent_state, root, relative_path, write=False)
    if not base.exists():
        return _json({"ok": False, "error": f"path does not exist: {relative_path}"})
    flags = 0 if case_sensitive else re.IGNORECASE
    try:
        pattern = re.compile(query if regex else re.escape(query), flags)
    except re.error as exc:
        return _json({"ok": False, "error": f"invalid regular expression: {exc}"})
    candidates = [base] if base.is_file() else sorted(base.glob(file_glob))
    root_base = _resolve_virtual(agent_state, root, ".", write=False)
    matches: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    searched_files = 0
    for path in candidates:
        if not path.is_file() or ".git" in path.parts:
            continue
        root_relative = path.relative_to(root_base).as_posix()
        if path.is_symlink():
            skipped.append({"path": root_relative, "reason": "symlink"})
            continue
        try:
            handle = path.open("r", encoding="utf-8")
        except OSError as exc:
            skipped.append(
                {
                    "path": root_relative,
                    "reason": f"{type(exc).__name__}: {exc}",
                }
            )
            continue
        relative = path.relative_to(base if base.is_dir() else base.parent).as_posix()
        try:
            searched_files += 1
            for line_number, line in enumerate(handle, start=1):
                if pattern.search(line):
                    _record_debugger_artifact(agent_state, root, root_relative)
                    matches.append(
                        {
                            "path": relative,
                            "line": line_number,
                            "text": line[:2_000].rstrip("\n"),
                            "text_truncated": len(line) > 2_000,
                        }
                    )
                    if len(matches) >= _MAX_SEARCH_MATCHES:
                        return _json(
                            {
                                "ok": True,
                                "matches": matches,
                                "truncated": True,
                                "match_limit": _MAX_SEARCH_MATCHES,
                                "searched_files": searched_files,
                                "skipped": skipped[:20],
                                "skipped_total": len(skipped),
                                "skipped_truncated": len(skipped) > 20,
                            }
                        )
        except UnicodeDecodeError:
            skipped.append({"path": relative, "reason": "non-UTF-8 content"})
        except OSError as exc:
            skipped.append(
                {
                    "path": relative,
                    "reason": f"{type(exc).__name__}: {exc}",
                }
            )
        finally:
            handle.close()
    return _json(
        {
            "ok": True,
            "matches": matches,
            "truncated": False,
            "match_limit": _MAX_SEARCH_MATCHES,
            "searched_files": searched_files,
            "skipped": skipped[:20],
            "skipped_total": len(skipped),
            "skipped_truncated": len(skipped) > 20,
        }
    )


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
        "schema_version": 2,
        "task_id": expected_task_id,
        "primary_failure_class": primary_failure_class,
        "earliest_failure": earliest_failure,
        "evidence": evidence,
        "evidence_coverage": evidence_coverage,
        "paired_comparison": paired_comparison,
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
    accessed = set(_storage_get(agent_state, "debugger_artifacts_read", []))
    claimed = set(diagnosis.evidence_coverage.artifacts_read)
    primary = str(_context_value(agent_state, "debugger_primary_artifact"))
    if primary not in claimed:
        return _completion_error(
            "evidence_coverage.artifacts_read must include the primary task detail: "
            + primary
        )
    if not claimed <= accessed:
        return _completion_error(
            "evidence_coverage.artifacts_read includes files not read through "
            "the bounded tools: " + ", ".join(sorted(claimed - accessed))
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


def write_file(
    root: str,
    relative_path: str,
    content: str,
    agent_state: AgentState | None = None,
) -> str:
    path = _resolve_virtual(agent_state, root, relative_path, write=True)
    progress_error = _deliverable_write_error(agent_state, path, content)
    if progress_error is not None:
        return progress_error
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    _record_deliverable_write(agent_state, path, content)
    _record_mutation(agent_state)
    return _json(
        {
            "ok": True,
            "path": relative_path,
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
    )


def replace_text(
    root: str,
    relative_path: str,
    old_text: str,
    new_text: str,
    expected_occurrences: int = 1,
    agent_state: AgentState | None = None,
) -> str:
    path = _resolve_virtual(agent_state, root, relative_path, write=True)
    if not path.is_file():
        return _json({"ok": False, "error": f"not a file: {relative_path}"})
    text = path.read_text(encoding="utf-8")
    occurrences = text.count(old_text)
    if occurrences != expected_occurrences:
        return _json(
            {
                "ok": False,
                "error": (
                    f"expected {expected_occurrences} occurrence(s), found "
                    f"{occurrences}"
                ),
            }
        )
    updated = text.replace(old_text, new_text)
    progress_error = _deliverable_write_error(agent_state, path, updated)
    if progress_error is not None:
        return progress_error
    path.write_text(updated, encoding="utf-8")
    _record_deliverable_write(agent_state, path, updated)
    _record_mutation(agent_state)
    return _json(
        {
            "ok": True,
            "path": relative_path,
            "occurrences": occurrences,
            "sha256": sha256_file(path),
        }
    )


def delete_path(
    root: str,
    relative_path: str,
    agent_state: AgentState | None = None,
) -> str:
    path = _resolve_virtual(agent_state, root, relative_path, write=True)
    if path.is_dir():
        try:
            path.rmdir()
        except OSError as exc:
            return _json(
                {
                    "ok": False,
                    "error": f"directory must be empty before deletion: {exc}",
                }
            )
    elif path.exists():
        path.unlink()
    else:
        return _json({"ok": False, "error": f"path does not exist: {relative_path}"})
    _record_mutation(agent_state)
    return _json({"ok": True, "path": relative_path})


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


def _resolve_virtual(
    agent_state: AgentState | None,
    root: str,
    relative_path: str,
    *,
    write: bool,
) -> Path:
    roots_key = "meta_write_roots" if write else "meta_read_roots"
    roots = {
        str(key): Path(str(value)).resolve()
        for key, value in dict(_context_value(agent_state, roots_key) or {}).items()
    }
    if not write:
        roots.update(
            {
                str(key): Path(str(value)).resolve()
                for key, value in dict(
                    _context_value(agent_state, "meta_write_roots") or {}
                ).items()
            }
        )
    if root not in roots:
        raise ValueError(f"unknown or prohibited root: {root}")
    base = roots[root]
    if relative_path in {"", "."}:
        target = base
    else:
        relative = Path(relative_path)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("relative_path must stay within the selected root")
        target = (base / relative).resolve(strict=False)
    try:
        target.relative_to(base)
    except ValueError as exc:
        raise ValueError("resolved path escapes the selected root") from exc
    return target


def _workspace(agent_state: AgentState | None) -> Path:
    return Path(str(_context_value(agent_state, "workspace_dir"))).resolve()


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


def _entry(path: Path, base: Path) -> dict[str, Any]:
    try:
        relative = path.relative_to(base).as_posix()
    except ValueError:
        relative = path.name
    return {
        "path": relative or ".",
        "type": "directory" if path.is_dir() else "file",
        "size": path.stat().st_size if path.is_file() else None,
    }


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


def _record_debugger_artifact(
    agent_state: AgentState | None,
    root: str,
    relative_path: str,
) -> None:
    if str(_context_value(agent_state, "meta_role")) != "debugger":
        return
    value = _storage_get(agent_state, "debugger_artifacts_read", [])
    artifacts = {str(item) for item in value if str(item).strip()}
    artifacts.add(f"{root}:{Path(relative_path).as_posix()}")
    _storage_set(agent_state, "debugger_artifacts_read", sorted(artifacts))


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
