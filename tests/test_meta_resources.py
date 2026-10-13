from __future__ import annotations

import inspect
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

import ahe_runner.meta.nexau_backend as backend_module
from ahe_runner.config import load_config
from ahe_runner.controller import CampaignController
from ahe_runner.errors import IntegrityError
from ahe_runner.evidence import EvidenceTask
from ahe_runner.io import atomic_write_json, sha256_file
from ahe_runner.meta import tools
from ahe_runner.meta.base import MetaBackend
from ahe_runner.meta.nexau_backend import NexAUMetaBackend

from .conftest import make_example_project


class _Storage:
    def __init__(self) -> None:
        self.values: dict[str, object] = {}

    def get(self, key: str, default=None):  # type: ignore[no-untyped-def]
        return self.values.get(key, default)

    def set(self, key: str, value: object) -> None:
        self.values[key] = value


class _AgentState:
    def __init__(self, context: dict[str, object]) -> None:
        self.agent_id = "debugger-test"
        self.agent_name = "debugger-test"
        self.global_storage = _Storage()
        self.context = context

    def get_context_value(self, key: str):  # type: ignore[no-untyped-def]
        return self.context.get(key)


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
        tools=(
            "complete_task",
            "submit_debugger_diagnosis",
            "checkpoint_log",
            "checkpoint_diff",
            "restore_checkpoint_paths",
        ),
        skills=(skill,),
        context={},
        query="smoke query",
        runtime_dir=tmp_path / "runtime",
    )
    assert result == "smoke complete"
    loaded = backend_module.AgentConfig.from_yaml(tmp_path / "runtime" / "agent.yaml")
    assert loaded.skills[0].name == "smoke"
    assert loaded.retry_attempts == 1
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
            settings=controller.config.document.models.meta,
            max_iterations=2,
            tools=("complete_task",),
            skills=(skill,),
            context={},
            query="smoke query",
            runtime_dir=runtime_dir,
        )
        assert result == "started"
        assert (runtime_dir / "sandbox" / ".skills" / "skill" / "SKILL.md").is_file()
        assert not (launch_dir / ".skills").exists()
    skill.chmod(0o755)


