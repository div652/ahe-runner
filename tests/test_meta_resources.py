from __future__ import annotations

import inspect
import json
from pathlib import Path
from types import SimpleNamespace

import yaml

import ahe_runner.meta.nexau_backend as backend_module
from ahe_runner.config import load_config
from ahe_runner.controller import CampaignController
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


def test_debugger_submission_requires_read_evidence_and_paired_analysis(
    tmp_path: Path,
) -> None:
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    detail = evidence / "task.json"
    detail.write_text('{"trials": ["pass", "fail"]}\n', encoding="utf-8")
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
        }
    )
    json.loads(
        tools.read_file(
            root="evidence",
            relative_path="task.json",
            agent_state=state,  # type: ignore[arg-type]
        )
    )

    result = tools.submit_debugger_diagnosis(
        primary_failure_class="prompt",
        earliest_failure="The failing trial acts before verification.",
        evidence=[
            {
                "location": "evidence:task.json",
                "observation": "The task detail exposes one pass and one failure.",
                "implication": "A paired comparison is possible.",
            }
        ],
        evidence_coverage={
            "artifacts_read": ["evidence:task.json"],
            "limitations": [],
        },
        paired_comparison={
            "status": "performed",
            "passing_evidence": ["pass trial verifies before acting"],
            "failing_evidence": ["fail trial acts immediately"],
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
    assert diagnosis["schema_version"] == 2
    assert diagnosis["paired_comparison"]["status"] == "performed"

    rejected = tools.submit_debugger_diagnosis(
        primary_failure_class="none",
        earliest_failure="No failure was observed.",
        evidence=[
            {
                "location": "evidence:missing.json",
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
    detail.write_text('{"trials": []}\n', encoding="utf-8")
    task = EvidenceTask(
        task_key="opaque",
        task_id=None,
        state="insufficient",
        detail_path="tasks/opaque.json",
        detail_sha256=sha256_file(detail),
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
                "schema_version": 2,
                "task_id": None,
                "primary_failure_class": "none",
                "earliest_failure": "No consequential failure is observable.",
                "evidence": [
                    {
                        "location": "evidence:tasks/opaque.json",
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
