from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from openai import OpenAI

from ahe_runner.config import MetaSettings, ModelSettings, TransportSettings
from ahe_runner.errors import ConfigurationError, InfrastructureError, IntegrityError
from ahe_runner.meta.model_client import (
    RecordedExactModelClient,
    is_infrastructure_exception,
    summarize_model_call_retries,
)
from ahe_runner.meta.preflight import run_model_preflight
from ahe_runner.model_requests import (
    context_overflow_message,
    is_context_overflow_exception,
    model_request_slot,
)


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


def test_meta_context_accounting_validates_unit_and_provider_limit() -> None:
    settings = MetaSettings(
        max_context_tokens=100,
        token_counter=" benchmark_tokens:count ",
        context_counting_unit=" provider tokens ",
        provider_max_input_tokens=120,
    )
    assert settings.token_counter == "benchmark_tokens:count"
    assert settings.context_counting_unit == "provider tokens"
    with pytest.raises(ValueError, match="provider_max_input_tokens"):
        MetaSettings(
            max_context_tokens=121,
            context_counting_unit="provider tokens",
            provider_max_input_tokens=120,
        )
    with pytest.raises(ValueError, match=r"module\.path:callable_name"):
        MetaSettings(token_counter="missing_separator")


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
        max_tokens=128,
        temperature=0.1,
    )
    assert response.model == "exact-model"
    assert seen["stream"] is False
    assert seen["max_tokens"] == 128
    assert seen["temperature"] == 0.1
    assert seen["extra_body"]["reasoning"] == {"effort": "high"}
    call = tmp_path / "calls" / "call-0001"
    request_record = json.loads((call / "request.json").read_text(encoding="utf-8"))
    response_record = json.loads((call / "response.json").read_text(encoding="utf-8"))
    assert request_record["prompt_accounting"] == response_record["prompt_accounting"]
    assert request_record["prompt_accounting"]["message_count"] == 1
    assert request_record["prompt_accounting"]["message_string_characters"] == 9
    assert request_record["prompt_accounting"]["request_utf8_bytes"] > 9
    assert response_record["attempt_count"] == 1

    with pytest.raises(IntegrityError, match="max_tokens"):
        client.chat.completions.create(
            model="exact-model",
            messages=[{"role": "user", "content": "hello"}],
            max_tokens=104_096,
            temperature=0.1,
        )
    with pytest.raises(IntegrityError, match="temperature"):
        client.chat.completions.create(
            model="exact-model",
            messages=[{"role": "user", "content": "hello"}],
            max_tokens=128,
            temperature=1.9,
        )


def test_recorded_client_clears_provider_default_reasoning(
    tmp_path: Path,
) -> None:
    settings = ModelSettings(
        model="exact-model",
        max_tokens=128,
        reasoning_effort=None,
        temperature=0.1,
    )
    client = RecordedExactModelClient(
        transport=_transport(),
        api_key="placeholder",
        settings=settings,
        log_dir=tmp_path / "calls",
    )
    client._base = _FakeOpenAI(  # type: ignore[attr-defined]
        lambda _kwargs: SimpleNamespace(
            model="exact-model",
            choices=[SimpleNamespace(message=SimpleNamespace(content="OK"))],
            model_dump=lambda mode="json": {"model": "exact-model"},
        )
    )
    with pytest.raises(IntegrityError, match="provider-default"):
        client.chat.completions.create(
            model="exact-model",
            messages=[{"role": "user", "content": "hello"}],
            max_tokens=128,
            temperature=0.1,
            reasoning={"effort": "high"},
        )


