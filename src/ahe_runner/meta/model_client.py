"""Exact-model, non-streaming, fully recorded OpenAI-compatible client."""

from __future__ import annotations

import email.utils
import json
import multiprocessing
import random
import subprocess
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    InternalServerError,
    OpenAI,
    RateLimitError,
)

from ahe_runner.config import ModelSettings, TransportSettings
from ahe_runner.errors import ConfigurationError, InfrastructureError, IntegrityError
from ahe_runner.io import atomic_write_json, sha256_json

_PROTECTED_EXTRA_BODY_FIELDS = frozenset(
    {
        "model",
        "max_tokens",
        "max_completion_tokens",
        "max_output_tokens",
        "temperature",
        "stream",
        "reasoning",
        "reasoning_effort",
    }
)

# Response headers worth keeping with each call: rate-limit state and request
# IDs. Anything else (for example a gateway's backend URL) is not recorded.
_RECORDED_HEADER_PREFIXES = ("x-ratelimit-",)
_RECORDED_HEADERS = frozenset(
    {
        "retry-after",
        "retry-after-ms",
        "x-request-id",
        "x-trapi-request-id",
        "apim-request-id",
        "x-ms-region",
    }
)
_TOKEN_REFRESH_MARGIN_SECONDS = 300.0
_TOKEN_COMMAND_TIMEOUT_SECONDS = 120.0
_TOOL_CALL_ITEM_TYPES = frozenset({"function_call", "custom_tool_call"})
_RESPONSE_ITEM_ALIASES = {"async_": "async"}
_TOKEN_PROVIDERS: dict[str, AzureCliTokenProvider] = {}
_TOKEN_PROVIDERS_LOCK = threading.Lock()

# A multiprocessing semaphore, so trial processes spawned by this process can
# share the endpoint's cap (AHE-025); it works across threads as well.
_REQUEST_SLOTS: dict[str, tuple[int, Any]] = {}
_REQUEST_SLOTS_LOCK = threading.Lock()
_SLOT_CONTEXT = multiprocessing.get_context("spawn")


def _shared_request_slots(transport: TransportSettings) -> Any | None:
    """Return the call queue shared by every client for this endpoint, if set."""
    limit = transport.max_concurrent_requests
    if limit is None:
        return None
    with _REQUEST_SLOTS_LOCK:
        existing = _REQUEST_SLOTS.get(transport.base_url)
        if existing is None:
            slots = _SLOT_CONTEXT.BoundedSemaphore(limit)
            _REQUEST_SLOTS[transport.base_url] = (limit, slots)
            return slots
        if existing[0] != limit:
            raise ConfigurationError(
                f"max_concurrent_requests for {transport.base_url} is already "
                f"{existing[0]} in this process; got {limit}"
            )
        return existing[1]


def shared_request_slots(transport: TransportSettings) -> Any | None:
    """Return this endpoint's semaphore, to hand to a spawned child process."""
    return _shared_request_slots(transport)


def adopt_shared_request_slots(transport: TransportSettings, slots: Any) -> None:
    """Make a spawned child use its parent's semaphore for this endpoint."""
    limit = transport.max_concurrent_requests
    if (limit is None) != (slots is None):
        raise ConfigurationError(
            "a child process must receive request slots exactly when "
            "max_concurrent_requests is set"
        )
    if slots is None:
        return
    with _REQUEST_SLOTS_LOCK:
        existing = _REQUEST_SLOTS.get(transport.base_url)
        if existing is not None and existing[1] is not slots:
            raise ConfigurationError(
                f"request slots for {transport.base_url} already exist in this process"
            )
        _REQUEST_SLOTS[transport.base_url] = (limit, slots)


@contextmanager
def _request_slot(slots: Any | None, waited: dict[str, float]) -> Iterator[None]:
    if slots is None:
        yield
        return
    started = time.monotonic()
    with slots:
        waited["slot_wait_seconds"] = round(time.monotonic() - started, 3)
        yield


