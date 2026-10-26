from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from ahe_runner.adapter import EvaluationResult
from ahe_runner.cli import main as cli_main
from ahe_runner.config import load_config
from ahe_runner.controller import CampaignController
from ahe_runner.errors import (
    ConfigurationError,
    InfrastructureError,
    IntegrityError,
    StageBlockedError,
    StageInterruptedError,
)
from ahe_runner.io import atomic_write_json, read_json, sha256_json, tree_hashes
from ahe_runner.meta.scripted import ScriptedMetaBackend
from ahe_runner.workspace import git_output

from .conftest import make_example_project


def test_full_deterministic_campaign_and_idempotent_resume(tmp_path: Path) -> None:
    config_path = make_example_project(tmp_path, rounds=2)
    controller = CampaignController(load_config(config_path))

    validation = controller.validate_project()
    assert validation["harness"]["valid"] is True
    assert validation["adapter_api_generation"] == "v2"
    assert validation["benchmark_interface"]["tool_delivery"] == "none"
    assert controller.conformance()["harness_abi"]["abi_version"] == ("ahe.harness/v2")

    status = controller.run()
    assert status["checkpoints"] == ["C0", "C1", "C2"]
    assert status["blocked_stages"] == []
    assert status["interrupted_stages"] == []
    assert status["model_call_retries"] == {
        "configured_extra_attempts": 0,
        "hung_attempt_count": 0,
        "calls_resent": 0,
        "calls_failed_after_resends": 0,
    }
    assert status["selection"]["selected_checkpoint"] in {"C1", "C2"}
    assert status["final_test"]["no_post_test_evolution"] is True

    campaign_dir = Path(status["campaign_dir"])
    selected = status["selection"]["selected_checkpoint"]
    assert (campaign_dir / "final" / "selected-harness" / "agent.yaml").is_file()
    assert (
        campaign_dir / "checkpoints" / selected / "harness" / "change_manifest.json"
    ).is_file()

    attempts_before = {row["name"]: len(row["attempts"]) for row in status["stages"]}
    resumed = CampaignController(load_config(config_path)).resume()
    attempts_after = {row["name"]: len(row["attempts"]) for row in resumed["stages"]}
    assert attempts_after == attempts_before
    assert resumed["selection"] == status["selection"]
    assert resumed["final_test"] == status["final_test"]


def test_validation_warns_about_deprecated_metric_guidance(tmp_path: Path) -> None:
    config_path = make_example_project(tmp_path)
    value = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    value["meta"]["deprecated_metric_terms"] = ["legacy-pass@k"]
    config_path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")
    supplement = config_path.parent / "meta_supplements" / "debugger.md"
    supplement.write_text(
        supplement.read_text(encoding="utf-8")
        + "\nOptimize the legacy-pass@k score.\n",
        encoding="utf-8",
    )

    controller = CampaignController(load_config(config_path))
    validation = controller.validate_project()

    assert len(validation["warnings"]) == 1
    assert "legacy-pass@k" in validation["warnings"][0]
    assert controller.conformance()["warnings"] == validation["warnings"]


def test_attribution_aliases_map_debugger_keys_to_task_keys(tmp_path: Path) -> None:
    config_path = make_example_project(tmp_path, rounds=1)
    controller = CampaignController(load_config(config_path))
    controller.run_round(1)
    campaign_dir = controller.config.campaign_dir
    before = EvaluationResult.model_validate(
        read_json(campaign_dir / "evaluations" / "C0-train" / "adapter_result.json")
    )
    after = EvaluationResult.model_validate(
        read_json(campaign_dir / "evaluations" / "C1-train" / "adapter_result.json")
    )
    task = next(row for row in before.summary.task_states if row.task_id)
    atomic_write_json(
        campaign_dir / "debugger" / "C1" / "summary.json",
        {"tasks": [{"task_key": "task-opaque", "task_id": task.task_id}]},
    )

    aliases = controller._attribution_task_aliases(1, before, after)

    assert aliases["task-opaque"] == task.task_key
    assert aliases[str(task.task_id)] == task.task_key
    assert aliases[task.task_key] == task.task_key


