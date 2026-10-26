"""RRSI: Regularized Recursive Self-Improvement of agent harnesses.

Xia et al., "RRSI: Regularized Recursive Self-Improvement of Agent
Harnesses", arXiv:2609.24972; reference implementation
github.com/google-research/rrsi (Apache-2.0). This module ports the method
onto the runner: the harness is a NexAU harness, the evolve set is the
training split, every candidate is an immutable checkpoint, and every step
is a durable stage.

Before round 0: evaluate C0 on train, price its cost, and calibrate the
noise band delta. Round t (0-indexed) of T = campaign.rounds:

    1. Analyze: the analyst dispatches digesters over the incumbent's
       training evidence and reports failure modes, capability gaps, and
       success habits (F_t).
    2. Directives: edit budget b_t, stall flag sigma_t, untried components,
       reserved exploration slots, and the prune set B_t from the history.
    3. For each of m variants: the proposer edits a workspace seeded from the
       incumbent and submits tagged edits; the critic screens the diff for
       leakage before any evaluation, with bounded repair rounds; the runner
       validates and freezes the candidate as a new checkpoint.
    4. Evaluate every frozen candidate on the full training split.
    5. Select (Algorithm 2): noise-adjusted floor, cost rule, within-band
       rule; the best admissible candidate becomes the incumbent, otherwise
       the incumbent stays.

The algorithm picks the final incumbent. Its nominees for validation
selection are the incumbents along the accepted path.
"""

from __future__ import annotations

import inspect
import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import nexau

from ...adapter import EvaluationResult
from ...config import model_identity
from ...errors import IntegrityError
from ...harness import validate_harness
from ...io import atomic_write_json, read_json, sha256_json
from ...meta import MetaBackend, RoleSpec, RoleTool, dispatch
from ...state import StageContext, StageResult
from ...workspace import archive_uncommitted_workspace, git_output
from ..base import AlgorithmIdentity, EvolutionAlgorithm, EvolutionOutcome
from ..kernel import EvolutionKernel
from . import scripted, submission
from .history import History, exploration, stall_flag
from .objective import (
    calibrate_bootstrap,
    calibrate_repeats,
    check_metric,
    task_means,
    trial_rows,
    weighted_score,
)
from .schedule import edit_budget
from .selection import Candidate, Weights, select_round
from .settings import RrsiSettings

RESOURCES = Path(__file__).resolve().parent / "resources"
PROMPTS = RESOURCES / "prompts"
TOOL_DESCRIPTIONS = RESOURCES / "tool_descriptions"
VARIANT_LABELS = "ABCDEFGH"
_READ_TOOLS = ("read_file", "search_file_content", "glob", "list_directory")
_WRITE_TOOLS = ("write_file", "replace", "delete_path")
_EVOLUTION_GUIDE = (
    Path(__file__).resolve().parents[2] / "meta" / "skills" / "nexau-evolution-guide"
)
_DIFF_CAP = 120_000
_CREDENTIAL = re.compile(
    r"AIza[0-9A-Za-z_-]{35}|sk-[A-Za-z0-9]{20,}|gh[pousr]_[A-Za-z0-9]{30,}"
    r"|api_key\s*=\s*[\"'][^\"']{8,}"
)


def _tool(name: str) -> RoleTool:
    return RoleTool(
        name=name,
        description_path=TOOL_DESCRIPTIONS / f"{name}.tool.yaml",
        binding=f"ahe_runner.algorithms.rrsi.tools:{name}",
    )