class AzureCliTokenProvider:
    """Bearer token for one scope from the Azure CLI login, cached in-process.

    The token is refreshed shortly before it expires and is never written
    anywhere. Each process (including spawned trial processes) has its own.
    """

    def __init__(self, scope: str) -> None:
        self._scope = scope
        self._token: str | None = None
        self._expires_on = 0.0
        self._lock = threading.Lock()

    def __call__(self) -> str:
        with self._lock:
            if (
                self._token is None
                or self._expires_on - time.time() < _TOKEN_REFRESH_MARGIN_SECONDS
            ):
                self._refresh()
            assert self._token is not None
            return self._token

    def _refresh(self) -> None:
        command = [
            "az",
            "account",
            "get-access-token",
            "--scope",
            self._scope,
            "--output",
            "json",
        ]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=_TOKEN_COMMAND_TIMEOUT_SECONDS,
                check=False,
            )
        except FileNotFoundError as exc:
            raise ConfigurationError(
                "provider azure_entra needs the Azure CLI (az) on PATH"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise InfrastructureError("az account get-access-token timed out") from exc
        if completed.returncode != 0:
            detail = (completed.stderr or "").strip().splitlines()
            raise ConfigurationError(
                "az account get-access-token failed (run `az login`): "
                + (detail[-1] if detail else f"exit {completed.returncode}")
            )
        try:
            payload = json.loads(completed.stdout)
            token = str(payload["accessToken"])
        except (ValueError, KeyError, TypeError) as exc:
            raise InfrastructureError(
                "az account get-access-token returned no token"
            ) from exc
        expires_on = payload.get("expires_on")
        try:
            expiry = float(expires_on)
        except (TypeError, ValueError):
            expiry = time.time() + 30 * 60
        self._token = token
        self._expires_on = expiry


def azure_cli_token_provider(scope: str) -> AzureCliTokenProvider:
    with _TOKEN_PROVIDERS_LOCK:
        provider = _TOKEN_PROVIDERS.get(scope)
        if provider is None:
            provider = AzureCliTokenProvider(scope)
            _TOKEN_PROVIDERS[scope] = provider
        return provider


def openai_credential(
    transport: TransportSettings, api_key: str
) -> str | Callable[[], str]:
    """The SDK ``api_key``: the key itself, or a token provider for Entra."""
    if transport.provider == "azure_entra":
        assert transport.token_scope is not None
        return azure_cli_token_provider(transport.token_scope)
    return api_key


def build_openai_client(transport: TransportSettings, api_key: str) -> OpenAI:
    """OpenAI SDK client for the transport's provider, without SDK retries."""
    return OpenAI(
        base_url=transport.base_url,
        api_key=openai_credential(transport, api_key),
        max_retries=0,
        timeout=transport.request_timeout_seconds,
    )


class RecordedExactModelClient:
    """OpenAI client facade that fails closed on observable model mismatch."""

    def __init__(
        self,
        *,
        transport: TransportSettings,
        api_key: str,
        settings: ModelSettings,
        log_dir: Path,
    ) -> None:
        self._base = build_openai_client(transport, api_key)
        self._transport = transport
        self._settings = settings
        self._log_dir = log_dir
        self._lock = threading.Lock()
        self._request_slots = _shared_request_slots(transport)
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._sequence = max(
            (
                int(path.name.removeprefix("call-"))
                for path in self._log_dir.glob("call-*")
                if path.is_dir() and path.name.removeprefix("call-").isdigit()
            ),
            default=0,
        )
        self.chat = _RecordedChatProxy(self)
        self.responses = _RecordedResponsesProxy(self)

    def _create(self, *args: Any, **kwargs: Any) -> Any:
        requested_model = self._require_model(kwargs)
        if kwargs.get("stream") not in (None, False):
            raise IntegrityError("streaming model calls are prohibited")
        kwargs["stream"] = False
        extra_body = self._extra_body(kwargs)
        self._require_setting(
            kwargs,
            name="max_tokens",
            expected=self._settings.max_tokens,
        )
        self._require_temperature(kwargs)
        self._reject_alias(kwargs, "max_completion_tokens")
        self._reject_alias(kwargs, "max_output_tokens")
        reasoning = kwargs.pop("reasoning", None)
        reasoning_effort = kwargs.pop("reasoning_effort", None)
        if self._settings.reasoning_effort is not None:
            expected = {"effort": self._settings.reasoning_effort}
            if reasoning is not None and reasoning != expected:
                raise IntegrityError(
                    f"reasoning was {reasoning!r}; expected {expected!r}"
                )
            if (
                reasoning_effort is not None
                and reasoning_effort != self._settings.reasoning_effort
            ):
                raise IntegrityError(
                    f"reasoning_effort was {reasoning_effort!r}; expected "
                    f"{self._settings.reasoning_effort!r}"
                )
            extra_body["reasoning"] = expected
        else:
            if reasoning is not None or reasoning_effort is not None:
                raise IntegrityError(
                    "reasoning override is prohibited when campaign reasoning "
                    "is provider-default"
                )
        self._set_extra_body(kwargs, extra_body)
        return self._send("chat_completions", args, kwargs, requested_model)

    def _create_response(self, *args: Any, **kwargs: Any) -> Any:
        """Responses API call with the campaign's exact model settings."""
        requested_model = self._require_model(kwargs)
        if kwargs.get("stream") not in (None, False):
            raise IntegrityError("streaming model calls are prohibited")
        kwargs["stream"] = False
        extra_body = self._extra_body(kwargs)
        self._reject_alias(kwargs, "max_tokens")
        self._reject_alias(kwargs, "max_completion_tokens")
        self._require_setting(
            kwargs,
            name="max_output_tokens",
            expected=self._settings.max_tokens,
        )
        self._require_temperature(kwargs)
        if kwargs.pop("reasoning_effort", None) is not None:
            raise IntegrityError("the Responses API takes reasoning.effort only")
        reasoning = kwargs.pop("reasoning", None)
        if self._settings.reasoning_effort is not None:
            expected = responses_reasoning(self._settings.reasoning_effort)
            if reasoning is not None and reasoning != expected:
                raise IntegrityError(
                    f"reasoning was {reasoning!r}; expected {expected!r}"
                )
            kwargs["reasoning"] = expected
        elif reasoning is not None:
            raise IntegrityError(
                "reasoning override is prohibited when campaign reasoning "
                "is provider-default"
            )
        self._set_extra_body(kwargs, extra_body)
        if "input" in kwargs:
            kwargs["input"] = wire_input_items(kwargs["input"])
        return self._send("responses", args, kwargs, requested_model)

    def _require_model(self, kwargs: dict[str, Any]) -> str:
        requested_model = str(kwargs.get("model") or "")
        if requested_model != self._settings.model:
            raise IntegrityError(
                f"model request used {requested_model!r}; "
                f"expected {self._settings.model!r}"
            )
        return requested_model

    def _extra_body(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        extra_body_value = kwargs.get("extra_body")
        if extra_body_value is None:
            extra_body: dict[str, Any] = {}
        elif isinstance(extra_body_value, Mapping):
            extra_body = dict(extra_body_value)
        else:
            raise IntegrityError("extra_body must be an object")
        collisions = sorted(_PROTECTED_EXTRA_BODY_FIELDS & set(extra_body))
        if collisions:
            raise IntegrityError(
                "extra_body cannot override protected model fields: "
                + ", ".join(collisions)
            )
        return extra_body

    @staticmethod
    def _set_extra_body(kwargs: dict[str, Any], extra_body: dict[str, Any]) -> None:
        if extra_body:
            kwargs["extra_body"] = extra_body
        else:
            kwargs.pop("extra_body", None)

    def _send(
        self,
        api: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        requested_model: str,
    ) -> Any:
        call_dir = self._next_call_dir()
        request_payload = _json_safe(kwargs)
        atomic_write_json(
            call_dir / "request.json",
            {
                "created_at": datetime.now(UTC).isoformat(),
                **({"api": api} if api != "chat_completions" else {}),
                "args": _json_safe(args),
                "kwargs": request_payload,
                "request_sha256": sha256_json(request_payload),
            },
        )
        hung_attempts: list[dict[str, Any]] = []
        transient_attempts: list[dict[str, Any]] = []
        response_headers: dict[str, str] | None = None
        while True:
            waited: dict[str, float] = {}
            try:
                with _request_slot(self._request_slots, waited):
                    if api == "responses":
                        raw = self._base.responses.with_raw_response.create(
                            *args, **kwargs
                        )
                        response_headers = recorded_headers(raw.headers)
                        response = raw.parse()
                    else:
                        response = self._base.chat.completions.create(*args, **kwargs)
                break
            except Exception as exc:
                error = {**_error_payload(exc), **waited}
                if (
                    is_hung_call_exception(exc)
                    and len(hung_attempts) < self._transport.hung_call_retries
                ):
                    hung_attempts.append(error)
                    atomic_write_json(
                        call_dir / f"hung-attempt-{len(hung_attempts)}.json", error
                    )
                    continue
                if (
                    is_transient_exception(exc)
                    and len(transient_attempts) < self._transport.transient_retries
                ):
                    delay = transient_retry_delay(
                        exc,
                        attempt=len(transient_attempts) + 1,
                        max_wait=self._transport.transient_max_wait_seconds,
                    )
                    error["retry_delay_seconds"] = round(delay, 3)
                    transient_attempts.append(error)
                    atomic_write_json(
                        call_dir / f"transient-attempt-{len(transient_attempts)}.json",
                        error,
                    )
                    time.sleep(delay)
                    continue
                if hung_attempts:
                    error["hung_attempts"] = hung_attempts
                if transient_attempts:
                    error["transient_attempts"] = transient_attempts
                atomic_write_json(call_dir / "error.json", error)
                if is_infrastructure_exception(exc):
                    raise InfrastructureError(error["message"]) from exc
                raise
        returned_model = getattr(response, "model", None)
        if returned_model != self._settings.model:
            error = {
                "type": "ObservableModelMismatch",
                "message": (
                    f"response returned {returned_model!r}; "
                    f"expected {self._settings.model!r}"
                ),
            }
            atomic_write_json(call_dir / "error.json", error)
            raise IntegrityError(error["message"])
        response_payload = _model_dump(response)
        record = {
            "completed_at": datetime.now(UTC).isoformat(),
            "requested_model": requested_model,
            "returned_model": returned_model,
            "response": response_payload,
            **waited,
            **({"hung_attempts": hung_attempts} if hung_attempts else {}),
            **(
                {"transient_attempts": transient_attempts}
                if transient_attempts
                else {}
            ),
            **(
                {"response_headers": response_headers}
                if response_headers is not None
                else {}
            ),
        }
        if api == "responses" and getattr(response, "status", None) == "failed":
            failure = getattr(response, "error", None)
            error = {
                "type": "ResponseFailed",
                "message": f"response status failed: {failure!r}",
            }
            atomic_write_json(call_dir / "error.json", {**error, **record})
            raise InfrastructureError(error["message"])
        atomic_write_json(call_dir / "response.json", record)
        return response

    def _require_setting(
        self,
        kwargs: dict[str, Any],
        *,
        name: str,
        expected: Any,
    ) -> None:
        received = kwargs.get(name)
        if received is not None and received != expected:
            raise IntegrityError(
                f"{name} was {received!r}; expected campaign value {expected!r}"
            )
        kwargs[name] = expected

    def _require_temperature(self, kwargs: dict[str, Any]) -> None:
        expected = self._settings.temperature
        if expected is not None:
            self._require_setting(kwargs, name="temperature", expected=expected)
            return
        received = kwargs.pop("temperature", None)
        if received is not None:
            raise IntegrityError(
                f"temperature was {received!r}; the campaign sends no temperature"
            )

    def _reject_alias(self, kwargs: dict[str, Any], name: str) -> None:
        if kwargs.pop(name, None) is not None:
            raise IntegrityError(
                f"{name} is a protected alias; use the campaign max_tokens setting"
            )

    def _next_call_dir(self) -> Path:
        with self._lock:
            self._sequence += 1
            sequence = self._sequence
        path = self._log_dir / f"call-{sequence:04d}"
        path.mkdir(parents=True, exist_ok=False)
        return path

    def __getattr__(self, name: str) -> Any:
        return getattr(self._base, name)


class _RecordedChatProxy:
    def __init__(self, owner: RecordedExactModelClient) -> None:
        self._owner = owner
        self.completions = _RecordedCompletionsProxy(owner)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._owner._base.chat, name)


class _RecordedCompletionsProxy:
    def __init__(self, owner: RecordedExactModelClient) -> None:
        self._owner = owner

    def create(self, *args: Any, **kwargs: Any) -> Any:
        return self._owner._create(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._owner._base.chat.completions, name)


class _RecordedResponsesProxy:
    """Only ``create`` is allowed; streaming and other calls fail closed."""

    def __init__(self, owner: RecordedExactModelClient) -> None:
        self._owner = owner

    def create(self, *args: Any, **kwargs: Any) -> Any:
        return self._owner._create_response(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        if name.startswith("__"):
            raise AttributeError(name)
        raise IntegrityError(f"responses.{name} is not available on this client")


def wire_input_items(value: Any) -> Any:
    """Give resent tool-call items their API field names and drop nulls.

    NexAU 0.3.9 copies Responses output items into the next request with
    ``model_dump()``: Python field names, nulls kept. Newer openai SDKs give
    tool calls ``async_`` (API name ``async``) and a nullable ``namespace``
    that the API only accepts as a string, so the request is rejected.
    """
    if not isinstance(value, list):
        return value
    items: list[Any] = []
    for item in value:
        if isinstance(item, Mapping) and item.get("type") in _TOOL_CALL_ITEM_TYPES:
            item = {
                _RESPONSE_ITEM_ALIASES.get(str(key), str(key)): field
                for key, field in item.items()
                if field is not None
            }
        items.append(item)
    return items


def responses_reasoning(effort: str) -> dict[str, str]:
    """Reasoning request for the Responses API, as LAB's OpenAI adapter sends it."""
    return {"effort": effort, "summary": "auto"}


def nexau_api_type(transport: TransportSettings) -> str:
    """NexAU ``llm_config.api_type`` for the transport's wire API."""
    if transport.api_mode == "responses":
        return "openai_responses"
    return "openai_chat_completion"


def nexau_reasoning(
    transport: TransportSettings, effort: str | None
) -> dict[str, str] | None:
    """NexAU ``llm_config.reasoning`` for the transport's wire API."""
    if effort is None:
        return None
    if transport.api_mode == "responses":
        return responses_reasoning(effort)
    return {"effort": effort}


def recorded_headers(headers: Any) -> dict[str, str]:
    if headers is None:
        return {}
    kept: dict[str, str] = {}
    for name, value in headers.items():
        key = str(name).casefold()
        if key in _RECORDED_HEADERS or key.startswith(_RECORDED_HEADER_PREFIXES):
            kept[key] = str(value)
    return dict(sorted(kept.items()))


def is_transient_exception(exc: BaseException) -> bool:
    """HTTP 429, a 5xx status or a connection error; not a request timeout."""
    if is_hung_call_exception(exc):
        return False
    if isinstance(exc, (RateLimitError, InternalServerError, APIConnectionError)):
        return True
    return isinstance(exc, APIStatusError) and (
        exc.status_code == 429 or exc.status_code >= 500
    )


def transient_retry_delay(
    exc: BaseException,
    *,
    attempt: int,
    max_wait: float,
) -> float:
    """Server Retry-After when given, else exponential; capped, plus jitter."""
    advised = _retry_after_seconds(getattr(exc, "response", None))
    base = advised if advised is not None else float(min(60, 2**attempt))
    return min(max(base, 0.0), max_wait) + random.uniform(0.0, 1.0)


def _retry_after_seconds(response: Any) -> float | None:
    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    milliseconds = headers.get("retry-after-ms")
    if milliseconds is not None:
        try:
            return float(milliseconds) / 1000.0
        except ValueError:
            pass
    value = headers.get("retry-after")
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        pass
    try:
        when = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - datetime.now(UTC)).total_seconds())


