"""Offline checks for Harvey grading: LAB-002 limit, error classes, retries."""

from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar

import pytest
from harvey_ahe import grading
from lab_core.evaluation.judge import PROMPTS_DIR

from ahe_runner.config import ModelSettings, TransportSettings
from ahe_runner.errors import ConfigurationError, InfrastructureError, IntegrityError

JUDGE_A = "judge-model-a"
JUDGE_B = "judge-model-b"
HELPER = "helper-model"
TASK_TITLE = "Review the memo"


class BadRequestError(Exception):
    """Stand-in for a non-transient provider error such as HTTP 400."""


class FakeClient:
    """Scripted replacement for RecordedExactModelClient."""

    behaviors: ClassVar[dict[str, Any]] = {}
    calls: ClassVar[list[str]] = []

    def __init__(self, *, settings: ModelSettings, **_: Any) -> None:
        self.model = settings.model
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs: Any) -> Any:
        FakeClient.calls.append(self.model)
        behavior = FakeClient.behaviors[self.model]
        content = behavior() if callable(behavior) else behavior
        message = SimpleNamespace(content=content)
        return SimpleNamespace(
            model=kwargs["model"], choices=[SimpleNamespace(message=message)]
        )

    def close(self) -> None:
        return None


class FakeMatcher:
    def __init__(self, **_: Any) -> None:
        self.closed = False

    def __call__(self, prompt: str, output_schema: dict[str, Any]) -> dict[str, Any]:
        raise AssertionError("exact deliverable names must not need the helper")

    def close(self) -> None:
        self.closed = True


def _verdict(value: str) -> str:
    return json.dumps({"verdict": value, "reasoning": f"judge says {value}"})


def _raise(exc: Exception) -> Any:
    def behavior() -> str:
        raise exc

    return behavior


