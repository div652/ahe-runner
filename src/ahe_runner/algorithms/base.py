"""The evolution-algorithm plugin interface.

The runner owns everything an evolution algorithm does not decide: the
frozen baseline C0, evaluation and its verification, training evidence,
candidate workspaces, validation and freezing of checkpoints, cost
accounting, meta-agent execution, durable stages, and the closing lifecycle
(selection and the final test of C0 against the shipped checkpoint).

An algorithm decides how to search: which checkpoints to build from which
parents, which roles to run with which prompts, which candidates to keep,
and which checkpoint it would ship. It does all of this through
``EvolutionKernel`` (kernel.py). Every kernel call that does work is a
durable stage, and the algorithm decides only from stage outputs, so running
``evolve`` again after an interruption replays completed work and continues.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

if TYPE_CHECKING:
    from .kernel import EvolutionKernel

SelectionPolicy = Literal["validation", "algorithm"]


class AlgorithmIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    version: str
    description: str


class EvolutionOutcome(BaseModel):
    """What an algorithm hands to the closing lifecycle."""

    model_config = ConfigDict(extra="forbid")

    # The checkpoint the algorithm would ship (selection policy "algorithm").
    pick: str = Field(pattern=r"^C[0-9]+$")
    # Checkpoints eligible for validation selection (policy "validation").
    # The runner always adds C0.
    nominees: list[str] = Field(min_length=1)
    summary: str
    data: dict[str, Any] = Field(default_factory=dict)

    @field_validator("nominees")
    @classmethod
    def validate_nominees(cls, values: list[str]) -> list[str]:
        for value in values:
            if not value.startswith("C") or not value[1:].isdecimal():
                raise ValueError(f"invalid checkpoint label: {value!r}")
        if len(values) != len(set(values)):
            raise ValueError("nominees must be unique")
        return values


class EvolutionAlgorithm(ABC):
    """Base class for evolution algorithms."""

    #: Settings model for ``algorithm.settings``.
    settings_model: ClassVar[type[BaseModel]]
    #: Selection policy used when ``selection.policy`` is not set.
    default_selection_policy: ClassVar[SelectionPolicy] = "validation"
    #: Whether ``ahe extend-rounds`` may raise ``campaign.rounds`` in place.
    supports_round_extension: ClassVar[bool] = False
    #: Role names that accept ``algorithm.role_supplements``.
    roles: ClassVar[tuple[str, ...]] = ()

    def __init__(self, settings: BaseModel) -> None:
        self.settings = settings

    @abstractmethod
    def identity(self) -> AlgorithmIdentity:
        """Stable name and version; part of the campaign identity."""

    def resources(self) -> tuple[Path, ...]:
        """Files and folders that define the algorithm's behavior (prompts,
        tool descriptions, code). Their hashes enter the campaign identity."""

        return ()

    def check(self, kernel: EvolutionKernel) -> list[str]:
        """Problems that make this algorithm unusable for the campaign."""

        del kernel
        return []

    @abstractmethod
    def evolve(self, kernel: EvolutionKernel) -> EvolutionOutcome:
        """Run (or resume) the whole search and return its outcome."""
