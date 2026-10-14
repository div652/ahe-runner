"""Context compaction for long-running meta-agents."""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from nexau.archs.main_sub.execution.hooks import (
    BeforeModelHookInput,
    HookResult,
    Middleware,
    ModelCallFn,
    ModelCallParams,
)
from nexau.archs.main_sub.utils.token_counter import TokenCounter
from nexau.core.messages import Message, Role, ToolResultBlock, ToolUseBlock

from ahe_runner.errors import IntegrityError

logger = logging.getLogger(__name__)

_COMPACTED_TOOL_RESULT = "[older tool result compacted; re-read the source artifact]"
_COMPACTED_TOOL_ARGUMENTS = (
    "Older tool arguments were compacted. Re-read the current source artifact "
    "when exact prior content is needed."
)


class MetaContextCompactionMiddleware(Middleware):
    def __init__(
        self,
        *,
        max_context_tokens: int = 200_000,
        threshold: float = 0.75,
        keep_iterations: int = 10,
    ) -> None:
        if max_context_tokens < 1:
            raise ValueError("max_context_tokens must be positive")
        if not 0 < threshold < 1:
            raise ValueError("threshold must be between zero and one")
        if keep_iterations < 1:
            raise ValueError("keep_iterations must be positive")
        self.max_context_tokens = max_context_tokens
        self.threshold = threshold
        self.keep_iterations = keep_iterations
        self._token_counter = TokenCounter()
        self._pending_budget_error: str | None = None

    def before_model(self, hook_input: BeforeModelHookInput) -> HookResult:
        self._pending_budget_error = None
        try:
            token_count = self._token_counter.count_tokens(hook_input.messages)
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            logger.warning("meta-agent token counting failed: %s", exc)
            return HookResult.no_changes()
        target = int(self.max_context_tokens * self.threshold)
        if token_count < target:
            return HookResult.no_changes()
        compacted, changed, compacted_count, retained = _compact_to_budget(
            hook_input.messages,
            token_counter=self._token_counter,
            target_tokens=target,
            keep_iterations=self.keep_iterations,
        )
        if compacted_count > target:
            self._pending_budget_error = (
                "meta-agent context remains above the configured compaction target "
                f"after preserving the latest iteration: {compacted_count} > {target}"
            )
            logger.error(self._pending_budget_error)
        if not changed:
            logger.warning(
                "meta-agent context is %d tokens but no older payload can compact",
                token_count,
            )
            return HookResult.no_changes()
        logger.info(
            "compacted meta context from %d to %d tokens; retained %d iterations",
            token_count,
            compacted_count,
            retained,
        )
        return HookResult.with_modifications(messages=compacted)

    def wrap_model_call(
        self,
        params: ModelCallParams,
        call_next: ModelCallFn,
    ) -> Any:
        if self._pending_budget_error is not None:
            message = self._pending_budget_error
            self._pending_budget_error = None
            raise IntegrityError(message)
        return call_next(params)


def _compact_to_budget(
    messages: list[Message],
    *,
    token_counter: TokenCounter,
    target_tokens: int,
    keep_iterations: int,
) -> tuple[list[Message], bool, int, int]:
    compacted = messages
    changed = False
    count = token_counter.count_tokens(compacted)
    retained = keep_iterations
    for retained in range(keep_iterations, 0, -1):
        compacted, iteration_changed = _compact_old_tool_results(
            compacted,
            keep_iterations=retained,
        )
        if iteration_changed:
            changed = True
            count = token_counter.count_tokens(compacted)
        if count <= target_tokens:
            break
        compacted, argument_changed = _compact_old_tool_arguments(
            compacted,
            keep_iterations=retained,
        )
        if argument_changed:
            changed = True
            count = token_counter.count_tokens(compacted)
        if count <= target_tokens:
            break
    return compacted, changed, count, retained


def _compact_old_tool_results(
    messages: list[Message],
    *,
    keep_iterations: int,
) -> tuple[list[Message], bool]:
    assistant_indices = [
        index
        for index, message in enumerate(messages)
        if message.role == Role.ASSISTANT
    ]
    if len(assistant_indices) <= keep_iterations:
        return messages, False
    protected_start = assistant_indices[-keep_iterations]
    changed = False
    result: list[Message] = []
    for index, message in enumerate(messages):
        if message.role != Role.TOOL or index >= protected_start:
            result.append(message)
            continue
        blocks = []
        message_changed = False
        for block in message.content:
            if (
                isinstance(block, ToolResultBlock)
                and block.content != _COMPACTED_TOOL_RESULT
                and _payload_size(block.content) > len(_COMPACTED_TOOL_RESULT)
            ):
                blocks.append(
                    block.model_copy(update={"content": _COMPACTED_TOOL_RESULT})
                )
                message_changed = True
            else:
                blocks.append(block)
        result.append(
            message.model_copy(update={"content": blocks})
            if message_changed
            else message
        )
        changed = changed or message_changed
    return result, changed


def _compact_old_tool_arguments(
    messages: list[Message],
    *,
    keep_iterations: int,
) -> tuple[list[Message], bool]:
    assistant_indices = [
        index
        for index, message in enumerate(messages)
        if message.role == Role.ASSISTANT
    ]
    if len(assistant_indices) <= keep_iterations:
        return messages, False
    protected_start = assistant_indices[-keep_iterations]
    changed = False
    result: list[Message] = []
    for index, message in enumerate(messages):
        if message.role != Role.ASSISTANT or index >= protected_start:
            result.append(message)
            continue
        blocks = []
        message_changed = False
        for block in message.content:
            if not isinstance(block, ToolUseBlock):
                blocks.append(block)
                continue
            compacted_input = _compact_tool_input(block.input)
            if compacted_input is block.input:
                blocks.append(block)
                continue
            blocks.append(
                block.model_copy(
                    update={
                        "input": compacted_input,
                        "raw_input": None,
                    }
                )
            )
            message_changed = True
        result.append(
            message.model_copy(update={"content": blocks})
            if message_changed
            else message
        )
        changed = changed or message_changed
    return result, changed


def _compact_tool_input(value: dict[str, Any]) -> dict[str, Any]:
    serialized = json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        default=repr,
    )
    payload = {
        "_ahe_compacted": _COMPACTED_TOOL_ARGUMENTS,
        "original_bytes": len(serialized.encode("utf-8")),
        "sha256": hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
    }
    if _payload_size(payload) >= len(serialized):
        return value
    return payload


def _payload_size(value: Any) -> int:
    if isinstance(value, str):
        return len(value)
    return len(
        json.dumps(
            value,
            sort_keys=True,
            ensure_ascii=False,
            default=repr,
        )
    )
