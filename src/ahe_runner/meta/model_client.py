"""Exact-model, non-streaming, fully recorded OpenAI-compatible client."""

from __future__ import annotations

import json
import threading
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
from ahe_runner.errors import InfrastructureError, IntegrityError
from ahe_runner.io import atomic_write_json, sha256_json


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
        if kwargs.get("stream") is True:
            raise IntegrityError("streaming model calls are prohibited")
        kwargs["stream"] = False
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
        if self._settings.reasoning_effort is not None:
            expected = {"effort": self._settings.reasoning_effort}
            reasoning = kwargs.pop("reasoning", None)
            if reasoning is not None and reasoning != expected:
                raise IntegrityError(
                    f"reasoning was {reasoning!r}; expected {expected!r}"
                )
            extra_body = dict(kwargs.get("extra_body") or {})
            existing = extra_body.get("reasoning")
            if existing is not None and existing != expected:
                raise IntegrityError(
                    f"extra_body reasoning was {existing!r}; expected {expected!r}"
                )
            extra_body["reasoning"] = expected
            kwargs["extra_body"] = extra_body
        else:
            reasoning = kwargs.pop("reasoning", None)
            extra_body = dict(kwargs.get("extra_body") or {})
            extra_reasoning = extra_body.pop("reasoning", None)
            if reasoning is not None or extra_reasoning is not None:
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
        atomic_write_json(
            call_dir / "request.json",
            {
                "created_at": datetime.now(UTC).isoformat(),
                "args": _json_safe(args),
                "kwargs": request_payload,
                "request_sha256": sha256_json(request_payload),
            },
        )
        try:
            response = self._base.chat.completions.create(*args, **kwargs)
        except Exception as exc:
            error = _error_payload(exc)
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
        atomic_write_json(
            call_dir / "response.json",
            {
                "completed_at": datetime.now(UTC).isoformat(),
                "requested_model": requested_model,
                "returned_model": returned_model,
                "response": response_payload,
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
    }


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
