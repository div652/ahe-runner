"""LAB-exact starting harness, workspace seeding, and shared ``docs_dir`` tasks.

``harness_lab/`` must give the evaluated model exactly what LAB's own
``run.py`` gives it: the same system message, the same tool schemas, and the
same skill scripts under ``/workspace/skills``.
"""

from __future__ import annotations

import ast
import filecmp
import json
import os
import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar

import pytest
import yaml
from harvey_ahe import adapter as adapter_module
from harvey_ahe import tasks as tasks_module
from harvey_ahe.adapter import HarveyAdapter, _Environment, seed_workspace
from harvey_ahe.candidate_checks import (
    harness_import_errors,
    harvey_candidate_errors,
    training_slug,
)
from harvey_ahe.grading import JUDGE_MAX_TOKENS
from harvey_ahe.tasks import load_task, verify_shared_documents
from harvey_ahe.workspace import WorkspaceHandle
from lab_core.sandbox.sandbox import PodmanError

import ahe_runner.runtime as runtime_module
from ahe_runner.adapter import BenchmarkInterface, SplitName
from ahe_runner.config import ModelSettings, TransportSettings
from ahe_runner.errors import (
    CandidateValidationError,
    ConfigurationError,
    InfrastructureError,
    IntegrityError,
)
from ahe_runner.harness import load_harness_manifest, validate_harness
from ahe_runner.runtime import EvaluatedAgentRuntime

from .harvey_fakes import LAB_ROOT, PROJECT_ROOT, FakeSandbox, started_sandbox
from .test_harvey_seed_runtime import MODEL, _call, offline_endpoint  # noqa: F401
from .test_runtime import FakeRecordedClient

LAB_HARNESS = LAB_ROOT / "lab_core" / "harness"
HARNESS_LAB = PROJECT_ROOT / "harness_lab"
LAB_MANIFEST = PROJECT_ROOT / "harness_manifest_lab.yaml"
FIRM_TASK = "firm-knowledge/tasks/001"


def _lab_constants(path: Path, names: set[str]) -> dict[str, Any]:
    # run.py parses CLI arguments at import time, so read its literals instead.
    values: dict[str, Any] = {}
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id in names
        ):
            values[node.targets[0].id] = ast.literal_eval(node.value)
    assert set(values) == names
    return values


