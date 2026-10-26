from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from openai import APIStatusError

from ahe_runner.cli import main as cli_main
from ahe_runner.config import load_config
from ahe_runner.controller import CampaignController, _refuse_reentered_main
from ahe_runner.errors import (
    PROVIDER_BLOCKED_MARKER,
    ConfigurationError,
    IntegrityError,
)
from ahe_runner.fixed_evaluation import compare_evaluations, evaluate_fixed
from ahe_runner.io import read_json
from ahe_runner.model_requests import provider_rejection_message
from ahe_runner.supervisor import supervise

from .conftest import make_example_project


def _error(kind: str, message: str) -> str:
    payload = {"status": "error", "type": kind, "message": message}
    return "progress line\n" + json.dumps(payload, indent=2, sort_keys=True) + "\n"


def test_supervisor_resumes_infrastructure_stops_only(tmp_path: Path) -> None:
    config_path = make_example_project(tmp_path, rounds=0)
    campaign_dir = load_config(config_path).campaign_dir
    outcomes = [
        (2, "", _error("StageInterruptedError", "endpoint timed out")),
        (-9, "", "Killed\n"),
        (0, json.dumps({"campaign": "done"}, indent=2) + "\n", ""),
    ]
    commands: list[list[str]] = []
    sleeps: list[float] = []

    def launch(command: list[str]) -> tuple[int, str, str]:
        commands.append(command)
        return outcomes.pop(0)

    report = supervise(
        config_path,
        initial_backoff_seconds=10,
        max_backoff_seconds=100,
        sleep=sleeps.append,
        launch=launch,
    )
    assert report["status"] == "completed"
    assert report["launches"] == 3
    assert commands[0][1:] == [
        "-m",
        "ahe_runner",
        "run",
        "--config",
        str(config_path.resolve()),
    ]
    assert 10 <= sleeps[0] <= 12.5 and 20 <= sleeps[1] <= 25
    log = [
        json.loads(line)
        for line in (campaign_dir / "reports" / "supervisor.jsonl")
        .read_text()
        .splitlines()
    ]
    assert [row["decision"] for row in log] == ["resume", "resume", "completed"]

    for message, kind, expected in (
        (
            f"{PROVIDER_BLOCKED_MARKER}: 403",
            "StageInterruptedError",
            "stop_provider_block",
        ),
        ("stage exhausted 3 attempts", "StageBlockedError", "stop_operator_required"),
        ("campaign identity changed", "IntegrityError", "stop_operator_required"),
    ):
        stopped = supervise(
            config_path,
            sleep=sleeps.append,
            launch=lambda _command, kind=kind, message=message: (
                2,
                "",
                _error(kind, message),
            ),
        )
        assert stopped["status"] == expected

    repeated = supervise(
        config_path,
        max_consecutive_failures=2,
        sleep=lambda _seconds: None,
        launch=lambda _command: (2, "", _error("StageInterruptedError", "down")),
    )
    assert repeated["status"] == "stop_repeated_failures"
    assert repeated["launches"] == 2
    with pytest.raises(ConfigurationError):
        supervise(config_path, initial_backoff_seconds=0)


def test_reentered_operator_script_fails_clearly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import ahe_runner.controller as controller_module

    monkeypatch.setattr(
        controller_module.multiprocessing, "parent_process", lambda: object()
    )
    _refuse_reentered_main()
    namespace = {"__name__": "__mp_main__", "check": _refuse_reentered_main}
    with pytest.raises(ConfigurationError, match="__name__ == '__main__'"):
        exec("check()", namespace)


def test_provider_rejections_are_classified_narrowly() -> None:
    def status(code: int, message: str) -> APIStatusError:
        response = httpx.Response(
            code, request=httpx.Request("POST", "http://127.0.0.1/v1/responses")
        )
        return APIStatusError(message, response=response, body=None)

    assert provider_rejection_message(status(400, "content filtered")) is not None
    assert provider_rejection_message(status(422, "invalid tool schema")) is not None
    assert provider_rejection_message(status(400, "context_length_exceeded")) is None
    assert provider_rejection_message(status(401, "unauthorized")) is None
    assert provider_rejection_message(status(429, "slow down")) is None
    wrapped = RuntimeError("agent failed")
    wrapped.__cause__ = status(400, "content filtered")
    assert provider_rejection_message(wrapped) == "content filtered"


