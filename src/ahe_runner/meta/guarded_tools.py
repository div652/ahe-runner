"""Original AHE file and shell tools behind the runner's path guard (AHE-016).

The file tools call NexAU's built-in implementations (the same code the
original AHE uses) after resolving every path against the agent's allowed
roots. ``run_shell_command`` runs each command in a disposable Docker
container that mounts only those roots.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nexau.archs.main_sub.agent_state import AgentState
from nexau.archs.sandbox.base_sandbox import smart_truncate_output
from nexau.archs.tool.builtin.file_tools.glob_tool import glob as _builtin_glob
from nexau.archs.tool.builtin.file_tools.list_directory import (
    list_directory as _builtin_list_directory,
)
from nexau.archs.tool.builtin.file_tools.read_file import (
    read_file as _builtin_read_file,
)
from nexau.archs.tool.builtin.file_tools.replace import replace as _builtin_replace
from nexau.archs.tool.builtin.file_tools.search_file_content import (
    search_file_content as _builtin_search_file_content,
)
from nexau.archs.tool.builtin.file_tools.write_file import (
    write_file as _builtin_write_file,
)
from nexau.archs.tool.builtin.shell_tools.run_shell_command import (
    _truncate_shell_output,
)

from .tools import (
    _context_value,
    _deliverable_write_error,
    _git,
    _record_deliverable_write,
    _record_mutation,
    _research_gate,
)

# Roots whose files the debugger may cite.
CITABLE_ROOTS = frozenset({"evidence", "raw_evaluation"})
# Mirrors the built-in search and glob default excludes.
_WALK_EXCLUDES = frozenset(
    {
        "node_modules",
        ".git",
        "__pycache__",
        "venv",
        ".venv",
        "dist",
        "build",
        ".tox",
        ".eggs",
    }
)
_BINARY_DOCUMENT_SUFFIXES = frozenset(
    {
        ".docx",
        ".doc",
        ".xlsx",
        ".xlsm",
        ".xls",
        ".pptx",
        ".ppt",
        ".odt",
        ".ods",
        ".odp",
        ".pdf",
        ".zip",
        ".gz",
        ".tgz",
        ".tar",
        ".bz2",
        ".xz",
        ".7z",
        ".whl",
        ".jar",
        ".db",
        ".sqlite",
        ".pyc",
        ".so",
        ".bin",
    }
)
_LINE_BREAKING_CHARACTERS = frozenset("\x85\u2028\u2029\x7f")
_SHELL_READ_LIMIT_BYTES = 8 * 1024 * 1024
# What a ``root:`` prefix looks like, for canonicalizing citations.
_ROOT_PREFIX = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*")


@dataclass(frozen=True)
class MetaRoot:
    name: str
    path: Path
    writable: bool

    @property
    def is_file(self) -> bool:
        return self.path.is_file()

    def contains(self, candidate: str) -> bool:
        base = str(self.path)
        if self.is_file:
            return candidate == base
        return candidate == base or candidate.startswith(base.rstrip(os.sep) + os.sep)

    def relative(self, candidate: str) -> str:
        if candidate == str(self.path):
            return "" if not self.is_file else self.path.name
        return Path(candidate).relative_to(self.path).as_posix()


class PathDenied(Exception):
    """A requested path is outside the agent's allowed roots."""


# ---------------------------------------------------------------------------
# Path guard
# ---------------------------------------------------------------------------


def meta_roots(agent_state: AgentState | None) -> list[MetaRoot]:
    roots: dict[str, MetaRoot] = {}
    for key, writable in (("meta_read_roots", False), ("meta_write_roots", True)):
        value = _optional_context(agent_state, key) or {}
        if not isinstance(value, Mapping):
            raise RuntimeError(f"meta-agent context {key} must be a mapping")
        for name, raw in value.items():
            roots[str(name)] = MetaRoot(
                name=str(name),
                path=Path(os.path.realpath(str(raw))),
                writable=writable,
            )
    return list(roots.values())


def default_root(agent_state: AgentState | None) -> MetaRoot:
    roots = {root.name: root for root in meta_roots(agent_state)}
    name = _optional_context(agent_state, "meta_default_root")
    if name is not None and str(name) in roots:
        return roots[str(name)]
    writable = [root for root in roots.values() if root.writable]
    if writable:
        return writable[0]
    if not roots:
        raise RuntimeError("meta-agent context has no allowed roots")
    return next(iter(roots.values()))