@pytest.mark.parametrize(
    ("override", "expected_error"),
    [
        ({"extra_body": {"model": "other-model"}}, "protected model fields"),
        ({"extra_body": {"max_tokens": 999}}, "protected model fields"),
        ({"extra_body": {"temperature": 1.9}}, "protected model fields"),
        ({"extra_body": {"stream": True}}, "protected model fields"),
        ({"extra_body": {"reasoning": {"effort": "low"}}}, "protected model fields"),
        ({"reasoning_effort": "low"}, "reasoning_effort"),
        ({"max_completion_tokens": 999}, "protected alias"),
    ],
)
def test_recorded_client_rejects_wire_policy_overrides_before_request(
    tmp_path: Path,
    override: dict[str, Any],
    expected_error: str,
) -> None:
    requests: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(500)

    client = RecordedExactModelClient(
        transport=_transport(),
        api_key="placeholder",
        settings=_settings(),
        log_dir=tmp_path / "calls",
    )
    client._base.close()  # type: ignore[attr-defined]
    client._base = OpenAI(  # type: ignore[attr-defined]
        base_url=_transport().base_url,
        api_key="placeholder",
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    try:
        with pytest.raises(IntegrityError, match=expected_error):
            client.chat.completions.create(
                model="exact-model",
                messages=[{"role": "user", "content": "hello"}],
                **override,
            )
    finally:
        client.close()
    assert requests == []
    assert not (tmp_path / "calls" / "call-0001").exists()


def test_recorded_client_serializes_only_fixed_policy_and_allowed_extras(
    tmp_path: Path,
) -> None:
    requests: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        return httpx.Response(
            200,
            json={
                "id": "fixed-policy",
                "object": "chat.completion",
                "created": 1,
                "model": "exact-model",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "OK"},
                    }
                ],
            },
        )

    client = RecordedExactModelClient(
        transport=_transport(),
        api_key="placeholder",
        settings=_settings(),
        log_dir=tmp_path / "calls",
    )
    client._base.close()  # type: ignore[attr-defined]
    client._base = OpenAI(  # type: ignore[attr-defined]
        base_url=_transport().base_url,
        api_key="placeholder",
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    try:
        client.chat.completions.create(
            model="exact-model",
            messages=[{"role": "user", "content": "hello"}],
            reasoning_effort="high",
            extra_body={"provider_extension": "allowed"},
        )
    finally:
        client.close()
    assert len(requests) == 1
    assert requests[0]["model"] == "exact-model"
    assert requests[0]["max_tokens"] == 128
    assert requests[0]["temperature"] == 0.1
    assert requests[0]["stream"] is False
    assert requests[0]["reasoning"] == {"effort": "high"}
    assert requests[0]["provider_extension"] == "allowed"
    assert "reasoning_effort" not in requests[0]


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


def _hung_client(
    tmp_path: Path, retries: int, outcomes: list[Any]
) -> tuple[RecordedExactModelClient, list[dict[str, Any]]]:
    sent: list[dict[str, Any]] = []

    def callback(kwargs):  # type: ignore[no-untyped-def]
        sent.append(json.loads(json.dumps(kwargs)))
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return SimpleNamespace(
            model="exact-model",
            choices=[SimpleNamespace(message=SimpleNamespace(content=outcome))],
            model_dump=lambda mode="json": {"model": "exact-model"},
        )

    client = RecordedExactModelClient(
        transport=_transport().model_copy(update={"hung_call_retries": retries}),
        api_key="placeholder",
        settings=_settings(),
        log_dir=tmp_path / "calls",
    )
    client._base = _FakeOpenAI(callback)  # type: ignore[attr-defined]
    return client, sent


_SERVER_HANG = RuntimeError(
    "Error code: 500 - {'error': {'message': 'Model request timed out after "
    "300000 ms', 'type': 'server_error'}}"
)


def test_hung_call_is_resent_and_every_attempt_is_recorded(tmp_path: Path) -> None:
    client, sent = _hung_client(
        tmp_path, 2, [_SERVER_HANG, TimeoutError("read timed out"), "OK"]
    )
    response = client.chat.completions.create(
        model="exact-model", messages=[{"role": "user", "content": "hello"}]
    )
    assert response.choices[0].message.content == "OK"
    assert len(sent) == 3 and sent[0] == sent[1] == sent[2]
    call = tmp_path / "calls" / "call-0001"
    assert sorted(path.name for path in call.iterdir()) == [
        "hung-attempt-1.json",
        "hung-attempt-2.json",
        "request.json",
        "response.json",
    ]
    recorded = json.loads((call / "response.json").read_text(encoding="utf-8"))
    assert recorded["attempt_count"] == 3
    assert [row["message"] for row in recorded["hung_attempts"]] == [
        str(_SERVER_HANG),
        "read timed out",
    ]
    assert all(row["will_retry"] is True for row in recorded["hung_attempts"])


def test_hung_call_retries_are_bounded_and_off_by_default(tmp_path: Path) -> None:
    client, sent = _hung_client(tmp_path / "bounded", 1, [_SERVER_HANG] * 3)
    with pytest.raises(InfrastructureError, match="timed out"):
        client.chat.completions.create(
            model="exact-model", messages=[{"role": "user", "content": "hello"}]
        )
    assert len(sent) == 2
    call = tmp_path / "bounded" / "calls" / "call-0001"
    error = json.loads((call / "error.json").read_text(encoding="utf-8"))
    assert error["attempt"] == 2
    assert len(error["hung_attempts"]) == 1
    assert (call / "hung-attempt-1.json").is_file()

    client, sent = _hung_client(tmp_path / "default", 0, [_SERVER_HANG, "OK"])
    with pytest.raises(InfrastructureError):
        client.chat.completions.create(
            model="exact-model", messages=[{"role": "user", "content": "hello"}]
        )
    assert len(sent) == 1
    error = json.loads(
        (tmp_path / "default" / "calls" / "call-0001" / "error.json").read_text(
            encoding="utf-8"
        )
    )
    assert "hung_attempts" not in error
    with pytest.raises(ValueError, match="hung_call_retries"):
        _transport().model_validate(
            {**_transport().model_dump(), "hung_call_retries": -1}
        )
    with pytest.raises(ValueError, match="hung_call_retries"):
        _transport().model_validate(
            {**_transport().model_dump(), "hung_call_retries": 6}
        )


def test_other_errors_are_not_resent(tmp_path: Path) -> None:
    overload = RuntimeError(
        "Error code: 503 - upstream model provider is experiencing high demand"
    )
    client, sent = _hung_client(tmp_path, 2, [overload, "OK"])
    with pytest.raises(InfrastructureError, match="high demand"):
        client.chat.completions.create(
            model="exact-model", messages=[{"role": "user", "content": "hello"}]
        )
    assert len(sent) == 1
    assert not list((tmp_path / "calls" / "call-0001").glob("hung-attempt-*"))


def test_provider_context_rejection_is_not_infrastructure(tmp_path: Path) -> None:
    rejection = RuntimeError(
        "Error code: 500 - {'error': {'message': 'Message exceeds token limit.', "
        "'type': 'server_error'}}"
    )
    assert is_context_overflow_exception(rejection)
    assert context_overflow_message(rejection) == str(rejection)
    assert is_infrastructure_exception(rejection) is False
    assert is_infrastructure_exception(RuntimeError("Error code: 500 - crashed"))

    client, sent = _hung_client(tmp_path, 2, [rejection, "OK"])
    with pytest.raises(RuntimeError, match="exceeds token limit"):
        client.chat.completions.create(
            model="exact-model", messages=[{"role": "user", "content": "hello"}]
        )
    assert len(sent) == 1
    error = json.loads(
        (tmp_path / "calls" / "call-0001" / "error.json").read_text(encoding="utf-8")
    )
    assert error["context_overflow"] is True
    assert error["infrastructure"] is False
    assert not list((tmp_path / "calls" / "call-0001").glob("hung-attempt-*"))


def test_model_call_retry_summary_uses_immutable_attempt_files(
    tmp_path: Path,
) -> None:
    successful = tmp_path / "first" / "call-0001"
    failed = tmp_path / "second" / "call-0002"
    successful.mkdir(parents=True)
    failed.mkdir(parents=True)
    for path in (
        successful / "hung-attempt-1.json",
        failed / "hung-attempt-1.json",
        failed / "hung-attempt-2.json",
        failed / "error.json",
    ):
        path.write_text("{}\n", encoding="utf-8")

    assert summarize_model_call_retries(tmp_path, configured_retries=2) == {
        "configured_extra_attempts": 2,
        "hung_attempt_count": 3,
        "calls_resent": 2,
        "calls_failed_after_resends": 1,
        "configured_transient_retries": 0,
        "transient_attempt_count": 0,
        "rate_limited_attempt_count": 0,
        "provider_blocked_calls": 0,
    }
    assert summarize_model_call_retries(tmp_path, configured_retries=0) == {
        "configured_extra_attempts": 0,
        "hung_attempt_count": 0,
        "calls_resent": 0,
        "calls_failed_after_resends": 0,
        "configured_transient_retries": 0,
        "transient_attempt_count": 0,
        "rate_limited_attempt_count": 0,
        "provider_blocked_calls": 0,
    }


def _queued_transport(port: int, limit: int | None) -> TransportSettings:
    return TransportSettings(
        base_url=f"http://127.0.0.1:{port}/v1",
        request_timeout_seconds=5,
        max_concurrent_requests=limit,
    )


def _peak_in_flight(
    tmp_path: Path, transports: list[TransportSettings], calls_per_client: int
) -> tuple[int, list[RecordedExactModelClient]]:
    lock = threading.Lock()
    state = {"active": 0, "peak": 0}

    def callback(_kwargs):  # type: ignore[no-untyped-def]
        with lock:
            state["active"] += 1
            state["peak"] = max(state["peak"], state["active"])
        time.sleep(0.02)
        with lock:
            state["active"] -= 1
        return SimpleNamespace(
            model="exact-model",
            choices=[SimpleNamespace(message=SimpleNamespace(content="OK"))],
            model_dump=lambda mode="json": {"model": "exact-model"},
        )

    clients = []
    for index, transport in enumerate(transports):
        client = RecordedExactModelClient(
            transport=transport,
            api_key="placeholder",
            settings=_settings(),
            log_dir=tmp_path / f"client-{index}",
        )
        client._base = _FakeOpenAI(callback)  # type: ignore[attr-defined]
        clients.append(client)

    def call(client: RecordedExactModelClient) -> None:
        client.chat.completions.create(
            model="exact-model", messages=[{"role": "user", "content": "hello"}]
        )

    threads = [
        threading.Thread(target=call, args=(client,))
        for client in clients
        for _ in range(calls_per_client)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return state["peak"], clients


def test_request_queue_is_shared_by_every_client_for_the_endpoint(
    tmp_path: Path,
) -> None:
    transport = _queued_transport(39101, 2)
    peak, _clients = _peak_in_flight(tmp_path, [transport, transport, transport], 4)

    assert peak == 2
    responses = [
        json.loads(path.read_text("utf-8"))
        for path in tmp_path.glob("client-*/call-*/response.json")
    ]
    assert len(responses) == 12
    assert all(row["slot_wait_seconds"] >= 0 for row in responses)
    assert max(row["slot_wait_seconds"] for row in responses) > 0


def test_public_request_slot_shares_budget_with_recorded_clients(
    tmp_path: Path,
) -> None:
    transport = _queued_transport(39105, 1)
    holder_ready = threading.Event()
    release_holder = threading.Event()
    caller_started = threading.Event()
    callback_started = threading.Event()

    def hold_adapter_slot() -> None:
        with model_request_slot(transport):
            holder_ready.set()
            assert release_holder.wait(timeout=2)

    def callback(_kwargs):  # type: ignore[no-untyped-def]
        callback_started.set()
        return SimpleNamespace(
            model="exact-model",
            choices=[SimpleNamespace(message=SimpleNamespace(content="OK"))],
            model_dump=lambda mode="json": {"model": "exact-model"},
        )

    client = RecordedExactModelClient(
        transport=transport,
        api_key="placeholder",
        settings=_settings(),
        log_dir=tmp_path / "calls",
    )
    client._base = _FakeOpenAI(callback)  # type: ignore[attr-defined]

    holder = threading.Thread(target=hold_adapter_slot)
    holder.start()
    assert holder_ready.wait(timeout=2)

    def call_recorded_client() -> None:
        caller_started.set()
        client.chat.completions.create(
            model="exact-model",
            messages=[{"role": "user", "content": "hello"}],
        )

    caller = threading.Thread(target=call_recorded_client)
    caller.start()
    assert caller_started.wait(timeout=2)
    time.sleep(0.03)
    assert not callback_started.is_set()
    release_holder.set()
    holder.join(timeout=2)
    caller.join(timeout=2)
    assert not holder.is_alive()
    assert not caller.is_alive()
    assert callback_started.is_set()
    response = json.loads(
        (tmp_path / "calls" / "call-0001" / "response.json").read_text()
    )
    assert response["slot_wait_seconds"] > 0


def test_unset_request_queue_keeps_calls_unqueued(tmp_path: Path) -> None:
    peak, _clients = _peak_in_flight(tmp_path, [_queued_transport(39102, None)], 6)

    assert peak > 2
    responses = [
        json.loads(path.read_text("utf-8"))
        for path in tmp_path.glob("client-*/call-*/response.json")
    ]
    assert len(responses) == 6
    assert all("slot_wait_seconds" not in row for row in responses)


def test_request_queue_records_wait_on_errors_and_rejects_conflicts(
    tmp_path: Path,
) -> None:
    client = RecordedExactModelClient(
        transport=_queued_transport(39103, 3),
        api_key="placeholder",
        settings=_settings(),
        log_dir=tmp_path / "calls",
    )

    def timeout_callback(_kwargs):  # type: ignore[no-untyped-def]
        raise TimeoutError("upstream request timed out")

    client._base = _FakeOpenAI(timeout_callback)  # type: ignore[attr-defined]
    with pytest.raises(InfrastructureError, match="timed out"):
        client.chat.completions.create(
            model="exact-model", messages=[{"role": "user", "content": "hello"}]
        )
    error = json.loads((tmp_path / "calls" / "call-0001" / "error.json").read_text())
    assert error["slot_wait_seconds"] >= 0

    with pytest.raises(ConfigurationError, match="already 3"):
        RecordedExactModelClient(
            transport=_queued_transport(39103, 4),
            api_key="placeholder",
            settings=_settings(),
            log_dir=tmp_path / "other",
        )
    with pytest.raises(ValueError, match="max_concurrent_requests"):
        _queued_transport(39104, 0)


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
    assert "near-limit prompts" in report["transport_fidelity_scope"]
    assert output.is_file()


def test_preflight_retries_only_hung_model_calls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outcomes: list[Any] = [TimeoutError("request timed out"), "positive"]

    def callback(kwargs):  # type: ignore[no-untyped-def]
        if kwargs["model"] != "exact-model":
            raise RuntimeError(f"unknown model {kwargs['model']}")
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return SimpleNamespace(model="exact-model")

    monkeypatch.setattr(
        "ahe_runner.meta.preflight.OpenAI",
        lambda **_kwargs: _FakeOpenAI(callback, ["exact-model"]),
    )
    report = run_model_preflight(
        transport=_transport().model_copy(update={"hung_call_retries": 1}),
        api_key="placeholder",
        settings=_settings(),
        output_path=tmp_path / "preflight.json",
    )
    positive = report["positive_probe"]
    assert positive["passed"] is True
    assert positive["attempt_count"] == 2
    assert len(positive["hung_attempts"]) == 1
    assert positive["hung_attempts"][0]["will_retry"] is True
