"""AHE-016 meta middleware: the original NexAU middleware plus the recorder."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from nexau.archs.llm.llm_config import LLMConfig
from nexau.archs.main_sub.execution.hooks import AfterToolHookInput, MiddlewareManager
from nexau.archs.sandbox.local_sandbox import LocalSandbox
from nexau.archs.tool.builtin.file_tools.read_file import MAX_LINE_LENGTH

import ahe_runner.meta.middleware as middleware_module
from ahe_runner.meta import guarded_tools
from ahe_runner.meta.middleware import (
    AheContextCompactionMiddleware,
    AheLongToolOutputMiddleware,
    DebuggerAccessRecorderMiddleware,
)
from ahe_runner.meta.tools import _debugger_accesses

from .meta_fakes import FakeAgentState


def _hook(
    tool_name: str,
    output: object,
    *,
    tool_input: dict[str, Any] | None = None,
    agent_state: object | None = None,
    sandbox: object | None = None,
) -> AfterToolHookInput:
    return AfterToolHookInput(
        agent_state=agent_state or SimpleNamespace(),  # type: ignore[arg-type]
        sandbox=sandbox,  # type: ignore[arg-type]
        tool_name=tool_name,
        tool_call_id="call_0123456789",
        tool_input=tool_input or {},
        tool_output=output,
    )


def _long_output(tmp_path: Path, **overrides: Any) -> AheLongToolOutputMiddleware:
    params: dict[str, Any] = {
        "max_output_chars": 10_000,
        "head_lines": 50,
        "tail_lines": 30,
        "head_chars": 5_000,
        "tail_chars": 5_000,
        "temp_dir": str(tmp_path / "tool_outputs"),
        "bypass_tool_names": ["run_shell_command", "LoadSkill", "complete_task"],
    }
    params.update(overrides)
    return AheLongToolOutputMiddleware(**params)


def _debugger_state(tmp_path: Path) -> FakeAgentState:
    evidence = tmp_path / "evidence"
    scratch = tmp_path / "scratch"
    evidence.mkdir(exist_ok=True)
    scratch.mkdir(exist_ok=True)
    return FakeAgentState(
        {
            "meta_role": "debugger",
            "meta_read_roots": {"evidence": str(evidence)},
            "meta_write_roots": {"scratch": str(scratch)},
            "meta_default_root": "evidence",
        },
        work_dir=tmp_path,
    )


def test_long_output_measures_and_saves_the_text_itself(tmp_path: Path) -> None:
    text = "\n".join(f"{index}| trace event {index}" for index in range(1, 2_001))
    middleware = _long_output(tmp_path)
    sandbox = LocalSandbox(sandbox_id="test", work_dir=str(tmp_path))
    result = middleware.after_tool(
        _hook(
            "read_file",
            {"content": text, "returnDisplay": "Read 2000 lines"},
            sandbox=sandbox,
        )
    )
    output = result.tool_output
    assert isinstance(output, dict)
    shown = output["content"]
    assert "lines omitted] ..." in shown
    assert shown.startswith("1| trace event 1\n")
    saved = list((tmp_path / "tool_outputs").iterdir())
    assert len(saved) == 1
    # The saved copy is the text itself, with real line breaks, not JSON.
    assert saved[0].read_text(encoding="utf-8") == text
    assert str(saved[0]) in shown


def test_long_output_leaves_short_and_bypassed_results_alone(tmp_path: Path) -> None:
    middleware = _long_output(tmp_path)
    short = middleware.after_tool(_hook("read_file", {"content": "x" * 9_000}))
    assert short.tool_output is None
    for name in ("run_shell_command", "LoadSkill", "complete_task"):
        bypassed = middleware.after_tool(_hook(name, {"content": "y" * 50_000}))
        assert bypassed.tool_output is None
    assert not (tmp_path / "tool_outputs").exists()


def test_long_output_falls_back_to_json_for_other_shapes() -> None:
    assert (
        AheLongToolOutputMiddleware._serialize({"items": [1, 2]})
        == '{\n  "items": [\n    1,\n    2\n  ]\n}'
    )
    assert AheLongToolOutputMiddleware._serialize({"result": "plain"}) == "plain"


def test_emergency_summary_keeps_campaign_model_settings(monkeypatch) -> None:
    seen: dict[str, Any] = {}

    class FakeCaller:
        def __init__(self, client, config, **kwargs):  # type: ignore[no-untyped-def]
            seen["config"] = config
            seen["init"] = kwargs

        def call_llm(self, messages, **kwargs):  # type: ignore[no-untyped-def]
            seen["call"] = kwargs
            return SimpleNamespace(content="  summary text  ")

    monkeypatch.setattr(middleware_module, "LLMCaller", FakeCaller)
    middleware = AheContextCompactionMiddleware(
        max_context_tokens=220_000,
        auto_compact=True,
        threshold=0.75,
        compaction_strategy="tool_result_compaction",
        keep_iterations=10,
    )
    config = LLMConfig(
        model="campaign-model",
        base_url="http://127.0.0.1:1",
        api_key="key",
        temperature=0.7,
        max_tokens=32_000,
        stream=True,
    )
    params = SimpleNamespace(
        llm_config=config,
        openai_client=object(),
        agent_state=None,
        shutdown_event=None,
    )
    summarize = middleware._build_emergency_summarize_fn(params)  # type: ignore[arg-type]
    assert summarize([], "summarize", 1_234) == "summary text"
    assert seen["config"].temperature == 0.7
    assert seen["config"].max_tokens == 32_000
    assert seen["config"].stream is False
    assert config.stream is True
    assert seen["call"]["max_tokens"] is None


def test_recorder_counts_read_lines_and_skips_cut_ones(tmp_path: Path) -> None:
    state = _debugger_state(tmp_path)
    long_line = "L" * (MAX_LINE_LENGTH + 10)
    (tmp_path / "evidence" / "trace.md").write_text(
        f"alpha\nbeta\n{long_line}\ngamma\n", encoding="utf-8"
    )
    output = guarded_tools.read_file("trace.md", agent_state=state)  # type: ignore[arg-type]
    assert "... [truncated]" in output["content"]
    DebuggerAccessRecorderMiddleware().after_tool(
        _hook(
            "read_file",
            output,
            tool_input={"file_path": "trace.md"},
            agent_state=state,
        )
    )
    assert _debugger_accesses(state) == [  # type: ignore[arg-type]
        {"artifact": "evidence:trace.md", "mode": "lines", "start": 1, "end": 2},
        {"artifact": "evidence:trace.md", "mode": "lines", "start": 4, "end": 4},
    ]


def test_recorder_counts_search_hits_only_inside_citable_roots(
    tmp_path: Path,
) -> None:
    state = _debugger_state(tmp_path)
    (tmp_path / "evidence" / "sub").mkdir()
    (tmp_path / "evidence" / "sub" / "a.md").write_text(
        "one\nKEY two\nthree KEY\n", encoding="utf-8"
    )
    (tmp_path / "scratch" / "notes.md").write_text("KEY\n", encoding="utf-8")
    recorder = DebuggerAccessRecorderMiddleware()
    for dir_path in (None, "scratch:"):
        output = guarded_tools.search_file_content(
            "KEY",
            dir_path=dir_path,
            agent_state=state,  # type: ignore[arg-type]
        )
        recorder.after_tool(
            _hook(
                "search_file_content",
                output,
                tool_input={"pattern": "KEY", "dir_path": dir_path},
                agent_state=state,
            )
        )
    assert _debugger_accesses(state) == [  # type: ignore[arg-type]
        {"artifact": "evidence:sub/a.md", "mode": "lines", "start": 2, "end": 3},
    ]


def test_recorder_ignores_partial_lines_at_omission_markers(tmp_path: Path) -> None:
    state = _debugger_state(tmp_path)
    (tmp_path / "evidence" / "t.md").write_text("x\n", encoding="utf-8")
    content = (
        "1| first\n2| second-partial\n... [900 chars omitted] ...\n"
        "7| tail-partial\n8| last\n\n... [3 lines omitted] ...\n12| after"
    )
    DebuggerAccessRecorderMiddleware().after_tool(
        _hook(
            "read_file",
            {"content": content},
            tool_input={"file_path": "evidence:t.md"},
            agent_state=state,
        )
    )
    ranges = [
        (item["start"], item["end"])
        for item in _debugger_accesses(state)  # type: ignore[arg-type]
    ]
    assert ranges == [(1, 1), (8, 8), (12, 12)]


def test_recorder_search_forgets_the_file_after_a_cut(tmp_path: Path) -> None:
    state = _debugger_state(tmp_path)
    (tmp_path / "evidence" / "a.md").write_text("x\n", encoding="utf-8")
    content = (
        'Found 3 matches for pattern "K":\n---\nFile: a.md\nL1: K\n'
        "... [10 lines omitted] ...\nL9: K\n---\nFile: a.md\nL5: K\n---"
    )
    DebuggerAccessRecorderMiddleware().after_tool(
        _hook(
            "search_file_content",
            {"content": content},
            tool_input={"pattern": "K"},
            agent_state=state,
        )
    )
    ranges = [
        (item["start"], item["end"])
        for item in _debugger_accesses(state)  # type: ignore[arg-type]
    ]
    assert ranges == [(1, 1), (5, 5)]


def test_recorder_ignores_other_roles_errors_and_tools(tmp_path: Path) -> None:
    state = _debugger_state(tmp_path)
    (tmp_path / "evidence" / "a.md").write_text("x\n", encoding="utf-8")
    recorder = DebuggerAccessRecorderMiddleware()
    good = {"content": "1| x"}
    recorder.after_tool(
        _hook("read_file", {**good, "error": {"message": "no"}}, agent_state=state)
    )
    recorder.after_tool(
        _hook(
            "run_shell_command",
            good,
            tool_input={"file_path": "a.md"},
            agent_state=state,
        )
    )
    recorder.after_tool(
        _hook(
            "read_file",
            good,
            tool_input={"file_path": "/etc/passwd"},
            agent_state=state,
        )
    )
    state.context["meta_role"] = "evolve"
    recorder.after_tool(
        _hook("read_file", good, tool_input={"file_path": "a.md"}, agent_state=state)
    )
    assert _debugger_accesses(state) == []  # type: ignore[arg-type]


def test_recorder_sees_the_output_after_long_output_truncation(
    tmp_path: Path,
) -> None:
    state = _debugger_state(tmp_path)
    lines = [f"event {index} " + "z" * 90 for index in range(1, 301)]
    (tmp_path / "evidence" / "trace.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )
    manager = MiddlewareManager(
        [DebuggerAccessRecorderMiddleware(), _long_output(tmp_path)]
    )
    output = guarded_tools.read_file("trace.md", agent_state=state)  # type: ignore[arg-type]
    hook = _hook(
        "read_file",
        output,
        tool_input={"file_path": "trace.md"},
        agent_state=state,
        sandbox=LocalSandbox(sandbox_id="test", work_dir=str(tmp_path)),
    )
    shown = manager.run_after_tool(hook, output)
    assert "lines omitted" in shown["content"]
    ranges = [
        (item["start"], item["end"])
        for item in _debugger_accesses(state)  # type: ignore[arg-type]
    ]
    assert ranges == [(1, 50), (271, 300)]