def test_long_output_backstop_adds_a_private_read_only_root(
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
    seen: dict[str, object] = {}

    class RecordingAgent:
        def __init__(self, *, config):  # type: ignore[no-untyped-def]
            seen["middlewares"] = [type(item).__name__ for item in config.middlewares]
            self.full_trace = []
            self.global_storage = SimpleNamespace(get=lambda _key: None)

        def run(self, **kwargs):  # type: ignore[no-untyped-def]
            seen["context"] = kwargs["context"]
            return "done"

        def sync_cleanup(self) -> None:
            return None

    monkeypatch.setattr(backend_module, "Agent", RecordingAgent)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    caller_context = {
        "meta_role": "evolve",
        "meta_read_roots": {"evidence": str(tmp_path)},
        "meta_write_roots": {"workspace": str(workspace)},
    }
    runtime_dir = tmp_path / "runtime"
    controller.meta._run_agent(
        name="AHE-Backstop-Smoke",
        description="Load the long-output safety net without a model call.",
        prompt_name="evolve.md",
        settings=controller.config.document.models.meta,
        max_iterations=2,
        tools=("list_files", "read_file", "write_file", "complete_task"),
        skills=(),
        context=caller_context,
        query="smoke query",
        runtime_dir=runtime_dir,
        tool_output_backstop=True,
    )
    tool_outputs = runtime_dir / "tool_outputs"
    assert tool_outputs.is_dir()
    assert seen["middlewares"] == [
        "MetaToolOutputBackstopMiddleware",
        "MetaContextCompactionMiddleware",
    ]
    agent_yaml = yaml.safe_load((runtime_dir / "agent.yaml").read_text("utf-8"))
    params = agent_yaml["middlewares"][0]["params"]
    assert params == {
        "output_dir": str(tool_outputs),
        "root_name": "tool_outputs",
        "max_output_chars": 64_000,
        "head_chars": 24_000,
        "tail_chars": 8_000,
        "bypass_tool_names": ["LoadSkill", "complete_task"],
    }
    context = seen["context"]
    assert isinstance(context, dict)
    assert context["meta_read_roots"] == {
        "evidence": str(tmp_path),
        "tool_outputs": str(tool_outputs),
    }
    assert context["meta_write_roots"] == {"workspace": str(workspace)}
    assert "tool_outputs" not in caller_context["meta_read_roots"]  # type: ignore[operator]

    (tool_outputs / "0001-list_files-call.txt").write_text(
        "line one\nline two\n", encoding="utf-8"
    )
    state = _AgentState(context)
    read = json.loads(
        tools.read_file(
            "tool_outputs",
            "0001-list_files-call.txt",
            start_line=2,
            end_line=2,
            agent_state=state,  # type: ignore[arg-type]
        )
    )
    assert read["content"] == "line two\n"
    with pytest.raises(ValueError, match="unknown or prohibited root"):
        tools.write_file(
            "tool_outputs",
            "0001-list_files-call.txt",
            "changed",
            agent_state=state,  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="stay within the selected root"):
        tools.read_file(
            "tool_outputs",
            "../agent.yaml",
            agent_state=state,  # type: ignore[arg-type]
        )

    default_runtime = tmp_path / "runtime-default"
    controller.meta._run_agent(
        name="AHE-No-Backstop",
        description="Debugger-style run without the safety net.",
        prompt_name="debugger.md",
        settings=controller.config.document.models.meta,
        max_iterations=2,
        tools=("read_file",),
        skills=(),
        context={"meta_read_roots": {"evidence": str(tmp_path)}},
        query="smoke query",
        runtime_dir=default_runtime,
    )
    assert seen["middlewares"] == ["MetaContextCompactionMiddleware"]
    assert not (default_runtime / "tool_outputs").exists()

    with pytest.raises(IntegrityError, match="reserved"):
        controller.meta._run_agent(
            name="AHE-Root-Clash",
            description="A caller root may not reuse the reserved name.",
            prompt_name="evolve.md",
            settings=controller.config.document.models.meta,
            max_iterations=2,
            tools=("read_file",),
            skills=(),
            context={"meta_read_roots": {"tool_outputs": str(tmp_path)}},
            query="smoke query",
            runtime_dir=tmp_path / "runtime-clash",
            tool_output_backstop=True,
        )


def test_long_output_backstop_covers_evolve_and_explore_not_debugger() -> None:
    def calls(method: object) -> int:
        return inspect.getsource(method).count("tool_output_backstop=True")  # type: ignore[arg-type]

    assert calls(NexAUMetaBackend.run_evolve) == 1
    assert calls(NexAUMetaBackend._ensure_explorer) == 1
    assert calls(NexAUMetaBackend._run_debugger_task) == 0


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
        roots=["method"],
        benchmark_context={"objective": "reliability"},
        benchmark_interface={"tool_delivery": "none"},
        research_seed_urls=["https://example.com/agent-reliability"],
    )
    assert "Required research seed URLs" in query
    assert "https://example.com/agent-reliability" in query
    evolve_query = backend_module._evolve_query(
        round_label="C1",
        evidence={"checkpoint": "C0"},
        roots=["evidence"],
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


def test_large_meta_files_support_ranged_reads_and_honest_search(
    tmp_path: Path,
) -> None:
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    long_path = evidence / "long-trace.txt"
    long_path.write_text(
        "KNOWN-FIRST-LINE\n" + ("ordinary trace event\n" * 12_000),
        encoding="utf-8",
    )
    single_line = evidence / "single-line.json"
    single_line.write_text("a" * 200_000 + "TAIL", encoding="utf-8")
    (evidence / "binary.bin").write_bytes(b"\xff\xfe\x00")
    state = _AgentState(
        {
            "meta_role": "debugger",
            "meta_read_roots": {"evidence": str(evidence)},
            "meta_write_roots": {},
            "meta_output_dir": str(tmp_path / "output"),
            "debugger_task": {"task_id": "task-1", "task_key": "opaque-1"},
            "debugger_task_key": "opaque-1",
            "debugger_primary_artifact": "evidence:long-trace.txt",
        }
    )

    listing = json.loads(
        tools.list_files(
            root="evidence",
            agent_state=state,  # type: ignore[arg-type]
        )
    )
    assert listing["ok"] is True
    assert {entry["path"] for entry in listing["entries"]} >= {
        "long-trace.txt",
        "single-line.json",
    }

    first_line = json.loads(
        tools.read_file(
            root="evidence",
            relative_path="long-trace.txt",
            start_line=1,
            end_line=1,
            max_bytes=128,
            agent_state=state,  # type: ignore[arg-type]
        )
    )
    assert first_line["ok"] is True
    assert first_line["content"] == "KNOWN-FIRST-LINE\n"
    assert first_line["file_size"] > 160_000
    assert first_line["has_more_after_range"] is True

    tail = json.loads(
        tools.read_file(
            root="evidence",
            relative_path="single-line.json",
            start_byte=199_995,
            end_byte=200_004,
            max_bytes=16,
            agent_state=state,  # type: ignore[arg-type]
        )
    )
    assert tail["content"] == "aaaaaTAIL"
    assert tail["mode"] == "bytes"

    found = json.loads(
        tools.search_text(
            root="evidence",
            query="KNOWN-FIRST-LINE",
            agent_state=state,  # type: ignore[arg-type]
        )
    )
    assert found["matches"][0]["path"] == "long-trace.txt"
    assert found["searched_files"] >= 1

    missing = json.loads(
        tools.search_text(
            root="evidence",
            query="not-present",
            agent_state=state,  # type: ignore[arg-type]
        )
    )
    assert any(
        item["path"] == "binary.bin" and item["reason"] == "non-UTF-8 content"
        for item in missing["skipped"]
    )


def test_search_text_bounds_total_output_and_returns_match_snippets(
    tmp_path: Path,
) -> None:
    # AHE-010: six parallel searches over one-line JSON model-call logs once
    # returned about 617k tokens in a single Evolve turn.
    evidence = tmp_path / "evidence"
    calls = evidence / "runtime" / "model_calls"
    calls.mkdir(parents=True)
    for index in range(60):
        (calls / f"call-{index:04d}.json").write_text(
            "\n".join(
                json.dumps({"pad": "x" * 3_000, "task_key": "KEY-60e254", "n": row})
                for row in range(5)
            )
            + "\n",
            encoding="utf-8",
        )
    (evidence / "short.txt").write_text("KEY-60e254 short line\n", encoding="utf-8")
    state = _AgentState(
        {
            "meta_role": "debugger",
            "meta_read_roots": {"evidence": str(evidence)},
            "meta_write_roots": {},
            "meta_output_dir": str(tmp_path / "output"),
        }
    )

    raw = tools.search_text(
        root="evidence",
        query="KEY-60e254",
        agent_state=state,  # type: ignore[arg-type]
    )
    found = json.loads(raw)
    assert found["ok"] is True
    assert found["truncated"] is True
    assert found["truncation_reason"] == "output_char_limit"
    assert found["output_chars"] <= found["output_char_limit"] == 32_000
    assert len(raw) < 48_000
    assert 0 < len(found["matches"]) < 300
    for match in found["matches"]:
        assert "KEY-60e254" in match["text"]
        assert len(match["text"]) <= 400
    long_match = next(m for m in found["matches"] if m["path"] != "short.txt")
    assert long_match["text_truncated"] is True
    assert long_match["column"] > 3_000
    assert long_match["snippet_start_column"] == long_match["column"] - 150

    accesses = tools._debugger_accesses(state)  # type: ignore[arg-type]
    assert all("model_calls" not in str(item["artifact"]) for item in accesses)

    short = json.loads(
        tools.search_text(
            root="evidence",
            query="KEY-60e254",
            file_glob="*.txt",
            agent_state=state,  # type: ignore[arg-type]
        )
    )
    assert short["truncated"] is False
    assert short["matches"] == [
        {
            "path": "short.txt",
            "line": 1,
            "column": 1,
            "snippet_start_column": 1,
            "text": "KEY-60e254 short line",
            "text_truncated": False,
        }
    ]
    accesses = tools._debugger_accesses(state)  # type: ignore[arg-type]
    assert {
        "artifact": "evidence:short.txt",
        "mode": "lines",
        "start": 1,
        "end": 1,
    } in accesses


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
        json.loads(
            tools.read_file(
                root="evidence",
                relative_path=relative_path,
                start_line=1,
                end_line=1,
                agent_state=state,  # type: ignore[arg-type]
            )
        )

    result = tools.submit_debugger_diagnosis(
        primary_failure_class="prompt",
        earliest_failure="The failing trial acts before verification.",
        evidence=[
            {
                "citation": {
                    "artifact": "evidence:task.json",
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
    assert "not read" in rejected["error"]

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
    empty = json.loads(
        tools.read_file(
            root="evidence",
            relative_path="task.json",
            start_byte=size + 10,
            end_byte=size + 20,
            agent_state=empty_state,  # type: ignore[arg-type]
        )
    )
    assert empty["bytes_returned"] == 0
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
            "limitations": ["The requested range returned no bytes."],
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
    assert "not read" in empty_rejected["error"]


def test_debugger_line_citations_require_complete_returned_lines(
    tmp_path: Path,
) -> None:
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    first_line = b'{"event":"setup"}\n'
    second_line = b'{"event":"UNREAD_FAILURE"}\n'
    trace = evidence / "trace.jsonl"
    trace.write_bytes(first_line + second_line)

    def make_state(name: str) -> _AgentState:
        return _AgentState(
            {
                "meta_role": "debugger",
                "meta_read_roots": {"evidence": str(evidence)},
                "meta_write_roots": {},
                "meta_output_dir": str(tmp_path / name),
                "debugger_task": {"task_id": "task-1", "task_key": "opaque-1"},
                "debugger_task_key": "opaque-1",
                "debugger_primary_artifact": "evidence:trace.jsonl",
                "debugger_trial_classes": ["other"],
                "debugger_artifact_classes": {},
            }
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

    exact_state = make_state("exact-boundary")
    exact = json.loads(
        tools.read_file(
            root="evidence",
            relative_path="trace.jsonl",
            start_line=1,
            end_line=2,
            max_bytes=len(first_line),
            agent_state=exact_state,  # type: ignore[arg-type]
        )
    )
    assert exact["content"] == first_line.decode()
    assert exact["end_line"] == 1
    assert exact["partial_line"] is None
    exact_rejected = submit(
        exact_state,
        {
            "artifact": "evidence:trace.jsonl",
            "line_start": 2,
            "line_end": 2,
        },
    )
    assert exact_rejected["status"] == "error"
    assert "not covered" in str(exact_rejected["error"])

    partial_state = make_state("partial-line")
    partial_byte_count = 5
    partial = json.loads(
        tools.read_file(
            root="evidence",
            relative_path="trace.jsonl",
            start_line=1,
            end_line=2,
            max_bytes=len(first_line) + partial_byte_count,
            agent_state=partial_state,  # type: ignore[arg-type]
        )
    )
    assert partial["end_line"] == 1
    assert partial["partial_line"] == 2
    assert partial["partial_start_byte"] == len(first_line)
    assert partial["partial_end_byte"] == len(first_line) + partial_byte_count
    partial_line_rejected = submit(
        partial_state,
        {
            "artifact": "evidence:trace.jsonl",
            "line_start": 2,
            "line_end": 2,
        },
    )
    assert partial_line_rejected["status"] == "error"
    assert "not covered" in str(partial_line_rejected["error"])

    partial_bytes_accepted = submit(
        partial_state,
        {
            "artifact": "evidence:trace.jsonl",
            "start_byte": len(first_line),
            "end_byte": len(first_line) + partial_byte_count,
        },
    )
    assert partial_bytes_accepted["ok"] is True

    complete = json.loads(
        tools.read_file(
            root="evidence",
            relative_path="trace.jsonl",
            start_line=2,
            end_line=2,
            max_bytes=len(second_line),
            agent_state=partial_state,  # type: ignore[arg-type]
        )
    )
    assert complete["content"] == second_line.decode()
    assert complete["end_line"] == 2
    complete_line_accepted = submit(
        partial_state,
        {
            "artifact": "evidence:trace.jsonl",
            "line_start": 2,
            "line_end": 2,
        },
    )
    assert complete_line_accepted["ok"] is True


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

    def fake_run_agent(**kwargs):  # type: ignore[no-untyped-def]
        assert kwargs["tools"] == (
            "list_files",
            "read_file",
            "search_text",
            "submit_debugger_diagnosis",
        )
        assert kwargs["stop_tools"] == ("submit_debugger_diagnosis",)
        assert kwargs["skills"] == ()
        assert kwargs["context"]["meta_write_roots"] == {}
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
    assert result["diagnosis"]["paired_comparison"]["status"] == "unavailable"
    assert result["raw_response"] == "submitted"
