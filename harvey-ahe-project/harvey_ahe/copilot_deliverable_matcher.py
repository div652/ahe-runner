"""Protected, recorded callbacks for LAB's filename-matching helper.

``CopilotDeliverableMatcher`` calls a model (chat completions on GH Copilot
Server, or the Responses API). ``DisabledDeliverableMatcher`` makes no model
call and answers "no match" for every key, recording each call.
"""

from __future__ import annotations

import json
import tempfile
import threading
from pathlib import Path
from typing import Any, Literal

from jsonschema import Draft202012Validator, ValidationError
from lab_core.harness.adapters.copilot import resolve_copilot_base_url

from ahe_runner.config import ModelSettings, TransportSettings
from ahe_runner.errors import ConfigurationError, InfrastructureError, IntegrityError
from ahe_runner.io import atomic_write_json, sha256_file
from ahe_runner.meta.model_client import (
    RecordedExactModelClient,
    completion_text,
    responses_reasoning,
)
from ahe_runner.meta.preflight import run_model_preflight


class DisabledDeliverableMatcher:
    """No filename helper: every unresolved deliverable stays unmatched.

    This is what LAB's built-in fallback returns when its helper model is
    unreachable, but here it is explicit and recorded.
    """

    def __init__(self, *, artifact_root: Path) -> None:
        self._artifact_root = artifact_root.resolve()
        self._call_index = 0
        self._lock = threading.Lock()

    def __call__(
        self,
        prompt: str,
        output_schema: dict[str, Any],
    ) -> dict[str, str | None]:
        Draft202012Validator.check_schema(output_schema)
        keys = output_schema.get("required")
        if not isinstance(keys, list) or not all(isinstance(key, str) for key in keys):
            raise IntegrityError("Filename-helper schema has no required key list")
        matches: dict[str, str | None] = {key: None for key in keys}
        with self._lock:
            self._call_index += 1
            atomic_write_json(
                self._artifact_root
                / "disabled"
                / "matches"
                / f"call-{self._call_index:04d}.json",
                {"status": "disabled", "matches": matches},
            )
        return matches

    def close(self) -> None:
        return None


class CopilotDeliverableMatcher:
    """Match filenames without silently converting helper failures into no match."""

    def __init__(
        self,
        *,
        settings: ModelSettings,
        transport: TransportSettings,
        api_key: str,
        artifact_root: Path,
        api: Literal["chat", "responses"] = "chat",
    ) -> None:
        if api == "chat":
            resolve_copilot_base_url(transport.base_url)
            if settings.max_tokens != 1024 or settings.temperature != 0.0:
                raise ConfigurationError(
                    "The LAB filename helper requires max_tokens=1024 and "
                    "temperature=0.0"
                )
            if (
                transport.model_catalog != "required"
                or transport.negative_control != "required"
            ):
                raise ConfigurationError(
                    "The filename helper requires catalog and negative-control "
                    "preflights"
                )
        elif transport.api_mode != "responses":
            raise ConfigurationError(
                "A Responses filename helper requires transport.api_mode responses"
            )
        self._api = api
        self._settings = settings.model_copy(deep=True)
        self._transport = transport.model_copy(deep=True)
        self._api_key = api_key
        self._artifact_root = artifact_root.resolve()
        self._artifact_dir: Path | None = None
        self._client: RecordedExactModelClient | None = None
        self._preflight_error: Exception | None = None
        self._call_index = 0
        self._closed = False
        self._lock = threading.Lock()

    @property
    def artifact_dir(self) -> Path | None:
        return self._artifact_dir

    def __call__(
        self,
        prompt: str,
        output_schema: dict[str, Any],
    ) -> dict[str, str | None]:
        Draft202012Validator.check_schema(output_schema)
        with self._lock:
            if self._closed:
                raise ConfigurationError("The filename matcher is closed")
            if self._preflight_error is not None:
                raise self._preflight_error
            if self._client is None:
                try:
                    self._artifact_root.mkdir(parents=True, exist_ok=True)
                    self._artifact_dir = Path(
                        tempfile.mkdtemp(prefix="attempt-", dir=self._artifact_root)
                    )
                    run_model_preflight(
                        transport=self._transport,
                        api_key=self._api_key,
                        settings=self._settings,
                        output_path=self._artifact_dir / "model-preflight.json",
                    )
                    self._client = RecordedExactModelClient(
                        transport=self._transport,
                        api_key=self._api_key,
                        settings=self._settings,
                        log_dir=self._artifact_dir / "model_calls",
                    )
                except Exception as exc:
                    self._preflight_error = exc
                    raise

            client = self._client
            artifact_dir = self._artifact_dir
            if client is None or artifact_dir is None:
                raise IntegrityError("Incomplete filename-helper initialization")
            self._call_index += 1
            result_path = artifact_dir / "matches" / f"call-{self._call_index:04d}.json"
            if self._api == "chat":
                # The proxy ignores response_format, so include the format contract.
                wire_prompt = (
                    prompt + "\n\nReturn only a JSON object, with no Markdown fences, "
                    "that conforms to this schema:\n"
                    + json.dumps(output_schema, sort_keys=True)
                )
                response = client.chat.completions.create(
                    model=self._settings.model,
                    messages=[{"role": "user", "content": wire_prompt}],
                )
            else:
                # Same contract as LAB's Anthropic helper: structured output.
                kwargs: dict[str, Any] = {
                    "model": self._settings.model,
                    "input": prompt,
                    "max_output_tokens": self._settings.max_tokens,
                    "text": {
                        "format": {
                            "type": "json_schema",
                            "name": "deliverable_matches",
                            "schema": output_schema,
                            "strict": True,
                        }
                    },
                }
                if self._settings.temperature is not None:
                    kwargs["temperature"] = self._settings.temperature
                if self._settings.reasoning_effort is not None:
                    kwargs["reasoning"] = responses_reasoning(
                        self._settings.reasoning_effort
                    )
                response = client.responses.create(**kwargs)
            try:
                text = (
                    completion_text(response)
                    if self._api == "chat"
                    else response.output_text or ""
                )
                value = json.loads(text)
                Draft202012Validator(output_schema).validate(value)
                if not isinstance(value, dict):
                    raise ValueError("Filename matches must be a JSON object")
                matches: dict[str, str | None] = {}
                for key, filename in value.items():
                    if not isinstance(key, str) or (
                        filename is not None and not isinstance(filename, str)
                    ):
                        raise ValueError(
                            "Filename matches require string or null values"
                        )
                    matches[key] = filename
            except (ValueError, ValidationError) as exc:
                atomic_write_json(
                    result_path,
                    {
                        "status": "infrastructure_failed",
                        "error_type": type(exc).__name__,
                        "message": str(exc),
                    },
                )
                raise InfrastructureError(
                    "Invalid filename-helper response; grading cannot complete"
                ) from exc

            atomic_write_json(
                result_path,
                {
                    "status": "completed",
                    "requested_model": self._settings.model,
                    "returned_model": response.model,
                    "preflight_sha256": sha256_file(
                        artifact_dir / "model-preflight.json"
                    ),
                    "matches": matches,
                },
            )
            return matches

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            if self._client is not None:
                self._client.close()
