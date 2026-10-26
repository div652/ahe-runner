from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml

from ahe_runner.adapter import (
    EvaluationResult,
    EvaluationTrialFact,
    MetricSummary,
    MetricValue,
    SplitName,
)
from ahe_runner.algorithms.rrsi import submission
from ahe_runner.algorithms.rrsi.components import normalize_tag, novelty
from ahe_runner.algorithms.rrsi.history import History, exploration, stall_flag
from ahe_runner.algorithms.rrsi.objective import (
    calibrate_bootstrap,
    calibrate_repeats,
    check_metric,
    weighted_score,
)
from ahe_runner.algorithms.rrsi.schedule import edit_budget
from ahe_runner.algorithms.rrsi.selection import Candidate, Weights, select_round
from ahe_runner.algorithms.rrsi.settings import RrsiSettings
from ahe_runner.cli import main as cli_main
from ahe_runner.config import ModelPricing, load_config
from ahe_runner.controller import CampaignController
from ahe_runner.cost import call_cost, call_usage, evaluation_cost
from ahe_runner.errors import ConfigurationError, IntegrityError
from ahe_runner.io import atomic_write_json, read_json

from .conftest import make_example_project

PAPER = Weights(beta0=0.10, beta1=35.4, w_s=1414.0, w_c=15.0, w_n=0.5)


def _rrsi_project(tmp_path: Path, *, rounds: int = 2, **settings: Any) -> Path:
    config_path = make_example_project(tmp_path, rounds=rounds)
    value = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    value["algorithm"] = {
        "name": "rrsi",
        "settings": {"cost": "disabled", **settings},
    }
    config_path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")
    return config_path


# -- pure method ---------------------------------------------------------------


def test_edit_budget_anneals_like_the_paper() -> None:
    table = [edit_budget(t, 20, 1, 3) for t in range(20)]
    # The reference implementation's ceiling reaches b_min only at t = T, so
    # the last of T rounds still allows 2 edits.
    assert table == [3] * 10 + [2] * 10
    assert edit_budget(20, 20, 1, 3) == 1
    assert edit_budget(0, 2, 1, 3) == 3 and edit_budget(1, 2, 1, 3) == 2


def test_selection_floor_cost_rule_and_within_band() -> None:
    def pick(
        *candidates: Candidate,
        cost: float | None = 100.0,
        counts: dict[str, int] | None = None,
        score: float = 0.80,
        best: float = 0.81,
    ):
        return select_round(
            list(candidates),
            incumbent_score=score,
            incumbent_cost=cost,
            best_score=best,
            delta=0.004,
            weights=PAPER,
            accepted_counts=counts or {},
        )

    # A clear gain within the cost budget wins.
    winner, decisions = pick(Candidate("A", "C1", ["prompt"], 0.82, 120.0))
    assert winner is not None and decisions[0].admissible
    # The same gain at too much extra cost: budget 0.10 + 35.4 * 0.02 = 0.808.
    winner, decisions = pick(Candidate("A", "C1", ["prompt"], 0.82, 190.0))
    assert winner is None and "cost rule failed" in decisions[0].reason
    # Below S* - delta never passes, even when it saves cost.
    winner, decisions = pick(Candidate("A", "C1", ["prompt"], 0.80, 50.0))
    assert winner is None and "floor" in decisions[0].reason
    # Within the band (no gain): a prompt edit must save cost ...
    neutral = {"score": 0.805, "best": 0.806}
    winner, decisions = pick(Candidate("A", "C1", ["prompt"], 0.805, 101.0), **neutral)
    assert winner is None and "within delta" in decisions[0].reason
    winner, _ = pick(Candidate("A", "C1", ["prompt"], 0.805, 99.0), **neutral)
    assert winner is not None
    # ... while a first structural component earns novelty credit
    # (w_n / w_c = 3.3% of cost) ...
    winner, decisions = pick(Candidate("A", "C1", ["skill"], 0.805, 101.0), **neutral)
    assert winner is not None and decisions[0].novelty == 1
    _, decisions = pick(
        Candidate("A", "C1", ["skill"], 0.805, 101.0),
        counts={"skill": 1},
        **neutral,
    )
    assert decisions[0].novelty == 0 and not decisions[0].admissible
    # ... and a within-band gain buys cost in proportion: at dS = 0.002 the
    # allowance is 1414 * 0.002 / 15 = 18.9%.
    winner, _ = pick(Candidate("A", "C1", ["prompt"], 0.807, 118.0), **neutral)
    assert winner is not None
    winner, _ = pick(Candidate("A", "C1", ["prompt"], 0.807, 120.0), **neutral)
    assert winner is None
    # Gate failures are never admissible; the best admissible score wins.
    winner, decisions = pick(
        Candidate("A", None, ["prompt"], gate_failure="critic_reject"),
        Candidate("B", "C2", ["prompt"], 0.83, 100.0),
        Candidate("C", "C3", ["prompt"], 0.84, 100.0),
    )
    assert winner is not None and winner.checkpoint == "C3"
    assert decisions[0].reason == "critic_reject"
    # No cost measured: dC = 0.
    winner, _ = pick(Candidate("A", "C1", ["prompt"], 0.82, None), cost=None)
    assert winner is not None


