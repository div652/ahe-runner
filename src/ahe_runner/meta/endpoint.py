"""Endpoint policy shared by recorded model calls, preflights, and adapters.

Everything here concerns how requests reach one endpoint, never what they
ask: credentials, back-off, provider blocks, and token admission control.
"""

from __future__ import annotations

import email.utils
import fcntl
import json
import os
import random
import shlex
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager, suppress
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

import httpx
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    InternalServerError,
    OpenAI,
    RateLimitError,
)

from ahe_runner.config import ModelSettings, TransportSettings
from ahe_runner.errors import (
    PROVIDER_BLOCKED_MARKER,
    ConfigurationError,
    InfrastructureError,
    ProviderBlockedError,
)

STATE_DIR_ENV = "AHE_RUNNER_STATE_DIR"

# Transient retries: exponential back-off from this base, never shorter than
# the server's Retry-After, plus up to this fraction of random extra wait so
# that clients rate-limited together do not retry together (AHE-031).
BACKOFF_BASE_SECONDS = 5.0
BACKOFF_JITTER_FRACTION = 0.5

_TOKEN_COMMAND_TIMEOUT_SECONDS = 120.0
# A token command that reports no expiry is rerun after this long.
_TOKEN_DEFAULT_LIFETIME_SECONDS = 1800.0
# Never sleep longer than this per admission check, so stale state cannot stall.
_WINDOW_MAX_SLEEP_SECONDS = 60.0

_TOKEN_PROVIDERS: dict[tuple[str, float], CommandTokenProvider] = {}
_TOKEN_PROVIDERS_LOCK = threading.Lock()
_BLOCKED_ENDPOINTS: dict[str, str] = {}
_BLOCKED_ENDPOINTS_LOCK = threading.Lock()


def wire_api(transport: TransportSettings, model: ModelSettings) -> str:
    """The wire API one model uses: its own override, else the transport's."""

    return model.api_mode or transport.api_mode


def nexau_api_type(transport: TransportSettings, model: ModelSettings) -> str:
    """NexAU ``llm_config.api_type`` for the model's wire API."""

    if wire_api(transport, model) == "responses":
        return "openai_responses"
    return "openai_chat_completion"


def responses_reasoning(effort: str) -> dict[str, str]:
    """Reasoning request for the Responses API, with a reasoning summary."""

    return {"effort": effort, "summary": "auto"}


def nexau_reasoning(
    transport: TransportSettings,
    model: ModelSettings,
) -> dict[str, str] | None:
    """NexAU ``llm_config.reasoning`` for the model's wire API."""

    if model.reasoning_effort is None:
        return None
    if wire_api(transport, model) == "responses":
        return responses_reasoning(model.reasoning_effort)
    return {"effort": model.reasoning_effort}


def route_fingerprint(
    transport: TransportSettings,
    model: ModelSettings,
) -> dict[str, Any]:
    """What the evaluated model sees because of the route, not the harness.

    Scores can depend on these properties (AHE-035), so every evaluation
    records them and comparisons can check that they match.
    """

    api = wire_api(transport, model)
    return {
        "schema_version": 1,
        "base_url": transport.base_url,
        "wire_api": api,
        "authentication": "token_command" if transport.token_command else "api_key",
        "model": model.model,
        "reasoning_effort": model.reasoning_effort,
        "temperature": model.temperature,
        "max_output_tokens": model.max_tokens,
        "system_prompt_placement": (
            "responses_instructions" if api == "responses" else "system_message"
        ),
        "reasoning_carried_between_turns": api == "responses",
        "observable_model_policy": transport.observable_model_policy,
    }


class CommandTokenProvider:
    """Bearer token from an operator command, cached and refreshed in-process.

    The command prints either the raw token, or a JSON object with the token
    under ``accessToken``, ``access_token`` or ``token`` and an optional expiry
    (``expires_on``/``expires_at`` epoch seconds or ``expires_in`` seconds).
    The token is never written anywhere. Each process has its own cache.
    """

    def __init__(self, command: str, *, refresh_margin_seconds: float) -> None:
        self._command = command
        self._margin = refresh_margin_seconds
        self._token: str | None = None
        self._expires_at = 0.0
        self._lock = threading.Lock()

    def __call__(self) -> str:
        with self._lock:
            if self._token is None or self._expires_at - time.time() < self._margin:
                self._token, self._expires_at = _run_token_command(self._command)
            return self._token


