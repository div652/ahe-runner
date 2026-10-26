"""The edit history L_t and the summaries RRSI computes from it (paper D.2).

    L_t = {(t_i, l_i, h_i, d_i, dS_i, dC_i, a_i)}            one record per edit
    T_t = {l_i}                                             tried components
    g_t(l) = max{dS_i : l_i = l, t - t_i <= n_prune}         recent yield
    U_t = K \\ T_t,  sigma_t = 1[S_t - S_{t-w} <= delta]       exploration
    B_t = {l in T_t : g_t(l) <= 0}                           prune set

A candidate bundling n edits gets one measurement, which every edit in the
bundle carries. Candidates dropped before measurement (critic rejection,
invalid candidate) are recorded with delta_S = None and do not enter T_t or
g_t. Ported from google-research/rrsi (Apache-2.0), rrsi/history.py.

The history is rebuilt from completed stages each time the algorithm runs,
so it is always consistent with the recorded decisions.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from .components import K

MEASURED_OUTCOMES = ("ACCEPTED", "REJECTED", "LOST")


@dataclass
class History:
    records: list[dict[str, Any]] = field(default_factory=list)

    def append_candidate(
        self,
        *,
        t: int,
        variant: str,
        checkpoint: str | None,
        edits: list[dict[str, Any]],
        outcome: str,
        delta_s: float | None,
        delta_c: float | None,
        accepted: bool,
        score: float | None,
        cost: float | None,
        detail: str = "",
    ) -> None:
        for edit in edits or [{"id": None, "component": None, "hypothesis": None}]:
            self.records.append(
                {
                    "t": t,
                    "variant": variant,
                    "checkpoint": checkpoint,
                    "edit_id": edit.get("id"),
                    "component": edit.get("component"),
                    "hypothesis": edit.get("hypothesis"),
                    "targets_mode": edit.get("targets_mode"),
                    "predicted_affected": edit.get("predicted_affected"),
                    "delta_S": None if delta_s is None else round(delta_s, 6),
                    "delta_C": None if delta_c is None else round(delta_c, 6),
                    "accepted": bool(accepted),
                    "outcome": outcome,
                    "S": None if score is None else round(score, 6),
                    "C": cost,
                    "bundle": len(edits or []),
                    "detail": detail[:600] if detail else "",
                }
            )

    def measured(self) -> list[dict[str, Any]]:
        return [
            record
            for record in self.records
            if record.get("delta_S") is not None and record.get("component") in K
        ]

    def tried(self) -> set[str]:
        return {record["component"] for record in self.measured()}

    def accepted_edits(self) -> dict[str, list[dict[str, Any]]]:
        """Machinery in the incumbent, per component (accepted edits)."""

        result: dict[str, list[dict[str, Any]]] = {name: [] for name in K}
        for record in self.records:
            if record.get("accepted") and record.get("component") in result:
                result[record["component"]].append(
                    {
                        "t": record["t"],
                        "checkpoint": record.get("checkpoint"),
                        "edit_id": record.get("edit_id"),
                        "hypothesis": record.get("hypothesis"),
                        "delta_S": record.get("delta_S"),
                    }
                )
        return result

    def accepted_counts(self) -> dict[str, int]:
        return {name: len(rows) for name, rows in self.accepted_edits().items()}

    def recent_yield(self, t: int, n_prune: int) -> dict[str, float]:
        """g_t(l) for every tried component; -inf when nothing is recent."""

        result = {name: -math.inf for name in self.tried()}
        for record in self.measured():
            if t - int(record["t"]) <= n_prune:
                name = record["component"]
                result[name] = max(result.get(name, -math.inf), record["delta_S"])
        return result

    def prune_set(self, t: int, n_prune: int) -> list[dict[str, Any]]:
        """B_t with, per component, the accepted machinery to remove."""

        recent = self.recent_yield(t, n_prune)
        accepted = self.accepted_edits()
        return [
            {
                "component": name,
                "recent_best_gain": (
                    None if recent[name] == -math.inf else round(recent[name], 5)
                ),
                "accepted_edits_in_incumbent": accepted.get(name, []),
            }
            for name in sorted(recent)
            if recent[name] <= 0
        ]

    def render(self, limit: int = 40) -> list[dict[str, Any]]:
        """Compact recent records for the proposer. Gate failures without a
        measurement are kept only for the most recent few."""

        kept: list[dict[str, Any]] = []
        unmeasured = 0
        for record in reversed(self.records):
            if record.get("delta_S") is None:
                unmeasured += 1
                if unmeasured > 4:
                    continue
            kept.append(record)
            if len(kept) >= limit:
                break
        keys = (
            "t",
            "variant",
            "checkpoint",
            "edit_id",
            "component",
            "hypothesis",
            "targets_mode",
            "delta_S",
            "delta_C",
            "accepted",
            "outcome",
            "bundle",
            "detail",
        )
        return [
            {key: record.get(key) for key in keys if record.get(key) is not None}
            for record in reversed(kept)
        ]


def stall_flag(trajectory: list[float], t: int, window: int, delta: float) -> int:
    """sigma_t = 1[S_t - S_{t-w} <= delta]; 0 while fewer than w rounds exist."""

    if t < window or t >= len(trajectory):
        return 0
    return int(trajectory[t] - trajectory[t - window] <= delta)


def exploration(stall: int, tried: set[str], reserved_slots: int) -> dict[str, Any]:
    """E_t = (sigma_t, U_t, m_draft) and the directive shown to the proposer."""

    untried = [name for name in K if name not in tried]
    if stall and untried:
        text = (
            "STALL: the incumbent has not moved by more than the noise band over "
            f"the last rounds (sigma_t = 1). {reserved_slots} candidate slot(s) this "
            "round are RESERVED for exploratory edits on components the run has "
            f"never exercised: {untried}. A variant holding a reserved slot must "
            "put at least one edit on one of those components."
        )
    elif untried:
        text = (
            f"Components not yet exercised in this run: {untried}. Not mandatory "
            "this round (sigma_t = 0), but evidence about them is still missing."
        )
    else:
        text = "Every component in K has been exercised at least once."
    return {
        "sigma": stall,
        "untried": untried,
        "m_draft": reserved_slots,
        "text": text,
    }