@pytest.fixture(autouse=True)
def fake_models(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_preflight(*, output_path: Path, **_: Any) -> dict[str, Any]:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("{}", encoding="utf-8")
        return {}

    FakeClient.behaviors = {JUDGE_A: _verdict("pass"), JUDGE_B: _verdict("pass")}
    FakeClient.calls = []
    monkeypatch.setattr(grading, "run_model_preflight", fake_preflight)
    monkeypatch.setattr(grading, "RecordedExactModelClient", FakeClient)
    monkeypatch.setattr(grading, "CopilotDeliverableMatcher", FakeMatcher)


def _transport() -> TransportSettings:
    return TransportSettings(
        base_url="http://127.0.0.1:3141/v1",
        model_catalog="required",
        negative_control="required",
    )


def _judge(model: str) -> ModelSettings:
    return ModelSettings(model=model, max_tokens=64000, temperature=0.0)


def _criteria() -> list[dict[str, Any]]:
    return [
        {
            "id": "c1",
            "title": "Flags the indemnity cap",
            "match_criteria": "The memo flags the indemnity cap.",
            "deliverables": ["memo.md"],
        },
        {
            "id": "c2",
            "title": "Names the governing law of the agreement",
            "match_criteria": "The memo names the governing law.",
            "deliverables": ["memo.md"],
        },
    ]


def _run_dir(tmp_path: Path, text: str = "Indemnity cap; New York law.") -> Path:
    run_dir = tmp_path / "run"
    (run_dir / "output").mkdir(parents=True)
    (run_dir / "output" / "memo.md").write_text(text, encoding="utf-8")
    return run_dir


def _grade(
    tmp_path: Path, run_dir: Path, *, limit: int = 240000, name: str = "grading"
) -> dict[str, Any]:
    return grading.grade_task(
        task_id="area/task",
        task_config={"title": TASK_TITLE, "criteria": _criteria()},
        run_dir=run_dir,
        grading_dir=tmp_path / name,
        judges=(_judge(JUDGE_A), _judge(JUDGE_B)),
        helper=ModelSettings(model=HELPER, max_tokens=1024, temperature=0.0),
        transport=_transport(),
        api_key="test-key",
        max_prompt_chars=limit,
    )


def _formatted_prompt_chars(output_text: str, criterion: dict[str, Any]) -> int:
    template = (PROMPTS_DIR / f"{grading.JUDGE_PROMPT_NAME}.txt").read_text(
        encoding="utf-8"
    )
    return len(
        template.format(
            task_description=TASK_TITLE,
            agent_output=f"## Agent Output: memo.md\n{output_text}",
            criterion_title=criterion["title"],
            match_criteria=criterion["match_criteria"],
        )
    )


def test_both_judges_pass_writes_dual_scores(tmp_path: Path) -> None:
    result = _grade(tmp_path, _run_dir(tmp_path))

    assert result["dual_all_pass_rate"] == 1.0
    assert result["all_pass"] is True
    assert result["judges_agree"] is True
    assert result["n_judge_input_limit_failures"] == 0
    assert result["judge_input_limit_chars"] == 240000
    assert len(FakeClient.calls) == 4
    written = json.loads((tmp_path / "grading" / "scores_dual.json").read_text())
    assert written == result
    judge_1 = json.loads((tmp_path / "grading" / "scores_judge-1.json").read_text())
    assert judge_1["judge_model"] == f"copilot/{JUDGE_A}"
    assert judge_1["n_passed"] == 2
    assert not (tmp_path / "run" / "scores_dual.json").exists()


def test_parallel_criteria_grading_matches_serial_in_rubric_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    criteria = [
        {
            "id": f"c{index}",
            "title": f"Criterion number {index}",
            "match_criteria": f"The memo satisfies check {index}.",
            "deliverables": ["memo.md"],
        }
        for index in range(8)
    ]

    def create(self: FakeClient, **kwargs: Any) -> Any:
        prompt = json.dumps(kwargs["messages"])
        index = next(i for i in range(8) if f"Criterion number {i}" in prompt)
        # Later criteria answer first, so parallel completion order is reversed.
        time.sleep(0.005 * (8 - index))
        FakeClient.calls.append(self.model)
        verdict = "fail" if index % 3 == 0 else "pass"
        message = SimpleNamespace(content=_verdict(verdict))
        return SimpleNamespace(
            model=kwargs["model"], choices=[SimpleNamespace(message=message)]
        )

    monkeypatch.setattr(FakeClient, "_create", create)
    run_dir = _run_dir(tmp_path)
    results = {}
    for parallel in (1, 6):
        name = f"grading-parallel-{parallel}"
        results[parallel] = grading.grade_task(
            task_id="area/task",
            task_config={"title": TASK_TITLE, "criteria": criteria},
            run_dir=run_dir,
            grading_dir=tmp_path / name,
            judges=(_judge(JUDGE_A), _judge(JUDGE_B)),
            helper=ModelSettings(model=HELPER, max_tokens=1024, temperature=0.0),
            transport=_transport(),
            api_key="test-key",
            max_prompt_chars=240000,
            parallel=parallel,
        )

    assert results[1] == results[6]
    assert results[6]["n_passed"] == [5, 5]
    assert len(FakeClient.calls) == 32
    for index in (1, 2):
        serial, concurrent = (
            json.loads(
                (tmp_path / name / f"scores_judge-{index}.json").read_text("utf-8")
            )["criteria_results"]
            for name in ("grading-parallel-1", "grading-parallel-6")
        )
        assert concurrent == serial
        assert [row["verdict"] for row in concurrent] == [
            "fail" if position % 3 == 0 else "pass" for position in range(8)
        ]


def test_disagreement_scores_half_and_is_not_all_pass(tmp_path: Path) -> None:
    FakeClient.behaviors[JUDGE_B] = _verdict("fail")

    result = _grade(tmp_path, _run_dir(tmp_path))

    assert result["dual_all_pass_rate"] == 0.5
    assert result["all_pass"] is False
    assert result["judges_agree"] is False
    assert result["dual_criterion_pass"] == 0.5


def test_over_limit_prompt_fails_without_model_call(tmp_path: Path) -> None:
    result = _grade(tmp_path, _run_dir(tmp_path, "x" * 5000), limit=1000)

    assert FakeClient.calls == []
    assert result["dual_all_pass_rate"] == 0.0
    assert result["n_judge_input_limit_failures"] == 4
    judge_1 = json.loads((tmp_path / "grading" / "scores_judge-1.json").read_text())
    assert judge_1["n_judge_input_limit_failures"] == 2
    assert judge_1["n_grading_errors"] == 0
    assert {row["verdict"] for row in judge_1["criteria_results"]} == {"fail"}
    assert all(
        row["reasoning"].startswith(grading.JUDGE_INPUT_LIMIT_MARKER)
        for row in judge_1["criteria_results"]
    )
    assert judge_1["judge_input_limit_failures"][0]["limit_chars"] == 1000


def test_limit_boundary_uses_lab_formatted_prompt_length(tmp_path: Path) -> None:
    text = "Indemnity cap; New York law."
    criteria = _criteria()
    lengths = [_formatted_prompt_chars(text, row) for row in criteria]
    assert lengths[0] != lengths[1]
    limit = min(lengths)

    result = _grade(tmp_path, _run_dir(tmp_path, text), limit=limit)

    assert result["n_judge_input_limit_failures"] == 2
    assert len(FakeClient.calls) == 2


@pytest.mark.parametrize(
    ("behavior", "expected"),
    [
        (_raise(InfrastructureError("timeout")), grading.RetryableGradingError),
        ("not json at all", grading.RetryableGradingError),
        (_raise(BadRequestError("context length exceeded")), ConfigurationError),
        (_raise(IntegrityError("model mismatch")), IntegrityError),
    ],
)
def test_grading_errors_are_classified(
    tmp_path: Path, behavior: Any, expected: type[Exception]
) -> None:
    FakeClient.behaviors[JUDGE_B] = behavior

    with pytest.raises(expected):
        _grade(tmp_path, _run_dir(tmp_path))

    report = json.loads((tmp_path / "grading" / "grading_error.json").read_text())
    assert len(report["errors"]) == 2
    assert not (tmp_path / "grading" / "scores_dual.json").exists()


def test_retry_keeps_failed_try_and_returns_success(tmp_path: Path) -> None:
    replies = iter(["not json", "still not json"])

    def flaky() -> str:
        return next(replies, _verdict("pass"))

    FakeClient.behaviors[JUDGE_A] = flaky
    kwargs = {
        "task_id": "area/task",
        "task_config": {"title": TASK_TITLE, "criteria": _criteria()[:1]},
        "run_dir": _run_dir(tmp_path),
        "judges": (_judge(JUDGE_A), _judge(JUDGE_B)),
        "helper": ModelSettings(model=HELPER, max_tokens=1024, temperature=0.0),
        "transport": _transport(),
        "api_key": "test-key",
        "max_prompt_chars": 240000,
    }

    grading_dir, result = grading.grade_with_retries(
        attempts=2, grading_root=tmp_path / "attempt", **kwargs
    )

    assert grading_dir.name == "grading-2"
    assert result["all_pass"] is True
    assert (tmp_path / "attempt" / "grading-1" / "grading_error.json").is_file()


def test_retries_exhausted_raise_infrastructure_error(tmp_path: Path) -> None:
    FakeClient.behaviors[JUDGE_A] = _raise(InfrastructureError("503"))

    with pytest.raises(InfrastructureError, match="after 2 attempt"):
        grading.grade_with_retries(
            attempts=2,
            grading_root=tmp_path / "attempt",
            task_id="area/task",
            task_config={"title": TASK_TITLE, "criteria": _criteria()},
            run_dir=_run_dir(tmp_path),
            judges=(_judge(JUDGE_A), _judge(JUDGE_B)),
            helper=ModelSettings(model=HELPER, max_tokens=1024, temperature=0.0),
            transport=_transport(),
            api_key="test-key",
            max_prompt_chars=240000,
        )
    assert (tmp_path / "attempt" / "grading-2").is_dir()


def test_non_transient_errors_are_not_retried(tmp_path: Path) -> None:
    FakeClient.behaviors[JUDGE_A] = _raise(BadRequestError("bad request"))

    with pytest.raises(ConfigurationError):
        grading.grade_with_retries(
            attempts=3,
            grading_root=tmp_path / "attempt",
            task_id="area/task",
            task_config={"title": TASK_TITLE, "criteria": _criteria()},
            run_dir=_run_dir(tmp_path),
            judges=(_judge(JUDGE_A), _judge(JUDGE_B)),
            helper=ModelSettings(model=HELPER, max_tokens=1024, temperature=0.0),
            transport=_transport(),
            api_key="test-key",
            max_prompt_chars=240000,
        )
    assert not (tmp_path / "attempt" / "grading-2").exists()


@pytest.mark.parametrize(
    ("settings", "transport", "limit"),
    [
        (ModelSettings(model=JUDGE_A, max_tokens=1000, temperature=0.0), None, 1),
        (ModelSettings(model=JUDGE_A, max_tokens=64000, temperature=0.2), None, 1),
        (
            ModelSettings(model=JUDGE_A, max_tokens=64000, temperature=0.0),
            TransportSettings(base_url="http://127.0.0.1:3141/v1"),
            1,
        ),
        (ModelSettings(model=JUDGE_A, max_tokens=64000, temperature=0.0), None, 0),
    ],
)
def test_judge_rejects_non_lab_settings(
    tmp_path: Path,
    settings: ModelSettings,
    transport: TransportSettings | None,
    limit: int,
) -> None:
    with pytest.raises(ConfigurationError):
        grading.RecordedCopilotJudge(
            settings=settings,
            transport=transport or _transport(),
            api_key="test-key",
            artifact_dir=tmp_path,
            max_prompt_chars=limit,
        )


def test_identical_judges_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="distinct"):
        grading.grade_task(
            task_id="area/task",
            task_config={"title": TASK_TITLE, "criteria": _criteria()},
            run_dir=_run_dir(tmp_path),
            grading_dir=tmp_path / "grading",
            judges=(_judge(JUDGE_A), _judge(JUDGE_A)),
            helper=ModelSettings(model=HELPER, max_tokens=1024, temperature=0.0),
            transport=_transport(),
            api_key="test-key",
            max_prompt_chars=240000,
        )