def resolve_path(
    agent_state: AgentState | None,
    raw_path: str,
    *,
    write: bool,
) -> tuple[Path, MetaRoot]:
    """Resolve a model-supplied path to a real path inside an allowed root.

    Accepted forms: ``root:relative/path`` for a known root, an absolute
    path, or a path relative to the agent's default root.
    """

    roots = meta_roots(agent_state)
    joined = _joined_path(agent_state, raw_path, roots)
    candidate = os.path.realpath(joined)
    root = _containing_root(candidate, roots)
    if root is None:
        raise PathDenied(f"path is outside the allowed roots: {raw_path}")
    if write:
        _require_writable(root, candidate, raw_path)
    return Path(candidate), root


def citable_artifact(agent_state: AgentState | None, path: Path) -> str | None:
    """Return ``root:relative`` when a resolved file lies in a citable root."""

    candidate = os.path.realpath(path)
    root = _containing_root(candidate, meta_roots(agent_state))
    if root is None or root.name not in CITABLE_ROOTS:
        return None
    relative = root.relative(candidate)
    if not relative:
        return None
    return f"{root.name}:{relative}"


def canonical_artifact(agent_state: AgentState | None, value: str) -> str:
    """Normalize an artifact reference to ``root:relative`` when possible."""

    text = str(value).strip()
    try:
        roots = meta_roots(agent_state)
        prefix, separator, _ = text.partition(":")
        if (
            separator
            and _ROOT_PREFIX.fullmatch(prefix)
            and prefix not in {root.name for root in roots}
        ):
            return text
        path, root = resolve_path(agent_state, text, write=False)
    except (PathDenied, ValueError, RuntimeError):
        return text
    relative = root.relative(str(path))
    return f"{root.name}:{relative}" if relative else text


def _joined_path(
    agent_state: AgentState | None,
    raw_path: str,
    roots: list[MetaRoot],
) -> str:
    text = str(raw_path)
    if not text.strip():
        raise PathDenied("path must not be empty")
    if _has_control_characters(text):
        raise PathDenied("path must not contain control or line-break characters")
    named = {root.name: root for root in roots}
    prefix, separator, rest = text.partition(":")
    if separator and prefix in named:
        root = named[prefix]
        relative = rest.lstrip("/")
        if relative in {"", "."}:
            return str(root.path)
        if root.is_file:
            raise PathDenied(f"root {prefix!r} is a single file")
        return os.path.join(root.path, relative)
    if os.path.isabs(text):
        return text
    base = default_root(agent_state)
    if base.is_file:
        raise PathDenied(
            "relative paths need a directory root; use an absolute path or "
            "root:relative/path"
        )
    return os.path.join(base.path, text)


def _containing_root(candidate: str, roots: list[MetaRoot]) -> MetaRoot | None:
    matches = [root for root in roots if root.contains(candidate)]
    if not matches:
        return None
    return max(matches, key=lambda root: len(str(root.path)))


def _require_writable(root: MetaRoot, candidate: str, raw_path: str) -> None:
    if not root.writable:
        raise PathDenied(f"path is in read-only root {root.name!r}: {raw_path}")
    if candidate == str(root.path):
        raise PathDenied(f"cannot modify the root directory itself: {raw_path}")
    if ".git" in Path(root.relative(candidate)).parts:
        raise PathDenied(f"paths inside .git cannot be modified: {raw_path}")


def _has_control_characters(text: str) -> bool:
    return any(
        ord(character) < 32 or character in _LINE_BREAKING_CHARACTERS
        for character in text
    )


def _escaping_symlink(directory: Path, roots: list[MetaRoot]) -> str | None:
    """Return the first symlink under ``directory`` that leaves the roots."""

    if not directory.is_dir():
        return None
    for current, dirnames, filenames in os.walk(directory, followlinks=False):
        dirnames[:] = [name for name in dirnames if name not in _WALK_EXCLUDES]
        for name in (*dirnames, *filenames):
            path = os.path.join(current, name)
            if os.path.islink(path):
                target = os.path.realpath(path)
                if _containing_root(target, roots) is None:
                    return path
    return None


