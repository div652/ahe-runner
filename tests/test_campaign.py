from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from ahe_runner.config import load_config
from ahe_runner.controller import CampaignController
from ahe_runner.errors import InfrastructureError, IntegrityError, StageInterruptedError
from ahe_runner.meta.scripted import ScriptedMetaBackend
from ahe_runner.workspace import git_output

from .conftest import make_example_project


def test_full_deterministic_campaign_and_idempotent_resume(tmp_path: Path) -> None:
    config_path = make_example_project(tmp_path, rounds=2)
    controller = CampaignController(load_config(config_path))

    validation = controller.validate_project()
    assert validation["harness"]["valid"] is True

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
        campaign_dir
        / "checkpoints"
        / selected
        / "harness"
        / "change_manifest.json"
    ).is_file()

    attempts_before = {
        row["name"]: len(row["attempts"]) for row in status["stages"]
    }
    resumed = CampaignController(load_config(config_path)).resume()
    attempts_after = {
        row["name"]: len(row["attempts"]) for row in resumed["stages"]
    }
    assert attempts_after == attempts_before
    assert resumed["selection"] == status["selection"]
    assert resumed["final_test"] == status["final_test"]


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
