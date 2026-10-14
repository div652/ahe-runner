from __future__ import annotations

import inspect
import json
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar

import pytest
import yaml
from nexau.archs.main_sub.execution.hooks import AfterToolHookInput

import ahe_runner.meta.nexau_backend as backend_module
from ahe_runner.config import load_config
from ahe_runner.controller import CampaignController
from ahe_runner.errors import ConfigurationError, InfrastructureError, IntegrityError
from ahe_runner.evidence import EvidenceTask
from ahe_runner.io import atomic_write_json, sha256_file
from ahe_runner.meta import guarded_tools, tools
from ahe_runner.meta.base import MetaBackend
from ahe_runner.meta.middleware import DebuggerAccessRecorderMiddleware
from ahe_runner.meta.nexau_backend import NexAUMetaBackend

from .conftest import make_example_project
from .meta_fakes import FakeAgentState

_AgentState = FakeAgentState


def _read(
    state: FakeAgentState,
    file_path: str,
    *,
    offset: int | None = None,
    limit: int | None = None,
) -> dict[str, object]:
    """Call read_file and the recorder, as NexAU's after_tool chain does."""

    output = guarded_tools.read_file(
        file_path,
        offset=offset,
        limit=limit,
        agent_state=state,  # type: ignore[arg-type]
    )
    DebuggerAccessRecorderMiddleware().after_tool(
        AfterToolHookInput(
            agent_state=state,  # type: ignore[arg-type]
            sandbox=None,
            tool_name="read_file",
            tool_call_id="call-1",
            tool_input={"file_path": file_path, "offset": offset, "limit": limit},
            tool_output=output,
        )
    )
    return output


def _nexau_controller(
    tmp_path: Path, monkeypatch, **meta: object
) -> CampaignController:
    config_path = make_example_project(tmp_path)
    value = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    value["meta"]["backend"] = "nexau"
    value["meta"]["explore"]["web_search_backend"] = "ddgs"
    value["meta"].update(meta)
    value["transport"]["api_key_env"] = "AHE_TEST_META_KEY"
    config_path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")
    monkeypatch.setenv("AHE_TEST_META_KEY", "synthetic-meta-key")
    return CampaignController(load_config(config_path))


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
        module = (
            guarded_tools if payload["name"] in backend_module._GUARDED_TOOLS else tools
        )
        function = getattr(module, payload["name"])
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
    assert "ROLLBACK + PIVOT" in prompts["evolve.md"]
    assert "restore_checkpoint_paths" in prompts["evolve.md"]
    assert "submit_debugger_diagnosis" in prompts["debugger.md"]
    assert "at least one passing and one failing trial" in prompts["debugger.md"]
    assert "Your first tool call must be `write_file`" in prompts["source_explorer.md"]
    assert "Your first tool call must be `write_file`" in prompts["web_explorer.md"]


