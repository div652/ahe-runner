"""Deterministic backend used by the bundled executable example."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ahe_runner.evidence import load_evidence_manifest
from ahe_runner.io import atomic_write_json, sha256_file
from ahe_runner.workspace import git_output

from .base import MetaBackend

_RULES = (
    "Verify tool results before claiming that an action succeeded.",
    "Track confirmed state separately from unknown or inferred state.",
    "Stop repeated actions and change strategy when an action has no effect.",
    "Check completion criteria before ending the interaction.",
)


class ScriptedMetaBackend(MetaBackend):
    def ensure_exploration(
        self,
        *,
        seed_harness: Path,
        output_dir: Path,
        attempt_dir: Path,
    ) -> dict[str, Any]:
        del seed_harness, attempt_dir
        source = output_dir / "source-exploration" / "SKILL.md"
        web = output_dir / "web-research" / "SKILL.md"
        source.parent.mkdir(parents=True, exist_ok=True)
        web.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(
            "---\n"
            "name: source-exploration\n"
            "description: Inspect the pinned NexAU runtime before changing a harness.\n"
            "---\n\n"
            "# Source exploration\n\n"
            "Resolve configuration, registration, middleware ordering, tools, "
            "skills, sub-agents, state ownership, and validation against the "
            "installed NexAU source before implementing a change.\n",
            encoding="utf-8",
        )
        web.write_text(
            "---\n"
            "name: web-research\n"
            "description: Apply evidence-backed external agent-design research.\n"
            "---\n\n"
            "# External research\n\n"
            "Use external mechanisms only when completed training evidence "
            "supports the same failure class. Record uncertainty and regression "
            "risk rather than treating popularity as proof.\n",
            encoding="utf-8",
        )
        manifest = {
            "schema_version": 1,
            "backend": "scripted",
            "skills": {
                "source": {
                    "path": str(source),
                    "sha256": sha256_file(source),
                },
                "web": {
                    "path": str(web),
                    "sha256": sha256_file(web),
                },
            },
        }
        atomic_write_json(output_dir / "skills_manifest.json", manifest)
        return manifest

    def run_debugger(
        self,
        *,
        checkpoint: str,
        evidence_dir: Path,
        output_dir: Path,
        attempt_dir: Path,
    ) -> dict[str, Any]:
        del attempt_dir
        manifest = load_evidence_manifest(evidence_dir / "evidence_manifest.json")
        analyses: list[dict[str, Any]] = []
        for task in manifest.tasks:
            analyses.append(
                {
                    "task_key": task.task_key,
                    "task_state": task.state,
                    "outcome_summary": (
                        f"Deterministic example diagnosis for state {task.state}."
                    ),
                    "root_causes": [
                        {
                            "failure_class": "example-reliability-gap",
                            "explanation": (
                                "The example harness lacks one general reliability "
                                "instruction required by this synthetic task."
                            ),
                            "confidence": "high",
                        }
                    ],
                    "harness_opportunities": [
                        {
                            "component": "system_prompt",
                            "mechanism": "Add one general reliability rule.",
                        }
                    ],
                }
            )
        payload = {
            "schema_version": 1,
            "checkpoint": checkpoint,
            "task_count": len(analyses),
            "analyses": analyses,
        }
        output_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_json(output_dir / "overview.json", payload)
        (output_dir / "overview.md").write_text(
            "# Scripted Debugger overview\n\n"
            + "\n".join(
                f"- `{row['task_key']}`: {row['outcome_summary']}"
                for row in analyses
            )
            + "\n",
            encoding="utf-8",
        )
        return payload

    def run_evolve(
        self,
        *,
        round_index: int,
        workspace: Path,
        evidence_dir: Path,
        raw_evaluation_dir: Path,
        debugger_dir: Path,
        history_dir: Path,
        attribution_dir: Path | None,
        explore_skills_dir: Path,
        recovery_dir: Path | None,
        output_dir: Path,
    ) -> dict[str, Any]:
        del (
            evidence_dir,
            raw_evaluation_dir,
            debugger_dir,
            history_dir,
            attribution_dir,
            explore_skills_dir,
            recovery_dir,
        )
        prompt_path = workspace / "systemprompt.md"
        if not prompt_path.is_file():
            raise FileNotFoundError(prompt_path)
        rule = _RULES[(round_index - 1) % len(_RULES)]
        marker = f"<!-- scripted-ahe-round-{round_index} -->"
        text = prompt_path.read_text(encoding="utf-8").rstrip()
        if marker not in text:
            prompt_path.write_text(
                f"{text}\n\n{marker}\n- {rule}\n",
                encoding="utf-8",
            )
        manifest = {
            "schema_version": 2,
            "checkpoint": f"C{round_index}",
            "parent_checkpoint": f"C{round_index - 1}",
            "objective": f"Make reliability rule {round_index} explicit.",
            "evidence_task_keys": [],
            "components_changed": ["system_prompt"],
            "changes": [
                {
                    "id": "chg-1",
                    "type": "improvement",
                    "component": "system_prompt",
                    "path": "systemprompt.md",
                    "failure_pattern": "example-reliability-gap",
                    "mechanism": rule,
                    "why_this_component": (
                        "The deterministic example represents broad behavior."
                    ),
                }
            ],
            "predicted_effects": [],
            "regression_risks": [],
            "validation": ["runner generic validation"],
        }
        atomic_write_json(workspace / "change_manifest.json", manifest)
        git_output(workspace, "add", "--all")
        git_output(
            workspace,
            "commit",
            "--quiet",
            "-m",
            (
                f"Add scripted reliability rule {round_index}\n\n"
                "Co-authored-by: Copilot "
                "<223556219+Copilot@users.noreply.github.com>"
            ),
        )
        output_dir.mkdir(parents=True, exist_ok=True)
        response = {
            "round": round_index,
            "rule": rule,
            "workspace_head": git_output(workspace, "rev-parse", "HEAD").strip(),
        }
        atomic_write_json(output_dir / "result.json", response)
        (output_dir / "final_response.txt").write_text(
            json.dumps(response, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return response
