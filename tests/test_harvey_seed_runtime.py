"""Offline check that AHE's real NexAU runtime runs the seed Harvey harness.

The model is scripted (no network) and the sandbox is the LAB-path fake, so
this exercises the real harness YAML, tool bindings, session-bound workspace
handle (AHE-006), and the ``finish`` stop tool through the adapter's own
``_run_session``.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from harvey_ahe import adapter as adapter_module
from harvey_ahe.workspace import WorkspaceHandle
from openai import OpenAI

import ahe_runner.meta.preflight as preflight_module
import ahe_runner.runtime as runtime_module
from ahe_runner.adapter import BenchmarkInterface
from ahe_runner.config import ModelSettings, TransportSettings
from ahe_runner.harness import load_harness_manifest
from ahe_runner.runtime import EvaluatedAgentRuntime

from .harvey_fakes import HARNESS_DIR, PROJECT_ROOT, started_sandbox
from .test_runtime import (
    FakeRecordedClient,
    _completion,
    _PreflightEndpoint,
    _tool_call,
)

MODEL = "example-evaluated-model"
SEED_TOOLS = {"bash", "edit", "finish", "glob", "grep", "read", "write"}


@pytest.fixture
def offline_endpoint(monkeypatch: pytest.MonkeyPatch) -> Iterator[_PreflightEndpoint]:
    endpoint = _PreflightEndpoint()
    clients: list[OpenAI] = []

    def make_client(**kwargs: Any) -> OpenAI:
        client = OpenAI(
            **kwargs,
            http_client=httpx.Client(transport=httpx.MockTransport(endpoint.handle)),
        )
        clients.append(client)
        return client

    monkeypatch.setattr(preflight_module, "OpenAI", make_client)
    yield endpoint
    for client in clients:
        client.close()


def _call(name: str, arguments: dict[str, Any], call_id: str) -> Any:
    return _completion(model=MODEL, tool_calls=[_tool_call(name, arguments, call_id)])


def test_seed_harness_runs_through_the_session_bound_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    offline_endpoint: _PreflightEndpoint,
) -> None:
    trial = tmp_path / "trial"
    sandbox = started_sandbox(trial, documents={"lease.txt": "Rent is $10."})
    handle = WorkspaceHandle(sandbox, shell_timeout=60)
    FakeRecordedClient.instances = []
    FakeRecordedClient.scenarios = [
        [
            _call("read", {"file_path": "lease.txt"}, "call-read"),
            _call("write", {"file_path": "memo.md", "content": "Rent: $10."}, "call-w"),
            _call("finish", {"summary": "early", "deliverables": ["memo.docx"]}, "f1"),
            _call("finish", {"summary": "Memo written.", "deliverables": ["memo.md"]}, "f2"),
        ]
    ]
    monkeypatch.setattr(runtime_module, "RecordedExactModelClient", FakeRecordedClient)
    artifact_root = tmp_path / "evaluation" / "runtime"
    runtime = EvaluatedAgentRuntime(
        harness_dir=HARNESS_DIR,
        manifest=load_harness_manifest(PROJECT_ROOT / "harness_manifest.yaml"),
        benchmark_interface=BenchmarkInterface(
            description="Harvey offline fixture.",
            input_kinds=("prompt", "data", "environment"),
            tool_delivery="none",
        ),
        model=ModelSettings(model=MODEL, max_tokens=4096, temperature=0.0),
        transport=TransportSettings(
            base_url="http://127.0.0.1:9/v1",
            model_catalog="required",
            negative_control="required",
        ),
        api_key="offline-key",
        artifact_root=artifact_root,
    )
    working_directory = trial / "nexau_cwd"
    working_directory.mkdir()
    try:
        output, error = adapter_module._run_session(
            runtime=runtime,  # type: ignore[arg-type]
            session_id="unit-1-1",
            artifact_dir=artifact_root / "unit-1" / "attempt-1",
            working_directory=working_directory,
            handle=handle,
            instructions="State the rent in memo.md.",
        )
    finally:
        runtime.close()

    assert error is None and output is not None
    assert handle.finished
    assert handle.finish_summary == "Memo written."
    assert (trial / "output" / "memo.md").read_text(encoding="utf-8") == "Rent: $10."
    run = adapter_module._AgentRun(
        output=output,
        error=None,
        finished=handle.finished,
        finish_summary=handle.finish_summary,
        wall_clock_seconds=0.0,
    )
    assert adapter_module._termination_reason(run) == "finish_tool"
    counts, turns = adapter_module._tool_counts(adapter_module._trace_messages(output))
    assert counts == {"finish": 2, "read": 1, "write": 1}
    assert turns == 4

    (client,) = FakeRecordedClient.instances
    assert len(client.calls) == 4, "an accepted finish must end the run"
    first = client.calls[0]
    assert {tool["function"]["name"] for tool in first["tools"]} == SEED_TOOLS
    system = json.dumps(first["messages"][0])
    assert "You are an AI agent executing a task provided by the user" in system
    assert "State the rent in memo.md." in json.dumps(first["messages"])
    assert "Rent is $10." in json.dumps(client.calls[1]["messages"])
    assert "Not finished" in json.dumps(client.calls[3]["messages"])
    assert (artifact_root / "unit-1" / "attempt-1" / "runtime_manifest.json").is_file()
    assert not working_directory.joinpath("NEXAU.md").exists()
