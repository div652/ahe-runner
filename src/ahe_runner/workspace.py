"""Git-backed candidate workspaces and immutable harness checkpoints."""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .errors import IntegrityError
from .io import atomic_write_json, read_json, sha256_json, tree_hashes

_COPILOT_TRAILER = (
    "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
)
_HARNESS_COPY_IGNORE = shutil.ignore_patterns(
    ".git",
    "__pycache__",
    "*.pyc",
    "*.pyo",
)


def initialize_baseline_checkpoint(
    *,
    source_harness: Path,
    checkpoint_dir: Path,
    validation_report: dict[str, Any],
) -> dict[str, Any]:
    manifest_path = checkpoint_dir / "checkpoint.json"
    if manifest_path.is_file():
        return verify_checkpoint(checkpoint_dir)
    if checkpoint_dir.exists():
        raise IntegrityError(f"incomplete checkpoint directory: {checkpoint_dir}")
    temporary = checkpoint_dir.with_name(checkpoint_dir.name + ".tmp")
    if temporary.exists():
        shutil.rmtree(temporary)
    temporary.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        source_harness,
        temporary / "harness",
        ignore=_HARNESS_COPY_IGNORE,
    )
    manifest = _checkpoint_manifest(
        checkpoint="C0",
        parent_checkpoint=None,
        harness_dir=temporary / "harness",
        workspace_commit=None,
        source={
            "kind": "operator-base-harness",
            "path": str(source_harness.resolve()),
            "validation_report": validation_report,
        },
    )
    atomic_write_json(temporary / "checkpoint.json", manifest)
    _make_tree_read_only(temporary / "harness")
    os.replace(temporary, checkpoint_dir)
    return manifest


def prepare_evolve_workspace(
    *,
    workspaces_dir: Path,
    checkpoint: str,
    attempt: int,
    parent_harness_dir: Path,
    recovery_output_dir: Path,
) -> Path:
    round_root = workspaces_dir / checkpoint
    workspace = round_root / f"attempt-{attempt:04d}"
    if workspace.exists():
        if not (workspace / ".git").is_dir():
            raise IntegrityError(f"workspace has no Git repository: {workspace}")
        return workspace
    round_root.mkdir(parents=True, exist_ok=True)
    temporary = workspace.with_name(workspace.name + ".tmp")
    if temporary.exists():
        shutil.rmtree(temporary)
    if attempt == 1:
        shutil.copytree(
            parent_harness_dir,
            temporary,
            ignore=_HARNESS_COPY_IGNORE,
        )
        _make_tree_writable(temporary)
        _git(temporary, "init", "--quiet")
        _configure_git(temporary)
        _git(temporary, "add", "--all")
        _git(
            temporary,
            "commit",
            "--quiet",
            "-m",
            f"Seed {checkpoint} candidate\n\n{_COPILOT_TRAILER}",
        )
    else:
        previous = round_root / f"attempt-{attempt - 1:04d}"
        if not (previous / ".git").is_dir():
            raise IntegrityError(f"prior Evolve workspace is missing: {previous}")
        archive_uncommitted_workspace(previous, recovery_output_dir)
        completed = subprocess.run(
            [
                "git",
                "clone",
                "--quiet",
                "--no-hardlinks",
                str(previous),
                str(temporary),
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                "could not continue from last Evolve commit: "
                + completed.stderr.strip()
            )
        _configure_git(temporary)
    os.replace(temporary, workspace)
    return workspace


def archive_uncommitted_workspace(
    workspace: Path,
    destination: Path,
) -> dict[str, Any]:
    destination.mkdir(parents=True, exist_ok=True)
    status = git_output(
        workspace,
        "status",
        "--porcelain",
        "--untracked-files=all",
    )
    diff = git_output(workspace, "diff", "--binary", "--no-ext-diff")
    cached = git_output(
        workspace,
        "diff",
        "--binary",
        "--no-ext-diff",
        "--cached",
    )
    (destination / "git-status.txt").write_text(status, encoding="utf-8")
    (destination / "unstaged.patch").write_text(diff, encoding="utf-8")
    (destination / "staged.patch").write_text(cached, encoding="utf-8")
    untracked_root = destination / "untracked"
    untracked: list[str] = []
    for line in status.splitlines():
        if not line.startswith("?? "):
            continue
        relative = line[3:]
        source = workspace / relative
        if source.is_symlink() or not source.is_file():
            continue
        target = untracked_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        untracked.append(relative)
    payload = {
        "workspace": str(workspace.resolve()),
        "head": git_output(workspace, "rev-parse", "HEAD").strip(),
        "dirty": bool(status.strip()),
        "untracked_files": untracked,
    }
    atomic_write_json(destination / "workspace-recovery.json", payload)
    return payload


def freeze_checkpoint(
    *,
    checkpoint: str,
    parent_checkpoint: str,
    workspace: Path,
    destination: Path,
    validation_report: dict[str, Any],
) -> dict[str, Any]:
    manifest_path = destination / "checkpoint.json"
    if manifest_path.is_file():
        return verify_checkpoint(destination)
    if destination.exists():
        raise IntegrityError(f"incomplete checkpoint directory: {destination}")
    status = git_output(workspace, "status", "--porcelain")
    if status.strip():
        raise IntegrityError("Evolve workspace is not clean before freeze:\n" + status)
    commit_count = int(git_output(workspace, "rev-list", "--count", "HEAD"))
    if commit_count < 2:
        raise IntegrityError("Evolve produced no committed harness change")
    root_commit = git_output(
        workspace,
        "rev-list",
        "--max-parents=0",
        "HEAD",
    ).strip()
    changed_paths = sorted(
        line
        for line in git_output(
            workspace,
            "diff",
            "--name-only",
            f"{root_commit}..HEAD",
        ).splitlines()
        if line
    )
    if not set(changed_paths) - {"change_manifest.json"}:
        raise IntegrityError("Evolve changed no harness component")
    change_manifest_path = workspace / "change_manifest.json"
    change_manifest = _validate_change_manifest(
        change_manifest_path,
        checkpoint=checkpoint,
        parent_checkpoint=parent_checkpoint,
        changed_paths=changed_paths,
    )

    temporary = destination.with_name(destination.name + ".tmp")
    if temporary.exists():
        shutil.rmtree(temporary)
    temporary.mkdir(parents=True)
    shutil.copytree(
        workspace,
        temporary / "harness",
        ignore=_HARNESS_COPY_IGNORE,
    )
    head = git_output(workspace, "rev-parse", "HEAD").strip()
    manifest = _checkpoint_manifest(
        checkpoint=checkpoint,
        parent_checkpoint=parent_checkpoint,
        harness_dir=temporary / "harness",
        workspace_commit=head,
        source={
            "kind": "evolve-workspace",
            "workspace": str(workspace.resolve()),
            "commit_count": commit_count,
            "changed_paths": changed_paths,
            "change_manifest": change_manifest,
            "validation_report": validation_report,
        },
    )
    atomic_write_json(temporary / "checkpoint.json", manifest)
    _make_tree_read_only(temporary / "harness")
    os.replace(temporary, destination)
    return manifest


def verify_checkpoint(checkpoint_dir: Path) -> dict[str, Any]:
    manifest = read_json(checkpoint_dir / "checkpoint.json")
    files = tree_hashes(checkpoint_dir / "harness")
    if files != manifest.get("harness_files"):
        raise IntegrityError(f"checkpoint file hashes changed: {checkpoint_dir}")
    if sha256_json(files) != manifest.get("harness_sha256"):
        raise IntegrityError(f"checkpoint tree hash changed: {checkpoint_dir}")
    return manifest


def git_output(repository: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "--no-pager", *args],
        cwd=repository,
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed in {repository}: {completed.stderr.strip()}"
        )
    return completed.stdout


