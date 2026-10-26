"""NexAU tools of the RRSI roles. Each reads its inputs from the agent
context set by the algorithm and delegates to submission.py."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from nexau.archs.main_sub.agent_state import AgentState

from ...meta import dispatch
from . import submission

CANDIDATE_CONTEXT_KEYS = (
    "workspace_dir",
    "rrsi_base_commit",
    "rrsi_edit_budget",
    "rrsi_reserved_untried",
    "checkpoint",
    "parent_checkpoint",
    "parent_harness_dir",
    "harness_manifest_path",
    "project_root",
    "config_path",
    "training_task_ids",
    "parent_runtime_owned_hashes",
    "meta_output_dir",
)


def rrsi_digest_tasks(
    requests: list[dict[str, Any]],
    agent_state: AgentState | None = None,
) -> dict[str, Any]:
    token = str(_context(agent_state, "rrsi_dispatch_token"))
    try:
        digests = dispatch.call(token, requests)
    except ValueError as exc:
        return submission.error(str(exc))
    return {"ok": True, "digests": digests}


def rrsi_submit_report(
    report_json: str,
    agent_state: AgentState | None = None,
) -> dict[str, Any]:
    return submission.submit_report(_output_dir(agent_state), report_json)


def rrsi_submit_digest(
    digest_json: str,
    agent_state: AgentState | None = None,
) -> dict[str, Any]:
    return submission.submit_digest(
        _output_dir(agent_state),
        task=str(_context(agent_state, "rrsi_task")),
        lens=str(_context(agent_state, "rrsi_lens")),
        digest_json=digest_json,
    )


def rrsi_submit_review(
    verdict: str,
    reasons: list[str],
    risk_notes: list[str],
    agent_state: AgentState | None = None,
) -> dict[str, Any]:
    return submission.submit_review(
        _output_dir(agent_state),
        verdict=verdict,
        reasons=reasons,
        risk_notes=risk_notes,
    )


def rrsi_submit_candidate(
    summary: str,
    edits: list[dict[str, Any]],
    agent_state: AgentState | None = None,
) -> dict[str, Any]:
    context = {
        key: _context(agent_state, key, required=False)
        for key in CANDIDATE_CONTEXT_KEYS
    }
    missing = [
        key
        for key, value in context.items()
        if value is None and key != "rrsi_reserved_untried"
    ]
    if missing:
        raise RuntimeError(f"required RRSI proposer context is missing: {missing}")
    try:
        return submission.submit_candidate(
            context=context,
            summary=summary,
            edits=[dict(edit) for edit in edits],
        )
    except Exception as exc:  # report, so the agent can fix and resubmit
        return submission.error(f"{type(exc).__name__}: {exc}")


def _output_dir(agent_state: AgentState | None) -> Path:
    return Path(str(_context(agent_state, "meta_output_dir"))).resolve()


def _context(
    agent_state: AgentState | None,
    key: str,
    *,
    required: bool = True,
) -> Any:
    if agent_state is None:
        raise RuntimeError("NexAU did not provide agent state")
    value = agent_state.get_context_value(key)
    if value is None and required:
        raise RuntimeError(f"required RRSI context is missing: {key}")
    if isinstance(value, str) and key in {"training_task_ids"}:
        return json.loads(value)
    return value
