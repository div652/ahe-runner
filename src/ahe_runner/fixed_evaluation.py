"""Evaluate a fixed harness outside the evolution lifecycle (AHE-039).

``ahe evaluate`` runs the campaign's adapter, model, and transport on one
harness, either a campaign checkpoint or a harness folder, for a chosen split
or task list. The output folder is self-contained and resumable: rerunning
the same command continues it, and a different command for the same folder
is refused. ``ahe compare`` reports a paired comparison of two evaluations
and warns when their routes differ (AHE-035).
"""

from __future__ import annotations

import json
import os
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import __version__
from .adapter import EvaluationRequest, EvaluationResult, SplitDefinition, SplitName
from .errors import ConfigurationError, IntegrityError
from .harness import validate_harness
from .io import atomic_write_json, read_json, sha256_json, tree_hashes
from .meta.endpoint import route_fingerprint
from .state import StageStore
from .statistics import paired_task_comparison
from .workspace import verify_checkpoint

if TYPE_CHECKING:
    from .controller import CampaignController

REPORT_NAME = "evaluation_report.json"
_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def evaluate_fixed(
    controller: CampaignController,
    *,
    output: Path,
    checkpoint: str | None = None,
    harness: Path | None = None,
    split: str | None = None,
    tasks_file: Path | None = None,
    trials: int | None = None,
    seed: int | None = None,
    label: str | None = None,
) -> dict[str, Any]:
    """Evaluate one fixed harness and return its verified report."""

    config = controller.config
    output = output.resolve()
    campaign_dir = config.campaign_dir.resolve()
    if output == campaign_dir or output.is_relative_to(campaign_dir):
        raise ConfigurationError(
            "the evaluation output must be outside the campaign artifacts"
        )
    if (checkpoint is None) == (harness is None):
        raise ConfigurationError("give exactly one of --checkpoint or --harness")
    if (split is None) == (tasks_file is None):
        raise ConfigurationError("give exactly one of --split or --tasks")
    if trials is not None and trials <= 0:
        raise ConfigurationError("--trials must be positive")

    if checkpoint is not None:
        source_dir = campaign_dir / "checkpoints" / checkpoint
        if not (source_dir / "checkpoint.json").is_file():
            raise ConfigurationError(
                f"campaign checkpoint does not exist: {checkpoint}"
            )
        verify_checkpoint(source_dir)
        source_harness = source_dir / "harness"
        source: dict[str, Any] = {"checkpoint": checkpoint}
    else:
        assert harness is not None
        source_harness = harness.resolve()
        report = validate_harness(
            source_harness,
            controller.harness_manifest,
            project_root=config.project_root,
        )
        if not report["valid"]:
            raise ConfigurationError(
                "harness is not conformant: " + "; ".join(report["errors"][:5])
            )
        source = {"harness": str(source_harness)}
    label = label or checkpoint or "fixed"
    if not _LABEL.match(label):
        raise ConfigurationError(f"invalid evaluation label: {label!r}")

    harness_dir = output / "harness"
    _freeze_harness(source_harness, harness_dir)
    harness_sha256 = sha256_json(tree_hashes(harness_dir))
    definition = _split_definition(controller, split=split, tasks_file=tasks_file)
    default_trials = {
        SplitName.TRAIN: config.document.evaluation.train_trials,
        SplitName.VALIDATION: config.document.evaluation.validation_trials,
        SplitName.TEST: config.document.evaluation.test_trials,
    }[definition.name]
    request = EvaluationRequest(
        checkpoint=label,
        harness_dir=harness_dir,
        split=definition,
        trials=trials or default_trials,
        root_seed=config.document.campaign.root_seed if seed is None else seed,
        output_dir=output / "evaluation",
        model=config.document.models.evaluated,
        transport=config.document.transport,
        api_key=config.api_key,
        max_concurrency=config.document.evaluation.max_concurrency,
        provider_rejection_policy=config.document.evaluation.provider_rejection_policy,
    )
    store = StageStore(
        campaign_dir=output,
        maximum_attempts=config.document.recovery.maximum_stage_attempts,
        pre_stage_command=controller.stage_store.pre_stage_command,
        pre_stage_timeout_seconds=controller.stage_store.pre_stage_timeout_seconds,
    )
    stage = store.run(
        name="evaluation",
        semantic_inputs={
            **controller._evaluation_semantic_inputs(
                request,
                harness_sha256=harness_sha256,
            ),
            "source": source,
        },
        action=lambda context: controller._evaluation_action(request),
    )
    result = EvaluationResult.model_validate(stage.data["evaluation"])
    report = {
        "schema_version": 1,
        "kind": "fixed_harness_evaluation",
        "label": label,
        "source": source,
        "harness_sha256": harness_sha256,
        "split": {
            "name": definition.name.value,
            "identity": definition.identity,
            "task_count": len(definition.task_ids),
            "task_ids_sha256": sha256_json(definition.task_ids),
        },
        "trials": request.trials,
        "root_seed": request.root_seed,
        "evaluation_id": result.evaluation_id,
        "summary": result.summary.model_dump(mode="json"),
        "route": route_fingerprint(request.transport, request.model),
        "adapter": controller.adapter.identity().model_dump(mode="json"),
        "config_path": str(config.config_path),
        "runner_version": __version__,
        "completed_at": datetime.now(UTC).isoformat(),
    }
    atomic_write_json(output / REPORT_NAME, report)
    return report