def token_provider(transport: TransportSettings) -> CommandTokenProvider | None:
    if transport.token_command is None:
        return None
    key = (transport.token_command, transport.token_refresh_margin_seconds)
    with _TOKEN_PROVIDERS_LOCK:
        provider = _TOKEN_PROVIDERS.get(key)
        if provider is None:
            provider = CommandTokenProvider(
                transport.token_command,
                refresh_margin_seconds=transport.token_refresh_margin_seconds,
            )
            _TOKEN_PROVIDERS[key] = provider
        return provider


def _run_token_command(command: str) -> tuple[str, float]:
    try:
        completed = subprocess.run(
            shlex.split(command),
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=_TOKEN_COMMAND_TIMEOUT_SECONDS,
            check=False,
        )
    except FileNotFoundError as exc:
        raise ConfigurationError(
            f"transport.token_command executable not found: {exc}"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise InfrastructureError("transport.token_command timed out") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or "").strip().splitlines()
        raise InfrastructureError(
            f"transport.token_command exited {completed.returncode}"
            + (f": {detail[-1]}" if detail else "")
        )
    output = completed.stdout.strip()
    now = time.time()
    if not output.startswith("{"):
        if not output:
            raise InfrastructureError("transport.token_command printed no token")
        return output, now + _TOKEN_DEFAULT_LIFETIME_SECONDS
    try:
        payload = json.loads(output)
    except ValueError as exc:
        raise InfrastructureError(
            "transport.token_command printed invalid JSON"
        ) from exc
    token = next(
        (
            str(payload[key])
            for key in ("accessToken", "access_token", "token")
            if payload.get(key)
        ),
        None,
    )
    if token is None:
        raise InfrastructureError("transport.token_command JSON has no token field")
    expires_at = now + _TOKEN_DEFAULT_LIFETIME_SECONDS
    for key in ("expires_on", "expires_at"):
        try:
            expires_at = float(payload[key])
            break
        except (KeyError, TypeError, ValueError):
            continue
    else:
        with suppress(KeyError, TypeError, ValueError):
            expires_at = now + float(payload["expires_in"])
    return token, expires_at


def build_openai_client(
    transport: TransportSettings,
    api_key: str,
    *,
    client_id: str | None = None,
) -> OpenAI:
    """OpenAI SDK client for the transport, without SDK retries.

    A token command supplies the bearer token per request. ``client_id`` is
    sent in ``transport.client_id_header`` when that header is configured.
    """

    headers = (
        {transport.client_id_header: client_id}
        if client_id is not None and transport.client_id_header
        else None
    )
    provider = token_provider(transport)
    http_client = None
    if provider is not None:

        def authorize(request: httpx.Request) -> None:
            request.headers["Authorization"] = f"Bearer {provider()}"

        http_client = httpx.Client(
            timeout=transport.request_timeout_seconds,
            event_hooks={"request": [authorize]},
        )
    return OpenAI(
        base_url=transport.base_url,
        api_key=api_key if provider is None else "token-command",
        max_retries=0,
        timeout=transport.request_timeout_seconds,
        default_headers=headers,
        http_client=http_client,
    )


def client_id_for(log_dir: Path) -> str:
    """A stable client ID per call log, that is per agent session or judge."""

    digest = sha256(str(log_dir.resolve()).encode()).hexdigest()[:16]
    return f"ahe-runner-{digest}"


def is_hung_call_exception(exc: BaseException) -> bool:
    """A request timeout (client or endpoint) fired before any response."""

    for current in cause_chain(exc):
        if isinstance(current, (APITimeoutError, TimeoutError)):
            return True
        if "timed out" in str(current).casefold():
            return True
    return False