def _denied(
    agent_state: AgentState | None,
    message: str,
    *,
    error_type: str = "PATH_NOT_ALLOWED",
) -> dict[str, Any]:
    lines = [message]
    try:
        roots = meta_roots(agent_state)
        default = default_root(agent_state)
    except RuntimeError:
        roots = []
        default = None
    if roots:
        lines.append("Allowed roots (use an absolute path or root:relative/path):")
        for root in sorted(roots, key=lambda item: item.name):
            access = "read-write" if root.writable else "read-only"
            lines.append(f"- {root.name}: {root.path} ({access})")
        if default is not None:
            lines.append(f"Relative paths resolve against {default.path}.")
    text = "\n".join(lines)
    return {
        "content": text,
        "returnDisplay": f"Error: {message}",
        "error": {"message": text, "type": error_type},
    }


def _tool_error(message: str, error_type: str) -> dict[str, Any]:
    return {
        "content": message,
        "returnDisplay": f"Error: {message}",
        "error": {"message": message, "type": error_type},
    }


def _gate_error(agent_state: AgentState | None) -> dict[str, Any] | None:
    blocked = _research_gate(agent_state)
    if blocked is None:
        return None
    return _tool_error(_json_error_message(blocked), "PROGRESS_GATE")


def _json_error_message(payload: str) -> str:
    try:
        value = json.loads(payload)
    except ValueError:
        return payload
    if isinstance(value, Mapping) and value.get("error"):
        return str(value["error"])
    return payload


def _optional_context(agent_state: AgentState | None, key: str) -> Any:
    if agent_state is None:
        raise RuntimeError("NexAU did not provide agent state")
    return agent_state.get_context_value(key)


def _as_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    return int(value)


# ---------------------------------------------------------------------------
# Read tools
# ---------------------------------------------------------------------------


def read_file(
    file_path: str,
    offset: float | None = None,
    limit: float | None = None,
    agent_state: AgentState | None = None,
) -> dict[str, Any]:
    blocked = _gate_error(agent_state)
    if blocked is not None:
        return blocked
    try:
        path, _ = resolve_path(agent_state, file_path, write=False)
    except PathDenied as exc:
        return _denied(agent_state, str(exc))
    if path.suffix.lower() in _BINARY_DOCUMENT_SUFFIXES and path.is_file():
        return _tool_error(
            f"{path} is a binary document ({path.suffix}) and cannot be read "
            "as text. " + _binary_hint(agent_state),
            "BINARY_FILE",
        )
    return _builtin_read_file(
        str(path),
        offset=_as_int(offset),
        limit=_as_int(limit),
        agent_state=agent_state,
    )


def search_file_content(
    pattern: str,
    dir_path: str | None = None,
    include: str | None = None,
    agent_state: AgentState | None = None,
) -> dict[str, Any]:
    blocked = _gate_error(agent_state)
    if blocked is not None:
        return blocked
    if not pattern or pattern.startswith("-"):
        return _tool_error(
            "pattern must be non-empty and must not start with '-' (write "
            "'[-]...' to match a leading hyphen)",
            "INVALID_PATTERN",
        )
    if _has_control_characters(pattern) or (
        include is not None and _has_control_characters(include)
    ):
        return _tool_error(
            "pattern and include must be single-line text",
            "INVALID_PATTERN",
        )
    try:
        directory = _search_directory(agent_state, dir_path)
    except PathDenied as exc:
        return _denied(agent_state, str(exc))
    return _builtin_search_file_content(
        pattern,
        dir_path=str(directory),
        include=include or None,
        agent_state=agent_state,
    )


def glob(
    pattern: str,
    dir_path: str | None = None,
    case_sensitive: bool = False,
    respect_git_ignore: bool = True,
    agent_state: AgentState | None = None,
) -> dict[str, Any]:
    blocked = _gate_error(agent_state)
    if blocked is not None:
        return blocked
    if (
        not pattern
        or os.path.isabs(pattern)
        or ".." in Path(pattern).parts
        or _has_control_characters(pattern)
    ):
        return _tool_error(
            "pattern must be a relative glob without '..'; pass the directory "
            "as dir_path",
            "INVALID_PATTERN",
        )
    try:
        directory = _search_directory(agent_state, dir_path)
    except PathDenied as exc:
        return _denied(agent_state, str(exc))
    return _builtin_glob(
        pattern,
        dir_path=str(directory),
        case_sensitive=case_sensitive,
        respect_git_ignore=respect_git_ignore,
        agent_state=agent_state,
    )


