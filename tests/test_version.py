from __future__ import annotations

import importlib.metadata
import tomllib
from pathlib import Path

from ahe_runner import __version__
from ahe_runner.config import load_config
from ahe_runner.controller import CampaignController
from ahe_runner.io import read_json

from .conftest import make_example_project


def test_release_version_matches_metadata_and_fresh_campaign(
    tmp_path: Path,
) -> None:
    repository = Path(__file__).resolve().parents[1]
    declared = tomllib.loads(
        (repository / "pyproject.toml").read_text(encoding="utf-8")
    )["project"]["version"]

    assert importlib.metadata.version("ahe-runner") == declared
    assert __version__ == declared

    config_path = make_example_project(tmp_path, rounds=0)
    controller = CampaignController(load_config(config_path))
    controller.prepare()
    manifest = read_json(controller.config.campaign_dir / "campaign_manifest.json")
    assert manifest["runner_version"] == declared