def test_history_summaries_and_exploration() -> None:
    history = History()
    history.append_candidate(
        t=0,
        variant="A",
        checkpoint="C1",
        edits=[{"id": "E1", "component": "prompt", "hypothesis": "h"}],
        outcome="ACCEPTED",
        delta_s=0.01,
        delta_c=0.0,
        accepted=True,
        score=0.81,
        cost=1.0,
    )
    history.append_candidate(
        t=1,
        variant="A",
        checkpoint="C3",
        edits=[{"id": "E1", "component": "middleware-typo"}],
        outcome="REJECTED",
        delta_s=-0.01,
        delta_c=0.0,
        accepted=False,
        score=0.80,
        cost=1.0,
    )
    history.append_candidate(
        t=1,
        variant="B",
        checkpoint=None,
        edits=[{"id": "E1", "component": "skill"}],
        outcome="critic_reject",
        delta_s=None,
        delta_c=None,
        accepted=False,
        score=None,
        cost=None,
    )
    assert history.tried() == {"prompt"}
    assert history.accepted_counts()["prompt"] == 1
    assert history.recent_yield(6, 4) == {"prompt": -math.inf}
    prune = history.prune_set(6, 4)
    assert prune[0]["component"] == "prompt"
    assert prune[0]["accepted_edits_in_incumbent"][0]["checkpoint"] == "C1"
    assert stall_flag([0.8, 0.8, 0.801, 0.802], 3, 3, 0.004) == 1
    assert stall_flag([0.8, 0.8, 0.801, 0.9], 3, 3, 0.004) == 0
    assert stall_flag([0.8, 0.8], 1, 3, 0.004) == 0
    explore = exploration(1, {"prompt"}, 1)
    assert "STALL" in explore["text"] and "skill" in explore["untried"]


def test_component_tags_follow_the_changed_files() -> None:
    def rows(*items: tuple[str, str, str]) -> list[dict[str, str]]:
        return [{"path": p, "change": c, "component": k} for p, c, k in items]

    prompt = rows(("systemprompt.md", "modified", "system_prompt"))
    assert normalize_tag("prompt", prompt) == ("prompt", None)
    tag, reason = normalize_tag("skill", prompt)
    assert tag == "prompt" and "re-tagged" in str(reason)
    middleware = rows(("middleware/x.py", "modified", "middleware"))
    assert normalize_tag("context_mgmt", middleware)[0] == "context_mgmt"
    new_tool = rows(
        ("tools/a.py", "added", "tool_implementation"),
        ("tool_descriptions/a.tool.yaml", "added", "tool_description"),
        ("agent.yaml", "modified", "agent_config"),
    )
    assert normalize_tag("client_tool", new_tool)[0] == "client_tool"
    assert normalize_tag("prompt", new_tool)[0] == "client_tool"
    edited_tool = rows(("tools/a.py", "modified", "tool_implementation"))
    assert normalize_tag("client_tool", edited_tool)[0] == "control_flow"
    config_only = rows(("agent.yaml", "modified", "agent_config"))
    assert normalize_tag("config", config_only)[0] == "config"
    skill = rows(
        ("skills/s/SKILL.md", "added", "skill"),
        ("agent.yaml", "modified", "agent_config"),
    )
    assert normalize_tag("skill", skill)[0] == "skill"
    assert novelty(["skill", "prompt", "skill"], {}) == 1
    assert novelty(["skill"], {"skill": 2}) == 0


