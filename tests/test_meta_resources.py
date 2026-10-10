from __future__ import annotations

import inspect
from pathlib import Path

import yaml

from ahe_runner.meta import tools
from ahe_runner.meta.base import MetaBackend
from ahe_runner.meta.nexau_backend import NexAUMetaBackend


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
