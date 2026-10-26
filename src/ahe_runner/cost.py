"""Priced token cost of the evaluated agent, per completed trial.

The runtime marks every evaluated-agent session directory
(``runtime.EVALUATED_SESSION_MARKERS``) with the unit and attempt it served,
and records every model call under that directory. Cost is computed only from
those calls, so judge and helper calls an adapter records elsewhere never
count, and only from the attempt that completed each unit, so an attempt
abandoned on infrastructure never counts either.

A call's cost prices four disjoint token categories: uncached input, cache
reads, cache writes, and output (which includes reasoning tokens, billed as
output by providers). A call whose prompt exceeds the long-context threshold
is priced at the long-context tier.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import ModelPricing, TokenPrices
from .errors import IntegrityError
from .io import read_json
from .runtime import EVALUATED_SESSION_MARKERS

TOKEN_CATEGORIES = ("input", "cached_input", "cache_write", "output")


@dataclass(frozen=True, slots=True)
class CallUsage:
    prompt_tokens: int
    cached_input: int
    cache_write: int
    output: int

    @property
    def uncached_input(self) -> int:
        return max(0, self.prompt_tokens - self.cached_input - self.cache_write)


def call_usage(response: dict[str, Any]) -> CallUsage | None:
    """Token usage of one recorded response, for either wire API."""

    usage = response.get("usage")
    if not isinstance(usage, dict):
        return None
    if "input_tokens" in usage:  # Responses API
        prompt = usage.get("input_tokens")
        output = usage.get("output_tokens")
        details = usage.get("input_tokens_details") or {}
    else:  # Chat Completions
        prompt = usage.get("prompt_tokens")
        output = usage.get("completion_tokens")
        details = usage.get("prompt_tokens_details") or {}
    if not isinstance(prompt, int) or not isinstance(output, int):
        return None
    cached = details.get("cached_tokens") or 0
    # Gateways name cache writes differently; all are subsets of the prompt.
    written = (
        details.get("cache_write_tokens")
        or details.get("cache_creation_tokens")
        or usage.get("cache_creation_input_tokens")
        or 0
    )
    return CallUsage(
        prompt_tokens=prompt,
        cached_input=int(cached),
        cache_write=int(written),
        output=output,
    )


def call_cost(usage: CallUsage, pricing: ModelPricing) -> float:
    prices: TokenPrices = pricing
    tier = pricing.long_context
    if tier is not None and usage.prompt_tokens > tier.above_prompt_tokens:
        prices = tier
    cached = (
        prices.cached_input_per_million
        if prices.cached_input_per_million is not None
        else prices.input_per_million
    )
    written = (
        prices.cache_write_per_million
        if prices.cache_write_per_million is not None
        else prices.input_per_million
    )
    return (
        usage.uncached_input * prices.input_per_million
        + usage.cached_input * cached
        + usage.cache_write * written
        + usage.output * prices.output_per_million
    ) / 1_000_000


def evaluation_cost(
    evaluation_dir: Path,
    pricing: ModelPricing | None,
) -> dict[str, Any]:
    """Cost and token totals per completed trial of one evaluation.

    ``mean_cost_per_trial`` is the mean over trials whose completed attempt
    has recorded usage; trials without it are counted in
    ``trials_without_usage`` and left out of the mean, never treated as zero.
    """

    completed = _completed_attempts(evaluation_dir)
    sessions = _session_directories(evaluation_dir / "runtime")
    per_trial: dict[str, dict[str, Any]] = {}
    unattributed_sessions = 0
    for directory, owner in sessions.items():
        if owner is None or owner[0] not in completed:
            unattributed_sessions += 1
            continue
        unit_id, attempt = owner
        if completed[unit_id] != attempt:
            continue  # an attempt that did not complete the unit
        row = per_trial.setdefault(
            unit_id,
            {
                "calls": 0,
                "calls_without_usage": 0,
                "tokens": dict.fromkeys(TOKEN_CATEGORIES, 0),
                "cost": 0.0,
            },
        )
        for response_path in sorted(
            (directory / "model_calls").glob("*/response.json")
        ):
            record = read_json(response_path)
            usage = call_usage(record.get("response") or {})
            row["calls"] += 1
            if usage is None:
                row["calls_without_usage"] += 1
                continue
            row["tokens"]["input"] += usage.uncached_input
            row["tokens"]["cached_input"] += usage.cached_input
            row["tokens"]["cache_write"] += usage.cache_write
            row["tokens"]["output"] += usage.output
            if pricing is not None:
                row["cost"] += call_cost(usage, pricing)
    measured = [
        row
        for row in per_trial.values()
        if row["calls"] and not row["calls_without_usage"]
    ]
    totals = dict.fromkeys(TOKEN_CATEGORIES, 0)
    for row in measured:
        for key in TOKEN_CATEGORIES:
            totals[key] += row["tokens"][key]
    count = len(measured)
    mean_cost = (
        sum(row["cost"] for row in measured) / count
        if count and pricing is not None
        else None
    )
    return {
        "schema_version": 1,
        "currency": pricing.currency if pricing is not None else None,
        "priced": pricing is not None,
        "trial_count": len(completed),
        "trials_with_usage": count,
        "trials_without_usage": len(completed) - count,
        "unattributed_sessions": unattributed_sessions,
        "mean_cost_per_trial": mean_cost,
        "mean_tokens_per_trial": {
            key: (value / count if count else None) for key, value in totals.items()
        },
        "per_trial": {
            unit_id: {
                **row,
                "cost": row["cost"] if pricing is not None else None,
            }
            for unit_id, row in sorted(per_trial.items())
        },
    }


def relative_cost_change(candidate: float | None, incumbent: float | None) -> float:
    """(C' - C) / C; 0 when either side has no measured cost."""

    if not candidate or not incumbent or not math.isfinite(incumbent):
        return 0.0
    return (candidate - incumbent) / incumbent


def _completed_attempts(evaluation_dir: Path) -> dict[str, int]:
    attempts: dict[str, int] = {}
    units = evaluation_dir / "units"
    if not units.is_dir():
        return attempts
    for completion in sorted(units.glob("*/complete.json")):
        attempt = read_json(completion).get("attempt")
        if isinstance(attempt, int):
            attempts[completion.parent.name] = attempt
    return attempts


def _session_directories(
    runtime_root: Path,
) -> dict[Path, tuple[str, int] | None]:
    """Each marked session directory and the (unit, attempt) it served."""

    owners: dict[Path, tuple[str, int] | None] = {}
    if not runtime_root.is_dir():
        return owners
    for marker in sorted(runtime_root.rglob(f"{EVALUATED_SESSION_MARKERS}/*.json")):
        directory = marker.parent.parent
        payload = read_json(marker)
        unit_id, attempt = payload.get("unit_id"), payload.get("attempt")
        owner = (
            (str(unit_id), int(attempt))
            if unit_id is not None and attempt is not None
            else None
        )
        if directory in owners and owners[directory] != owner:
            raise IntegrityError(
                f"one session directory serves different trials: {directory}"
            )
        owners[directory] = owner
    return owners
