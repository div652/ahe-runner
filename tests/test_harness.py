from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest
import yaml

from ahe_runner.errors import CandidateValidationError
from ahe_runner.harness import load_harness_manifest, validate_harness

from .conftest import make_example_project


def test_harness_validation_rejects_symlinks_and_task_memorization(
    tmp_path: Path,
) -> None:
    config_path = make_example_project(tmp_path)
    project = config_path.parent
    manifest = load_harness_manifest(project / "harness_manifest.yaml")
    harness = project / "harness"
    original_dont_write_bytecode = sys.dont_write_bytecode

    report = validate_harness(
        harness,
        manifest,
        project_root=project,
    )
    assert report["valid"] is True
    assert report["conformance"]["abi_version"] == "ahe.harness/v2"
    assert report["conformance"]["registrations"]["tool_implementations"] == [
        "tools/record_note.py"
    ]
    assert report["conformance"]["registrations"]["skills"] == [
        "skills/evidence-first/SKILL.md"
    ]
    assert report["conformance"]["registrations"]["sub_agents"] == [
        "sub_agents/reviewer/agent.yaml"
    ]
    assert not list(harness.rglob("__pycache__"))
    assert sys.dont_write_bytecode is original_dont_write_bytecode

    symlink_harness = tmp_path / "symlink-harness"
    shutil.copytree(harness, symlink_harness)
    (symlink_harness / "linked.md").symlink_to(symlink_harness / "systemprompt.md")
    with pytest.raises(CandidateValidationError) as symlink_error:
        validate_harness(
            symlink_harness,
            manifest,
            project_root=project,
        )
    assert "symlink is prohibited" in str(symlink_error.value)

    memorized = tmp_path / "memorized-harness"
    shutil.copytree(harness, memorized)
    with (memorized / "systemprompt.md").open("a", encoding="utf-8") as handle:
        handle.write("\nSpecial-case train-secret-17.\n")
    with pytest.raises(CandidateValidationError) as memorization_error:
        validate_harness(
            memorized,
            manifest,
            project_root=project,
            training_task_ids=("train-secret-17",),
        )
    assert "complete training task ID" in str(memorization_error.value)

    short_id_harness = tmp_path / "short-id-harness"
    shutil.copytree(harness, short_id_harness)
    short_id_report = validate_harness(
        short_id_harness,
        manifest,
        project_root=project,
        training_task_ids=("1",),
        run_nexau_smoke=False,
    )
    assert short_id_report["valid"] is True


def test_harness_validation_preserves_protected_and_runtime_owned_fields(
    tmp_path: Path,
) -> None:
    config_path = make_example_project(tmp_path)
    project = config_path.parent
    manifest = load_harness_manifest(project / "harness_manifest.yaml")
    harness = project / "harness"
    parent = validate_harness(
        harness,
        manifest,
        project_root=project,
        run_nexau_smoke=False,
    )

    candidate = tmp_path / "candidate"
    shutil.copytree(harness, candidate)
    agent = yaml.safe_load((candidate / "agent.yaml").read_text(encoding="utf-8"))
    agent["llm_config"]["model"] = "unauthorized-model"
    (candidate / "agent.yaml").write_text(
        yaml.safe_dump(agent, sort_keys=False),
        encoding="utf-8",
    )
    with pytest.raises(CandidateValidationError) as protected_error:
        validate_harness(
            candidate,
            manifest,
            project_root=project,
            parent_runtime_owned_hashes=parent["runtime_owned_hashes"],
            run_nexau_smoke=False,
        )
    assert "protected agent field" in str(protected_error.value)

    runtime_candidate = tmp_path / "runtime-candidate"
    shutil.copytree(harness, runtime_candidate)
    (runtime_candidate / "ShortTermMEMORY.md").write_text(
        "mutated runtime state\n",
        encoding="utf-8",
    )
    with pytest.raises(CandidateValidationError) as runtime_error:
        validate_harness(
            runtime_candidate,
            manifest,
            project_root=project,
            parent_runtime_owned_hashes=parent["runtime_owned_hashes"],
            run_nexau_smoke=False,
        )
    assert "runtime-owned harness files changed" in str(runtime_error.value)


def test_v2_conformance_rejects_inactive_and_mismatched_tools(
    tmp_path: Path,
) -> None:
    config_path = make_example_project(tmp_path)
    project = config_path.parent
    manifest = load_harness_manifest(project / "harness_manifest.yaml")
    harness = project / "harness"

    inactive = tmp_path / "inactive"
    shutil.copytree(harness, inactive)
    (inactive / "tools" / "unused.py").write_text(
        "def unused(value: str) -> str:\n    return value\n",
        encoding="utf-8",
    )
    with pytest.raises(CandidateValidationError) as inactive_error:
        validate_harness(
            inactive,
            manifest,
            project_root=project,
            run_nexau_smoke=False,
        )
    assert "not runtime-attached: tools/unused.py" in str(inactive_error.value)

    mismatch = tmp_path / "mismatch"
    shutil.copytree(harness, mismatch)
    tool_path = mismatch / "tool_descriptions" / "record_note.tool.yaml"
    tool = yaml.safe_load(tool_path.read_text(encoding="utf-8"))
    tool["input_schema"]["properties"] = {
        "text": {"type": "string"},
    }
    tool["input_schema"]["required"] = ["text"]
    tool_path.write_text(
        yaml.safe_dump(tool, sort_keys=False),
        encoding="utf-8",
    )
    with pytest.raises(CandidateValidationError) as mismatch_error:
        validate_harness(
            mismatch,
            manifest,
            project_root=project,
            run_nexau_smoke=False,
        )
    assert "schema properties ['text']" in str(mismatch_error.value)


def test_v2_conformance_accepts_transitive_tool_support_modules(
    tmp_path: Path,
) -> None:
    config_path = make_example_project(tmp_path)
    project = config_path.parent
    manifest = load_harness_manifest(project / "harness_manifest.yaml")
    harness = project / "harness"
    (harness / "tools" / "helper.py").write_text(
        "def normalize_note(value: str) -> str:\n    return value.strip()\n",
        encoding="utf-8",
    )
    tool_path = harness / "tools" / "record_note.py"
    tool_path.write_text(
        tool_path.read_text(encoding="utf-8")
        .replace(
            "from typing import Any\n",
            "from typing import Any\n\nfrom tools.helper import normalize_note\n",
        )
        .replace(
            "    notes = list(",
            "    note = normalize_note(note)\n    notes = list(",
        ),
        encoding="utf-8",
    )

    report = validate_harness(
        harness,
        manifest,
        project_root=project,
        run_nexau_smoke=False,
    )
    assert report["conformance"]["registrations"]["tool_implementations"] == [
        "tools/helper.py",
        "tools/record_note.py",
    ]
