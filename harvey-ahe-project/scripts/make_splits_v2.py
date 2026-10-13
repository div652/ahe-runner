"""Build ``splits-v2.json``: 60 train / 15 validation / 15 test LAB tasks.

The output is deterministic for a given LAB checkout and pilot split file:

1. Eligible tasks are those that load and pass ``eligibility_errors`` (the
   same filter as the pilot split).
2. Train starts with all 12 pilot tasks from ``splits.json``, whose pins must
   still match.
3. The other eligible tasks are grouped by practice area (first task-id
   segment). Areas are ordered by sha256(area), and tasks within an area by
   sha256(task_id). Dealing one task per area per pass gives one sequence.
4. The first 15 tasks in that sequence are test, the next 15 validation and
   the next 48 train. Validation and test must each cover 15 distinct areas.

Usage: python scripts/make_splits_v2.py --lab-root ../../harvey-labs
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from harvey_ahe.tasks import (  # noqa: E402
    MAX_CRITERIA,
    MAX_DOCUMENT_BYTES,
    MAX_DOCUMENT_FILES,
    MIN_CRITERIA,
    eligibility_errors,
    load_pinned_task,
    load_split_file,
    load_task,
)

from ahe_runner.adapter import SplitName  # noqa: E402
from ahe_runner.errors import ConfigurationError  # noqa: E402

IDENTITY = "harvey-lab-ahe-v2-60-15-15"
NEW_TEST = 15
NEW_VALIDATION = 15
NEW_TRAIN = 48


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _area(task_id: str) -> str:
    return task_id.split("/", 1)[0]


def _eligible(lab_root: Path) -> list[str]:
    tasks_root = lab_root / "tasks"
    eligible: list[str] = []
    for config_path in sorted(tasks_root.rglob("task.json")):
        task_id = config_path.parent.relative_to(tasks_root).as_posix()
        try:
            task = load_task(lab_root, task_id)
        except ConfigurationError:
            continue
        if not eligibility_errors(task):
            eligible.append(task_id)
    return eligible


def _deal(task_ids: list[str]) -> list[str]:
    by_area: dict[str, list[str]] = defaultdict(list)
    for task_id in task_ids:
        by_area[_area(task_id)].append(task_id)
    queues = [sorted(by_area[area], key=_sha) for area in sorted(by_area, key=_sha)]
    sequence: list[str] = []
    while any(queues):
        for queue in queues:
            if queue:
                sequence.append(queue.pop(0))
    return sequence


def _pin(lab_root: Path, task_id: str) -> dict[str, str]:
    task = load_task(lab_root, task_id)
    return {
        "task_id": task_id,
        "task_json_sha256": task.task_json_sha256,
        "documents_sha256": task.documents_sha256,
    }


def build(lab_root: Path, pilot_path: Path) -> dict[str, object]:
    pilot = load_split_file(pilot_path)
    lab_commit = subprocess.run(
        ["git", "-C", str(lab_root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if lab_commit != pilot.lab_commit:
        raise SystemExit(f"LAB is at {lab_commit}, pilot pins {pilot.lab_commit}")
    pilot_ids = [
        task.task_id
        for name in (SplitName.TRAIN, SplitName.VALIDATION, SplitName.TEST)
        for task in pilot.splits[name]
    ]
    for name in SplitName:
        for task in pilot.splits[name]:
            load_pinned_task(lab_root, task)

    eligible = _eligible(lab_root)
    missing = sorted(set(pilot_ids) - set(eligible))
    if missing:
        raise SystemExit(f"pilot tasks no longer eligible: {missing}")
    sequence = _deal([task_id for task_id in eligible if task_id not in pilot_ids])
    needed = NEW_TEST + NEW_VALIDATION + NEW_TRAIN
    if len(sequence) < needed:
        raise SystemExit(f"only {len(sequence)} non-pilot tasks; need {needed}")
    test = sequence[:NEW_TEST]
    validation = sequence[NEW_TEST : NEW_TEST + NEW_VALIDATION]
    train = pilot_ids + sequence[NEW_TEST + NEW_VALIDATION : needed]
    for name, rows in (("test", test), ("validation", validation)):
        if len({_area(task_id) for task_id in rows}) != len(rows):
            raise SystemExit(f"{name} repeats a practice area")

    policy = (
        "Eligible LAB tasks: no docs_dir; inline instructions; top-level "
        f"deliverables; own documents/ with 1-{MAX_DOCUMENT_FILES} files "
        f"totalling <= {MAX_DOCUMENT_BYTES:,} bytes; {MIN_CRITERIA}-"
        f"{MAX_CRITERIA} criteria ({len(eligible)} tasks at this commit). "
        "Train starts with the 12 tasks of harvey-lab-ahe-pilot-v1. The other "
        "eligible tasks are grouped by practice area (first task-id segment); "
        "areas are ordered by sha256(area) and tasks within an area by "
        "sha256(task_id); dealing one task per area per pass gives one "
        f"sequence. Its first {NEW_TEST} tasks are test, the next "
        f"{NEW_VALIDATION} validation, and the next {NEW_TRAIN} train. "
        "Validation and test each cover distinct practice areas. Built by "
        "scripts/make_splits_v2.py. Pins are sha256 of task.json and of the "
        "documents/ tree hash map."
    )
    return {
        "schema_version": 1,
        "identity": IDENTITY,
        "lab_commit": lab_commit,
        "selection_policy": policy,
        "splits": {
            "train": [_pin(lab_root, task_id) for task_id in train],
            "validation": [_pin(lab_root, task_id) for task_id in validation],
            "test": [_pin(lab_root, task_id) for task_id in test],
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--lab-root", type=Path, required=True)
    parser.add_argument("--pilot", type=Path, default=PROJECT_ROOT / "splits.json")
    parser.add_argument("--output", type=Path, default=PROJECT_ROOT / "splits-v2.json")
    args = parser.parse_args()
    document = build(args.lab_root.resolve(), args.pilot)
    args.output.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    load_split_file(args.output)
    counts = {name: len(rows) for name, rows in document["splits"].items()}
    print(f"wrote {args.output} {counts}")


if __name__ == "__main__":
    main()
