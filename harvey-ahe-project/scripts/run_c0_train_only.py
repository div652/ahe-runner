"""Run only the C0 training evaluation of a campaign (campaign 9).

`ahe run` with `rounds: 0` would still start the Explore meta agents and then
evaluate the validation and test splits. `ahe close-evolution` does not
freeze C0 on a fresh campaign. This script runs the controller's own stages
for just the baseline freeze and the C0 training evaluation, so the artifacts
are the same as in a normal campaign. Rerunning it resumes the evaluation;
only unfinished trials run again.

Usage (from the repository root):
    .venv/bin/python harvey-ahe-project/scripts/run_c0_train_only.py \
        --config harvey-ahe-project/ahe.yaml
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ahe_runner.config import load_config
from ahe_runner.controller import CampaignController
from ahe_runner.errors import AHERunnerError


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    try:
        controller = CampaignController(load_config(args.config))
        if controller.config.document.campaign.rounds != 0:
            raise SystemExit("this script is for campaigns with rounds: 0")
        controller.prepare()
        result = controller._ensure_training_evaluation(0)
        controller._write_state()
    except AHERunnerError as exc:
        error = {"status": "error", "type": type(exc).__name__, "message": str(exc)}
        print(json.dumps(error, indent=2), file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "status": "complete",
                "evaluation_id": result.evaluation_id,
                "checkpoint": result.checkpoint,
                "split": result.split.value,
                "summary": result.summary.model_dump(mode="json"),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
