from __future__ import annotations

import importlib.util
import json
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest
from lab_core.evaluation.scoring import score_rubric
from openai import OpenAI

import ahe_runner.meta.model_client as client_module
import ahe_runner.meta.preflight as preflight_module
from ahe_runner.config import ModelSettings, TransportSettings
from ahe_runner.errors import ConfigurationError, InfrastructureError, IntegrityError
from ahe_runner.io import read_json


class _Endpoint:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.content = '{"memo.md": "alpha.md"}'
        self.returned_model: str | None = "test-helper"
        self.fail_generation = False
        self.reject_catalog = False

    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            self.requests.append({"catalog": True})
            models = [] if self.reject_catalog else [{"id": "test-helper"}]
            return httpx.Response(200, json={"object": "list", "data": models})
        body = json.loads(request.content)
        self.requests.append(body)
        if body["model"].startswith("ahe-negative-control-"):
            return httpx.Response(
                404, json={"error": {"message": f"Unknown model {body['model']}"}}
            )
        generation = body["max_tokens"] == 1024
        if generation and self.fail_generation:
            raise httpx.ReadTimeout("mock helper timeout", request=request)
        payload: dict[str, Any] = {
            "id": "offline-completion",
            "object": "chat.completion",
            "created": 1,
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {
                        "role": "assistant",
                        "content": self.content if generation else "OK",
                    },
                }
            ],
        }
        returned_model = self.returned_model if generation else "test-helper"
        if returned_model is not None:
            payload["model"] = returned_model
        return httpx.Response(200, json=payload)


@pytest.fixture
def matcher_module() -> ModuleType:
    path = (
        Path(__file__).resolve().parents[1]
        / "harvey-ahe-project"
        / "copilot_deliverable_matcher.py"
    )
    spec = importlib.util.spec_from_file_location("harvey_filename_matcher", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def endpoint(monkeypatch: pytest.MonkeyPatch) -> Iterator[_Endpoint]:
    endpoint = _Endpoint()
    clients: list[OpenAI] = []

    def make_client(**kwargs: Any) -> OpenAI:
        client = OpenAI(
            **kwargs,
            http_client=httpx.Client(transport=httpx.MockTransport(endpoint.handle)),
        )
        clients.append(client)
        return client

    monkeypatch.setattr(client_module, "OpenAI", make_client)
    monkeypatch.setattr(preflight_module, "OpenAI", make_client)
    yield endpoint
    for client in clients:
        client.close()


@pytest.fixture
def matcher(
    tmp_path: Path,
    matcher_module: ModuleType,
    endpoint: _Endpoint,
) -> Iterator[Any]:
    callback = matcher_module.CopilotDeliverableMatcher(
        settings=ModelSettings(
            model="test-helper",
            max_tokens=1024,
            temperature=0.0,
            reasoning_effort=None,
        ),
        transport=TransportSettings(
            base_url="http://127.0.0.1:3141/v1",
            model_catalog="required",
            negative_control="required",
        ),
        api_key="offline-placeholder",
        artifact_root=tmp_path / "helper-artifacts",
    )
    yield callback
    callback.close()


def _schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {"memo.md": {"type": ["string", "null"]}},
        "required": ["memo.md"],
        "additionalProperties": False,
    }


def test_matcher_is_lazy_recorded_exact_and_non_streaming(
    matcher: Any, endpoint: _Endpoint
) -> None:
    assert endpoint.requests == []
    assert matcher.artifact_dir is None
    assert matcher("Original matching prompt", _schema()) == {"memo.md": "alpha.md"}
    assert matcher("Second matching prompt", _schema()) == {"memo.md": "alpha.md"}
    assert len(endpoint.requests) == 5
    calls = endpoint.requests[3:]
    for request in calls:
        assert request["model"] == "test-helper"
        assert request["max_tokens"] == 1024
        assert request["temperature"] == 0.0
        assert request["stream"] is False
        assert "reasoning" not in request
        assert "response_format" not in request
    assert calls[0]["messages"][0]["content"].startswith("Original matching prompt")
    assert json.dumps(_schema(), sort_keys=True) in calls[0]["messages"][0]["content"]
    proof = read_json(matcher.artifact_dir / "model-preflight.json")
    assert proof["positive_probe"]["passed"] is True
    assert proof["negative_control"]["passed"] is True
    recorded = read_json(
        matcher.artifact_dir / "model_calls" / "call-0001" / "response.json"
    )
    assert recorded["requested_model"] == recorded["returned_model"] == "test-helper"
    assert read_json(matcher.artifact_dir / "matches" / "call-0002.json")[
        "matches"
    ] == {"memo.md": "alpha.md"}