def test_generated_production_agent_config_loads_with_native_skill(
    tmp_path: Path,
    monkeypatch,
) -> None:
    secret = "synthetic-meta-key-that-must-not-be-persisted"
    config_path = make_example_project(tmp_path)
    value = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    value["meta"]["backend"] = "nexau"
    value["meta"]["explore"]["web_search_backend"] = "ddgs"
    value["models"]["meta"]["model"] = "exact-meta-model"
    value["transport"]["api_key_env"] = "AHE_TEST_META_KEY"
    config_path.write_text(
        yaml.safe_dump(value, sort_keys=False),
        encoding="utf-8",
    )
    supplement = config_path.parent / "meta_supplements" / "evolve.md"
    supplement.write_text(
        supplement.read_text(encoding="utf-8")
        + "\nPreserve this literal template token: {{ benchmark_metric }}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("AHE_TEST_META_KEY", secret)
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
            assert config.llm_config.api_key == secret
            assert config.system_prompt_type == "string"
            assert config.system_prompt == ""
            assert "{{ benchmark_metric }}" in config.system_prompt_suffix
            self.full_trace = []
            self.global_storage = SimpleNamespace(get=lambda _key: None)

        def run(self, **kwargs):  # type: ignore[no-untyped-def]
            assert kwargs["message"].startswith("smoke query\n\n")
            return "smoke complete"

        def sync_cleanup(self) -> None:
            return None

    monkeypatch.setattr(backend_module, "Agent", FakeAgent)
    backend = controller.meta
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    result = backend._run_agent(
        name="AHE-Config-Smoke",
        description="Load a production tool and native skill.",
        prompt_name="evolve.md",
        role="evolve",
        max_iterations=2,
        tools=(
            *backend_module._READ_TOOLS,
            *backend_module._WRITE_TOOLS,
            "run_shell_command",
            "complete_task",
            "submit_debugger_diagnosis",
            "checkpoint_log",
            "checkpoint_diff",
            "restore_checkpoint_paths",
        ),
        skills=(skill,),
        context={"meta_write_roots": {"workspace": str(workspace)}},
        query="smoke query",
        runtime_dir=tmp_path / "runtime",
        default_root="workspace",
    )
    assert result == "smoke complete"
    loaded = backend_module.AgentConfig.from_yaml(tmp_path / "runtime" / "agent.yaml")
    assert loaded.skills[0].name == "smoke"
    assert loaded.retry_attempts == 1
    bindings = {tool.name: _binding(tool) for tool in loaded.tools}
    assert bindings["read_file"] == "ahe_runner.meta.guarded_tools:read_file"
    assert bindings["run_shell_command"] == (
        "ahe_runner.meta.guarded_tools:run_shell_command"
    )
    assert bindings["complete_task"] == "ahe_runner.meta.tools:complete_task"
    runtime_files = [
        path.read_text(encoding="utf-8", errors="replace")
        for path in (tmp_path / "runtime").rglob("*")
        if path.is_file()
    ]
    assert all(secret not in text for text in runtime_files)
    assert "AHE_RUNTIME_INJECTED" in (tmp_path / "runtime" / "agent.yaml").read_text(
        encoding="utf-8"
    )


def test_meta_agent_skill_copies_stay_in_private_runtime_sandbox(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = make_example_project(tmp_path)
    value = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    value["meta"]["backend"] = "nexau"
    value["meta"]["explore"]["web_search_backend"] = "ddgs"
    value["transport"]["api_key_env"] = "AHE_TEST_META_KEY"
    config_path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")
    monkeypatch.setenv("AHE_TEST_META_KEY", "synthetic-meta-key")
    controller = CampaignController(load_config(config_path))
    skill = tmp_path / "skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: smoke\ndescription: smoke skill\n---\n\n# Smoke\n",
        encoding="utf-8",
    )
    skill.chmod(0o555)
    launch_dir = tmp_path / "operator-cwd"
    launch_dir.mkdir()
    monkeypatch.chdir(launch_dir)
    runtime_dir = tmp_path / "runtime"
    real_agent = backend_module.Agent

    class SandboxStartingAgent:
        def __init__(self, *, config):  # type: ignore[no-untyped-def]
            self.agent = real_agent(config=config)
            self.full_trace = []
            self.global_storage = SimpleNamespace(get=lambda _key: None)

        def run(self, **_kwargs):  # type: ignore[no-untyped-def]
            sandbox = self.agent.sandbox_manager.start_sync()
            assert sandbox is not None
            return "started"

        def sync_cleanup(self) -> None:
            self.agent.sync_cleanup()

    monkeypatch.setattr(backend_module, "Agent", SandboxStartingAgent)
    for _attempt in range(2):
        result = controller.meta._run_agent(
            name="AHE-Sandbox-Smoke",
            description="Start the meta sandbox without a model call.",
            prompt_name="evolve.md",
            role="evolve",
            max_iterations=2,
            tools=("complete_task",),
            skills=(skill,),
            context={"meta_read_roots": {"evidence": str(tmp_path)}},
            query="smoke query",
            runtime_dir=runtime_dir,
            default_root="evidence",
        )
        assert result == "started"
        assert (runtime_dir / "sandbox" / ".skills" / "skill" / "SKILL.md").is_file()
        assert not (launch_dir / ".skills").exists()
    skill.chmod(0o755)


def _binding(tool: object) -> str:
    implementation = getattr(tool, "implementation", None)
    if implementation is not None:
        return f"{implementation.__module__}:{implementation.__name__}"
    return str(tool.implementation_import_path)


class _RecordingAgent:
    seen: ClassVar[dict[str, object]] = {}

    def __init__(self, *, config):  # type: ignore[no-untyped-def]
        type(self).seen["middlewares"] = [
            type(item).__name__ for item in config.middlewares
        ]
        self.full_trace = []
        self.global_storage = SimpleNamespace(get=lambda _key: None)

    def run(self, **kwargs):  # type: ignore[no-untyped-def]
        type(self).seen["context"] = kwargs["context"]
        type(self).seen["message"] = kwargs["message"]
        return "done"

    def sync_cleanup(self) -> None:
        return None


def test_run_agent_wires_the_original_middleware_and_roots(
    tmp_path: Path,
    monkeypatch,
) -> None:
    controller = _nexau_controller(
        tmp_path,
        monkeypatch,
        shell={"backend": "docker", "image": "ahe-meta-shell:test"},
    )
    monkeypatch.setattr(backend_module, "Agent", _RecordingAgent)
    seen = _RecordingAgent.seen
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    skill = tmp_path / "skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: smoke\ndescription: smoke skill\n---\n\n# Smoke\n",
        encoding="utf-8",
    )
    caller_context = {
        "meta_role": "evolve",
        "meta_read_roots": {"evidence": str(tmp_path)},
        "meta_write_roots": {"workspace": str(workspace)},
    }
    runtime_dir = tmp_path / "runtime"
    controller.meta._run_agent(
        name="AHE-Wiring-Smoke",
        description="Load the original middleware without a model call.",
        prompt_name="evolve.md",
        role="evolve",
        max_iterations=2,
        tools=("read_file", "write_file", "run_shell_command", "complete_task"),
        skills=(skill,),
        context=caller_context,
        query="smoke query",
        runtime_dir=runtime_dir,
        default_root="workspace",
    )
    tool_outputs = runtime_dir / "tool_outputs"
    assert tool_outputs.is_dir()
    assert seen["middlewares"] == [
        "AheLongToolOutputMiddleware",
        "AheContextCompactionMiddleware",
    ]
    agent_yaml = yaml.safe_load((runtime_dir / "agent.yaml").read_text("utf-8"))
    assert agent_yaml["middlewares"][0]["params"] == {
        "max_output_chars": 10_000,
        "head_lines": 50,
        "tail_lines": 30,
        "head_chars": 5_000,
        "tail_chars": 5_000,
        "temp_dir": str(tool_outputs),
        "bypass_tool_names": ["run_shell_command", "LoadSkill", "complete_task"],
    }
    assert agent_yaml["middlewares"][1]["params"] == {
        "max_context_tokens": 220_000,
        "keep_iterations": 10,
        "auto_compact": True,
        "threshold": 0.75,
        "compaction_strategy": "tool_result_compaction",
    }
    context = seen["context"]
    assert isinstance(context, dict)
    assert context["meta_read_roots"] == {
        "evidence": str(tmp_path),
        "tool_outputs": str(tool_outputs),
        "loaded_skills": str(runtime_dir / "sandbox" / ".skills"),
    }
    assert context["meta_write_roots"] == {"workspace": str(workspace)}
    assert context["meta_default_root"] == "workspace"
    assert context["meta_tool_output_dir"] == str(tool_outputs)
    assert context["meta_shell"]["backend"] == "docker"
    assert "tool_outputs" not in caller_context["meta_read_roots"]  # type: ignore[operator]
    message = str(seen["message"])
    assert message.startswith("smoke query\n\n")
    assert f"- workspace (writable): {workspace}" in message
    assert f"- tool_outputs (read-only): {tool_outputs}" in message
    assert "run_shell_command" in message

    debugger_runtime = tmp_path / "runtime-debugger"
    controller.meta._run_agent(
        name="AHE-Debugger-Wiring",
        description="Debugger wiring without a model call.",
        prompt_name="debugger.md",
        role="debugger",
        max_iterations=2,
        tools=("read_file", "submit_debugger_diagnosis"),
        stop_tools=("submit_debugger_diagnosis",),
        skills=(),
        context={"meta_read_roots": {"evidence": str(tmp_path)}},
        query="smoke query",
        runtime_dir=debugger_runtime,
        default_root="evidence",
    )
    assert seen["middlewares"] == [
        "DebuggerAccessRecorderMiddleware",
        "AheLongToolOutputMiddleware",
        "AheContextCompactionMiddleware",
    ]
    context = seen["context"]
    assert isinstance(context, dict)
    assert "meta_shell" not in context
    assert "loaded_skills" not in context["meta_read_roots"]
    debugger_yaml = yaml.safe_load((debugger_runtime / "agent.yaml").read_text("utf-8"))
    assert debugger_yaml["middlewares"][1]["params"]["bypass_tool_names"] == [
        "run_shell_command",
        "LoadSkill",
        "submit_debugger_diagnosis",
    ]

    for bad_context, default_root, message_text in (
        (
            {"meta_read_roots": {"tool_outputs": str(tmp_path)}},
            "tool_outputs",
            "reserved",
        ),
        ({"meta_read_roots": {"evidence": str(tmp_path)}}, "workspace", "default"),
    ):
        with pytest.raises(IntegrityError, match=message_text):
            controller.meta._run_agent(
                name="AHE-Root-Clash",
                description="Invalid roots are rejected.",
                prompt_name="evolve.md",
                role="evolve",
                max_iterations=2,
                tools=("read_file",),
                skills=(),
                context=bad_context,
                query="smoke query",
                runtime_dir=tmp_path / "runtime-clash",
                default_root=default_root,
            )