def _lab_system_prompt() -> str:
    """``build_system_preamble(True) + load_skills(DEFAULT_SKILLS)``."""
    run = _lab_constants(
        LAB_HARNESS / "run.py", {"FINISH_PROMPT_ANCHOR", "FINISH_PROMPT_BLOCK"}
    )
    preamble = (LAB_HARNESS / "system_prompt.md").read_text(encoding="utf-8")
    anchor = run["FINISH_PROMPT_ANCHOR"]
    assert anchor in preamble
    prompt = preamble.replace(anchor, anchor + run["FINISH_PROMPT_BLOCK"], 1)
    names = sorted(p.parent.name for p in (LAB_HARNESS / "skills").glob("*/SKILL.md"))
    assert names == ["docx", "pptx", "xlsx"]
    return prompt + "\n".join(
        f"\n\n## Skill: {name}\n\n"
        + (LAB_HARNESS / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
        for name in names
    )


def _lab_tools() -> list[dict[str, Any]]:
    tools = _lab_constants(
        LAB_HARNESS / "tools.py", {"TOOL_DEFINITIONS", "FINISH_TOOL_DEFINITION"}
    )
    return [*tools["TOOL_DEFINITIONS"], tools["FINISH_TOOL_DEFINITION"]]


# harness_lab matches LAB's run.py


def test_harness_lab_prompt_and_memory_match_lab() -> None:
    text = (HARNESS_LAB / "systemprompt.md").read_text(encoding="utf-8")
    assert text == _lab_system_prompt()
    assert (HARNESS_LAB / "LongTermMEMORY.md").read_text(encoding="utf-8") == ""


def test_harness_lab_matches_lab_loop_limits() -> None:
    config = yaml.safe_load((HARNESS_LAB / "agent.yaml").read_text(encoding="utf-8"))
    assert config["max_iterations"] == 200
    assert config["llm_config"]["max_tokens"] == 64000
    assert config["middlewares"] == []
    # LAB lets the provider bound the context. Copilot silently cuts a prompt
    # over 921793 tokens (AHE-021), so stop 10,000 below it, counted exactly.
    assert config["max_context_tokens"] == 911793
    assert config["token_counter"] == "harvey_ahe.token_count:count_prompt_tokens"


def test_exact_token_counter_uses_o200k_with_nexau_overheads() -> None:
    import tiktoken
    from harvey_ahe.token_count import count_prompt_tokens
    from nexau.archs.main_sub.utils.token_counter import TokenCounter
    from nexau.core.messages import Message

    encoding = tiktoken.get_encoding("o200k_base")
    text = "Rider 4.2 <|endoftext|> applies; see Schedule B, row 17."
    messages = [Message.user(text)]
    tools = [{"name": "read", "description": "Read a file."}]
    nexau = TokenCounter(strategy="tiktoken", model="gpt-5")
    expected = nexau._count_tokens_with_text_encoder(
        messages, tools, lambda t: len(encoding.encode_ordinary(t))
    )
    assert count_prompt_tokens(messages, tools=tools) == expected
    # Dense tables cost far more tokens than NexAU's characters / 4 estimate.
    table = "\n".join(
        "|"
        + "|".join(f"{(r * 7919 + c * 104729) % 99991:05d}" for c in range(12))
        + "|"
        for r in range(300)
    )
    exact = count_prompt_tokens([Message.user(table)])
    estimate = TokenCounter(strategy="fallback").count_tokens([Message.user(table)])
    assert exact > estimate * 1.2


def test_harness_lab_tool_schemas_match_lab() -> None:
    for lab in _lab_tools():
        path = HARNESS_LAB / "tool_descriptions" / f"{lab['name']}.tool.yaml"
        ours = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert ours["description"] == lab["description"]
        assert ours["input_schema"] == lab["parameters"]


def _validation_errors(harness: Path, manifest: Path) -> list[str]:
    try:
        validate_harness(
            harness,
            load_harness_manifest(manifest),
            project_root=PROJECT_ROOT,
            run_nexau_smoke=False,
        )
    except CandidateValidationError as exc:
        return list(exc.report["errors"])
    return []


def test_harness_lab_validates_only_with_the_lab_manifest() -> None:
    assert _validation_errors(HARNESS_LAB, LAB_MANIFEST) == []
    strict = _validation_errors(HARNESS_LAB, PROJECT_ROOT / "harness_manifest.yaml")
    assert strict and all("additionalProperties must be false" in e for e in strict)
    assert len(strict) == len(_lab_tools())


def test_lab_manifest_differs_only_in_the_schema_policy() -> None:
    strict = load_harness_manifest(PROJECT_ROOT / "harness_manifest.yaml")
    lab = load_harness_manifest(LAB_MANIFEST)
    assert not strict.tool_schemas.allow_omitted_additional_properties
    assert lab.tool_schemas.allow_omitted_additional_properties
    assert lab.model_dump(exclude={"tool_schemas"}) == strict.model_dump(
        exclude={"tool_schemas"}
    )


@pytest.mark.parametrize("value", [True, {"type": "string"}])
def test_lab_manifest_still_rejects_explicit_open_schemas(
    tmp_path: Path, value: object
) -> None:
    harness = tmp_path / "harness"
    shutil.copytree(HARNESS_LAB, harness, ignore=shutil.ignore_patterns("__pycache__"))
    path = harness / "tool_descriptions" / "read.tool.yaml"
    tool = yaml.safe_load(path.read_text(encoding="utf-8"))
    tool["input_schema"]["additionalProperties"] = value
    path.write_text(yaml.safe_dump(tool, sort_keys=False), encoding="utf-8")
    errors = _validation_errors(harness, LAB_MANIFEST)
    assert errors == ["agent.tools[1].input_schema.additionalProperties must be false"]


def test_harness_lab_seed_scripts_match_lab() -> None:
    seed = HARNESS_LAB / "workspace_seed"
    assert sorted(p.name for p in seed.iterdir()) == ["skills"]
    for name in ("docx", "pptx", "xlsx"):
        comparison = filecmp.dircmp(
            LAB_HARNESS / "skills" / name / "scripts",
            seed / "skills" / name / "scripts",
            ignore=["__pycache__"],
        )
        assert not (
            comparison.left_only or comparison.right_only or comparison.diff_files
        )
        assert not comparison.subdirs


def test_harness_lab_sends_lab_system_message_and_tools(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    offline_endpoint: Any,  # noqa: F811
) -> None:
    """The assembled request, not just the files (memory suffix included)."""
    trial = tmp_path / "trial"
    sandbox = started_sandbox(trial)
    handle = WorkspaceHandle(sandbox, shell_timeout=60)
    FakeRecordedClient.instances = []
    FakeRecordedClient.scenarios = [
        [
            _call("write", {"file_path": "response.md", "content": "x"}, "w"),
            _call("finish", {"summary": "done", "deliverables": ["response.md"]}, "f"),
        ]
    ]
    monkeypatch.setattr(runtime_module, "RecordedExactModelClient", FakeRecordedClient)
    artifact_root = tmp_path / "runtime"
    runtime = EvaluatedAgentRuntime(
        harness_dir=HARNESS_LAB,
        manifest=load_harness_manifest(LAB_MANIFEST),
        benchmark_interface=BenchmarkInterface(
            description="Harvey offline fixture.",
            input_kinds=("prompt", "data", "environment"),
            tool_delivery="none",
        ),
        model=ModelSettings(model=MODEL, max_tokens=4096, temperature=0.0),
        transport=TransportSettings(
            base_url="http://127.0.0.1:9/v1",
            model_catalog="required",
            negative_control="required",
        ),
        api_key="offline-key",
        artifact_root=artifact_root,
    )
    working_directory = trial / "nexau_cwd"
    working_directory.mkdir()
    try:
        output, error, overflow = adapter_module._run_session(
            runtime=runtime,  # type: ignore[arg-type]
            session_id="unit-1-1",
            artifact_dir=artifact_root / "unit-1" / "attempt-1",
            working_directory=working_directory,
            handle=handle,
            instructions="Answer in response.md.",
        )
    finally:
        runtime.close()
    assert error is None and overflow is None
    assert output is not None and handle.finished

    first = FakeRecordedClient.instances[0].calls[0]
    system, user = first["messages"][:2]
    assert system["role"] == "system"
    content = system["content"]
    if not isinstance(content, str):
        content = "".join(part["text"] for part in content)
    # NexAU strips the rendered file prompt; nothing else may be added.
    assert content == _lab_system_prompt().strip()
    assert "Persistent harness memory" not in json.dumps(first["messages"])
    assert user["role"] == "user"
    sent = {tool["function"]["name"]: tool["function"] for tool in first["tools"]}
    for lab in _lab_tools():
        assert sent[lab["name"]]["description"] == lab["description"]
        assert sent[lab["name"]]["parameters"] == lab["parameters"]
    assert set(sent) == {tool["name"] for tool in _lab_tools()}


# Long-term memory attachment (AHE core)


def _memory_manifest() -> Any:
    return load_harness_manifest(PROJECT_ROOT / "harness_manifest.yaml")


@pytest.mark.parametrize("text", ["", "  \n\n"])
def test_empty_long_term_memory_adds_nothing(tmp_path: Path, text: str) -> None:
    (tmp_path / "LongTermMEMORY.md").write_text(text, encoding="utf-8")
    config = SimpleNamespace(system_prompt_suffix=None)
    runtime_module._attach_long_term_memory(
        config,  # type: ignore[arg-type]
        harness_dir=tmp_path,
        manifest=_memory_manifest(),
    )
    assert config.system_prompt_suffix is None


def test_long_term_memory_is_separated_from_the_prompt(tmp_path: Path) -> None:
    (tmp_path / "LongTermMEMORY.md").write_text("Be brief.\n", encoding="utf-8")
    config = SimpleNamespace(system_prompt_suffix=None)
    runtime_module._attach_long_term_memory(
        config,  # type: ignore[arg-type]
        harness_dir=tmp_path,
        manifest=_memory_manifest(),
    )
    assert config.system_prompt_suffix == (
        "\n\n# Persistent harness memory\n\n"
        "## Persistent harness memory: LongTermMEMORY.md\n\nBe brief."
    )


# Candidate checks: workspace_seed and numeric task slugs


def test_harness_lab_passes_candidate_checks_with_numbered_tasks() -> None:
    task_ids = [f"firm-knowledge/tasks/{n:03d}" for n in range(1, 251)]
    assert harvey_candidate_errors(HARNESS_LAB, training_task_ids=task_ids) == []


def test_training_slug_widens_numeric_leaves() -> None:
    assert training_slug("firm-knowledge/tasks/200") == "tasks/200"
    assert training_slug("tax/some-memo") == "some-memo"


def _lab_copy(tmp_path: Path) -> Path:
    harness = tmp_path / "harness"
    shutil.copytree(HARNESS_LAB, harness, ignore=shutil.ignore_patterns("__pycache__"))
    return harness


def test_seed_python_is_not_scanned_as_host_code(tmp_path: Path) -> None:
    harness = _lab_copy(tmp_path)
    assert harness_import_errors(harness) == []
    seeded = harness / "workspace_seed" / "skills" / "docx" / "scripts" / "soffice.py"
    assert "subprocess" in seeded.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("relative", "text", "message"),
    [
        ("tools/x.py", "from workspace_seed.skills import docx\n", "sandbox-only"),
        ("tools/x.py", "import runpy\n", "import of 'runpy'"),
        (
            "tool_descriptions/x.tool.yaml",
            "binding: workspace_seed.skills.docx.scripts.pack:main\n",
            "may not import or bind workspace_seed",
        ),
    ],
)
def test_host_code_cannot_load_seed_files(
    tmp_path: Path, relative: str, text: str, message: str
) -> None:
    harness = _lab_copy(tmp_path)
    (harness / relative).write_text(text, encoding="utf-8")
    errors = harvey_candidate_errors(harness, training_task_ids=[])
    assert any(message in error for error in errors), errors


