from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from openai import APIStatusError, RateLimitError

from ahe_runner.config import ModelSettings, TransportSettings
from ahe_runner.errors import IntegrityError, ProviderBlockedError
from ahe_runner.meta import endpoint
from ahe_runner.meta.endpoint import (
    TokenWindow,
    build_openai_client,
    is_provider_blocked_failure,
    nexau_api_type,
    nexau_reasoning,
    route_fingerprint,
    token_provider,
    transient_retry_delay,
)
from ahe_runner.meta.model_client import RecordedExactModelClient, wire_input_items
from ahe_runner.meta.preflight import run_model_preflight


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(endpoint.STATE_DIR_ENV, str(tmp_path / "state"))
    monkeypatch.setattr(endpoint.time, "sleep", lambda _seconds: None)


def _transport(port: int, **updates: Any) -> TransportSettings:
    return TransportSettings(
        base_url=f"http://127.0.0.1:{port}/v1",
        request_timeout_seconds=5,
        **updates,
    )


def _settings(**updates: Any) -> ModelSettings:
    values: dict[str, Any] = {
        "model": "exact-model",
        "max_tokens": 128,
        "reasoning_effort": "high",
        **updates,
    }
    return ModelSettings(**values)


def _status_error(
    status: int,
    message: str,
    *,
    body: Any = None,
    headers: dict[str, str] | None = None,
) -> APIStatusError:
    response = httpx.Response(
        status,
        request=httpx.Request("POST", "http://127.0.0.1/v1/responses"),
        headers=headers or {},
    )
    if status == 429:
        return RateLimitError(message, response=response, body=body)
    return APIStatusError(message, response=response, body=body)


class _FakeRaw:
    def __init__(self, value: Any) -> None:
        self.headers = {"x-ratelimit-remaining-tokens": "10", "server": "gateway"}
        self._value = value

    def parse(self) -> Any:
        return self._value


class _FakeResponsesBase:
    def __init__(self, callback):  # type: ignore[no-untyped-def]
        self.calls: list[dict[str, Any]] = []

        def create(**kwargs):  # type: ignore[no-untyped-def]
            self.calls.append(kwargs)
            return _FakeRaw(callback(kwargs))

        self.responses = SimpleNamespace(
            with_raw_response=SimpleNamespace(create=create),
            create=lambda **kwargs: callback(kwargs),
        )
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=lambda **kwargs: callback(kwargs))
        )
        self.models = SimpleNamespace(
            list=lambda: SimpleNamespace(data=[SimpleNamespace(id="exact-model")])
        )


def _response(model: str = "exact-model", status: str = "completed") -> Any:
    return SimpleNamespace(
        model=model,
        status=status,
        error=None,
        model_dump=lambda mode="json": {"model": model, "status": status},
    )


