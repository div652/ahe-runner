from __future__ import annotations

import pytest

from ahe_runner.adapter import MetricSummary, MetricValue, TaskState
from ahe_runner.statistics import paired_task_comparison, selection_uncertainty


def _summary(scores: dict[str, float], trials: int = 3) -> MetricSummary:
    return MetricSummary(
        primary=MetricValue(name="pass_rate", value=sum(scores.values()) / len(scores)),
        metrics={},
        task_count=len(scores),
        rollout_count=len(scores) * trials,
        task_states=[
            TaskState(task_key=key, state="scored", score=value, trials=trials)
            for key, value in scores.items()
        ],
    )


def test_paired_comparison_is_deterministic_and_detects_clear_gains() -> None:
    tasks = [f"t{index}" for index in range(40)]
    baseline = _summary({task: 0.2 for task in tasks})
    better = _summary(
        {task: 0.8 if index % 4 else 0.4 for index, task in enumerate(tasks)}
    )
    report = paired_task_comparison(baseline, better)
    assert report == paired_task_comparison(baseline, better)
    assert report["available"] is True
    assert report["paired_task_count"] == 40
    assert report["mean_task_score_delta"] == pytest.approx(0.5)
    low, high = report["bootstrap_interval"]
    assert 0.0 < low <= 0.5 <= high
    assert report["interval_excludes_zero"] is True
    assert report["trials_per_task"] == [3]
    assert 0 < report["minimum_detectable_difference"] < 0.1


def test_noise_is_reported_when_differences_cancel() -> None:
    tasks = [f"t{index}" for index in range(20)]
    baseline = _summary({task: 0.5 for task in tasks})
    mixed = _summary(
        {task: (1.0 if index % 2 else 0.0) for index, task in enumerate(tasks)}
    )
    report = paired_task_comparison(baseline, mixed)
    assert report["interval_excludes_zero"] is False
    assert report["minimum_detectable_difference"] > 0.2

    tiny = paired_task_comparison(_summary({"a": 1.0}), _summary({"a": 0.0}))
    assert tiny["available"] is False


def test_selection_uncertainty_lists_indistinguishable_candidates() -> None:
    tasks = [f"t{index}" for index in range(30)]
    summaries = {
        "C0": _summary({task: 0.0 for task in tasks}),
        "C1": _summary(
            {task: (1.0 if index % 3 else 0.0) for index, task in enumerate(tasks)}
        ),
        "C2": _summary(
            {task: (1.0 if index % 3 != 1 else 0.0) for index, task in enumerate(tasks)}
        ),
    }
    report = selection_uncertainty(summaries, selected="C2")
    assert report["selected_vs_baseline"]["interval_excludes_zero"] is True
    assert report["candidates_not_distinguishable_from_selected"] == ["C1"]
    assert (
        selection_uncertainty(summaries, selected="C0")["selected_vs_baseline"] is None
    )