def _git(repository: Path, *args: str) -> None:
    git_output(repository, *args)


def _configure_git(repository: Path) -> None:
    _git(repository, "config", "user.name", "AHE Evolve Agent")
    _git(repository, "config", "user.email", "ahe-evolve-agent@localhost")


def _checkpoint_manifest(
    *,
    checkpoint: str,
    parent_checkpoint: str | None,
    harness_dir: Path,
    workspace_commit: str | None,
    source: dict[str, Any],
) -> dict[str, Any]:
    files = tree_hashes(harness_dir)
    return {
        "schema_version": 1,
        "checkpoint": checkpoint,
        "parent_checkpoint": parent_checkpoint,
        "created_at": datetime.now(UTC).isoformat(),
        "workspace_commit": workspace_commit,
        "harness_files": files,
        "harness_sha256": sha256_json(files),
        "source": source,
    }


def _validate_change_manifest(
    path: Path,
    *,
    checkpoint: str,
    parent_checkpoint: str,
    changed_paths: list[str],
) -> dict[str, Any]:
    if not path.is_file():
        raise IntegrityError("Evolve did not create change_manifest.json")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise IntegrityError("change_manifest.json must contain an object")
    required = {
        "schema_version": 2,
        "checkpoint": checkpoint,
        "parent_checkpoint": parent_checkpoint,
    }
    for key, expected in required.items():
        if value.get(key) != expected:
            raise IntegrityError(f"change manifest {key} must be {expected!r}")
    changes = value.get("changes")
    if not isinstance(changes, list) or not changes:
        raise IntegrityError("change manifest must contain logical changes")
    declared = {
        str(item.get("path"))
        for item in changes
        if isinstance(item, dict) and item.get("path")
    }
    actual_components = set(changed_paths) - {"change_manifest.json"}
    if not declared.issubset(actual_components):
        raise IntegrityError(
            "change manifest declares files not changed by the workspace"
        )
    return value


def _make_tree_read_only(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        mode = path.stat().st_mode
        if path.is_dir() or path.is_file():
            path.chmod(mode & ~stat.S_IWUSR & ~stat.S_IWGRP & ~stat.S_IWOTH)


def _make_tree_writable(root: Path) -> None:
    for path in sorted(root.rglob("*")):
        mode = path.stat().st_mode
        path.chmod(mode | stat.S_IWUSR)