def test_campaign_identity_drift_requires_new_artifacts_dir(tmp_path: Path) -> None:
    config_path = make_example_project(tmp_path, rounds=0)
    CampaignController(load_config(config_path)).prepare()

    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config["campaign"]["root_seed"] = 99
    config_path.write_text(
        yaml.safe_dump(config, sort_keys=False),
        encoding="utf-8",
    )

    with pytest.raises(IntegrityError, match="campaign identity changed"):
        CampaignController(load_config(config_path)).prepare()


@pytest.mark.parametrize(
    ("relative_path", "changed_identity"),
    [
        ("meta_supplements/evolve.md", "resolved_meta_inputs"),
        ("benchmark_public/ADAPTER.md", "resolved_meta_inputs"),
        ("benchmark_adapter.py", "adapter_dependencies"),
    ],
)
def test_protected_project_input_drift_invalidates_resume(
    tmp_path: Path,
    relative_path: str,
    changed_identity: str,
) -> None:
    config_path = make_example_project(tmp_path, rounds=0)
    controller = CampaignController(load_config(config_path))
    controller.prepare()
    manifest = read_json(controller.config.campaign_dir / "campaign_manifest.json")
    assert manifest["schema_version"] == 2
    assert manifest["protected_runner_resources"]["package"]["sha256"]
    assert manifest["resolved_meta_inputs"]["prompt_context"]

    protected_input = config_path.parent / relative_path
    protected_input.write_text(
        protected_input.read_text(encoding="utf-8") + "\nprotected drift\n",
        encoding="utf-8",
    )

    with pytest.raises(IntegrityError, match=changed_identity):
        CampaignController(load_config(config_path)).prepare()


def test_final_export_rejects_partial_existing_harness(tmp_path: Path) -> None:
    config_path = make_example_project(tmp_path, rounds=0)
    controller = CampaignController(load_config(config_path))
    controller.prepare()
    selected_harness = controller.config.campaign_dir / "final" / "selected-harness"
    selected_harness.mkdir(parents=True)
    (selected_harness / "partial.txt").write_text(
        "interrupted export\n",
        encoding="utf-8",
    )

    with pytest.raises(IntegrityError, match="does not match"):
        controller._final_action(  # type: ignore[arg-type]
            None,
            selected="C0",
            results={},
        )
    assert not (controller.config.campaign_dir / "final" / "final_test.json").exists()


class _CommitThenInterruptBackend(ScriptedMetaBackend):
    def run_evolve(self, **kwargs):  # type: ignore[no-untyped-def]
        marker = self.context.config.campaign_dir / "injected-interruption"
        if not marker.exists():
            workspace = Path(kwargs["workspace"])
            prompt = workspace / "systemprompt.md"
            prompt.write_text(
                prompt.read_text(encoding="utf-8")
                + "\n<!-- durable-partial-commit -->\n",
                encoding="utf-8",
            )
            git_output(workspace, "add", "--all")
            git_output(
                workspace,
                "commit",
                "--quiet",
                "-m",
                "Persist partial Evolve progress",
            )
            prompt.write_text(
                prompt.read_text(encoding="utf-8")
                + "\n<!-- uncommitted-interrupted-edit -->\n",
                encoding="utf-8",
            )
            marker.write_text("failed once\n", encoding="utf-8")
            raise InfrastructureError("injected endpoint timeout")
        return super().run_evolve(**kwargs)


def test_evolve_resume_uses_last_commit_and_archives_dirty_state(
    tmp_path: Path,
) -> None:
    config_path = make_example_project(tmp_path, rounds=1)
    controller = CampaignController(load_config(config_path))
    controller.meta = _CommitThenInterruptBackend(controller.meta.context)

    with pytest.raises(StageInterruptedError):
        controller.run()

    interrupted = controller.status()
    assert [row["name"] for row in interrupted["interrupted_stages"]] == [
        "round.1.evolve"
    ]

    resumed_controller = CampaignController(load_config(config_path))
    resumed_controller.meta = _CommitThenInterruptBackend(
        resumed_controller.meta.context
    )
    status = resumed_controller.resume()
    assert status["checkpoints"] == ["C0", "C1"]
    assert status["interrupted_stages"] == []

    campaign_dir = Path(status["campaign_dir"])
    prompt = (
        campaign_dir / "checkpoints" / "C1" / "harness" / "systemprompt.md"
    ).read_text(encoding="utf-8")
    assert "durable-partial-commit" in prompt
    assert "uncommitted-interrupted-edit" not in prompt
    recovery_patch = (
        campaign_dir
        / "stages"
        / "round.1.evolve"
        / "attempts"
        / "0002"
        / "recovery"
        / "unstaged.patch"
    ).read_text(encoding="utf-8")
    assert "uncommitted-interrupted-edit" in recovery_patch


