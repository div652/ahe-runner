from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from nexau.archs.main_sub.execution.hooks import (
    AfterToolHookInput,
    BeforeModelHookInput,
)
from nexau.archs.main_sub.utils.token_counter import TokenCounter
from nexau.core.messages import Message, Role, TextBlock, ToolResultBlock, ToolUseBlock

from ahe_runner.errors import IntegrityError
from ahe_runner.meta.middleware import (
    MetaContextCompactionMiddleware,
    MetaToolOutputBackstopMiddleware,
    _compact_to_budget,
)


def _write_history() -> list[Message]:
    content = "Read inputs, verify shape, preserve state, return the result.\n" * 2000
    messages = [Message.user("Maintain the current implementation.")]
    for index in range(5):
        tool_id = f"write-{index}"
        messages.extend(
            [
                Message(
                    role=Role.ASSISTANT,
                    content=[
                        ToolUseBlock(
                            id=tool_id,
                            name="write_file",
                            input={
                                "relative_path": "implementation.py",
                                "content": f"Revision {index}\n{content}",
                            },
                        )
                    ],
                ),
                Message(
                    role=Role.TOOL,
                    content=[
                        ToolResultBlock(
                            tool_use_id=tool_id,
                            content="File updated.",
                            is_error=index == 0,
                        )
                    ],
                ),
            ]
        )
    return messages


def test_write_heavy_compaction_bounds_context_and_preserves_structure() -> None:
    messages = _write_history()
    counter = TokenCounter()
    target = int(200_000 * 0.72)
    latest = messages[-2:]
    compacted, changed, count, retained = _compact_to_budget(
        messages,
        token_counter=counter,
        target_tokens=target,
        keep_iterations=12,
    )

    assert changed is True
    assert count <= target
    assert 1 <= retained < 5
    assert compacted[-2:] == latest
    original_calls = [
        block
        for message in messages
        for block in message.content
        if isinstance(block, ToolUseBlock)
    ]
    compacted_calls = [
        block
        for message in compacted
        for block in message.content
        if isinstance(block, ToolUseBlock)
    ]
    assert [(block.id, block.name) for block in compacted_calls] == [
        (block.id, block.name) for block in original_calls
    ]
    assert all("_ahe_compacted" in block.input for block in compacted_calls[:-retained])
    assert all(
        "_ahe_compacted" not in block.input for block in compacted_calls[-retained:]
    )
    results = [
        block
        for message in compacted
        for block in message.content
        if isinstance(block, ToolResultBlock)
    ]
    assert [block.content for block in results] == ["File updated."] * 5
    assert results[0].is_error is True
    assert [block.tool_use_id for block in results] == [
        f"write-{index}" for index in range(5)
    ]


def test_compaction_blocks_provider_call_when_latest_iteration_exceeds_target() -> None:
    middleware = MetaContextCompactionMiddleware(
        max_context_tokens=200,
        threshold=0.5,
        keep_iterations=1,
    )
    messages = [
        Message.user("start"),
        Message(
            role=Role.ASSISTANT,
            content=[TextBlock(text="uncompactable latest response " * 500)],
        ),
    ]
    result = middleware.before_model(
        BeforeModelHookInput(
            agent_state=SimpleNamespace(),
            max_iterations=10,
            current_iteration=1,
            messages=messages,
        )
    )
    assert result.messages is None
    calls = 0

    def call_next(_params):  # type: ignore[no-untyped-def]
        nonlocal calls
        calls += 1
        return None

    with pytest.raises(IntegrityError, match="above the configured compaction target"):
        middleware.wrap_model_call(SimpleNamespace(), call_next)  # type: ignore[arg-type]
    assert calls == 0


def _after_tool(tool_name: str, output: object, call_id: str = "call-1"):
    return AfterToolHookInput(
        agent_state=SimpleNamespace(),  # type: ignore[arg-type]
        sandbox=None,
        tool_name=tool_name,
        tool_call_id=call_id,
        tool_input={},
        tool_output=output,
    )


def _backstop(tmp_path: Path, **kwargs) -> MetaToolOutputBackstopMiddleware:  # type: ignore[no-untyped-def]
    params = {
        "output_dir": str(tmp_path / "tool_outputs"),
        "max_output_chars": 64_000,
        "head_chars": 24_000,
        "tail_chars": 8_000,
        "bypass_tool_names": ["LoadSkill", "complete_task"],
    }
    params.update(kwargs)
    return MetaToolOutputBackstopMiddleware(**params)


