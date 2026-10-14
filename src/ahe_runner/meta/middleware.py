"""Meta-agent middleware (AHE-016).

The original AHE wires NexAU's ``LongToolOutputMiddleware`` and
``ContextCompactionMiddleware`` into its meta agents. The runner uses them
with two small, documented adjustments, and adds a recorder that turns the
debugger's file reads into citation coverage.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from nexau.archs.main_sub.execution.hooks import (
    AfterToolHookInput,
    HookResult,
    Middleware,
    ModelCallParams,
)
from nexau.archs.main_sub.execution.llm_caller import LLMCaller
from nexau.archs.main_sub.execution.middleware.context_compaction import (
    ContextCompactionMiddleware,
)
from nexau.archs.main_sub.execution.middleware.long_tool_output import (
    LongToolOutputMiddleware,
)
from nexau.archs.tool.builtin.file_tools.read_file import MAX_LINE_LENGTH
from nexau.core.messages import Message

from .guarded_tools import (
    PathDenied,
    citable_artifact,
    resolve_path,
    search_directory,
)
from .tools import _context_value, _record_debugger_access


class AheLongToolOutputMiddleware(LongToolOutputMiddleware):
    """The original long-output middleware, measuring the text itself.

    NexAU measures and saves ``json.dumps(output, indent=2)``. That saved copy
    holds the tool's text on one line with escaped newlines, so reading it back
    with ``read_file`` is impractical. When a tool returns a text field, measure
    and save that text instead.
    """

    @staticmethod
    def _serialize(output: object) -> str:
        if isinstance(output, dict):
            for key in ("content", "result"):
                value = output.get(key)
                if isinstance(value, str):
                    return value
        return LongToolOutputMiddleware._serialize(output)


class AheContextCompactionMiddleware(ContextCompactionMiddleware):
    """The original compaction middleware with campaign model settings kept.

    NexAU's emergency summary call forces temperature 0 and its own
    ``max_tokens``. The runner's model client accepts only the campaign's
    values, so the summary call keeps them.
    """

    def _build_emergency_summarize_fn(
        self,
        params: ModelCallParams,
    ) -> Callable[[list[Message], str, int], str]:
        if params.llm_config is None:
            raise RuntimeError(
                "llm_config is required for emergency compaction summarization"
            )
        summary_llm_config = params.llm_config.copy()
        summary_llm_config.stream = False
        llm_caller = LLMCaller(
            params.openai_client,
            summary_llm_config,
            retry_attempts=1,
            middleware_manager=None,
        )

        def summarize_fn(messages: list[Message], prompt: str, max_tokens: int) -> str:
            del max_tokens  # the campaign max_tokens applies
            summary_messages = [*messages, Message.user(prompt)]
            response = llm_caller.call_llm(
                summary_messages,
                max_tokens=None,
                force_stop_reason=None,
                agent_state=params.agent_state,
                tool_call_mode="xml",
                tools=None,
                openai_client=params.openai_client,
                shutdown_event=params.shutdown_event,
            )
            text = (response.content or "").strip() if response else ""
            return text if text else "NONE"

        return summarize_fn


_READ_LINE = re.compile(r"^\s*(\d+)\| ")
_SEARCH_FILE = re.compile(r"^File: (.+)$")
_SEARCH_LINE = re.compile(r"^L(\d+): ")
_OMITTED = re.compile(r"^\.\.\. \[\d+ (lines|chars) omitted\] \.\.\.$")
# read_file cuts lines longer than MAX_LINE_LENGTH and appends this suffix.
_LINE_CUT_SUFFIX = "... [truncated]"


class DebuggerAccessRecorderMiddleware(Middleware):
    """Record which evidence lines the debugger actually saw.

    Place it first in the middleware list: ``after_tool`` runs in reverse
    order, so it sees the output after long-output truncation, exactly as the
    model does. Only ``read_file`` and ``search_file_content`` results count,
    and only for files in the citable roots (``evidence``, ``raw_evaluation``).
    """

    def after_tool(self, hook_input: AfterToolHookInput) -> HookResult:
        agent_state = hook_input.agent_state
        if str(_context_value(agent_state, "meta_role")) != "debugger":
            return HookResult.no_changes()
        output = hook_input.tool_output
        if not isinstance(output, dict) or "error" in output:
            return HookResult.no_changes()
        content = output.get("content")
        if not isinstance(content, str):
            return HookResult.no_changes()
        tool_input = hook_input.tool_input or {}
        if hook_input.tool_name == "read_file":
            hits = _read_file_hits(agent_state, tool_input, content)
        elif hook_input.tool_name == "search_file_content":
            hits = _search_hits(agent_state, tool_input, content)
        else:
            return HookResult.no_changes()
        for artifact, numbers in hits.items():
            for start, end in _ranges(numbers):
                _record_debugger_access(
                    agent_state,
                    artifact=artifact,
                    mode="lines",
                    start=start,
                    end=end,
                )
        return HookResult.no_changes()


def _read_file_hits(
    agent_state: Any,
    tool_input: dict[str, Any],
    content: str,
) -> dict[str, set[int]]:
    try:
        path, _ = resolve_path(
            agent_state,
            str(tool_input.get("file_path", "")),
            write=False,
        )
    except PathDenied:
        return {}
    artifact = citable_artifact(agent_state, path)
    if artifact is None:
        return {}
    numbers = {
        number for _, _, number in _parse_lines(content, _READ_LINE, search=False)
    }
    return {artifact: numbers} if numbers else {}


def _search_hits(
    agent_state: Any,
    tool_input: dict[str, Any],
    content: str,
) -> dict[str, set[int]]:
    dir_path = tool_input.get("dir_path")
    try:
        directory = search_directory(
            agent_state,
            None if dir_path is None else str(dir_path),
        )
    except PathDenied:
        return {}
    # Only runner-written, read-only evidence trees are trusted to name files.
    if citable_artifact(agent_state, directory / "_") is None:
        return {}
    hits: dict[str, set[int]] = {}
    for _, file_name, number in _parse_lines(content, _SEARCH_LINE, search=True):
        if file_name is None or os.path.isabs(file_name):
            continue
        candidate = Path(os.path.realpath(directory / file_name))
        if not candidate.is_relative_to(directory):
            continue
        artifact = citable_artifact(agent_state, candidate)
        if artifact is not None:
            hits.setdefault(artifact, set()).add(number)
    return hits


def _parse_lines(
    content: str,
    pattern: re.Pattern[str],
    *,
    search: bool,
) -> list[tuple[int, str | None, int]]:
    """Return ``(output_line_index, search_file, line_number)`` entries.

    Long-output truncation inserts ``... [N lines omitted] ...`` or
    ``... [N chars omitted] ...``. A chars cut leaves a partial line on each
    side; neither counts. After any cut, a search loses track of its file.
    A ``read_file`` line shortened to ``MAX_LINE_LENGTH`` does not count.
    """

    lines = content.split("\n")
    entries: list[tuple[int, str | None, int]] = []
    current_file: str | None = None
    started = not search
    skip_index = -1
    for index, line in enumerate(lines):
        if not started:
            started = line == "---"
            continue
        marker = _OMITTED.match(line)
        if marker is not None:
            current_file = None
            if marker.group(1) == "chars":
                entries = [entry for entry in entries if entry[0] != index - 1]
                skip_index = index + 1
            continue
        if index == skip_index:
            continue
        if search:
            file_match = _SEARCH_FILE.match(line)
            if file_match is not None:
                current_file = file_match.group(1)
                continue
            if line == "---":
                current_file = None
                continue
            if current_file is None:
                continue
        match = pattern.match(line)
        if match is None:
            continue
        text = line[match.end() :]
        if (
            not search
            and text.endswith(_LINE_CUT_SUFFIX)
            and len(text) == MAX_LINE_LENGTH + len(_LINE_CUT_SUFFIX)
        ):
            continue
        entries.append((index, current_file, int(match.group(1))))
    return entries


def _ranges(numbers: set[int]) -> list[tuple[int, int]]:
    ordered = sorted(numbers)
    ranges: list[tuple[int, int]] = []
    for number in ordered:
        if ranges and number == ranges[-1][1] + 1:
            ranges[-1] = (ranges[-1][0], number)
        else:
            ranges.append((number, number))
    return ranges
