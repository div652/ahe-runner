"""RRSI settings (``algorithm.settings`` when ``algorithm.name: rrsi``).

Defaults are the paper's agentic-workspace (Harvey LAB) instance, Table 5:
T = campaign.rounds (20), k = evaluation.train_trials (2), m = 2,
b_min = 1, b_max = 3, w = 3, m_draft = 1, n_prune = 4, beta0 = 0.10,
beta1 = 35.4, delta_z = 2.

Two groups are expressed so that they transfer between setups:

- ``within_band``: the paper fixes w_s, w_c, w_n per instance. Only their
  ratios matter, and w_s is meaningful only relative to delta. This file
  takes the two allowances they imply and derives w_s from the measured
  delta: w_s = band_edge_cost_allowance * w_c / delta and
  w_n = novelty_cost_allowance * w_c. With the paper's delta (0.004) the
  defaults reproduce its values (w_s = 1414, w_c = 15, w_n = 0.5).
  Explicit ``w_s``/``w_n`` override the derivation.
- ``stall_window`` and ``prune_window``: None scales them with the round
  count, max(2, round(0.15 T)) and max(3, round(0.2 T)), which gives the
  paper's 3 and 4 at T = 20.

beta1 is relative cost per unit of score (a fraction), so it transfers as is
when the objective is the same kind of fraction.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NoiseSettings(_Strict):
    # bootstrap: resample trials within each task of the C0 evaluation.
    # repeats: evaluate C0 ``repeats`` times independently.
    # fixed: use ``delta`` as given.
    method: Literal["bootstrap", "repeats", "fixed"] = "bootstrap"
    delta: float | None = Field(default=None, gt=0)
    repeats: int = Field(default=2, ge=2, le=10)
    z: float = Field(default=2.0, gt=0)
    bootstrap_resamples: int = Field(default=2000, ge=100)
    bootstrap_seed: int = 7

    @model_validator(mode="after")
    def require_fixed_delta(self) -> NoiseSettings:
        if self.method == "fixed" and self.delta is None:
            raise ValueError("noise.method=fixed requires noise.delta")
        if self.method != "fixed" and self.delta is not None:
            raise ValueError("noise.delta is used only with noise.method=fixed")
        return self


class WithinBandSettings(_Strict):
    w_c: float = Field(default=15.0, gt=0)
    # Relative cost increase admitted for a gain of exactly delta.
    band_edge_cost_allowance: float = Field(default=0.377, ge=0)
    # Relative cost increase a never-accepted structural component earns.
    novelty_cost_allowance: float = Field(default=0.5 / 15.0, ge=0)
    w_s: float | None = Field(default=None, ge=0)
    w_n: float | None = Field(default=None, ge=0)


class ObjectiveSettings(_Strict):
    # Adapter metric that must equal S (sum of weight * score over trials,
    # divided by the sum of weights), as a check on the adapter's trial
    # scores and weights. None: no check.
    check_metric: str | None = None


class RoleLimits(_Strict):
    analyst_max_iterations: int = Field(default=80, gt=0)
    digester_max_iterations: int = Field(default=40, gt=0)
    proposer_max_iterations: int = Field(default=300, gt=0)
    critic_max_iterations: int = Field(default=12, gt=0)
    digests_per_call: int = Field(default=8, gt=0)
    # Digesters that may run at once; None: meta.max_concurrent_model_calls.
    digest_concurrency: int | None = Field(default=None, gt=0)


class RrsiSettings(_Strict):
    variants_per_round: int = Field(default=2, ge=1, le=8)
    edit_budget_min: int = Field(default=1, ge=1)
    edit_budget_max: int = Field(default=3, ge=1)
    stall_window: int | None = Field(default=None, ge=1)
    reserved_exploration_slots: int = Field(default=1, ge=0)
    prune_window: int | None = Field(default=None, ge=1)
    repair_rounds: int = Field(default=5, ge=0)
    beta0: float = Field(default=0.10, ge=0)
    beta1: float = Field(default=35.4, ge=0)
    # priced: relative change in priced cost per trial (models.evaluated.
    # pricing); disabled: dC = 0 for every candidate.
    cost: Literal["priced", "disabled"] = "priced"
    noise: NoiseSettings = Field(default_factory=NoiseSettings)
    within_band: WithinBandSettings = Field(default_factory=WithinBandSettings)
    objective: ObjectiveSettings = Field(default_factory=ObjectiveSettings)
    roles: RoleLimits = Field(default_factory=RoleLimits)

    @model_validator(mode="after")
    def validate_budget(self) -> RrsiSettings:
        if self.edit_budget_min > self.edit_budget_max:
            raise ValueError("edit_budget_min must not exceed edit_budget_max")
        if self.reserved_exploration_slots > self.variants_per_round:
            raise ValueError(
                "reserved_exploration_slots must not exceed variants_per_round"
            )
        return self

    def windows(self, rounds: int) -> tuple[int, int]:
        stall = self.stall_window or max(2, round(0.15 * rounds))
        prune = self.prune_window or max(3, round(0.2 * rounds))
        return stall, prune

    def weights(self, delta: float) -> dict[str, float]:
        band = self.within_band
        w_s = (
            band.w_s
            if band.w_s is not None
            else band.band_edge_cost_allowance * band.w_c / delta
        )
        w_n = (
            band.w_n if band.w_n is not None else band.novelty_cost_allowance * band.w_c
        )
        return {
            "beta0": self.beta0,
            "beta1": self.beta1,
            "w_s": w_s,
            "w_c": band.w_c,
            "w_n": w_n,
        }
