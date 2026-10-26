"""Fresh observable-contract preflight for each model-backed stage attempt."""

from __future__ import annotations

import secrets
from collections.abc import Callable
from contextlib import nullcontext
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from openai import OpenAI

from ahe_runner.config import ModelSettings, TransportSettings
from ahe_runner.errors import InfrastructureError, IntegrityError
from ahe_runner.io import atomic_write_json

from .endpoint import (
    TokenWindow,
    cause_chain,
    responses_reasoning,
    send_with_endpoint_policy,
    token_provider,
    wire_api,
)
from .model_client import (
    _request_slot,
    _shared_request_slots,
    is_infrastructure_exception,
)

# Structured error codes that say the requested model itself was refused.
_MODEL_REJECTION_CODES = frozenset(
    {
        "model_not_found",
        "model_not_supported",
        "unsupported_model",
        "invalid_model",
        "deploymentnotfound",
    }
)
# Wording of gateways that refuse an unknown model without naming it or giving
# a code. Each phrase must say the model was refused, so a rejection for any
# other reason (bad input, authentication) still fails the control.
_MODEL_REJECTION_PHRASES = (
    "this model does not support the responses endpoint",
    "the requested model is not supported",
)


def run_model_preflight(
    *,
    transport: TransportSettings,
    api_key: str,
    settings: ModelSettings,
    output_path: Path,
) -> dict[str, Any]:
    api = wire_api(transport, settings)
    report: dict[str, Any] = {
        "schema_version": 1,
        "status": "running",
        "checked_at": datetime.now(UTC).isoformat(),
        "base_url": transport.base_url,
        "api_mode": api,
        "authentication": "token_command" if transport.token_command else "api_key",
        "stream": False,
        "requested_model": settings.model,
        "reasoning_effort": settings.reasoning_effort,
        "temperature": settings.temperature,
        "advertised_model_ids": None,
        "catalog_error": None,
        "positive_probe": None,
        "negative_control": None,
        "identity_scope": (
            "This verifies the endpoint's observable model label. An endpoint "
            "that synthesizes that label may not expose transparent upstream "
            "substitution."
        ),
        "transport_fidelity_scope": (
            "This does not prove that a proxy preserves the start of near-limit "
            "prompts or applies requested reasoning effort. Those behaviors "
            "require an independent per-model transport probe."
        ),
    }
    provider = token_provider(transport)
    client = OpenAI(
        base_url=transport.base_url,
        api_key=provider() if provider is not None else api_key,
        max_retries=0,
        timeout=transport.request_timeout_seconds,
    )
    advertised: list[str] | None = None
    catalog_error: str | None = None
    if transport.model_catalog != "disabled":
        try:
            advertised = sorted(str(item.id) for item in client.models.list().data)
        except Exception as exc:
            if is_infrastructure_exception(exc):
                report["status"] = "infrastructure_failed"
                report["catalog_error"] = f"{type(exc).__name__}: {exc}"
                atomic_write_json(output_path, report)
                raise InfrastructureError(str(exc)) from exc
            catalog_error = f"{type(exc).__name__}: {exc}"
            if transport.model_catalog == "required":
                report["status"] = "failed"
                report["catalog_error"] = catalog_error
                atomic_write_json(output_path, report)
                raise IntegrityError(
                    f"model catalog preflight failed: {catalog_error}"
                ) from exc
    report["advertised_model_ids"] = advertised
    report["catalog_error"] = catalog_error
    if advertised is not None and settings.model not in advertised:
        report["status"] = "failed"
        report["positive_probe"] = {
            "passed": False,
            "error": "requested model absent from catalog",
        }
        atomic_write_json(output_path, report)
        raise IntegrityError(
            f"exact model {settings.model!r} is absent from the endpoint catalog"
        )

    positive_payload: dict[str, Any]
    create: Callable[..., Any]
    if api == "responses":
        positive_payload = {
            "model": settings.model,
            "input": "Reply with OK only.",
            "max_output_tokens": min(settings.max_tokens, 64),
            "stream": False,
        }
        if settings.reasoning_effort is not None:
            positive_payload["reasoning"] = responses_reasoning(
                settings.reasoning_effort
            )
        create = client.responses.create
    else:
        positive_payload = {
            "model": settings.model,
            "messages": [{"role": "user", "content": "Reply with OK only."}],
            "max_tokens": min(settings.max_tokens, 64),
            "stream": False,
        }
        if settings.reasoning_effort is not None:
            positive_payload["extra_body"] = {
                "reasoning": {"effort": settings.reasoning_effort}
            }
        create = client.chat.completions.create
    if settings.temperature is not None:
        positive_payload["temperature"] = settings.temperature
    window = (
        TokenWindow(transport, settings.model)
        if transport.token_budget_per_minute is not None
        else None
    )
    try:
        positive, positive_retries = _probe(transport, create, positive_payload, window)
    except Exception as exc:
        hung, transient = getattr(_root(exc), "ahe_attempts", ([], []))
        report["positive_probe"] = {
            "passed": False,
            **_probe_error_payload(exc, attempt=len(hung) + len(transient) + 1),
            "attempt_count": len(hung) + len(transient) + 1,
            **({"hung_attempts": hung} if hung else {}),
            **({"transient_attempts": transient} if transient else {}),
        }
        if is_infrastructure_exception(exc):
            report["status"] = "infrastructure_failed"
            atomic_write_json(output_path, report)
            if isinstance(exc, InfrastructureError):
                raise
            raise InfrastructureError(str(exc)) from exc
        report["status"] = "failed"
        atomic_write_json(output_path, report)
        raise
    returned_model = getattr(positive, "model", None)
    if returned_model != settings.model:
        report["status"] = "failed"
        report["positive_probe"] = {
            "passed": False,
            "returned_model": returned_model,
            "error": "observable model mismatch",
            **positive_retries,
        }
        atomic_write_json(output_path, report)
        raise IntegrityError(
            f"positive probe returned {returned_model!r}; expected {settings.model!r}"
        )
    report["positive_probe"] = {
        "passed": True,
        "returned_model": returned_model,
        **positive_retries,
    }
    if api == "responses":
        report["positive_probe"]["status"] = getattr(positive, "status", None)

    negative: dict[str, Any] | None = None
    if transport.negative_control != "disabled":
        nonexistent = f"ahe-negative-control-{secrets.token_hex(16)}"
        negative_payload = dict(positive_payload)
        negative_payload["model"] = nonexistent
        try:
            response, negative_retries = _probe(
                transport, create, negative_payload, window
            )
        except Exception as exc:
            hung, transient = getattr(_root(exc), "ahe_attempts", ([], []))
            identified_by = _negative_control_rejection(exc, nonexistent, transport)
            passed = identified_by is not None
            negative = {
                "requested_model": nonexistent,
                "passed": passed,
                "identified_by": identified_by,
                **_probe_error_payload(exc, attempt=len(hung) + len(transient) + 1),
                "attempt_count": len(hung) + len(transient) + 1,
                **({"hung_attempts": hung} if hung else {}),
                **({"transient_attempts": transient} if transient else {}),
            }
            if not passed and is_infrastructure_exception(exc):
                report["status"] = "infrastructure_failed"
                report["negative_control"] = negative
                atomic_write_json(output_path, report)
                if isinstance(exc, InfrastructureError):
                    raise
                raise InfrastructureError(str(exc)) from exc
            if not passed and transport.negative_control == "required":
                report["status"] = "failed"
                report["negative_control"] = negative
                atomic_write_json(output_path, report)
                raise IntegrityError(
                    "negative control failed without identifying the nonexistent "
                    f"model {nonexistent!r}"
                ) from exc
        else:
            negative = {
                "requested_model": nonexistent,
                "passed": False,
                "returned_model": getattr(response, "model", None),
                "error": "nonexistent model unexpectedly generated a response",
                **negative_retries,
            }
            if transport.negative_control == "required":
                report["status"] = "failed"
                report["negative_control"] = negative
                atomic_write_json(output_path, report)
                raise IntegrityError(negative["error"])

    report["status"] = "completed"
    report["negative_control"] = negative
    atomic_write_json(output_path, report)
    return report