def list_directory(
    dir_path: str,
    ignore: list[str] | None = None,
    file_filtering_options: dict[str, bool] | None = None,
    show_hidden: bool = True,
    agent_state: AgentState | None = None,
) -> dict[str, Any]:
    blocked = _gate_error(agent_state)
    if blocked is not None:
        return blocked
    try:
        path, _ = resolve_path(agent_state, dir_path, write=False)
    except PathDenied as exc:
        return _denied(agent_state, str(exc))
    return _builtin_list_directory(
        str(path),
        ignore=ignore,
        file_filtering_options=file_filtering_options,
        show_hidden=show_hidden,
        agent_state=agent_state,
    )


def search_directory(agent_state: AgentState | None, dir_path: str | None) -> Path:
    """Resolve a search or glob directory exactly as the tools do."""

    if dir_path is None or not str(dir_path).strip():
        return default_root(agent_state).path
    path, _ = resolve_path(agent_state, str(dir_path), write=False)
    return path


def _search_directory(agent_state: AgentState | None, dir_path: str | None) -> Path:
    directory = search_directory(agent_state, dir_path)
    escaping = _escaping_symlink(directory, meta_roots(agent_state))
    if escaping is not None:
        raise PathDenied(
            f"the search tree contains a symlink that leaves the allowed roots: "
            f"{escaping}"
        )
    return directory


def _binary_hint(agent_state: AgentState | None) -> str:
    hints = [
        "Use the text views under the evidence root (the task detail lists "
        "them as readable_artifacts, e.g. evidence:readable/<task>/trial-<n>/"
        "deliverables/<name>.md) when they exist."
    ]
    if _shell_enabled(agent_state):
        hints.append(
            "Otherwise inspect it with run_shell_command, e.g. "
            "`unzip -p file.docx word/document.xml | head -c 20000`."
        )
    return " ".join(hints)


# ---------------------------------------------------------------------------
# Write tools
# ---------------------------------------------------------------------------


def write_file(
    file_path: str,
    content: str,
    agent_state: AgentState | None = None,
) -> dict[str, Any]:
    try:
        path, _ = resolve_path(agent_state, file_path, write=True)
    except PathDenied as exc:
        return _denied(agent_state, str(exc))
    progress_error = _deliverable_write_error(agent_state, path, content)
    if progress_error is not None:
        return _tool_error(_json_error_message(progress_error), "PROGRESS_GATE")
    result = _builtin_write_file(str(path), content, agent_state=agent_state)
    if "error" not in result:
        _record_deliverable_write(agent_state, path, content)
        _record_mutation(agent_state)
    return result


def replace(
    file_path: str,
    old_string: str,
    new_string: str,
    instruction: str | None = None,
    expected_replacements: float = 1,
    agent_state: AgentState | None = None,
) -> dict[str, Any]:
    try:
        path, _ = resolve_path(agent_state, file_path, write=True)
    except PathDenied as exc:
        return _denied(agent_state, str(exc))
    previous = path.read_text(encoding="utf-8") if path.is_file() else None
    result = _builtin_replace(
        str(path),
        old_string,
        new_string,
        expected_replacements=_as_int(expected_replacements) or 1,
        instruction=instruction,
        agent_state=agent_state,
    )
    if "error" in result:
        return result
    updated = path.read_text(encoding="utf-8") if path.is_file() else ""
    progress_error = _deliverable_write_error(agent_state, path, updated)
    if progress_error is not None:
        if previous is None:
            path.unlink(missing_ok=True)
        else:
            path.write_text(previous, encoding="utf-8")
        return _tool_error(
            _json_error_message(progress_error) + " (the edit was not applied)",
            "PROGRESS_GATE",
        )
    _record_deliverable_write(agent_state, path, updated)
    _record_mutation(agent_state)
    return result