def test_responses_calls_enforce_settings_and_resend_tool_items(
    tmp_path: Path,
) -> None:
    client = RecordedExactModelClient(
        transport=_transport(39201, api_mode="responses"),
        api_key="placeholder",
        settings=_settings(temperature=None),
        log_dir=tmp_path / "calls",
    )
    base = _FakeResponsesBase(lambda _kwargs: _response())
    client._base = base  # type: ignore[attr-defined]
    client.responses.create(
        model="exact-model",
        input=[
            {"type": "message", "role": "user", "content": "hi"},
            {
                "type": "function_call",
                "call_id": "c1",
                "name": "read",
                "arguments": "{}",
                "async_": False,
                "namespace": None,
            },
        ],
        max_output_tokens=128,
    )
    sent = base.calls[0]
    assert sent["max_output_tokens"] == 128
    assert "temperature" not in sent
    assert sent["reasoning"] == {"effort": "high", "summary": "auto"}
    assert sent["input"][1] == {
        "type": "function_call",
        "call_id": "c1",
        "name": "read",
        "arguments": "{}",
        "async": False,
    }
    record = json.loads(
        (tmp_path / "calls" / "call-0001" / "response.json").read_text()
    )
    assert record["response_headers"] == {"x-ratelimit-remaining-tokens": "10"}
    request = json.loads(
        (tmp_path / "calls" / "call-0001" / "request.json").read_text()
    )
    assert request["api"] == "responses"

    with pytest.raises(IntegrityError, match="sends no temperature"):
        client.responses.create(model="exact-model", input="x", temperature=0.2)
    with pytest.raises(IntegrityError, match="protected alias"):
        client.responses.create(model="exact-model", input="x", max_tokens=128)
    with pytest.raises(IntegrityError, match="configured for the responses API"):
        client.chat.completions.create(model="exact-model", messages=[])
    with pytest.raises(IntegrityError, match="not available"):
        client.responses.stream  # noqa: B018

    failed = RecordedExactModelClient(
        transport=_transport(39201, api_mode="responses"),
        api_key="placeholder",
        settings=_settings(),
        log_dir=tmp_path / "failed",
    )
    failed._base = _FakeResponsesBase(  # type: ignore[attr-defined]
        lambda _kwargs: _response(status="failed")
    )
    with pytest.raises(Exception, match="response status failed"):
        failed.responses.create(model="exact-model", input="x")


def test_wire_input_items_leave_other_items_untouched() -> None:
    message = {"type": "message", "content": None}
    assert wire_input_items([message]) == [message]
    assert wire_input_items("text") == "text"


def test_per_model_wire_api_and_route_fingerprint() -> None:
    transport = _transport(39202)
    chat = _settings()
    responses = _settings(api_mode="responses")
    assert nexau_api_type(transport, chat) == "openai_chat_completion"
    assert nexau_api_type(transport, responses) == "openai_responses"
    assert nexau_reasoning(transport, chat) == {"effort": "high"}
    assert nexau_reasoning(transport, responses) == {
        "effort": "high",
        "summary": "auto",
    }
    assert nexau_reasoning(transport, _settings(reasoning_effort=None)) is None
    fingerprint = route_fingerprint(transport, responses)
    assert fingerprint["wire_api"] == "responses"
    assert fingerprint["system_prompt_placement"] == "responses_instructions"
    assert fingerprint["reasoning_carried_between_turns"] is True
    assert route_fingerprint(transport, chat)["system_prompt_placement"] == (
        "system_message"
    )


def test_transient_failures_are_resent_with_jittered_backoff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outcomes: list[Any] = [
        _status_error(429, "slow down", headers={"retry-after": "0"}),
        _status_error(503, "unavailable"),
        _response(),
    ]

    def callback(_kwargs):  # type: ignore[no-untyped-def]
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    client = RecordedExactModelClient(
        transport=_transport(39203, api_mode="responses", transient_retries=2),
        api_key="placeholder",
        settings=_settings(),
        log_dir=tmp_path / "calls",
    )
    client._base = _FakeResponsesBase(callback)  # type: ignore[attr-defined]
    client.responses.create(model="exact-model", input="x")
    call = tmp_path / "calls" / "call-0001"
    record = json.loads((call / "response.json").read_text())
    assert record["attempt_count"] == 3
    assert [row["status_code"] for row in record["transient_attempts"]] == [429, 503]
    assert (call / "transient-attempt-2.json").is_file()

    outcomes[:] = [_status_error(400, "bad request")]
    with pytest.raises(APIStatusError):
        client.responses.create(model="exact-model", input="x")
    assert not list((tmp_path / "calls" / "call-0002").glob("transient-*"))

    monkeypatch.setattr(endpoint.random, "uniform", lambda low, high: high)
    advised = _status_error(429, "slow", headers={"retry-after": "40"})
    assert transient_retry_delay(advised, attempt=1, max_wait=120) == 60.0
    assert transient_retry_delay(advised, attempt=6, max_wait=120) == 180.0
    plain = _status_error(500, "error")
    assert transient_retry_delay(plain, attempt=2, max_wait=120) == 15.0


