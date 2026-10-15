"""Operator command-line interface."""

from __future__ import annotations

import argparse
import json
import sys
from importlib.resources import files
from pathlib import Path
from typing import Any

from .config import load_config
from .controller import CampaignController
from .errors import AHERunnerError


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
    extend_parser.add_argument("--allow-runner-code-change", action="store_true")
    args = parser.parse_args(argv)

    try:
        if args.command == "init-project":
            result = init_project(args.path)
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
    if args.command == "extend-rounds":
        return controller.extend_rounds(
            reason=args.reason,
            apply=args.apply,
            allow_runner_change=args.allow_runner_code_change,
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
