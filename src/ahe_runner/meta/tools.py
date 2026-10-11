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
from ahe_runner.config import load_config
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
    agent_state: AgentState | None = None,
) -> str:
    blocked = _research_gate(agent_state)
    if blocked:
        return blocked
    path = _resolve_virtual(agent_state, root, relative_path, write=False)
    if not path.is_file():
        return _json({"ok": False, "error": f"not a file: {relative_path}"})
    if path.stat().st_size > _MAX_READ_BYTES:
        return _json(
            {
                "ok": False,
                "error": f"file exceeds {_MAX_READ_BYTES} byte read limit",
                "size": path.stat().st_size,
            }
        )
    if start_line < 1 or end_line < start_line:
        return _json({"ok": False, "error": "invalid line range"})
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    selected = lines[start_line - 1 : end_line]
    return _json(
        {
            "ok": True,
            "path": relative_path,
            "start_line": start_line,
            "end_line": min(end_line, len(lines)),
            "total_lines": len(lines),
            "content": "\n".join(selected),
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
    pattern = re.compile(query if regex else re.escape(query), flags)
    candidates = [base] if base.is_file() else sorted(base.glob(file_glob))
    matches: list[dict[str, Any]] = []
    skipped: list[str] = []
    for path in candidates:
        if (
            not path.is_file()
            or path.is_symlink()
            or ".git" in path.parts
            or path.stat().st_size > _MAX_READ_BYTES
        ):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            skipped.append(path.as_posix())
            continue
        for line_number, line in enumerate(text.splitlines(), start=1):
            if pattern.search(line):
                matches.append(
                    {
                        "path": path.relative_to(
                            base if base.is_dir() else base.parent
                        ).as_posix(),
                        "line": line_number,
                        "text": line[:2_000],
                    }
                )
                if len(matches) >= _MAX_SEARCH_MATCHES:
                    return _json(
                        {
                            "ok": True,
                            "matches": matches,
                            "truncated": True,
                            "skipped": skipped[:20],
                        }
                    )
    return _json(
        {
            "ok": True,
            "matches": matches,
            "truncated": False,
            "skipped": skipped[:20],
        }
    )


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
                manifest = read_json(manifest_path)
            except Exception as exc:
                problems.append(f"change_manifest.json is invalid: {exc}")
            else:
                expected_checkpoint = _context_value(agent_state, "checkpoint")
                expected_parent = _context_value(
                    agent_state,
                    "parent_checkpoint",
                )
                if manifest.get("schema_version") != 2:
                    problems.append("change_manifest.json schema_version must be 2")
                if manifest.get("checkpoint") != expected_checkpoint:
                    problems.append(
                        "change_manifest.json checkpoint does not match this round"
                    )
                if manifest.get("parent_checkpoint") != expected_parent:
                    problems.append(
                        "change_manifest.json parent_checkpoint is incorrect"
                    )
                changes = manifest.get("changes")
                if not isinstance(changes, list) or not changes:
                    problems.append(
                        "change_manifest.json changes must be a non-empty list"
                    )
                elif any(
                    not isinstance(item, dict) or not item.get("path")
                    for item in changes
                ):
                    problems.append("every change_manifest.json change requires a path")
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
