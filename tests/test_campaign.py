from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from ahe_runner.adapter import EvaluationResult
from ahe_runner.config import load_config
from ahe_runner.controller import CampaignController
from ahe_runner.errors import InfrastructureError, IntegrityError, StageInterruptedError
from ahe_runner.io import atomic_write_json, read_json
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
