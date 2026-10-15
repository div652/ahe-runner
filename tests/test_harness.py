from __future__ import annotations

import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from ahe_runner.errors import CandidateValidationError, ConfigurationError
from ahe_runner.harness import load_harness_manifest, validate_harness
from ahe_runner.harness_imports import HarnessImportScope

from .conftest import make_example_project


def test_harness_validation_rejects_symlinks_and_task_memorization(
    tmp_path: Path,
) -> None:
    config_path = make_example_project(tmp_path)
    project = config_path.parent
    manifest = load_harness_manifest(project / "harness_manifest.yaml")
    harness = project / "harness"
    original_dont_write_bytecode = sys.dont_write_bytecode

    report = validate_harness(
        harness,
        manifest,
        project_root=project,
    )
    assert report["valid"] is True
    assert report["conformance"]["abi_version"] == "ahe.harness/v2"
    assert report["conformance"]["context_accounting"] == {
        "max_context_tokens": 200000,
        "token_counter": None,
        "counting_unit": (
            "NexAU fallback estimate (serialized characters divided by four)"
        ),
        "provider_max_input_tokens": None,
    }
    assert report["conformance"]["registrations"]["tool_implementations"] == [
        "tools/record_note.py"
    ]
    assert report["conformance"]["registrations"]["skills"] == [
        "skills/evidence-first/SKILL.md"
    ]
    assert report["conformance"]["registrations"]["sub_agents"] == [
        "sub_agents/reviewer/agent.yaml"
    ]
    assert not list(harness.rglob("__pycache__"))
    assert sys.dont_write_bytecode is original_dont_write_bytecode

    symlink_harness = tmp_path / "symlink-harness"
    shutil.copytree(harness, symlink_harness)
    (symlink_harness / "linked.md").symlink_to(symlink_harness / "systemprompt.md")
    with pytest.raises(CandidateValidationError) as symlink_error:
        validate_harness(
            symlink_harness,
            manifest,
            project_root=project,
        )
    assert "symlink is prohibited" in str(symlink_error.value)

    memorized = tmp_path / "memorized-harness"
    shutil.copytree(harness, memorized)
    with (memorized / "systemprompt.md").open("a", encoding="utf-8") as handle:
        handle.write("\nSpecial-case train-secret-17.\n")
    with pytest.raises(CandidateValidationError) as memorization_error:
        validate_harness(
            memorized,
            manifest,
            project_root=project,
            training_task_ids=("train-secret-17",),
        )
    assert "complete training task ID" in str(memorization_error.value)

    short_id_harness = tmp_path / "short-id-harness"
    shutil.copytree(harness, short_id_harness)
    short_id_report = validate_harness(
        short_id_harness,
        manifest,
        project_root=project,
        training_task_ids=("1",),
        run_nexau_smoke=False,
    )
    assert short_id_report["valid"] is True


def test_harness_validation_preserves_protected_and_runtime_owned_fields(
    tmp_path: Path,
) -> None:
    config_path = make_example_project(tmp_path)
    project = config_path.parent
    manifest = load_harness_manifest(project / "harness_manifest.yaml")
    harness = project / "harness"
    parent = validate_harness(
        harness,
        manifest,
        project_root=project,
        run_nexau_smoke=False,
    )

    candidate = tmp_path / "candidate"
    shutil.copytree(harness, candidate)
    agent = yaml.safe_load((candidate / "agent.yaml").read_text(encoding="utf-8"))
    agent["llm_config"]["model"] = "unauthorized-model"
    (candidate / "agent.yaml").write_text(
        yaml.safe_dump(agent, sort_keys=False),
        encoding="utf-8",
    )
    with pytest.raises(CandidateValidationError) as protected_error:
        validate_harness(
            candidate,
            manifest,
            project_root=project,
            parent_runtime_owned_hashes=parent["runtime_owned_hashes"],
            run_nexau_smoke=False,
        )
    assert "protected agent field" in str(protected_error.value)

    runtime_candidate = tmp_path / "runtime-candidate"
    shutil.copytree(harness, runtime_candidate)
    (runtime_candidate / "ShortTermMEMORY.md").write_text(
        "mutated runtime state\n",
        encoding="utf-8",
    )
    with pytest.raises(CandidateValidationError) as runtime_error:
        validate_harness(
            runtime_candidate,
            manifest,
            project_root=project,
            parent_runtime_owned_hashes=parent["runtime_owned_hashes"],
            run_nexau_smoke=False,
        )
    assert "runtime-owned harness files changed" in str(runtime_error.value)


