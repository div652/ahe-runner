"""Stop tool: end the run once the listed deliverables exist."""

from __future__ import annotations

import posixpath
from typing import Any

from tools._common import (
    OUTPUT_PATH,
    WORKSPACE_PATH,
    WorkspaceHandle,
    error_text,
    handle_for,
)

MAX_REJECTIONS = 2
REJECTIONS_KEY = "harvey_finish_rejections"


def finish(
    summary: str,
    agent_state: Any,
    deliverables: list[str] | None = None,
) -> str | dict[str, str]:
    """Latch completion, after up to two rejections for missing deliverables.

    NexAU ends the run when a stop tool succeeds, so every rejection and error
    is returned with ``status: error`` to keep the agent running.
    """
    try:
        handle = handle_for(agent_state)
        missing = _missing_deliverables(handle, deliverables or [])
        rejections = int(agent_state.get_global_value(REJECTIONS_KEY, 0))
        if missing and rejections < MAX_REJECTIONS:
            agent_state.set_global_value(REJECTIONS_KEY, rejections + 1)
            return {
                "status": "error",
                "error": (
                    "Not finished: the following file(s) you listed do not "
                    f"exist in the output directory: {', '.join(missing)}. "
                    "Create them (generate binary formats with a script run "
                    "through bash) or correct the paths, then call finish again."
                ),
            }
        handle.mark_finished(summary)
        return "Finished."
    except Exception as exc:
        return {"status": "error", "error": error_text(exc)}


def _missing_deliverables(handle: WorkspaceHandle, paths: Any) -> list[str]:
    if not isinstance(paths, list):
        return [repr(paths)]
    missing: list[str] = []
    for path in paths:
        if not isinstance(path, str) or not path.strip():
            missing.append(repr(path))
        elif not _deliverable_exists(handle, path):
            missing.append(path)
    return missing


def _deliverable_exists(handle: WorkspaceHandle, path_str: str) -> bool:
    """Accept bare, ``output/``-prefixed, and absolute output paths."""
    if path_str.startswith("/"):
        candidates = [path_str]
    else:
        candidates = [f"{OUTPUT_PATH}/{path_str}", f"{WORKSPACE_PATH}/{path_str}"]
    for candidate in candidates:
        normalized = posixpath.normpath(candidate)
        if normalized.startswith(OUTPUT_PATH + "/") and handle.exists(normalized):
            return True
    return False
