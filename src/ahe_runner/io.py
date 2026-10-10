"""Atomic persistence and content hashing."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .errors import IntegrityError


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"{path} must contain a JSON object")
    return value


def atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    os.replace(temporary, path)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def sha256_json(value: Any) -> str:
    return sha256_bytes(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    )


def tree_hashes(
    root: Path,
    *,
    exclude_names: frozenset[str] = frozenset({".git", "__pycache__"}),
) -> dict[str, str]:
    root = root.resolve()
    if not root.is_dir():
        raise FileNotFoundError(root)
    result: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if any(part in exclude_names for part in relative.parts):
            continue
        if path.is_symlink():
            raise IntegrityError(f"tree contains a symlink: {path}")
        if path.is_file():
            result[relative.as_posix()] = sha256_file(path)
    return result


def tree_sha256(root: Path) -> str:
    return sha256_json(tree_hashes(root))


def path_fingerprint(path: Path, *, relative_to: Path) -> dict[str, Any]:
    resolved = path.resolve()
    base = relative_to.resolve()
    try:
        relative = resolved.relative_to(base).as_posix()
    except ValueError as exc:
        raise IntegrityError(f"output path escapes campaign root: {path}") from exc
    if resolved.is_symlink():
        raise IntegrityError(f"output path is a symlink: {path}")
    if resolved.is_file():
        return {
            "path": relative,
            "type": "file",
            "sha256": sha256_file(resolved),
        }
    if resolved.is_dir():
        files = tree_hashes(resolved)
        return {
            "path": relative,
            "type": "directory",
            "sha256": sha256_json(files),
            "files": files,
        }
    raise IntegrityError(f"required stage output is missing: {path}")


def verify_path_fingerprint(
    fingerprint: Mapping[str, Any],
    *,
    relative_to: Path,
) -> None:
    path = relative_to / str(fingerprint["path"])
    actual = path_fingerprint(path, relative_to=relative_to)
    if actual != dict(fingerprint):
        raise IntegrityError(f"stage output hash mismatch: {path}")
