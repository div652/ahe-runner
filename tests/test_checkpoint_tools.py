from __future__ import annotations

import json
from pathlib import Path

from ahe_runner.io import atomic_write_json, sha256_json, tree_hashes
from ahe_runner.meta import tools


class _Storage:
    def __init__(self) -> None:
        self.values: dict[str, object] = {}

    def get(self, key: str, default=None):  # type: ignore[no-untyped-def]
        return self.values.get(key, default)

    def set(self, key: str, value: object) -> None:
        self.values[key] = value


class _AgentState:
    def __init__(self, context: dict[str, object]) -> None:
        self.agent_id = "evolve-test"
        self.agent_name = "evolve-test"
        self.global_storage = _Storage()
        self.context = context

    def get_context_value(self, key: str):  # type: ignore[no-untyped-def]
        return self.context.get(key)


def _checkpoint(
    root: Path,
    label: str,
    *,
    parent: str | None,
    files: dict[str, str],
    changed_paths: list[str],
) -> None:
    checkpoint = root / label
    harness = checkpoint / "harness"
    for relative, content in files.items():
        path = harness / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    hashes = tree_hashes(harness)
    atomic_write_json(
        checkpoint / "checkpoint.json",
        {
            "schema_version": 1,
            "checkpoint": label,
            "parent_checkpoint": parent,
            "created_at": "2026-01-01T00:00:00+00:00",
            "workspace_commit": None,
            "harness_files": hashes,
            "harness_sha256": sha256_json(hashes),
            "source": {
                "kind": "test",
                "changed_paths": changed_paths,
                "change_manifest": None,
            },
        },
    )


def test_selective_checkpoint_restore_preserves_unrelated_improvement(
    tmp_path: Path,
) -> None:
    checkpoint_root = tmp_path / "checkpoints"
    _checkpoint(
        checkpoint_root,
        "C0",
        parent=None,
        files={"policy.md": "base policy\n", "tool.md": "base tool\n"},
        changed_paths=[],
    )
    _checkpoint(
        checkpoint_root,
        "C1",
        parent="C0",
        files={"policy.md": "harmful policy\n", "tool.md": "base tool\n"},
        changed_paths=["policy.md"],
    )
    _checkpoint(
        checkpoint_root,
        "C2",
        parent="C1",
        files={"policy.md": "harmful policy\n", "tool.md": "improved tool\n"},
        changed_paths=["tool.md"],
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "policy.md").write_text("harmful policy\n", encoding="utf-8")
    (workspace / "tool.md").write_text("improved tool\n", encoding="utf-8")
    output = tmp_path / "evolve-output"
    state = _AgentState(
        {
            "meta_role": "evolve",
            "workspace_dir": str(workspace),
            "checkpoint_root": str(checkpoint_root),
            "parent_checkpoint": "C2",
            "meta_output_dir": str(output),
        }
    )

    log = json.loads(
        tools.checkpoint_log(agent_state=state)  # type: ignore[arg-type]
    )
    assert [row["checkpoint"] for row in log["checkpoints"]] == ["C0", "C1", "C2"]

    summary = json.loads(
        tools.checkpoint_diff(
            checkpoint="C0",
            agent_state=state,  # type: ignore[arg-type]
        )
    )
    assert summary["modified"] == ["policy.md", "tool.md"]

    restored = json.loads(
        tools.restore_checkpoint_paths(
            checkpoint="C0",
            paths=["policy.md"],
            rationale=(
                "Rollback the harmful policy while retaining the independent "
                "tool improvement."
            ),
            agent_state=state,  # type: ignore[arg-type]
        )
    )
    assert restored["ok"] is True
    assert restored["changed"] is True
    assert (workspace / "policy.md").read_text(encoding="utf-8") == "base policy\n"
    assert (workspace / "tool.md").read_text(encoding="utf-8") == "improved tool\n"

    event_log = json.loads(
        (output / "historical_restores.json").read_text(encoding="utf-8")
    )
    assert event_log["events"][0]["checkpoint"] == "C0"
    assert event_log["events"][0]["paths"] == ["policy.md"]
