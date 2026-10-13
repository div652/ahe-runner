from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from ahe_runner.adapter import EvidenceBundle, SplitName, VerificationRequest
from ahe_runner.config import load_config
from ahe_runner.controller import CampaignController
from ahe_runner.errors import IntegrityError
from ahe_runner.evaluation import verify_evaluation_output
from ahe_runner.evidence import verify_evidence_bundle
from ahe_runner.io import atomic_write_json, read_json, sha256_file

from .conftest import make_example_project


def _bundle(evidence_dir: Path) -> EvidenceBundle:
    manifest = evidence_dir / "evidence_manifest.json"
    value = read_json(manifest)
    return EvidenceBundle(
        checkpoint=str(value["checkpoint"]),
        manifest_path=str(manifest),
        manifest_sha256=sha256_file(manifest),
        task_count=len(value["tasks"]),
        supervision_policy=str(value["supervision_policy"]),
    )


def test_evaluation_contract_rejects_foreign_identity_and_artifacts(
    tmp_path: Path,
) -> None:
    controller = CampaignController(
        load_config(make_example_project(tmp_path, rounds=0))
    )
    controller.prepare()
    result = controller._ensure_training_evaluation(0)
    request = controller._evaluation_request(0, SplitName.TRAIN)

    verify_evaluation_output(request, result)

    wrong_checkpoint = result.model_copy(update={"checkpoint": "WRONG-CHECKPOINT"})
    with pytest.raises(IntegrityError, match="checkpoint identity mismatch"):
        verify_evaluation_output(request, wrong_checkpoint)

    wrong_split = result.model_copy(update={"split": SplitName.TEST})
    with pytest.raises(IntegrityError, match="split identity mismatch"):
        verify_evaluation_output(request, wrong_split)

    wrong_request = result.model_copy(update={"request_sha256": "0" * 64})
    with pytest.raises(IntegrityError, match="request identity mismatch"):
        verify_evaluation_output(request, wrong_request)

    missing_artifact = result.model_copy(
        update={"artifact_hashes": {"missing.json": "0" * 64}}
    )
    with pytest.raises(IntegrityError, match="artifact coverage/hash mismatch"):
        verify_evaluation_output(request, missing_artifact)

    missing_fact = result.model_copy(update={"trial_facts": result.trial_facts[1:]})
    with pytest.raises(IntegrityError, match="trial facts do not cover every unit"):
        verify_evaluation_output(request, missing_fact)

    first_unit = next((request.output_dir / "units").iterdir())
    shutil.rmtree(first_unit)
    with pytest.raises(IntegrityError, match="unit coverage mismatch"):
        verify_evaluation_output(request, result)


def test_example_verifier_recomputes_instead_of_trusting_result_json(
    tmp_path: Path,
) -> None:
    controller = CampaignController(
        load_config(make_example_project(tmp_path, rounds=0))
    )
    controller.prepare()
    result = controller._ensure_training_evaluation(0)
    request = controller._evaluation_request(0, SplitName.TRAIN)
    result_path = request.output_dir / "adapter_result.json"
    corrupted = result.model_dump(mode="json")
    corrupted["checkpoint"] = "CORRUPTED"
    atomic_write_json(result_path, corrupted)

    independently_verified = controller.adapter.verify_evaluation(
        VerificationRequest(evaluation=request)
    )
    assert independently_verified == result

    atomic_write_json(result_path, result.model_dump(mode="json"))


def test_evidence_contract_rejects_false_lineage_stale_metrics_and_empty_trials(
    tmp_path: Path,
) -> None:
    controller = CampaignController(
        load_config(make_example_project(tmp_path, rounds=0))
    )
    controller.prepare()
    result = controller._ensure_training_evaluation(0)
    evidence_dir = controller._ensure_evidence(0)
    request = controller._evaluation_request(0, SplitName.TRAIN)
    evaluation_dir = request.output_dir

    verify_evidence_bundle(
        _bundle(evidence_dir),
        evidence_dir=evidence_dir,
        expected_checkpoint="C0",
        evaluation_dir=evaluation_dir,
        source_evaluation=result,
        source_request=request,
    )

    false_lineage = tmp_path / "false-lineage"
    shutil.copytree(evidence_dir, false_lineage)
    manifest = read_json(false_lineage / "evidence_manifest.json")
    manifest["source_evaluation_sha256"] = "0" * 64
    atomic_write_json(false_lineage / "evidence_manifest.json", manifest)
    with pytest.raises(IntegrityError, match="source evaluation hash mismatch"):
        verify_evidence_bundle(
            _bundle(false_lineage),
            evidence_dir=false_lineage,
            expected_checkpoint="C0",
            evaluation_dir=evaluation_dir,
            source_evaluation=result,
            source_request=request,
        )

    stale_metrics = tmp_path / "stale-metrics"
    shutil.copytree(evidence_dir, stale_metrics)
    manifest = read_json(stale_metrics / "evidence_manifest.json")
    manifest["metrics"]["primary"]["value"] = -1.0
    atomic_write_json(stale_metrics / "evidence_manifest.json", manifest)
    with pytest.raises(IntegrityError, match="metrics are stale"):
        verify_evidence_bundle(
            _bundle(stale_metrics),
            evidence_dir=stale_metrics,
            expected_checkpoint="C0",
            evaluation_dir=evaluation_dir,
            source_evaluation=result,
            source_request=request,
        )

    empty_trials = tmp_path / "empty-trials"
    shutil.copytree(evidence_dir, empty_trials)
    manifest = read_json(empty_trials / "evidence_manifest.json")
    first = manifest["tasks"][0]
    detail_path = empty_trials / first["detail_path"]
    detail = read_json(detail_path)
    detail["trials"] = []
    atomic_write_json(detail_path, detail)
    first["detail_sha256"] = sha256_file(detail_path)
    atomic_write_json(empty_trials / "evidence_manifest.json", manifest)
    with pytest.raises(IntegrityError, match="trials"):
        verify_evidence_bundle(
            _bundle(empty_trials),
            evidence_dir=empty_trials,
            expected_checkpoint="C0",
            evaluation_dir=evaluation_dir,
            source_evaluation=result,
            source_request=request,
        )