class RrsiAlgorithm(EvolutionAlgorithm):
    settings_model = RrsiSettings
    default_selection_policy = "algorithm"
    supports_round_extension = False
    roles = ("analyst", "digester", "proposer", "critic")

    settings: RrsiSettings

    def identity(self) -> AlgorithmIdentity:
        return AlgorithmIdentity(
            name="rrsi",
            version="1",
            description=(
                "Regularized Recursive Self-Improvement (arXiv:2609.24972): "
                "annealed edit budget, evidence-aware history, structured "
                "exploration, leakage critic, noise-adjusted and cost-aware "
                "selection, structural pruning"
            ),
        )

    def resources(self) -> tuple[Path, ...]:
        return (Path(__file__).resolve().parent,)

    def check(self, kernel: EvolutionKernel) -> list[str]:
        problems: list[str] = []
        if kernel.rounds < 1:
            problems.append("RRSI needs campaign.rounds >= 1")
        if (
            self.settings.cost == "priced"
            and kernel.config.document.models.evaluated.pricing is None
        ):
            problems.append(
                "RRSI's cost rule prices the evaluated agent's tokens: set "
                "models.evaluated.pricing, or algorithm.settings.cost: disabled"
            )
        if self.settings.noise.method == "bootstrap" and kernel.train_trials < 2:
            problems.append("noise.method=bootstrap needs evaluation.train_trials >= 2")
        meta = kernel._controller.meta
        if type(meta).run_role is MetaBackend.run_role:
            problems.append(
                f"meta backend {type(meta).__name__} cannot run algorithm roles"
            )
        return problems

    # -- the search ------------------------------------------------------

    def evolve(self, kernel: EvolutionKernel) -> EvolutionOutcome:
        settings = self.settings
        rounds = kernel.rounds
        incumbent = "C0"
        score = self._score(kernel, incumbent)
        cost = self._cost(kernel, incumbent)
        calibration = self._calibrate(kernel)
        delta = float(calibration["delta"])
        weights = Weights(**calibration["weights"])
        stall_window, prune_window = settings.windows(rounds)
        best = score
        trajectory = [score]
        path = [incumbent]
        history = History()
        scoreboard: list[dict[str, Any]] = []
        prior: dict[str, Any] = {}
        rounds_log: list[dict[str, Any]] = []
        for t in range(rounds):
            evidence_dir = kernel.evidence(incumbent)
            analysis = self._analysis(
                kernel,
                t=t,
                incumbent=incumbent,
                evidence_dir=evidence_dir,
                prior=prior,
            )
            prior = {
                "failure_modes": analysis["report"].get("failure_modes") or [],
                "success_habits": analysis["report"].get("success_habits") or [],
            }
            budget = edit_budget(
                t, rounds, settings.edit_budget_min, settings.edit_budget_max
            )
            sigma = stall_flag(trajectory, t, stall_window, delta)
            explore = exploration(
                sigma, history.tried(), settings.reserved_exploration_slots
            )
            directives = {
                "t": t,
                "rounds": rounds,
                "incumbent": incumbent,
                "S_incumbent": score,
                "C_incumbent": cost,
                "S_star": best,
                "delta": delta,
                "weights": calibration["weights"],
                "b_t": budget,
                "sigma_t": sigma,
                "tried": sorted(history.tried()),
                "explore": explore,
                "prune_set": history.prune_set(t, prune_window),
                "history": history.render(),
                "scoreboard": scoreboard[-20:],
            }
            round_dir = kernel.algorithm_dir / "rounds" / f"r{t:03d}"
            round_dir.mkdir(parents=True, exist_ok=True)
            atomic_write_json(round_dir / "directives.json", directives)
            drafts = []
            for index in range(settings.variants_per_round):
                variant = VARIANT_LABELS[index]
                reserved = bool(
                    sigma
                    and explore["untried"]
                    and index
                    >= settings.variants_per_round - settings.reserved_exploration_slots
                )
                drafts.append(
                    self._draft(
                        kernel,
                        t=t,
                        variant=variant,
                        incumbent=incumbent,
                        evidence_dir=evidence_dir,
                        analysis=analysis,
                        directives=directives,
                        reserved=reserved,
                    )
                )
            candidates = []
            for draft in drafts:
                label = draft.get("checkpoint")
                if draft.get("gate_failure") is None and label is not None:
                    candidates.append(
                        Candidate(
                            variant=draft["variant"],
                            checkpoint=label,
                            components=[edit["component"] for edit in draft["edits"]],
                            score=self._score(kernel, label),
                            cost=self._cost(kernel, label),
                        )
                    )
                else:
                    candidates.append(
                        Candidate(
                            variant=draft["variant"],
                            checkpoint=None,
                            components=[
                                edit.get("component") for edit in draft["edits"]
                            ],
                            gate_failure=draft.get("gate_failure"),
                        )
                    )
            decision = self._select(
                kernel,
                t=t,
                candidates=candidates,
                incumbent=incumbent,
                score=score,
                cost=cost,
                best=best,
                delta=delta,
                weights=weights,
                accepted_counts=history.accepted_counts(),
            )
            winner = decision["winner"]
            for draft, row in zip(drafts, decision["decisions"], strict=True):
                if row["S"] is None:
                    history.append_candidate(
                        t=t,
                        variant=draft["variant"],
                        checkpoint=None,
                        edits=draft["edits"],
                        outcome=draft.get("gate_failure") or "not_evaluated",
                        delta_s=None,
                        delta_c=None,
                        accepted=False,
                        score=None,
                        cost=None,
                        detail=draft.get("detail", ""),
                    )
                    continue
                accepted = row["checkpoint"] == winner
                outcome = (
                    "ACCEPTED"
                    if accepted
                    else "LOST"
                    if row["admissible"]
                    else "REJECTED"
                )
                history.append_candidate(
                    t=t,
                    variant=draft["variant"],
                    checkpoint=row["checkpoint"],
                    edits=draft["edits"],
                    outcome=outcome,
                    delta_s=row["delta_S"],
                    delta_c=row["delta_C"],
                    accepted=accepted,
                    score=row["S"],
                    cost=row["C"],
                    detail=row["reason"],
                )
                scoreboard.extend(
                    self._attribute(
                        kernel,
                        t=t,
                        variant=draft["variant"],
                        edits=draft["edits"],
                        incumbent=incumbent,
                        candidate=row["checkpoint"],
                    )
                )
            if winner is not None:
                incumbent = winner
                chosen = next(
                    row for row in decision["decisions"] if row["checkpoint"] == winner
                )
                score, cost = chosen["S"], chosen["C"]
                best = max(best, score)
                path.append(incumbent)
            trajectory.append(score)
            rounds_log.append(
                {
                    "t": t,
                    "b_t": budget,
                    "sigma_t": sigma,
                    "winner": winner,
                    "incumbent": incumbent,
                    "S": score,
                    "C": cost,
                    "S_star": best,
                    "decisions": decision["decisions"],
                }
            )
            self._write_reports(
                kernel,
                calibration=calibration,
                history=history,
                scoreboard=scoreboard,
                rounds_log=rounds_log,
                trajectory=trajectory,
                path=path,
            )
        if rounds == 0:
            self._write_reports(
                kernel,
                calibration=calibration,
                history=history,
                scoreboard=scoreboard,
                rounds_log=rounds_log,
                trajectory=trajectory,
                path=path,
            )
        return EvolutionOutcome(
            pick=incumbent,
            nominees=path,
            summary=(
                f"RRSI: {len(path) - 1} accepted candidate(s) over {rounds} round(s); "
                f"final incumbent {incumbent} with S = {score:.4f} "
                f"(C0 {trajectory[0]:.4f}, noise band {delta:.4f})"
            ),
            data={
                "delta": delta,
                "weights": calibration["weights"],
                "trajectory": trajectory,
                "accepted_path": path,
            },
        )

    # -- measurement -----------------------------------------------------

    def _score(self, kernel: EvolutionKernel, label: str, repeat: int = 1) -> float:
        result = kernel.evaluate_train(label, repeat=repeat)
        rows = trial_rows(result, kernel.train_evaluation_dir(label, repeat=repeat))
        value = weighted_score(rows)
        check_metric(value, result, self.settings.objective.check_metric)
        return value

    def _rows(self, kernel: EvolutionKernel, label: str) -> dict[str, list]:
        result = kernel.evaluate_train(label)
        return trial_rows(result, kernel.train_evaluation_dir(label))

    def _cost(self, kernel: EvolutionKernel, label: str) -> float | None:
        if self.settings.cost == "disabled":
            return None
        value = kernel.cost(label).get("mean_cost_per_trial")
        return float(value) if value is not None else None

    def _calibrate(self, kernel: EvolutionKernel) -> dict[str, Any]:
        noise = self.settings.noise
        evaluations: list[EvaluationResult] = [kernel.evaluate_train("C0")]
        if noise.method == "repeats":
            evaluations += [
                kernel.evaluate_train("C0", repeat=repeat)
                for repeat in range(2, noise.repeats + 1)
            ]
        output = kernel.algorithm_dir / "calibration.json"

        def action(context: StageContext) -> StageResult:
            del context
            if noise.method == "fixed":
                payload: dict[str, Any] = {
                    "method": "fixed",
                    "delta": float(noise.delta or 0.0),
                }
            elif noise.method == "repeats":
                payload = calibrate_repeats(
                    [
                        self._score(kernel, "C0", repeat=repeat)
                        for repeat in range(1, noise.repeats + 1)
                    ],
                    z=noise.z,
                )
            else:
                payload = calibrate_bootstrap(
                    self._rows(kernel, "C0"),
                    trials=kernel.train_trials,
                    z=noise.z,
                    resamples=noise.bootstrap_resamples,
                    seed=noise.bootstrap_seed,
                )
            # A deterministic benchmark measures delta = 0; the within-band
            # weight is derived against a tiny floor instead of dividing by 0.
            payload["weights"] = self.settings.weights(max(payload["delta"], 1e-9))
            payload["windows"] = dict(
                zip(
                    ("stall_window", "prune_window"),
                    self.settings.windows(kernel.rounds),
                    strict=True,
                )
            )
            atomic_write_json(output, payload)
            return StageResult(outputs=(output,), data=payload)

        return kernel.stage(
            "calibration",
            semantic_inputs={
                "noise": noise.model_dump(mode="json"),
                "evaluations": [result.evaluation_id for result in evaluations],
                "weights": self.settings.within_band.model_dump(mode="json"),
                "beta": [self.settings.beta0, self.settings.beta1],
            },
            action=action,
            retryable=False,
        )

    # -- analysis ----------------------------------------------------------

    def _analysis(
        self,
        kernel: EvolutionKernel,
        *,
        t: int,
        incumbent: str,
        evidence_dir: Path,
        prior: dict[str, Any],
    ) -> dict[str, Any]:
        evaluation = kernel.evaluate_train(incumbent)
        manifest = read_json(evidence_dir / "evidence_manifest.json")
        tasks = _task_table(manifest, task_means(self._rows(kernel, incumbent)))
        prior_names = {
            "failure_modes": [
                {"mode": mode.get("mode"), "description": mode.get("description")}
                for mode in prior.get("failure_modes") or []
                if isinstance(mode, dict)
            ],
            "success_habits": [
                {"habit": habit.get("habit"), "description": habit.get("description")}
                for habit in prior.get("success_habits") or []
                if isinstance(habit, dict)
            ],
        }
        root = kernel.algorithm_dir / "rounds" / f"r{t:03d}" / "analysis"
        limits = self.settings.roles
        concurrency = (
            limits.digest_concurrency
            or kernel.config.document.meta.max_concurrent_model_calls
        )

        def action(context: StageContext) -> StageResult:
            run_dir = root / f"attempt-{context.attempt:04d}"
            run_dir.mkdir(parents=True, exist_ok=True)
            known = {row["task"] for row in tasks}
            counter = iter(range(1, 1_000_000))
            lock = threading.Lock()

            def digest_many(requests: list[dict[str, Any]]) -> list[dict[str, Any]]:
                if not isinstance(requests, list) or not requests:
                    raise ValueError("requests must be a non-empty list")
                if len(requests) > limits.digests_per_call:
                    raise ValueError(
                        f"at most {limits.digests_per_call} requests per call"
                    )
                jobs = []
                for request in requests:
                    task = str(request.get("task", ""))
                    lens = str(request.get("lens", "failure"))
                    if task not in known or lens not in submission._LENS_KEYS:
                        jobs.append((task, lens, None, None))
                        continue
                    with lock:
                        number = next(counter)
                    jobs.append(
                        (task, lens, list(request.get("questions") or [])[:5], number)
                    )

                def run(job: tuple[str, str, list[str] | None, int | None]) -> dict:
                    task, lens, questions, number = job
                    if number is None:
                        return {
                            "task": task,
                            "lens": lens,
                            "error": "unknown task key or lens",
                        }
                    return self._digest(
                        kernel,
                        task=task,
                        lens=lens,
                        questions=questions or [],
                        evidence_dir=evidence_dir,
                        incumbent=incumbent,
                        runtime_dir=run_dir / "digests" / f"{number:04d}-{lens}",
                    )

                with ThreadPoolExecutor(max_workers=concurrency) as executor:
                    return list(executor.map(run, jobs))

            with dispatch.registered(digest_many) as token:
                query = "\n\n".join(
                    [
                        _facts_block(kernel, incumbent),
                        "## Task table (lowest scores first)",
                        json.dumps(tasks, indent=1),
                        "## Prior failure-mode and success-habit names",
                        json.dumps(prior_names, indent=1),
                        f"Digest requests per call: at most {limits.digests_per_call}.",
                        "Dispatch digesters, then submit the report.",
                    ]
                )
                spec = RoleSpec(
                    role="analyst",
                    agent_name="RRSI-Analyst",
                    description="Aggregates digests into a three-lens report.",
                    prompt_path=PROMPTS / "analyst.md",
                    supplement=kernel.role_supplement("analyst"),
                    query=query,
                    runtime_dir=run_dir / "analyst",
                    max_iterations=limits.analyst_max_iterations,
                    default_root="evidence",
                    read_roots={"evidence": evidence_dir},
                    algorithm_tools=(
                        _tool("rrsi_digest_tasks"),
                        _tool("rrsi_submit_report"),
                    ),
                    stop_tools=("rrsi_submit_report",),
                    context={"rrsi_dispatch_token": token},
                    profile="reader",
                    scripted=scripted.analyst,
                )
                kernel.run_role(spec)
            report_path = run_dir / "analyst" / submission.REPORT_FILE
            if not report_path.is_file():
                raise IntegrityError("the RRSI analyst submitted no report")
            digests = [
                read_json(path)
                for path in sorted(
                    (run_dir / "digests").glob("*/" + submission.DIGEST_FILE)
                )
            ]
            data = {
                "run_dir": str(run_dir),
                "report": read_json(report_path),
                "digests": digests,
            }
            atomic_write_json(run_dir / "analysis.json", data)
            return StageResult(outputs=(run_dir,), data=data)

        return kernel.stage(
            f"round-{t:03d}.analysis",
            semantic_inputs={
                "incumbent": incumbent,
                "evaluation_id": evaluation.evaluation_id,
                "prior_names": prior_names,
                "meta_model": model_identity(kernel.config.document.models.meta),
            },
            action=action,
        )

    def _digest(
        self,
        kernel: EvolutionKernel,
        *,
        task: str,
        lens: str,
        questions: list[str],
        evidence_dir: Path,
        incumbent: str,
        runtime_dir: Path,
    ) -> dict[str, Any]:
        detail = _task_detail_path(evidence_dir, task)
        query = "\n\n".join(
            [
                f"Assigned task: {task}",
                f"Lens: {lens}",
                f"Task detail: evidence:{detail.relative_to(evidence_dir).as_posix()}",
                (
                    "Questions from the analyst you MUST answer:\n"
                    + "\n".join(f"- {question}" for question in questions)
                    if questions
                    else "No specific questions."
                ),
            ]
        )
        spec = RoleSpec(
            role="digester",
            agent_name="RRSI-Digester",
            description="Digests one task's trials through one lens.",
            prompt_path=PROMPTS / "digester.md",
            supplement=kernel.role_supplement("digester"),
            query=query,
            runtime_dir=runtime_dir,
            max_iterations=self.settings.roles.digester_max_iterations,
            default_root="evidence",
            read_roots={
                "evidence": evidence_dir,
                "raw_evaluation": kernel.train_evaluation_dir(incumbent),
            },
            tools=_READ_TOOLS,
            algorithm_tools=(_tool("rrsi_submit_digest"),),
            stop_tools=("rrsi_submit_digest",),
            context={"rrsi_task": task, "rrsi_lens": lens},
            profile="reader",
            # The analyst's stage already ran the preflight for this model.
            preflight=False,
            scripted=scripted.digester,
        )
        try:
            kernel.run_role(spec)
        except Exception as exc:
            if not _is_role_failure(exc):
                raise
            return {"task": task, "lens": lens, "error": f"digester failed: {exc}"}
        path = runtime_dir / submission.DIGEST_FILE
        if not path.is_file():
            return {"task": task, "lens": lens, "error": "digester returned no digest"}
        return read_json(path)

    # -- drafting ------------------------------------------------------------

    def _draft(
        self,
        kernel: EvolutionKernel,
        *,
        t: int,
        variant: str,
        incumbent: str,
        evidence_dir: Path,
        analysis: dict[str, Any],
        directives: dict[str, Any],
        reserved: bool,
    ) -> dict[str, Any]:
        label = kernel.allocate_checkpoint(f"rrsi.r{t:03d}.{variant}")
        root = kernel.algorithm_dir / "rounds" / f"r{t:03d}" / variant
        workspace = kernel.candidate_workspace(
            label=label,
            parent=incumbent,
            attempt=1,
            recovery_dir=root / "workspace-recovery",
        )
        base = git_output(workspace, "rev-list", "--max-parents=0", "HEAD").strip()
        start = base
        review: dict[str, Any] | None = None
        candidate: dict[str, Any] | None = None
        for repair in range(self.settings.repair_rounds + 1):
            proposal = self._propose(
                kernel,
                t=t,
                variant=variant,
                label=label,
                incumbent=incumbent,
                workspace=workspace,
                base=base,
                start=start,
                evidence_dir=evidence_dir,
                analysis=analysis,
                directives=directives,
                reserved=reserved,
                repair=repair,
                review=review,
                candidate=candidate,
            )
            if proposal.get("candidate") is None:
                return {
                    "variant": variant,
                    "checkpoint": None,
                    "edits": (candidate or {}).get("edits") or [],
                    "gate_failure": "no_proposal",
                    "detail": str(proposal.get("detail") or ""),
                }
            candidate = proposal["candidate"]
            start = candidate["head"]
            review = self._critic(
                kernel,
                t=t,
                variant=variant,
                label=label,
                workspace=workspace,
                base=base,
                candidate=candidate,
                repair=repair,
            )
            if review["verdict"] == "accept":
                break
        assert candidate is not None and review is not None
        if review["verdict"] != "accept":
            return {
                "variant": variant,
                "checkpoint": None,
                "edits": candidate["edits"],
                "gate_failure": "critic_reject",
                "detail": "; ".join(review.get("reasons") or [])[:600],
            }
        _reset(workspace, candidate["head"], root / "workspace-recovery")
        validation = kernel.validate_candidate(
            label=label,
            parent=incumbent,
            workspace=workspace,
        )
        if not validation.get("valid"):
            errors = (validation.get("generic") or {}).get("errors") or []
            errors += (validation.get("adapter") or {}).get("errors") or []
            return {
                "variant": variant,
                "checkpoint": None,
                "edits": candidate["edits"],
                "gate_failure": "invalid_candidate",
                "detail": "; ".join(errors)[:600],
            }
        kernel.freeze_candidate(
            label=label,
            parent=incumbent,
            workspace=workspace,
            validation=validation,
        )
        kernel.evaluate_train(label)
        return {
            "variant": variant,
            "checkpoint": label,
            "edits": candidate["edits"],
            "gate_failure": None,
            "summary": candidate["summary"],
        }

    def _propose(
        self,
        kernel: EvolutionKernel,
        *,
        t: int,
        variant: str,
        label: str,
        incumbent: str,
        workspace: Path,
        base: str,
        start: str,
        evidence_dir: Path,
        analysis: dict[str, Any],
        directives: dict[str, Any],
        reserved: bool,
        repair: int,
        review: dict[str, Any] | None,
        candidate: dict[str, Any] | None,
    ) -> dict[str, Any]:
        root = kernel.algorithm_dir / "rounds" / f"r{t:03d}" / variant
        parent_dir = kernel.checkpoint_dir(incumbent) / "harness"
        reserved_untried = directives["explore"]["untried"] if reserved else []

        def action(context: StageContext) -> StageResult:
            run_dir = root / f"propose-{repair}" / f"attempt-{context.attempt:04d}"
            _reset(workspace, start, run_dir / "recovery")
            parent_report = validate_harness(
                parent_dir,
                kernel.harness_manifest,
                project_root=kernel.config.project_root,
                run_nexau_smoke=False,
            )
            query = self._proposer_query(
                kernel,
                variant=variant,
                directives=directives,
                analysis=analysis,
                reserved_untried=reserved_untried,
                review=review,
                candidate=candidate,
            )
            spec = RoleSpec(
                role="proposer",
                agent_name="RRSI-Proposer",
                description="Drafts one regularized candidate harness.",
                prompt_path=PROMPTS / "proposer.md",
                supplement=kernel.role_supplement("proposer"),
                query=query,
                runtime_dir=run_dir,
                max_iterations=self.settings.roles.proposer_max_iterations,
                default_root="workspace",
                read_roots={
                    "evidence": evidence_dir,
                    "raw_evaluation": kernel.train_evaluation_dir(incumbent),
                    "analysis": Path(analysis["run_dir"]),
                    "framework": Path(inspect.getsourcefile(nexau) or "").parent,
                },
                write_roots={"workspace": workspace},
                tools=(
                    *_READ_TOOLS,
                    *_WRITE_TOOLS,
                    "run_shell_command",
                    "git_status",
                    "git_diff",
                    "validate_candidate",
                ),
                algorithm_tools=(_tool("rrsi_submit_candidate"),),
                stop_tools=("rrsi_submit_candidate",),
                skills=(_EVOLUTION_GUIDE,),
                context={
                    "workspace_dir": str(workspace),
                    "checkpoint": label,
                    "parent_checkpoint": incumbent,
                    "parent_harness_dir": str(parent_dir),
                    "harness_manifest_path": str(kernel.config.harness_manifest_path),
                    "project_root": str(kernel.config.project_root),
                    "config_path": str(kernel.config.config_path),
                    "training_task_ids": list(kernel.train_split.task_ids),
                    "parent_runtime_owned_hashes": parent_report[
                        "runtime_owned_hashes"
                    ],
                    "rrsi_base_commit": base,
                    "rrsi_edit_budget": directives["b_t"],
                    "rrsi_reserved_untried": reserved_untried,
                    "rrsi_variant": variant,
                },
                profile="writer",
                scripted=scripted.proposer,
            )
            kernel.run_role(spec)
            submitted = submission.load_candidate(run_dir)
            if submitted is not None:
                head = git_output(workspace, "rev-parse", "HEAD").strip()
                dirty = git_output(workspace, "status", "--porcelain").strip()
                if submitted["head"] != head or dirty:
                    raise IntegrityError(
                        "the proposer changed the workspace after submitting"
                    )
            data = {
                "candidate": submitted,
                "detail": None
                if submitted
                else "the proposer ended without a successful submission",
            }
            atomic_write_json(run_dir / "proposal.json", data)
            return StageResult(outputs=(run_dir,), data=data)

        return kernel.stage(
            f"round-{t:03d}.{variant}.propose-{repair}",
            semantic_inputs={
                "checkpoint": label,
                "incumbent": incumbent,
                "start_commit": start,
                "analysis": sha256_json(analysis["report"]),
                "directives": sha256_json(directives),
                "reserved": reserved_untried,
                "review": review,
                "meta_model": model_identity(kernel.config.document.models.meta),
            },
            action=action,
        )

    def _critic(
        self,
        kernel: EvolutionKernel,
        *,
        t: int,
        variant: str,
        label: str,
        workspace: Path,
        base: str,
        candidate: dict[str, Any],
        repair: int,
    ) -> dict[str, Any]:
        root = kernel.algorithm_dir / "rounds" / f"r{t:03d}" / variant

        def action(context: StageContext) -> StageResult:
            run_dir = root / f"critic-{repair}" / f"attempt-{context.attempt:04d}"
            run_dir.mkdir(parents=True, exist_ok=True)
            diff = git_output(
                workspace,
                "diff",
                "--no-ext-diff",
                base,
                candidate["head"],
                "--",
                ".",
                ":(exclude)change_manifest.json",
            )
            edits = [
                {key: value for key, value in edit.items() if key != "files"}
                for edit in candidate["edits"]
            ]
            review_dir = run_dir / "candidate"
            review_dir.mkdir(exist_ok=True)
            (review_dir / "diff.patch").write_text(diff, encoding="utf-8")
            atomic_write_json(review_dir / "edits.json", edits)
            hits = _precheck(diff, kernel.prompt_context.task_id_leakage_terms)
            if hits:
                verdict = {
                    "verdict": "reject",
                    "reasons": [f"precheck: {hit}" for hit in hits],
                    "risk_notes": [],
                }
            elif not diff.strip():
                verdict = {
                    "verdict": "reject",
                    "reasons": ["empty diff"],
                    "risk_notes": [],
                }
            else:
                query = "\n\n".join(
                    [
                        f"CANDIDATE SUMMARY: {candidate['summary']}",
                        "## Declared edits",
                        json.dumps(edits, indent=1)[:20_000],
                        "## Diff against the incumbent",
                        diff[:_DIFF_CAP]
                        + (
                            "\n...[diff truncated; the full diff is "
                            "candidate:diff.patch]"
                            if len(diff) > _DIFF_CAP
                            else ""
                        ),
                    ]
                )
                spec = RoleSpec(
                    role="critic",
                    agent_name="RRSI-Critic",
                    description="Screens a candidate diff for leakage.",
                    prompt_path=PROMPTS / "critic.md",
                    supplement=kernel.role_supplement("critic"),
                    query=query,
                    runtime_dir=run_dir / "critic",
                    max_iterations=self.settings.roles.critic_max_iterations,
                    default_root="candidate",
                    read_roots={"candidate": review_dir},
                    tools=("read_file",),
                    algorithm_tools=(_tool("rrsi_submit_review"),),
                    stop_tools=("rrsi_submit_review",),
                    context={
                        "rrsi_training_task_ids": list(kernel.train_split.task_ids)
                    },
                    profile="reader",
                    scripted=scripted.critic,
                )
                kernel.run_role(spec)
                path = run_dir / "critic" / submission.REVIEW_FILE
                verdict = (
                    read_json(path)
                    if path.is_file()
                    else {
                        "verdict": "reject",
                        "reasons": ["the critic returned no verdict"],
                        "risk_notes": [],
                    }
                )
            atomic_write_json(run_dir / "verdict.json", verdict)
            return StageResult(outputs=(run_dir,), data=verdict)

        return kernel.stage(
            f"round-{t:03d}.{variant}.critic-{repair}",
            semantic_inputs={
                "checkpoint": label,
                "head": candidate["head"],
                "edits": sha256_json(candidate["edits"]),
                "meta_model": model_identity(kernel.config.document.models.meta),
            },
            action=action,
        )

    def _proposer_query(
        self,
        kernel: EvolutionKernel,
        *,
        variant: str,
        directives: dict[str, Any],
        analysis: dict[str, Any],
        reserved_untried: list[str],
        review: dict[str, Any] | None,
        candidate: dict[str, Any] | None,
    ) -> str:
        explore = directives["explore"]["text"]
        if reserved_untried:
            explore += (
                "\n\nRESERVED EXPLORATION SLOT: this variant holds one. At least "
                "one of your edits MUST be on a never-exercised component from: "
                f"{reserved_untried}."
            )
        variants = self.settings.variants_per_round
        sections = [
            _facts_block(kernel, directives["incumbent"], directives=directives),
            "## This variant",
            f"You are variant {variant} of round {directives['t']}. {variants} "
            "variants are drafted independently from the same incumbent this "
            "round and each is evaluated on the full evolve set; the best "
            f"admissible one becomes the next incumbent.",
            "## Edit history L_t (every measured edit: component, hypothesis, "
            "dS, dC, accepted). A rejected mechanism is negative evidence; do "
            "not redraw it unchanged.",
            json.dumps(directives["history"], indent=1),
            "## Attribution scoreboard (how past edits' predictions fared; "
            "unpredicted_regressions are tasks an edit likely broke)",
            json.dumps(directives["scoreboard"], indent=1),
            "## Exploration directives E_t",
            explore,
            "## Components to prune B_t (exercised, no strictly improving edit "
            "in the recent window; remove the accepted machinery listed)",
            json.dumps(directives["prune_set"], indent=1)
            if directives["prune_set"]
            else "(none)",
            "## Three-lens analysis report F_t (analysis:analysis.json also "
            "holds every digest)",
            json.dumps(analysis["report"], indent=1),
            "## This round's edit budget b_t",
            f"Ship AT MOST {directives['b_t']} independent edit(s) in this "
            "candidate. Ship fewer if the evidence supports fewer.",
        ]
        if review is not None and candidate is not None:
            sections += [
                "## Task: REPAIR ROUND",
                "Your previous edits for this candidate are already committed in "
                "the workspace. The reviewer raised the objections below. Fix "
                "ONLY what the objections require (remove leaked content, split "
                "or re-declare edits, wire up dead code, or delete the offending "
                "part) with minimal additional edits, then submit again with the "
                "corrected edits.",
                json.dumps(
                    {
                        "reasons": review.get("reasons"),
                        "risk_notes": review.get("risk_notes"),
                        "your_declared_edits": [
                            {k: v for k, v in edit.items() if k != "files"}
                            for edit in candidate["edits"]
                        ],
                    },
                    indent=1,
                ),
            ]
        else:
            sections += [
                "## Task",
                "Address the highest-impact failure modes or capability gaps "
                "within your edit budget. Implement the edits in the workspace, "
                "then submit them with rrsi_submit_candidate.",
            ]
        return "\n\n".join(sections)

    # -- selection and attribution ---------------------------------------------

    def _select(
        self,
        kernel: EvolutionKernel,
        *,
        t: int,
        candidates: list[Candidate],
        incumbent: str,
        score: float,
        cost: float | None,
        best: float,
        delta: float,
        weights: Weights,
        accepted_counts: dict[str, int],
    ) -> dict[str, Any]:
        output = kernel.algorithm_dir / "rounds" / f"r{t:03d}" / "decisions.json"
        inputs = {
            "incumbent": incumbent,
            "S_incumbent": score,
            "C_incumbent": cost,
            "S_star": best,
            "delta": delta,
            "weights": weights.__dict__,
            "accepted_counts": accepted_counts,
            "candidates": [candidate.__dict__ for candidate in candidates],
        }

        def action(context: StageContext) -> StageResult:
            del context
            winner, decisions = select_round(
                candidates,
                incumbent_score=score,
                incumbent_cost=cost,
                best_score=best,
                delta=delta,
                weights=weights,
                accepted_counts=accepted_counts,
            )
            payload = {
                "t": t,
                **inputs,
                "winner": winner.checkpoint if winner is not None else None,
                "decisions": [decision.to_json() for decision in decisions],
            }
            atomic_write_json(output, payload)
            return StageResult(outputs=(output,), data=payload)

        return kernel.stage(
            f"round-{t:03d}.select",
            semantic_inputs=inputs,
            action=action,
            retryable=False,
        )

    def _attribute(
        self,
        kernel: EvolutionKernel,
        *,
        t: int,
        variant: str,
        edits: list[dict[str, Any]],
        incumbent: str,
        candidate: str,
    ) -> list[dict[str, Any]]:
        before = task_means(self._rows(kernel, incumbent))
        after = task_means(self._rows(kernel, candidate))
        threshold = 1.0 / max(1, kernel.train_trials)
        drops = [
            task
            for task in before
            if task in after and after[task] - before[task] <= -threshold
        ]
        rows = []
        for edit in edits:
            predicted = [
                str(task)
                for task in edit.get("predicted_affected") or []
                if str(task) in before and str(task) in after
            ]
            hits = [task for task in predicted if after[task] > before[task]]
            rows.append(
                {
                    "t": t,
                    "variant": variant,
                    "checkpoint": candidate,
                    "edit_id": edit.get("id"),
                    "component": edit.get("component"),
                    "hypothesis": str(edit.get("hypothesis"))[:160],
                    "n_predicted": len(predicted),
                    "predicted_hit": hits,
                    "hit_rate": round(len(hits) / len(predicted), 2)
                    if predicted
                    else None,
                    "unpredicted_regressions": [
                        task for task in drops if task not in predicted
                    ][:12],
                }
            )
        return rows

    def _write_reports(
        self,
        kernel: EvolutionKernel,
        *,
        calibration: dict[str, Any],
        history: History,
        scoreboard: list[dict[str, Any]],
        rounds_log: list[dict[str, Any]],
        trajectory: list[float],
        path: list[str],
    ) -> None:
        """Human-readable state, rebuilt from completed stages on every run."""

        reports = kernel.algorithm_dir / "reports"
        reports.mkdir(parents=True, exist_ok=True)
        atomic_write_json(
            reports / "frontier.json",
            {
                "calibration": calibration,
                "trajectory": trajectory,
                "accepted_path": path,
                "rounds": rounds_log,
            },
        )
        (reports / "history.jsonl").write_text(
            "".join(
                json.dumps(record, sort_keys=True) + "\n" for record in history.records
            ),
            encoding="utf-8",
        )
        (reports / "attribution.jsonl").write_text(
            "".join(json.dumps(row, sort_keys=True) + "\n" for row in scoreboard),
            encoding="utf-8",
        )