def _set_campaign(config_path: Path, **values: object) -> None:
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config["campaign"].update(values)
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")


def _extend(config_path: Path, **kwargs: object) -> dict:
    return CampaignController(load_config(config_path)).extend_rounds(
        reason="operator asked for more rounds", **kwargs
    )


def test_extend_rounds_continues_the_same_campaign(tmp_path: Path) -> None:
    config_path = make_example_project(tmp_path, rounds=2)
    controller = CampaignController(load_config(config_path))
    controller.run_round(1)
    controller.run_round(2)
    campaign_dir = controller.config.campaign_dir
    ledger = campaign_dir / "campaign_amendments.json"
    manifest_bytes = (campaign_dir / "campaign_manifest.json").read_bytes()
    stages_before = {
        path: path.read_bytes()
        for path in (campaign_dir / "stages").glob("*/stage.json")
    }
    assert "evolution.close" not in {path.parent.name for path in stages_before}

    _set_campaign(config_path, rounds=3)
    with pytest.raises(IntegrityError, match="ahe extend-rounds"):
        CampaignController(load_config(config_path)).resume()

    dry_run = _extend(config_path)
    assert dry_run["status"] == "dry_run"
    assert dry_run["changes"] == [
        "config.config_sha256",
        "config.document.campaign.rounds",
    ]
    assert (dry_run["from_rounds"], dry_run["to_rounds"]) == (2, 3)
    assert not ledger.exists()

    assert _extend(config_path, apply=True)["status"] == "applied"
    _set_campaign(config_path, rounds=4)
    with pytest.raises(IntegrityError, match="since the last campaign amendment"):
        CampaignController(load_config(config_path)).prepare()
    second = _extend(config_path, apply=True)
    assert (second["from_rounds"], second["to_rounds"]) == (3, 4)
    amendments = read_json(ledger)["amendments"]
    assert [row["to_rounds"] for row in amendments] == [3, 4]
    assert (
        amendments[1]["previous_identity_sha256"] == (amendments[0]["identity_sha256"])
    )

    status = CampaignController(load_config(config_path)).resume()
    assert status["checkpoints"] == ["C0", "C1", "C2", "C3", "C4"]
    assert status["blocked_stages"] == []
    assert status["interrupted_stages"] == []
    assert status["final_test"]["no_post_test_evolution"] is True
    assert [row["to_rounds"] for row in status["campaign_amendments"]] == [3, 4]
    completed = {
        row["name"] for row in status["stages"] if row["status"] == "completed"
    }
    assert {f"evaluation.C{index}.validation" for index in range(5)} <= completed
    assert {"round.4.evolve", "evaluation.C4.train", "evolution.close"} <= completed

    # Nothing recorded before the extension was rewritten, before or after resume.
    assert (campaign_dir / "campaign_manifest.json").read_bytes() == manifest_bytes
    for path, content in stages_before.items():
        assert path.read_bytes() == content

    attempts = {row["name"]: len(row["attempts"]) for row in status["stages"]}
    resumed = CampaignController(load_config(config_path)).resume()
    assert {row["name"]: len(row["attempts"]) for row in resumed["stages"]} == attempts

    _set_campaign(config_path, rounds=5)
    with pytest.raises(IntegrityError, match="after evolution has closed"):
        _extend(config_path, apply=True)
    assert len(read_json(ledger)["amendments"]) == 2