def test_context_accounting_is_protected_across_the_agent_tree(
    tmp_path: Path,
) -> None:
    config_path = make_example_project(tmp_path)
    project = config_path.parent
    manifest_path = project / "harness_manifest.yaml"
    manifest = load_harness_manifest(manifest_path)
    harness = project / "harness"

    root_changed = tmp_path / "root-context"
    shutil.copytree(harness, root_changed)
    root_config_path = root_changed / "agent.yaml"
    root_config = yaml.safe_load(root_config_path.read_text(encoding="utf-8"))
    root_config["max_context_tokens"] = 199_999
    root_config_path.write_text(
        yaml.safe_dump(root_config, sort_keys=False),
        encoding="utf-8",
    )
    with pytest.raises(CandidateValidationError, match="max_context_tokens"):
        validate_harness(
            root_changed,
            manifest,
            project_root=project,
            run_nexau_smoke=False,
        )

    sub_agent_changed = tmp_path / "sub-agent-context"
    shutil.copytree(harness, sub_agent_changed)
    reviewer_path = sub_agent_changed / "sub_agents" / "reviewer" / "agent.yaml"
    reviewer = yaml.safe_load(reviewer_path.read_text(encoding="utf-8"))
    reviewer["max_context_tokens"] = 199_999
    reviewer_path.write_text(
        yaml.safe_dump(reviewer, sort_keys=False),
        encoding="utf-8",
    )
    with pytest.raises(CandidateValidationError, match="max_context_tokens"):
        validate_harness(
            sub_agent_changed,
            manifest,
            project_root=project,
            run_nexau_smoke=False,
        )

    manifest_value = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    manifest_value["context_accounting"] = {
        "max_context_tokens": 200_000,
        "token_counter": "benchmark_tokens:count_prompt_tokens",
        "counting_unit": "provider-model tokens",
        "provider_max_input_tokens": 220_000,
    }
    custom_manifest_path = project / "harness_manifest.context.yaml"
    custom_manifest_path.write_text(
        yaml.safe_dump(manifest_value, sort_keys=False),
        encoding="utf-8",
    )
    custom_manifest = load_harness_manifest(custom_manifest_path)
    with pytest.raises(CandidateValidationError, match="token_counter"):
        validate_harness(
            harness,
            custom_manifest,
            project_root=project,
            run_nexau_smoke=False,
        )

    local_counter = tmp_path / "local-counter"
    shutil.copytree(harness, local_counter)
    (local_counter / "token_count.py").write_text(
        "def count_prompt_tokens(messages, tools=None):\n    return 1\n",
        encoding="utf-8",
    )
    for relative in ("agent.yaml", "sub_agents/reviewer/agent.yaml"):
        path = local_counter / relative
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
        value["token_counter"] = "token_count:count_prompt_tokens"
        path.write_text(
            yaml.safe_dump(value, sort_keys=False),
            encoding="utf-8",
        )
    local_manifest_value = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    local_manifest_value["context_accounting"] = {
        "max_context_tokens": 200_000,
        "token_counter": "token_count:count_prompt_tokens",
        "counting_unit": "candidate-defined tokens",
        "provider_max_input_tokens": 220_000,
    }
    local_manifest_path = project / "harness_manifest.local-context.yaml"
    local_manifest_path.write_text(
        yaml.safe_dump(local_manifest_value, sort_keys=False),
        encoding="utf-8",
    )
    with pytest.raises(CandidateValidationError, match="outside the evolvable"):
        validate_harness(
            local_counter,
            load_harness_manifest(local_manifest_path),
            project_root=project,
            run_nexau_smoke=False,
        )

    manifest_value["context_accounting"]["provider_max_input_tokens"] = 199_999
    invalid_manifest_path = project / "harness_manifest.invalid-context.yaml"
    invalid_manifest_path.write_text(
        yaml.safe_dump(manifest_value, sort_keys=False),
        encoding="utf-8",
    )
    with pytest.raises(ConfigurationError, match="provider_max_input_tokens"):
        load_harness_manifest(invalid_manifest_path)


def test_v2_conformance_rejects_inactive_and_mismatched_tools(
    tmp_path: Path,
) -> None:
    config_path = make_example_project(tmp_path)
    project = config_path.parent
    manifest = load_harness_manifest(project / "harness_manifest.yaml")
    harness = project / "harness"

    inactive = tmp_path / "inactive"
    shutil.copytree(harness, inactive)
    (inactive / "tools" / "unused.py").write_text(
        "def unused(value: str) -> str:\n    return value\n",
        encoding="utf-8",
    )
    with pytest.raises(CandidateValidationError) as inactive_error:
        validate_harness(
            inactive,
            manifest,
            project_root=project,
            run_nexau_smoke=False,
        )
    assert "not runtime-attached: tools/unused.py" in str(inactive_error.value)

    mismatch = tmp_path / "mismatch"
    shutil.copytree(harness, mismatch)
    tool_path = mismatch / "tool_descriptions" / "record_note.tool.yaml"
    tool = yaml.safe_load(tool_path.read_text(encoding="utf-8"))
    tool["input_schema"]["properties"] = {
        "text": {"type": "string"},
    }
    tool["input_schema"]["required"] = ["text"]
    tool_path.write_text(
        yaml.safe_dump(tool, sort_keys=False),
        encoding="utf-8",
    )
    with pytest.raises(CandidateValidationError) as mismatch_error:
        validate_harness(
            mismatch,
            manifest,
            project_root=project,
            run_nexau_smoke=False,
        )
    assert "schema properties ['text']" in str(mismatch_error.value)