def is_transient_exception(exc: BaseException) -> bool:
    """HTTP 429, a 5xx status, or a connection error; not a request timeout."""

    if is_hung_call_exception(exc):
        return False
    if isinstance(exc, (RateLimitError, InternalServerError, APIConnectionError)):
        return True
    return isinstance(exc, APIStatusError) and (
        exc.status_code == 429 or exc.status_code >= 500
    )


def is_rate_limited(exc: BaseException) -> bool:
    return isinstance(exc, RateLimitError) or (
        isinstance(exc, APIStatusError) and exc.status_code == 429
    )


def is_provider_block(exc: BaseException, transport: TransportSettings) -> bool:
    """HTTP 403 whose body names a configured provider-block marker."""

    if not isinstance(exc, APIStatusError) or exc.status_code != 403:
        return False
    text = f"{exc} {getattr(exc, 'body', '') or ''}"
    return any(marker in text for marker in transport.provider_block_markers)


def is_provider_blocked_failure(exc: BaseException | str) -> bool:
    """Whether a failure, possibly wrapped or from a child process, is a block."""

    if isinstance(exc, str):
        return PROVIDER_BLOCKED_MARKER in exc
    return any(
        isinstance(current, ProviderBlockedError)
        or PROVIDER_BLOCKED_MARKER in str(current)
        for current in cause_chain(exc)
    )


_REJECTION_STATUSES = frozenset({400, 422})


def provider_rejection_message(exc: BaseException) -> str | None:
    """A provider's refusal of one request, if that is what failed (AHE-030).

    Only HTTP 400 or 422 qualifies: content filters, malformed requests, or a
    request the provider will not serve. Context-length rejections, rate
    limits, timeouts, authentication, missing models, and provider blocks are
    classified elsewhere and return None.
    """

    from .model_client import is_context_overflow_exception

    if is_context_overflow_exception(exc) or is_provider_blocked_failure(exc):
        return None
    for current in cause_chain(exc):
        if getattr(current, "status_code", None) in _REJECTION_STATUSES:
            return str(current)
    return None


def transient_retry_delay(
    exc: BaseException,
    *,
    attempt: int,
    max_wait: float,
) -> float:
    """Exponential back-off, at least Retry-After, capped, with jitter.

    Attempt n waits ``max(Retry-After, 5 * 2^(n-1))`` seconds, capped at
    ``max_wait``, plus a random extra of up to half of that, so clients
    refused together spread out instead of retrying in the same second.
    """

    advised = retry_after_seconds(getattr(exc, "response", None))
    backoff = BACKOFF_BASE_SECONDS * 2 ** min(max(attempt - 1, 0), 16)
    base = min(max(advised or 0.0, backoff), max_wait)
    return base + random.uniform(0.0, base * BACKOFF_JITTER_FRACTION)


def retry_after_seconds(response: Any) -> float | None:
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


def cause_chain(exc: BaseException) -> Iterator[BaseException]:
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def state_dir() -> Path:
    """Per-user directory for host-wide endpoint state shared by processes."""

    directory = Path(
        os.environ.get(STATE_DIR_ENV)
        or Path(tempfile.gettempdir()) / f"ahe-runner-{os.getuid()}"
    )
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _endpoint_key(base_url: str, *parts: str) -> str:
    return sha256("\n".join((base_url, *parts)).encode()).hexdigest()[:16]


def record_provider_block(transport: TransportSettings, message: str) -> None:
    """Stop every client of this endpoint, in this and other host processes."""

    with _BLOCKED_ENDPOINTS_LOCK:
        _BLOCKED_ENDPOINTS.setdefault(transport.base_url, message[:1500])
    path = state_dir() / f"{_endpoint_key(transport.base_url)}-blocked.json"
    path.write_text(
        json.dumps({"blocked_at": time.time(), "message": message[:1500]}),
        encoding="utf-8",
    )