def test_middleware_settings_match_the_original_agent_yamls() -> None:
    assert backend_module._LONG_OUTPUT_PARAMS["explorers"] == {
        "max_output_chars": 20_000,
        "head_lines": 100,
        "tail_lines": 80,
    }
    assert backend_module._LONG_OUTPUT_BYPASS == {
        "debugger": ("run_shell_command",),
        "evolve": ("run_shell_command",),
        "explorers": (),
    }
    assert backend_module._COMPACTION_PARAMS["explorers"] == {
        "max_context_tokens": 200_000,
        "keep_iterations": 15,
    }
    assert (
        backend_module._LONG_OUTPUT_PARAMS["debugger"]
        == backend_module._LONG_OUTPUT_PARAMS["evolve"]
    )


def test_role_settings_override_only_what_is_set(tmp_path: Path, monkeypatch) -> None:
    controller = _nexau_controller(
        tmp_path,
        monkeypatch,
        roles={"debugger": {"temperature": 0.4, "max_tokens": 20_000}},
    )
    base = controller.config.document.models.meta
    debugger = controller.meta._role_settings("debugger")
    assert (debugger.temperature, debugger.max_tokens) == (0.4, 20_000)
    assert debugger.model == base.model
    assert controller.meta._role_settings("evolve") is base


