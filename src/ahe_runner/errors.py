"""Domain-specific errors used by the runner."""

from __future__ import annotations

from typing import Any


class AHERunnerError(RuntimeError):
    """Base error for operator-visible runner failures."""


class ConfigurationError(AHERunnerError):
    """Configuration or plugin identity is invalid."""


class IntegrityError(AHERunnerError):
    """A persisted artifact does not match its recorded identity."""


class InfrastructureError(AHERunnerError):
    """External infrastructure failed before producing a valid outcome."""


class CandidateValidationError(AHERunnerError):
    """An evolved harness failed deterministic validation."""

    def __init__(self, report: dict[str, Any]) -> None:
        self.report = report
        errors = report.get("errors") or ["candidate validation failed"]
        super().__init__("\n".join(str(error) for error in errors))


class StageInterruptedError(AHERunnerError):
    """A resumable stage attempt failed and was durably preserved."""

    def __init__(self, stage_name: str, attempt: int, message: str) -> None:
        self.stage_name = stage_name
        self.attempt = attempt
        super().__init__(message)


class StageBlockedError(AHERunnerError):
    """A stage cannot continue without operator intervention."""

    def __init__(self, stage_name: str, message: str) -> None:
        self.stage_name = stage_name
        super().__init__(message)