def test_fixed_harness_evaluation_and_comparison(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = make_example_project(tmp_path, rounds=1)
    controller = CampaignController(load_config(config_path))
    status = controller.run()
    campaign_dir = Path(status["campaign_dir"])
    validation = controller.splits[
        next(name for name in controller.splits if name.value == "validation")
    ]
    tasks = tmp_path / "tasks.json"
    tasks.write_text(json.dumps(validation.task_ids[:2]), encoding="utf-8")

    baseline = evaluate_fixed(
        controller,
        output=tmp_path / "eval-c0",
        checkpoint="C0",
        tasks_file=tasks,
        trials=2,
    )
    assert baseline["label"] == "C0"
    assert baseline["split"]["task_count"] == 2
    assert baseline["trials"] == 2
    assert baseline["route"]["wire_api"] == "chat_completions"
    stages = read_json(tmp_path / "eval-c0" / "stages" / "evaluation" / "stage.json")
    assert len(stages["attempts"]) == 1

    # Rerunning the same command reuses the completed evaluation.
    again = evaluate_fixed(
        controller,
        output=tmp_path / "eval-c0",
        checkpoint="C0",
        tasks_file=tasks,
        trials=2,
    )
    assert again["evaluation_id"] == baseline["evaluation_id"]
    with pytest.raises(IntegrityError, match="semantic inputs changed"):
        evaluate_fixed(
            controller,
            output=tmp_path / "eval-c0",
            checkpoint="C0",
            tasks_file=tasks,
            trials=3,
        )
    with pytest.raises(IntegrityError, match="different harness"):
        evaluate_fixed(
            controller,
            output=tmp_path / "eval-c0",
            checkpoint="C1",
            tasks_file=tasks,
            trials=2,
        )

    assert (
        cli_main(
            [
                "evaluate",
                "--config",
                str(config_path),
                "--output",
                str(tmp_path / "eval-harness"),
                "--harness",
                str(campaign_dir / "checkpoints" / "C1" / "harness"),
                "--tasks",
                str(tasks),
                "--trials",
                "2",
                "--label",
                "candidate",
            ]
        )
        == 0
    )
    assert '"label": "candidate"' in capsys.readouterr().out
    comparison = compare_evaluations(tmp_path / "eval-c0", tmp_path / "eval-harness")
    assert comparison["comparison"]["paired_task_count"] == 2
    assert comparison["warnings"] == []
    campaign_eval = compare_evaluations(
        campaign_dir / "evaluations" / "C0-validation",
        tmp_path / "eval-harness",
    )
    assert campaign_eval["comparison"]["paired_task_count"] == 2
    assert any("task sets differ" in warning for warning in campaign_eval["warnings"])

    for kwargs, message in (
        (
            {"checkpoint": "C0", "split": "train", "output": campaign_dir / "x"},
            "outside",
        ),
        ({"split": "train"}, "exactly one of --checkpoint"),
        ({"checkpoint": "C0"}, "exactly one of --split"),
        ({"checkpoint": "C9", "split": "train"}, "does not exist"),
    ):
        kwargs.setdefault("output", tmp_path / "bad")
        with pytest.raises(ConfigurationError, match=message):
            evaluate_fixed(controller, **kwargs)  # type: ignore[arg-type]
    mixed = tmp_path / "mixed.json"
    mixed.write_text(json.dumps(["no-such-task"]), encoding="utf-8")
    with pytest.raises(ConfigurationError, match="not in any split"):
        evaluate_fixed(
            controller,
            output=tmp_path / "bad",
            checkpoint="C0",
            tasks_file=mixed,
        )


def test_selection_and_final_reports_carry_uncertainty_and_route(
    tmp_path: Path,
) -> None:
    status: dict[str, Any] = CampaignController(
        load_config(make_example_project(tmp_path, rounds=1))
    ).run()
    selection = status["selection"]
    assert selection["route"]["wire_api"] == "chat_completions"
    uncertainty = selection["uncertainty"]
    assert "paired task bootstrap" in uncertainty["method"]
    assert set(uncertainty["selected_vs_each_candidate"]) == (
        {"C0", "C1"} - {selection["selected_checkpoint"]}
    )
    final = status["final_test"]
    assert final["route"] == selection["route"]
    if final["selected_checkpoint"] != "C0":
        assert final["uncertainty"]["paired_task_count"] > 0


def test_supervisor_launch_survives_large_output() -> None:
    import sys

    from ahe_runner.supervisor import _launch

    script = (
        "import sys; sys.stderr.write('progress\\n' * 20000); "
        "print('{'); print('  \"status\": \"' + 'x' * 300000 + '\"'); print('}'); "
        "sys.exit(0)"
    )
    returncode, stdout, stderr = _launch([sys.executable, "-c", script])
    assert returncode == 0
    assert len(stdout) > 300000
    assert stderr.endswith("progress\n")
