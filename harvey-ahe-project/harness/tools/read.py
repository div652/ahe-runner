"""Read a sandbox file; office documents and PDFs are parsed in the sandbox."""

from __future__ import annotations

import shlex
from pathlib import PurePosixPath
from typing import Any

from tools._common import WorkspaceHandle, resolve_read_path, run_tool

PARSED_EXTENSIONS = ("docx", "pdf", "pptx", "xlsx")
PARSE_TIMEOUT_SECONDS = 120


def read(
    file_path: str,
    agent_state: Any,
    offset: int | None = None,
    limit: int | None = None,
) -> str:
    return run_tool(agent_state, lambda handle: _read(handle, file_path, offset, limit))


def _read(
    handle: WorkspaceHandle,
    file_path: str,
    offset: int | None,
    limit: int | None,
) -> str:
    if not file_path:
        return "Error: file_path is required"
    sb_path = resolve_read_path(handle, file_path)
    if not handle.exists(sb_path):
        return f"Error: file not found: {file_path}"
    content = _read_and_parse(handle, sb_path)
    if offset is not None or limit is not None:
        lines = content.split("\n")
        start = offset or 0
        end = (start + limit) if limit else len(lines)
        content = "\n".join(lines[start:end])
    return content


def _read_and_parse(handle: WorkspaceHandle, sb_path: str) -> str:
    ext = PurePosixPath(sb_path).suffix.lower()[1:]
    if ext in PARSED_EXTENSIONS:
        return _parse_in_sandbox(handle, ext, sb_path)
    try:
        return handle.read_file(sb_path).decode("utf-8", errors="replace")
    except IsADirectoryError:
        return f"Error: {sb_path} is a directory, not a file"
    except OSError as exc:
        return f"Error: failed to read {sb_path}: {type(exc).__name__}: {exc}"


def _parse_in_sandbox(handle: WorkspaceHandle, ext: str, sb_path: str) -> str:
    result = handle.exec(
        f"parse-doc {ext} {shlex.quote(sb_path)}",
        timeout=PARSE_TIMEOUT_SECONDS,
    )
    if result.timed_out:
        return f"Error: parser timed out on {sb_path} ({ext})"
    if result.returncode != 0:
        err = (result.stderr or "").strip().splitlines()
        tail = err[-1] if err else f"exit {result.returncode}"
        return f"Error: failed to parse {sb_path} ({ext}): {tail}"
    return result.stdout