def completion_text(response: Any) -> str:
    choices = getattr(response, "choices", None)
    if not choices:
        raise ValueError("completion returned no choices")
    content = getattr(choices[0].message, "content", None)
    if not isinstance(content, str) or not content.strip():
        raise ValueError("completion returned empty content")
    return content.strip()


def parse_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if len(lines) >= 3 and lines[-1].strip() == "```":
            stripped = "\n".join(lines[1:-1])
            if stripped.lstrip().startswith("json"):
                stripped = stripped.lstrip()[4:].lstrip()
    try:
        value = json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start < 0 or end <= start:
            raise
        value = json.loads(stripped[start : end + 1])
    if not isinstance(value, dict):
        raise TypeError("model response must be a JSON object")
    return value


def is_infrastructure_exception(exc: BaseException) -> bool:
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(
            current,
            (
                APITimeoutError,
                APIConnectionError,
                InternalServerError,
                RateLimitError,
            ),
        ):
            return True
        if isinstance(current, APIStatusError) and current.status_code >= 500:
            return True
        text = str(current).casefold()
        if any(
            token in text
            for token in (
                "timed out",
                "timeout",
                "connection reset",
                "connection refused",
                "server_error",
                "error code: 500",
                "error code: 502",
                "error code: 503",
                "error code: 504",
            )
        ):
            return True
        current = current.__cause__ or current.__context__
    return False


def is_hung_call_exception(exc: BaseException) -> bool:
    """A request timeout (client or endpoint) fired before any response."""
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, (APITimeoutError, TimeoutError)):
            return True
        if "timed out" in str(current).casefold():
            return True
        current = current.__cause__ or current.__context__
    return False


def raise_classified_model_error(exc: BaseException) -> None:
    if is_infrastructure_exception(exc):
        raise InfrastructureError(str(exc)) from exc
    raise exc


def _error_payload(exc: BaseException) -> dict[str, Any]:
    status = getattr(exc, "status_code", None)
    payload = {
        "completed_at": datetime.now(UTC).isoformat(),
        "type": type(exc).__name__,
        "message": str(exc),
        "status_code": status,
        "infrastructure": is_infrastructure_exception(exc),
    }
    headers = recorded_headers(
        getattr(getattr(exc, "response", None), "headers", None)
    )
    if headers:
        payload["response_headers"] = headers
    return payload


def _model_dump(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return _json_safe(value)


def _json_safe(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except TypeError:
        if isinstance(value, dict):
            return {str(key): _json_safe(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [_json_safe(item) for item in value]
        return repr(value)
