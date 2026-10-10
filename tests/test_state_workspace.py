from __future__ import annotations

from pathlib import Path

import pytest

from ahe_runner.errors import InfrastructureError, IntegrityError, StageInterruptedError
from ahe_runner.io import atomic_write_json, read_json
from ahe_runner.state import StageResult, StageStore
from ahe_runner.workspace import git_output, prepare_evolve_workspace


def test_stage_store_preserves_attempts_and_verifies_cached_outputs(
    tmp_path: Path,
) -> None:
    campaign = tmp_path / "campaign"
    store = StageStore(campaign_dir=campaign, maximum_attempts=3)
    output = campaign / "result.json"

    def fail_once(context):  # type: ignore[no-untyped-def]
        atomic_write_json(context.attempt_dir / "partial.json", {"attempt": 1})
        raise InfrastructureError("temporary transport failure")

    with pytest.raises(StageInterruptedError):
        store.run(name="demo", semantic_inputs={"value": 1}, action=fail_once)

    calls = 0

    def complete(context):  # type: ignore[no-untyped-def]
        nonlocal calls
        calls += 1
        assert context.attempt == 2
        assert context.previous_attempt_dir is not None
        atomic_write_json(output, {"ok": True})
        return StageResult(outputs=(output,), data={"attempt": context.attempt})

    result = store.run(
        name="demo",
        semantic_inputs={"value": 1},
        action=complete,
    )
    assert result.data == {"attempt": 2}
    assert calls == 1

    cached = store.run(
        name="demo",
        semantic_inputs={"value": 1},
        action=complete,
    )
    assert cached.data == {"attempt": 2}
    assert calls == 1

    stage = read_json(campaign / "stages" / "demo" / "stage.json")
    assert [row["status"] for row in stage["attempts"]] == [
        "infrastructure_failed",
        "completed",
    ]

    atomic_write_json(output, {"ok": False})
    with pytest.raises(IntegrityError, match="stage output hash mismatch"):
        store.run(
            name="demo",
            semantic_inputs={"value": 1},
            action=complete,
        )


def test_stage_store_rejects_semantic_input_drift(tmp_path: Path) -> None:
    campaign = tmp_path / "campaign"
    store = StageStore(campaign_dir=campaign, maximum_attempts=2)
    output = campaign / "output.json"

    def action(_context):  # type: ignore[no-untyped-def]
        atomic_write_json(output, {"ok": True})
        return StageResult(outputs=(output,), data={})

    store.run(name="stable", semantic_inputs={"value": 1}, action=action)
    with pytest.raises(IntegrityError, match="semantic inputs changed"):
        store.run(name="stable", semantic_inputs={"value": 2}, action=action)


def test_evolve_workspace_continues_committed_head_only(tmp_path: Path) -> None:
    parent = tmp_path / "parent"
    parent.mkdir()
    (parent / "systemprompt.md").write_text("base\n", encoding="utf-8")
    recovery = tmp_path / "recovery"

    first = prepare_evolve_workspace(
        workspaces_dir=tmp_path / "workspaces",
        checkpoint="C1",
        attempt=1,
        parent_harness_dir=parent,
        recovery_output_dir=recovery,
    )
    prompt = first / "systemprompt.md"
    prompt.write_text("base\ncommitted\n", encoding="utf-8")
    git_output(first, "add", "--all")
    git_output(first, "commit", "--quiet", "-m", "Commit durable progress")
    prompt.write_text("base\ncommitted\ndirty\n", encoding="utf-8")
    (first / "untracked.txt").write_text("untracked\n", encoding="utf-8")

    second = prepare_evolve_workspace(
        workspaces_dir=tmp_path / "workspaces",
        checkpoint="C1",
        attempt=2,
        parent_harness_dir=parent,
        recovery_output_dir=recovery,
    )
    assert (second / "systemprompt.md").read_text(encoding="utf-8") == (
        "base\ncommitted\n"
    )
    assert not (second / "untracked.txt").exists()
    assert "dirty" in (recovery / "unstaged.patch").read_text(encoding="utf-8")
    assert (recovery / "untracked" / "untracked.txt").is_file()
