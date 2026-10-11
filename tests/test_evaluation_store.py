from __future__ import annotations

from pathlib import Path

import pytest

from ahe_runner.errors import IntegrityError
from ahe_runner.evaluation_store import EvaluationStore
from ahe_runner.io import atomic_write_json


def test_valid_zero_completion_is_not_retried_and_attempts_are_absolute(
    tmp_path: Path,
) -> None:
    store = EvaluationStore(
        output_dir=tmp_path / "evaluation",
        request_sha256="a" * 64,
        semantic_inputs={"checkpoint": "C0", "split": "train"},
        task_ids=["task-a"],
        trials=1,
        trial_seeds=[17],
    )
    unit = store.units[0]

    first_attempt, _ = store.begin_attempt(unit)
    assert first_attempt == 1
    store.record_infrastructure_failure(
        unit,
        attempt=first_attempt,
        error={"type": "Timeout", "message": "temporary"},
    )

    second_attempt, second_dir = store.begin_attempt(unit)
    assert second_attempt == 2
    result = second_dir / "result.json"
    trace = second_dir / "trace.jsonl"
    atomic_write_json(result, {"reward": 0.0, "valid": True})
    trace.write_text('{"event":"complete"}\n', encoding="utf-8")
    store.complete_unit(
        unit,
        attempt=second_attempt,
        result_path=result,
        trace_path=trace,
    )

    assert store.pending_units() == []
    assert store.next_attempt(unit) == 3
    finalized = store.finalize({"status": "complete"})
    assert finalized.is_file()


def test_attempt_history_gaps_and_incomplete_finalization_fail(tmp_path: Path) -> None:
    store = EvaluationStore(
        output_dir=tmp_path / "evaluation",
        request_sha256="b" * 64,
        semantic_inputs={"checkpoint": "C0"},
        task_ids=["task-a"],
        trials=1,
        trial_seeds=[1],
    )
    unit = store.units[0]
    unit.artifact_dir.mkdir(parents=True)
    (unit.artifact_dir / "attempt-1").mkdir()
    (unit.artifact_dir / "attempt-3").mkdir()
    with pytest.raises(IntegrityError, match="not contiguous"):
        store.next_attempt(unit)
    with pytest.raises(IntegrityError, match="incomplete units"):
        store.finalize({"status": "complete"})


def test_semantic_run_identity_excludes_external_attempt_evidence(
    tmp_path: Path,
) -> None:
    semantic = {"checkpoint": "C2", "split": "train", "model": "exact"}
    first = EvaluationStore(
        output_dir=tmp_path / "evaluation",
        request_sha256="c" * 64,
        semantic_inputs=semantic,
        task_ids=["task-a"],
        trials=1,
        trial_seeds=[5],
    )
    (tmp_path / "fresh-preflight.json").write_text(
        '{"checked_at":"later","nonce":"different"}\n',
        encoding="utf-8",
    )
    second = EvaluationStore(
        output_dir=tmp_path / "evaluation",
        request_sha256="c" * 64,
        semantic_inputs=semantic,
        task_ids=["task-a"],
        trials=1,
        trial_seeds=[5],
    )
    assert second.run_id == first.run_id
    assert second.units[0].unit_id == first.units[0].unit_id