def test_shell_image_is_checked_once_and_fails_as_configuration(
    tmp_path: Path,
    monkeypatch,
) -> None:
    controller = _nexau_controller(
        tmp_path,
        monkeypatch,
        shell={"backend": "docker", "image": "ahe-meta-shell:test"},
    )
    calls: list[list[str]] = []

    def fake_run(argv, **_kwargs):  # type: ignore[no-untyped-def]
        calls.append(list(argv))
        return SimpleNamespace(returncode=1, stderr="No such image")

    monkeypatch.setattr(backend_module.subprocess, "run", fake_run)
    with pytest.raises(ConfigurationError, match="not available locally"):
        controller.meta._require_shell_image()
    assert calls == [["docker", "image", "inspect", "ahe-meta-shell:test"]]
    monkeypatch.setattr(
        backend_module.subprocess,
        "run",
        lambda argv, **_kwargs: (
            calls.append(list(argv)) or SimpleNamespace(returncode=0, stderr="")
        ),
    )
    controller.meta._require_shell_image()
    controller.meta._require_shell_image()
    assert len(calls) == 2


def test_stage_inputs_change_only_when_roles_or_shell_are_set(
    tmp_path: Path,
    monkeypatch,
) -> None:
    plain = _nexau_controller(tmp_path / "plain", monkeypatch)
    assert plain._meta_stage_inputs("debugger") == {}
    configured = _nexau_controller(
        tmp_path / "configured",
        monkeypatch,
        roles={"evolve": {"temperature": 0.7}},
        shell={"backend": "docker", "image": "ahe-meta-shell:test"},
    )
    inputs = configured._meta_stage_inputs("evolve")
    assert inputs["meta_role_overrides"] == {"temperature": 0.7}
    assert inputs["meta_shell"]["image"] == "ahe-meta-shell:test"
    assert "meta_role_overrides" not in configured._meta_stage_inputs("debugger")


def test_meta_agents_mount_only_declared_public_adapter_roots(
    tmp_path: Path,
) -> None:
    config_path = make_example_project(tmp_path)
    value = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    value["meta"]["backend"] = "nexau"
    value["meta"]["explore"]["web_search_backend"] = "ddgs"
    config_path.write_text(
        yaml.safe_dump(value, sort_keys=False),
        encoding="utf-8",
    )
    controller = CampaignController(load_config(config_path))
    backend = controller.meta
    adapter_source = Path(inspect.getsourcefile(type(controller.adapter)) or "")
    assert "validation-confirm-actions" in adapter_source.read_text(encoding="utf-8")
    assert backend._safe_source_roots == {  # type: ignore[attr-defined]
        "adapter_public_1": config_path.parent / "benchmark_public" / "ADAPTER.md"
    }
    assert adapter_source not in backend._safe_source_roots.values()  # type: ignore[attr-defined]
    authorized_text = "\n".join(
        path.read_text(encoding="utf-8")
        for root in backend._safe_source_roots.values()  # type: ignore[attr-defined]
        for path in ([root] if root.is_file() else root.rglob("*"))
        if path.is_file()
    )
    assert "validation-confirm-actions" not in authorized_text
    assert "validation-confirm-actions" not in json.dumps(
        controller.prompt_context.model_dump(mode="json")
    )


