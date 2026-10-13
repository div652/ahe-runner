"""Context compaction and tool-output limits for long-running meta-agents."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from nexau.archs.main_sub.execution.hooks import (
    AfterToolHookInput,
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


# Keys NexAU's tool executor adds or strips around the tool's own result.
_TOOL_CONTROL_KEYS = frozenset({"_is_stop_tool", "returnDisplay"})


class MetaToolOutputBackstopMiddleware(Middleware):
    """Shorten oversized tool results and keep the full text readable (AHE-014).

    Results longer than ``max_output_chars`` are replaced by a head/tail
    preview and a note. The full result is saved under ``output_dir``, which
    the runner exposes to the agent as the read-only root ``root_name``.
    """

    def __init__(
        self,
        *,
        output_dir: str,
        root_name: str = "tool_outputs",
        max_output_chars: int = 64_000,
        head_chars: int = 24_000,
        tail_chars: int = 8_000,
        bypass_tool_names: Sequence[str] = (),
    ) -> None:
        if max_output_chars < 1:
            raise ValueError("max_output_chars must be positive")
        if head_chars < 0 or tail_chars < 0:
            raise ValueError("head_chars and tail_chars must not be negative")
        if head_chars + tail_chars > max_output_chars:
            raise ValueError("head_chars + tail_chars must not exceed max_output_chars")
        if not root_name:
            raise ValueError("root_name must not be empty")
        self.output_dir = Path(output_dir)
        self.root_name = root_name
        self.max_output_chars = max_output_chars
        self.head_chars = head_chars
        self.tail_chars = tail_chars
        self._bypass_tool_names = frozenset(bypass_tool_names)
        self._lock = threading.Lock()
        self._sequence = 0

    def after_tool(self, hook_input: AfterToolHookInput) -> HookResult:
        if hook_input.tool_name in self._bypass_tool_names:
            return HookResult.no_changes()
        output = hook_input.tool_output
        if isinstance(output, dict):
            result_key = next(
                (
                    key
                    for key in ("result", "content")
                    if isinstance(output.get(key), str)
                ),
                None,
            )
            if result_key is not None:
                text = str(output[result_key])
            else:
                text = json.dumps(
                    {
                        key: value
                        for key, value in output.items()
                        if key not in _TOOL_CONTROL_KEYS
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    default=str,
                )
        elif isinstance(output, str):
            result_key = None
            text = output
        else:
            return HookResult.no_changes()
        if len(text) <= self.max_output_chars:
            return HookResult.no_changes()

        readable = _readable_tool_text(text)
        if len(readable) <= self.max_output_chars:
            # Escaped JSON can shrink when decoded; the whole result then fits.
            return self._replace(output, result_key, readable)
        try:
            relative_path = self._save(
                tool_name=hook_input.tool_name,
                tool_call_id=hook_input.tool_call_id,
                text=readable,
            )
        except OSError as exc:
            logger.warning(
                "could not save the full %s result: %s", hook_input.tool_name, exc
            )
            relative_path = None
        shortened = self._preview(readable) + self._note(
            original_chars=len(text),
            saved_lines=readable.count("\n") + 1,
            relative_path=relative_path,
        )
        logger.info(
            "shortened %s result from %d to %d characters; full copy: %s",
            hook_input.tool_name,
            len(text),
            len(shortened),
            relative_path,
        )
        return self._replace(output, result_key, shortened)

    @staticmethod
    def _replace(output: Any, result_key: str | None, text: str) -> HookResult:
        if not isinstance(output, dict):
            return HookResult.with_modifications(tool_output=text)
        if result_key is not None:
            replaced = dict(output)
            replaced[result_key] = text
        else:
            replaced = {
                key: value for key, value in output.items() if key in _TOOL_CONTROL_KEYS
            }
            replaced["result"] = text
        return HookResult.with_modifications(tool_output=replaced)

    def _preview(self, text: str) -> str:
        head = text[: self.head_chars]
        tail = text[len(text) - self.tail_chars :] if self.tail_chars else ""
        omitted = len(text) - len(head) - len(tail)
        return (
            head
            + f"\n\n... [{omitted:,} characters omitted by the tool-output "
            + "limit] ...\n\n"
            + tail
        )

    def _note(
        self,
        *,
        original_chars: int,
        saved_lines: int,
        relative_path: str | None,
    ) -> str:
        shown = (
            f"[tool-output limit] This result had {original_chars:,} characters, "
            f"more than the {self.max_output_chars:,}-character limit. Only the "
            f"first {self.head_chars:,} and last {self.tail_chars:,} characters "
            "are shown above."
        )
        if relative_path is None:
            return f"\n\n{shown} The full result could not be saved."
        return (
            f"\n\n{shown} The full result is saved ({saved_lines:,} lines; JSON is "
            f"reformatted one item per line) in read-only root "
            f'"{self.root_name}" at relative_path "{relative_path}". Read it with '
            f'read_file(root="{self.root_name}", relative_path="{relative_path}", '
            "start_line=..., end_line=...) or search it with search_text. Keep "
            "each read well under the limit (for example max_bytes=40000), or it "
            "will be shortened again. A narrower original call (a deeper "
            "relative_path, recursive=false, or a smaller range) is often better."
        )

    def _save(self, *, tool_name: str, tool_call_id: str, text: str) -> str:
        safe_tool = re.sub(r"[^A-Za-z0-9_.-]", "_", tool_name)[:40] or "tool"
        safe_call = re.sub(r"[^A-Za-z0-9_-]", "_", tool_call_id)[:24] or "call"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        with self._lock:
            while True:
                self._sequence += 1
                name = f"{self._sequence:04d}-{safe_tool}-{safe_call}.txt"
                try:
                    with (self.output_dir / name).open("x", encoding="utf-8") as handle:
                        handle.write(text)
                except FileExistsError:
                    continue
                return name


def _readable_tool_text(text: str) -> str:
    try:
        parsed = json.loads(text)
    except ValueError:
        return text
    if not isinstance(parsed, dict | list):
        return text
    return json.dumps(parsed, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