def test_provider_block_stops_every_client_of_the_endpoint(tmp_path: Path) -> None:
    transport = _transport(39204, api_mode="responses", transient_retries=5)
    blocked = _status_error(
        403,
        "forbidden",
        body={"error": "PrincipalBlocked", "reason": "retry-flood"},
    )

    def refuse(_kwargs):  # type: ignore[no-untyped-def]
        raise blocked

    first = RecordedExactModelClient(
        transport=transport,
        api_key="placeholder",
        settings=_settings(),
        log_dir=tmp_path / "first",
    )
    first._base = _FakeResponsesBase(refuse)  # type: ignore[attr-defined]
    with pytest.raises(ProviderBlockedError) as caught:
        first.responses.create(model="exact-model", input="x")
    assert is_provider_blocked_failure(caught.value)
    assert is_provider_blocked_failure(str(caught.value))
    assert not list((tmp_path / "first" / "call-0001").glob("transient-*"))

    second = RecordedExactModelClient(
        transport=transport,
        api_key="placeholder",
        settings=_settings(),
        log_dir=tmp_path / "second",
    )
    base = _FakeResponsesBase(lambda _kwargs: _response())
    second._base = base  # type: ignore[attr-defined]
    with pytest.raises(ProviderBlockedError, match="no request was sent"):
        second.responses.create(model="exact-model", input="x")
    assert base.calls == []
    error = json.loads((tmp_path / "second" / "call-0001" / "error.json").read_text())
    assert error["type"] == "ProviderBlockedError"
    assert error["sent"] is False

    # Another process on the host sees the block through the shared state file
    # until it expires.
    endpoint._BLOCKED_ENDPOINTS.clear()
    assert endpoint.provider_block(transport) is not None
    expired = transport.model_copy(update={"provider_block_seconds": 1e-9})
    assert endpoint.provider_block(expired) is None
    plain_403 = _status_error(403, "forbidden", body={"error": "Unauthorized"})
    assert not endpoint.is_provider_block(plain_403, transport)


def test_token_window_admits_adapts_and_recovers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [1000.0]
    monkeypatch.setattr(endpoint.time, "time", lambda: clock[0])

    def sleep(seconds: float) -> None:
        clock[0] += seconds

    monkeypatch.setattr(endpoint.time, "sleep", sleep)
    monkeypatch.setattr(endpoint.random, "uniform", lambda low, high: 0.0)
    transport = _transport(
        39205,
        token_budget_per_minute=1000,
        token_budget_cut=0.5,
        token_budget_floor=0.3,
        token_budget_recover_seconds=100,
    )
    window = TokenWindow(transport, "exact-model")
    assert window.acquire(600) == 0.0
    waited = window.acquire(600)
    assert 59.0 <= waited <= 61.0

    window.refused(10)
    window.refused(10)
    window.refused(10)
    with window._state() as state:
        assert state["budget"] == 300.0
    clock[0] += 200
    with window._state() as state:
        assert window._budget(state, clock[0]) == pytest.approx(330.0)
    assert window.estimate({"input": "x" * 30, "max_output_tokens": 5}) == (
        pytest.approx(
            len(json.dumps({"input": "x" * 30, "max_output_tokens": 5})) / 3 + 5
        )
    )


