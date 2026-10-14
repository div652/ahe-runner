"""Find files by glob pattern, newest first."""

from __future__ import annotations

from typing import Any

from tools._common import WorkspaceHandle, is_under, resolve_search_path, run_tool

MAX_MATCHES = 100


def glob(pattern: str, agent_state: Any, path: str | None = None) -> str:
    return run_tool(agent_state, lambda handle: _glob(handle, pattern, path))


def _glob(handle: WorkspaceHandle, pattern: str, search_path: str | None) -> str:
    if not pattern:
        return "Error: pattern is required"
    sb_path = resolve_search_path(handle, search_path)
    if not handle.exists(sb_path):
        return f"Error: path does not exist: {search_path}"
    host_root = handle.to_host(sb_path)
    if not host_root.exists():
        return f"Error: path does not exist: {search_path}"
    root_resolved = host_root.resolve(strict=False)
    matches = sorted(
        (
            match
            for match in host_root.glob(pattern)
            if match.is_file() and is_under(match, root_resolved)
        ),
        key=lambda match: match.stat().st_mtime,
        reverse=True,
    )
    if not matches:
        return f"No files matching '{pattern}' in {sb_path}"
    return "\n".join(
        str(match.relative_to(host_root)) for match in matches[:MAX_MATCHES]
    )