def _large_listing() -> str:
    entries = [
        {"path": f"trial-{index:05d}/agent/trace.json", "size": index, "type": "file"}
        for index in range(4_000)
    ]
    return json.dumps({"ok": True, "entries": entries, "truncated": False})


def test_backstop_passes_results_within_the_limit_unchanged(tmp_path: Path) -> None:
    middleware = _backstop(tmp_path)
    result = middleware.after_tool(_after_tool("read_file", {"result": "x" * 64_000}))
    assert result.tool_output is None
    assert not (tmp_path / "tool_outputs").exists()


def test_backstop_shortens_long_results_and_saves_a_readable_copy(
    tmp_path: Path,
) -> None:
    listing = _large_listing()
    assert len(listing) > 64_000
    middleware = _backstop(tmp_path)
    result = middleware.after_tool(
        _after_tool(
            "list_files",
            {"result": listing, "_is_stop_tool": False},
            call_id="call_abc/../1",
        )
    )
    output = result.tool_output
    assert isinstance(output, dict)
    assert output["_is_stop_tool"] is False
    text = output["result"]
    assert len(text) < 34_000
    assert "characters omitted by the tool-output limit" in text
    assert f"This result had {len(listing):,} characters" in text
    saved = sorted((tmp_path / "tool_outputs").iterdir())
    assert [path.name for path in saved] == ["0001-list_files-call_abc____1.txt"]
    assert (
        'root "tool_outputs" at relative_path "0001-list_files-call_abc____1.txt"'
        in (text)
    )
    copy = saved[0].read_text(encoding="utf-8")
    assert json.loads(copy) == json.loads(listing)
    assert copy.count("\n") > 4_000
    assert text.startswith(copy[:24_000])
    head_end = text.index("\n\n... [")
    tail_start = text.index("] ...\n\n") + len("] ...\n\n")
    note_start = text.index("\n\n[tool-output limit]")
    assert head_end == 24_000
    assert text[tail_start:note_start] == copy[-8_000:]


def test_backstop_never_shortens_bypassed_tools(tmp_path: Path) -> None:
    middleware = _backstop(tmp_path)
    for name in ("LoadSkill", "complete_task"):
        result = middleware.after_tool(
            _after_tool(name, {"result": "s" * 200_000, "_is_stop_tool": True})
        )
        assert result.tool_output is None
    assert not (tmp_path / "tool_outputs").exists()


def test_backstop_saves_each_long_result_under_a_new_name(tmp_path: Path) -> None:
    middleware = _backstop(tmp_path)
    for _ in range(2):
        middleware.after_tool(
            _after_tool("search_text", {"result": "plain text\n" * 10_000})
        )
    names = sorted(path.name for path in (tmp_path / "tool_outputs").iterdir())
    assert names == [
        "0001-search_text-call-1.txt",
        "0002-search_text-call-1.txt",
    ]
    restarted = _backstop(tmp_path)
    restarted.after_tool(_after_tool("search_text", {"result": "y" * 70_000}))
    assert (tmp_path / "tool_outputs" / "0003-search_text-call-1.txt").is_file()


def test_backstop_still_shortens_when_the_copy_cannot_be_saved(
    tmp_path: Path,
) -> None:
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("occupied", encoding="utf-8")
    middleware = _backstop(tmp_path, output_dir=str(blocker))
    result = middleware.after_tool(_after_tool("read_file", {"result": "z" * 90_000}))
    assert isinstance(result.tool_output, dict)
    text = result.tool_output["result"]
    assert len(text) < 34_000
    assert text.endswith("The full result could not be saved.")


def test_backstop_returns_decoded_json_whole_when_it_fits(tmp_path: Path) -> None:
    escaped = json.dumps({"ok": True, "content": "\u00e9" * 20_000})
    assert len(escaped) > 64_000
    middleware = _backstop(tmp_path)
    result = middleware.after_tool(_after_tool("read_file", {"result": escaped}))
    assert isinstance(result.tool_output, dict)
    assert json.loads(result.tool_output["result"]) == json.loads(escaped)
    assert len(result.tool_output["result"]) <= 64_000
    assert not (tmp_path / "tool_outputs").exists()


def test_backstop_rejects_a_preview_larger_than_the_limit(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="head_chars \\+ tail_chars"):
        _backstop(tmp_path, head_chars=60_000, tail_chars=8_000)