def test_settings_derive_paper_weights_and_windows() -> None:
    settings = RrsiSettings()
    weights = settings.weights(0.004)
    assert weights["w_s"] == pytest.approx(1414.0, rel=1e-3)
    assert weights["w_c"] == 15.0
    assert weights["w_n"] == pytest.approx(0.5)
    assert settings.windows(20) == (3, 4)
    assert settings.windows(5) == (2, 3)
    assert RrsiSettings(stall_window=5).windows(20) == (5, 4)
    explicit = RrsiSettings.model_validate({"within_band": {"w_s": 0.0}})
    assert explicit.weights(0.017)["w_s"] == 0.0
    with pytest.raises(ValueError):
        RrsiSettings.model_validate({"noise": {"method": "fixed"}})
    with pytest.raises(ValueError):
        RrsiSettings(edit_budget_min=4, edit_budget_max=3)


def test_weighted_score_and_noise_calibration() -> None:
    rows = {
        "a": [(1.0, 20.0), (0.5, 20.0)],
        "b": [(0.0, 80.0), (0.25, 80.0)],
    }
    # (20 + 10 + 0 + 20) / 200: criteria passed over criteria graded.
    assert weighted_score(rows) == pytest.approx(0.25)
    calibration = calibrate_bootstrap(rows, trials=2, z=2.0, resamples=500, seed=1)
    assert calibration["delta"] > 0
    assert calibration["delta_by_trials"]["4"] < calibration["delta"]
    assert (
        calibrate_bootstrap(
            {"a": [(1.0, 1.0), (1.0, 1.0)]}, trials=2, z=2.0, resamples=200, seed=1
        )["delta"]
        == 0.0
    )
    with pytest.raises(ConfigurationError):
        calibrate_bootstrap(rows, trials=1, z=2.0, resamples=200, seed=1)
    repeats = calibrate_repeats([0.80, 0.81], z=2.0)
    assert repeats["delta"] == pytest.approx(0.02)
    summary = MetricSummary(
        primary=MetricValue(name="primary", value=0.3),
        metrics={"pooled": 0.25},
        task_count=2,
        rollout_count=4,
    )
    result = EvaluationResult(
        evaluation_id="e",
        request_sha256="r",
        checkpoint="C0",
        split=SplitName.TRAIN,
        summary=summary,
        trial_facts=[],
        artifact_hashes={},
    )
    check_metric(0.25, result, "pooled")
    check_metric(0.3, result, "primary")
    with pytest.raises(IntegrityError):
        check_metric(0.26, result, "pooled")
    with pytest.raises(ValueError):
        EvaluationTrialFact(
            unit_id="u",
            outcome="pass",
            termination_reason="done",
            diagnostic_class="passing",
            weight=0.0,
        )


# -- cost ----------------------------------------------------------------------


