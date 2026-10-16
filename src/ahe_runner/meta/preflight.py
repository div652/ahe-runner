"""Fresh observable-contract preflight for each model-backed stage attempt."""

from __future__ import annotations

import secrets
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from openai import OpenAI

from ahe_runner.config import ModelSettings, TransportSettings
from ahe_runner.errors import InfrastructureError, IntegrityError
from ahe_runner.io import atomic_write_json

from .model_client import (
    is_infrastructure_exception,
    is_transient_exception,
    openai_credential,
    responses_reasoning,
    transient_retry_delay,
)


def run_model_preflight(
    *,
    transport: TransportSettings,
    api_key: str,
    settings: ModelSettings,
    output_path: Path,
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "schema_version": 1,
        "status": "running",
        "checked_at": datetime.now(UTC).isoformat(),
        "base_url": transport.base_url,
        "api_mode": transport.api_mode,
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
    }
    if transport.provider != "gh_copilot_server":
        report["provider"] = transport.provider
    client = OpenAI(
        base_url=transport.base_url,
        api_key=openai_credential(transport, api_key),
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

    responses_api = transport.api_mode == "responses"
    positive_payload: dict[str, Any]
    if responses_api:
        positive_payload = {
            "model": settings.model,
            "input": "Reply with OK only.",
            "max_output_tokens": min(settings.max_tokens, 64),
            "stream": False,
        }
        if settings.temperature is not None:
            positive_payload["temperature"] = settings.temperature
        if settings.reasoning_effort is not None:
            positive_payload["reasoning"] = responses_reasoning(
                settings.reasoning_effort
            )
        create: Callable[..., Any] = client.responses.create
    else:
        positive_payload = {
            "model": settings.model,
            "messages": [{"role": "user", "content": "Reply with OK only."}],
            "max_tokens": min(settings.max_tokens, 64),
            "stream": False,
        }
        if settings.temperature is not None:
            positive_payload["temperature"] = settings.temperature
        if settings.reasoning_effort is not None:
            positive_payload["extra_body"] = {
                "reasoning": {"effort": settings.reasoning_effort}
            }
        create = client.chat.completions.create
    transient_errors: list[str] = []
    try:
        positive = _create_with_transient_retries(
            create, positive_payload, transport, transient_errors
        )
    except Exception as exc:
        report["positive_probe"] = {
            "passed": False,
            "error_type": type(exc).__name__,
            "error": str(exc),
            "status_code": getattr(exc, "status_code", None),
        }
        if is_infrastructure_exception(exc):
            report["status"] = "infrastructure_failed"
            atomic_write_json(output_path, report)
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
        }
        atomic_write_json(output_path, report)
        raise IntegrityError(
            f"positive probe returned {returned_model!r}; expected {settings.model!r}"
        )
    report["positive_probe"] = {
        "passed": True,
        "returned_model": returned_model,
    }
    if responses_api:
        report["positive_probe"]["status"] = getattr(positive, "status", None)
    if transient_errors:
        report["positive_probe"]["transient_errors"] = transient_errors

    negative: dict[str, Any] | None = None
    if transport.negative_control != "disabled":
        nonexistent = f"ahe-negative-control-{secrets.token_hex(16)}"
        negative_payload = dict(positive_payload)
        negative_payload["model"] = nonexistent
        try:
            response = create(**negative_payload)
        except Exception as exc:
            text = str(exc)
            passed = nonexistent.casefold() in text.casefold()
            negative = {
                "requested_model": nonexistent,
                "passed": passed,
                "error_type": type(exc).__name__,
                "error": text,
                "status_code": getattr(exc, "status_code", None),
            }
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


def _create_with_transient_retries(
    create: Callable[..., Any],
    payload: dict[str, Any],
    transport: TransportSettings,
    errors: list[str],
) -> Any:
    """Resend a probe rejected for rate limits or 5xx, as model calls are."""
    while True:
        try:
            return create(**payload)
        except Exception as exc:
            if (
                not is_transient_exception(exc)
                or len(errors) >= transport.transient_retries
            ):
                raise
            errors.append(f"{type(exc).__name__}: {exc}")
            time.sleep(
                transient_retry_delay(
                    exc,
                    attempt=len(errors),
                    max_wait=transport.transient_max_wait_seconds,
                )
            )
