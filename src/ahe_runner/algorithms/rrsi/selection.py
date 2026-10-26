"""RRSI's selection side (paper Algorithm 2) as pure functions.

For every screened candidate H' with measurement (S', C') against the
incumbent (S_t, C_t):

    dS = S' - S_t,   dC = (C' - C_t) / C_t
    c  = [dC <= beta0 + beta1 dS]               if dS > delta   (Eq. 7)
         [w_s dS - w_c dC + w_n nu > 0]          otherwise       (Eq. 17)
    admissible iff S' >= S* - delta and c                       (Eq. 5)

    H_{t+1} = argmax over admissible S', or H_t if none is admissible
    S*      = max(S*, S_{t+1})

Ported from google-research/rrsi (Apache-2.0), rrsi/selection.py.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from ...cost import relative_cost_change
from .components import novelty


@dataclass(frozen=True)
class Weights:
    beta0: float
    beta1: float
    w_s: float
    w_c: float
    w_n: float


@dataclass
class Candidate:
    variant: str
    checkpoint: str | None
    components: list[str]
    score: float | None = None
    cost: float | None = None
    gate_failure: str | None = None


@dataclass
class Decision:
    variant: str
    checkpoint: str | None
    admissible: bool
    reason: str
    S: float | None = None
    C: float | None = None
    delta_S: float | None = None
    delta_C: float | None = None
    novelty: int = 0
    guards: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def cost_rule(
    delta_s: float,
    delta_c: float,
    nu: int,
    delta: float,
    weights: Weights,
) -> tuple[bool, str]:
    if delta_s > delta:
        budget = weights.beta0 + weights.beta1 * delta_s
        ok = delta_c <= budget
        return ok, (
            f"gain {delta_s:+.4f} > delta {delta:.4f}; cost change {delta_c:+.3f} "
            f"{'<=' if ok else '>'} budget {budget:.3f} (beta0 {weights.beta0} + "
            f"beta1 {weights.beta1} * dS)"
        )
    shaped = weights.w_s * delta_s - weights.w_c * delta_c + weights.w_n * nu
    ok = shaped > 0
    return ok, (
        f"gain {delta_s:+.4f} within delta {delta:.4f}; shaped "
        f"{weights.w_s:g}*dS - {weights.w_c:g}*dC + {weights.w_n:g}*nu = "
        f"{shaped:+.4f} {'>' if ok else '<='} 0 (nu={nu})"
    )


def judge(
    candidate: Candidate,
    *,
    incumbent_score: float,
    incumbent_cost: float | None,
    best_score: float,
    delta: float,
    weights: Weights,
    accepted_counts: dict[str, int],
) -> Decision:
    if candidate.score is None:
        return Decision(
            candidate.variant,
            candidate.checkpoint,
            False,
            candidate.gate_failure or "not evaluated",
        )
    delta_s = candidate.score - incumbent_score
    delta_c = relative_cost_change(candidate.cost, incumbent_cost)
    nu = novelty(candidate.components, accepted_counts)
    decision = Decision(
        candidate.variant,
        candidate.checkpoint,
        False,
        "",
        S=candidate.score,
        C=candidate.cost,
        delta_S=delta_s,
        delta_C=delta_c,
        novelty=nu,
    )
    floor = best_score - delta
    if candidate.score < floor:
        decision.reason = (
            f"below noise-adjusted floor: S' {candidate.score:.4f} < S* "
            f"{best_score:.4f} - delta {delta:.4f}"
        )
        return decision
    ok, why = cost_rule(delta_s, delta_c, nu, delta, weights)
    if not ok:
        decision.reason = f"cost rule failed: {why}"
        return decision
    decision.admissible = True
    decision.reason = f"admissible: {why}"
    return decision


def select_round(
    candidates: list[Candidate],
    *,
    incumbent_score: float,
    incumbent_cost: float | None,
    best_score: float,
    delta: float,
    weights: Weights,
    accepted_counts: dict[str, int],
) -> tuple[Candidate | None, list[Decision]]:
    """Judge every candidate; the highest admissible score wins (first on ties)."""

    decisions = [
        judge(
            candidate,
            incumbent_score=incumbent_score,
            incumbent_cost=incumbent_cost,
            best_score=best_score,
            delta=delta,
            weights=weights,
            accepted_counts=accepted_counts,
        )
        for candidate in candidates
    ]
    admissible = [
        (candidate, decision)
        for candidate, decision in zip(candidates, decisions, strict=True)
        if decision.admissible
    ]
    if not admissible:
        return None, decisions
    winner = max(admissible, key=lambda pair: pair[1].S or 0.0)[0]
    return winner, decisions
