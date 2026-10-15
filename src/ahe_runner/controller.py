"""Idempotent full AHE campaign controller."""

from __future__ import annotations

import importlib.metadata
import inspect
import os
import platform
import shutil
import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import __version__
from .adapter import (
    BenchmarkAdapter,
    BenchmarkAdapterBase,
    BenchmarkAdapterV2,
    CandidateValidationRequest,
    EvaluationRequest,
    EvaluationResult,
    EvidenceRequest,
    SplitName,
    VerificationRequest,
    adapter_api_generation,
    benchmark_interface_for,
)
from .change_manifest import load_change_manifest
from .config import ResolvedConfig
from .errors import CandidateValidationError, ConfigurationError, IntegrityError
from .evaluation import verify_evaluation_output
from .evidence import verify_evidence_bundle
from .harness import HarnessManifest, load_harness_manifest, validate_harness
from .history import materialize_history, write_transition
from .io import (
    atomic_write_json,
    read_json,
    sha256_file,
    sha256_json,
    tree_hashes,
)
from .loading import load_adapter
from .meta import MetaBackendContext, create_meta_backend
from .meta.model_client import summarize_model_call_retries
from .runtime import EvaluatedAgentRuntime
from .state import StageContext, StageResult, StageStore
from .workspace import (
    freeze_checkpoint,
    git_output,
    initialize_baseline_checkpoint,
    prepare_evolve_workspace,
    verify_checkpoint,
)


