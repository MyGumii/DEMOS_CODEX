from __future__ import annotations

import json
from pathlib import Path

import pytest

import code_assist_v1.tools.compile_internal_collaborative_z0_final_manifest as compiler
import code_assist_v1.tools.internal_collaborative_validation as validation
from code_assist_v1.tests.notion_collaboration.test_internal_collaborative_z0_completion import (
    z0_bundle,
)


_HASH_D = "sha256:" + "d" * 64
_HASH_E = "sha256:" + "e" * 64


def _write_json(path: Path, value: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def _prepare_current_authority(
    z0_bundle: object,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[list[Path], Path]:
    goal_observation = {
        "thread_id": "goal-thread-z0-test",
        "persisted_tool_status": "active",
    }
    ownership = {"snapshot": "z0-compiler-test"}
    lineage_sha256 = validation.sha256_file(
        validation.KNOWLEDGE_DOCS_ROOT
        / "second_brain_evolution"
        / "FORWARD_SCHEMA.md"
    )
    agents_sha256 = validation.sha256_file(validation.PROJECT_ROOT / "AGENTS.md")
    checkpoint = {
        "current_schema_lineage_sha256": lineage_sha256,
        "agents_md_sha256": agents_sha256,
        "goal_observation": goal_observation,
        "cross_catalog_ownership_snapshot": ownership,
    }
    checkpoint_path = _write_json(
        validation.REPOSITORY_ROOT / "checkpoint.json",
        checkpoint,
    )
    pointer_path = _write_json(
        validation.REPOSITORY_ROOT / "pointer.json",
        {"pointer": "z0-compiler-test"},
    )
    monkeypatch.setattr(validation, "DEFAULT_CHECKPOINT", pointer_path)
    monkeypatch.setattr(
        validation,
        "load_current_checkpoint",
        lambda *args, **kwargs: (checkpoint, checkpoint_path, {"pointer": True}),
    )
    sbe_report = {
        "status": "pass",
        "error_count": 0,
        "errors": [],
        "input_sha256": _HASH_D,
    }
    monkeypatch.setattr(compiler, "audit_contracts", lambda *args: (sbe_report, 0))
    authority = {
        "source_manifest_sha256": validation.sha256_file(validation.DEFAULT_CATALOG),
        "validation_manifest_sha256": validation.sha256_file(validation.DEFAULT_LADDER),
        "schema_catalog_sha256": validation.sha256_file(validation.DEFAULT_MATRIX),
        "current_schema_lineage_sha256": lineage_sha256,
        "agents_md_sha256": agents_sha256,
        "goal_observation_sha256": validation.canonical_sha256(goal_observation),
        "goal_thread_id": goal_observation["thread_id"],
        "goal_status_observed": "active",
        "cross_catalog_ownership_snapshot_sha256": validation.canonical_sha256(
            ownership
        ),
        "sbe_contract_audit_input_sha256": _HASH_D,
    }
    suite_paths = []
    for lane_id in validation.Z0_REQUIRED_SUITE_LANES:
        path = z0_bundle.suite_paths[lane_id]
        receipt = validation.load_json(path)
        receipt.update(authority)
        _write_json(path, receipt)
        suite_paths.append(path)
    promotion_path = validation.REPOSITORY_ROOT / z0_bundle.manifest[
        "promotion_receipt"
    ]["path"]
    return suite_paths, promotion_path


@pytest.mark.unit
def test_compiler_emits_exact_create_only_manifest(
    z0_bundle: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    suite_paths, promotion_path = _prepare_current_authority(
        z0_bundle,
        monkeypatch,
    )
    output = z0_bundle.manifest_path.parent / "compiled-final.json"

    result = compiler.compile_z0_final_manifest(
        suite_receipt_paths=list(reversed(suite_paths)),
        promotion_receipt_path=promotion_path,
        output_path=output,
    )

    assert result == {
        "contract": compiler.RESULT_CONTRACT,
        "status": "pass",
        "manifest_id": result["manifest_id"],
        "manifest_sha256": validation.sha256_file(output),
        "suite_receipt_count": 7,
        "promotion_receipt_count": 1,
        "tests_executed_by_compiler": False,
        "operator_actions_executed": False,
        "full_rollout_enabled": False,
        "new_packet_or_gate_credit_awarded": False,
        "product_complete": False,
        "active_shared_goal_complete": False,
    }
    manifest = validation.load_json(output)
    assert set(manifest) == validation.Z0_FINAL_MANIFEST_FIELDS
    assert [item["lane_id"] for item in manifest["suite_receipts"]] == list(
        validation.Z0_REQUIRED_SUITE_LANES
    )
    assert manifest["claims"] == validation.Z0_FINAL_MANIFEST_CLAIMS
    assert not list(output.parent.glob(".z0-final-manifest-stage-*.json"))

    with pytest.raises(FileExistsError):
        compiler.compile_z0_final_manifest(
            suite_receipt_paths=suite_paths,
            promotion_receipt_path=promotion_path,
            output_path=output,
        )


@pytest.mark.unit
def test_compiler_rejects_reused_suite_receipt_without_output(
    z0_bundle: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    suite_paths, promotion_path = _prepare_current_authority(
        z0_bundle,
        monkeypatch,
    )
    output = z0_bundle.manifest_path.parent / "must-not-exist.json"

    with pytest.raises(
        compiler.Z0ManifestCompileError,
        match="suite_receipt_reused",
    ):
        compiler.compile_z0_final_manifest(
            suite_receipt_paths=[suite_paths[0]] * 7,
            promotion_receipt_path=promotion_path,
            output_path=output,
        )
    assert not output.exists()


@pytest.mark.unit
def test_compiler_rejects_noncurrent_suite_authority_without_output(
    z0_bundle: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    suite_paths, promotion_path = _prepare_current_authority(
        z0_bundle,
        monkeypatch,
    )
    receipt = validation.load_json(suite_paths[0])
    receipt["source_manifest_sha256"] = _HASH_D
    _write_json(suite_paths[0], receipt)
    output = z0_bundle.manifest_path.parent / "must-not-exist.json"

    with pytest.raises(
        compiler.Z0ManifestCompileError,
        match="suite_authority_binding_drift|suite_current_authority_mismatch",
    ):
        compiler.compile_z0_final_manifest(
            suite_receipt_paths=suite_paths,
            promotion_receipt_path=promotion_path,
            output_path=output,
        )
    assert not output.exists()


@pytest.mark.unit
def test_compiler_rejects_release_manifest_hash_drift_without_output(
    z0_bundle: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    suite_paths, promotion_path = _prepare_current_authority(
        z0_bundle,
        monkeypatch,
    )
    _write_json(z0_bundle.release_path, {"manifest_id": "changed-after-suite"})
    output = z0_bundle.manifest_path.parent / "must-not-exist.json"

    with pytest.raises(
        compiler.Z0ManifestCompileError,
        match="suite_release_manifest_hash_mismatch",
    ):
        compiler.compile_z0_final_manifest(
            suite_receipt_paths=suite_paths,
            promotion_receipt_path=promotion_path,
            output_path=output,
        )
    assert not output.exists()


@pytest.mark.unit
def test_compiler_rejects_non_green_sbe_audit_without_output(
    z0_bundle: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    suite_paths, promotion_path = _prepare_current_authority(
        z0_bundle,
        monkeypatch,
    )
    monkeypatch.setattr(
        compiler,
        "audit_contracts",
        lambda *args: (
            {
                "status": "fail",
                "error_count": 1,
                "errors": [{"code": "TEST_DRIFT"}],
                "input_sha256": _HASH_D,
            },
            1,
        ),
    )
    output = z0_bundle.manifest_path.parent / "must-not-exist.json"

    with pytest.raises(
        compiler.Z0ManifestCompileError,
        match="sbe_contract_audit_not_green",
    ):
        compiler.compile_z0_final_manifest(
            suite_receipt_paths=suite_paths,
            promotion_receipt_path=promotion_path,
            output_path=output,
        )
    assert not output.exists()


@pytest.mark.unit
def test_compiler_rejects_output_outside_final_evidence_root(
    z0_bundle: object,
) -> None:
    output = validation.REPOSITORY_ROOT / "outside-final-evidence.json"
    with pytest.raises(
        compiler.Z0ManifestCompileError,
        match="output_path_invalid",
    ):
        compiler.compile_z0_final_manifest(
            suite_receipt_paths=list(z0_bundle.suite_paths.values()),
            promotion_receipt_path=validation.REPOSITORY_ROOT
            / z0_bundle.manifest["promotion_receipt"]["path"],
            output_path=output,
        )
    assert not output.exists()


@pytest.mark.unit
def test_cli_failure_never_claims_action_rollout_or_credit(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert compiler.main(["--compact"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "fail"
    assert report["operator_actions_executed"] is False
    assert report["full_rollout_enabled"] is False
    assert report["new_packet_or_gate_credit_awarded"] is False
    assert report["product_complete"] is False


@pytest.mark.unit
def test_compiler_detects_receipt_change_before_publish_and_cleans_stage(
    z0_bundle: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    suite_paths, promotion_path = _prepare_current_authority(
        z0_bundle,
        monkeypatch,
    )
    output = z0_bundle.manifest_path.parent / "must-not-exist.json"
    victim = suite_paths[0]
    original_validate = validation._validate_z0_final_evidence

    def validate_then_mutate(*args: object, **kwargs: object) -> list[dict[str, str]]:
        errors = original_validate(*args, **kwargs)
        if not errors:
            receipt = validation.load_json(victim)
            receipt["run_id"] = "changed-after-final-reconstruction"
            _write_json(victim, receipt)
        return errors

    monkeypatch.setattr(
        validation,
        "_validate_z0_final_evidence",
        validate_then_mutate,
    )

    with pytest.raises(
        compiler.Z0ManifestCompileError,
        match="evidence_changed_before_publish",
    ):
        compiler.compile_z0_final_manifest(
            suite_receipt_paths=suite_paths,
            promotion_receipt_path=promotion_path,
            output_path=output,
        )
    assert not output.exists()
    assert not list(output.parent.glob(".z0-final-manifest-stage-*.json"))


@pytest.mark.unit
def test_compiler_detects_sbe_change_before_publish_and_cleans_stage(
    z0_bundle: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    suite_paths, promotion_path = _prepare_current_authority(
        z0_bundle,
        monkeypatch,
    )
    calls = 0

    def changing_audit(*args: object) -> tuple[dict[str, object], int]:
        nonlocal calls
        calls += 1
        return (
            {
                "status": "pass",
                "error_count": 0,
                "errors": [],
                "input_sha256": _HASH_D if calls == 1 else _HASH_E,
            },
            0,
        )

    monkeypatch.setattr(compiler, "audit_contracts", changing_audit)
    output = z0_bundle.manifest_path.parent / "must-not-exist.json"

    with pytest.raises(
        compiler.Z0ManifestCompileError,
        match="sbe_contract_audit_changed_before_publish",
    ):
        compiler.compile_z0_final_manifest(
            suite_receipt_paths=suite_paths,
            promotion_receipt_path=promotion_path,
            output_path=output,
        )
    assert calls == 2
    assert not output.exists()
    assert not list(output.parent.glob(".z0-final-manifest-stage-*.json"))


@pytest.mark.unit
def test_compiler_detects_receipt_change_during_final_audit(
    z0_bundle: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    suite_paths, promotion_path = _prepare_current_authority(
        z0_bundle,
        monkeypatch,
    )
    calls = 0
    victim = suite_paths[0]

    def audit_and_mutate(*args: object) -> tuple[dict[str, object], int]:
        nonlocal calls
        calls += 1
        if calls == 2:
            receipt = validation.load_json(victim)
            receipt["run_id"] = "changed-during-final-sbe-audit"
            _write_json(victim, receipt)
        return (
            {
                "status": "pass",
                "error_count": 0,
                "errors": [],
                "input_sha256": _HASH_D,
            },
            0,
        )

    monkeypatch.setattr(compiler, "audit_contracts", audit_and_mutate)
    output = z0_bundle.manifest_path.parent / "must-not-exist.json"

    with pytest.raises(
        compiler.Z0ManifestCompileError,
        match="evidence_changed_before_publish",
    ):
        compiler.compile_z0_final_manifest(
            suite_receipt_paths=suite_paths,
            promotion_receipt_path=promotion_path,
            output_path=output,
        )
    assert calls == 2
    assert not output.exists()
    assert not list(output.parent.glob(".z0-final-manifest-stage-*.json"))
