"""Build ``splits-firm-knowledge-80-20-20.json`` for campaign 12.

The split is random within difficulty strata, so each part's difficulty
matches the 250-task pool. Campaign 10's plain random draw put easier tasks in
train by chance (9b dual all-pass 0.342 on its 60 train tasks, against 0.224
on all 250).

1. The pool is every LAB task under ``tasks/firm-knowledge/`` (250 at the
   pinned commit), as in campaign 10. No eligibility filter is applied.
2. Difficulty is 9b's result on each task: gpt-5.6-sol, reasoning high, the
   LAB-exact harness, one trial per task (runbook 5o). Tasks are sorted from
   hardest to easiest by (dual all-pass rate, criterion pass rate), ties
   broken by ``sha256(f"{SALT}:order:{task_id}")``.
3. The sorted list is cut into 10 blocks of 25. Within each block the tasks
   are put in the order ``sha256(f"{SALT}:assign:{task_id}")``: the first 2
   are test, the next 2 validation, the next 8 train, and the other 13 are not
   used. This gives 80 train, 20 validation and 20 test tasks.
4. The LAB commit must equal the one pinned by ``splits-firm-knowledge.json``
   (campaigns 9 and 9b), and 9b must have exactly one graded result for every
   pool task.

The output records each task's 9b values and block under ``stratification``,
so the split can be checked without the 9b artifacts. The output is the same
on every run.

Usage: python scripts/make_splits_firm_knowledge_c12.py --lab-root ../../harvey-labs
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from harvey_ahe.tasks import load_split_file, load_task  # noqa: E402

IDENTITY = "harvey-lab-firm-knowledge-80-20-20-stratified-9b"
AREA = "firm-knowledge"
SALT = "harvey-lab-campaign-12"
BLOCK_SIZE = 25
PER_BLOCK = (("test", 2), ("validation", 2), ("train", 8))
BASELINE_9B = (
    PROJECT_ROOT
    / "campaign-artifacts/harvey-lab-campaign-9b-lab-exact-token-fix"
    / "evaluations/C0-train/units"
)


def _hash(kind: str, task_id: str) -> str:
    return hashlib.sha256(f"{SALT}:{kind}:{task_id}".encode()).hexdigest()


def _pin(lab_root: Path, task_id: str) -> dict[str, str]:
    task = load_task(lab_root, task_id)
    return {
        "task_id": task_id,
        "task_json_sha256": task.task_json_sha256,
        "documents_sha256": task.documents_sha256,
    }


def _load_9b(units: Path) -> dict[str, dict[str, float]]:
    found: dict[str, list[dict[str, float]]] = {}
    for path in sorted(units.glob("*/attempt-*/result.json")):
        result = json.loads(path.read_text(encoding="utf-8"))
        found.setdefault(result["task_id"], []).append(
            {
                "dual_all_pass_rate": float(result["dual"]["dual_all_pass_rate"]),
                "criterion_pass_rate": float(result["score"]),
            }
        )
    repeated = sorted(task for task, rows in found.items() if len(rows) != 1)
    if repeated:
        raise SystemExit(f"9b has more than one result for {repeated}")
    return {task: rows[0] for task, rows in found.items()}


def _means(tasks: list[str], values: dict[str, dict[str, float]]) -> dict[str, float]:
    return {
        key: round(statistics.mean(values[task][key] for task in tasks), 4)
        for key in ("dual_all_pass_rate", "criterion_pass_rate")
    }


def build(lab_root: Path, campaign_9_path: Path, units: Path) -> dict[str, object]:
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
    if len(pool) % BLOCK_SIZE:
        raise SystemExit(f"{len(pool)} tasks is not a multiple of {BLOCK_SIZE}")
    shared = {load_task(lab_root, task_id).documents_dir for task_id in pool}
    if len(shared) != 1:
        raise SystemExit(f"{AREA} tasks use {len(shared)} document stores")
    values = _load_9b(units)
    if set(values) != set(pool):
        raise SystemExit("9b results do not cover exactly the task pool")

    ordered = sorted(
        pool,
        key=lambda task: (
            values[task]["dual_all_pass_rate"],
            values[task]["criterion_pass_rate"],
            _hash("order", task),
        ),
    )
    splits: dict[str, list[str]] = {name: [] for name, _ in PER_BLOCK}
    block_of: dict[str, int] = {}
    for block in range(len(pool) // BLOCK_SIZE):
        members = ordered[block * BLOCK_SIZE : (block + 1) * BLOCK_SIZE]
        start = 0
        for name, size in PER_BLOCK:
            for task in sorted(members, key=lambda t: _hash("assign", t))[
                start : start + size
            ]:
                splits[name].append(task)
            start += size
        for task in members:
            block_of[task] = block
    for name in splits:
        splits[name].sort()
    used = {task for tasks in splits.values() for task in tasks}
    unused = [task for task in pool if task not in used]

    policy = (
        f"Pool: all {len(pool)} LAB tasks under tasks/{AREA}/, which share the "
        f"{AREA}/dms document store through docs_dir; no eligibility filter "
        "(apply_task_filter: false). Stratified by 9b difficulty (gpt-5.6-sol "
        "high, LAB-exact harness, one trial per task): tasks sorted by (9b "
        "dual all-pass rate, 9b criterion pass rate, "
        f"sha256('{SALT}:order:' + task_id)), cut into "
        f"{len(pool) // BLOCK_SIZE} blocks of {BLOCK_SIZE}; within each block, "
        f"tasks sorted by sha256('{SALT}:assign:' + task_id): first 2 test, "
        "next 2 validation, next 8 train, other 13 not used. Each split is "
        "listed sorted by task id. Built by "
        "scripts/make_splits_firm_knowledge_c12.py. Pins are sha256 of "
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
        "stratification": {
            "source": "campaign 9b C0-train results (runbook 5o)",
            "mean_9b": {
                "all": _means(pool, values),
                **{name: _means(splits[name], values) for name in splits},
                "unused": _means(unused, values),
            },
            "tasks": {
                task: {**values[task], "block": block_of[task]} for task in pool
            },
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
    parser.add_argument("--baseline-units", type=Path, default=BASELINE_9B)
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "splits-firm-knowledge-80-20-20.json",
    )
    args = parser.parse_args()
    document = build(
        args.lab_root.resolve(), args.campaign_9_splits, args.baseline_units
    )
    args.output.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    load_split_file(args.output)
    counts = {name: len(rows) for name, rows in document["splits"].items()}
    print(f"wrote {args.output} {counts}")
    for name, means in document["stratification"]["mean_9b"].items():
        print(f"  {name:10s} 9b dual all-pass {means['dual_all_pass_rate']:.3f}"
              f"  criterion pass {means['criterion_pass_rate']:.3f}")


if __name__ == "__main__":
    main()