def test_token_command_supplies_refreshed_bearer_tokens(tmp_path: Path) -> None:
    counter = tmp_path / "count"
    script = (
        "import json, pathlib, sys, time; p = pathlib.Path(sys.argv[1]); "
        "n = int(p.read_text()) + 1 if p.exists() else 1; p.write_text(str(n)); "
        "print(json.dumps({'accessToken': f'token-{n}', "
        "'expires_on': time.time() + float(sys.argv[2])}))"
    )
    transport = _transport(
        39206,
        token_command=shlex.join([sys.executable, "-c", script, str(counter), "3600"]),
    )
    provider = token_provider(transport)
    assert provider is not None
    assert provider() == "token-1"
    assert provider() == "token-1"

    short = _transport(
        39207,
        token_command=shlex.join([sys.executable, "-c", script, str(counter), "1"]),
    )
    short_provider = token_provider(short)
    assert short_provider is not None
    assert short_provider() == "token-2"
    assert short_provider() == "token-3"

    client = build_openai_client(transport, "unused", client_id="ahe-runner-x")
    request = httpx.Request("POST", "http://127.0.0.1/v1/responses")
    for hook in client._client.event_hooks["request"]:
        hook(request)
    assert request.headers["Authorization"] == "Bearer token-1"

    with pytest.raises(ValueError, match="exclusive"):
        _transport(39208, token_command="x", api_key_env="KEY")
    raw = _transport(39209, token_command=shlex.join(["echo", "plain-token"]))
    assert token_provider(raw)() == "plain-token"  # type: ignore[misc]


def test_client_id_header_is_stable_per_session(tmp_path: Path) -> None:
    transport = _transport(39210, client_id_header="X-Workload-ID")
    client = RecordedExactModelClient(
        transport=transport,
        api_key="placeholder",
        settings=_settings(),
        log_dir=tmp_path / "session",
    )
    header = client._base.default_headers["X-Workload-ID"]  # type: ignore[attr-defined]
    again = RecordedExactModelClient(
        transport=transport,
        api_key="placeholder",
        settings=_settings(),
        log_dir=tmp_path / "session",
    )
    other = RecordedExactModelClient(
        transport=transport,
        api_key="placeholder",
        settings=_settings(),
        log_dir=tmp_path / "other",
    )
    assert header.startswith("ahe-runner-")
    assert again._base.default_headers["X-Workload-ID"] == header  # type: ignore[attr-defined]
    assert other._base.default_headers["X-Workload-ID"] != header  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("error", "phrases", "identified_by"),
    [
        (
            _status_error(
                400,
                "The requested model is not supported.",
                body={"error": {"code": "model_not_supported"}},
            ),
            [],
            "error_code",
        ),
        (
            _status_error(400, "this model does not support the responses endpoint"),
            [],
            "model_rejection_phrase",
        ),
        (
            _status_error(404, "deployment missing here"),
            ["deployment missing"],
            ("model_rejection_phrase"),
        ),
        (_status_error(400, "invalid input"), [], None),
    ],
)
def test_preflight_negative_control_recognises_gateway_rejections(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error: APIStatusError,
    phrases: list[str],
    identified_by: str | None,
) -> None:
    def callback(kwargs):  # type: ignore[no-untyped-def]
        if kwargs["model"] == "exact-model":
            assert kwargs["max_output_tokens"] == 64
            assert kwargs["reasoning"] == {"effort": "high", "summary": "auto"}
            return _response()
        raise error

    monkeypatch.setattr(
        "ahe_runner.meta.preflight.OpenAI",
        lambda **_kwargs: _FakeResponsesBase(callback),
    )
    transport = _transport(
        39211,
        api_mode="responses",
        negative_control="best_effort",
        model_rejection_phrases=phrases,
    )
    report = run_model_preflight(
        transport=transport,
        api_key="placeholder",
        settings=_settings(),
        output_path=tmp_path / "preflight.json",
    )
    assert report["api_mode"] == "responses"
    assert report["positive_probe"]["status"] == "completed"
    assert report["negative_control"]["identified_by"] == identified_by
    assert report["negative_control"]["passed"] is (identified_by is not None)


def _responses_payload(model: str, output: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "id": "resp-test",
        "object": "response",
        "created_at": 1,
        "model": model,
        "status": "completed",
        "output": output,
        "parallel_tool_calls": True,
        "tool_choice": "auto",
        "tools": [],
        "usage": {
            "input_tokens": 10,
            "output_tokens": 5,
            "total_tokens": 15,
            "input_tokens_details": {"cached_tokens": 0},
            "output_tokens_details": {"reasoning_tokens": 2},
        },
    }


