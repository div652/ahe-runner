"""Native LAB rubric grading with recorded, exact-model Copilot roles.

Grading reuses LAB's ``score_rubric``, prompts, JSON parsing, and parsing
retries. Local, operator-approved additions:

* LAB-001: the filename helper is the recorded Copilot callback.
* LAB-002: a fully formatted judge prompt longer than ``max_prompt_chars`` is
  scored ``fail`` without a model call, with a fixed reasoning marker.
* Error classification: transient judge failures and unparseable replies are
  retried within the attempt; integrity, configuration, and other
  non-transient API errors halt the stage.
* Shared call slots: an optional semaphore caps judge criterion calls across
  every trial graded in the process (``judge_max_concurrent_calls``).
* Judge API: ``copilot_chat`` (LAB's Copilot judge path, two judges) or
  ``openai_responses`` (LAB's OpenAI judge path on a Responses endpoint, one
  or two judges); see docs/MODEL_PROVIDERS.md.
"""

from __future__ import annotations

import json
import re
import threading
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Literal

from lab_core.evaluation.judge import _VERDICT_SCHEMA, PROMPTS_DIR, Judge
from lab_core.evaluation.run_eval import (
    CUSTOM_DUAL_JUDGE_PROFILE,
    JUDGE_MODELS,
    STANDARD_DUAL_JUDGE_PROFILE,
)
from lab_core.evaluation.scoring import score_rubric
from lab_core.harness.adapters.copilot import resolve_copilot_base_url

from ahe_runner.config import ModelSettings, TransportSettings
from ahe_runner.errors import ConfigurationError, InfrastructureError, IntegrityError
from ahe_runner.io import atomic_write_json
from ahe_runner.meta.model_client import (
    RecordedExactModelClient,
    responses_reasoning,
)
from ahe_runner.meta.preflight import run_model_preflight

from .copilot_deliverable_matcher import (
    CopilotDeliverableMatcher,
    DisabledDeliverableMatcher,
)

JudgeApi = Literal["copilot_chat", "openai_responses"]
JUDGE_MAX_TOKENS = 64000
SINGLE_JUDGE_PROFILE = "custom-single"
JUDGE_INPUT_LIMIT_MARKER = "[harvey-ahe judge-input limit]"
JUDGE_PROMPT_NAME = "rubric_criterion"
_GRADING_ERROR = re.compile(r"^grading error: (?P<type>[A-Za-z_][A-Za-z0-9_.]*): ")
_RETRYABLE_ERROR_TYPES = frozenset({"InfrastructureError", "ValueError"})


class RetryableGradingError(InfrastructureError):
    """Criteria could not be graded for transient or judge-format reasons."""


def judge_label(judge_api: JudgeApi, model: str) -> str:
    """The judge identity written to score files and checked on load.

    The prefix names LAB's judge code path (``copilot`` or ``openai``); the
    transport provider is recorded separately in ``judge_transport``.
    """
    return f"{'copilot' if judge_api == 'copilot_chat' else 'openai'}/{model}"