def test_priced_cost_uses_categories_tiers_and_completed_attempts(
    tmp_path: Path,
) -> None:
    pricing = ModelPricing(
        input_per_million=20,
        cached_input_per_million=2,
        cache_write_per_million=25,
        output_per_million=120,
        long_context={
            "above_prompt_tokens": 200_000,
            "input_per_million": 40,
            "cached_input_per_million": 4,
            "cache_write_per_million": 50,
            "output_per_million": 180,
        },
    )
    responses_usage = {
        "input_tokens": 1_000_000,
        "input_tokens_details": {
            "cached_tokens": 400_000,
            "cache_write_tokens": 100_000,
        },
        "output_tokens": 10_000,
        "output_tokens_details": {"reasoning_tokens": 6_000},
    }
    usage = call_usage({"usage": responses_usage})
    assert usage is not None and usage.uncached_input == 500_000
    # Long-context tier: 0.5M * 40 + 0.4M * 4 + 0.1M * 50 + 0.01M * 180.
    assert call_cost(usage, pricing) == pytest.approx(20 + 1.6 + 5 + 1.8)
    chat = call_usage({"usage": {"prompt_tokens": 1000, "completion_tokens": 100}})
    assert chat is not None
    assert call_cost(chat, pricing) == pytest.approx((1000 * 20 + 100 * 120) / 1e6)

    evaluation = tmp_path / "evaluation"

    def unit(unit_id: str, attempt: int) -> None:
        atomic_write_json(
            evaluation / "units" / unit_id / "complete.json", {"attempt": attempt}
        )

    def session(name: str, unit_id: str | None, attempt: int | None, calls: int):
        directory = evaluation / "runtime" / name
        atomic_write_json(
            directory / "evaluated_sessions" / f"{name}.json",
            {"session_id": name, "unit_id": unit_id, "attempt": attempt},
        )
        for index in range(calls):
            atomic_write_json(
                directory / "model_calls" / f"call-{index:04d}" / "response.json",
                {
                    "response": {
                        "usage": {"prompt_tokens": 1000, "completion_tokens": 100}
                    }
                },
            )

    unit("u1", 2)
    unit("u2", 1)
    session("u1-1", "u1", 1, 5)  # abandoned attempt: never counted
    session("u1-2", "u1", 2, 1)
    session("u2-1", "u2", 1, 3)
    session("loose", None, None, 7)  # no unit: unattributed
    # A judge's calls, recorded without a session marker, never count.
    atomic_write_json(
        evaluation
        / "runtime"
        / "judge"
        / "model_calls"
        / "call-0000"
        / "response.json",
        {"response": {"usage": {"prompt_tokens": 9, "completion_tokens": 9}}},
    )
    report = evaluation_cost(evaluation, pricing)
    one_call = (1000 * 20 + 100 * 120) / 1e6
    assert report["per_trial"]["u1"]["cost"] == pytest.approx(one_call)
    assert report["per_trial"]["u2"]["cost"] == pytest.approx(3 * one_call)
    assert report["mean_cost_per_trial"] == pytest.approx(2 * one_call)
    assert report["unattributed_sessions"] == 1
    assert report["trials_with_usage"] == 2
    assert evaluation_cost(evaluation, None)["mean_cost_per_trial"] is None


# -- candidate submission ---------------------------------------------------------


