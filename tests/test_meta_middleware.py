from __future__ import annotations

from types import SimpleNamespace

import pytest
from nexau.archs.main_sub.execution.hooks import BeforeModelHookInput
from nexau.archs.main_sub.utils.token_counter import TokenCounter
from nexau.core.messages import Message, Role, TextBlock, ToolResultBlock, ToolUseBlock

from ahe_runner.errors import IntegrityError
from ahe_runner.meta.middleware import (
    MetaContextCompactionMiddleware,
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
    assert all(
        "_ahe_compacted" in block.input
        for block in compacted_calls[:-retained]
    )
    assert all(
        "_ahe_compacted" not in block.input
        for block in compacted_calls[-retained:]
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