def _probe_error_payload(exc: BaseException, *, attempt: int) -> dict[str, Any]:
    return {
        "completed_at": datetime.now(UTC).isoformat(),
        "error_type": type(exc).__name__,
        "error": str(exc),
        "status_code": getattr(exc, "status_code", None),
        "attempt": attempt,
    }


def _probe(
    transport: TransportSettings,
    create: Callable[..., Any],
    payload: dict[str, Any],
    window: TokenWindow | None,
) -> tuple[Any, dict[str, Any]]:
    """Send a probe under the same endpoint policy as recorded model calls."""

    slots = _shared_request_slots(transport)

    def slot(waited: dict[str, float]) -> Any:
        if slots is None:
            return nullcontext()
        return _request_slot(slots, waited)

    response, _waited, hung, transient = send_with_endpoint_policy(
        transport=transport,
        model=str(payload["model"]),
        send=lambda: create(**payload),
        payload=payload,
        window=window,
        on_retry=lambda _kind, _error: None,
        slot=slot,
        error_payload=lambda exc: _probe_error_payload(exc, attempt=0),
    )
    return response, {
        "attempt_count": len(hung) + len(transient) + 1,
        **({"hung_attempts": hung} if hung else {}),
        **({"transient_attempts": transient} if transient else {}),
    }


def _root(exc: BaseException) -> BaseException:
    for current in cause_chain(exc):
        if hasattr(current, "ahe_attempts"):
            return current
    return exc


def _negative_control_rejection(
    exc: BaseException,
    model: str,
    transport: TransportSettings,
) -> str | None:
    """How the error shows that the nonexistent model was refused, if it does."""

    text = str(exc).casefold()
    if model.casefold() in text:
        return "model_name"
    status = getattr(exc, "status_code", None)
    if status not in (400, 404):
        return None
    body = getattr(exc, "body", None)
    code = getattr(exc, "code", None)
    if isinstance(body, dict):
        error = body.get("error", body)
        if isinstance(error, dict):
            code = error.get("code") or code
    if isinstance(code, str) and code.casefold() in _MODEL_REJECTION_CODES:
        return "error_code"
    phrases = (*_MODEL_REJECTION_PHRASES, *transport.model_rejection_phrases)
    if any(phrase.casefold() in text for phrase in phrases):
        return "model_rejection_phrase"
    return None