# Workspace seeding


def test_seed_workspace_copies_regular_files_only(tmp_path: Path) -> None:
    harness = tmp_path / "harness"
    script = harness / "workspace_seed" / "skills" / "a" / "scripts" / "run.sh"
    script.parent.mkdir(parents=True)
    script.write_text("echo hi\n", encoding="utf-8")
    script.chmod(0o755)
    secret = tmp_path / "secret.txt"
    secret.write_text("host only", encoding="utf-8")
    (script.parent / "link.txt").symlink_to(secret)
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    assert seed_workspace(harness, workspace) == ["skills/a/scripts/run.sh"]
    copied = workspace / "skills" / "a" / "scripts" / "run.sh"
    assert copied.read_text(encoding="utf-8") == "echo hi\n"
    assert os.access(copied, os.X_OK)
    assert not (copied.parent / "link.txt").exists()
    assert seed_workspace(tmp_path / "no-seed", workspace) == []


# Shared docs_dir tasks


def _fake_lab(tmp_path: Path, *, docs_dir: str | None) -> Path:
    lab = tmp_path / "lab"
    task_dir = lab / "tasks" / "fk" / "tasks" / "001"
    task_dir.mkdir(parents=True)
    config = json.loads(
        (LAB_ROOT / "tasks" / FIRM_TASK / "task.json").read_text(encoding="utf-8")
    )
    config.pop("docs_dir", None)
    if docs_dir is None:
        (task_dir / "documents").mkdir()
        (task_dir / "documents" / "brief.txt").write_text("own", encoding="utf-8")
    else:
        config["docs_dir"] = docs_dir
    (task_dir / "task.json").write_text(json.dumps(config), encoding="utf-8")
    corpus = lab / "tasks" / "fk" / "dms" / "matters" / "m1"
    corpus.mkdir(parents=True)
    (corpus / "memo.txt").write_text("shared", encoding="utf-8")
    return lab


