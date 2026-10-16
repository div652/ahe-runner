"""Offline checks for the optional azure_entra provider and the Responses API."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import openai
import pytest

from ahe_runner.config import ModelSettings, TransportSettings
from ahe_runner.errors import ConfigurationError, InfrastructureError, IntegrityError
from ahe_runner.meta import model_client as client_module
from ahe_runner.meta.model_client import (
    AzureCliTokenProvider,
    RecordedExactModelClient,
    nexau_api_type,
    nexau_reasoning,
    openai_credential,
    transient_retry_delay,
)
from ahe_runner.meta.preflight import run_model_preflight

AZURE_URL = "https://models.example.invalid/openai/v1"
SCOPE = "api://example/.default"


def _azure(**update: Any) -> TransportSettings:
    return TransportSettings(
        provider="azure_entra",
        base_url=AZURE_URL,
        token_scope=SCOPE,
        api_mode="responses",
        model_catalog="disabled",
        negative_control="disabled",
        **update,
    )


def _settings(**update: Any) -> ModelSettings:
    values: dict[str, Any] = {
        "model": "exact-model",
        "max_tokens": 256,
        "reasoning_effort": "high",
        "temperature": None,
    }
    values.update(update)
    return ModelSettings(**values)


# --------------------------------------------------------------------------
# Configuration


def test_default_transport_serialization_is_unchanged() -> None:
    transport = TransportSettings(
        base_url="http://127.0.0.1:3141/v1",
        model_catalog="required",
        negative_control="required",
        hung_call_retries=2,
    )
    assert transport.model_dump(mode="json") == {
        "base_url": "http://127.0.0.1:3141/v1",
        "api_key_env": None,
        "api_mode": "chat_completions",
        "stream": False,
        "request_timeout_seconds": 300.0,
        "observable_model_policy": "exact",
        "model_catalog": "required",
        "negative_control": "required",
        "max_concurrent_requests": None,
        "hung_call_retries": 2,
    }
    assert TransportSettings.model_validate(transport.model_dump()) == transport


def test_non_default_provider_fields_are_serialized() -> None:
    dumped = _azure(transient_retries=5).model_dump(mode="json")
    assert dumped["provider"] == "azure_entra"
    assert dumped["token_scope"] == SCOPE
    assert dumped["api_mode"] == "responses"
    assert dumped["transient_retries"] == 5
    assert "transient_max_wait_seconds" not in dumped
    assert TransportSettings.model_validate(dumped) == _azure(transient_retries=5)


@pytest.mark.parametrize(
    ("values", "message"),
    [
        ({"provider": "azure_entra", "base_url": AZURE_URL}, "token_scope"),
        (
            {
                "provider": "azure_entra",
                "base_url": AZURE_URL,
                "token_scope": SCOPE,
                "api_key_env": "SOME_KEY",
            },
            "api_key_env",
        ),
        (
            {
                "provider": "azure_entra",
                "base_url": "http://models.example.invalid/v1",
                "token_scope": SCOPE,
            },
            "https",
        ),
        (
            {"base_url": "http://127.0.0.1:3141/v1", "token_scope": SCOPE},
            "token_scope",
        ),
        (
            {"base_url": "http://127.0.0.1:3141/v1", "transient_max_wait_seconds": 0},
            "transient_max_wait_seconds",
        ),
    ],
)
def test_provider_validation(values: dict[str, Any], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        TransportSettings.model_validate(values)


def test_nexau_wire_settings() -> None:
    copilot = TransportSettings(base_url="http://127.0.0.1:3141/v1")
    assert nexau_api_type(copilot) == "openai_chat_completion"
    assert nexau_reasoning(copilot, "high") == {"effort": "high"}
    assert nexau_api_type(_azure()) == "openai_responses"
    assert nexau_reasoning(_azure(), "high") == {"effort": "high", "summary": "auto"}
    assert nexau_reasoning(_azure(), None) is None


# --------------------------------------------------------------------------
# Azure CLI token


def _token_json(token: str, expires_on: float) -> str:
    return json.dumps({"accessToken": token, "expires_on": int(expires_on)})


def test_token_provider_caches_and_refreshes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = [1_000_000.0]
    commands: list[list[str]] = []

    def run(command: list[str], **_: Any) -> Any:
        commands.append(command)
        token = f"token-{len(commands)}"
        return SimpleNamespace(
            returncode=0, stdout=_token_json(token, now[0] + 3600), stderr=""
        )

    monkeypatch.setattr(client_module.subprocess, "run", run)
    monkeypatch.setattr(client_module.time, "time", lambda: now[0])
    provider = AzureCliTokenProvider(SCOPE)
    assert provider() == "token-1"
    assert provider() == "token-1"
    assert commands == [
        [
            "az",
            "account",
            "get-access-token",
            "--scope",
            SCOPE,
            "--output",
            "json",
        ]
    ]
    now[0] += 3600 - 60
    assert provider() == "token-2"


def test_token_provider_failures_are_classified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def failed(*_: Any, **__: Any) -> Any:
        return SimpleNamespace(returncode=1, stdout="", stderr="Please run az login")

    monkeypatch.setattr(client_module.subprocess, "run", failed)
    with pytest.raises(ConfigurationError, match="az login"):
        AzureCliTokenProvider(SCOPE)()

    def timeout(command: list[str], **_: Any) -> Any:
        raise subprocess.TimeoutExpired(command, 1)

    monkeypatch.setattr(client_module.subprocess, "run", timeout)
    with pytest.raises(InfrastructureError):
        AzureCliTokenProvider(SCOPE)()


def test_credential_is_a_token_provider_only_for_azure() -> None:
    copilot = TransportSettings(base_url="http://127.0.0.1:3141/v1")
    assert openai_credential(copilot, "placeholder") == "placeholder"
    credential = openai_credential(_azure(), "placeholder")
    assert callable(credential)
    assert credential is openai_credential(_azure(), "ignored")


# --------------------------------------------------------------------------
# Recorded Responses client


class _Raw:
    def __init__(self, response: Any, headers: dict[str, str]) -> None:
        self._response = response
        self.headers = httpx.Headers(headers)

    def parse(self) -> Any:
        return self._response


def _response(model: str = "exact-model", status: str = "completed") -> Any:
    return SimpleNamespace(
        model=model,
        status=status,
        error=None,
        output_text="OK",
        model_dump=lambda mode="json": {"model": model, "status": status},
    )


def _responses_client(
    tmp_path: Path, outcomes: list[Any], transport: TransportSettings | None = None
) -> tuple[RecordedExactModelClient, list[dict[str, Any]]]:
    sent: list[dict[str, Any]] = []

    def create(**kwargs: Any) -> Any:
        sent.append(json.loads(json.dumps(kwargs)))
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return _Raw(
            outcome,
            {
                "x-ratelimit-remaining-tokens": "999000",
                "x-request-id": "req-1",
                "set-cookie": "secret",
            },
        )

    client = RecordedExactModelClient(
        transport=transport or _azure(),
        api_key="unused",
        settings=_settings(),
        log_dir=tmp_path / "calls",
    )
    client._base = SimpleNamespace(  # type: ignore[attr-defined]
        responses=SimpleNamespace(with_raw_response=SimpleNamespace(create=create))
    )
    return client, sent


def test_responses_call_enforces_settings_and_records_headers(tmp_path: Path) -> None:
    client, sent = _responses_client(tmp_path, [_response()])
    response = client.responses.create(
        model="exact-model", input="hello", max_output_tokens=256
    )
    assert response.output_text == "OK"
    assert sent == [
        {
            "model": "exact-model",
            "input": "hello",
            "max_output_tokens": 256,
            "stream": False,
            "reasoning": {"effort": "high", "summary": "auto"},
        }
    ]
    call = tmp_path / "calls" / "call-0001"
    request = json.loads((call / "request.json").read_text("utf-8"))
    assert request["api"] == "responses"
    recorded = json.loads((call / "response.json").read_text("utf-8"))
    assert recorded["returned_model"] == "exact-model"
    assert recorded["response_headers"] == {
        "x-ratelimit-remaining-tokens": "999000",
        "x-request-id": "req-1",
    }


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"model": "other-model", "max_output_tokens": 256}, "model"),
        ({"model": "exact-model", "max_output_tokens": 64}, "max_output_tokens"),
        ({"model": "exact-model", "max_tokens": 256}, "max_tokens"),
        ({"model": "exact-model", "temperature": 0.5}, "temperature"),
        ({"model": "exact-model", "reasoning_effort": "high"}, "reasoning"),
        ({"model": "exact-model", "reasoning": {"effort": "low"}}, "reasoning"),
        ({"model": "exact-model", "stream": True}, "stream"),
    ],
)
def test_responses_overrides_are_rejected_before_any_request(
    tmp_path: Path, kwargs: dict[str, Any], message: str
) -> None:
    client, sent = _responses_client(tmp_path, [])
    with pytest.raises(IntegrityError, match=message):
        client.responses.create(input="hello", **kwargs)
    assert sent == []


def test_responses_proxy_exposes_only_create(tmp_path: Path) -> None:
    client, _ = _responses_client(tmp_path, [])
    with pytest.raises(IntegrityError):
        client.responses.retrieve  # noqa: B018


def test_responses_mismatch_and_failed_status_fail_closed(tmp_path: Path) -> None:
    client, _ = _responses_client(
        tmp_path, [_response(model="other-model"), _response(status="failed")]
    )
    with pytest.raises(IntegrityError, match="other-model"):
        client.responses.create(model="exact-model", input="hello")
    with pytest.raises(InfrastructureError, match="failed"):
        client.responses.create(model="exact-model", input="hello")
    assert (tmp_path / "calls" / "call-0002" / "error.json").is_file()


def _status_error(status: int, headers: dict[str, str]) -> Exception:
    request = httpx.Request("POST", f"{AZURE_URL}/responses")
    response = httpx.Response(status, headers=headers, request=request)
    error_class = openai.RateLimitError if status == 429 else openai.InternalServerError
    return error_class("busy", response=response, body=None)


def test_transient_errors_wait_and_are_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr(client_module.time, "sleep", sleeps.append)
    monkeypatch.setattr(client_module.random, "uniform", lambda *_: 0.0)
    client, sent = _responses_client(
        tmp_path,
        [
            _status_error(429, {"retry-after": "7"}),
            _status_error(503, {}),
            _response(),
        ],
        _azure(transient_retries=2),
    )
    client.responses.create(model="exact-model", input="hello")
    assert len(sent) == 3 and sent[0] == sent[1] == sent[2]
    assert sleeps == [7.0, 4.0]
    call = tmp_path / "calls" / "call-0001"
    recorded = json.loads((call / "response.json").read_text("utf-8"))
    assert [row["retry_delay_seconds"] for row in recorded["transient_attempts"]] == [
        7.0,
        4.0,
    ]
    assert (call / "transient-attempt-2.json").is_file()


def test_transient_retries_are_bounded_and_off_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(client_module.time, "sleep", lambda _: None)
    client, sent = _responses_client(
        tmp_path / "default", [_status_error(429, {}), _response()]
    )
    with pytest.raises(InfrastructureError):
        client.responses.create(model="exact-model", input="hello")
    assert len(sent) == 1

    client, sent = _responses_client(
        tmp_path / "bounded",
        [_status_error(429, {})] * 3,
        _azure(transient_retries=1),
    )
    with pytest.raises(InfrastructureError):
        client.responses.create(model="exact-model", input="hello")
    assert len(sent) == 2
    error = json.loads(
        (tmp_path / "bounded" / "calls" / "call-0001" / "error.json").read_text()
    )
    assert len(error["transient_attempts"]) == 1


def test_bad_requests_are_not_transient(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    request = httpx.Request("POST", f"{AZURE_URL}/responses")
    bad = openai.BadRequestError(
        "bad", response=httpx.Response(400, request=request), body=None
    )
    client, sent = _responses_client(
        tmp_path, [bad, _response()], _azure(transient_retries=3)
    )
    with pytest.raises(openai.BadRequestError):
        client.responses.create(model="exact-model", input="hello")
    assert len(sent) == 1


def test_retry_delay_honours_retry_after_and_caps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(client_module.random, "uniform", lambda *_: 0.0)
    assert transient_retry_delay(
        _status_error(429, {"retry-after-ms": "1500"}), attempt=1, max_wait=120
    ) == pytest.approx(1.5)
    assert transient_retry_delay(
        _status_error(429, {"retry-after": "60"}), attempt=1, max_wait=30
    ) == pytest.approx(30.0)
    assert transient_retry_delay(
        _status_error(503, {}), attempt=8, max_wait=120
    ) == pytest.approx(60.0)


# --------------------------------------------------------------------------
# Preflight


class _FakeResponsesOpenAI:
    def __init__(self, callback: Any) -> None:
        self.responses = SimpleNamespace(create=callback)


def test_preflight_responses_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[dict[str, Any]] = []
    built: list[dict[str, Any]] = []

    def create(**kwargs: Any) -> Any:
        seen.append(kwargs)
        return SimpleNamespace(model="exact-model", status="completed")

    def make_client(**kwargs: Any) -> Any:
        built.append(kwargs)
        return _FakeResponsesOpenAI(create)

    monkeypatch.setattr("ahe_runner.meta.preflight.OpenAI", make_client)
    report = run_model_preflight(
        transport=_azure(),
        api_key="unused",
        settings=_settings(),
        output_path=tmp_path / "preflight.json",
    )
    assert report["status"] == "completed"
    assert report["provider"] == "azure_entra"
    assert report["positive_probe"]["returned_model"] == "exact-model"
    assert len(seen) == 1
    probe = seen[0]
    assert probe["model"] == "exact-model"
    assert "input" in probe and "messages" not in probe
    assert probe["reasoning"] == {"effort": "high", "summary": "auto"}
    assert "temperature" not in probe
    assert callable(built[0]["api_key"])
    assert "api_key" not in json.dumps(report)
