"""Build ``splits-firm-knowledge-60-15-15.json`` for campaign 10.

1. The pool is every LAB task under ``tasks/firm-knowledge/`` (250 at the
   pinned commit). All of them share the ``firm-knowledge/dms`` store through
   ``docs_dir``. No eligibility filter is applied; the campaign sets
   ``apply_task_filter: false``.
2. The pool is put in a seeded random order: tasks are sorted by
   ``sha256(f"{SALT}:{task_id}")``. The first 15 tasks are test, the next 15
   validation and the next 60 train. The remaining tasks are not used.
3. The LAB commit must equal the one pinned by ``splits-firm-knowledge.json``
   (campaign 9), so both campaigns grade the same task files.

The output is the same on every run.

Usage: python scripts/make_splits_firm_knowledge_c10.py --lab-root ../../harvey-labs
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from harvey_ahe.tasks import load_split_file, load_task  # noqa: E402

IDENTITY = "harvey-lab-firm-knowledge-60-15-15"
AREA = "firm-knowledge"
SALT = "harvey-lab-campaign-10"
SIZES = (("test", 15), ("validation", 15), ("train", 60))


def _pin(lab_root: Path, task_id: str) -> dict[str, str]:
    task = load_task(lab_root, task_id)
    return {
        "task_id": task_id,
        "task_json_sha256": task.task_json_sha256,
        "documents_sha256": task.documents_sha256,
    }


def _order_key(task_id: str) -> str:
    return hashlib.sha256(f"{SALT}:{task_id}".encode()).hexdigest()


def build(lab_root: Path, campaign_9_path: Path) -> dict[str, object]:
    campaign_9 = load_split_file(campaign_9_path)
    lab_commit = subprocess.run(
        ["git", "-C", str(lab_root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if lab_commit != campaign_9.lab_commit:
        raise SystemExit(
            f"LAB is at {lab_commit}, {campaign_9_path.name} pins "
            f"{campaign_9.lab_commit}"
        )

    tasks_root = lab_root / "tasks"
    pool = sorted(
        path.parent.relative_to(tasks_root).as_posix()
        for path in (tasks_root / AREA).rglob("task.json")
    )
    needed = sum(size for _, size in SIZES)
    if len(pool) < needed:
        raise SystemExit(f"{len(pool)} tasks under tasks/{AREA}, need {needed}")
    shared = {load_task(lab_root, task_id).documents_dir for task_id in pool}
    if len(shared) != 1:
        raise SystemExit(f"{AREA} tasks use {len(shared)} document stores")

    ordered = sorted(pool, key=_order_key)
    splits: dict[str, list[str]] = {}
    start = 0
    for name, size in SIZES:
        splits[name] = sorted(ordered[start : start + size])
        start += size

    policy = (
        f"Pool: all {len(pool)} LAB tasks under tasks/{AREA}/, which share the "
        f"{AREA}/dms document store through docs_dir; no eligibility filter "
        "(apply_task_filter: false). Seeded random order: tasks sorted by "
        f"sha256('{SALT}:' + task_id). The first 15 are test, the next 15 "
        f"validation, the next 60 train; the other {len(pool) - needed} are "
        "not used. Each split is listed sorted by task id. Built by "
        "scripts/make_splits_firm_knowledge_c10.py. Pins are sha256 of "
        "task.json and of the document tree hash map (the shared store)."
    )
    return {
        "schema_version": 1,
        "identity": IDENTITY,
        "lab_commit": lab_commit,
        "selection_policy": policy,
        "splits": {
            name: [_pin(lab_root, task_id) for task_id in splits[name]]
            for name in ("train", "validation", "test")
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--lab-root", type=Path, required=True)
    parser.add_argument(
        "--campaign-9-splits",
        type=Path,
        default=PROJECT_ROOT / "splits-firm-knowledge.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "splits-firm-knowledge-60-15-15.json",
    )
    args = parser.parse_args()
    document = build(args.lab_root.resolve(), args.campaign_9_splits)
    args.output.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    load_split_file(args.output)
    counts = {name: len(rows) for name, rows in document["splits"].items()}
    print(f"wrote {args.output} {counts}")


if __name__ == "__main__":
    main()