def test_evidence_contract_allows_sanitized_task_ids(tmp_path: Path) -> None:
    controller = CampaignController(
        load_config(make_example_project(tmp_path, rounds=0))
    )
    controller.prepare()
    result = controller._ensure_training_evaluation(0)
    evidence_dir = controller._ensure_evidence(0)
    request = controller._evaluation_request(0, SplitName.TRAIN)
    sanitized = tmp_path / "sanitized-evidence"
    shutil.copytree(evidence_dir, sanitized)
    manifest_path = sanitized / "evidence_manifest.json"
    manifest = read_json(manifest_path)
    manifest["true_task_ids_exposed"] = False
    for task in manifest["tasks"]:
        task["task_id"] = None
        detail_path = sanitized / task["detail_path"]
        detail = read_json(detail_path)
        detail["task_id"] = None
        atomic_write_json(detail_path, detail)
        task["detail_sha256"] = sha256_file(detail_path)
    atomic_write_json(manifest_path, manifest)

    verify_evidence_bundle(
        _bundle(sanitized),
        evidence_dir=sanitized,
        expected_checkpoint="C0",
        evaluation_dir=request.output_dir,
        source_evaluation=result,
        source_request=request,
    )


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("outcome", "contradictory-outcome"),
        ("score", -7.0),
        ("termination_reason", "contradictory-termination"),
        ("diagnostic_class", "other"),
    ],
)
def test_evidence_contract_rejects_contradictory_trial_semantics(
    tmp_path: Path,
    field: str,
    replacement: object,
) -> None:
    controller = CampaignController(
        load_config(make_example_project(tmp_path, rounds=0))
    )
    controller.prepare()
    result = controller._ensure_training_evaluation(0)
    evidence_dir = controller._ensure_evidence(0)
    request = controller._evaluation_request(0, SplitName.TRAIN)
    corrupted = tmp_path / f"semantic-{field}"
    shutil.copytree(evidence_dir, corrupted)
    manifest_path = corrupted / "evidence_manifest.json"
    manifest = read_json(manifest_path)
    task = manifest["tasks"][0]
    detail_path = corrupted / task["detail_path"]
    detail = read_json(detail_path)
    detail["trials"][0][field] = replacement
    atomic_write_json(detail_path, detail)
    task["detail_sha256"] = sha256_file(detail_path)
    atomic_write_json(manifest_path, manifest)

    with pytest.raises(IntegrityError, match="trial semantics mismatch"):
        verify_evidence_bundle(
            _bundle(corrupted),
            evidence_dir=corrupted,
            expected_checkpoint="C0",
            evaluation_dir=request.output_dir,
            source_evaluation=result,
            source_request=request,
        )


@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("valid", None),
        ("duplicate", "duplicate readable evidence artifact"),
        ("wrong_hash", "readable evidence hash mismatch"),
        ("missing", "readable evidence hash mismatch"),
        ("escape", "path escapes its root"),
    ],
)
def test_evidence_contract_checks_readable_artifacts(
    tmp_path: Path,
    case: str,
    message: str | None,
) -> None:
    controller = CampaignController(
        load_config(make_example_project(tmp_path, rounds=0))
    )
    controller.prepare()
    result = controller._ensure_training_evaluation(0)
    evidence_dir = controller._ensure_evidence(0)
    request = controller._evaluation_request(0, SplitName.TRAIN)
    copied = tmp_path / f"readable-{case}"
    shutil.copytree(evidence_dir, copied)
    view = copied / "readable" / "trace.md"
    view.parent.mkdir()
    view.write_text("line one\n", encoding="utf-8")
    artifact = {
        "kind": "readable_trace",
        "path": "readable/trace.md",
        "sha256": sha256_file(view),
    }
    views = {
        "valid": [artifact],
        "duplicate": [artifact, artifact],
        "wrong_hash": [{**artifact, "sha256": "0" * 64}],
        "missing": [{**artifact, "path": "readable/absent.md"}],
        "escape": [{**artifact, "path": "../outside.md"}],
    }[case]
    manifest_path = copied / "evidence_manifest.json"
    manifest = read_json(manifest_path)
    task = manifest["tasks"][0]
    detail_path = copied / task["detail_path"]
    detail = read_json(detail_path)
    detail["trials"][0]["readable_artifacts"] = views
    atomic_write_json(detail_path, detail)
    task["detail_sha256"] = sha256_file(detail_path)
    atomic_write_json(manifest_path, manifest)

    def verify() -> None:
        verify_evidence_bundle(
            _bundle(copied),
            evidence_dir=copied,
            expected_checkpoint="C0",
            evaluation_dir=request.output_dir,
            source_evaluation=result,
            source_request=request,
        )

    if message is None:
        verify()
        return
    with pytest.raises(IntegrityError, match=message):
        verify()