def test_extend_rounds_refuses_lower_rounds_and_other_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = make_example_project(tmp_path, rounds=2)
    controller = CampaignController(load_config(config_path))
    controller.prepare()
    ledger = controller.config.campaign_dir / "campaign_amendments.json"
    seed = yaml.safe_load(config_path.read_text(encoding="utf-8"))["campaign"][
        "root_seed"
    ]

    with pytest.raises(ConfigurationError, match="nothing to amend"):
        _extend(config_path, apply=True)
    _set_campaign(config_path, rounds=1)
    with pytest.raises(ConfigurationError, match="only increases"):
        _extend(config_path, apply=True)
    _set_campaign(config_path, rounds=3, root_seed=seed + 1)
    with pytest.raises(
        IntegrityError, match=re.escape("config.document.campaign.root_seed")
    ):
        _extend(config_path, apply=True)
    _set_campaign(config_path, root_seed=seed)
    adapter = config_path.parent / "benchmark_adapter.py"
    original_adapter = adapter.read_text(encoding="utf-8")
    adapter.write_text(original_adapter + "\n# drift\n", encoding="utf-8")
    with pytest.raises(IntegrityError, match="adapter_dependencies"):
        _extend(config_path, apply=True)
    adapter.write_text(original_adapter, encoding="utf-8")
    with pytest.raises(ConfigurationError, match="reason"):
        CampaignController(load_config(config_path)).extend_rounds(reason=" ")

    # Runner code is protected campaign identity; extending never accepts it.
    original = CampaignController._campaign_identity

    def changed_runner(self: CampaignController) -> dict:
        value = original(self)
        entry = value["protected_runner_resources"]["package"]
        entry["files"] = {**entry["files"], "controller.py": "0" * 64}
        entry["sha256"] = sha256_json(entry["files"])
        return value

    monkeypatch.setattr(CampaignController, "_campaign_identity", changed_runner)
    with pytest.raises(
        IntegrityError, match=re.escape("protected_runner_resources.package")
    ):
        _extend(config_path, apply=True)
    assert not ledger.exists()


def test_tampered_campaign_amendments_are_rejected(tmp_path: Path) -> None:
    config_path = make_example_project(tmp_path, rounds=1)
    CampaignController(load_config(config_path)).prepare()
    _set_campaign(config_path, rounds=2)
    _extend(config_path, apply=True)
    ledger_path = load_config(config_path).campaign_dir / "campaign_amendments.json"
    original = read_json(ledger_path)

    tampered = read_json(ledger_path)
    tampered["amendments"][0]["identity"]["config"]["document"]["campaign"][
        "rounds"
    ] = 9
    atomic_write_json(ledger_path, tampered)
    with pytest.raises(IntegrityError, match="does not match its identity hash"):
        CampaignController(load_config(config_path)).prepare()

    tampered = {**original, "base_identity_sha256": "0" * 64}
    atomic_write_json(ledger_path, tampered)
    with pytest.raises(IntegrityError, match="do not belong to this campaign"):
        CampaignController(load_config(config_path)).prepare()

    atomic_write_json(ledger_path, original)
    CampaignController(load_config(config_path)).prepare()


