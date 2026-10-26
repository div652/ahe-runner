"""Annealed edit budget (RRSI Eq. 4).

    b_t = ceil(b_min + (b_max - b_min) * 1/2 * (1 + cos(pi * t / T))), t = 0..T-1

Early rounds may bundle several coordinated edits in one candidate; late
rounds become sparse and attributable. As in the reference implementation,
the ceiling reaches b_min only at t = T: with T = 20, b_min = 1, b_max = 3,
rounds 0-9 allow 3 edits and rounds 10-19 allow 2. Ported from google-research/rrsi
(Apache-2.0), rrsi/schedule.py.
"""

from __future__ import annotations

import math


def edit_budget(t: int, rounds: int, b_min: int, b_max: int) -> int:
    """b_t for round t (0-indexed) of a run of ``rounds`` rounds."""

    if rounds <= 0:
        return int(b_max)
    t = max(0, min(int(t), int(rounds)))
    value = b_min + (b_max - b_min) * 0.5 * (1.0 + math.cos(math.pi * t / rounds))
    # Guard against 1.0000000002 -> 2 from floating error at t = T.
    return math.ceil(round(value, 9))