def compare_evaluations(baseline: Path, candidate: Path) -> dict[str, Any]:
    """Paired comparison of two evaluations, with route and coverage checks."""

    before = _load_evaluation(baseline)
    after = _load_evaluation(candidate)
    warnings: list[str] = []
    if before["route"] is None or after["route"] is None:
        warnings.append("route fingerprint unavailable for at least one evaluation")
    elif before["route"] != after["route"]:
        differing = sorted(
            key
            for key in set(before["route"]) | set(after["route"])
            if before["route"].get(key) != after["route"].get(key)
        )
        warnings.append(
            "evaluations used different routes; differences in "
            f"{differing} can change scores independently of the harness"
        )
    before_tasks = {state.task_key for state in before["result"].summary.task_states}
    after_tasks = {state.task_key for state in after["result"].summary.task_states}
    if before_tasks != after_tasks:
        warnings.append(
            f"task sets differ; compared on the {len(before_tasks & after_tasks)} "
            "shared tasks only"
        )
    if before["result"].summary.primary.name != after["result"].summary.primary.name:
        warnings.append("primary metrics differ; the comparison is not meaningful")
    return {
        "baseline": {"path": str(baseline), "label": before["result"].checkpoint},
        "candidate": {"path": str(candidate), "label": after["result"].checkpoint},
        "comparison": paired_task_comparison(
            before["result"].summary,
            after["result"].summary,
        ),
        "warnings": warnings,
    }


def _load_evaluation(path: Path) -> dict[str, Any]:
    """Load an ``ahe evaluate`` folder or a campaign evaluation folder."""

    path = path.resolve()
    report_path = path / REPORT_NAME
    if report_path.is_file():
        report = read_json(report_path)
        result = EvaluationResult.model_validate(
            read_json(path / "evaluation" / "adapter_result.json")
        )
        return {"result": result, "route": report.get("route")}
    result_path = path / "adapter_result.json"
    if not result_path.is_file():
        raise ConfigurationError(
            f"{path} is neither an `ahe evaluate` output nor an evaluation folder"
        )
    result = EvaluationResult.model_validate(read_json(result_path))
    route = None
    suffix = path.name.split("-", 1)[-1].replace("final-test", "test")
    stage = path.parent.parent / "stages" / f"evaluation.{result.checkpoint}.{suffix}"
    if (stage / "stage.json").is_file():
        route = (read_json(stage / "stage.json").get("data") or {}).get("route")
    return {"result": result, "route": route}


def _split_definition(
    controller: CampaignController,
    *,
    split: str | None,
    tasks_file: Path | None,
) -> SplitDefinition:
    if split is not None:
        try:
            return controller.splits[SplitName(split)]
        except ValueError as exc:
            raise ConfigurationError(f"unknown split: {split!r}") from exc
    assert tasks_file is not None
    tasks = _read_tasks(tasks_file)
    for definition in controller.splits.values():
        if set(tasks) <= set(definition.task_ids):
            return SplitDefinition(
                name=definition.name,
                identity=f"{definition.identity}#tasks-{sha256_json(tasks)[:16]}",
                task_ids=tasks,
                metadata=dict(definition.metadata),
            )
    known = {task for item in controller.splits.values() for task in item.task_ids}
    unknown = sorted(set(tasks) - known)
    if unknown:
        raise ConfigurationError(f"tasks are not in any split: {unknown[:5]}")
    raise ConfigurationError("a task list must come from one split")


def _read_tasks(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    try:
        value = json.loads(text)
    except ValueError:
        value = [line.strip() for line in text.splitlines()]
    if isinstance(value, dict):
        value = value.get("task_ids")
    if not isinstance(value, list):
        raise ConfigurationError("--tasks must be a JSON list, or one task per line")
    tasks = list(dict.fromkeys(str(task) for task in value if str(task).strip()))
    if not tasks:
        raise ConfigurationError("--tasks lists no task")
    return tasks


def _freeze_harness(source: Path, destination: Path) -> None:
    expected = tree_hashes(source)
    if destination.exists():
        if tree_hashes(destination) != expected:
            raise IntegrityError(
                f"{destination} already holds a different harness; "
                "use a new output folder"
            )
        return
    temporary = destination.with_name(destination.name + ".tmp")
    if temporary.exists():
        shutil.rmtree(temporary)
    shutil.copytree(source, temporary)
    for path in [temporary, *temporary.rglob("*")]:
        path.chmod(path.stat().st_mode | 0o200)
    if tree_hashes(temporary) != expected:
        shutil.rmtree(temporary)
        raise IntegrityError("harness copy failed content verification")
    os.replace(temporary, destination)