def test_v2_conformance_allows_omitted_additional_properties_only_by_policy(
    tmp_path: Path,
) -> None:
    config_path = make_example_project(tmp_path)
    project = config_path.parent
    manifest_path = project / "harness_manifest.yaml"
    harness = project / "harness"
    tool_path = harness / "tool_descriptions" / "record_note.tool.yaml"
    tool = yaml.safe_load(tool_path.read_text(encoding="utf-8"))
    tool["input_schema"].pop("additionalProperties")
    tool_path.write_text(yaml.safe_dump(tool, sort_keys=False), encoding="utf-8")

    with pytest.raises(CandidateValidationError, match="additionalProperties"):
        validate_harness(
            harness,
            load_harness_manifest(manifest_path),
            project_root=project,
            run_nexau_smoke=False,
        )

    manifest_value = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    manifest_value["tool_schemas"] = {
        "allow_omitted_additional_properties": True,
    }
    parity_manifest_path = project / "harness_manifest.parity.yaml"
    parity_manifest_path.write_text(
        yaml.safe_dump(manifest_value, sort_keys=False),
        encoding="utf-8",
    )
    parity_manifest = load_harness_manifest(parity_manifest_path)
    assert parity_manifest.tool_schemas.allow_omitted_additional_properties
    report = validate_harness(
        harness,
        parity_manifest,
        project_root=project,
        run_nexau_smoke=False,
    )
    assert report["valid"] is True

    tool["input_schema"]["additionalProperties"] = True
    tool_path.write_text(yaml.safe_dump(tool, sort_keys=False), encoding="utf-8")
    with pytest.raises(CandidateValidationError, match="additionalProperties"):
        validate_harness(
            harness,
            parity_manifest,
            project_root=project,
            run_nexau_smoke=False,
        )


def test_v2_conformance_accepts_transitive_tool_support_modules(
    tmp_path: Path,
) -> None:
    config_path = make_example_project(tmp_path)
    project = config_path.parent
    manifest = load_harness_manifest(project / "harness_manifest.yaml")
    harness = project / "harness"
    (harness / "tools" / "helper.py").write_text(
        "def normalize_note(value: str) -> str:\n    return value.strip()\n",
        encoding="utf-8",
    )
    tool_path = harness / "tools" / "record_note.py"
    tool_path.write_text(
        tool_path.read_text(encoding="utf-8")
        .replace(
            "from typing import Any\n",
            "from typing import Any\n\nfrom tools.helper import normalize_note\n",
        )
        .replace(
            "    notes = list(",
            "    note = normalize_note(note)\n    notes = list(",
        ),
        encoding="utf-8",
    )

    report = validate_harness(
        harness,
        manifest,
        project_root=project,
        run_nexau_smoke=False,
    )
    assert report["conformance"]["registrations"]["tool_implementations"] == [
        "tools/helper.py",
        "tools/record_note.py",
    ]

    tool_path.write_text(
        tool_path.read_text(encoding="utf-8")
        .replace("from tools.helper import normalize_note\n", "")
        .replace(
            "    note = normalize_note(note)\n",
            "    from tools.helper import normalize_note\n\n"
            "    note = normalize_note(note)\n",
        ),
        encoding="utf-8",
    )
    lazy_report = validate_harness(
        harness,
        manifest,
        project_root=project,
        run_nexau_smoke=False,
    )
    assert lazy_report["conformance"]["registrations"]["tool_implementations"] == [
        "tools/helper.py",
        "tools/record_note.py",
    ]

    values: dict[str, object] = {}
    state = SimpleNamespace(
        get_global_value=lambda key, default=None: values.get(key, default),
        set_global_value=lambda key, value: values.__setitem__(key, value),
    )
    with HarnessImportScope(harness):
        from tools.record_note import record_note

        assert record_note(" padded ", state) == {
            "recorded": "padded",
            "note_count": 1,
        }


