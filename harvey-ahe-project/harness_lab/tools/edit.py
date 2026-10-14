"""Exact string replacement in a writable sandbox file."""

from __future__ import annotations

from typing import Any

from tools._common import (
    DOCUMENTS_PATH,
    OUTPUT_PATH,
    WORKSPACE_PATH,
    WorkspaceHandle,
    run_tool,
)


def edit(
    file_path: str,
    old_string: str,
    new_string: str,
    agent_state: Any,
    replace_all: bool = False,
) -> str:
    return run_tool(
        agent_state,
        lambda handle: _edit(handle, file_path, old_string, new_string, replace_all),
    )


def _edit(
    handle: WorkspaceHandle,
    file_path: str,
    old_string: str,
    new_string: str,
    replace_all: bool,
) -> str:
    if not file_path:
        return "Error: file_path is required"
    if file_path.startswith("/"):
        handle.assert_sandbox_path(file_path)
        sb_path = file_path
    else:
        found = None
        for mount in (OUTPUT_PATH, WORKSPACE_PATH, DOCUMENTS_PATH):
            candidate = f"{mount}/{file_path}"
            if handle.exists(candidate):
                found = candidate
                break
        if found is None:
            return f"Error: file not found: {file_path}"
        sb_path = found
    if not handle.is_writable(sb_path):
        return f"SecurityError: write denied: {sb_path} is not under a writable mount"
    if not handle.exists(sb_path):
        return f"Error: file not found: {file_path}"
    text = handle.read_file(sb_path).decode("utf-8", errors="replace")
    count = text.count(old_string)
    if count == 0:
        return f"Error: old_string not found in {file_path}"
    if count > 1 and not replace_all:
        return (
            f"Error: old_string found {count} times in {file_path}. "
            "Use replace_all=true to replace all."
        )
    if replace_all:
        new_text = text.replace(old_string, new_string)
    else:
        new_text = text.replace(old_string, new_string, 1)
    handle.write_file(sb_path, new_text)
    replaced = count if replace_all else 1
    return f"Replaced {replaced} occurrence(s) in {file_path}"
