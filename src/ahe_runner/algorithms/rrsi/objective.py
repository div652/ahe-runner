"""RRSI's empirical score S and the noise band delta.

S (paper Eq. 3) is the weighted mean of trial scores over every trial of an
evaluation: sum(w * r) / sum(w). With weight = number of rubric criteria and
score = fraction passed, S is the pooled criterion pass rate, the metric the
paper uses on Harvey LAB; with no weights it is the mean trial score.

delta (paper Sec. 3.3, Appendix E.1) bounds the difference between two
independent evaluations of the same harness. ``bootstrap`` resamples trials
within each task of one evaluation: delta = z * sqrt(2) * se(S). ``repeats``
uses several independent evaluations of the unchanged base harness. Ported
from google-research/rrsi (Apache-2.0), rrsi/calibrate.py.
"""

from __future__ import annotations

import math
import random
import statistics
from pathlib import Path
from typing import Any

from ...adapter import EvaluationResult
from ...errors import ConfigurationError, IntegrityError
from ...io import read_json

TrialRows = dict[str, list[tuple[float, float]]]


def trial_rows(result: EvaluationResult, evaluation_dir: Path) -> TrialRows:
    """(score, weight) of every trial, grouped by task key."""

    task_by_unit: dict[str, str] = {}
    for unit_path in (evaluation_dir / "units").glob("*/unit.json"):
        unit = read_json(unit_path)
        task_by_unit[str(unit["unit_id"])] = str(unit["task_key"])
    rows: TrialRows = {}
    for fact in result.trial_facts:
        if fact.score is None:
            raise ConfigurationError(
                "RRSI needs a score on every evaluation trial fact; the adapter "
                f"left unit {fact.unit_id} without one"
            )
        task = task_by_unit.get(fact.unit_id)
        if task is None:
            raise IntegrityError(f"trial fact names an unknown unit: {fact.unit_id}")
        weight = 1.0 if fact.weight is None else float(fact.weight)
        rows.setdefault(task, []).append((float(fact.score), weight))
    return rows


def weighted_score(rows: TrialRows) -> float:
    numerator = denominator = 0.0
    for trials in rows.values():
        for score, weight in trials:
            numerator += score * weight
            denominator += weight
    if denominator <= 0:
        raise IntegrityError("evaluation has no weighted trials")
    return numerator / denominator


def task_means(rows: TrialRows) -> dict[str, float]:
    result = {}
    for task, trials in rows.items():
        weight = sum(w for _, w in trials)
        result[task] = sum(s * w for s, w in trials) / weight if weight else 0.0
    return result


def check_metric(
    score: float,
    result: EvaluationResult,
    metric: str | None,
    *,
    tolerance: float = 1e-6,
) -> None:
    """Fail when S disagrees with the adapter metric it is meant to equal."""

    if metric is None:
        return
    value = result.summary.metrics.get(metric)
    if value is None and result.summary.primary.name == metric:
        value = result.summary.primary.value
    if not isinstance(value, int | float):
        raise ConfigurationError(
            f"objective.check_metric {metric!r} is not a numeric adapter metric"
        )
    if abs(float(value) - score) > tolerance:
        raise IntegrityError(
            f"RRSI score {score:.6f} differs from adapter metric {metric!r} "
            f"{float(value):.6f}; check the adapter's trial scores and weights"
        )


def bootstrap_se(rows: TrialRows, *, resamples: int, seed: int) -> float:
    """Standard error of S, resampling trials within each task."""

    generator = random.Random(seed)
    tasks = [trials for trials in rows.values() if trials]
    values: list[float] = []
    for _ in range(resamples):
        numerator = denominator = 0.0
        for trials in tasks:
            count = len(trials)
            for _ in range(count):
                score, weight = trials[generator.randrange(count)]
                numerator += score * weight
                denominator += weight
        values.append(numerator / denominator if denominator else 0.0)
    return statistics.pstdev(values) if len(values) > 1 else 0.0


def calibrate_bootstrap(
    rows: TrialRows,
    *,
    trials: int,
    z: float,
    resamples: int,
    seed: int,
) -> dict[str, Any]:
    if trials < 2:
        raise ConfigurationError(
            "bootstrap noise calibration needs at least 2 training trials per "
            "task; raise evaluation.train_trials or use noise.method=repeats/fixed"
        )
    se = bootstrap_se(rows, resamples=resamples, seed=seed)
    sd_null = math.sqrt(2) * se
    delta = z * sd_null
    return {
        "method": "bootstrap over trials within each task of one evaluation",
        "delta": round(delta, 6),
        "z": z,
        "se_bootstrap": round(se, 6),
        "sd_null": round(sd_null, 6),
        "resamples": resamples,
        "trials_per_task": trials,
        # delta scales about as 1/sqrt(trials): what other trial counts buy.
        "delta_by_trials": {
            str(k): round(delta * math.sqrt(trials / k), 6) for k in range(1, 7)
        },
    }


def calibrate_repeats(scores: list[float], *, z: float) -> dict[str, Any]:
    if len(scores) < 2:
        raise ConfigurationError("repeats calibration needs at least 2 evaluations")
    sd_null = statistics.stdev(scores) * math.sqrt(2)
    differences = [
        abs(left - right)
        for index, left in enumerate(scores)
        for right in scores[index + 1 :]
    ]
    return {
        "method": "repeated independent evaluations of the base harness",
        "delta": round(z * sd_null, 6),
        "z": z,
        "sd_null": round(sd_null, 6),
        "scores": [round(value, 6) for value in scores],
        "max_abs_difference": round(max(differences), 6),
    }
