"""AHE-016 guarded tools: path guard, original file tools, and the Docker shell."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from ahe_runner.meta import guarded_tools as g
from ahe_runner.meta.guarded_tools import MetaRoot, PathDenied

from .meta_fakes import FakeAgentState

SHELL_IMAGE = "ahe-meta-shell:py313-v2"


def _roots(tmp_path: Path) -> tuple[Path, Path, Path]:
    evidence = tmp_path / "evidence"
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    for path in (evidence, workspace, outside):
        path.mkdir(exist_ok=True)
    (evidence / "trace.md").write_text("alpha\nbeta KEY\ngamma\n", encoding="utf-8")
    (outside / "secret.txt").write_text("SECRET KEY\n", encoding="utf-8")
    return evidence, workspace, outside


def _state(
    tmp_path: Path,
    *,
    role: str = "evolve",
    default: str = "workspace",
    **extra: object,
) -> FakeAgentState:
    evidence, workspace, _ = _roots(tmp_path)
    context: dict[str, object] = {
        "meta_role": role,
        "meta_read_roots": {"evidence": str(evidence)},
        "meta_write_roots": {"workspace": str(workspace)},
        "meta_default_root": default,
        "meta_tool_output_dir": str(tmp_path / "tool_outputs"),
    }
    context.update(extra)
    return FakeAgentState(context, work_dir=tmp_path / "sandbox")


def test_paths_resolve_by_root_name_absolute_or_default_root(tmp_path: Path) -> None:
    state = _state(tmp_path)
    evidence = tmp_path / "evidence"
    for raw in ("evidence:trace.md", "evidence:/trace.md", str(evidence / "trace.md")):
        path, root = g.resolve_path(state, raw, write=False)  # type: ignore[arg-type]
        assert (path, root.name) == (evidence / "trace.md", "evidence")
    path, root = g.resolve_path(state, "new/file.py", write=True)  # type: ignore[arg-type]
    assert (path, root.name) == (
        tmp_path / "workspace" / "new" / "file.py",
        "workspace",
    )
    assert g.canonical_artifact(state, str(evidence / "trace.md")) == (  # type: ignore[arg-type]
        "evidence:trace.md"
    )
    assert g.canonical_artifact(state, "unknown:thing") == "unknown:thing"  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "raw",
    [
        "/etc/passwd",
        "evidence:../outside/secret.txt",
        "../outside/secret.txt",
        "",
        "evidence:trace.md\nx",
        "evidence:trace\u2028.md",
    ],
)
def test_paths_outside_the_roots_are_denied(tmp_path: Path, raw: str) -> None:
    state = _state(tmp_path)
    with pytest.raises(PathDenied):
        g.resolve_path(state, raw, write=False)  # type: ignore[arg-type]


def test_symlinks_cannot_escape_the_roots(tmp_path: Path) -> None:
    state = _state(tmp_path)
    evidence = tmp_path / "evidence"
    (evidence / "link.txt").symlink_to(tmp_path / "outside" / "secret.txt")
    (evidence / "linkdir").symlink_to(tmp_path / "outside")
    denied = g.read_file("evidence:link.txt", agent_state=state)  # type: ignore[arg-type]
    assert denied["error"]["type"] == "PATH_NOT_ALLOWED"
    assert "Allowed roots" in denied["content"]
    assert "SECRET" not in denied["content"]
    search = g.search_file_content(
        "KEY",
        dir_path="evidence:",
        agent_state=state,  # type: ignore[arg-type]
    )
    assert search["error"]["type"] == "PATH_NOT_ALLOWED"
    assert "symlink" in search["content"]
    found = g.glob("**/*", dir_path="evidence:", agent_state=state)  # type: ignore[arg-type]
    assert found["error"]["type"] == "PATH_NOT_ALLOWED"


def test_write_rules_cover_read_only_roots_git_and_the_root_itself(
    tmp_path: Path,
) -> None:
    state = _state(tmp_path)
    workspace = tmp_path / "workspace"
    (workspace / ".git").mkdir()
    for raw in ("evidence:new.txt", "workspace:.git/config", "workspace:"):
        result = g.write_file(raw, "x", agent_state=state)  # type: ignore[arg-type]
        assert result["error"]["type"] == "PATH_NOT_ALLOWED", raw
    (workspace / "escape").symlink_to(tmp_path / "evidence")
    escaped = g.write_file("escape/trace.md", "x", agent_state=state)  # type: ignore[arg-type]
    assert "read-only root 'evidence'" in escaped["content"]
    assert (tmp_path / "evidence" / "trace.md").read_text().startswith("alpha")


def test_write_replace_and_delete_inside_a_writable_root(tmp_path: Path) -> None:
    state = _state(tmp_path)
    workspace = tmp_path / "workspace"
    created = g.write_file("pkg/mod.py", "value = 1\n", agent_state=state)  # type: ignore[arg-type]
    assert "error" not in created
    assert (workspace / "pkg" / "mod.py").read_text() == "value = 1\n"
    replaced = g.replace(
        "workspace:pkg/mod.py",
        "value = 1",
        "value = 2",
        agent_state=state,  # type: ignore[arg-type]
    )
    assert "error" not in replaced
    assert (workspace / "pkg" / "mod.py").read_text() == "value = 2\n"
    not_empty = g.delete_path("pkg", agent_state=state)  # type: ignore[arg-type]
    assert not_empty["error"]["type"] == "DIRECTORY_NOT_EMPTY"
    assert "error" not in g.delete_path("pkg/mod.py", agent_state=state)  # type: ignore[arg-type]
    assert "error" not in g.delete_path("pkg", agent_state=state)  # type: ignore[arg-type]
    assert not (workspace / "pkg").exists()
    missing = g.delete_path("pkg", agent_state=state)  # type: ignore[arg-type]
    assert missing["error"]["type"] == "FILE_NOT_FOUND"
    # Deleting a link removes the link, never its target.
    (workspace / "link").symlink_to(tmp_path / "evidence" / "trace.md")
    assert "error" not in g.delete_path("link", agent_state=state)  # type: ignore[arg-type]
    assert (tmp_path / "evidence" / "trace.md").is_file()
    progress = state.global_storage.values
    assert any("progress" in key for key in progress)


def test_reads_lists_and_searches_use_the_original_tool_output(
    tmp_path: Path,
) -> None:
    state = _state(tmp_path, role="debugger", default="evidence")
    read = g.read_file("trace.md", offset=1, limit=1, agent_state=state)  # type: ignore[arg-type]
    assert read["content"].endswith("2| beta KEY")
    assert "Showing lines 2-2 of 3" in read["content"]
    listing = g.list_directory("evidence:", agent_state=state)  # type: ignore[arg-type]
    assert "trace.md" in listing["content"]
    found = g.search_file_content("KEY", agent_state=state)  # type: ignore[arg-type]
    assert "File: trace.md\nL2: beta KEY" in found["content"]
    assert "SECRET" not in found["content"]
    globbed = g.glob("*.md", agent_state=state)  # type: ignore[arg-type]
    assert "trace.md" in globbed["content"]


def test_patterns_and_binary_documents_are_refused_with_guidance(
    tmp_path: Path,
) -> None:
    state = _state(tmp_path, meta_shell={"backend": "docker", "image": SHELL_IMAGE})
    (tmp_path / "evidence" / "memo.docx").write_bytes(b"PK\x03\x04binary")
    binary = g.read_file("evidence:memo.docx", agent_state=state)  # type: ignore[arg-type]
    assert binary["error"]["type"] == "BINARY_FILE"
    assert "readable_artifacts" in binary["content"]
    assert "run_shell_command" in binary["content"]
    dash = g.search_file_content("-e", agent_state=state)  # type: ignore[arg-type]
    assert dash["error"]["type"] == "INVALID_PATTERN"
    up = g.glob("../*", agent_state=state)  # type: ignore[arg-type]
    assert up["error"]["type"] == "INVALID_PATTERN"


def test_explorer_research_waits_for_the_skill_scaffold(tmp_path: Path) -> None:
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    deliverable = knowledge / "SKILL.md"
    state = _state(
        tmp_path,
        role="source_explorer",
        default="evidence",
        required_deliverable=str(deliverable),
    )
    state.context["meta_write_roots"] = {"knowledge": str(knowledge)}
    blocked = g.read_file("evidence:trace.md", agent_state=state)  # type: ignore[arg-type]
    assert blocked["error"]["type"] == "PROGRESS_GATE"
    short = g.write_file(str(deliverable), "too short", agent_state=state)  # type: ignore[arg-type]
    assert short["error"]["type"] == "PROGRESS_GATE"
    assert not deliverable.exists()
    scaffold = "# Skill\n" + "x" * 1_600
    assert "error" not in g.write_file(str(deliverable), scaffold, agent_state=state)  # type: ignore[arg-type]
    assert "error" not in g.read_file("evidence:trace.md", agent_state=state)  # type: ignore[arg-type]
    shrunk = g.replace(
        str(deliverable),
        "x" * 1_600,
        "y",
        agent_state=state,  # type: ignore[arg-type]
    )
    assert shrunk["error"]["type"] == "PROGRESS_GATE"
    assert deliverable.read_text() == scaffold


def test_shell_mounts_are_parent_first_and_protect_git(tmp_path: Path) -> None:
    evidence, workspace, _ = _roots(tmp_path)
    (workspace / ".git").mkdir()
    nested = evidence / "nested"
    nested.mkdir()
    mounts = g.shell_mounts(
        [
            MetaRoot("nested", nested, writable=True),
            MetaRoot("evidence", evidence, writable=False),
            MetaRoot("workspace", workspace, writable=True),
            MetaRoot("missing", tmp_path / "missing", writable=False),
        ]
    )
    assert mounts == sorted(
        [
            (str(evidence), True),
            (str(workspace), False),
            (str(nested), False),
            (str(workspace / ".git"), True),
        ],
        key=lambda item: (len(Path(item[0]).parts), item[0]),
    )
    comma = tmp_path / "a,b"
    comma.mkdir()
    with pytest.raises(PathDenied):
        g.shell_mounts([MetaRoot("bad", comma, writable=False)])


def test_docker_argv_is_isolated(tmp_path: Path) -> None:
    argv = g.docker_shell_argv(
        settings={"image": SHELL_IMAGE, "memory": "1g", "pids_limit": 64},
        container="ahe-meta-shell-test",
        cwd=tmp_path,
        mounts=[(str(tmp_path), True), (str(tmp_path / "w"), False)],
        command="echo hi",
    )
    joined = " ".join(argv)
    for flag in (
        "--rm",
        "--network none",
        "--cap-drop ALL",
        "--security-opt no-new-privileges",
        "--read-only",
        "--pids-limit 64",
        "--memory 1g --memory-swap 1g",
        f"--user {os.getuid()}:{os.getgid()}",
        f"-w {tmp_path}",
    ):
        assert flag in joined
    assert f"type=bind,source={tmp_path},target={tmp_path},readonly" in argv
    assert f"type=bind,source={tmp_path / 'w'},target={tmp_path / 'w'}" in argv
    assert argv[-4:] == [SHELL_IMAGE, "bash", "-c", "echo hi"]


def test_shell_is_disabled_without_docker_settings(tmp_path: Path) -> None:
    state = _state(tmp_path)
    result = g.run_shell_command("ls", agent_state=state)  # type: ignore[arg-type]
    assert result["error"]["type"] == "SHELL_DISABLED"


def _shell_image_available() -> bool:
    if shutil.which("docker") is None:
        return False
    probe = subprocess.run(
        ["docker", "image", "inspect", SHELL_IMAGE],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return probe.returncode == 0


@pytest.mark.skipif(not _shell_image_available(), reason="meta shell image missing")
def test_live_docker_shell_sees_only_the_roots(tmp_path: Path) -> None:
    state = _state(
        tmp_path,
        meta_shell={"backend": "docker", "image": SHELL_IMAGE, "timeout_seconds": 120},
    )
    (tmp_path / "tool_outputs").mkdir()
    evidence = tmp_path / "evidence"
    read = g.run_shell_command(
        f"cat {evidence}/trace.md && python3 -c 'import yaml; print(\"yaml ok\")'",
        agent_state=state,  # type: ignore[arg-type]
    )
    assert read["exit_code"] == 0, read
    assert "beta KEY" in read["content"]
    assert "yaml ok" in read["content"]
    write = g.run_shell_command(
        f"echo new > {evidence}/x.txt",
        agent_state=state,  # type: ignore[arg-type]
    )
    assert write["exit_code"] != 0
    assert not (evidence / "x.txt").exists()
    created = g.run_shell_command(
        "echo made > made.txt && pwd",
        agent_state=state,  # type: ignore[arg-type]
    )
    assert created["exit_code"] == 0, created
    assert (tmp_path / "workspace" / "made.txt").read_text() == "made\n"
    hidden = g.run_shell_command(
        f"cat {tmp_path}/outside/secret.txt",
        agent_state=state,  # type: ignore[arg-type]
    )
    assert hidden["exit_code"] != 0
    assert "SECRET" not in hidden["content"]
    offline = g.run_shell_command(
        "python3 -c \"import socket; socket.create_connection(('1.1.1.1', 53), 3)\"",
        agent_state=state,  # type: ignore[arg-type]
    )
    assert offline["exit_code"] != 0
