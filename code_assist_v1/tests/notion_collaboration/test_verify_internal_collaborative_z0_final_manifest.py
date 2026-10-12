from __future__ import annotations

import json
from pathlib import Path

import pytest

import code_assist_v1.tools.compile_internal_collaborative_z0_final_manifest as compiler
import code_assist_v1.tools.internal_collaborative_validation as validation
import code_assist_v1.tools.verify_internal_collaborative_z0_final_manifest as verifier
from code_assist_v1.tests.notion_collaboration.test_compile_internal_collaborative_z0_final_manifest import (
    _prepare_current_authority,
)
from code_assist_v1.tests.notion_collaboration.test_internal_collaborative_z0_completion import (
    z0_bundle,
)


_HASH_D = "sha256:" + "d" * 64
_HASH_E = "sha256:" + "e" * 64


def _write_json(path: Path, value: object) -> Path:
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def _compile_valid_manifest(
    z0_bundle: object,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    suite_paths, promotion_path = _prepare_current_authority(
        z0_bundle,
        monkeypatch,
    )
    output = z0_bundle.manifest_path.parent / "compiled-for-verification.json"
    result = compiler.compile_z0_final_manifest(
        suite_receipt_paths=suite_paths,
        promotion_receipt_path=promotion_path,
        output_path=output,
    )
    assert result["status"] == "pass"
    return output


@pytest.mark.unit
def test_read_only_verifier_reconstructs_compiled_manifest(
    z0_bundle: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest_path = _compile_valid_manifest(z0_bundle, monkeypatch)
    before = validation.sha256_file(manifest_path)

    report, exit_code = verifier.verify_z0_final_manifest(
        manifest_path,
        expected_manifest_sha256=before,
    )

    assert exit_code == 0, report
    assert report == {
        "report_schema_version": 1,
        "contract": verifier.VERIFY_CONTRACT,
        "status": "pass",
        "error_count": 0,
        "errors": [],
        "manifest_id": report["manifest_id"],
        "manifest_sha256": before,
        "suite_receipt_count": 7,
        "promotion_receipt_count": 1,
        "read_only": True,
        "receipt_created": False,
        "tests_executed_by_verifier": False,
        "operator_actions_executed": False,
        "full_rollout_enabled": False,
        "new_packet_or_gate_credit_awarded": False,
        "product_complete": False,
        "active_shared_goal_complete": False,
    }
    assert validation.sha256_file(manifest_path) == before
    assert not list(manifest_path.parent.glob(".z0-final-manifest-stage-*.json"))


@pytest.mark.unit
def test_read_only_verifier_rejects_tampered_supporting_receipt(
    z0_bundle: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest_path = _compile_valid_manifest(z0_bundle, monkeypatch)
    manifest = validation.load_json(manifest_path)
    suite_path = validation.REPOSITORY_ROOT / manifest["suite_receipts"][0]["path"]
    suite = validation.load_json(suite_path)
    suite["run_id"] = "tampered-after-manifest-compile"
    _write_json(suite_path, suite)

    report, exit_code = verifier.verify_z0_final_manifest(
        manifest_path,
        expected_manifest_sha256=validation.sha256_file(manifest_path),
    )

    assert exit_code == 1
    assert report["status"] == "fail"
    assert "Z0_EVIDENCE_HASH_MISMATCH" in {
        item["code"] for item in report["errors"]
    }
    assert report["receipt_created"] is False
    assert report["new_packet_or_gate_credit_awarded"] is False


@pytest.mark.unit
def test_read_only_verifier_rejects_noncurrent_manifest_authority(
    z0_bundle: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest_path = _compile_valid_manifest(z0_bundle, monkeypatch)
    manifest = validation.load_json(manifest_path)
    manifest["source_manifest_sha256"] = _HASH_E
    _write_json(manifest_path, manifest)

    report, exit_code = verifier.verify_z0_final_manifest(
        manifest_path,
        expected_manifest_sha256=validation.sha256_file(manifest_path),
    )

    assert exit_code == 1
    assert report["errors"][0]["code"] == "manifest_current_authority_mismatch"
    assert report["receipt_created"] is False


@pytest.mark.unit
def test_read_only_verifier_detects_sbe_change_during_verification(
    z0_bundle: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest_path = _compile_valid_manifest(z0_bundle, monkeypatch)
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
    report, exit_code = verifier.verify_z0_final_manifest(
        manifest_path,
        expected_manifest_sha256=validation.sha256_file(manifest_path),
    )

    assert calls == 2
    assert exit_code == 1
    assert report["errors"][0]["code"] == (
        "sbe_contract_audit_changed_during_verify"
    )
    assert report["receipt_created"] is False


@pytest.mark.unit
def test_read_only_verifier_rejects_path_outside_final_evidence_root(
    z0_bundle: object,
) -> None:
    report, exit_code = verifier.verify_z0_final_manifest(
        validation.REPOSITORY_ROOT / "outside.json",
        expected_manifest_sha256=_HASH_D,
    )
    assert exit_code == 1
    assert report["errors"][0]["code"] in {
        "MANIFEST_NOT_FOUND",
        "z0_final_manifest_path_invalid",
    }
    assert report["receipt_created"] is False


@pytest.mark.unit
def test_cli_argument_failure_is_fail_closed(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert verifier.main(["--compact"]) == 2
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "fail"
    assert report["read_only"] is True
    assert report["receipt_created"] is False
    assert report["operator_actions_executed"] is False
    assert report["new_packet_or_gate_credit_awarded"] is False
    assert report["product_complete"] is False


@pytest.mark.unit
def test_read_only_verifier_detects_suite_change_during_final_audit(
    z0_bundle: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest_path = _compile_valid_manifest(z0_bundle, monkeypatch)
    manifest = validation.load_json(manifest_path)
    suite_path = validation.REPOSITORY_ROOT / manifest["suite_receipts"][0]["path"]
    calls = 0

    def audit_and_mutate(*args: object) -> tuple[dict[str, object], int]:
        nonlocal calls
        calls += 1
        if calls == 2:
            suite = validation.load_json(suite_path)
            suite["run_id"] = "changed-during-read-only-final-audit"
            _write_json(suite_path, suite)
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
    report, exit_code = verifier.verify_z0_final_manifest(
        manifest_path,
        expected_manifest_sha256=validation.sha256_file(manifest_path),
    )

    assert calls == 2
    assert exit_code == 1
    assert report["errors"][0]["code"] == "evidence_changed_during_verify"
    assert report["receipt_created"] is False
    assert report["new_packet_or_gate_credit_awarded"] is False


@pytest.mark.unit
def test_read_only_verifier_rejects_unpinned_manifest_substitution(
    z0_bundle: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest_path = _compile_valid_manifest(z0_bundle, monkeypatch)

    report, exit_code = verifier.verify_z0_final_manifest(
        manifest_path,
        expected_manifest_sha256=_HASH_E,
    )

    assert exit_code == 1
    assert report["errors"][0]["code"] == "manifest_sha256_mismatch"
    assert report["manifest_sha256"] == validation.sha256_file(manifest_path)
    assert report["receipt_created"] is False


@pytest.mark.unit
def test_cli_verifies_the_exact_pinned_manifest(
    z0_bundle: object,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    manifest_path = _compile_valid_manifest(z0_bundle, monkeypatch)
    expected = validation.sha256_file(manifest_path)

    assert verifier.main(
        [
            "--manifest",
            str(manifest_path),
            "--expected-manifest-sha256",
            expected,
            "--compact",
        ]
    ) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "pass"
    assert report["manifest_sha256"] == expected
    assert report["receipt_created"] is False
    assert report["new_packet_or_gate_credit_awarded"] is False
