from __future__ import annotations

import inspect
from pathlib import Path
from types import SimpleNamespace

import yaml

import ahe_runner.meta.nexau_backend as backend_module
from ahe_runner.config import load_config
from ahe_runner.controller import CampaignController
from ahe_runner.meta import tools
from ahe_runner.meta.base import MetaBackend
from ahe_runner.meta.nexau_backend import NexAUMetaBackend

from .conftest import make_example_project


def test_nexau_backend_implements_complete_backend_contract() -> None:
    assert issubclass(NexAUMetaBackend, MetaBackend)
    assert not inspect.isabstract(NexAUMetaBackend)


def test_tool_descriptors_match_injected_agent_state_bindings() -> None:
    root = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "ahe_runner"
        / "meta"
        / "tool_descriptions"
    )
    descriptors = sorted(root.glob("*.tool.yaml"))
    assert descriptors
    for path in descriptors:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert payload["type"] == "tool"
        assert payload["name"] == path.name.removesuffix(".tool.yaml")
        function = getattr(tools, payload["name"])
        signature = inspect.signature(function)
        assert "agent_state" in signature.parameters


def test_prompts_are_substantive_and_require_native_explore_skills() -> None:
    prompt_root = (
        Path(__file__).resolve().parents[1] / "src" / "ahe_runner" / "meta" / "prompts"
    )
    prompts = {
        path.name: path.read_text(encoding="utf-8") for path in prompt_root.glob("*.md")
    }
    assert set(prompts) == {
        "debugger.md",
        "evolve.md",
        "source_explorer.md",
        "web_explorer.md",
    }
    assert all(len(text) > 2_000 for text in prompts.values())
    assert "LoadSkill" in prompts["evolve.md"]
    assert "change_manifest.json" in prompts["evolve.md"]
    assert "Your first tool call must be `write_file`" in prompts["source_explorer.md"]
    assert "Your first tool call must be `write_file`" in prompts["web_explorer.md"]


def test_generated_production_agent_config_loads_with_native_skill(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = make_example_project(tmp_path)
    value = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    value["meta"]["backend"] = "nexau"
    value["meta"]["explore"]["web_search_backend"] = "ddgs"
    value["models"]["meta"]["model"] = "exact-meta-model"
    config_path.write_text(
        yaml.safe_dump(value, sort_keys=False),
        encoding="utf-8",
    )
    controller = CampaignController(load_config(config_path))
    skill = tmp_path / "skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: smoke\ndescription: smoke skill\n---\n\n# Smoke\n",
        encoding="utf-8",
    )

    class FakeAgent:
        def __init__(self, *, config):  # type: ignore[no-untyped-def]
            self.config = config
            self.full_trace = []
            self.global_storage = SimpleNamespace(get=lambda _key: None)

        def run(self, **kwargs):  # type: ignore[no-untyped-def]
            assert kwargs["message"] == "smoke query"
            return "smoke complete"

        def sync_cleanup(self) -> None:
            return None

    monkeypatch.setattr(backend_module, "Agent", FakeAgent)
    backend = controller.meta
    result = backend._run_agent(
        name="AHE-Config-Smoke",
        description="Load a production tool and native skill.",
        prompt_name="evolve.md",
        settings=controller.config.document.models.meta,
        max_iterations=2,
        tools=("complete_task",),
        skills=(skill,),
        context={},
        query="smoke query",
        runtime_dir=tmp_path / "runtime",
    )
    assert result == "smoke complete"
    loaded = backend_module.AgentConfig.from_yaml(tmp_path / "runtime" / "agent.yaml")
    assert loaded.skills[0].name == "smoke"