def _facts_block(
    kernel: EvolutionKernel,
    incumbent: str,
    *,
    directives: dict[str, Any] | None = None,
) -> str:
    facts: dict[str, Any] = {
        "evolve_set_tasks": len(kernel.train_split.task_ids),
        "trials_per_task": kernel.train_trials,
        "score": (
            "S = sum over every trial of weight x trial score, divided by the "
            "sum of weights (the adapter's trial scores and weights)"
        ),
        "incumbent": incumbent,
        "benchmark_context": kernel.prompt_context.model_dump(mode="json"),
    }
    if directives is not None:
        pricing = kernel.config.document.models.evaluated.pricing
        facts.update(
            {
                "S_incumbent": directives["S_incumbent"],
                "S_star": directives["S_star"],
                "noise_band_delta": directives["delta"],
                "cost_per_trial_incumbent": directives["C_incumbent"],
                "cost_currency": pricing.currency if pricing else None,
                "selection_weights": directives["weights"],
            }
        )
    return "## Run facts\n\n" + json.dumps(facts, indent=1)


def _task_table(
    manifest: dict[str, Any],
    means: dict[str, float],
) -> list[dict[str, Any]]:
    rows = []
    for task in manifest.get("tasks") or []:
        key = task.get("task_key")
        rows.append(
            {
                "task": key,
                "state": task.get("state"),
                "score": round(means[key], 4) if key in means else None,
                "trials": task.get("trial_count"),
                "detail": task.get("detail_path"),
            }
        )
    return sorted(rows, key=lambda row: (row["score"] is None, row["score"] or 0.0))


