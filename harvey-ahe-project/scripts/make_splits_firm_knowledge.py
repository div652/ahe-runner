"""Build ``splits-firm-knowledge.json`` for a single C0 evaluation (campaign 9).

1. Train is every LAB task under ``tasks/firm-knowledge/`` (all of them share
   the ``firm-knowledge/dms`` store through ``docs_dir``), sorted by task id.
   No eligibility filter is applied; the campaign sets
   ``apply_task_filter: false``.
2. AHE requires non-empty validation and test splits. Each holds one
   placeholder: the first validation and the first test task of
   ``splits-v2.json``, whose pins must still match. Campaign 9 runs only the
   C0 training evaluation, so the placeholders are never evaluated.

Usage: python scripts/make_splits_firm_knowledge.py --lab-root ../../harvey-labs
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from harvey_ahe.tasks import (  # noqa: E402
    load_pinned_task,
    load_split_file,
    load_task,
)

from ahe_runner.adapter import SplitName  # noqa: E402

IDENTITY = "harvey-lab-firm-knowledge-250"
AREA = "firm-knowledge"


def _pin(lab_root: Path, task_id: str) -> dict[str, str]:
    task = load_task(lab_root, task_id)
    return {
        "task_id": task_id,
        "task_json_sha256": task.task_json_sha256,
        "documents_sha256": task.documents_sha256,
    }


def build(lab_root: Path, v2_path: Path) -> dict[str, object]:
    v2 = load_split_file(v2_path)
    lab_commit = subprocess.run(
        ["git", "-C", str(lab_root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if lab_commit != v2.lab_commit:
        raise SystemExit(f"LAB is at {lab_commit}, splits-v2 pins {v2.lab_commit}")

    tasks_root = lab_root / "tasks"
    train = sorted(
        path.parent.relative_to(tasks_root).as_posix()
        for path in (tasks_root / AREA).rglob("task.json")
    )
    if not train:
        raise SystemExit(f"no tasks under tasks/{AREA}")
    shared = {load_task(lab_root, task_id).documents_dir for task_id in train}
    if len(shared) != 1:
        raise SystemExit(f"{AREA} tasks use {len(shared)} document stores")

    placeholders = {}
    for name in (SplitName.VALIDATION, SplitName.TEST):
        pinned = v2.splits[name][0]
        load_pinned_task(lab_root, pinned)
        placeholders[name.value] = pinned.task_id

    policy = (
        f"Train: all {len(train)} LAB tasks under tasks/{AREA}/ (sorted by "
        f"task id), which share the {AREA}/dms document store through "
        "docs_dir; no eligibility filter (apply_task_filter: false). "
        "Validation and test each hold one placeholder, the first validation "
        "and first test task of splits-v2.json, because AHE requires "
        "non-empty splits; campaign 9 evaluates only C0 on train, so they are "
        "never run. Built by scripts/make_splits_firm_knowledge.py. Pins are "
        "sha256 of task.json and of the document tree hash map (the shared "
        "store for firm-knowledge tasks)."
    )
    return {
        "schema_version": 1,
        "identity": IDENTITY,
        "lab_commit": lab_commit,
        "selection_policy": policy,
        "splits": {
            "train": [_pin(lab_root, task_id) for task_id in train],
            "validation": [_pin(lab_root, placeholders["validation"])],
            "test": [_pin(lab_root, placeholders["test"])],
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--lab-root", type=Path, required=True)
    parser.add_argument(
        "--splits-v2", type=Path, default=PROJECT_ROOT / "splits-v2.json"
    )
    parser.add_argument(
        "--output", type=Path, default=PROJECT_ROOT / "splits-firm-knowledge.json"
    )
    args = parser.parse_args()
    document = build(args.lab_root.resolve(), args.splits_v2)
    args.output.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    load_split_file(args.output)
    counts = {name: len(rows) for name, rows in document["splits"].items()}
    print(f"wrote {args.output} {counts}")


if __name__ == "__main__":
    main()
