from __future__ import annotations

import shlex
import sys
from pathlib import Path

import pytest

from ahe_runner.errors import (
    ConfigurationError,
    InfrastructureError,
    IntegrityError,
    StageBlockedError,
    StageInterruptedError,
)
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


def _infrastructure_failure(_context):  # type: ignore[no-untyped-def]
    raise InfrastructureError("model server down")


def _completes(output: Path):  # type: ignore[no-untyped-def]
    def action(context):  # type: ignore[no-untyped-def]
        atomic_write_json(output, {"attempt": context.attempt})
        return StageResult(outputs=(output,), data={"attempt": context.attempt})

    return action


def test_reopened_blocked_stage_gets_more_attempts(tmp_path: Path) -> None:
    campaign = tmp_path / "campaign"
    store = StageStore(campaign_dir=campaign, maximum_attempts=2)
    run = {"name": "demo", "semantic_inputs": {"value": 1}}

    with pytest.raises(StageInterruptedError):
        store.run(**run, action=_infrastructure_failure)
    with pytest.raises(StageBlockedError):
        store.run(**run, action=_infrastructure_failure)
    with pytest.raises(StageBlockedError):
        store.run(**run, action=_completes(campaign / "out.json"))

    plan = store.blocked_retry_plan("demo")
    assert plan["attempts"] == 2
    assert plan["allowed_attempts"] == 2
    assert plan["attempt_statuses"] == ["infrastructure_failed"] * 2
    assert plan["last_error_type"] == "InfrastructureError"
    with pytest.raises(ConfigurationError, match="must be larger"):
        store.reopen_blocked("demo", allowed_attempts=2, override={})
    store.reopen_blocked("demo", allowed_attempts=4, override={"reason": "restart"})
    with pytest.raises(ConfigurationError, match="not 'blocked'"):
        store.blocked_retry_plan("demo")

    with pytest.raises(StageInterruptedError):
        store.run(**run, action=_infrastructure_failure)
    result = store.run(**run, action=_completes(campaign / "out.json"))
    assert result.data == {"attempt": 4}

    stage = read_json(campaign / "stages" / "demo" / "stage.json")
    assert stage["status"] == "completed"
    assert stage["attempt_allowance"] == 4
    assert [row["status"] for row in stage["attempts"]] == [
        "infrastructure_failed",
        "infrastructure_failed",
        "infrastructure_failed",
        "completed",
    ]
    assert stage["operator_overrides"] == [
        {
            "reason": "restart",
            "previous_stage_state": "blocked",
            "previous_allowed_attempts": 2,
            "allowed_attempts": 4,
            "attempts_before": 2,
        }
    ]
    with pytest.raises(ConfigurationError, match="no stage named"):
        store.blocked_retry_plan("missing")


def test_only_infrastructure_blocked_retryable_stages_reopen(tmp_path: Path) -> None:
    campaign = tmp_path / "campaign"
    store = StageStore(campaign_dir=campaign, maximum_attempts=2)

    def logic_error(_context):  # type: ignore[no-untyped-def]
        raise ValueError("bad candidate")

    with pytest.raises(StageBlockedError):
        store.run(name="logic", semantic_inputs={}, action=logic_error)
    with pytest.raises(IntegrityError, match="did not fail on infrastructure"):
        store.blocked_retry_plan("logic")

    with pytest.raises(StageBlockedError):
        store.run(
            name="final",
            semantic_inputs={},
            action=_infrastructure_failure,
            retryable=False,
        )
    with pytest.raises(IntegrityError, match="not retryable"):
        store.blocked_retry_plan("final")

    # Records written before `retryable` was stored: an earlier infrastructure
    # failure that did not block shows the stage was retryable.
    for name in ("legacy", "final"):
        if name == "legacy":
            with pytest.raises(StageInterruptedError):
                store.run(name=name, semantic_inputs={}, action=_infrastructure_failure)
            with pytest.raises(StageBlockedError):
                store.run(name=name, semantic_inputs={}, action=_infrastructure_failure)
        path = campaign / "stages" / name / "stage.json"
        stage = read_json(path)
        del stage["retryable"]
        atomic_write_json(path, stage)
    assert store.blocked_retry_plan("legacy")["attempts"] == 2
    with pytest.raises(IntegrityError, match="not retryable"):
        store.blocked_retry_plan("final")


def test_pre_stage_command_runs_before_each_attempt(tmp_path: Path) -> None:
    campaign = tmp_path / "campaign"
    log = tmp_path / "hook.log"
    record = (
        "import os, sys; "
        "open(sys.argv[1], 'a').write(os.environ['AHE_STAGE_NAME'] + ' ' "
        "+ os.environ['AHE_CAMPAIGN_DIR'] + '\\n')"
    )
    command = shlex.join([sys.executable, "-c", record, str(log)])
    store = StageStore(
        campaign_dir=campaign, maximum_attempts=2, pre_stage_command=command
    )
    output = campaign / "out.json"

    store.run(name="demo", semantic_inputs={}, action=_completes(output))
    store.run(name="demo", semantic_inputs={}, action=_completes(output))
    assert log.read_text(encoding="utf-8") == f"demo {campaign.resolve()}\n"
    stage = read_json(campaign / "stages" / "demo" / "stage.json")
    assert stage["attempts"][0]["pre_stage_command"] == command

    failing = StageStore(
        campaign_dir=campaign,
        maximum_attempts=2,
        pre_stage_command=shlex.join([sys.executable, "-c", "raise SystemExit(3)"]),
    )
    for _ in range(3):
        with pytest.raises(InfrastructureError, match="exited 3"):
            failing.run(name="other", semantic_inputs={}, action=_completes(output))
    slow = StageStore(
        campaign_dir=campaign,
        maximum_attempts=2,
        pre_stage_command=shlex.join(
            [sys.executable, "-c", "import time; time.sleep(30)"]
        ),
        pre_stage_timeout_seconds=0.5,
    )
    with pytest.raises(InfrastructureError, match="pre-stage command failed"):
        slow.run(name="other", semantic_inputs={}, action=_completes(output))
    other = campaign / "stages" / "other"
    assert not (other / "attempts").exists()
    assert read_json(other / "stage.json").get("attempts", []) == []

    result = store.run(name="other", semantic_inputs={}, action=_completes(output))
    assert result.data == {"attempt": 1}
    assert log.read_text(encoding="utf-8").splitlines()[-1].startswith("other ")


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
