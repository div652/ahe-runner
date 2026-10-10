from __future__ import annotations

from pathlib import Path

import yaml

from ahe_runner.cli import init_project


def make_example_project(
    root: Path,
    *,
    rounds: int = 1,
) -> Path:
    project = root / "project"
    init_project(project)
    config_path = project / "ahe.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config["campaign"]["rounds"] = rounds
    config_path.write_text(
        yaml.safe_dump(config, sort_keys=False),
        encoding="utf-8",
    )
    return config_path