def provider_block(transport: TransportSettings) -> str | None:
    """The active provider block for this endpoint, if any."""

    with _BLOCKED_ENDPOINTS_LOCK:
        local = _BLOCKED_ENDPOINTS.get(transport.base_url)
    if local is not None:
        return local
    path = state_dir() / f"{_endpoint_key(transport.base_url)}-blocked.json"
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    age = time.time() - float(record.get("blocked_at", 0.0))
    if age > transport.provider_block_seconds:
        return None
    return str(record.get("message") or "provider block")


def raise_if_provider_blocked(transport: TransportSettings) -> None:
    blocked = provider_block(transport)
    if blocked is not None:
        raise ProviderBlockedError(
            f"{PROVIDER_BLOCKED_MARKER}; no request was sent: {blocked}"
        )


class TokenWindow:
    """Host-wide admission control for one model on one endpoint (AHE-032).

    Every process on the host shares a log of [send time, estimated cost] for
    the last 60 seconds, the current budget, and a cool-down time, in a file
    locked with ``flock``. A request is admitted only when the window's total
    plus its cost fits the budget, or the window is empty. Costs are never
    refunded, so the window cannot burst. Each 429 multiplies the budget by
    ``cut`` (down to ``floor`` of the maximum) and pauses every client for the
    advised wait; each quiet ``recover_seconds`` restores 10%. Advertised
    limits and remaining-token headers are not trusted.
    """

    WINDOW_SECONDS = 60.0
    RECOVER = 1.1

    def __init__(self, transport: TransportSettings, model: str) -> None:
        if transport.token_budget_per_minute is None:
            raise ConfigurationError("token admission control is not configured")
        self.path = state_dir() / f"{_endpoint_key(transport.base_url, model)}.json"
        self.maximum = float(transport.token_budget_per_minute)
        self.cut = transport.token_budget_cut
        self.floor = transport.token_budget_floor
        self.recover_seconds = transport.token_budget_recover_seconds
        self.chars_per_token = transport.token_budget_chars_per_token

    @contextmanager
    def _state(self) -> Iterator[dict[str, Any]]:
        with open(self.path, "a+", encoding="utf-8") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                handle.seek(0)
                try:
                    state = json.loads(handle.read() or "{}")
                except ValueError:
                    state = {}
                yield state
                handle.seek(0)
                handle.truncate()
                handle.write(json.dumps(state))
                handle.flush()
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def _budget(self, state: dict[str, Any], now: float) -> float:
        # A budget left by a run with a larger maximum never exceeds this one.
        budget = min(float(state.get("budget", self.maximum)), self.maximum)
        quiet_since = max(state.get("last_429", 0.0), state.get("last_raise", 0.0))
        if budget < self.maximum and now - quiet_since >= self.recover_seconds:
            budget = min(self.maximum, budget * self.RECOVER)
            state["last_raise"] = now
        state["budget"] = budget
        return budget

    def estimate(self, kwargs: Mapping[str, Any]) -> float:
        """Input tokens from the payload size, plus the requested output cap."""

        size = len(json.dumps(kwargs, ensure_ascii=False, default=repr))
        output = (
            kwargs.get("max_output_tokens")
            or kwargs.get("max_completion_tokens")
            or kwargs.get("max_tokens")
            or 0
        )
        return size / self.chars_per_token + float(output)

    def acquire(self, cost: float) -> float:
        """Wait until ``cost`` fits, reserve it, and return the seconds waited."""

        waited = 0.0
        while True:
            with self._state() as state:
                now = time.time()
                log = [
                    entry
                    for entry in state.get("log", [])
                    if now - entry[0] < self.WINDOW_SECONDS
                ]
                state["log"] = log
                budget = self._budget(state, now)
                wait = state.get("cooldown_until", 0.0) - now
                if wait <= 0:
                    used = sum(entry[1] for entry in log)
                    if not log or used + cost <= budget:
                        log.append([now, cost])
                        return waited
                    excess = used + cost - budget
                    for sent_at, entry_cost in log:
                        excess -= entry_cost
                        if excess <= 0:
                            wait = sent_at + self.WINDOW_SECONDS - now
                            break
                    else:
                        wait = log[-1][0] + self.WINDOW_SECONDS - now
            pause = min(max(wait, 0.05), _WINDOW_MAX_SLEEP_SECONDS)
            pause += random.uniform(0.0, 1.0)
            time.sleep(pause)
            waited += pause

    def refused(self, seconds: float) -> None:
        """After a 429: shrink the budget and pause every client."""

        with self._state() as state:
            now = time.time()
            budget = min(float(state.get("budget", self.maximum)), self.maximum)
            state["budget"] = max(self.maximum * self.floor, budget * self.cut)
            state["last_429"] = now
            state["cooldown_until"] = max(
                state.get("cooldown_until", 0.0), now + seconds
            )