def test_role_supplements_append_without_replacing_core_prompts(
    tmp_path: Path,
) -> None:
    config_path = make_example_project(tmp_path)
    value = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    value["meta"]["backend"] = "nexau"
    value["meta"]["explore"]["web_search_backend"] = "ddgs"
    value["meta"]["explore"]["research_seed_urls"] = [
        "https://example.com/agent-reliability"
    ]
    config_path.write_text(
        yaml.safe_dump(value, sort_keys=False),
        encoding="utf-8",
    )
    backend = CampaignController(load_config(config_path)).meta
    roles = {
        "source_explorer.md": "Describe the public adapter contract",
        "web_explorer.md": "Research transferable tool-agent reliability methods",
        "debugger.md": "Use the adapter-provided failure taxonomy",
        "evolve.md": "Prefer one reusable reliability intervention",
    }
    for prompt_name, supplement_text in roles.items():
        resolved = backend._resolved_prompt_text(prompt_name)  # type: ignore[attr-defined]
        core = (
            Path(backend_module.__file__).parent / "prompts" / prompt_name
        ).read_text(encoding="utf-8")
        assert resolved.startswith(core.rstrip())
        assert "Protected project domain supplement" in resolved
        assert supplement_text in resolved

    query = backend_module._explorer_query(
        kind="web",
        benchmark_context={"objective": "reliability"},
        benchmark_interface={"tool_delivery": "none"},
        research_seed_urls=["https://example.com/agent-reliability"],
    )
    assert "Required research seed URLs" in query
    assert "https://example.com/agent-reliability" in query
    evolve_query = backend_module._evolve_query(
        round_label="C1",
        evidence={"checkpoint": "C0"},
        recovery_present=False,
        benchmark_interface={"tool_delivery": "none"},
        benchmark_context={"failure_taxonomy": ["domain-specific-failure"]},
    )
    assert "Resolved benchmark-family context" in evolve_query
    assert "domain-specific-failure" in evolve_query


def test_failed_completion_uses_nexau_repairable_error_shape() -> None:
    result = tools.complete_task(final_summary="", agent_state=None)
    assert result == {
        "status": "error",
        "ok": False,
        "error": "final_summary cannot be empty",
    }
    assert json.loads(json.dumps(result))["status"] == "error"


