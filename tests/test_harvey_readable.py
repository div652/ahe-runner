"""Readable text views of Harvey evidence (AHE-016)."""

from __future__ import annotations

import json
from pathlib import Path

from harvey_ahe import readable
from harvey_ahe.readable import (
    WRAP_COLUMNS,
    normalize_text,
    render_trace,
    write_readable_views,
)


def test_normalize_text_uses_only_newlines_and_wraps_long_lines() -> None:
    text = "a\r\nb\rc\x0bd\u2028e\x85f\n" + "x" * (WRAP_COLUMNS * 2 + 5)
    lines = normalize_text(text).split("\n")
    assert lines[:3] == ["a", "b", "c d e f"]
    assert lines[3] == "x" * WRAP_COLUMNS
    assert all(line.startswith("↪ ") for line in lines[4:])
    assert all(len(line) <= WRAP_COLUMNS for line in lines)
    assert "".join(line.removeprefix("↪ ") for line in lines[3:]) == "x" * (
        WRAP_COLUMNS * 2 + 5
    )
    assert normalize_text(text).splitlines() == lines


def _trace(tmp_path: Path) -> Path:
    events = [
        {
            "event_type": "message",
            "index": 0,
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "Reading the brief."},
                    {
                        "type": "tool_use",
                        "name": "read_file",
                        "id": "call-1",
                        "input": {"file_path": "/work/brief.md", "limit": 50},
                    },
                ],
            },
        },
        {
            "event_type": "message",
            "index": 1,
            "message": {
                "role": "tool",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "call-1",
                        "is_error": False,
                        "content": json.dumps(
                            {"result": "line one\nline two\nline three"}
                        ),
                    }
                ],
            },
        },
        {"event_type": "finish", "summary": "done", "files": ["memo.docx"]},
    ]
    path = tmp_path / "trace.jsonl"
    lines = [json.dumps(event) for event in events]
    lines.insert(2, "")
    lines.append("{not json")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_render_trace_decodes_tool_results_into_real_lines(tmp_path: Path) -> None:
    text = render_trace(_trace(tmp_path), source_label="raw_evaluation:t.jsonl")
    lines = text.split("\n")
    assert "Source: raw_evaluation:t.jsonl (one JSON event per line)." in lines
    assert "## jsonl line 1 · message 0 · assistant" in lines
    assert "[tool_use read_file id=call-1]" in lines
    assert "file_path: /work/brief.md" in lines
    assert "limit: 50" in lines
    assert "## jsonl line 2 · message 1 · tool" in lines
    assert "[tool_result id=call-1 error=false]" in lines
    start = lines.index("[tool_result id=call-1 error=false]")
    assert lines[start + 1 : start + 4] == ["line one", "line two", "line three"]
    assert "## jsonl line 4 · finish" in lines
    assert 'files: ["memo.docx"]' in lines
    assert "## jsonl line 5 · unparsed" in lines


def test_write_readable_views_skips_unsafe_and_missing_deliverables(
    tmp_path: Path,
) -> None:
    evidence = tmp_path / "evidence"
    output = tmp_path / "output"
    output.mkdir()
    (output / "memo.docx").write_bytes(b"binary")
    (output / "outside.md").write_text("x", encoding="utf-8")
    seen: list[Path] = []

    def as_text(path: Path) -> str:
        seen.append(path)
        return "Memo body\u2028second\n"

    artifacts = write_readable_views(
        evidence_dir=evidence,
        task_key="task-a",
        trial=1,
        trace_path=_trace(tmp_path),
        trace_label="raw_evaluation:trace.jsonl",
        deliverables=[
            ("memo.docx", output / "memo.docx"),
            ("../outside.md", output / "outside.md"),
            ("/abs.md", output / "outside.md"),
            ("missing.md", output / "missing.md"),
        ],
        read_as_text=as_text,
    )
    assert seen == [output / "memo.docx"]
    assert [(item["kind"], item["path"], item["source"]) for item in artifacts] == [
        (
            "readable_trace",
            "readable/task-a/trial-1/trace.md",
            "raw_evaluation:trace.jsonl",
        ),
        (
            "readable_deliverable",
            "readable/task-a/trial-1/deliverables/memo.docx.md",
            "memo.docx",
        ),
    ]
    view = evidence / artifacts[1]["path"]
    assert view.read_text(encoding="utf-8").endswith("Memo body second\n")
    for item in artifacts:
        assert item["sha256"] == readable.sha256_file(evidence / item["path"])