def test_extend_rounds_cli_defaults_to_a_dry_run(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = make_example_project(tmp_path, rounds=1)
    CampaignController(load_config(config_path)).prepare()
    _set_campaign(config_path, rounds=2)

    args = ["extend-rounds", "--config", str(config_path), "--reason", "more"]
    assert cli_main(args) == 0
    assert '"status": "dry_run"' in capsys.readouterr().out
    assert cli_main([*args, "--apply"]) == 0
    assert '"status": "applied"' in capsys.readouterr().out
    assert cli_main(["status", "--config", str(config_path)]) == 0


_CLOSURE_PATHS = {
    "stages/evolution.close",
    "stages/selection",
    "stages/final.complete",
    "selection/evolution_closed.json",
    "selection/selection.json",
    "final/final_test.json",
    "final/selected-harness",
}


def test_extend_rounds_reopens_a_closed_campaign(tmp_path: Path) -> None:
    config_path = make_example_project(tmp_path, rounds=2)
    closed = CampaignController(load_config(config_path)).run()
    campaign_dir = Path(closed["campaign_dir"])
    ledger = campaign_dir / "campaign_amendments.json"
    manifest_bytes = (campaign_dir / "campaign_manifest.json").read_bytes()
    kept_before = {
        path: path.read_bytes()
        for path in (campaign_dir / "stages").glob("*/stage.json")
        if f"stages/{path.parent.name}" not in _CLOSURE_PATHS
    }
    closure_hashes = {
        relative: (
            sha256_json(tree_hashes(campaign_dir / relative))
            if (campaign_dir / relative).is_dir()
            else (campaign_dir / relative).read_bytes()
        )
        for relative in _CLOSURE_PATHS
    }
    selected = closed["selection"]["selected_checkpoint"]

    _set_campaign(config_path, rounds=3)
    with pytest.raises(IntegrityError, match="--reopen-closed-evolution"):
        _extend(config_path, apply=True)

    dry_run = _extend(config_path, reopen_closed_evolution=True)
    assert dry_run["status"] == "dry_run"
    plan = dry_run["reopened_closed_evolution"]
    assert plan["closed_rounds"] == 2
    assert plan["archive_dir"] == "superseded/closure-01"
    assert {move["path"] for move in plan["moves"]} == _CLOSURE_PATHS
    assert plan["previous_selected_checkpoint"] == selected
    assert set(plan["previous_validation_primary"]) == {"C0", "C1", "C2"}
    assert set(plan["previous_test_primary"]) == {"C0", selected}
    assert not ledger.exists()
    assert not (campaign_dir / "superseded").exists()
    assert all((campaign_dir / relative).exists() for relative in _CLOSURE_PATHS)

    applied = _extend(config_path, apply=True, reopen_closed_evolution=True)
    assert applied["status"] == "applied"
    archive = campaign_dir / "superseded" / "closure-01"
    for relative, before in closure_hashes.items():
        assert not (campaign_dir / relative).exists()
        moved = archive / relative
        assert (
            sha256_json(tree_hashes(moved)) if moved.is_dir() else moved.read_bytes()
        ) == before
    record = read_json(archive / "reopen.json")
    assert record["reason"] == "operator asked for more rounds"
    assert record["moves"] == plan["moves"]
    amendment = read_json(ledger)["amendments"][-1]
    assert (amendment["from_rounds"], amendment["to_rounds"]) == (2, 3)
    assert amendment["reopened_closed_evolution"] == plan

    status = CampaignController(load_config(config_path)).resume()
    assert status["checkpoints"] == ["C0", "C1", "C2", "C3"]
    assert status["blocked_stages"] == []
    assert status["interrupted_stages"] == []
    assert set(status["selection"]["validation_summaries"]) == {
        "C0",
        "C1",
        "C2",
        "C3",
    }
    assert status["final_test"]["no_post_test_evolution"] is True
    assert status["campaign_amendments"][0]["reopened_closure"] == (
        "superseded/closure-01"
    )
    assert (campaign_dir / "final" / "selected-harness" / "agent.yaml").is_file()
    assert read_json(campaign_dir / "selection" / "evolution_closed.json")[
        "final_training_checkpoint"
    ] == ("C3")

    # Every stage outside the closure was reused, byte for byte.
    assert (campaign_dir / "campaign_manifest.json").read_bytes() == manifest_bytes
    for path, content in kept_before.items():
        assert path.read_bytes() == content
    attempts = {row["name"]: len(row["attempts"]) for row in status["stages"]}
    assert attempts["evaluation.C0.test"] == 1
    assert attempts["round.3.evolve"] == 1

    resumed = CampaignController(load_config(config_path)).resume()
    assert {row["name"]: len(row["attempts"]) for row in resumed["stages"]} == attempts

    _set_campaign(config_path, rounds=4)
    second = _extend(config_path, apply=True, reopen_closed_evolution=True)
    assert second["reopened_closed_evolution"]["archive_dir"] == (
        "superseded/closure-02"
    )
    assert second["reopened_closed_evolution"]["closed_rounds"] == 3
    assert (archive / "reopen.json").is_file()
    assert len(read_json(ledger)["amendments"]) == 2


def test_reopen_closed_evolution_refusals(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = make_example_project(tmp_path, rounds=1)
    CampaignController(load_config(config_path)).prepare()
    _set_campaign(config_path, rounds=2)
    with pytest.raises(ConfigurationError, match="has not closed"):
        _extend(config_path, reopen_closed_evolution=True)

    _set_campaign(config_path, rounds=1)
    campaign_dir = Path(
        CampaignController(load_config(config_path)).run()["campaign_dir"]
    )
    ledger = campaign_dir / "campaign_amendments.json"
    _set_campaign(config_path, rounds=2)

    stage_path = campaign_dir / "stages" / "evaluation.C1.train" / "stage.json"
    original = read_json(stage_path)
    atomic_write_json(stage_path, {**original, "status": "interrupted"})
    with pytest.raises(IntegrityError, match="every stage is completed"):
        _extend(config_path, apply=True, reopen_closed_evolution=True)
    atomic_write_json(stage_path, original)

    selection_path = campaign_dir / "selection" / "selection.json"
    selection_bytes = selection_path.read_bytes()
    selection_path.write_text("{}", encoding="utf-8")
    with pytest.raises(IntegrityError, match="hash mismatch"):
        _extend(config_path, apply=True, reopen_closed_evolution=True)
    selection_path.write_bytes(selection_bytes)
    assert not (campaign_dir / "superseded").exists()
    assert not ledger.exists()

    args = [
        "extend-rounds",
        "--config",
        str(config_path),
        "--reason",
        "more",
        "--reopen-closed-evolution",
    ]
    assert cli_main(args) == 0
    output = capsys.readouterr().out
    assert '"status": "dry_run"' in output
    assert '"reopened_closed_evolution"' in output
    assert cli_main([*args, "--apply"]) == 0
    assert '"status": "applied"' in capsys.readouterr().out
    assert (campaign_dir / "superseded" / "closure-01" / "reopen.json").is_file()


def _evolve_fails_while(marker: Path) -> type[ScriptedMetaBackend]:
    class Backend(ScriptedMetaBackend):
        def run_evolve(self, **kwargs):  # type: ignore[no-untyped-def]
            if marker.exists():
                raise InfrastructureError("injected model server crash")
            return super().run_evolve(**kwargs)

    return Backend


def _controller_with(
    config_path: Path, backend: type[ScriptedMetaBackend]
) -> CampaignController:
    controller = CampaignController(load_config(config_path))
    controller.meta = backend(controller.meta.context)
    return controller


def test_retry_stage_reopens_an_infrastructure_exhausted_stage(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = make_example_project(tmp_path, rounds=1)
    marker = tmp_path / "server-down"
    marker.touch()
    backend = _evolve_fails_while(marker)
    with pytest.raises(StageInterruptedError):
        _controller_with(config_path, backend).run()
    with pytest.raises(StageInterruptedError):
        _controller_with(config_path, backend).resume()
    with pytest.raises(StageBlockedError):
        _controller_with(config_path, backend).resume()

    controller = CampaignController(load_config(config_path))
    campaign_dir = controller.config.campaign_dir
    stage_path = campaign_dir / "stages" / "round.1.evolve" / "stage.json"
    assert [row["name"] for row in controller.status()["blocked_stages"]] == [
        "round.1.evolve"
    ]

    args = [
        "retry-stage",
        "--config",
        str(config_path),
        "--stage",
        "round.1.evolve",
        "--reason",
        "model server crashed",
    ]
    assert cli_main(args) == 0
    report = capsys.readouterr().out
    assert '"kind": "infrastructure_reopen"' in report
    assert '"allowed_attempts": 6' in report
    stage = read_json(stage_path)
    assert stage["status"] == "interrupted"
    assert stage["retryable"] is True
    assert stage["attempt_allowance"] == 6
    assert stage["manual_retries"][0]["reason"] == "model server crashed"
    assert cli_main(args) == 2
    assert not (campaign_dir / "campaign_amendments.json").exists()

    marker.unlink()
    status = _controller_with(config_path, backend).resume()
    assert status["checkpoints"] == ["C0", "C1"]
    assert status["blocked_stages"] == []
    assert status["final_test"]["no_post_test_evolution"] is True
    assert [row["status"] for row in read_json(stage_path)["attempts"]] == [
        "infrastructure_failed",
        "infrastructure_failed",
        "infrastructure_failed",
        "completed",
    ]