def test_debugger_submission_requires_read_evidence_and_paired_analysis(
    tmp_path: Path,
) -> None:
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    detail = evidence / "task.json"
    detail.write_text('{"trials": ["pass", "fail"]}\n', encoding="utf-8")
    passing_trace = evidence / "passing.jsonl"
    passing_trace.write_text('{"event": "verified before acting"}\n', encoding="utf-8")
    failing_trace = evidence / "failing.jsonl"
    failing_trace.write_text(
        '{"event": "acted before verification"}\n',
        encoding="utf-8",
    )
    output = tmp_path / "output"
    state = _AgentState(
        {
            "meta_role": "debugger",
            "meta_read_roots": {"evidence": str(evidence)},
            "meta_write_roots": {},
            "meta_output_dir": str(output),
            "debugger_task": {"task_id": "task-1", "task_key": "opaque-1"},
            "debugger_task_key": "opaque-1",
            "debugger_primary_artifact": "evidence:task.json",
            "debugger_trial_classes": ["passing", "failing"],
            "debugger_artifact_classes": {
                "evidence:passing.jsonl": "passing",
                "evidence:failing.jsonl": "failing",
            },
        }
    )
    for relative_path in ("task.json", "passing.jsonl", "failing.jsonl"):
        assert "error" not in _read(state, f"evidence:{relative_path}", limit=1)

    result = tools.submit_debugger_diagnosis(
        primary_failure_class="prompt",
        earliest_failure="The failing trial acts before verification.",
        evidence=[
            {
                "citation": {
                    "artifact": str(detail),
                    "line_start": 1,
                    "line_end": 1,
                },
                "observation": "The task detail exposes one pass and one failure.",
                "implication": "A paired comparison is possible.",
            }
        ],
        evidence_coverage={
            "artifacts_read": [
                "evidence:task.json",
                "evidence:passing.jsonl",
                "evidence:failing.jsonl",
            ],
            "limitations": [],
        },
        paired_comparison={
            "status": "performed",
            "passing_evidence": [
                {
                    "citation": {
                        "artifact": "evidence:passing.jsonl",
                        "line_start": 1,
                        "line_end": 1,
                    },
                    "observation": "The passing trial verifies before acting.",
                }
            ],
            "failing_evidence": [
                {
                    "citation": {
                        "artifact": "evidence:failing.jsonl",
                        "line_start": 1,
                        "line_end": 1,
                    },
                    "observation": "The failing trial acts immediately.",
                }
            ],
            "earliest_divergence": "verification before the first action",
            "limitation": "",
        },
        root_cause="The parent prompt does not make verification a precondition.",
        contributing_factors=[],
        recommended_changes=[
            {
                "component_class": "system_prompt",
                "change": "Require verification before consequential actions.",
                "expected_effect": "The first action follows a verification step.",
                "risk": "Additional verification may increase latency.",
                "verification": "Compare the next pass/fail trial pair.",
            }
        ],
        recurrent_pattern_key="missing-pre-action-verification",
        confidence=0.9,
        falsifier="A failing trial that verifies first would falsify this cause.",
        agent_state=state,  # type: ignore[arg-type]
    )
    assert result["ok"] is True
    diagnosis = json.loads(
        (output / "submitted_diagnosis.json").read_text(encoding="utf-8")
    )
    assert diagnosis["schema_version"] == 3
    assert diagnosis["paired_comparison"]["status"] == "performed"
    assert diagnosis["evidence"][0]["citation"]["artifact"] == "evidence:task.json"

    rejected = tools.submit_debugger_diagnosis(
        primary_failure_class="none",
        earliest_failure="No failure was observed.",
        evidence=[
            {
                "citation": {
                    "artifact": "evidence:missing.json",
                    "line_start": 1,
                    "line_end": 1,
                },
                "observation": "Claimed observation.",
                "implication": "Claimed implication.",
            }
        ],
        evidence_coverage={
            "artifacts_read": [
                "evidence:task.json",
                "evidence:missing.json",
            ],
            "limitations": ["The claimed file was not actually read."],
        },
        paired_comparison={
            "status": "unavailable",
            "passing_evidence": [],
            "failing_evidence": [],
            "earliest_divergence": "",
            "limitation": "No comparable trials were available.",
        },
        root_cause="No supported root cause.",
        contributing_factors=[],
        recommended_changes=[],
        recurrent_pattern_key="",
        confidence=0.1,
        falsifier="Any direct failure evidence.",
        agent_state=state,  # type: ignore[arg-type]
    )
    assert rejected["status"] == "error"
    assert "no recorded read_file" in rejected["error"]

    false_unavailable = tools.submit_debugger_diagnosis(
        primary_failure_class="none",
        earliest_failure="No comparison was attempted.",
        evidence=[
            {
                "citation": {
                    "artifact": "evidence:task.json",
                    "line_start": 1,
                    "line_end": 1,
                },
                "observation": "The task detail was read.",
                "implication": "The available trials must be compared.",
            }
        ],
        evidence_coverage={
            "artifacts_read": ["evidence:task.json"],
            "limitations": [],
        },
        paired_comparison={
            "status": "unavailable",
            "passing_evidence": [],
            "failing_evidence": [],
            "earliest_divergence": "",
            "limitation": "Comparison was incorrectly declared unavailable.",
        },
        root_cause="No comparison was attempted.",
        contributing_factors=[],
        recommended_changes=[],
        recurrent_pattern_key="",
        confidence=0.1,
        falsifier="A valid paired comparison.",
        agent_state=state,  # type: ignore[arg-type]
    )
    assert false_unavailable["status"] == "error"
    assert "paired comparison is required" in false_unavailable["error"]

    empty_state = _AgentState(
        {
            "meta_role": "debugger",
            "meta_read_roots": {"evidence": str(evidence)},
            "meta_write_roots": {},
            "meta_output_dir": str(tmp_path / "empty-output"),
            "debugger_task": {"task_id": "task-1", "task_key": "opaque-1"},
            "debugger_task_key": "opaque-1",
            "debugger_primary_artifact": "evidence:task.json",
            "debugger_trial_classes": ["other"],
            "debugger_artifact_classes": {},
        }
    )
    size = detail.stat().st_size
    _read(empty_state, "evidence:task.json")
    empty_rejected = tools.submit_debugger_diagnosis(
        primary_failure_class="none",
        earliest_failure="No evidence was returned.",
        evidence=[
            {
                "citation": {
                    "artifact": "evidence:task.json",
                    "start_byte": size + 10,
                    "end_byte": size + 20,
                },
                "observation": "An empty range was claimed as evidence.",
                "implication": "The claim is unsupported.",
            }
        ],
        evidence_coverage={
            "artifacts_read": ["evidence:task.json"],
            "limitations": ["Byte ranges are not recorded as reads."],
        },
        paired_comparison={
            "status": "unavailable",
            "passing_evidence": [],
            "failing_evidence": [],
            "earliest_divergence": "",
            "limitation": "No comparable passing and failing trials exist.",
        },
        root_cause="No supported root cause.",
        contributing_factors=[],
        recommended_changes=[],
        recurrent_pattern_key="",
        confidence=0.0,
        falsifier="Any nonempty direct evidence.",
        agent_state=empty_state,  # type: ignore[arg-type]
    )
    assert empty_rejected["status"] == "error"
    assert "not covered" in empty_rejected["error"]


