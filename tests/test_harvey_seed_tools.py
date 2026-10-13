"""Offline checks for the Harvey workspace handle and the seed harness tools.

The tools run against a LAB ``Sandbox`` subclass with LAB's own path rules over
``tmp_path``; no podman, model, or network is involved.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest
from harvey_ahe.workspace import RESOURCE_KEY, WorkspaceHandle, handle_for
from lab_core.sandbox.sandbox import ExecResult

from ahe_runner.harness_imports import HarnessImportScope

from .harvey_fakes import (
    HARNESS_DIR,
    ExecCall,
    FakeAgentState,
    FakeSandbox,
    started_sandbox,
)

TOOL_NAMES = ("bash", "edit", "finish", "glob", "grep", "read", "write")


@pytest.fixture(scope="module")
def tools() -> Iterator[SimpleNamespace]:
    import importlib

    with HarnessImportScope(HARNESS_DIR):
        modules = {
            name: importlib.import_module(f"tools.{name}") for name in TOOL_NAMES
        }
        yield SimpleNamespace(**modules)


def _bound(
    tmp_path: Path,
    *,
    documents: dict[str, str] | None = None,
    exec_handler=None,
    shell_timeout: int = 60,
) -> tuple[FakeSandbox, WorkspaceHandle, FakeAgentState]:
    kwargs = {} if exec_handler is None else {"exec_handler": exec_handler}
    sandbox = started_sandbox(tmp_path, documents=documents, **kwargs)
    handle = WorkspaceHandle(sandbox, shell_timeout=shell_timeout)
    return sandbox, handle, FakeAgentState({RESOURCE_KEY: handle})


# Workspace handle


def test_handle_for_requires_this_sessions_handle(tmp_path: Path) -> None:
    _sandbox, handle, state = _bound(tmp_path)
    assert handle_for(state) is handle
    with pytest.raises(LookupError, match="agent_state"):
        handle_for(None)
    with pytest.raises(LookupError, match="no 'harvey_workspace' resource"):
        handle_for(FakeAgentState())
    with pytest.raises(LookupError, match="no 'harvey_workspace' resource"):
        handle_for(FakeAgentState({RESOURCE_KEY: object()}))


@pytest.mark.parametrize("value", [0, 601])
def test_handle_rejects_out_of_range_shell_timeout(
    tmp_path: Path,
    value: int,
) -> None:
    sandbox = started_sandbox(tmp_path)
    with pytest.raises(ValueError, match="shell_timeout"):
        WorkspaceHandle(sandbox, shell_timeout=value)


def test_handle_exec_timeouts_are_bounded(tmp_path: Path) -> None:
    sandbox, handle, _state = _bound(tmp_path, shell_timeout=45)
    handle.exec("true")
    handle.exec("true", timeout=600)
    assert [call.timeout for call in sandbox.calls] == [45, 600]
    for bad in (0, 601, True, 1.5):
        with pytest.raises(ValueError, match="timeout"):
            handle.exec("true", timeout=bad)  # type: ignore[arg-type]
    assert len(sandbox.calls) == 2


def test_revoked_handle_fails_closed(tmp_path: Path, tools: SimpleNamespace) -> None:
    _sandbox, handle, state = _bound(tmp_path, documents={"memo.txt": "text"})
    handle.revoke()
    assert handle.revoked
    with pytest.raises(LookupError, match="closed"):
        handle.read_file("/workspace/documents/memo.txt")
    with pytest.raises(LookupError, match="closed"):
        handle.exec("true")
    assert tools.read.read("memo.txt", state).startswith("Error: LookupError:")
    assert tools.write.write("x.md", "y", state).startswith("Error: LookupError:")
    assert tools.finish.finish("done", state, deliverables=["memo.md"]) == {
        "status": "error",
        "error": "Error: LookupError: the Harvey workspace for this trial is closed",
    }


def test_handle_path_rules_come_from_lab(tmp_path: Path) -> None:
    _sandbox, handle, _state = _bound(tmp_path)
    with pytest.raises(PermissionError, match="escapes"):
        handle.to_host("/workspace/../etc/passwd")
    with pytest.raises(ValueError, match="not under"):
        handle.to_host("/etc/passwd")
    with pytest.raises(PermissionError, match="write denied"):
        handle.write_file("/workspace/documents/new.txt", "x")
    assert handle.is_writable("/workspace/output/a.md")
    assert handle.is_writable("/workspace/scratch.md")
    assert not handle.is_writable("/workspace/documents/a.md")


def test_finish_latch(tmp_path: Path) -> None:
    _sandbox, handle, _state = _bound(tmp_path)
    assert not handle.finished
    handle.mark_finished("")
    assert handle.finished
    assert handle.finish_summary is None
    handle.mark_finished("all done")
    assert handle.finish_summary == "all done"


# read


def test_read_resolves_relative_paths_and_slices_lines(
    tmp_path: Path,
    tools: SimpleNamespace,
) -> None:
    _sandbox, _handle, state = _bound(
        tmp_path,
        documents={"memo.txt": "l0\nl1\nl2\nl3", "sub/a.txt": "nested"},
    )
    assert tools.read.read("memo.txt", state) == "l0\nl1\nl2\nl3"
    assert tools.read.read("sub/a.txt", state) == "nested"
    assert tools.read.read("/workspace/documents/memo.txt", state, 1, 2) == "l1\nl2"
    assert tools.read.read("memo.txt", state, offset=3) == "l3"
    assert tools.read.read("memo.txt", state, limit=1) == "l0"
    assert tools.read.read("missing.txt", state) == "Error: file not found: missing.txt"
    assert tools.read.read("", state) == "Error: file_path is required"
    assert tools.read.read("/etc/passwd", state).startswith(
        "Error: sandbox path '/etc/passwd' not under"
    )
    assert "is a directory" in tools.read.read("/workspace/documents/sub", state)


def test_read_parses_office_files_inside_the_sandbox(
    tmp_path: Path,
    tools: SimpleNamespace,
) -> None:
    def handler(_sandbox: FakeSandbox, call: ExecCall) -> ExecResult:
        if "broken" in call.command:
            return ExecResult(stdout="", stderr="trace\nBadZipFile: no", returncode=1)
        if "slow" in call.command:
            return ExecResult(stdout="", stderr="", returncode=None, timed_out=True)
        return ExecResult(stdout="parsed text", stderr="", returncode=0)

    sandbox, _handle, state = _bound(
        tmp_path,
        documents={"deal memo.docx": "zip", "broken.pdf": "x", "slow.xlsx": "x"},
        exec_handler=handler,
    )
    assert tools.read.read("deal memo.docx", state) == "parsed text"
    assert sandbox.calls[0] == ExecCall(
        command="parse-doc docx '/workspace/documents/deal memo.docx'",
        cwd="/workspace",
        timeout=120,
    )
    assert tools.read.read("broken.pdf", state) == (
        "Error: failed to parse /workspace/documents/broken.pdf (pdf): BadZipFile: no"
    )
    assert tools.read.read("slow.xlsx", state) == (
        "Error: parser timed out on /workspace/documents/slow.xlsx (xlsx)"
    )


# write and edit


def test_write_defaults_to_output_and_refuses_documents(
    tmp_path: Path,
    tools: SimpleNamespace,
) -> None:
    _sandbox, _handle, state = _bound(tmp_path, documents={"memo.txt": "original"})
    assert tools.write.write("memo/draft.md", "abc", state) == (
        "Wrote 3 bytes to memo/draft.md"
    )
    assert (tmp_path / "output/memo/draft.md").read_text() == "abc"
    assert tools.write.write("/workspace/notes.txt", "n", state).startswith("Wrote")
    assert (tmp_path / "workspace/notes.txt").read_text() == "n"
    assert tools.write.write("/workspace/documents/memo.txt", "x", state) == (
        "SecurityError: write denied: /workspace/documents/memo.txt is read-only "
        "(documents) or outside /workspace"
    )
    assert (tmp_path / "documents/memo.txt").read_text() == "original"
    assert tools.write.write("../../escape.txt", "x", state).startswith(
        "SecurityError: path escapes sandbox mount"
    )
    assert not (tmp_path.parent / "escape.txt").exists()


def test_edit_replaces_exact_text_in_writable_files(
    tmp_path: Path,
    tools: SimpleNamespace,
) -> None:
    _sandbox, _handle, state = _bound(tmp_path, documents={"memo.txt": "a a"})
    (tmp_path / "output/out.md").write_text("one two two", encoding="utf-8")
    assert tools.edit.edit("out.md", "one", "1", state) == (
        "Replaced 1 occurrence(s) in out.md"
    )
    assert tools.edit.edit("out.md", "two", "2", state).startswith(
        "Error: old_string found 2 times"
    )
    assert tools.edit.edit("out.md", "two", "2", state, replace_all=True) == (
        "Replaced 2 occurrence(s) in out.md"
    )
    assert (tmp_path / "output/out.md").read_text() == "1 2 2"
    assert tools.edit.edit("out.md", "zzz", "y", state) == (
        "Error: old_string not found in out.md"
    )
    assert tools.edit.edit("memo.txt", "a", "b", state).startswith(
        "SecurityError: write denied"
    )
    assert tools.edit.edit("nope.md", "a", "b", state) == (
        "Error: file not found: nope.md"
    )


# glob and grep


def test_glob_lists_newest_first_and_ignores_symlink_escapes(
    tmp_path: Path,
    tools: SimpleNamespace,
) -> None:
    _sandbox, _handle, state = _bound(
        tmp_path,
        documents={"old.txt": "o", "new.txt": "n", "sub/deep.txt": "d"},
    )
    os.utime(tmp_path / "documents/old.txt", (1, 1))
    secret = tmp_path / "secret.txt"
    secret.write_text("outside", encoding="utf-8")
    (tmp_path / "documents/link.txt").symlink_to(secret)
    listing = tools.glob.glob("**/*.txt", state).splitlines()
    assert listing[-1] == "old.txt"
    assert set(listing) == {"old.txt", "new.txt", "sub/deep.txt"}
    assert tools.glob.glob("*.md", state) == (
        "No files matching '*.md' in /workspace/documents"
    )
    assert tools.glob.glob("*", state, path="missing") == (
        "Error: path does not exist: missing"
    )


def test_glob_caps_matches(tmp_path: Path, tools: SimpleNamespace) -> None:
    documents = {f"f{index:03}.txt": "x" for index in range(105)}
    _sandbox, _handle, state = _bound(tmp_path, documents=documents)
    assert len(tools.glob.glob("*.txt", state).splitlines()) == (
        tools.glob.MAX_MATCHES
    )


def test_grep_modes_and_symlink_escape(
    tmp_path: Path,
    tools: SimpleNamespace,
) -> None:
    _sandbox, _handle, state = _bound(
        tmp_path,
        documents={"a.txt": "Indemnity\nnone\nindemnity cap", "b.md": "Indemnity"},
    )
    secret = tmp_path / "secret.txt"
    secret.write_text("Indemnity", encoding="utf-8")
    (tmp_path / "documents/link.txt").symlink_to(secret)
    assert set(tools.grep.grep("Indemnity", state).splitlines()) == {"a.txt", "b.md"}
    assert tools.grep.grep("(?i)indemnity", state, glob="*.txt", output_mode="count") == (
        "a.txt: 2"
    )
    assert tools.grep.grep("cap", state, output_mode="content") == (
        "a.txt:3: indemnity cap"
    )
    assert tools.grep.grep("absent", state) == "No matches for 'absent'"
    assert tools.grep.grep("(", state).startswith("Error: invalid regex:")


# bash


def test_bash_reports_stderr_exit_code_and_timeouts(
    tmp_path: Path,
    tools: SimpleNamespace,
) -> None:
    def handler(_sandbox: FakeSandbox, call: ExecCall) -> ExecResult:
        if call.command == "fail":
            return ExecResult(stdout="out", stderr="bad", returncode=2)
        if call.command == "hang":
            return ExecResult(stdout="", stderr="", returncode=None, timed_out=True)
        if call.command == "quiet":
            return ExecResult(stdout="", stderr="", returncode=0)
        return ExecResult(stdout="ok\n", stderr="", returncode=0)

    sandbox, _handle, state = _bound(tmp_path, exec_handler=handler, shell_timeout=30)
    assert tools.bash.bash("echo ok", state) == "ok\n"
    assert sandbox.calls[0] == ExecCall("echo ok", "/workspace", 30)
    assert tools.bash.bash("fail", state) == "out\nSTDERR:\nbad\n(exit code 2)"
    assert tools.bash.bash("hang", state) == "Error: command timed out after 30s\n"
    assert tools.bash.bash("quiet", state) == "(no output)"
    assert tools.bash.bash("", state) == "Error: command is required"


# finish


def test_finish_rejects_missing_deliverables_at_most_twice(
    tmp_path: Path,
    tools: SimpleNamespace,
) -> None:
    _sandbox, handle, state = _bound(tmp_path)
    for expected in (1, 2):
        result = tools.finish.finish("done", state, deliverables=["memo.docx"])
        assert result["status"] == "error"
        assert "memo.docx" in result["error"]
        assert state.values[tools.finish.REJECTIONS_KEY] == expected
        assert not handle.finished
    assert tools.finish.finish("done", state, deliverables=["memo.docx"]) == (
        "Finished."
    )
    assert handle.finished
    assert handle.finish_summary == "done"


@pytest.mark.parametrize(
    "path",
    ["memo.md", "output/memo.md", "/workspace/output/memo.md", "./memo.md"],
)
def test_finish_accepts_existing_output_paths(
    tmp_path: Path,
    tools: SimpleNamespace,
    path: str,
) -> None:
    _sandbox, handle, state = _bound(tmp_path)
    (tmp_path / "output/memo.md").write_text("m", encoding="utf-8")
    assert tools.finish.finish("ok", state, deliverables=[path]) == "Finished."
    assert handle.finished


@pytest.mark.parametrize(
    "deliverables",
    [["../documents/memo.txt"], ["/workspace/documents/memo.txt"], [""], "memo.md"],
)
def test_finish_counts_non_output_or_malformed_paths_as_missing(
    tmp_path: Path,
    tools: SimpleNamespace,
    deliverables: object,
) -> None:
    _sandbox, handle, state = _bound(tmp_path, documents={"memo.txt": "m"})
    (tmp_path / "output/memo.md").write_text("m", encoding="utf-8")
    result = tools.finish.finish("x", state, deliverables=deliverables)
    assert isinstance(result, dict) and result["status"] == "error"
    assert not handle.finished


def test_finish_without_deliverables_finishes(
    tmp_path: Path,
    tools: SimpleNamespace,
) -> None:
    _sandbox, handle, state = _bound(tmp_path)
    assert tools.finish.finish("nothing listed", state) == "Finished."
    assert handle.finished
