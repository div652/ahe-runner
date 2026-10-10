from __future__ import annotations

import shutil
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

    report = validate_harness(
        harness,
        manifest,
        project_root=project,
    )
    assert report["valid"] is True

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
    assert "training task identifier" in str(memorization_error.value)


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