def delete_path(
    file_path: str,
    agent_state: AgentState | None = None,
) -> dict[str, Any]:
    try:
        roots = meta_roots(agent_state)
        joined = _joined_path(agent_state, file_path, roots)
    except PathDenied as exc:
        return _denied(agent_state, str(exc))
    name = os.path.basename(joined.rstrip(os.sep))
    if name in {"", ".", ".."}:
        return _denied(agent_state, f"cannot delete {file_path!r}")
    parent = os.path.realpath(os.path.dirname(joined.rstrip(os.sep)))
    target = os.path.join(parent, name)
    root = _containing_root(target, roots)
    if root is None:
        return _denied(agent_state, f"path is outside the allowed roots: {file_path}")
    try:
        _require_writable(root, target, file_path)
    except PathDenied as exc:
        return _denied(agent_state, str(exc))
    if os.path.islink(target) or os.path.isfile(target):
        os.unlink(target)
    elif os.path.isdir(target):
        try:
            os.rmdir(target)
        except OSError as exc:
            return _tool_error(
                f"directory must be empty before deletion: {exc}",
                "DIRECTORY_NOT_EMPTY",
            )
    else:
        return _tool_error(f"path does not exist: {target}", "FILE_NOT_FOUND")
    _record_mutation(agent_state)
    return {"content": f"Deleted {target}", "returnDisplay": f"Deleted {target}"}


# ---------------------------------------------------------------------------
# Docker shell
# ---------------------------------------------------------------------------


def run_shell_command(
    command: str,
    description: str | None = None,
    dir_path: str | None = None,
    agent_state: AgentState | None = None,
) -> dict[str, Any]:
    settings = _shell_settings(agent_state)
    if settings is None:
        return _tool_error("run_shell_command is not enabled", "SHELL_DISABLED")
    blocked = _gate_error(agent_state)
    if blocked is not None:
        return blocked
    if not command or not command.strip():
        return _tool_error("Command cannot be empty.", "INVALID_COMMAND")
    try:
        if dir_path is None or not str(dir_path).strip():
            cwd = default_root(agent_state).path
        else:
            cwd, _ = resolve_path(agent_state, str(dir_path), write=False)
        mounts = shell_mounts(meta_roots(agent_state))
    except PathDenied as exc:
        return _denied(agent_state, str(exc))
    if not cwd.is_dir():
        return _tool_error(f"Path is not a directory: {cwd}", "NOT_A_DIRECTORY")
    output_root = Path(str(_context_value(agent_state, "meta_tool_output_dir")))
    output_dir = output_root / f"shell-{uuid.uuid4().hex[:8]}"
    output_dir.mkdir(parents=True, exist_ok=True)
    container = f"ahe-meta-shell-{uuid.uuid4().hex[:12]}"
    argv = docker_shell_argv(
        settings=settings,
        container=container,
        cwd=cwd,
        mounts=mounts,
        command=command,
    )
    (output_dir / "command.txt").write_text(
        command + "\n\n# docker invocation\n" + shlex.join(argv) + "\n",
        encoding="utf-8",
    )
    workspace = _evolve_workspace(agent_state)
    before = _git_status(workspace)
    timeout = float(settings.get("timeout_seconds", 300))
    start = time.monotonic()
    timed_out = False
    with (
        (output_dir / "stdout.txt").open("wb") as stdout_handle,
        (output_dir / "stderr.txt").open("wb") as stderr_handle,
    ):
        process = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=stdout_handle,
            stderr=stderr_handle,
        )
        try:
            exit_code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            subprocess.run(
                [str(settings.get("docker_binary", "docker")), "kill", container],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=60,
                check=False,
            )
            try:
                exit_code = process.wait(timeout=60)
            except subprocess.TimeoutExpired:
                process.kill()
                exit_code = process.wait()
    duration_ms = int((time.monotonic() - start) * 1000)
    if workspace is not None and _git_status(workspace) != before:
        _record_mutation(agent_state)
    stdout = _read_capped(output_dir / "stdout.txt")
    stderr = _read_capped(output_dir / "stderr.txt")
    stdout, stderr, truncated, stdout_length, stderr_length = smart_truncate_output(
        stdout,
        stderr,
        str(output_dir),
    )
    output = stdout
    if stderr:
        output = f"{stdout}\n{stderr}" if stdout else stderr
    output = _truncate_shell_output(output)
    error_message: str | None = None
    if exit_code == 125 and not timed_out:
        error_message = "the Docker shell container could not start (exit code 125)"
    elif exit_code != 0 and not timed_out:
        error_message = f"Command failed with exit code {exit_code}"
    parts: list[str] = []
    if timed_out:
        parts.append(f"Timeout: command timed out after {timeout / 60:.1f} minutes.")
        if output:
            parts.append(f"Partial output: {output}")
    else:
        parts.append(f"Output: {output if output else '(empty)'}")
    if error_message:
        parts.append(f"Error: {error_message}")
    if exit_code != 0:
        parts.append(f"Exit Code: {exit_code}")
    result: dict[str, Any] = {
        "content": "\n".join(parts),
        "returnDisplay": output if output.strip() else "(empty)",
        "duration_ms": duration_ms,
        "exit_code": exit_code,
        "output_dir": str(output_dir),
        "stdout_file": str(output_dir / "stdout.txt"),
        "stderr_file": str(output_dir / "stderr.txt"),
    }
    if truncated:
        result["truncated"] = True
        result["original_stdout_length"] = stdout_length
        result["original_stderr_length"] = stderr_length
    if timed_out or error_message:
        result["error"] = {
            "message": error_message or "Command timed out",
            "type": "SHELL_EXECUTE_ERROR",
        }
    return result


