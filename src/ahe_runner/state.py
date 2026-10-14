"""Hash-verified, attempt-preserving campaign stages."""

from __future__ import annotations

import traceback
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .errors import (
    ConfigurationError,
    InfrastructureError,
    IntegrityError,
    StageBlockedError,
    StageInterruptedError,
)
from .io import (
    atomic_write_json,
    path_fingerprint,
    read_json,
    sha256_json,
    verify_path_fingerprint,
)


@dataclass(frozen=True, slots=True)
class StageContext:
    name: str
    stage_id: str
    stage_dir: Path
    attempt: int
    attempt_dir: Path
    previous_attempt_dir: Path | None


@dataclass(frozen=True, slots=True)
class StageResult:
    outputs: tuple[Path, ...]
    data: dict[str, Any]


class StageStore:
    def __init__(
        self,
        *,
        campaign_dir: Path,
        maximum_attempts: int,
    ) -> None:
        self.campaign_dir = campaign_dir.resolve()
        self.maximum_attempts = maximum_attempts
        self.root = self.campaign_dir / "stages"

    def run(
        self,
        *,
        name: str,
        semantic_inputs: Mapping[str, Any],
        action: Callable[[StageContext], StageResult],
        retryable: bool = True,
    ) -> StageResult:
        stage_dir = self.root / _stage_directory_name(name)
        stage_path = stage_dir / "stage.json"
        input_payload = dict(semantic_inputs)
        input_sha256 = sha256_json(input_payload)
        stage_id = sha256_json({"name": name, "semantic_inputs_sha256": input_sha256})[
            :24
        ]
        stage = self._load_or_create(
            stage_path=stage_path,
            name=name,
            stage_id=stage_id,
            semantic_inputs=input_payload,
            semantic_inputs_sha256=input_sha256,
        )
        if stage["status"] == "completed":
            for fingerprint in stage["outputs"]:
                verify_path_fingerprint(
                    fingerprint,
                    relative_to=self.campaign_dir,
                )
            return StageResult(
                outputs=tuple(
                    self.campaign_dir / str(row["path"]) for row in stage["outputs"]
                ),
                data=dict(stage.get("data") or {}),
            )
        if stage["status"] == "blocked":
            raise StageBlockedError(
                name,
                str(stage.get("last_error") or "stage is blocked"),
            )

        attempts = list(stage.get("attempts") or [])
        if attempts and attempts[-1].get("status") == "running":
            stale = dict(attempts[-1])
            stale["status"] = "interrupted"
            stale["completed_at"] = datetime.now(UTC).isoformat()
            stale["error"] = {
                "type": "AbandonedStageAttempt",
                "message": (
                    "The prior process ended before recording stage completion."
                ),
            }
            attempts[-1] = stale
            stage["attempts"] = attempts
            stage["status"] = "interrupted"
            atomic_write_json(stage_path, stage)

        attempt = len(attempts) + 1
        if attempt > self.maximum_attempts:
            stage["status"] = "blocked"
            stage["last_error"] = f"stage exhausted {self.maximum_attempts} attempts"
            atomic_write_json(stage_path, stage)
            raise StageBlockedError(name, stage["last_error"])

        attempt_dir = stage_dir / "attempts" / f"{attempt:04d}"
        attempt_dir.mkdir(parents=True, exist_ok=False)
        previous_attempt_dir = (
            stage_dir / "attempts" / f"{attempt - 1:04d}" if attempt > 1 else None
        )
        attempt_record = {
            "attempt": attempt,
            "status": "running",
            "started_at": datetime.now(UTC).isoformat(),
            "attempt_dir": str(attempt_dir.relative_to(self.campaign_dir)),
        }
        attempts.append(attempt_record)
        stage["attempts"] = attempts
        stage["status"] = "running"
        stage["updated_at"] = datetime.now(UTC).isoformat()
        atomic_write_json(stage_path, stage)
        atomic_write_json(attempt_dir / "attempt.json", attempt_record)

        context = StageContext(
            name=name,
            stage_id=stage_id,
            stage_dir=stage_dir,
            attempt=attempt,
            attempt_dir=attempt_dir,
            previous_attempt_dir=previous_attempt_dir,
        )
        try:
            result = action(context)
            outputs = [
                path_fingerprint(path, relative_to=self.campaign_dir)
                for path in result.outputs
            ]
        except Exception as exc:
            error = {
                "type": type(exc).__name__,
                "message": str(exc),
                "traceback": traceback.format_exc(),
            }
            atomic_write_json(attempt_dir / "error.json", error)
            attempts = list(stage["attempts"])
            attempts[-1] = {
                **attempts[-1],
                "status": "infrastructure_failed"
                if isinstance(exc, InfrastructureError)
                else "failed",
                "completed_at": datetime.now(UTC).isoformat(),
                "error": error,
            }
            stage["attempts"] = attempts
            stage["last_error"] = error
            can_retry = (
                retryable
                and isinstance(exc, InfrastructureError)
                and attempt < self.maximum_attempts
            )
            stage["status"] = "interrupted" if can_retry else "blocked"
            stage["updated_at"] = datetime.now(UTC).isoformat()
            atomic_write_json(stage_path, stage)
            atomic_write_json(attempt_dir / "attempt.json", attempts[-1])
            if can_retry:
                raise StageInterruptedError(
                    name,
                    attempt,
                    (
                        f"stage {name} attempt {attempt} failed due to "
                        f"infrastructure: {exc}. Run resume to continue."
                    ),
                ) from exc
            raise StageBlockedError(name, str(exc)) from exc

        completed_attempt = {
            **stage["attempts"][-1],
            "status": "completed",
            "completed_at": datetime.now(UTC).isoformat(),
            "outputs": outputs,
            "data": result.data,
        }
        stage["attempts"][-1] = completed_attempt
        stage["status"] = "completed"
        stage["completed_at"] = datetime.now(UTC).isoformat()
        stage["updated_at"] = stage["completed_at"]
        stage["outputs"] = outputs
        stage["data"] = result.data
        atomic_write_json(attempt_dir / "attempt.json", completed_attempt)
        atomic_write_json(stage_path, stage)
        return result

    def retry_blocked(self, *, name: str, reason: str) -> dict[str, Any]:
        """Authorize one more attempt of a blocked stage without erasing history."""

        normalized_reason = reason.strip()
        if not normalized_reason:
            raise ConfigurationError("retry reason must be non-empty")
        stage_path = self.root / _stage_directory_name(name) / "stage.json"
        if not stage_path.is_file():
            raise ConfigurationError(f"campaign stage does not exist: {name}")
        stage = read_json(stage_path)
        if stage.get("name") != name:
            raise IntegrityError(f"stage name does not match its path: {name}")
        if stage.get("status") != "blocked":
            raise ConfigurationError(
                f"stage {name!r} is {stage.get('status')!r}, not blocked"
            )
        attempts = list(stage.get("attempts") or [])
        if len(attempts) >= self.maximum_attempts:
            raise StageBlockedError(
                name,
                f"stage exhausted {self.maximum_attempts} attempts",
            )
        requested_at = datetime.now(UTC).isoformat()
        event = {
            "requested_at": requested_at,
            "reason": normalized_reason,
            "prior_error": stage.get("last_error"),
            "completed_attempts": len(attempts),
            "next_attempt": len(attempts) + 1,
            "attempts_remaining_after_retry": (
                self.maximum_attempts - len(attempts) - 1
            ),
        }
        retries = list(stage.get("manual_retries") or [])
        retries.append(event)
        stage["manual_retries"] = retries
        stage["status"] = "interrupted"
        stage["updated_at"] = requested_at
        atomic_write_json(stage_path, stage)
        return {
            "stage": name,
            "status": stage["status"],
            "retry": event,
        }

    def status(self) -> list[dict[str, Any]]:
        if not self.root.is_dir():
            return []
        rows: list[dict[str, Any]] = []
        for path in sorted(self.root.glob("*/stage.json")):
            rows.append(read_json(path))
        return rows

    def _load_or_create(
        self,
        *,
        stage_path: Path,
        name: str,
        stage_id: str,
        semantic_inputs: dict[str, Any],
        semantic_inputs_sha256: str,
    ) -> dict[str, Any]:
        if stage_path.is_file():
            stage = read_json(stage_path)
            expected = {
                "name": name,
                "stage_id": stage_id,
                "semantic_inputs": semantic_inputs,
                "semantic_inputs_sha256": semantic_inputs_sha256,
            }
            actual = {key: stage.get(key) for key in expected}
            if actual != expected:
                raise IntegrityError(
                    f"stage semantic inputs changed after creation: {name}"
                )
            return stage
        stage_path.parent.mkdir(parents=True, exist_ok=True)
        stage = {
            "schema_version": 1,
            "name": name,
            "stage_id": stage_id,
            "semantic_inputs": semantic_inputs,
            "semantic_inputs_sha256": semantic_inputs_sha256,
            "status": "pending",
            "created_at": datetime.now(UTC).isoformat(),
            "updated_at": datetime.now(UTC).isoformat(),
            "attempts": [],
            "outputs": [],
            "data": {},
        }
        atomic_write_json(stage_path, stage)
        return stage


def _stage_directory_name(name: str) -> str:
    normalized = "".join(
        character if character.isalnum() or character in "._-" else "-"
        for character in name
    )
    if not normalized:
        raise ValueError("stage name is empty")
    return normalized