class CampaignController:
    def __init__(self, config: ResolvedConfig) -> None:
        self.config = config
        self.adapter: BenchmarkAdapterBase = load_adapter(config)
        self.benchmark_interface = benchmark_interface_for(self.adapter)
        self.prompt_context = self.adapter.meta_prompt_context()
        self.harness_manifest: HarnessManifest = load_harness_manifest(
            config.harness_manifest_path
        )
        self.splits = {
            name: self.adapter.split(name)
            for name in (SplitName.TRAIN, SplitName.VALIDATION, SplitName.TEST)
        }
        self._validate_split_separation()
        self.stage_store = StageStore(
            campaign_dir=config.campaign_dir,
            maximum_attempts=config.document.recovery.maximum_stage_attempts,
        )
        self.method_reference = (
            Path(__file__).resolve().parent / "resources" / "AHE_METHODS_REFERENCE.md"
        )
        self.meta = create_meta_backend(
            MetaBackendContext(
                config=config,
                prompt_context=self.prompt_context,
                benchmark_interface=self.benchmark_interface,
                harness_manifest=self.harness_manifest,
                method_reference=self.method_reference,
            )
        )

    def validate_project(self) -> dict[str, Any]:
        report = validate_harness(
            self.config.harness_dir,
            self.harness_manifest,
            project_root=self.config.project_root,
            run_nexau_smoke=True,
        )
        return {
            "config": self.config.immutable_snapshot(),
            "adapter": self.adapter.identity().model_dump(mode="json"),
            "adapter_api_generation": adapter_api_generation(self.adapter),
            "benchmark_interface": self.benchmark_interface.model_dump(mode="json"),
            "splits": {
                name.value: {
                    "identity": split.identity,
                    "task_count": len(split.task_ids),
                    "task_ids_sha256": sha256_json(split.task_ids),
                }
                for name, split in self.splits.items()
            },
            "harness": report,
            "warnings": self._metric_guidance_warnings(),
        }

    def conformance(self) -> dict[str, Any]:
        validation = self.validate_project()
        return {
            "adapter": validation["adapter"],
            "adapter_api_generation": validation["adapter_api_generation"],
            "benchmark_interface": validation["benchmark_interface"],
            "harness_abi": validation["harness"]["conformance"],
            "nexau_smoke": validation["harness"]["nexau_smoke"],
            "warnings": validation["warnings"],
            "valid": validation["harness"]["valid"],
        }

    def run(self) -> dict[str, Any]:
        self.prepare()
        self._ensure_training_evaluation(0)
        self._ensure_evidence(0)
        self._ensure_exploration()
        for index in range(1, self.config.document.campaign.rounds + 1):
            self.run_round(index)
        self.close_evolution()
        self.run_validation()
        self.run_final_test()
        return self.status()

    def resume(self) -> dict[str, Any]:
        if not self._campaign_manifest_path().is_file():
            raise ConfigurationError("campaign does not exist; use run first")
        return self.run()

    def prepare(self) -> dict[str, Any]:
        self.config.campaign_dir.mkdir(parents=True, exist_ok=True)
        for name in (
            "checkpoints",
            "workspaces",
            "evaluations",
            "evidence",
            "debugger",
            "evolve",
            "explore_skills",
            "history",
            "attribution",
            "selection",
            "final",
            "reports",
        ):
            (self.config.campaign_dir / name).mkdir(parents=True, exist_ok=True)
        campaign_manifest = self._ensure_campaign_manifest()
        result = self.stage_store.run(
            name="baseline.freeze",
            semantic_inputs={
                "campaign_identity": campaign_manifest["identity_sha256"],
                "base_harness_sha256": campaign_manifest["base_harness_sha256"],
            },
            retryable=False,
            action=self._freeze_baseline,
        )
        self._write_state()
        return result.data

    def run_round(self, index: int) -> dict[str, Any]:
        rounds = self.config.document.campaign.rounds
        if not 1 <= index <= rounds:
            raise ValueError(f"round must be between 1 and {rounds}")
        self.prepare()
        self._ensure_training_evaluation(index - 1)
        evidence_dir = self._ensure_evidence(index - 1)
        self._ensure_exploration()
        debugger_dir = self._ensure_debugger(index, evidence_dir)
        history_dir = materialize_history(
            campaign_dir=self.config.campaign_dir,
            through_round=index - 1,
        )
        attribution_dir = (
            self.config.campaign_dir / "attribution" / f"C{index - 1}"
            if index > 1
            else None
        )
        evolve_result = self._ensure_evolve(
            index=index,
            evidence_dir=evidence_dir,
            debugger_dir=debugger_dir,
            history_dir=history_dir,
            attribution_dir=attribution_dir,
        )
        workspace = Path(str(evolve_result["workspace"]))
        validation = self._ensure_candidate_validation(index, workspace)
        self._ensure_checkpoint(index, workspace, validation)
        self._ensure_training_evaluation(index)
        self._ensure_evidence(index)
        self._ensure_attribution(index)
        self._write_state()
        return {
            "checkpoint": f"C{index}",
            "evaluation": str(self._evaluation_dir(index, SplitName.TRAIN)),
        }

    def close_evolution(self) -> dict[str, Any]:
        rounds = self.config.document.campaign.rounds
        self._ensure_training_evaluation(rounds)
        result = self.stage_store.run(
            name="evolution.close",
            semantic_inputs={
                "final_checkpoint": f"C{rounds}",
                "rounds": rounds,
            },
            retryable=False,
            action=lambda context: self._close_evolution_action(context, rounds),
        )
        self._write_state()
        return result.data

    def run_validation(self) -> dict[str, Any]:
        rounds = self.config.document.campaign.rounds
        self.close_evolution()
        summaries: dict[str, EvaluationResult] = {}
        for index in range(rounds + 1):
            summaries[f"C{index}"] = self._ensure_evaluation(
                index,
                SplitName.VALIDATION,
            )
        result = self.stage_store.run(
            name="selection",
            semantic_inputs={
                "validation_evaluations": {
                    key: value.evaluation_id for key, value in summaries.items()
                },
                "adapter": self.adapter.identity().model_dump(mode="json"),
            },
            retryable=False,
            action=lambda context: self._selection_action(context, summaries),
        )
        self._write_state()
        return result.data

    def run_final_test(self) -> dict[str, Any]:
        selection = self.run_validation()
        selected = str(selection["selected_checkpoint"])
        selected_index = int(selected.removeprefix("C"))
        results = {
            f"C{index}": self._ensure_evaluation(index, SplitName.TEST)
            for index in sorted({0, selected_index})
        }
        result = self.stage_store.run(
            name="final.complete",
            semantic_inputs={
                "selected_checkpoint": selected,
                "test_evaluations": {
                    key: value.evaluation_id for key, value in results.items()
                },
            },
            retryable=False,
            action=lambda context: self._final_action(
                context,
                selected=selected,
                results=results,
            ),
        )
        self._write_state()
        return result.data

    def status(self) -> dict[str, Any]:
        stages = self.stage_store.status()
        checkpoints = sorted(
            path.name
            for path in (self.config.campaign_dir / "checkpoints").glob("C*")
            if (path / "checkpoint.json").is_file()
        )
        completed = [row["name"] for row in stages if row["status"] == "completed"]
        blocked = [row for row in stages if row["status"] == "blocked"]
        interrupted = [row for row in stages if row["status"] == "interrupted"]
        selection_path = self.config.campaign_dir / "selection" / "selection.json"
        final_path = self.config.campaign_dir / "final" / "final_test.json"
        payload = {
            "schema_version": 1,
            "campaign": self.config.document.campaign.name,
            "campaign_dir": str(self.config.campaign_dir),
            "checkpoints": checkpoints,
            "completed_stage_count": len(completed),
            "blocked_stages": [
                {"name": row["name"], "last_error": row.get("last_error")}
                for row in blocked
            ],
            "interrupted_stages": [
                {"name": row["name"], "last_error": row.get("last_error")}
                for row in interrupted
            ],
            "model_call_retries": summarize_model_call_retries(
                self.config.campaign_dir,
                configured_retries=(self.config.document.transport.hung_call_retries),
            ),
            "selection": read_json(selection_path)
            if selection_path.is_file()
            else None,
            "final_test": read_json(final_path) if final_path.is_file() else None,
            "stages": stages,
        }
        atomic_write_json(self.config.campaign_dir / "campaign_state.json", payload)
        return payload

    def retry_stage(self, *, name: str, reason: str) -> dict[str, Any]:
        result = self.stage_store.retry_blocked(name=name, reason=reason)
        self._write_state()
        return result

    def _metric_guidance_warnings(self) -> list[str]:
        terms = self.config.document.meta.deprecated_metric_terms
        if not terms:
            return []
        warnings: list[str] = []
        for role, path in sorted(self.config.role_supplements):
            text = path.read_text(encoding="utf-8").casefold()
            for term in terms:
                if term.casefold() in text:
                    warnings.append(
                        f"meta role supplement {role!r} contains deprecated "
                        f"metric term {term!r}: {path}"
                    )
        return warnings

    def _freeze_baseline(self, context: StageContext) -> StageResult:
        del context
        validation = validate_harness(
            self.config.harness_dir,
            self.harness_manifest,
            project_root=self.config.project_root,
        )
        checkpoint_dir = self._checkpoint_dir(0)
        manifest = initialize_baseline_checkpoint(
            source_harness=self.config.harness_dir,
            checkpoint_dir=checkpoint_dir,
            validation_report=validation,
        )
        return StageResult(
            outputs=(checkpoint_dir,),
            data={"checkpoint_manifest": manifest},
        )

    def _ensure_training_evaluation(self, index: int) -> EvaluationResult:
        return self._ensure_evaluation(index, SplitName.TRAIN)

    def _ensure_evaluation(
        self,
        index: int,
        split_name: SplitName,
    ) -> EvaluationResult:
        request = self._evaluation_request(index, split_name)
        checkpoint = request.checkpoint
        checkpoint_manifest = verify_checkpoint(self._checkpoint_dir(index))
        output_dir = request.output_dir

        def action(context: StageContext) -> StageResult:
            del context
            if isinstance(self.adapter, BenchmarkAdapterV2):
                runtime = EvaluatedAgentRuntime(
                    harness_dir=request.harness_dir,
                    manifest=self.harness_manifest,
                    benchmark_interface=self.benchmark_interface,
                    model=request.model,
                    transport=request.transport,
                    api_key=request.api_key,
                    artifact_root=output_dir / "runtime",
                    project_root=self.config.project_root,
                )
                try:
                    result = self.adapter.evaluate(request, runtime)
                finally:
                    runtime.close()
            elif isinstance(self.adapter, BenchmarkAdapter):
                result = self.adapter.evaluate(request)
            else:
                raise TypeError(
                    f"unsupported adapter generation: {type(self.adapter).__name__}"
                )
            verify_evaluation_output(request, result)
            verified = self.adapter.verify_evaluation(
                VerificationRequest(evaluation=request)
            )
            verify_evaluation_output(request, verified)
            if verified != result:
                raise IntegrityError("adapter verification disagrees with evaluation")
            atomic_write_json(
                output_dir / "adapter_result.json",
                result.model_dump(mode="json"),
            )
            return StageResult(
                outputs=(output_dir,),
                data={"evaluation": result.model_dump(mode="json")},
            )

        stage = self.stage_store.run(
            name=f"evaluation.{checkpoint}.{split_name.value}",
            semantic_inputs={
                "checkpoint": checkpoint,
                "harness_sha256": checkpoint_manifest["harness_sha256"],
                "split_identity": request.split.identity,
                "split_task_ids_sha256": sha256_json(request.split.task_ids),
                "trials": request.trials,
                "root_seed": self.config.document.campaign.root_seed,
                "evaluated_model": self.config.document.models.evaluated.model_dump(
                    mode="json"
                ),
                "transport": self.config.document.transport.model_dump(mode="json"),
                "adapter": self.adapter.identity().model_dump(mode="json"),
                "adapter_api_generation": adapter_api_generation(self.adapter),
                "benchmark_interface": self.benchmark_interface.model_dump(mode="json"),
            },
            action=action,
        )
        self._write_state()
        return EvaluationResult.model_validate(stage.data["evaluation"])

    def _evaluation_request(
        self,
        index: int,
        split_name: SplitName,
    ) -> EvaluationRequest:
        trials = {
            SplitName.TRAIN: self.config.document.evaluation.train_trials,
            SplitName.VALIDATION: self.config.document.evaluation.validation_trials,
            SplitName.TEST: self.config.document.evaluation.test_trials,
        }[split_name]
        return EvaluationRequest(
            checkpoint=f"C{index}",
            harness_dir=self._checkpoint_dir(index) / "harness",
            split=self.splits[split_name],
            trials=trials,
            root_seed=self.config.document.campaign.root_seed,
            output_dir=self._evaluation_dir(index, split_name),
            model=self.config.document.models.evaluated,
            transport=self.config.document.transport,
            api_key=self.config.api_key,
            max_concurrency=self.config.document.evaluation.max_concurrency,
        )

    def _ensure_evidence(self, index: int) -> Path:
        checkpoint = f"C{index}"
        evaluation_dir = self._evaluation_dir(index, SplitName.TRAIN)
        result = EvaluationResult.model_validate(
            read_json(evaluation_dir / "adapter_result.json")
        )
        output_dir = self.config.campaign_dir / "evidence" / f"{checkpoint}-train"

        def action(context: StageContext) -> StageResult:
            del context
            bundle = self.adapter.materialize_training_evidence(
                EvidenceRequest(
                    checkpoint=checkpoint,
                    evaluation_dir=evaluation_dir,
                    output_dir=output_dir,
                )
            )
            manifest = verify_evidence_bundle(
                bundle,
                evidence_dir=output_dir,
                expected_checkpoint=checkpoint,
                evaluation_dir=evaluation_dir,
                source_evaluation=result,
                source_request=self._evaluation_request(index, SplitName.TRAIN),
            )
            return StageResult(
                outputs=(output_dir,),
                data={
                    "bundle": bundle.model_dump(mode="json"),
                    "metrics": manifest.metrics.model_dump(mode="json"),
                },
            )

        self.stage_store.run(
            name=f"evidence.{checkpoint}.train",
            semantic_inputs={
                "evaluation_id": result.evaluation_id,
                "checkpoint": checkpoint,
                "adapter": self.adapter.identity().model_dump(mode="json"),
            },
            retryable=False,
            action=action,
        )
        self._write_state()
        return output_dir

    def _ensure_exploration(self) -> Path:
        output_dir = self.config.campaign_dir / "explore_skills"
        baseline = verify_checkpoint(self._checkpoint_dir(0))

        def action(context: StageContext) -> StageResult:
            manifest = self.meta.ensure_exploration(
                seed_harness=self._checkpoint_dir(0) / "harness",
                output_dir=output_dir,
                attempt_dir=context.attempt_dir,
            )
            path = output_dir / "skills_manifest.json"
            if not path.is_file():
                atomic_write_json(path, manifest)
            return StageResult(outputs=(output_dir,), data=manifest)

        self.stage_store.run(
            name="exploration",
            semantic_inputs={
                "baseline_harness_sha256": baseline["harness_sha256"],
                "meta_model": self.config.document.models.meta.model_dump(mode="json"),
                **self._meta_stage_inputs("explorers"),
                "source_enabled": self.config.document.meta.explore.source_enabled,
                "web_enabled": self.config.document.meta.explore.web_enabled,
                "prompt_context": self.prompt_context.model_dump(mode="json"),
            },
            action=action,
        )
        self._write_state()
        return output_dir

    def _meta_stage_inputs(self, role: str) -> dict[str, Any]:
        """Meta settings that change a stage's result (AHE-016), when set."""

        meta = self.config.document.meta
        inputs: dict[str, Any] = {}
        overrides = getattr(meta.roles, role).model_dump(exclude_none=True)
        if overrides:
            inputs["meta_role_overrides"] = overrides
        if meta.shell.backend != "disabled":
            inputs["meta_shell"] = meta.shell.model_dump(mode="json")
        return inputs

    def _ensure_debugger(self, index: int, evidence_dir: Path) -> Path:
        checkpoint = f"C{index - 1}"
        output_dir = self.config.campaign_dir / "debugger" / f"C{index}"
        evidence_sha = sha256_file(evidence_dir / "evidence_manifest.json")

        def action(context: StageContext) -> StageResult:
            result = self.meta.run_debugger(
                checkpoint=checkpoint,
                evidence_dir=evidence_dir,
                output_dir=output_dir,
                attempt_dir=context.attempt_dir,
            )
            return StageResult(outputs=(output_dir,), data=result)

        self.stage_store.run(
            name=f"round.{index}.debugger",
            semantic_inputs={
                "parent_checkpoint": checkpoint,
                "evidence_manifest_sha256": evidence_sha,
                "meta_model": self.config.document.models.meta.model_dump(mode="json"),
                **self._meta_stage_inputs("debugger"),
                "prompt_context": self.prompt_context.model_dump(mode="json"),
            },
            action=action,
        )
        self._write_state()
        return output_dir

    def _ensure_evolve(
        self,
        *,
        index: int,
        evidence_dir: Path,
        debugger_dir: Path,
        history_dir: Path,
        attribution_dir: Path | None,
    ) -> dict[str, Any]:
        parent = self._checkpoint_dir(index - 1)
        parent_manifest = verify_checkpoint(parent)
        output_root = self.config.campaign_dir / "evolve" / f"C{index}"

        def action(context: StageContext) -> StageResult:
            workspace = prepare_evolve_workspace(
                workspaces_dir=self.config.campaign_dir / "workspaces",
                checkpoint=f"C{index}",
                attempt=context.attempt,
                parent_harness_dir=parent / "harness",
                recovery_output_dir=context.attempt_dir / "recovery",
            )
            recovery_dir = (
                context.attempt_dir / "recovery" if context.attempt > 1 else None
            )
            meta_output = output_root / f"attempt-{context.attempt:04d}"
            result = self.meta.run_evolve(
                round_index=index,
                workspace=workspace,
                evidence_dir=evidence_dir,
                raw_evaluation_dir=self._evaluation_dir(
                    index - 1,
                    SplitName.TRAIN,
                ),
                debugger_dir=debugger_dir,
                history_dir=history_dir,
                attribution_dir=attribution_dir,
                explore_skills_dir=self.config.campaign_dir / "explore_skills",
                recovery_dir=recovery_dir,
                output_dir=meta_output,
            )
            return StageResult(
                outputs=(workspace, meta_output),
                data={
                    "workspace": str(workspace),
                    "meta_output": str(meta_output),
                    "result": result,
                },
            )

        result = self.stage_store.run(
            name=f"round.{index}.evolve",
            semantic_inputs={
                "round": index,
                "parent_checkpoint": f"C{index - 1}",
                "parent_harness_sha256": parent_manifest["harness_sha256"],
                "evidence_sha256": sha256_file(evidence_dir / "evidence_manifest.json"),
                "debugger_sha256": sha256_file(debugger_dir / "overview.json"),
                "meta_model": self.config.document.models.meta.model_dump(mode="json"),
                **self._meta_stage_inputs("evolve"),
                "explore_skills_sha256": sha256_file(
                    self.config.campaign_dir / "explore_skills" / "skills_manifest.json"
                ),
            },
            action=action,
        )
        self._write_state()
        return result.data

    def _ensure_candidate_validation(
        self,
        index: int,
        workspace: Path,
    ) -> dict[str, Any]:
        parent_dir = self._checkpoint_dir(index - 1) / "harness"
        parent_validation = validate_harness(
            parent_dir,
            self.harness_manifest,
            project_root=self.config.project_root,
            run_nexau_smoke=False,
        )
        output_path = (
            self.config.campaign_dir / "evolve" / f"C{index}" / "validation.json"
        )
        workspace_head = _git_head(workspace)
        train_ids = tuple(self.splits[SplitName.TRAIN].task_ids)

        def action(context: StageContext) -> StageResult:
            del context
            generic = validate_harness(
                workspace,
                self.harness_manifest,
                project_root=self.config.project_root,
                training_task_ids=train_ids,
                parent_runtime_owned_hashes=parent_validation["runtime_owned_hashes"],
            )
            adapter_result = self.adapter.validate_candidate(
                CandidateValidationRequest(
                    checkpoint=f"C{index}",
                    parent_checkpoint=f"C{index - 1}",
                    harness_dir=workspace,
                    parent_harness_dir=parent_dir,
                    training_task_ids=train_ids,
                )
            )
            report = {
                "schema_version": 1,
                "valid": generic["valid"] and adapter_result.valid,
                "generic": generic,
                "adapter": adapter_result.model_dump(mode="json"),
            }
            atomic_write_json(output_path, report)
            if not report["valid"]:
                raise CandidateValidationError(
                    {
                        "errors": generic["errors"] + adapter_result.errors,
                        **report,
                    }
                )
            return StageResult(outputs=(output_path,), data=report)

        result = self.stage_store.run(
            name=f"round.{index}.validate",
            semantic_inputs={
                "workspace_head": workspace_head,
                "parent_checkpoint": f"C{index - 1}",
                "harness_manifest_sha256": sha256_file(
                    self.config.harness_manifest_path
                ),
                "training_task_ids_sha256": sha256_json(train_ids),
            },
            retryable=False,
            action=action,
        )
        return result.data

    def _ensure_checkpoint(
        self,
        index: int,
        workspace: Path,
        validation: dict[str, Any],
    ) -> dict[str, Any]:
        destination = self._checkpoint_dir(index)
        workspace_head = _git_head(workspace)

        def action(context: StageContext) -> StageResult:
            del context
            manifest = freeze_checkpoint(
                checkpoint=f"C{index}",
                parent_checkpoint=f"C{index - 1}",
                workspace=workspace,
                destination=destination,
                validation_report=validation,
            )
            return StageResult(
                outputs=(destination,),
                data={"checkpoint_manifest": manifest},
            )

        result = self.stage_store.run(
            name=f"round.{index}.freeze",
            semantic_inputs={
                "workspace_head": workspace_head,
                "validation_sha256": sha256_json(validation),
            },
            retryable=False,
            action=action,
        )
        return result.data

    def _ensure_attribution(self, index: int) -> Path:
        before = EvaluationResult.model_validate(
            read_json(
                self._evaluation_dir(index - 1, SplitName.TRAIN) / "adapter_result.json"
            )
        )
        after = EvaluationResult.model_validate(
            read_json(
                self._evaluation_dir(index, SplitName.TRAIN) / "adapter_result.json"
            )
        )
        change_manifest = load_change_manifest(
            self._checkpoint_dir(index) / "harness" / "change_manifest.json"
        )
        output_dir = self.config.campaign_dir / "attribution" / f"C{index}"
        task_aliases = self._attribution_task_aliases(index, before, after)

        def action(context: StageContext) -> StageResult:
            del context
            transition = self.adapter.compare(before.summary, after.summary)
            path = write_transition(
                output_dir=output_dir,
                transition=transition,
                change_manifest=change_manifest,
                task_aliases=task_aliases,
            )
            return StageResult(
                outputs=(path,),
                data={"transition": transition.model_dump(mode="json")},
            )

        self.stage_store.run(
            name=f"round.{index}.attribute",
            semantic_inputs={
                "before_evaluation": before.evaluation_id,
                "after_evaluation": after.evaluation_id,
                "change_manifest_sha256": sha256_json(
                    change_manifest.model_dump(mode="json")
                ),
                "task_aliases_sha256": sha256_json(task_aliases),
            },
            retryable=False,
            action=action,
        )
        return output_dir

    def _attribution_task_aliases(
        self,
        index: int,
        before: EvaluationResult,
        after: EvaluationResult,
    ) -> dict[str, str]:
        """Map debugger keys, task ids and task keys to evaluation task keys."""
        aliases: dict[str, str] = {}
        key_by_task_id: dict[str, str] = {}
        for task in (*before.summary.task_states, *after.summary.task_states):
            aliases[task.task_key] = task.task_key
            if task.task_id:
                key_by_task_id[task.task_id] = task.task_key
                aliases[task.task_id] = task.task_key
        summary_path = (
            self.config.campaign_dir / "debugger" / f"C{index}" / "summary.json"
        )
        if summary_path.is_file():
            for row in read_json(summary_path).get("tasks") or []:
                debugger_key = row.get("task_key")
                task_key = key_by_task_id.get(row.get("task_id") or "")
                if debugger_key and task_key:
                    aliases[str(debugger_key)] = task_key
        return aliases

    def _close_evolution_action(
        self,
        context: StageContext,
        rounds: int,
    ) -> StageResult:
        del context
        path = self.config.campaign_dir / "selection" / "evolution_closed.json"
        payload = {
            "closed_at": datetime.now(UTC).isoformat(),
            "final_training_checkpoint": f"C{rounds}",
            "no_further_evolution": True,
        }
        atomic_write_json(path, payload)
        return StageResult(outputs=(path,), data=payload)

    def _selection_action(
        self,
        context: StageContext,
        summaries: dict[str, EvaluationResult],
    ) -> StageResult:
        del context
        selected = max(
            summaries,
            key=lambda label: (
                self.adapter.selection_key(summaries[label].summary),
                -int(label.removeprefix("C")),
            ),
        )
        payload = {
            "schema_version": 1,
            "selected_checkpoint": selected,
            "selection_rule": "adapter.selection_key, then earliest checkpoint",
            "validation_summaries": {
                label: result.summary.model_dump(mode="json")
                for label, result in summaries.items()
            },
        }
        path = self.config.campaign_dir / "selection" / "selection.json"
        atomic_write_json(path, payload)
        return StageResult(outputs=(path,), data=payload)

    def _final_action(
        self,
        context: StageContext,
        *,
        selected: str,
        results: dict[str, EvaluationResult],
    ) -> StageResult:
        del context
        final_dir = self.config.campaign_dir / "final"
        selected_harness = final_dir / "selected-harness"
        source_harness = self.config.campaign_dir / "checkpoints" / selected / "harness"
        expected_hashes = tree_hashes(source_harness)
        if selected_harness.exists():
            if tree_hashes(selected_harness) != expected_hashes:
                raise IntegrityError(
                    "existing selected-harness export does not match the "
                    "selected immutable checkpoint"
                )
        else:
            temporary = final_dir / "selected-harness.tmp"
            if temporary.exists():
                _make_tree_writable(temporary)
                shutil.rmtree(temporary)
            shutil.copytree(
                source_harness,
                temporary,
            )
            if tree_hashes(temporary) != expected_hashes:
                _make_tree_writable(temporary)
                shutil.rmtree(temporary)
                raise IntegrityError(
                    "temporary selected-harness export failed content verification"
                )
            os.replace(temporary, selected_harness)
        payload = {
            "schema_version": 1,
            "baseline_checkpoint": "C0",
            "selected_checkpoint": selected,
            "test_summaries": {
                label: result.summary.model_dump(mode="json")
                for label, result in results.items()
            },
            "selected_harness_sha256": sha256_json(expected_hashes),
            "no_post_test_evolution": True,
        }
        path = final_dir / "final_test.json"
        atomic_write_json(path, payload)
        return StageResult(outputs=(path, selected_harness), data=payload)

    def _ensure_campaign_manifest(self) -> dict[str, Any]:
        adapter_source = inspect.getsourcefile(type(self.adapter))
        adapter_dependencies = [
            Path(adapter_source).resolve() if adapter_source else None,
            *self.config.adapter_dependency_paths,
        ]
        unique_adapter_dependencies = tuple(
            dict.fromkeys(path for path in adapter_dependencies if path is not None)
        )
        package_root = Path(__file__).resolve().parent
        identity = {
            "schema_version": 2,
            "runner_version": __version__,
            "python": platform.python_version(),
            "nexau_version": importlib.metadata.version("nexau"),
            "config": self.config.immutable_snapshot(),
            "harness_manifest_sha256": sha256_file(self.config.harness_manifest_path),
            "base_harness_sha256": sha256_json(tree_hashes(self.config.harness_dir)),
            "adapter": self.adapter.identity().model_dump(mode="json"),
            "adapter_api_generation": adapter_api_generation(self.adapter),
            "benchmark_interface": self.benchmark_interface.model_dump(mode="json"),
            "adapter_dependencies": [
                _protected_path_identity(path) for path in unique_adapter_dependencies
            ],
            "resolved_meta_inputs": {
                "prompt_context": self.prompt_context.model_dump(mode="json"),
                "safe_source_roots": [
                    _protected_path_identity(path)
                    for path in self.config.meta_agent_source_roots
                ],
                "role_supplements": {
                    role: _protected_path_identity(path)
                    for role, path in self.config.role_supplements
                },
                "research_seed_urls": list(
                    self.config.document.meta.explore.research_seed_urls
                ),
            },
            "protected_runner_resources": {
                "package": _protected_path_identity(package_root),
                "core_prompts": _protected_path_identity(
                    package_root / "meta" / "prompts"
                ),
                "tool_descriptions": _protected_path_identity(
                    package_root / "meta" / "tool_descriptions"
                ),
                "tool_implementation": _protected_path_identity(
                    package_root / "meta" / "tools.py"
                ),
                "method_reference": _protected_path_identity(self.method_reference),
            },
            "splits": {
                name.value: {
                    "identity": split.identity,
                    "task_count": len(split.task_ids),
                    "task_ids_sha256": sha256_json(split.task_ids),
                }
                for name, split in self.splits.items()
            },
        }
        identity_sha = sha256_json(identity)
        payload = {
            **identity,
            "identity_sha256": identity_sha,
            "created_at": datetime.now(UTC).isoformat(),
        }
        path = self._campaign_manifest_path()
        if path.is_file():
            existing = read_json(path)
            comparable = dict(existing)
            comparable.pop("created_at", None)
            expected = dict(payload)
            expected.pop("created_at", None)
            if comparable != expected:
                changed = sorted(
                    key
                    for key in set(comparable) | set(expected)
                    if key not in {"created_at", "identity_sha256"}
                    and comparable.get(key) != expected.get(key)
                )
                raise IntegrityError(
                    "campaign identity changed in protected inputs "
                    f"{changed}; use a new artifacts directory"
                )
            return existing
        atomic_write_json(path, payload)
        return payload

    def _validate_split_separation(self) -> None:
        sets = {name: set(split.task_ids) for name, split in self.splits.items()}
        for left, right in (
            (SplitName.TRAIN, SplitName.VALIDATION),
            (SplitName.TRAIN, SplitName.TEST),
            (SplitName.VALIDATION, SplitName.TEST),
        ):
            overlap = sets[left] & sets[right]
            if overlap:
                raise ConfigurationError(
                    f"{left.value} and {right.value} splits overlap: "
                    f"{sorted(overlap)[:5]}"
                )

    def _checkpoint_dir(self, index: int) -> Path:
        return self.config.campaign_dir / "checkpoints" / f"C{index}"

    def _evaluation_dir(self, index: int, split: SplitName) -> Path:
        suffix = {
            SplitName.TRAIN: "train",
            SplitName.VALIDATION: "validation",
            SplitName.TEST: "final-test",
        }[split]
        return self.config.campaign_dir / "evaluations" / f"C{index}-{suffix}"

    def _campaign_manifest_path(self) -> Path:
        return self.config.campaign_dir / "campaign_manifest.json"

    def _write_state(self) -> None:
        self.status()


def _protected_path_identity(path: Path) -> dict[str, Any]:
    resolved = path.resolve()
    if resolved.is_file():
        return {
            "path": str(resolved),
            "type": "file",
            "sha256": sha256_file(resolved),
        }

    if resolved.is_dir():
        files = tree_hashes(resolved)
        return {
            "path": str(resolved),
            "type": "directory",
            "sha256": sha256_json(files),
            "files": files,
        }
    raise IntegrityError(f"protected campaign input is missing: {resolved}")


def _git_head(workspace: Path) -> str:
    return git_output(workspace, "rev-parse", "HEAD").strip()


def _make_tree_writable(root: Path) -> None:
    for path in [root, *root.rglob("*")]:
        path.chmod(path.stat().st_mode | stat.S_IWUSR)
