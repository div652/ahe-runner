"""Trial processes (AHE-025): spawning, error transport and shared slots."""

from __future__ import annotations

import importlib
import os
import sys
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
from openai import APIConnectionError

from ahe_runner.child_process import run_in_child
from ahe_runner.config import TransportSettings
from ahe_runner.errors import ConfigurationError, InfrastructureError, IntegrityError
from ahe_runner.meta import model_client
from ahe_runner.meta.model_client import (
    adopt_shared_request_slots,
    shared_request_slots,
)

TESTS_ROOT = Path(__file__).resolve().parents[1]
HERE = __name__


def _double(value: int) -> int:
    return value * 2


def _integrity(_payload: Any) -> None:
    raise IntegrityError("tampered corpus")


def _unpicklable(_payload: Any) -> None:
    request = httpx.Request("POST", "http://127.0.0.1:9/v1/chat/completions")
    raise APIConnectionError(request=request)


def _die(_payload: Any) -> None:
    os._exit(3)


def _try_slot(transport: TransportSettings, slots: Any) -> bool:
    adopt_shared_request_slots(transport, slots)
    return bool(model_client._shared_request_slots(transport).acquire(timeout=0.5))


def test_child_returns_the_target_result() -> None:
    assert run_in_child(f"{HERE}:_double", 21, import_paths=(TESTS_ROOT,)) == 42


def test_child_exceptions_keep_their_type_and_traceback() -> None:
    with pytest.raises(IntegrityError, match="tampered corpus") as caught:
        run_in_child(f"{HERE}:_integrity", None, import_paths=(TESTS_ROOT,))
    assert any("_integrity" in note for note in caught.value.__notes__)


def test_unpicklable_child_exceptions_are_named() -> None:
    with pytest.raises(RuntimeError, match="APIConnectionError: Connection error"):
        run_in_child(f"{HERE}:_unpicklable", None, import_paths=(TESTS_ROOT,))


def test_a_child_that_dies_is_an_infrastructure_error() -> None:
    with pytest.raises(InfrastructureError, match="exited with code 3"):
        run_in_child(f"{HERE}:_die", None, import_paths=(TESTS_ROOT,))


def test_payload_types_come_from_the_child_import_paths(tmp_path: Path) -> None:
    (tmp_path / "ahe_child_probe.py").write_text(
        "from dataclasses import dataclass\n\n"
        "@dataclass(frozen=True)\n"
        "class Box:\n    value: int\n\n"
        "def unwrap(box):\n    return box.value + 1\n",
        "utf-8",
    )
    # Import the way adapters are loaded: the path is on sys.path only briefly.
    sys.path.insert(0, str(tmp_path))
    try:
        probe = importlib.import_module("ahe_child_probe")
    finally:
        sys.path.remove(str(tmp_path))
    try:
        assert (
            run_in_child(
                "ahe_child_probe:unwrap", probe.Box(4), import_paths=(tmp_path,)
            )
            == 5
        )
    finally:
        sys.modules.pop("ahe_child_probe", None)


def test_request_slots_are_shared_with_child_processes() -> None:
    transport = TransportSettings(
        base_url=f"http://127.0.0.1:39{os.getpid() % 1000:03d}/v1",
        max_concurrent_requests=1,
    )
    slots = shared_request_slots(transport)
    assert slots is shared_request_slots(transport)
    assert slots.acquire(timeout=1)
    try:
        started = time.monotonic()
        held = run_in_child(
            f"{HERE}:_try_slot",
            transport,
            import_paths=(TESTS_ROOT,),
            inherited=(slots,),
        )
        assert held is False
        assert time.monotonic() - started >= 0.5
    finally:
        slots.release()
    assert (
        run_in_child(
            f"{HERE}:_try_slot",
            transport,
            import_paths=(TESTS_ROOT,),
            inherited=(slots,),
        )
        is True
    )


def test_adopting_slots_requires_a_matching_transport() -> None:
    capped = TransportSettings(
        base_url="http://127.0.0.1:39998/v1", max_concurrent_requests=2
    )
    uncapped = TransportSettings(base_url="http://127.0.0.1:39998/v1")
    with pytest.raises(ConfigurationError, match="exactly when"):
        adopt_shared_request_slots(capped, None)
    with pytest.raises(ConfigurationError, match="exactly when"):
        adopt_shared_request_slots(uncapped, object())
    adopt_shared_request_slots(uncapped, None)
    shared_request_slots(capped)
    with pytest.raises(ConfigurationError, match="already exist"):
        adopt_shared_request_slots(capped, object())