class RecordedCopilotJudge(Judge):
    """Reuse LAB's prompts, JSON parsing, and parsing-retry policy."""

    def __init__(
        self,
        *,
        settings: ModelSettings,
        transport: TransportSettings,
        api_key: str,
        artifact_dir: Path,
        max_prompt_chars: int,
        call_slots: threading.BoundedSemaphore | None = None,
        judge_api: JudgeApi = "copilot_chat",
    ) -> None:
        if judge_api == "copilot_chat":
            if settings.max_tokens != JUDGE_MAX_TOKENS or settings.temperature != 0.0:
                raise ConfigurationError(
                    f"LAB rubric judges require max_tokens={JUDGE_MAX_TOKENS} "
                    "and temperature=0.0"
                )
            if (
                transport.model_catalog != "required"
                or transport.negative_control != "required"
            ):
                raise ConfigurationError(
                    "LAB judges require catalog and negative-control preflights"
                )
            self.base_url = resolve_copilot_base_url(transport.base_url)
            # LAB's Judge.evaluate dispatches on provider.
            self.provider = "copilot"
        else:
            if transport.api_mode != "responses":
                raise ConfigurationError(
                    "judge_api openai_responses requires transport.api_mode responses"
                )
            self.base_url = transport.base_url
            self.provider = "openai"
        if max_prompt_chars <= 0:
            raise ConfigurationError("judge max_prompt_chars must be positive")
        self.settings = settings.model_copy(deep=True)
        self.judge_api = judge_api
        self.model_id = settings.model
        self.model = judge_label(judge_api, settings.model)
        self.model_observations: list[dict[str, Any]] = []
        self.max_prompt_chars = max_prompt_chars
        self.input_limit_failures: list[dict[str, Any]] = []
        self.call_slots = call_slots
        run_model_preflight(
            settings=settings,
            transport=transport,
            api_key=api_key,
            output_path=artifact_dir / "model-preflight.json",
        )
        self.client = RecordedExactModelClient(
            settings=settings,
            transport=transport,
            api_key=api_key,
            log_dir=artifact_dir / "model_calls",
        )
        self._copilot_catalog_verified = True

    def evaluate_from_file(self, prompt_name: str, variables: dict) -> dict:
        template = (PROMPTS_DIR / f"{prompt_name}.txt").read_text(encoding="utf-8")
        prompt_chars = len(template.format(**variables))
        if prompt_chars > self.max_prompt_chars:
            self.input_limit_failures.append(
                {
                    "criterion_title": variables.get("criterion_title"),
                    "prompt_chars": prompt_chars,
                    "limit_chars": self.max_prompt_chars,
                }
            )
            return {
                "verdict": "fail",
                "reasoning": (
                    f"{JUDGE_INPUT_LIMIT_MARKER} The formatted judge prompt has "
                    f"{prompt_chars} characters, above the {self.max_prompt_chars}"
                    "-character limit; scored fail without a model call (LAB-002)."
                ),
            }
        if self.call_slots is None:
            result = self.evaluate(prompt_template=template, variables=variables)
        else:
            with self.call_slots:
                result = self.evaluate(prompt_template=template, variables=variables)
        if (
            not isinstance(result, dict)
            or result.get("verdict") not in {"pass", "fail"}
            or not isinstance(result.get("reasoning"), str)
        ):
            raise InfrastructureError(
                "Rubric judge returned an invalid verdict/reasoning object"
            )
        return result

    def _evaluate_openai(self, prompt: str, temperature: float, _retries: int) -> dict:
        """LAB's ``Judge._evaluate_openai`` on the recorded client.

        Same input, strict ``json_schema`` on all but the last attempt, and
        the same parsing. Output cap, reasoning and temperature come from the
        judge's settings (LAB's ``temperature`` argument is not used). API
        errors propagate, as in LAB's Copilot path, so integrity and
        infrastructure failures keep their types.
        """
        del temperature
        last_err: Exception | None = None
        for attempt in range(_retries):
            kwargs: dict[str, Any] = {
                "model": self.model_id,
                "input": prompt,
                "max_output_tokens": self.settings.max_tokens,
            }
            if self.settings.temperature is not None:
                kwargs["temperature"] = self.settings.temperature
            effort = self.settings.reasoning_effort
            if effort is not None:
                kwargs["reasoning"] = responses_reasoning(effort)
            if attempt < _retries - 1:
                kwargs["text"] = {
                    "format": {
                        "type": "json_schema",
                        "name": "verdict",
                        "schema": _VERDICT_SCHEMA,
                        "strict": True,
                    }
                }
            response = self.client.responses.create(**kwargs)
            self.model_observations.append(
                {
                    "requested_model": self.model_id,
                    "response_model": response.model,
                    "matched": response.model == self.model_id,
                    "status": getattr(response, "status", None),
                }
            )
            text = response.output_text or ""
            try:
                return self._parse_json(text)
            except (ValueError, json.JSONDecodeError) as e:
                last_err = e
        raise ValueError(
            f"Judge returned unparseable response after {_retries} attempts: {last_err}"
        )

    def close(self) -> None:
        self.client.close()


