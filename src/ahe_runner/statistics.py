"""Paired, task-level uncertainty for checkpoint comparisons (AHE-040).

Selection and final-test decisions compare checkpoints on the same tasks. The
per-task score differences are resampled over tasks, so the interval covers
both trial noise and which tasks happened to be sampled. The computation is
deterministic: the same summaries always give the same interval.
"""

from __future__ import annotations

import math
import random
from statistics import fmean, stdev
from typing import Any

from .adapter import MetricSummary

BOOTSTRAP_RESAMPLES = 2000
CONFIDENCE = 0.95
_SEED = 20261026
_Z_95 = 1.959963984540054


def paired_task_comparison(
    baseline: MetricSummary,
    candidate: MetricSummary,
    *,
    resamples: int = BOOTSTRAP_RESAMPLES,
) -> dict[str, Any]:
    """Compare two summaries task by task, with a paired bootstrap interval.

    The difference is ``candidate - baseline`` in the adapter's per-task score.
    The primary-metric delta is reported alongside, because an adapter's
    primary metric need not be the mean task score.
    """

    before = {state.task_key: state for state in baseline.task_states}
    after = {state.task_key: state for state in candidate.task_states}
    common = sorted(set(before) & set(after))
    report: dict[str, Any] = {
        "primary_metric": candidate.primary.name,
        "primary_delta": candidate.primary.value - baseline.primary.value,
        "higher_is_better": candidate.primary.higher_is_better,
        "paired_task_count": len(common),
    }
    if len(common) < 2:
        report["available"] = False
        report["reason"] = "fewer than two tasks have scores in both summaries"
        return report
    differences = [after[key].score - before[key].score for key in common]
    mean = fmean(differences)
    spread = stdev(differences)
    generator = random.Random(_SEED)
    count = len(differences)
    means = sorted(
        fmean(differences[generator.randrange(count)] for _ in range(count))
        for _ in range(resamples)
    )
    tail = (1.0 - CONFIDENCE) / 2.0
    low = means[max(0, math.floor(tail * resamples))]
    high = means[min(resamples - 1, math.ceil((1.0 - tail) * resamples) - 1)]
    trials = sorted(
        {
            state.trials
            for state in (*before.values(), *after.values())
            if state.trials is not None
        }
    )
    report.update(
        {
            "available": True,
            "mean_task_score_delta": mean,
            "confidence": CONFIDENCE,
            "bootstrap_interval": [low, high],
            "bootstrap_resamples": resamples,
            "interval_excludes_zero": low > 0.0 or high < 0.0,
            # Half-width of a normal 95% interval for the paired mean: a
            # difference smaller than this is within the measured noise.
            "minimum_detectable_difference": _Z_95 * spread / math.sqrt(count),
            "trials_per_task": trials,
        }
    )
    return report


def selection_uncertainty(
    summaries: dict[str, MetricSummary],
    *,
    selected: str,
    baseline: str = "C0",
) -> dict[str, Any]:
    """Paired comparisons behind a validation selection."""

    chosen = summaries[selected]
    versus_selected = {
        label: paired_task_comparison(summary, chosen)
        for label, summary in summaries.items()
        if label != selected
    }
    within_noise = sorted(
        (
            label
            for label, comparison in versus_selected.items()
            if comparison.get("available") and not comparison["interval_excludes_zero"]
        ),
        key=lambda label: int(label.removeprefix("C")),
    )
    return {
        "method": (
            "paired task bootstrap of per-task score differences; "
            f"{CONFIDENCE:.0%} percentile interval"
        ),
        "selected_vs_baseline": (
            paired_task_comparison(summaries[baseline], chosen)
            if baseline in summaries and baseline != selected
            else None
        ),
        "selected_vs_each_candidate": versus_selected,
        "candidates_not_distinguishable_from_selected": within_noise,
    }
