"""Write a text file, by default under the output directory."""

from __future__ import annotations

from typing import Any

from tools._common import WorkspaceHandle, resolve_write_path, run_tool


def write(file_path: str, content: str, agent_state: Any) -> str:
    return run_tool(agent_state, lambda handle: _write(handle, file_path, content))


def _write(handle: WorkspaceHandle, file_path: str, content: str) -> str:
    if not file_path:
        return "Error: file_path is required"
    sb_path = resolve_write_path(handle, file_path)
    handle.write_file(sb_path, content)
    return f"Wrote {len(content)} bytes to {file_path}"