def test_shared_docs_dir_resolves_and_hashes_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lab = _fake_lab(tmp_path, docs_dir="../../dms")
    shutil.copytree(lab / "tasks/fk/tasks/001", lab / "tasks/fk/tasks/002")
    calls: list[Path] = []
    real = tasks_module.tree_sha256

    def counting(directory: Path) -> str:
        calls.append(directory)
        return real(directory)

    monkeypatch.setattr(tasks_module, "tree_sha256", counting)
    first = load_task(lab, "fk/tasks/001")
    second = load_task(lab, "fk/tasks/002")
    assert first.shared_documents and second.shared_documents
    assert first.documents_dir == (lab / "tasks/fk/dms").resolve()
    assert first.documents_sha256 == second.documents_sha256
    assert calls == [first.documents_dir]


def test_docs_dir_may_not_escape_lab_tasks(tmp_path: Path) -> None:
    lab = _fake_lab(tmp_path, docs_dir="../../../../..")
    with pytest.raises(ConfigurationError, match="escapes"):
        load_task(lab, "fk/tasks/001")


def test_shared_documents_tamper_check(tmp_path: Path) -> None:
    lab = _fake_lab(tmp_path, docs_dir="../../dms")
    task = load_task(lab, "fk/tasks/001")
    verify_shared_documents(task)
    (task.documents_dir / "matters" / "m1" / "added.txt").write_text("x", "utf-8")
    with pytest.raises(IntegrityError, match="shared documents changed"):
        verify_shared_documents(task)