def test_evaluated_agent_runs_through_the_responses_api(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openai import OpenAI

    import ahe_runner.meta.model_client as model_client_module
    import ahe_runner.runtime as runtime_module
    from ahe_runner.adapter import BenchmarkInterface
    from ahe_runner.config import load_config
    from ahe_runner.harness import load_harness_manifest
    from ahe_runner.runtime import (
        AgentInput,
        AgentInputMessage,
        AgentSessionRequest,
        EvaluatedAgentRuntime,
    )

    from .conftest import make_example_project

    config = load_config(make_example_project(tmp_path))
    model = config.document.models.evaluated.model_copy(
        update={
            "api_mode": "responses",
            "reasoning_effort": "high",
            "temperature": None,
        }
    )
    requests: list[dict[str, Any]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/responses")
        payload = json.loads(request.content)
        requests.append(payload)
        if len(requests) == 1:
            output = [
                {
                    "type": "reasoning",
                    "id": "rs-1",
                    "summary": [{"type": "summary_text", "text": "Plan a note."}],
                    "encrypted_content": "opaque-reasoning",
                },
                {
                    "type": "function_call",
                    "id": "fc-1",
                    "call_id": "call-1",
                    "name": "record_note",
                    "arguments": json.dumps({"note": "confirmed"}),
                    "status": "completed",
                },
            ]
        else:
            output = [
                {
                    "type": "message",
                    "id": "msg-1",
                    "role": "assistant",
                    "status": "completed",
                    "content": [
                        {"type": "output_text", "text": "Done.", "annotations": []}
                    ],
                }
            ]
        return httpx.Response(200, json=_responses_payload(model.model, output))

    def build(transport, api_key, *, client_id=None):  # type: ignore[no-untyped-def]
        del transport, client_id
        return OpenAI(
            base_url="http://127.0.0.1:39299/v1",
            api_key=api_key,
            max_retries=0,
            http_client=httpx.Client(transport=httpx.MockTransport(handle)),
        )

    def fake_preflight(*, output_path: Path, **_kwargs: Any) -> dict[str, Any]:
        output_path.write_text(json.dumps({"status": "completed"}), encoding="utf-8")
        return {"status": "completed"}

    monkeypatch.setattr(model_client_module, "build_openai_client", build)
    monkeypatch.setattr(runtime_module, "run_model_preflight", fake_preflight)
    runtime = EvaluatedAgentRuntime(
        harness_dir=config.harness_dir,
        manifest=load_harness_manifest(config.harness_manifest_path),
        benchmark_interface=BenchmarkInterface(description="Responses fixture."),
        model=model,
        transport=config.document.transport,
        api_key=config.api_key,
        artifact_root=tmp_path / "runtime",
    )
    try:
        session = runtime.open_session(
            AgentSessionRequest(
                session_id="responses",
                artifact_dir=tmp_path / "runtime" / "responses",
            )
        )
        output = session.run(
            AgentInput(
                messages=(AgentInputMessage(role="user", content="Record a note."),)
            )
        )
        session.close()
    finally:
        runtime.close()

    assert "Done." in output.final_text
    assert len(requests) == 2
    first, second = requests
    assert first["model"] == model.model
    assert first["max_output_tokens"] == model.max_tokens
    assert first["reasoning"] == {"effort": "high", "summary": "auto"}
    assert "temperature" not in first
    assert first["instructions"].strip()
    resent = {item.get("type"): item for item in second["input"] if "type" in item}
    assert resent["reasoning"]["encrypted_content"] == "opaque-reasoning"
    call = resent["function_call"]
    assert "async_" not in call
    assert call.get("namespace", "") is not None
    assert resent["function_call_output"]["call_id"] == "call-1"