def shell_mounts(roots: list[MetaRoot]) -> list[tuple[str, bool]]:
    """Return ``(path, read_only)`` bind mounts, parents first."""

    mounts: dict[str, bool] = {}
    for root in roots:
        if not root.path.exists():
            continue
        path = str(root.path)
        mounts[path] = mounts.get(path, True) and not root.writable
        git_dir = root.path / ".git"
        if root.writable and (git_dir.is_dir() or git_dir.is_file()):
            mounts[str(git_dir)] = True
    for path in mounts:
        if "," in path or '"' in path:
            raise PathDenied(f"cannot mount a path containing ',' or '\"': {path}")
    return sorted(mounts.items(), key=lambda item: (len(Path(item[0]).parts), item[0]))


def docker_shell_argv(
    *,
    settings: Mapping[str, Any],
    container: str,
    cwd: Path,
    mounts: list[tuple[str, bool]],
    command: str,
) -> list[str]:
    memory = str(settings.get("memory", "2g"))
    argv = [
        str(settings.get("docker_binary", "docker")),
        "run",
        "--rm",
        "--init",
        "--name",
        container,
        "--network",
        "none",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--pids-limit",
        str(int(settings.get("pids_limit", 256))),
        "--memory",
        memory,
        "--memory-swap",
        memory,
        "--cpus",
        str(settings.get("cpus", 2.0)),
        "--read-only",
        "--tmpfs",
        f"/tmp:rw,size={settings.get('tmpfs_size', '1g')},mode=1777",
        "--user",
        f"{os.getuid()}:{os.getgid()}",
        "-e",
        "HOME=/tmp",
        "-e",
        "PYTHONDONTWRITEBYTECODE=1",
        "-w",
        str(cwd),
    ]
    for path, read_only in mounts:
        spec = f"type=bind,source={path},target={path}"
        if read_only:
            spec += ",readonly"
        argv.extend(["--mount", spec])
    argv.extend([str(settings["image"]), "bash", "-c", command])
    return argv


def _shell_settings(agent_state: AgentState | None) -> Mapping[str, Any] | None:
    value = _optional_context(agent_state, "meta_shell")
    if not isinstance(value, Mapping) or value.get("backend") != "docker":
        return None
    return value


def _shell_enabled(agent_state: AgentState | None) -> bool:
    try:
        return _shell_settings(agent_state) is not None
    except RuntimeError:
        return False


def _evolve_workspace(agent_state: AgentState | None) -> Path | None:
    if str(_optional_context(agent_state, "meta_role")) != "evolve":
        return None
    value = _optional_context(agent_state, "workspace_dir")
    return Path(str(value)) if value is not None else None


def _git_status(workspace: Path | None) -> str | None:
    if workspace is None:
        return None
    try:
        return _git(workspace, "status", "--porcelain", "--untracked-files=all")
    except Exception:  # a status probe must never fail the tool
        return None


def _read_capped(path: Path) -> str:
    size = path.stat().st_size
    with path.open("rb") as handle:
        if size <= _SHELL_READ_LIMIT_BYTES:
            data = handle.read()
            return data.decode("utf-8", errors="replace")
        half = _SHELL_READ_LIMIT_BYTES // 2
        head = handle.read(half)
        handle.seek(size - half)
        tail = handle.read(half)
    omitted = size - 2 * half
    return (
        head.decode("utf-8", errors="replace")
        + f"\n... [{omitted:,} bytes not loaded; see {path}] ...\n"
        + tail.decode("utf-8", errors="replace")
    )
