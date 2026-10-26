"""Operator command-line interface."""

from __future__ import annotations

import argparse
import json
import sys
from importlib.resources import files
from pathlib import Path
from typing import Any

from .adapter import SplitName
from .config import load_config
from .controller import CampaignController
from .errors import AHERunnerError
from .fixed_evaluation import compare_evaluations, evaluate_fixed
from .supervisor import supervise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ahe",
        description="Run checkpointed Agentic Harness Engineering campaigns.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    init_parser = subparsers.add_parser("init-project")
    init_parser.add_argument("path", type=Path)
    for command in (
        "conformance",
        "validate",
        "run",
        "resume",
        "status",
        "close-evolution",
        "run-validation",
        "run-final-test",
    ):
        command_parser = subparsers.add_parser(command)
        command_parser.add_argument("--config", type=Path, required=True)
    round_parser = subparsers.add_parser("run-round")
    round_parser.add_argument("--config", type=Path, required=True)
    round_parser.add_argument("--round", type=int, required=True)
    retry_parser = subparsers.add_parser("retry-stage")
    retry_parser.add_argument("--config", type=Path, required=True)
    retry_parser.add_argument("--stage", required=True)
    retry_parser.add_argument("--reason", required=True)
    extend_parser = subparsers.add_parser(
        "extend-rounds",
        help=(
            "Accept a raised campaign.rounds for an existing campaign "
            "(dry run unless --apply)."
        ),
    )
    extend_parser.add_argument("--config", type=Path, required=True)
    extend_parser.add_argument("--reason", required=True)
    extend_parser.add_argument("--apply", action="store_true")
    extend_parser.add_argument(
        "--reopen-closed-evolution",
        action="store_true",
        help=(
            "Also extend a closed campaign: move the closing stages and their "
            "outputs to superseded/closure-NN/."
        ),
    )
    supervise_parser = subparsers.add_parser(
        "supervise",
        help="Run a campaign, resuming after infrastructure stops.",
    )
    supervise_parser.add_argument("--config", type=Path, required=True)
    supervise_parser.add_argument("--max-consecutive-failures", type=int, default=5)
    supervise_parser.add_argument("--initial-backoff-seconds", type=float, default=60.0)
    supervise_parser.add_argument("--max-backoff-seconds", type=float, default=1800.0)
    evaluate_parser = subparsers.add_parser(
        "evaluate",
        help="Evaluate one fixed harness or checkpoint on a split or task list.",
    )
    evaluate_parser.add_argument("--config", type=Path, required=True)
    evaluate_parser.add_argument("--output", type=Path, required=True)
    evaluate_source = evaluate_parser.add_mutually_exclusive_group(required=True)
    evaluate_source.add_argument("--checkpoint")
    evaluate_source.add_argument("--harness", type=Path)
    evaluate_tasks = evaluate_parser.add_mutually_exclusive_group(required=True)
    evaluate_tasks.add_argument("--split", choices=[name.value for name in SplitName])
    evaluate_tasks.add_argument("--tasks", type=Path)
    evaluate_parser.add_argument("--trials", type=int)
    evaluate_parser.add_argument("--seed", type=int)
    evaluate_parser.add_argument("--label")
    compare_parser = subparsers.add_parser(
        "compare",
        help="Paired task-level comparison of two evaluations.",
    )
    compare_parser.add_argument("--baseline", type=Path, required=True)
    compare_parser.add_argument("--candidate", type=Path, required=True)
    args = parser.parse_args(argv)

    try:
        if args.command == "init-project":
            result = init_project(args.path)
        elif args.command == "supervise":
            result = supervise(
                args.config,
                max_consecutive_failures=args.max_consecutive_failures,
                initial_backoff_seconds=args.initial_backoff_seconds,
                max_backoff_seconds=args.max_backoff_seconds,
            )
        elif args.command == "compare":
            result = compare_evaluations(args.baseline, args.candidate)
        else:
            controller = CampaignController(load_config(args.config))
            result = _dispatch(controller, args)
        print(json.dumps(result, indent=2, sort_keys=True, default=str))
        return 0
    except AHERunnerError as exc:
        print(
            json.dumps(
                {
                    "status": "error",
                    "type": type(exc).__name__,
                    "message": str(exc),
                },
                indent=2,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2


def init_project(path: Path) -> dict[str, Any]:
    destination = path.resolve()
    if destination.exists() and any(destination.iterdir()):
        raise AHERunnerError(f"project directory is not empty: {destination}")
    destination.mkdir(parents=True, exist_ok=True)
    template = files("ahe_runner").joinpath("templates", "project")
    _copy_resource_tree(template, destination)
    return {
        "project_dir": str(destination),
        "config": str(destination / "ahe.yaml"),
        "next_command": (f"ahe conformance --config {destination / 'ahe.yaml'}"),
    }


def _dispatch(controller: CampaignController, args: argparse.Namespace) -> Any:
    if args.command == "conformance":
        return controller.conformance()
    if args.command == "validate":
        return controller.validate_project()
    if args.command == "run":
        return controller.run()
    if args.command == "resume":
        return controller.resume()
    if args.command == "status":
        return controller.status()
    if args.command == "run-round":
        return controller.run_round(args.round)
    if args.command == "retry-stage":
        return controller.retry_stage(name=args.stage, reason=args.reason)
    if args.command == "extend-rounds":
        return controller.extend_rounds(
            reason=args.reason,
            apply=args.apply,
            reopen_closed_evolution=args.reopen_closed_evolution,
        )
    if args.command == "evaluate":
        return evaluate_fixed(
            controller,
            output=args.output,
            checkpoint=args.checkpoint,
            harness=args.harness,
            split=args.split,
            tasks_file=args.tasks,
            trials=args.trials,
            seed=args.seed,
            label=args.label,
        )
    if args.command == "close-evolution":
        return controller.close_evolution()
    if args.command == "run-validation":
        return controller.run_validation()
    if args.command == "run-final-test":
        return controller.run_final_test()
    raise AssertionError(args.command)


def _copy_resource_tree(source: Any, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for entry in source.iterdir():
        if entry.name == "__pycache__" or entry.name.endswith((".pyc", ".pyo")):
            continue
        target = destination / entry.name
        if entry.is_dir():
            _copy_resource_tree(entry, target)
        else:
            target.write_bytes(entry.read_bytes())


if __name__ == "__main__":
    raise SystemExit(main())
