"""Reusable stable identities and attempt bookkeeping for benchmark adapters."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import IntegrityError
from .io import atomic_write_json, read_json, sha256_file, sha256_json


@dataclass(frozen=True, slots=True)
class EvaluationUnit:
    unit_id: str
    task_id: str
    task_key: str
    trial: int
    seed: int
    artifact_dir: Path


class EvaluationStore:
    """Adapter helper that never overwrites prior rollout attempts."""

    def __init__(
        self,
        *,
        output_dir: Path,
        semantic_inputs: dict[str, Any],
        task_ids: list[str],
        trials: int,
        trial_seeds: list[int],
    ) -> None:
        if trials != len(trial_seeds):
            raise ValueError("trial seed count does not match trials")
        self.output_dir = output_dir.resolve()
        self.semantic_inputs = semantic_inputs
        self.run_id = sha256_json(semantic_inputs)[:24]
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.manifest_path = self.output_dir / "run_manifest.json"
        manifest = {
            "schema_version": 1,
            "run_id": self.run_id,
            "semantic_inputs": semantic_inputs,
            "semantic_inputs_sha256": sha256_json(semantic_inputs),
            "task_ids": task_ids,
            "trials": trials,
            "trial_seeds": trial_seeds,
        }
        if self.manifest_path.is_file():
            existing = read_json(self.manifest_path)
            if existing != manifest:
                raise IntegrityError(
                    "evaluation output directory belongs to different inputs"
                )
        else:
            atomic_write_json(self.manifest_path, manifest)
        self.units = [
            self._unit(task_id, trial, seed)
            for trial, seed in enumerate(trial_seeds)
            for task_id in task_ids
        ]

    def pending_units(self) -> list[EvaluationUnit]:
        return [unit for unit in self.units if not self.verify_complete(unit)]

    def next_attempt(self, unit: EvaluationUnit) -> int:
        if not unit.artifact_dir.is_dir():
            return 1
        attempts: list[int] = []
        for path in unit.artifact_dir.iterdir():
            if not path.is_dir() or not path.name.startswith("attempt-"):
                continue
            suffix = path.name.removeprefix("attempt-")
            if not suffix.isdecimal() or int(suffix) < 1:
                raise IntegrityError(f"malformed attempt directory: {path}")
            attempts.append(int(suffix))
        if not attempts:
            return 1
        ordered = sorted(attempts)
        if ordered != list(range(1, ordered[-1] + 1)):
            raise IntegrityError(
                f"attempt history is not contiguous: {unit.artifact_dir}"
            )
        return ordered[-1] + 1

    def begin_attempt(self, unit: EvaluationUnit) -> tuple[int, Path]:
        unit.artifact_dir.mkdir(parents=True, exist_ok=True)
        self._write_or_verify_unit(unit)
        attempt = self.next_attempt(unit)
        directory = unit.artifact_dir / f"attempt-{attempt}"
        directory.mkdir(parents=True, exist_ok=False)
        return attempt, directory

    def record_infrastructure_failure(
        self,
        unit: EvaluationUnit,
        *,
        attempt: int,
        error: dict[str, Any],
    ) -> None:
        path = unit.artifact_dir / f"attempt-{attempt}"
        if not path.is_dir():
            raise IntegrityError(f"attempt directory is missing: {path}")
        atomic_write_json(path / "infrastructure_error.json", error)

    def complete_unit(
        self,
        unit: EvaluationUnit,
        *,
        attempt: int,
        result_path: Path,
        trace_path: Path,
        metadata_path: Path | None = None,
    ) -> None:
        for path in (result_path, trace_path):
            if not path.is_file():
                raise IntegrityError(f"unit completion file is missing: {path}")
            try:
                path.resolve().relative_to(unit.artifact_dir.resolve())
            except ValueError as exc:
                raise IntegrityError(
                    f"unit artifact escapes its directory: {path}"
                ) from exc
        payload = {
            "attempt": attempt,
            "result_path": str(result_path.resolve()),
            "result_sha256": sha256_file(result_path),
            "trace_path": str(trace_path.resolve()),
            "trace_sha256": sha256_file(trace_path),
        }
        if metadata_path is not None:
            if not metadata_path.is_file():
                raise IntegrityError(f"metadata file is missing: {metadata_path}")
            payload["metadata_path"] = str(metadata_path.resolve())
            payload["metadata_sha256"] = sha256_file(metadata_path)
        completion = unit.artifact_dir / "complete.json"
        if completion.exists():
            if read_json(completion) != payload:
                raise IntegrityError(f"unit completion changed: {completion}")
            return
        atomic_write_json(completion, payload)

    def verify_complete(self, unit: EvaluationUnit) -> bool:
        completion = unit.artifact_dir / "complete.json"
        if not completion.is_file():
            return False
        payload = read_json(completion)
        for path_key, hash_key in (
            ("result_path", "result_sha256"),
            ("trace_path", "trace_sha256"),
            ("metadata_path", "metadata_sha256"),
        ):
            if path_key not in payload:
                continue
            path = Path(str(payload[path_key])).resolve()
            try:
                path.relative_to(unit.artifact_dir.resolve())
            except ValueError as exc:
                raise IntegrityError(
                    f"completed unit path escapes artifact root: {path}"
                ) from exc
            if not path.is_file() or sha256_file(path) != payload[hash_key]:
                raise IntegrityError(f"completed unit hash mismatch: {path}")
        return True

    def finalize(self, payload: dict[str, Any]) -> Path:
        incomplete = [unit.unit_id for unit in self.units if not self.verify_complete(unit)]
        if incomplete:
            raise IntegrityError(
                f"cannot finalize evaluation with incomplete units: {incomplete[:5]}"
            )
        path = self.output_dir / "evaluation_result.json"
        final = {
            **payload,
            "run_id": self.run_id,
            "unit_count": len(self.units),
        }
        if path.is_file() and read_json(path) != final:
            raise IntegrityError("evaluation result changed after finalization")
        atomic_write_json(path, final)
        failure_summary = self.output_dir / "infrastructure_failures.json"
        if failure_summary.exists():
            failure_summary.unlink()
        return path

    def _unit(self, task_id: str, trial: int, seed: int) -> EvaluationUnit:
        unit_id = uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"{self.run_id}:{task_id}:{trial}:{seed}",
        ).hex
        return EvaluationUnit(
            unit_id=unit_id,
            task_id=task_id,
            task_key=sha256_json(task_id)[:16],
            trial=trial,
            seed=seed,
            artifact_dir=self.output_dir / "units" / unit_id,
        )

    def _write_or_verify_unit(self, unit: EvaluationUnit) -> None:
        path = unit.artifact_dir / "unit.json"
        payload = {
            "unit_id": unit.unit_id,
            "run_id": self.run_id,
            "task_id": unit.task_id,
            "task_key": unit.task_key,
            "trial": unit.trial,
            "seed": unit.seed,
        }
        if path.is_file():
            if read_json(path) != payload:
                raise IntegrityError(f"unit identity mismatch: {path}")
            return
        atomic_write_json(path, payload)
