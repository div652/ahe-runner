"""Shared helpers for the Harvey workspace tools.

These port the path resolution and error mapping of LAB's native
``ToolExecutor`` so that every tool reaches the trial sandbox the same way.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from harvey_ahe.workspace import (
    DOCUMENTS_PATH,
    OUTPUT_PATH,
    WORKSPACE_PATH,
    WorkspaceHandle,
    handle_for,
)

__all__ = [
    "DOCUMENTS_PATH",
    "OUTPUT_PATH",
    "WORKSPACE_PATH",
    "WorkspaceHandle",
    "error_text",
    "handle_for",
    "is_under",
    "resolve_read_path",
    "resolve_search_path",
    "resolve_write_path",
    "run_tool",
]


def error_text(exc: Exception) -> str:
    """Tool-facing text for an exception, as LAB's dispatcher reports it."""
    if isinstance(exc, PermissionError):
        return f"SecurityError: {exc}"
    if isinstance(exc, (FileNotFoundError, ValueError)):
        return f"Error: {exc}"
    return f"Error: {type(exc).__name__}: {exc}"


def run_tool(agent_state: Any, action: Callable[[WorkspaceHandle], str]) -> str:
    """Run one tool call against this session's sandbox; never raise."""
    try:
        return action(handle_for(agent_state))
    except Exception as exc:
        return error_text(exc)


def resolve_read_path(handle: WorkspaceHandle, path_str: str) -> str:
    """Absolute paths pass through; relative ones probe workspace, documents,
    output, and fall back to documents."""
    if path_str.startswith("/"):
        handle.assert_sandbox_path(path_str)
        return path_str
    for mount in (WORKSPACE_PATH, DOCUMENTS_PATH, OUTPUT_PATH):
        candidate = f"{mount}/{path_str}"
        if handle.exists(candidate):
            return candidate
    return f"{DOCUMENTS_PATH}/{path_str}"


def resolve_write_path(handle: WorkspaceHandle, path_str: str) -> str:
    """Absolute paths must be writable; relative ones go under output."""
    if path_str.startswith("/"):
        handle.assert_sandbox_path(path_str)
        if not handle.is_writable(path_str):
            raise PermissionError(
                f"write denied: {path_str} is read-only "
                f"(documents) or outside /workspace"
            )
        return path_str
    return f"{OUTPUT_PATH}/{path_str}"


def resolve_search_path(handle: WorkspaceHandle, path_str: str | None) -> str:
    """Search root for glob/grep; defaults to the documents directory."""
    if not path_str:
        return DOCUMENTS_PATH
    if path_str.startswith("/"):
        handle.assert_sandbox_path(path_str)
        return path_str
    for mount in (DOCUMENTS_PATH, WORKSPACE_PATH, OUTPUT_PATH):
        candidate = f"{mount}/{path_str}"
        if handle.exists(candidate):
            return candidate
    return f"{DOCUMENTS_PATH}/{path_str}"


def is_under(path: Path, root_resolved: Path) -> bool:
    """True if ``path`` resolves inside ``root_resolved`` (symlink-escape guard
    for host-side traversal)."""
    try:
        path.resolve(strict=False).relative_to(root_resolved)
        return True
    except ValueError:
        return False