def judge_score_counts(criteria_results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Recompute one judge's native counts from its criterion verdicts."""
    verdicts = [str(row.get("verdict")) for row in criteria_results]
    passed = sum(verdict == "pass" for verdict in verdicts)
    total = len(verdicts)
    return {
        "n_criteria": total,
        "n_passed": passed,
        "n_grading_errors": sum(verdict == "error" for verdict in verdicts),
        "n_judge_input_limit_failures": sum(
            str(row.get("reasoning", "")).startswith(JUDGE_INPUT_LIMIT_MARKER)
            for row in criteria_results
        ),
        "all_pass": total > 0 and passed == total,
        "criterion_pass_fraction": passed / total if total else 0.0,
    }


def dual_summary(per_judge: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """LAB dual aggregation, recomputed only from per-judge criterion rows.

    With one judge the same fields hold that judge's values, and
    ``judges_agree`` is true.
    """
    if len(per_judge) not in (1, 2):
        raise IntegrityError("Harvey grading requires one or two judges")
    counts = [judge_score_counts(scores["criteria_results"]) for scores in per_judge]
    if any(row["n_grading_errors"] for row in counts):
        raise IntegrityError("scored judges must not contain grading errors")
    all_pass = [bool(row["all_pass"]) for row in counts]
    judges = len(counts)
    return {
        "dual_criterion_pass": sum(row["criterion_pass_fraction"] for row in counts)
        / judges,
        "dual_all_pass_rate": sum(float(value) for value in all_pass) / judges,
        "all_pass": all(all_pass),
        "judges_agree": len(set(all_pass)) == 1,
        "n_criteria": [row["n_criteria"] for row in counts],
        "n_passed": [row["n_passed"] for row in counts],
        "n_judge_input_limit_failures": sum(
            row["n_judge_input_limit_failures"] for row in counts
        ),
    }


def grade_task(
    *,
    task_id: str,
    task_config: Mapping[str, Any],
    run_dir: Path,
    grading_dir: Path,
    judges: tuple[ModelSettings, ...],
    helper: ModelSettings | None,
    transport: TransportSettings,
    api_key: str,
    max_prompt_chars: int,
    parallel: int = 1,
    call_slots: threading.BoundedSemaphore | None = None,
    judge_api: JudgeApi = "copilot_chat",
) -> dict[str, Any]:
    """Grade ``run_dir/output`` once; write all artifacts into ``grading_dir``.

    Does not change LAB's global ``LAB_ROOT`` and does not write in ``run_dir``.
    """
    if len(judges) not in (1, 2):
        raise ConfigurationError("Harvey grading requires one or two judges")
    if len(judges) == 2 and judges[0].model == judges[1].model:
        raise ConfigurationError(
            "Harvey dual grading requires two distinct exact judge IDs"
        )
    if judge_api == "copilot_chat" and (len(judges) != 2 or helper is None):
        raise ConfigurationError(
            "judge_api copilot_chat requires two judges and a filename helper"
        )
    grading_dir.mkdir(parents=True, exist_ok=False)
    callback: CopilotDeliverableMatcher | DisabledDeliverableMatcher
    if helper is None:
        callback = DisabledDeliverableMatcher(
            artifact_root=grading_dir / "filename_helper"
        )
    else:
        callback = CopilotDeliverableMatcher(
            settings=helper,
            transport=transport,
            api_key=api_key,
            artifact_root=grading_dir / "filename_helper",
            api="responses" if judge_api == "openai_responses" else "chat",
        )
    per_judge: list[dict[str, Any]] = []
    try:
        for index, settings in enumerate(judges, start=1):
            judge = RecordedCopilotJudge(
                settings=settings,
                transport=transport,
                api_key=api_key,
                artifact_dir=grading_dir / f"judge-{index}",
                max_prompt_chars=max_prompt_chars,
                call_slots=call_slots,
                judge_api=judge_api,
            )
            try:
                rubric = score_rubric(
                    criteria=list(task_config["criteria"]),
                    run_dir=run_dir,
                    judge=judge,
                    task_desc=str(task_config["title"]),
                    parallel=parallel,
                    deliverable_matcher=callback,
                )
                rows = list(rubric.criteria_results)
                counts = judge_score_counts(rows)
                scores = {
                    "task": task_id,
                    "judge_index": index,
                    "judge_model": judge.model,
                    "judge_transport": {
                        **(
                            {"provider": transport.provider, "judge_api": judge_api}
                            if judge_api != "copilot_chat"
                            else {}
                        ),
                        "api_mode": transport.api_mode,
                        "base_url": transport.base_url,
                        "exact_model_id": settings.model,
                        "max_tokens": settings.max_tokens,
                        "reasoning_effort": settings.reasoning_effort,
                        "temperature": settings.temperature,
                    },
                    "score": rubric.score,
                    "max_score": rubric.max_score,
                    "all_pass": counts["all_pass"],
                    "n_criteria": counts["n_criteria"],
                    "n_passed": counts["n_passed"],
                    "n_grading_errors": rubric.n_grading_errors,
                    "n_judge_input_limit_failures": counts[
                        "n_judge_input_limit_failures"
                    ],
                    "judge_input_limit_chars": max_prompt_chars,
                    "judge_input_limit_failures": judge.input_limit_failures,
                    "pandoc_version": rubric.pandoc_version,
                    "criteria_results": rows,
                    "model_observations": judge.model_observations,
                }
                atomic_write_json(grading_dir / f"scores_judge-{index}.json", scores)
                per_judge.append(scores)
            finally:
                judge.close()
    finally:
        callback.close()

    _raise_for_grading_errors(grading_dir, per_judge)
    model_names = [str(scores["judge_model"]) for scores in per_judge]
    result = {
        "task": task_id,
        "judges": model_names,
        "judge_profile": (
            SINGLE_JUDGE_PROFILE
            if len(model_names) == 1
            else STANDARD_DUAL_JUDGE_PROFILE
            if set(model_names) == set(JUDGE_MODELS)
            else CUSTOM_DUAL_JUDGE_PROFILE
        ),
        "judge_input_limit_chars": max_prompt_chars,
        **dual_summary(per_judge),
    }
    atomic_write_json(grading_dir / "scores_dual.json", result)
    return result


def grade_with_retries(
    *,
    attempts: int,
    grading_root: Path,
    **grade_kwargs: Any,
) -> tuple[Path, dict[str, Any]]:
    """Retry transient grading failures inside one unit attempt.

    Every try keeps its own ``grading-<n>`` directory. Raises
    ``InfrastructureError`` when all tries fail transiently.
    """
    if attempts < 1:
        raise ConfigurationError("grading_attempts must be at least 1")
    failures: list[str] = []
    for number in range(1, attempts + 1):
        grading_dir = grading_root / f"grading-{number}"
        try:
            return grading_dir, grade_task(grading_dir=grading_dir, **grade_kwargs)
        except InfrastructureError as exc:
            failures.append(f"grading-{number}: {type(exc).__name__}: {exc}")
    raise InfrastructureError(
        f"grading failed after {attempts} attempt(s): " + " | ".join(failures)
    )


def _raise_for_grading_errors(
    grading_dir: Path,
    per_judge: Sequence[Mapping[str, Any]],
) -> None:
    errors: list[dict[str, str]] = []
    for scores in per_judge:
        for row in scores["criteria_results"]:
            if row.get("verdict") != "error":
                continue
            reasoning = str(row.get("reasoning", ""))
            match = _GRADING_ERROR.match(reasoning)
            errors.append(
                {
                    "judge_model": str(scores["judge_model"]),
                    "criterion_id": str(row.get("id")),
                    "error_type": match.group("type") if match else "Unknown",
                    "reasoning": reasoning,
                }
            )
    if not errors:
        return
    types = {row["error_type"] for row in errors}
    if "IntegrityError" in types:
        classification = "integrity"
    elif types <= _RETRYABLE_ERROR_TYPES:
        classification = "retryable"
    else:
        classification = "configuration"
    atomic_write_json(
        grading_dir / "grading_error.json",
        {"classification": classification, "errors": errors},
    )
    summary = f"{len(errors)} criterion verdict(s) could not be graded: {sorted(types)}"
    if classification == "integrity":
        raise IntegrityError(summary)
    if classification == "retryable":
        raise RetryableGradingError(summary)
    raise ConfigurationError(summary)
