"""Exact-model, non-streaming, fully recorded OpenAI-compatible client."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterator, Mapping
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
_CONTEXT_OVERFLOW_MARKERS = (
    "context_length_exceeded",
    "maximum context length",
    "message exceeds token limit",
    "model_max_prompt_tokens_exceeded",
    "prompt is too long",
)

_REQUEST_SLOTS: dict[str, tuple[int, threading.BoundedSemaphore]] = {}
_REQUEST_SLOTS_LOCK = threading.Lock()


def _shared_request_slots(
    transport: TransportSettings,
) -> threading.BoundedSemaphore | None:
    """Return the process-wide call queue for this endpoint, if one is set."""
    limit = transport.max_concurrent_requests
    if limit is None:
        return None
    with _REQUEST_SLOTS_LOCK:
        existing = _REQUEST_SLOTS.get(transport.base_url)
        if existing is None:
            slots = threading.BoundedSemaphore(limit)
            _REQUEST_SLOTS[transport.base_url] = (limit, slots)
            return slots
        if existing[0] != limit:
            raise ConfigurationError(
                f"max_concurrent_requests for {transport.base_url} is already "
                f"{existing[0]} in this process; got {limit}"
            )
        return existing[1]


@contextmanager
def _request_slot(
    slots: threading.BoundedSemaphore | None, waited: dict[str, float]
) -> Iterator[None]:
    if slots is None:
        yield
        return
    started = time.monotonic()
    with slots:
        waited["slot_wait_seconds"] = round(time.monotonic() - started, 3)
        yield


@contextmanager
def model_request_slot(
    transport: TransportSettings,
) -> Iterator[dict[str, float]]:
    """Share the endpoint request budget with runner and adapter model calls."""

    waited: dict[str, float] = {}
    with _request_slot(_shared_request_slots(transport), waited):
        yield waited


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
        self._base = OpenAI(
            base_url=transport.base_url,
            api_key=api_key,
            max_retries=0,
            timeout=transport.request_timeout_seconds,
        )
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

    def _create(self, *args: Any, **kwargs: Any) -> Any:
        requested_model = str(kwargs.get("model") or "")
        if requested_model != self._settings.model:
            raise IntegrityError(
                f"model request used {requested_model!r}; "
                f"expected {self._settings.model!r}"
            )
        if kwargs.get("stream") not in (None, False):
            raise IntegrityError("streaming model calls are prohibited")
        kwargs["stream"] = False
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
        self._require_setting(
            kwargs,
            name="max_tokens",
            expected=self._settings.max_tokens,
        )
        self._require_setting(
            kwargs,
            name="temperature",
            expected=self._settings.temperature,
        )
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
        if extra_body:
            kwargs["extra_body"] = extra_body
        else:
            kwargs.pop("extra_body", None)

        call_dir = self._next_call_dir()
        request_payload = _json_safe(kwargs)
        prompt_accounting = _prompt_accounting(request_payload)
        atomic_write_json(
            call_dir / "request.json",
            {
                "created_at": datetime.now(UTC).isoformat(),
                "args": _json_safe(args),
                "kwargs": request_payload,
                "request_sha256": sha256_json(request_payload),
                "prompt_accounting": prompt_accounting,
            },
        )
        hung_attempts: list[dict[str, Any]] = []
        while True:
            waited: dict[str, float] = {}
            try:
                with _request_slot(self._request_slots, waited):
                    response = self._base.chat.completions.create(*args, **kwargs)
                break
            except Exception as exc:
                error = {
                    **_error_payload(exc),
                    **waited,
                    "attempt": len(hung_attempts) + 1,
                }
                if (
                    is_hung_call_exception(exc)
                    and len(hung_attempts) < self._transport.hung_call_retries
                ):
                    error["will_retry"] = True
                    hung_attempts.append(error)
                    atomic_write_json(
                        call_dir / f"hung-attempt-{len(hung_attempts)}.json",
                        error,
                    )
                    continue
                if hung_attempts:
                    error["hung_attempts"] = hung_attempts
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
                "attempt_count": len(hung_attempts) + 1,
                **({"hung_attempts": hung_attempts} if hung_attempts else {}),
            }
            atomic_write_json(call_dir / "error.json", error)
            raise IntegrityError(error["message"])
        response_payload = _model_dump(response)
        atomic_write_json(
            call_dir / "response.json",
            {
                "completed_at": datetime.now(UTC).isoformat(),
                "requested_model": requested_model,
                "returned_model": returned_model,
                "response": response_payload,
                "attempt_count": len(hung_attempts) + 1,
                "prompt_accounting": prompt_accounting,
                **waited,
                **({"hung_attempts": hung_attempts} if hung_attempts else {}),
            },
        )
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


def context_overflow_message(exc: BaseException) -> str | None:
    """Return the provider context rejection from an exception chain."""

    for current in _cause_chain(exc):
        text = str(current)
        if any(marker in text.casefold() for marker in _CONTEXT_OVERFLOW_MARKERS):
            return text
    return None


def is_context_overflow_exception(exc: BaseException) -> bool:
    return context_overflow_message(exc) is not None


def is_infrastructure_exception(exc: BaseException) -> bool:
    if is_context_overflow_exception(exc):
        return False
    for current in _cause_chain(exc):
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
    return False


def is_hung_call_exception(exc: BaseException) -> bool:
    """A request timeout (client or endpoint) fired before any response."""

    for current in _cause_chain(exc):
        if isinstance(current, (APITimeoutError, TimeoutError)):
            return True
        if "timed out" in str(current).casefold():
            return True
    return False


def summarize_model_call_retries(
    root: Path,
    *,
    configured_retries: int,
) -> dict[str, int]:
    """Summarize immutable hung-call attempt artifacts under a campaign."""

    if configured_retries == 0:
        return {
            "configured_extra_attempts": 0,
            "hung_attempt_count": 0,
            "calls_resent": 0,
            "calls_failed_after_resends": 0,
        }
    attempts = tuple(root.rglob("hung-attempt-*.json"))
    call_dirs = {path.parent for path in attempts}
    return {
        "configured_extra_attempts": configured_retries,
        "hung_attempt_count": len(attempts),
        "calls_resent": len(call_dirs),
        "calls_failed_after_resends": sum(
            (call_dir / "error.json").is_file() for call_dir in call_dirs
        ),
    }


def _cause_chain(exc: BaseException) -> Iterator[BaseException]:
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def raise_classified_model_error(exc: BaseException) -> None:
    if is_infrastructure_exception(exc):
        raise InfrastructureError(str(exc)) from exc
    raise exc


def _error_payload(exc: BaseException) -> dict[str, Any]:
    status = getattr(exc, "status_code", None)
    return {
        "completed_at": datetime.now(UTC).isoformat(),
        "type": type(exc).__name__,
        "message": str(exc),
        "status_code": status,
        "infrastructure": is_infrastructure_exception(exc),
        "context_overflow": is_context_overflow_exception(exc),
    }


def _model_dump(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return _json_safe(value)


def _prompt_accounting(payload: dict[str, Any]) -> dict[str, int]:
    messages = payload.get("messages")
    tools = payload.get("tools")
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return {
        "request_utf8_bytes": len(serialized),
        "message_count": len(messages) if isinstance(messages, list) else 0,
        "message_string_characters": _string_characters(messages),
        "tool_count": len(tools) if isinstance(tools, list) else 0,
        "tool_string_characters": _string_characters(tools),
    }


def _string_characters(value: Any) -> int:
    if isinstance(value, str):
        return len(value)
    if isinstance(value, Mapping):
        return sum(_string_characters(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return sum(_string_characters(item) for item in value)
    return 0


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