def test_v2_conformance_tracks_skill_assets_and_package_imports(
    tmp_path: Path,
) -> None:
    config_path = make_example_project(tmp_path)
    project = config_path.parent
    manifest = load_harness_manifest(project / "harness_manifest.yaml")
    harness = project / "harness"
    skill_asset = harness / "skills" / "evidence-first" / "references" / "guide.md"
    skill_asset.parent.mkdir()
    skill_asset.write_text("Use the benchmark-visible evidence.\n", encoding="utf-8")
    (harness / "tools" / "helper.py").write_text(
        "def normalize_note(value: str) -> str:\n    return value.strip()\n",
        encoding="utf-8",
    )
    tool_path = harness / "tools" / "record_note.py"
    tool_path.write_text(
        tool_path.read_text(encoding="utf-8")
        .replace(
            "from typing import Any\n",
            "from typing import Any\n\nfrom tools import helper\n",
        )
        .replace(
            "    notes = list(",
            "    note = helper.normalize_note(note)\n    notes = list(",
        ),
        encoding="utf-8",
    )

    report = validate_harness(
        harness,
        manifest,
        project_root=project,
        run_nexau_smoke=False,
    )

    assert report["conformance"]["registrations"]["skills"] == [
        "skills/evidence-first/SKILL.md",
        "skills/evidence-first/references/guide.md",
    ]
    assert report["conformance"]["registrations"]["tool_implementations"] == [
        "tools/helper.py",
        "tools/record_note.py",
    ]


@pytest.mark.parametrize(
    ("filename", "message"),
    [
        ("json.py", "shadows the Python standard library"),
        ("ddgs.py", "shadows an importable host package"),
    ],
)
def test_harness_validation_rejects_top_level_module_shadowing(
    tmp_path: Path,
    filename: str,
    message: str,
) -> None:
    config_path = make_example_project(tmp_path)
    project = config_path.parent
    manifest = load_harness_manifest(project / "harness_manifest.yaml")
    harness = project / "harness"
    (harness / filename).write_text("value = None\n", encoding="utf-8")

    with pytest.raises(CandidateValidationError) as error:
        validate_harness(
            harness,
            manifest,
            project_root=project,
            run_nexau_smoke=False,
        )

    assert message in str(error.value)


@pytest.mark.parametrize("import_scope", ["eager", "lazy"])
@pytest.mark.parametrize("import_style", ["absolute", "relative"])
def test_v2_conformance_attaches_nested_package_initializers(
    tmp_path: Path,
    import_scope: str,
    import_style: str,
) -> None:
    config_path = make_example_project(tmp_path)
    project = config_path.parent
    manifest = load_harness_manifest(project / "harness_manifest.yaml")
    harness = project / "harness"
    tools = harness / "tools"
    helpers = tools / "helpers"
    helpers.mkdir()
    (tools / "__init__.py").write_text("", encoding="utf-8")
    (helpers / "__init__.py").write_text(
        "from .settings import STRIP_NOTES\n",
        encoding="utf-8",
    )
    (helpers / "settings.py").write_text(
        "STRIP_NOTES = True\n",
        encoding="utf-8",
    )
    (helpers / "normalize.py").write_text(
        "from . import STRIP_NOTES\n\n"
        "def normalize_note(value: str) -> str:\n"
        "    return value.strip() if STRIP_NOTES else value\n",
        encoding="utf-8",
    )
    import_line = (
        "from tools.helpers.normalize import normalize_note"
        if import_style == "absolute"
        else "from .helpers.normalize import normalize_note"
    )
    tool_path = tools / "record_note.py"
    source = tool_path.read_text(encoding="utf-8")
    if import_scope == "eager":
        source = source.replace(
            "from typing import Any\n",
            f"from typing import Any\n\n{import_line}\n",
        )
    else:
        source = source.replace(
            "    notes = list(",
            f"    {import_line}\n\n    notes = list(",
        )
    source = source.replace(
        "    notes = list(",
        "    note = normalize_note(note)\n    notes = list(",
    )
    tool_path.write_text(source, encoding="utf-8")

    report = validate_harness(
        harness,
        manifest,
        project_root=project,
        run_nexau_smoke=False,
    )
    assert report["conformance"]["registrations"]["tool_implementations"] == [
        "tools/__init__.py",
        "tools/helpers/__init__.py",
        "tools/helpers/normalize.py",
        "tools/helpers/settings.py",
        "tools/record_note.py",
    ]

    values: dict[str, object] = {}
    state = SimpleNamespace(
        get_global_value=lambda key, default=None: values.get(key, default),
        set_global_value=lambda key, value: values.__setitem__(key, value),
    )
    with HarnessImportScope(harness):
        from tools.record_note import record_note

        assert record_note(" padded ", state) == {
            "recorded": "padded",
            "note_count": 1,
        }