def _task_detail_path(evidence_dir: Path, task: str) -> Path:
    manifest = read_json(evidence_dir / "evidence_manifest.json")
    for row in manifest.get("tasks") or []:
        if row.get("task_key") == task:
            relative = row.get("detail_path")
            if relative:
                return evidence_dir / str(relative)
    return evidence_dir / "tasks" / f"{task}.json"


def _precheck(diff: str, leakage_terms: tuple[str, ...] | list[str]) -> list[str]:
    added = "\n".join(
        line[1:]
        for line in diff.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    )
    hits = []
    if _CREDENTIAL.search(added):
        hits.append("credential in diff")
    lowered = added.casefold()
    for term in leakage_terms:
        if term and term.casefold() in lowered:
            hits.append(f"benchmark-specific term {term!r} in added lines")
    return hits


def _reset(workspace: Path, commit: str, recovery_dir: Path) -> None:
    """Return the workspace to ``commit``, archiving any other state."""

    head = git_output(workspace, "rev-parse", "HEAD").strip()
    dirty = git_output(workspace, "status", "--porcelain").strip()
    if head == commit and not dirty:
        return
    archive_uncommitted_workspace(workspace, recovery_dir)
    git_output(workspace, "reset", "--hard", "--quiet", commit)
    git_output(workspace, "clean", "-fdq")


def _is_role_failure(exc: Exception) -> bool:
    """A digester failure the analyst can work around, not infrastructure or
    configuration, which must stop the stage."""

    from ...errors import (
        ConfigurationError,
        InfrastructureError,
        IntegrityError,
        StageInterruptedError,
    )

    return not isinstance(
        exc,
        InfrastructureError
        | StageInterruptedError
        | ConfigurationError
        | IntegrityError,
    )
