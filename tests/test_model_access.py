from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from ahe_runner.config import ModelSettings, TransportSettings
from ahe_runner.errors import InfrastructureError, IntegrityError
from ahe_runner.meta.model_client import RecordedExactModelClient
from ahe_runner.meta.preflight import run_model_preflight


class _FakeCompletions:
    def __init__(self, callback):  # type: ignore[no-untyped-def]
        self.callback = callback

    def create(self, **kwargs):  # type: ignore[no-untyped-def]
        return self.callback(kwargs)


class _FakeOpenAI:
    def __init__(self, callback, models: list[str] | None = None):  # type: ignore[no-untyped-def]
        self.chat = SimpleNamespace(completions=_FakeCompletions(callback))
        self.models = SimpleNamespace(
            list=lambda: SimpleNamespace(
                data=[SimpleNamespace(id=value) for value in (models or [])]
            )
        )


def _settings() -> ModelSettings:
    return ModelSettings(
        model="exact-model",
        max_tokens=128,
        reasoning_effort="high",
        temperature=0.1,
    )


def _transport() -> TransportSettings:
    return TransportSettings(
        base_url="http://127.0.0.1:3141/v1",
        request_timeout_seconds=5,
        model_catalog="required",
        negative_control="required",
    )


def test_recorded_client_enforces_model_reasoning_and_records_calls(
    tmp_path: Path,
) -> None:
    seen: dict[str, Any] = {}

    def callback(kwargs):  # type: ignore[no-untyped-def]
        seen.update(kwargs)
        return SimpleNamespace(
            model="exact-model",
            choices=[SimpleNamespace(message=SimpleNamespace(content="OK"))],
            model_dump=lambda mode="json": {"model": "exact-model"},
        )

    client = RecordedExactModelClient(
        transport=_transport(),
        api_key="placeholder",
        settings=_settings(),
        log_dir=tmp_path / "calls",
    )
    client._base = _FakeOpenAI(callback)  # type: ignore[attr-defined]
    response = client.chat.completions.create(
        model="exact-model",
        messages=[{"role": "user", "content": "hello"}],
        max_tokens=32,
    )
    assert response.model == "exact-model"
    assert seen["stream"] is False
    assert seen["extra_body"]["reasoning"] == {"effort": "high"}
    assert (tmp_path / "calls" / "call-0001" / "request.json").is_file()
    assert (tmp_path / "calls" / "call-0001" / "response.json").is_file()


def test_recorded_client_fails_closed_on_mismatch_and_timeout(
    tmp_path: Path,
) -> None:
    mismatch = RecordedExactModelClient(
        transport=_transport(),
        api_key="placeholder",
        settings=_settings(),
        log_dir=tmp_path / "mismatch",
    )
    mismatch._base = _FakeOpenAI(  # type: ignore[attr-defined]
        lambda _kwargs: SimpleNamespace(
            model="different-model",
            choices=[SimpleNamespace(message=SimpleNamespace(content="wrong"))],
            model_dump=lambda mode="json": {"model": "different-model"},
        )
    )
    with pytest.raises(IntegrityError, match="response returned"):
        mismatch.chat.completions.create(
            model="exact-model",
            messages=[{"role": "user", "content": "hello"}],
        )

    timeout = RecordedExactModelClient(
        transport=_transport(),
        api_key="placeholder",
        settings=_settings(),
        log_dir=tmp_path / "timeout",
    )

    def timeout_callback(_kwargs):  # type: ignore[no-untyped-def]
        raise TimeoutError("upstream request timed out")

    timeout._base = _FakeOpenAI(timeout_callback)  # type: ignore[attr-defined]
    with pytest.raises(InfrastructureError, match="timed out"):
        timeout.chat.completions.create(
            model="exact-model",
            messages=[{"role": "user", "content": "hello"}],
        )
    assert (tmp_path / "timeout" / "call-0001" / "error.json").is_file()


def test_preflight_positive_and_negative_controls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def callback(kwargs):  # type: ignore[no-untyped-def]
        if kwargs["model"] == "exact-model":
            return SimpleNamespace(model="exact-model")
        raise RuntimeError(f"unknown model {kwargs['model']}")

    monkeypatch.setattr(
        "ahe_runner.meta.preflight.OpenAI",
        lambda **_kwargs: _FakeOpenAI(callback, ["exact-model"]),
    )
    output = tmp_path / "preflight.json"
    report = run_model_preflight(
        transport=_transport(),
        api_key="placeholder",
        settings=_settings(),
        output_path=output,
    )
    assert report["status"] == "completed"
    assert report["positive_probe"]["returned_model"] == "exact-model"
    assert report["negative_control"]["passed"] is True
    assert output.is_file()