def test_debugger_line_citations_require_complete_returned_lines(
    tmp_path: Path,
) -> None:
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    long_line = '{"event":"' + "x" * 2_100 + '"}'
    lines = ['{"event":"setup"}', '{"event":"UNREAD_FAILURE"}', long_line, "tail"]
    (evidence / "trace.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def make_state(name: str) -> _AgentState:
        return _AgentState(
            {
                "meta_role": "debugger",
                "meta_read_roots": {"evidence": str(evidence)},
                "meta_write_roots": {},
                "meta_default_root": "evidence",
                "meta_output_dir": str(tmp_path / name),
                "debugger_task": {"task_id": "task-1", "task_key": "opaque-1"},
                "debugger_task_key": "opaque-1",
                "debugger_primary_artifact": "evidence:trace.jsonl",
                "debugger_trial_classes": ["other"],
                "debugger_artifact_classes": {},
            },
            work_dir=tmp_path,
        )

    def submit(
        state: _AgentState,
        citation: dict[str, int | str],
    ) -> dict[str, object]:
        return tools.submit_debugger_diagnosis(
            primary_failure_class="none",
            earliest_failure="The cited range is the only asserted evidence.",
            evidence=[
                {
                    "citation": citation,
                    "observation": "The cited range contains the observation.",
                    "implication": "Only returned content may support this claim.",
                }
            ],
            evidence_coverage={
                "artifacts_read": ["evidence:trace.jsonl"],
                "limitations": [],
            },
            paired_comparison={
                "status": "unavailable",
                "passing_evidence": [],
                "failing_evidence": [],
                "earliest_divergence": "",
                "limitation": "No comparable passing and failing trials exist.",
            },
            root_cause="No root cause is inferred beyond the cited content.",
            contributing_factors=[],
            recommended_changes=[],
            recurrent_pattern_key="",
            confidence=0.0,
            falsifier="A complete read could change the observation.",
            agent_state=state,  # type: ignore[arg-type]
        )

    def lines_citation(start: int, end: int) -> dict[str, int | str]:
        return {
            "artifact": "evidence:trace.jsonl",
            "line_start": start,
            "line_end": end,
        }

    state = make_state("union")
    _read(state, "trace.jsonl", limit=1)
    unread = submit(state, lines_citation(2, 2))
    assert unread["status"] == "error"
    assert "not covered" in str(unread["error"])
    assert "evidence:trace.jsonl lines 1-1" in str(unread["error"])

    _read(state, "trace.jsonl", offset=1, limit=1)
    # Two separate reads together cover lines 1-2.
    assert submit(state, lines_citation(1, 2))["ok"] is True

    _read(state, "trace.jsonl", offset=2, limit=2)
    # read_file cut line 3 at 2,000 characters, so it was not fully seen.
    cut = submit(state, lines_citation(3, 3))
    assert cut["status"] == "error"
    assert submit(state, lines_citation(4, 4))["ok"] is True
    assert submit(state, lines_citation(2, 4))["status"] == "error"

    byte_state = make_state("bytes")
    _read(byte_state, "trace.jsonl")
    byte_citation = submit(
        byte_state,
        {"artifact": "evidence:trace.jsonl", "start_byte": 0, "end_byte": 5},
    )
    assert byte_citation["status"] == "error"
    assert "line ranges only" in str(byte_citation["error"])


def test_debugger_task_runs_as_read_only_nexau_agent(
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
    backend = CampaignController(load_config(config_path)).meta
    evidence_dir = tmp_path / "evidence"
    detail = evidence_dir / "tasks" / "opaque.json"
    detail.parent.mkdir(parents=True)
    detail.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "task_key": "opaque",
                "task_id": None,
                "state": "insufficient",
                "trials": [
                    {
                        "unit_id": "unit-1",
                        "trial": 0,
                        "seed": 7,
                        "outcome": "unknown",
                        "score": None,
                        "termination_reason": "complete",
                        "diagnostic_class": "other",
                        "artifacts": [
                            {
                                "kind": "result",
                                "path": "units/unit-1/result.json",
                                "sha256": "a" * 64,
                            },
                            {
                                "kind": "trace",
                                "path": "units/unit-1/trace.jsonl",
                                "sha256": "b" * 64,
                            },
                        ],
                    }
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    task = EvidenceTask(
        task_key="opaque",
        task_id=None,
        state="insufficient",
        detail_path="tasks/opaque.json",
        detail_sha256=sha256_file(detail),
        trial_count=1,
    )

    seen: dict[str, object] = {}

    def fake_run_agent(**kwargs):  # type: ignore[no-untyped-def]
        seen.update(kwargs)
        assert kwargs["tools"] == (
            "read_file",
            "search_file_content",
            "glob",
            "list_directory",
            "write_file",
            "replace",
            "delete_path",
            "submit_debugger_diagnosis",
        )
        assert kwargs["stop_tools"] == ("submit_debugger_diagnosis",)
        assert kwargs["skills"] == ()
        assert kwargs["role"] == "debugger"
        assert kwargs["default_root"] == "evidence"
        scratch = Path(kwargs["runtime_dir"]) / "scratch"
        assert kwargs["context"]["meta_write_roots"] == {"scratch": str(scratch)}
        assert scratch.is_dir() and not any(scratch.iterdir())
        assert (
            kwargs["context"]["debugger_primary_artifact"]
            == "evidence:tasks/opaque.json"
        )
        atomic_write_json(
            Path(kwargs["runtime_dir"]).parent / "submitted_diagnosis.json",
            {
                "schema_version": 3,
                "task_id": None,
                "primary_failure_class": "none",
                "earliest_failure": "No consequential failure is observable.",
                "evidence": [
                    {
                        "citation": {
                            "artifact": "evidence:tasks/opaque.json",
                            "line_start": 1,
                            "line_end": 1,
                        },
                        "observation": "The detail contains no comparable trials.",
                        "implication": "The diagnosis is evidence-limited.",
                    }
                ],
                "evidence_coverage": {
                    "artifacts_read": ["evidence:tasks/opaque.json"],
                    "limitations": ["No trial traces were provided."],
                },
                "paired_comparison": {
                    "status": "unavailable",
                    "passing_evidence": [],
                    "failing_evidence": [],
                    "earliest_divergence": "",
                    "limitation": "No passing or failing trials were provided.",
                },
                "root_cause": "The available evidence cannot establish a root cause.",
                "contributing_factors": [],
                "recommended_changes": [],
                "recurrent_pattern_key": None,
                "confidence": 0.1,
                "falsifier": "A complete task-local trace would permit diagnosis.",
            },
        )
        return "submitted"

    monkeypatch.setattr(backend, "_run_agent", fake_run_agent)
    result = backend._run_debugger_task(
        checkpoint="C0",
        evidence_dir=evidence_dir,
        output_dir=tmp_path / "debugger",
        task=task,
    )
    assert result["status"] == "diagnosed"
    assert result["diagnosis"]["paired_comparison"]["status"] == "unavailable"
    assert result["raw_response"] == "submitted"
    assert "Readable views" not in str(seen["query"])


def _opaque_debugger_task(tmp_path: Path) -> tuple[Path, EvidenceTask]:
    evidence_dir = tmp_path / "evidence"
    detail = evidence_dir / "tasks" / "opaque.json"
    detail.parent.mkdir(parents=True)
    detail.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "task_key": "opaque",
                "task_id": None,
                "state": "insufficient",
                "trials": [
                    {
                        "unit_id": "unit-1",
                        "trial": 0,
                        "seed": 7,
                        "outcome": "failed",
                        "score": 0.0,
                        "termination_reason": "complete",
                        "diagnostic_class": "failing",
                        "artifacts": [
                            {
                                "kind": "result",
                                "path": "units/unit-1/result.json",
                                "sha256": "a" * 64,
                            },
                            {
                                "kind": "trace",
                                "path": "units/unit-1/trace.jsonl",
                                "sha256": "b" * 64,
                            },
                        ],
                        "readable_artifacts": [
                            {
                                "kind": "readable_trace",
                                "path": "readable/opaque/trial-0/trace.md",
                                "sha256": "c" * 64,
                            }
                        ],
                    }
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    task = EvidenceTask(
        task_key="opaque",
        task_id=None,
        state="insufficient",
        detail_path="tasks/opaque.json",
        detail_sha256=sha256_file(detail),
        trial_count=1,
    )
    return evidence_dir, task


def test_failed_debugger_task_is_unavailable_not_fatal(
    tmp_path: Path,
    monkeypatch,
) -> None:
    backend = _nexau_controller(tmp_path, monkeypatch).meta
    evidence_dir, task = _opaque_debugger_task(tmp_path)
    queries: list[str] = []

    def no_submission(**kwargs):  # type: ignore[no-untyped-def]
        queries.append(kwargs["query"])
        assert (
            kwargs["context"]["debugger_artifact_classes"][
                "evidence:readable/opaque/trial-0/trace.md"
            ]
            == "failing"
        )
        return "gave up"

    monkeypatch.setattr(backend, "_run_agent", no_submission)
    output_dir = tmp_path / "debugger"
    result = backend._run_debugger_task(
        checkpoint="C0",
        evidence_dir=evidence_dir,
        output_dir=output_dir,
        task=task,
    )
    assert result["status"] == "unavailable"
    assert result["diagnosis"] is None
    assert result["error"]["type"] == "IntegrityError"
    assert result["evidence_detail"] == "evidence:tasks/opaque.json"
    stored = json.loads(
        (output_dir / "tasks" / result["task_key"] / "result.json").read_text()
    )
    assert stored == result
    assert "evidence:readable/opaque/trial-0/trace.md" in queries[0]

    summary = backend_module._debugger_summary("C0", [result])
    assert summary["unavailable_task_count"] == 1
    overview = backend_module._debugger_overview("C0", [result])
    assert "unavailable" in overview
    assert "evidence:tasks/opaque.json" in overview

    monkeypatch.setattr(
        backend,
        "_run_agent",
        lambda **_kwargs: pytest.fail("cached unavailable task was rerun"),
    )
    assert backend._run_debugger_task(
        checkpoint="C0",
        evidence_dir=evidence_dir,
        output_dir=output_dir,
        task=task,
    ) == result

    def infrastructure(**_kwargs):  # type: ignore[no-untyped-def]
        raise InfrastructureError("model endpoint is down")

    monkeypatch.setattr(backend, "_run_agent", infrastructure)
    with pytest.raises(InfrastructureError):
        backend._run_debugger_task(
            checkpoint="C0",
            evidence_dir=evidence_dir,
            output_dir=tmp_path / "debugger-2",
            task=task,
        )