def _submission_context(tmp_path: Path) -> tuple[dict[str, Any], Path]:
    import subprocess

    from ahe_runner.harness import load_harness_manifest, validate_harness
    from ahe_runner.workspace import prepare_evolve_workspace

    config_path = make_example_project(tmp_path)
    config = load_config(config_path)
    workspace = prepare_evolve_workspace(
        workspaces_dir=tmp_path / "workspaces",
        checkpoint="C1",
        attempt=1,
        parent_harness_dir=config.harness_dir,
        recovery_output_dir=tmp_path / "recovery",
    )
    base = subprocess.run(
        ["git", "-C", str(workspace), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    output = tmp_path / "output"
    output.mkdir()
    return (
        {
            "workspace_dir": str(workspace),
            "rrsi_base_commit": base,
            "rrsi_edit_budget": 1,
            "rrsi_reserved_untried": [],
            "checkpoint": "C1",
            "parent_checkpoint": "C0",
            "parent_harness_dir": str(config.harness_dir),
            "harness_manifest_path": str(config.harness_manifest_path),
            "project_root": str(config.project_root),
            "config_path": str(config.config_path),
            "training_task_ids": ["train-verify-result"],
            "parent_runtime_owned_hashes": validate_harness(
                config.harness_dir,
                load_harness_manifest(config.harness_manifest_path),
                project_root=config.project_root,
            )["runtime_owned_hashes"],
            "meta_output_dir": str(output),
        },
        workspace,
    )


def _edit(**updates: Any) -> dict[str, Any]:
    return {
        "id": "E1",
        "type": "improvement",
        "component": "prompt",
        "paths": ["systemprompt.md"],
        "hypothesis": "h",
        "targets_mode": "m",
        "why_not_lower_lever": "w",
        "trigger_condition": "t",
        "predicted_affected": [],
        "retroactive_check": "r",
        "regression_risk": "x",
        "evidence": ["e"],
        **updates,
    }


def test_candidate_submission_enforces_the_contract(tmp_path: Path) -> None:
    context, workspace = _submission_context(tmp_path)
    prompt = workspace / "systemprompt.md"
    empty = submission.submit_candidate(context=context, summary="s", edits=[_edit()])
    assert not empty["ok"] and any("no harness change" in e for e in empty["errors"])

    prompt.write_text(prompt.read_text() + "\n- Check results.\n")
    over = submission.submit_candidate(
        context=context,
        summary="s",
        edits=[_edit(), _edit(id="E2", paths=["LongTermMEMORY.md"])],
    )
    assert any("edit budget" in e for e in over["errors"])
    uncovered = submission.submit_candidate(
        context=context, summary="s", edits=[_edit(paths=["LongTermMEMORY.md"])]
    )
    assert any("not covered" in e for e in uncovered["errors"])
    slot = submission.submit_candidate(
        context={**context, "rrsi_reserved_untried": ["skill"]},
        summary="s",
        edits=[_edit(component="skill")],
    )
    assert any("RESERVED EXPLORATION SLOT" in e for e in slot["errors"])

    accepted = submission.submit_candidate(
        context=context, summary="Add a check", edits=[_edit(component="memory")]
    )
    assert accepted["ok"], accepted
    assert "re-tagged 'prompt'" in accepted["retags"][0]
    manifest = read_json(workspace / "change_manifest.json")
    assert manifest["changes"][0]["component"] == "system_prompt"
    assert manifest["changes"][0]["tag"] == "prompt"
    candidate = submission.load_candidate(Path(context["meta_output_dir"]))
    assert candidate is not None and candidate["head"] == accepted["head"]
    assert candidate["edits"][0]["files"][0]["component"] == "system_prompt"

    leaked = (workspace / "systemprompt.md").read_text() + "\n- train-verify-result\n"
    (workspace / "systemprompt.md").write_text(leaked)
    rejected = submission.submit_candidate(
        context=context, summary="Leak", edits=[_edit()]
    )
    assert not rejected["ok"]
    assert any("validation failed" in e for e in rejected["errors"])


def test_role_deliverables_are_validated(tmp_path: Path) -> None:
    assert not submission.submit_report(tmp_path, "[]")["ok"]
    assert submission.submit_report(
        tmp_path,
        json.dumps(
            {
                "failure_modes": [
                    {"mode": "a", "n_tasks": 1},
                    {"mode": "b", "n_tasks": 3},
                ],
                "capability_gaps": [],
                "success_habits": [],
            }
        ),
    )["ok"]
    assert (
        read_json(tmp_path / "analysis_report.json")["failure_modes"][0]["mode"] == "b"
    )
    too_long = json.dumps({"blocker": "x" * 7000})
    assert not submission.submit_digest(
        tmp_path, task="t", lens="failure", digest_json=too_long
    )["ok"]
    assert not submission.submit_digest(
        tmp_path, task="t", lens="failure", digest_json='{"blocker": "x"}'
    )["ok"]
    assert not submission.submit_review(
        tmp_path, verdict="reject", reasons=[], risk_notes=[]
    )["ok"]


# -- the algorithm through the runner --------------------------------------------


def test_rrsi_campaign_runs_selects_and_replays(tmp_path: Path) -> None:
    controller = CampaignController(load_config(_rrsi_project(tmp_path)))
    assert controller.selection_policy == "algorithm"
    status = controller.run()
    campaign = Path(status["campaign_dir"])
    assert status["algorithm"] == "rrsi"
    frontier = read_json(campaign / "algorithm" / "reports" / "frontier.json")
    # The deterministic example: every new rule is a clear gain.
    assert frontier["trajectory"] == [0.25, 0.5, 0.75]
    assert frontier["accepted_path"] == ["C0", "C1", "C3"]
    assert frontier["calibration"]["delta"] == 0.0
    assert [r["b_t"] for r in frontier["rounds"]] == [3, 2]
    lineage = {
        label: read_json(campaign / "checkpoints" / label / "checkpoint.json")[
            "parent_checkpoint"
        ]
        for label in ("C1", "C2", "C3", "C4")
    }
    assert lineage == {"C1": "C0", "C2": "C0", "C3": "C1", "C4": "C1"}
    assert status["selection"]["selected_checkpoint"] == "C3"
    assert status["selection"]["validation_summaries"] == {}
    assert set(status["final_test"]["test_summaries"]) == {"C0", "C3"}
    assert not (campaign / "evaluations" / "C3-validation").exists()
    history = (campaign / "algorithm" / "reports" / "history.jsonl").read_text()
    assert history.count('"ACCEPTED"') == 2 and history.count('"LOST"') == 2

    stages = sorted(path.name for path in (campaign / "stages").iterdir())
    again = CampaignController(load_config(controller.config.config_path)).run()
    assert again["final_test"] == status["final_test"]
    assert sorted(path.name for path in (campaign / "stages").iterdir()) == stages

    with pytest.raises(ConfigurationError, match="AHE command"):
        controller.run_round(1)
    with pytest.raises(ConfigurationError, match="does not support extending"):
        controller.extend_rounds(reason="more")


def test_rrsi_validation_policy_and_configuration_checks(tmp_path: Path) -> None:
    config_path = _rrsi_project(tmp_path / "validation", rounds=1)
    value = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    value["selection"] = {"policy": "validation"}
    config_path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")
    status = CampaignController(load_config(config_path)).run()
    assert status["selection_policy"] == "validation"
    assert set(status["selection"]["validation_summaries"]) == {"C0", "C1"}
    assert status["selection"]["algorithm_outcome"]["nominees"] == ["C0", "C1"]

    priced = make_example_project(tmp_path / "priced")
    value = yaml.safe_load(priced.read_text(encoding="utf-8"))
    value["algorithm"] = {"name": "rrsi"}
    value["evaluation"]["train_trials"] = 1
    priced.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")
    controller = CampaignController(load_config(priced))
    problems = controller.validate_project()["algorithm"]["problems"]
    assert any("models.evaluated.pricing" in problem for problem in problems)
    assert any("train_trials >= 2" in problem for problem in problems)
    assert controller.conformance()["valid"] is False
    with pytest.raises(ConfigurationError, match="cannot run this campaign"):
        controller.run()

    for bad, message in (
        ({"name": "nope"}, "unknown algorithm"),
        ({"name": "rrsi", "settings": {"typo": 1}}, "invalid algorithm.settings"),
        (
            {
                "name": "rrsi",
                "role_supplements": {"debugger": "meta_supplements/debugger.md"},
            },
            "unknown roles",
        ),
    ):
        path = make_example_project(tmp_path / message.replace(" ", "-"))
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
        value["algorithm"] = bad
        path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")
        with pytest.raises(ConfigurationError, match=message):
            CampaignController(load_config(path))


def test_rrsi_identity_and_cli_status(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = _rrsi_project(tmp_path, rounds=1)
    controller = CampaignController(load_config(config_path))
    identity = controller._campaign_identity()
    assert identity["algorithm"]["name"] == "rrsi"
    assert identity["algorithm"]["settings"]["cost"] == "disabled"
    assert identity["algorithm"]["resources"]
    ahe = CampaignController(load_config(make_example_project(tmp_path / "ahe")))
    assert "algorithm" not in ahe._campaign_identity()
    assert "algorithm" not in ahe._campaign_identity()["config"]["document"]
    assert cli_main(["validate", "--config", str(config_path)]) == 0
    assert '"selection_policy": "algorithm"' in capsys.readouterr().out


def test_kernel_allocates_stable_labels(tmp_path: Path) -> None:
    controller = CampaignController(load_config(_rrsi_project(tmp_path)))
    controller.prepare()
    kernel = controller.kernel
    assert kernel.allocate_checkpoint("a") == "C1"
    assert kernel.allocate_checkpoint("b") == "C2"
    assert kernel.allocate_checkpoint("a") == "C1"
    with pytest.raises(IntegrityError):
        kernel.freeze_candidate(
            label="C1", parent="C0", workspace=tmp_path, validation={"valid": False}
        )


# -- a real NexAU role run against a mocked model endpoint -----------------------------


def test_nexau_backend_runs_an_rrsi_role_with_its_tools(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openai import OpenAI

    import ahe_runner.meta.model_client as model_client_module
    from ahe_runner.algorithms.rrsi import PROMPTS, _tool
    from ahe_runner.meta import RoleSpec
    from ahe_runner.meta.nexau_backend import NexAUMetaBackend

    config_path = make_example_project(tmp_path)
    value = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    value["meta"]["backend"] = "nexau"
    value["meta"]["explore"]["web_search_backend"] = "ddgs"
    value["models"]["meta"].update({"api_mode": "responses", "temperature": None})
    value["transport"]["api_key_env"] = "AHE_TEST_META_KEY"
    config_path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")
    monkeypatch.setenv("AHE_TEST_META_KEY", "synthetic-meta-key")
    controller = CampaignController(load_config(config_path))
    model = controller.config.document.models.meta.model
    requests: list[dict[str, Any]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        requests.append(payload)
        arguments = (
            {"verdict": "reject", "reasons": [], "risk_notes": []}
            if len(requests) == 1
            else {"verdict": "accept", "reasons": [], "risk_notes": ["ok"]}
        )
        output = [
            {
                "type": "function_call",
                "id": f"fc-{len(requests)}",
                "call_id": f"call-{len(requests)}",
                "name": "rrsi_submit_review",
                "arguments": json.dumps(arguments),
                "status": "completed",
            }
        ]
        return httpx.Response(
            200,
            json={
                "id": "resp",
                "object": "response",
                "created_at": 1,
                "model": model,
                "status": "completed",
                "output": output,
                "parallel_tool_calls": True,
                "tool_choice": "auto",
                "tools": [],
                "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
            },
        )

    def build(transport, api_key, *, client_id=None):  # type: ignore[no-untyped-def]
        del transport, client_id
        return OpenAI(
            base_url="http://127.0.0.1:39298/v1",
            api_key=api_key,
            max_retries=0,
            http_client=httpx.Client(transport=httpx.MockTransport(handle)),
        )

    monkeypatch.setattr(model_client_module, "build_openai_client", build)
    monkeypatch.setattr(NexAUMetaBackend, "_preflight", lambda self, **_: {})
    review_dir = tmp_path / "candidate"
    review_dir.mkdir()
    (review_dir / "diff.patch").write_text("+ a general rule\n", encoding="utf-8")
    controller.meta.run_role(
        RoleSpec(
            role="critic",
            agent_name="RRSI-Critic",
            description="test",
            prompt_path=PROMPTS / "critic.md",
            query="Review the candidate.",
            runtime_dir=tmp_path / "critic",
            max_iterations=4,
            default_root="candidate",
            read_roots={"candidate": review_dir},
            tools=("read_file",),
            algorithm_tools=(_tool("rrsi_submit_review"),),
            stop_tools=("rrsi_submit_review",),
        )
    )
    # The first call was an invalid rejection, returned as an error, and the
    # agent continued; the second, valid call ended the run.
    assert len(requests) == 2
    assert read_json(tmp_path / "critic" / "review.json")["verdict"] == "accept"
    tool_names = {tool.get("name") for tool in requests[0]["tools"]}
    assert {"read_file", "rrsi_submit_review"} <= tool_names
    # The protected role prompt reaches the model (regression: an empty
    # NexAU system prompt silently replaced it with NexAU's default).
    assert "RRSI Leakage Critic" in requests[0]["instructions"]
    assert "UNDECLARED BUNDLING" in requests[0]["instructions"]
