"""Spawned evaluated-trial isolation and error transport."""

from __future__ import annotations

import importlib
import multiprocessing
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import httpx
import pytest
from openai import APIConnectionError

import ahe_runner.runtime as runtime_module
from ahe_runner.adapter import BenchmarkInterface
from ahe_runner.child_process import (
    run_evaluated_trial_in_child,
    run_in_child,
)
from ahe_runner.config import load_config
from ahe_runner.errors import (
    CandidateValidationError,
    ConfigurationError,
    InfrastructureError,
    IntegrityError,
)
from ahe_runner.harness import load_harness_manifest
from ahe_runner.io import read_json
from ahe_runner.model_requests import model_request_slot
from ahe_runner.runtime import AgentSessionRequest, EvaluatedAgentRuntime

from .conftest import make_example_project

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
HERE = __name__


def _double(value: int) -> int:
    return value * 2


def _integrity(_payload: Any) -> None:
    raise IntegrityError("tampered corpus")


def _unpicklable(_payload: Any) -> None:
    request = httpx.Request("POST", "http://127.0.0.1:9/v1/chat/completions")
    raise APIConnectionError(request=request)


def _candidate_failure(_payload: Any) -> None:
    raise CandidateValidationError({"errors": ["invalid evolved harness"]})


def _die(_payload: Any) -> None:
    os._exit(3)


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


def _runtime_probe(
    value: int,
    runtime: EvaluatedAgentRuntime,
) -> dict[str, Any]:
    session_dir = runtime._artifact_root / "child-probe"
    session = runtime.open_session(
        AgentSessionRequest(
            session_id="child-probe",
            artifact_dir=session_dir,
        )
    )
    session.close()
    return {
        "value": value * 2,
        "pid": os.getpid(),
        "preflight_path": str(runtime._ensure_model_preflight()),
        "project_root": str(runtime._project_root),
        "runtime_manifest": read_json(session_dir / "runtime_manifest.json"),
    }


def _runtime_slot_probe(
    _payload: Any,
    runtime: EvaluatedAgentRuntime,
    ready: Any,
) -> dict[str, float]:
    ready.set()
    with model_request_slot(runtime._transport) as timing:
        return timing


def test_child_returns_target_result_and_preserves_errors() -> None:
    assert run_in_child(f"{HERE}:_double", 21, import_paths=(REPOSITORY_ROOT,)) == 42

    with pytest.raises(IntegrityError, match="tampered corpus") as caught:
        run_in_child(f"{HERE}:_integrity", None, import_paths=(REPOSITORY_ROOT,))
    assert any("_integrity" in note for note in caught.value.__notes__)

    with pytest.raises(RuntimeError, match="APIConnectionError: Connection error"):
        run_in_child(f"{HERE}:_unpicklable", None, import_paths=(REPOSITORY_ROOT,))

    with pytest.raises(CandidateValidationError, match="invalid evolved harness"):
        run_in_child(
            f"{HERE}:_candidate_failure",
            None,
            import_paths=(REPOSITORY_ROOT,),
        )


def test_child_exit_and_timeout_are_infrastructure_failures() -> None:
    with pytest.raises(InfrastructureError, match="exited with code 3"):
        run_in_child(f"{HERE}:_die", None, import_paths=(REPOSITORY_ROOT,))

    with pytest.raises(InfrastructureError, match=r"exceeded 0\.1 seconds"):
        run_in_child(
            f"{HERE}:_sleep",
            5.0,
            import_paths=(REPOSITORY_ROOT,),
            timeout_seconds=0.1,
        )


def test_child_validates_target_and_import_paths(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match=r"module\.path:function_name"):
        run_in_child("invalid", None)
    with pytest.raises(ConfigurationError, match="not a directory"):
        run_in_child(f"{HERE}:_double", 1, import_paths=(tmp_path / "missing",))


def test_payload_types_load_from_child_import_paths(tmp_path: Path) -> None:
    (tmp_path / "ahe_child_probe.py").write_text(
        "from dataclasses import dataclass\n\n"
        "@dataclass(frozen=True)\n"
        "class Box:\n    value: int\n\n"
        "def unwrap(box):\n    return box.value + 1\n",
        encoding="utf-8",
    )
    sys.path.insert(0, str(tmp_path))
    try:
        probe = importlib.import_module("ahe_child_probe")
    finally:
        sys.path.remove(str(tmp_path))
    try:
        assert (
            run_in_child(
                "ahe_child_probe:unwrap",
                probe.Box(4),
                import_paths=(tmp_path,),
            )
            == 5
        )
    finally:
        sys.modules.pop("ahe_child_probe", None)


def test_evaluated_child_reuses_preflight_project_root_and_request_slots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_config(make_example_project(tmp_path))
    transport = config.document.transport.model_copy(
        update={
            "base_url": f"http://127.0.0.1:{39000 + os.getpid() % 1000}/v1",
            "max_concurrent_requests": 1,
        }
    )
    preflight_calls: list[Path] = []

    def fake_preflight(*, output_path: Path, **_kwargs: Any) -> dict[str, Any]:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text('{"status":"completed"}\n', encoding="utf-8")
        preflight_calls.append(output_path)
        return {"status": "completed"}

    monkeypatch.setattr(runtime_module, "run_model_preflight", fake_preflight)
    runtime = EvaluatedAgentRuntime(
        harness_dir=config.harness_dir,
        manifest=load_harness_manifest(config.harness_manifest_path),
        benchmark_interface=BenchmarkInterface(description="Child process fixture."),
        model=config.document.models.evaluated,
        transport=transport,
        api_key=config.api_key,
        artifact_root=tmp_path / "runtime-artifacts",
        project_root=config.project_root,
    )
    try:
        spec = runtime.process_spec()
        assert len(preflight_calls) == 1
        assert "api_key=" not in repr(spec)
        assert "api_key" not in spec.model_dump(mode="json")

        result = run_evaluated_trial_in_child(
            f"{HERE}:_runtime_probe",
            21,
            runtime=runtime,
            import_paths=(REPOSITORY_ROOT,),
        )
        assert result["value"] == 42
        assert result["pid"] != os.getpid()
        assert result["preflight_path"] == str(spec.model_preflight_path)
        assert result["project_root"] == str(config.project_root)
        assert result["runtime_manifest"]["execution_process"] == {
            "mode": "spawned_child",
            "pid": result["pid"],
            "parent_runtime_pid": os.getpid(),
        }
        assert len(preflight_calls) == 1

        ready = multiprocessing.get_context("spawn").Event()
        with ThreadPoolExecutor(max_workers=1) as pool:
            with model_request_slot(transport):
                future = pool.submit(
                    run_evaluated_trial_in_child,
                    f"{HERE}:_runtime_slot_probe",
                    None,
                    runtime=runtime,
                    import_paths=(REPOSITORY_ROOT,),
                    inherited=(ready,),
                )
                assert ready.wait(timeout=10)
                time.sleep(0.2)
                assert not future.done()
            timing = future.result(timeout=10)
        assert timing["slot_wait_seconds"] >= 0.1
    finally:
        runtime.close()
