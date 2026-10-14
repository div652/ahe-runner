from __future__ import annotations

import importlib.machinery
import sys
from pathlib import Path

import pytest
from harvey_ahe.candidate_checks import (
    PROTECTED_MODULE_NAMES,
    module_shadowing_errors,
    top_level_import_names,
)

from ahe_runner.harness_imports import HarnessImportScope


def _write(root: Path, relative: str, text: str = "") -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _seed_like(root: Path) -> Path:
    _write(root, "systemprompt.md", "prompt")
    _write(root, "LongTermMEMORY.md")
    _write(root, "tool_descriptions/read.tool.yaml", "name: read")
    _write(root, "tools/read.py", "def read(file_path: str) -> str:\n    return ''\n")
    _write(root, "tools/_workspace.py", "X = 1\n")
    _write(root, "skills/README.md", "no python here")
    return root


def test_seed_shaped_harness_exposes_only_tools(tmp_path: Path) -> None:
    root = _seed_like(tmp_path / "harness")

    assert top_level_import_names(root) == {"tools": "tools/"}
    assert module_shadowing_errors(root) == []


@pytest.mark.parametrize(
    ("relative", "name"),
    [
        ("json.py", "json"),
        ("ahe_runner/errors.py", "ahe_runner"),
        ("lab_core/evaluation/scoring.py", "lab_core"),
        ("harvey_ahe/grading.py", "harvey_ahe"),
        ("nexau/__init__.py", "nexau"),
        ("benchmark_adapter.py", "benchmark_adapter"),
        ("yaml/__init__.py", "yaml"),
        ("openai/deep/nested/module.py", "openai"),
    ],
)
def test_shadowing_names_are_rejected(
    tmp_path: Path,
    relative: str,
    name: str,
) -> None:
    root = _seed_like(tmp_path / "harness")
    _write(root, relative, "SPOOF = True\n")

    errors = module_shadowing_errors(root)

    assert len(errors) == 1
    assert repr(name) in errors[0]
    assert "AHE-003" in errors[0]


def test_extension_module_suffix_is_recognized(tmp_path: Path) -> None:
    root = _seed_like(tmp_path / "harness")
    suffix = importlib.machinery.EXTENSION_SUFFIXES[0]
    _write(root, f"json{suffix}")

    assert top_level_import_names(root)["json"] == f"json{suffix}"
    assert any("'json'" in error for error in module_shadowing_errors(root))


def test_unique_helper_package_and_non_python_entries_are_allowed(
    tmp_path: Path,
) -> None:
    root = _seed_like(tmp_path / "harness")
    _write(root, "harvey_seed_helpers_unique/format.py", "X = 1\n")
    _write(root, "json/README.md", "a namespace-only folder cannot win")
    _write(root, ".hidden/json.py", "SPOOF = True\n")

    assert set(top_level_import_names(root)) == {
        "harvey_seed_helpers_unique",
        "tools",
    }
    assert module_shadowing_errors(root) == []


def test_protected_names_cover_the_grading_import_chain() -> None:
    assert {"ahe_runner", "harvey_ahe", "lab_core", "nexau"} <= PROTECTED_MODULE_NAMES


def test_reproduction_the_guard_rejects_actually_shadows(tmp_path: Path) -> None:
    root = _seed_like(tmp_path / "harness")
    _write(root, "ahe_runner/__init__.py")
    _write(root, "ahe_runner/errors.py", "SPOOF = 'candidate copy'\n")
    original = sys.modules["ahe_runner.errors"]

    with HarnessImportScope(root):
        import ahe_runner.errors as shadowed

        assert getattr(shadowed, "SPOOF", None) == "candidate copy"

    assert sys.modules["ahe_runner.errors"] is original
    assert any("'ahe_runner'" in error for error in module_shadowing_errors(root))