class _NoStartSandbox(FakeSandbox):
    created: ClassVar[list[_NoStartSandbox]] = []

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.created.append(self)

    def start(self) -> None:
        raise PodmanError("offline")


@pytest.mark.parametrize("shared", [True, False])
def test_sandbox_mounts_shared_corpus_in_place_and_seeds_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shared: bool
) -> None:
    lab = _fake_lab(tmp_path, docs_dir="../../dms" if shared else None)
    task = load_task(lab, "fk/tasks/001")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    _NoStartSandbox.created = []
    monkeypatch.setattr(adapter_module, "Sandbox", _NoStartSandbox)
    owner = SimpleNamespace(config=SimpleNamespace(shell_timeout=60))
    with pytest.raises(InfrastructureError, match="failed to start"):
        HarveyAdapter._run_in_sandbox(
            owner,  # type: ignore[arg-type]
            scratch=scratch,
            task=task,
            unit=None,  # type: ignore[arg-type]
            attempt=1,
            runtime=None,  # type: ignore[arg-type]
            runtime_dir=tmp_path / "runtime",
            environment=_Environment(image_id="img", pandoc_version="p"),
            harness_dir=HARNESS_LAB,
        )
    (sandbox,) = _NoStartSandbox.created
    if shared:
        assert sandbox.documents_dir == task.documents_dir
        assert not (scratch / "documents").exists()
    else:
        assert sandbox.documents_dir == scratch / "documents"
        assert (scratch / "documents" / "brief.txt").read_text("utf-8") == "own"
    assert (scratch / "workspace" / "skills" / "docx" / "scripts" / "pack.py").is_file()
    assert sorted(p.name for p in (scratch / "workspace").iterdir()) == ["skills"]


def _firm_split(tmp_path: Path) -> Path:
    rows = {}
    for split, number in (("train", 1), ("validation", 2), ("test", 3)):
        task = load_task(LAB_ROOT, f"firm-knowledge/tasks/{number:03d}")
        rows[split] = [
            {
                "task_id": task.task_id,
                "task_json_sha256": task.task_json_sha256,
                "documents_sha256": task.documents_sha256,
            }
        ]
    path = tmp_path / "splits.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "identity": "offline-firm-knowledge",
                "lab_commit": "offline",
                "selection_policy": "offline fixture",
                "splits": rows,
            }
        ),
        encoding="utf-8",
    )
    return path


def _adapter_settings(tmp_path: Path, **overrides: Any) -> dict[str, Any]:
    judge = {"max_tokens": JUDGE_MAX_TOKENS, "temperature": 0.0}
    return {
        "lab_root": str(LAB_ROOT),
        "split_file": str(_firm_split(tmp_path)),
        "judges": [{"model": "judge-a", **judge}, {"model": "judge-b", **judge}],
        "helper": {"model": "helper", "max_tokens": 1024, "temperature": 0.0},
        "scratch_root": str(tmp_path / "scratch"),
        **overrides,
    }


def test_task_filter_rejects_firm_knowledge_unless_disabled(tmp_path: Path) -> None:
    assert (
        adapter_module.HarveyAdapterSettings.model_validate(
            _adapter_settings(tmp_path)
        ).apply_task_filter
        is True
    )
    with pytest.raises(ConfigurationError, match="20-40 criteria"):
        HarveyAdapter(project_root=PROJECT_ROOT, settings=_adapter_settings(tmp_path))
    adapter = HarveyAdapter(
        project_root=PROJECT_ROOT,
        settings=_adapter_settings(tmp_path, apply_task_filter=False),
    )
    (task_id,) = adapter.split(SplitName.TRAIN).task_ids
    task = adapter._tasks[task_id]
    assert task.shared_documents
    assert task.documents_dir == (LAB_ROOT / "tasks/firm-knowledge/dms").resolve()