@pytest.mark.parametrize(
    "content",
    ["not JSON", "{}", '{"memo.md": false}', '{"memo.md": null, "extra": "x"}'],
)
def test_invalid_mapping_is_a_recorded_grading_failure(
    matcher: Any, endpoint: _Endpoint, content: str
) -> None:
    endpoint.content = content
    with pytest.raises(InfrastructureError, match="Invalid filename-helper"):
        matcher("Match files", _schema())
    result = read_json(matcher.artifact_dir / "matches" / "call-0001.json")
    assert result["status"] == "infrastructure_failed"


@pytest.mark.parametrize("returned_model", [None, "other-model"])
def test_each_helper_completion_rejects_missing_or_wrong_model(
    matcher: Any, endpoint: _Endpoint, returned_model: str | None
) -> None:
    endpoint.returned_model = returned_model
    with pytest.raises(IntegrityError, match="response returned"):
        matcher("Match files", _schema())
    assert (matcher.artifact_dir / "model_calls" / "call-0001" / "error.json").is_file()


def test_failed_preflight_is_not_retried_or_converted_to_no_match(
    matcher: Any, endpoint: _Endpoint
) -> None:
    endpoint.reject_catalog = True
    with pytest.raises(IntegrityError, match="absent"):
        matcher("Match files", _schema())
    original_requests = list(endpoint.requests)
    original_proof = (matcher.artifact_dir / "model-preflight.json").read_bytes()
    with pytest.raises(IntegrityError, match="absent"):
        matcher("Match files", _schema())
    assert endpoint.requests == original_requests
    assert (
        matcher.artifact_dir / "model-preflight.json"
    ).read_bytes() == original_proof
    assert not (matcher.artifact_dir / "model_calls").exists()


def test_helper_timeout_stops_native_scoring_before_any_rubric_call(
    tmp_path: Path, matcher: Any, endpoint: _Endpoint
) -> None:
    output = tmp_path / "run" / "output"
    output.mkdir(parents=True)
    (output / "alpha.md").write_text("Analysis.")
    (output / "beta.md").write_text("Notes.")
    judge = MagicMock()
    endpoint.fail_generation = True
    with pytest.raises(InfrastructureError, match="timed out"):
        score_rubric(
            [
                {
                    "id": "C-1",
                    "title": "Analysis",
                    "match_criteria": "Review memo.",
                    "deliverables": ["memo.md"],
                }
            ],
            tmp_path / "run",
            judge,
            "Test",
            1,
            deliverable_matcher=matcher,
        )
    judge.evaluate_from_file.assert_not_called()
    assert (matcher.artifact_dir / "model_calls" / "call-0001" / "error.json").is_file()


def test_valid_null_remains_a_normal_rubric_outcome(
    tmp_path: Path, matcher: Any, endpoint: _Endpoint
) -> None:
    output = tmp_path / "run" / "output"
    output.mkdir(parents=True)
    (output / "alpha.md").write_text("Analysis.")
    (output / "beta.md").write_text("Notes.")
    endpoint.content = '{"memo.md": null}'
    judge = MagicMock()
    judge.evaluate_from_file.return_value = {
        "verdict": "fail",
        "reasoning": "Missing memo",
    }
    result = score_rubric(
        [
            {
                "id": "C-1",
                "title": "Analysis",
                "match_criteria": "Review memo.",
                "deliverables": ["memo.md"],
            }
        ],
        tmp_path / "run",
        judge,
        "Test",
        1,
        deliverable_matcher=matcher,
    )
    assert result.score == 0.0
    assert result.n_grading_errors == 0
    judge.evaluate_from_file.assert_called_once()


def test_non_loopback_endpoint_is_rejected_before_any_request(
    tmp_path: Path, matcher_module: ModuleType, endpoint: _Endpoint
) -> None:
    with pytest.raises(ValueError, match="loopback"):
        matcher_module.CopilotDeliverableMatcher(
            settings=ModelSettings(
                model="test-helper", max_tokens=1024, temperature=0.0
            ),
            transport=TransportSettings(base_url="http://192.0.2.1:3141/v1"),
            api_key="offline-placeholder",
            artifact_root=tmp_path / "helper-artifacts",
        )
    assert endpoint.requests == []


@pytest.mark.parametrize(
    ("max_tokens", "temperature"),
    [(1023, 0.0), (1025, 0.0), (1024, 0.2)],
)
def test_native_helper_budget_and_temperature_are_preserved(
    tmp_path: Path,
    matcher_module: ModuleType,
    endpoint: _Endpoint,
    max_tokens: int,
    temperature: float,
) -> None:
    with pytest.raises(ConfigurationError, match="max_tokens=1024"):
        matcher_module.CopilotDeliverableMatcher(
            settings=ModelSettings(
                model="test-helper",
                max_tokens=max_tokens,
                temperature=temperature,
            ),
            transport=TransportSettings(base_url="http://127.0.0.1:3141/v1"),
            api_key="offline-placeholder",
            artifact_root=tmp_path / "helper-artifacts",
        )
    assert endpoint.requests == []
