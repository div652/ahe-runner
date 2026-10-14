"""Run a shell command in the trial sandbox."""

from __future__ import annotations

from typing import Any

from tools._common import WorkspaceHandle, run_tool


def bash(command: str, agent_state: Any) -> str:
    return run_tool(agent_state, lambda handle: _bash(handle, command))


def _bash(handle: WorkspaceHandle, command: str) -> str:
    if not command:
        return "Error: command is required"
    result = handle.exec(command)
    output = result.stdout
    if result.stderr:
        output += f"\nSTDERR:\n{result.stderr}"
    if result.timed_out:
        return f"Error: command timed out after {handle.shell_timeout}s\n{output}"
    if result.returncode is not None and result.returncode != 0:
        output += f"\n(exit code {result.returncode})"
    return output or "(no output)"
