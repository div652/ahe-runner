"""Plain-text views of Harvey training evidence for the meta agents (AHE-016).

``trace.jsonl`` stores one JSON event per line, so a tool result with real
line breaks becomes a single line of up to ~30,000 characters, and the
original ``read_file`` cuts lines at 2,000. Deliverables are ``.docx`` or
``.xlsx`` files. These views give the Debugger and Evolve the same content
as text with real, short lines and stable line numbers.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Any

from ahe_runner.io import atomic_write_text, sha256_file

WRAP_COLUMNS = 1000
_CONTINUATION = "↪ "
# Characters that str.splitlines() treats as line breaks, besides "\n".
_EXTRA_LINE_BREAKS = {
    ord("\x0b"): " ",
    ord("\x0c"): " ",
    ord("\x1c"): " ",
    ord("\x1d"): " ",
    ord("\x1e"): " ",
    ord("\x85"): " ",
    ord("\u2028"): " ",
    ord("\u2029"): " ",
}
_INLINE_VALUE_LIMIT = 200


def write_readable_views(
    *,
    evidence_dir: Path,
    task_key: str,
    trial: int,
    trace_path: Path,
    trace_label: str,
    deliverables: list[tuple[str, Path]],
    read_as_text: Callable[[Path], str],
) -> list[dict[str, str]]:
    """Write the trace and deliverable views; return evidence artifacts."""

    base = evidence_dir / "readable" / task_key / f"trial-{trial}"
    artifacts: list[dict[str, str]] = []
    trace_view = base / "trace.md"
    atomic_write_text(trace_view, render_trace(trace_path, source_label=trace_label))
    artifacts.append(_artifact("readable_trace", trace_view, evidence_dir, trace_label))
    for relative, path in deliverables:
        name = _safe_relative(relative)
        if name is None or not path.is_file():
            continue
        view = base / "deliverables" / f"{name}.md"
        text = (
            f"# Deliverable {relative} (text view)\n\n"
            "Same text extraction the judges use (pandoc for .docx, pandas for "
            ".xlsx).\n\n---\n\n" + read_as_text(path)
        )
        atomic_write_text(view, normalize_text(text))
        artifacts.append(
            _artifact("readable_deliverable", view, evidence_dir, relative)
        )
    return artifacts


def render_trace(trace_path: Path, *, source_label: str) -> str:
    sections = [
        "# Agent trace (readable view)",
        "",
        f"Source: {source_label} (one JSON event per line).",
        'Each "## jsonl line N" header names the trace.jsonl line the text comes from.',
        f"Lines longer than {WRAP_COLUMNS} characters are wrapped; a "
        f'continuation line starts with "{_CONTINUATION.strip()}".',
    ]
    raw_lines = trace_path.read_text(encoding="utf-8").split("\n")
    for number, raw in enumerate(raw_lines, start=1):
        if not raw.strip():
            continue
        try:
            event = json.loads(raw)
        except ValueError:
            sections.extend(["", f"## jsonl line {number} · unparsed", raw])
            continue
        sections.extend(["", *_render_event(number, event)])
    return normalize_text("\n".join(sections) + "\n")


def normalize_text(text: str) -> str:
    """Use only "\\n" line breaks and wrap lines at ``WRAP_COLUMNS``."""

    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.translate(_EXTRA_LINE_BREAKS)
    lines: list[str] = []
    for line in text.split("\n"):
        if len(line) <= WRAP_COLUMNS:
            lines.append(line)
            continue
        lines.append(line[:WRAP_COLUMNS])
        rest = line[WRAP_COLUMNS:]
        width = WRAP_COLUMNS - len(_CONTINUATION)
        while rest:
            lines.append(_CONTINUATION + rest[:width])
            rest = rest[width:]
    return "\n".join(lines)


def _render_event(number: int, event: Any) -> list[str]:
    if not isinstance(event, dict):
        return [f"## jsonl line {number}", _pretty(event)]
    kind = str(event.get("event_type", "event"))
    if kind == "message" and isinstance(event.get("message"), dict):
        message = event["message"]
        header = (
            f"## jsonl line {number} · message {event.get('index')} · "
            f"{message.get('role')}"
        )
        lines = [header]
        content = message.get("content")
        if isinstance(content, list):
            for block in content:
                lines.extend(_render_block(block))
        elif content is not None:
            lines.append(_text(content))
        return lines
    lines = [f"## jsonl line {number} · {kind}"]
    for key, value in event.items():
        if key == "event_type":
            continue
        lines.extend(_field(key, value))
    return lines


def _render_block(block: Any) -> list[str]:
    if not isinstance(block, dict):
        return [_pretty(block)]
    kind = block.get("type")
    if kind == "text":
        return [_text(block.get("text", ""))]
    if kind == "tool_use":
        lines = [f"[tool_use {block.get('name')} id={block.get('id')}]"]
        arguments = block.get("input")
        if isinstance(arguments, dict):
            for key, value in arguments.items():
                lines.extend(_field(key, value))
        else:
            lines.append(_text(block.get("raw_input", arguments)))
        return lines
    if kind == "tool_result":
        lines = [
            f"[tool_result id={block.get('tool_use_id')} "
            f"error={json.dumps(block.get('is_error'))}]"
        ]
        lines.extend(_tool_result(block.get("content")))
        return lines
    lines = [f"[{kind}]"]
    for key, value in block.items():
        if key != "type":
            lines.extend(_field(key, value))
    return lines


def _tool_result(content: Any) -> list[str]:
    if isinstance(content, list):
        texts = [
            _text(item.get("text"))
            if isinstance(item, dict) and item.get("type") == "text"
            else _pretty(item)
            for item in content
        ]
        return texts
    if not isinstance(content, str):
        return [_pretty(content)]
    try:
        decoded = json.loads(content)
    except ValueError:
        return [content]
    if isinstance(decoded, dict):
        if set(decoded) == {"result"} and isinstance(decoded["result"], str):
            return [decoded["result"]]
        lines: list[str] = []
        for key, value in decoded.items():
            lines.extend(_field(key, value))
        return lines
    if isinstance(decoded, str):
        return [decoded]
    return [_pretty(decoded)]


def _field(key: str, value: Any) -> list[str]:
    if isinstance(value, str):
        if "\n" in value or len(value) > _INLINE_VALUE_LIMIT:
            return [f"{key}:", value]
        return [f"{key}: {value}"]
    rendered = json.dumps(value, ensure_ascii=False)
    if len(rendered) > _INLINE_VALUE_LIMIT:
        return [f"{key}:", _pretty(value)]
    return [f"{key}: {rendered}"]


def _text(value: Any) -> str:
    return value if isinstance(value, str) else _pretty(value)


def _pretty(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


def _safe_relative(value: str) -> str | None:
    candidate = PurePosixPath(value)
    if (
        not value
        or "\\" in value
        or candidate.is_absolute()
        or any(part in {"", ".", ".."} for part in candidate.parts)
    ):
        return None
    return candidate.as_posix()


def _artifact(kind: str, path: Path, evidence_dir: Path, source: str) -> dict[str, str]:
    return {
        "kind": kind,
        "path": path.relative_to(evidence_dir).as_posix(),
        "sha256": sha256_file(path),
        "source": source,
    }