def send_with_endpoint_policy(
    *,
    transport: TransportSettings,
    model: str,
    send: Callable[[], Any],
    payload: Mapping[str, Any],
    window: TokenWindow | None,
    on_retry: Callable[[str, dict[str, Any]], None],
    slot: Callable[[dict[str, float]], Any],
    error_payload: Callable[[BaseException], dict[str, Any]],
) -> tuple[Any, dict[str, float], list[dict[str, Any]], list[dict[str, Any]]]:
    """Send one request under the endpoint policy.

    Before each attempt: refuse if the endpoint is blocked, wait for token
    admission, and take a request slot. On failure: a provider block stops
    the endpoint; a timeout before any response is resent up to
    ``hung_call_retries``; HTTP 429, 5xx, and connection errors are resent up
    to ``transient_retries`` with jittered back-off. ``on_retry`` records each
    failed attempt before it is resent. Any other failure is raised as is,
    with the attempts recorded so far attached as ``ahe_attempts``.
    """

    hung: list[dict[str, Any]] = []
    transient: list[dict[str, Any]] = []
    while True:
        raise_if_provider_blocked(transport)
        waited: dict[str, float] = {}
        if window is not None:
            paced = window.acquire(window.estimate(payload))
            if paced:
                waited["pacing_wait_seconds"] = round(paced, 3)
        try:
            with slot(waited):
                response = send()
            return response, waited, hung, transient
        except Exception as exc:
            error = {**error_payload(exc), **waited}
            if is_provider_block(exc, transport):
                record_provider_block(transport, str(exc))
                exc.ahe_attempts = (hung, transient)  # type: ignore[attr-defined]
                raise ProviderBlockedError(
                    f"{PROVIDER_BLOCKED_MARKER}: {str(exc)[:1500]}"
                ) from exc
            if window is not None and is_rate_limited(exc):
                advised = retry_after_seconds(getattr(exc, "response", None))
                window.refused(max(advised or 0.0, BACKOFF_BASE_SECONDS))
            if is_hung_call_exception(exc) and len(hung) < transport.hung_call_retries:
                error["attempt"] = len(hung) + len(transient) + 1
                error["will_retry"] = True
                hung.append(error)
                on_retry("hung", error)
                continue
            if (
                is_transient_exception(exc)
                and len(transient) < transport.transient_retries
            ):
                delay = transient_retry_delay(
                    exc,
                    attempt=len(transient) + 1,
                    max_wait=transport.transient_max_wait_seconds,
                )
                error["attempt"] = len(hung) + len(transient) + 1
                error["will_retry"] = True
                error["retry_delay_seconds"] = round(delay, 3)
                transient.append(error)
                on_retry("transient", error)
                time.sleep(delay)
                continue
            exc.ahe_attempts = (hung, transient)  # type: ignore[attr-defined]
            exc.ahe_waited = waited  # type: ignore[attr-defined]
            raise


__all__ = [
    "CommandTokenProvider",
    "TokenWindow",
    "build_openai_client",
    "client_id_for",
    "is_hung_call_exception",
    "is_provider_block",
    "is_provider_blocked_failure",
    "is_transient_exception",
    "nexau_api_type",
    "nexau_reasoning",
    "record_provider_block",
    "responses_reasoning",
    "route_fingerprint",
    "send_with_endpoint_policy",
    "transient_retry_delay",
    "wire_api",
]
