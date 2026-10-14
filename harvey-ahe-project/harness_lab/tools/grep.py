"""Regex search over sandbox files."""

from __future__ import annotations

import re
from typing import Any

from tools._common import WorkspaceHandle, is_under, resolve_search_path, run_tool

MAX_RESULTS = 250


def grep(
    pattern: str,
    agent_state: Any,
    path: str | None = None,
    glob: str | None = None,
    output_mode: str = "files_with_matches",
) -> str:
    return run_tool(
        agent_state,
        lambda handle: _grep(handle, pattern, path, glob, output_mode),
    )


def _grep(
    handle: WorkspaceHandle,
    pattern_str: str,
    search_path: str | None,
    file_glob: str | None,
    output_mode: str,
) -> str:
    if not pattern_str:
        return "Error: pattern is required"
    sb_path = resolve_search_path(handle, search_path)
    if not handle.exists(sb_path):
        return f"Error: path does not exist: {search_path}"
    try:
        regex = re.compile(pattern_str)
    except re.error as exc:
        return f"Error: invalid regex: {exc}"
    host_root = handle.to_host(sb_path)
    root_resolved = host_root.resolve(strict=False)
    results: list[str] = []
    for file_path in host_root.glob(file_glob or "**/*"):
        if not file_path.is_file() or not is_under(file_path, root_resolved):
            continue
        try:
            text = file_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        matches = list(regex.finditer(text))
        if not matches:
            continue
        relative = str(file_path.relative_to(host_root))
        if output_mode == "files_with_matches":
            results.append(relative)
        elif output_mode == "count":
            results.append(f"{relative}: {len(matches)}")
        elif output_mode == "content":
            for number, line in enumerate(text.split("\n"), start=1):
                if regex.search(line):
                    results.append(f"{relative}:{number}: {line}")
    if not results:
        return f"No matches for '{pattern_str}'"
    return "\n".join(results[:MAX_RESULTS])
