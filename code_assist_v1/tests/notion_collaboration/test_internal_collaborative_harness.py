from __future__ import annotations

import copy
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

import code_assist_v1.tools.internal_collaborative_validation as collaborative_validation
from code_assist_v1.tools.internal_collaborative_validation import (
    DEFAULT_CATALOG,
    DEFAULT_CHECKPOINT,
    DEFAULT_LADDER,
    DEFAULT_MATRIX,
    DEFAULT_RECEIPT_SCHEMA,
    JsonSchemaValidator,
    load_checkpoint_chain,
    load_current_checkpoint,
    load_json,
    validate_execution_receipts,
    verify_bundle,
)
from code_assist_v1.tools.run_internal_collaborative_validation import _execute_lane


SBE_PASS = {
    "status": "pass",
    "error_count": 0,
    "input_sha256": "4fbbf741aece1a5ee3ddd6e24479b3662dd76d04ed10566e642e300360ceedc7",
    "summary": {
        "goal": {"count": 20},
        "scenarios": {"count": 284},
    },
}


def _write_json(tmp_path: Path, name: str, value: object) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _codes(report: dict[str, object]) -> set[str]:
    return {item["code"] for item in report["errors"]}  # type: ignore[index]


@pytest.mark.unit
def test_checked_in_bundle_audits_clean_while_strict_completion_fails() -> None:
    audit, audit_exit = verify_bundle(mode="audit", sbe_report=SBE_PASS)
    assert audit_exit == 0, audit
    assert audit["status"] == "pass"
    assert audit["completion_state"] == "incomplete"
    assert audit["summary"] == {
        "required_packets": 50,
        "gate_passed_required_packets": 44,
        "matrix_rows": 28,
        "closed_matrix_rows": 21,
        "phase_gates": 20,
        "closed_phase_gates": 15,
        "open_decisions": 0,
        "planned_required_lanes": 8,
        "required_approvals": 6,
        "satisfied_approvals": 2,
        "required_external_gates": 3,
        "satisfied_external_gates": 1,
        "valid_execution_receipts": 21,
        "product_complete": False,
    }

    gate, gate_exit = verify_bundle(mode="gate", sbe_report=SBE_PASS)
    assert gate_exit == 1
    assert gate["completion_state"] == "incomplete"
    assert _codes(gate) == {"STRICT_COMPLETION_INCOMPLETE"}


@pytest.mark.unit
def test_duplicate_json_keys_and_nonfinite_numbers_fail_closed(tmp_path: Path) -> None:
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text('{"schema_version":2,"schema_version":2}', encoding="utf-8")
    report, exit_code = verify_bundle(catalog_path=duplicate, sbe_report=SBE_PASS)
    assert exit_code == 1
    assert "JSON_READ_FAILED" in _codes(report)

    nonfinite = tmp_path / "nonfinite.json"
    nonfinite.write_text('{"schema_version":NaN}', encoding="utf-8")
    report, exit_code = verify_bundle(catalog_path=nonfinite, sbe_report=SBE_PASS)
    assert exit_code == 1
    assert "JSON_READ_FAILED" in _codes(report)


@pytest.mark.unit
@pytest.mark.parametrize("contract,packet_id", list(collaborative_validation.SPECIALIZED_RECEIPT_ENVELOPES.items()))
def test_execution_receipt_discovery_ignores_specialized_documents_but_keeps_malformed_generic_receipts(
    tmp_path: Path,
    contract: str,
    packet_id: str | None,
) -> None:
    shared_receipt_id = "SHARED-ID-MUST-NOT-COLLIDE"
    _write_json(
        tmp_path,
        "packet-specific-receipt.json",
        {
            "schema_version": 1,
            "contract": contract,
            "packet_id": packet_id,
            "receipt_id": shared_receipt_id,
            "captures": [],
        },
    )
    _write_json(
        tmp_path,
        "malformed-generic-receipt.json",
        {
            "schema_version": 1,
            "receipt_id": shared_receipt_id,
            "rung_id": "L6-VISUAL-ACCESSIBILITY",
        },
    )

    discovery_errors: list[dict[str, str]] = []
    receipts = collaborative_validation.load_execution_receipts(tmp_path, discovery_errors)

    assert discovery_errors == []
    assert len(receipts) == 1
    assert Path(receipts[0]["__path__"]).name == "malformed-generic-receipt.json"
    _, validation_errors = validate_execution_receipts(
        receipts,
        load_json(DEFAULT_CATALOG),
        load_json(DEFAULT_LADDER),
        load_json(DEFAULT_MATRIX),
    )
    assert "RECEIPT_REQUIRED_FIELD_MISSING" in {item["code"] for item in validation_errors}


@pytest.mark.unit
@pytest.mark.parametrize("value", [{}, {"receipt_id": "x"}, {"contract": "unknown.v1"}, [], None, 1, "receipt"])
def test_execution_receipt_discovery_rejects_unknown_and_nonobject_documents(tmp_path: Path, value: object) -> None:
    _write_json(tmp_path, "unknown.json", value)
    errors: list[dict[str, str]] = []
    assert collaborative_validation.load_execution_receipts(tmp_path, errors) == []
    assert len(errors) == 1
    assert errors[0]["code"] in {"RECEIPT_DOCUMENT_SHAPE", "RECEIPT_DOCUMENT_KIND_UNKNOWN"}


@pytest.mark.unit
@pytest.mark.parametrize("schema_version,packet_id", [(2, "V0B-P1"), (True, "V0B-P1"), (1, "V0R-P1")])
def test_execution_receipt_discovery_rejects_invalid_specialized_envelopes(
    tmp_path: Path, schema_version: object, packet_id: str,
) -> None:
    _write_json(tmp_path, "invalid.json", {
        "contract": collaborative_validation.V0B_FINAL_RECEIPT_CONTRACT,
        "schema_version": schema_version, "packet_id": packet_id,
    })
    errors: list[dict[str, str]] = []
    assert collaborative_validation.load_execution_receipts(tmp_path, errors) == []
    assert [item["code"] for item in errors] == ["SPECIALIZED_RECEIPT_ENVELOPE_INVALID"]


@pytest.mark.unit
def test_execution_receipt_discovery_generic_discriminators_win_and_duplicates_fail(tmp_path: Path) -> None:
    value = {
        "contract": collaborative_validation.V0B_FINAL_RECEIPT_CONTRACT,
        "schema_version": 1, "packet_id": "V0B-P1", "packet_ids": None, "receipt_id": "duplicate",
    }
    _write_json(tmp_path, "first.json", value)
    _write_json(tmp_path, "second.json", value)
    errors: list[dict[str, str]] = []
    receipts = collaborative_validation.load_execution_receipts(tmp_path, errors)
    assert len(receipts) == 2
    assert [item["code"] for item in errors] == ["DUPLICATE_RECEIPT_ID"]
    valid, validation_errors = validate_execution_receipts(
        receipts, load_json(DEFAULT_CATALOG), load_json(DEFAULT_LADDER), load_json(DEFAULT_MATRIX),
    )
    assert valid == []
    assert validation_errors


@pytest.mark.unit
def test_evidence_revocation_is_hash_bound_and_preserves_the_target(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    checkpoint_root = tmp_path / "checkpoints"
    receipt_root = tmp_path / "receipts"
    checkpoint_root.mkdir()
    receipt_root.mkdir()
    monkeypatch.setattr(collaborative_validation, "CHECKPOINT_ROOT", checkpoint_root)
    monkeypatch.setattr(
        collaborative_validation,
        "repository_path",
        lambda value: (
            checkpoint_root / Path(value).name
            if Path(value).name == "remediation.json"
            else receipt_root / Path(value).name
        ),
    )
    checkpoint = _write_json(
        checkpoint_root,
        "remediation.json",
        {
            "status": "verified_fail_closed_remediation",
            "packet_id": "V0B-P1",
            "result": {
                "historical_r1620_accepted_as_current_visual_evidence": False,
                "V0B_P1_gate_passed": False,
            },
        },
    )
    target = _write_json(
        receipt_root,
        "target.json",
        {
            "receipt_id": "revoked-v0",
            "packet_ids": ["V0R-P1", "V0B-P1"],
        },
    )
    revocation = {
        "schema_version": 1,
        "contract": collaborative_validation.EVIDENCE_REVOCATION_CONTRACT,
        "packet_id": "V0B-P1",
        "receipt_id": "revocation",
        "recorded_at": "2026-09-07T00:00:00+09:00",
        "remediation_checkpoint": {
            "path": checkpoint.name,
            "sha256": collaborative_validation.sha256_file(checkpoint),
        },
        "revoked_execution_receipts": [
            {
                "path": target.name,
                "sha256": collaborative_validation.sha256_file(target),
                "receipt_id": "revoked-v0",
                "reason_codes": sorted(collaborative_validation.EVIDENCE_REVOCATION_REASON_CODES),
            }
        ],
        "claims": {
            "historical_receipt_preserved": True,
            "historical_specialized_evidence_preserved": True,
            "execution_credit_withdrawn": True,
            "semantic_fields_remain_reviewable": True,
            "exact_pixel_claim_accepted": False,
            "V0R_P1_gate_passed_by_this_receipt": False,
            "V0B_P1_gate_passed_by_this_receipt": False,
            "product_complete": False,
            "active_shared_goal_complete": False,
        },
    }
    revocation_path = _write_json(receipt_root, "revocation.json", revocation)

    errors: list[dict[str, str]] = []
    assert collaborative_validation.load_execution_receipts(receipt_root, errors) == []
    assert errors == []
    assert target.is_file()

    revocation["revoked_execution_receipts"][0]["sha256"] = "sha256:" + ("0" * 64)
    revocation_path.write_text(json.dumps(revocation), encoding="utf-8")
    errors = []
    receipts = collaborative_validation.load_execution_receipts(receipt_root, errors)
    assert [Path(item["__path__"]).name for item in receipts] == ["target.json"]
    assert [item["code"] for item in errors] == ["EVIDENCE_REVOCATION_TARGET"]
    assert target.is_file()


@pytest.mark.unit
def test_v0r_only_generic_receipt_requires_fresh_exact_reference_verification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reference_path = collaborative_validation.V0R_REFERENCE_RECEIPT_PATH
    reference = load_json(reference_path)
    reference_hash = collaborative_validation.sha256_file(reference_path)
    relative = reference_path.resolve().relative_to(collaborative_validation.REPOSITORY_ROOT.resolve()).as_posix()
    receipt = {
        "packet_ids": ["V0R-P1"],
        "release_candidate_id": reference_hash,
        "artifact_hashes": {relative: reference_hash},
        "v0r_reference_receipt": {
            "path": relative,
            "sha256": reference_hash,
            "receipt_id": reference["receipt_id"],
        },
    }
    passing_report = {
        "status": "pass",
        "error_count": 0,
        "errors": [],
        "gate_passed": True,
        "exact_reference_gate_passed": True,
        "capture_count": 36,
        "capture_state_count": 18,
        "viewport_count": 2,
        "cleanup_complete": True,
        "receipt_sha256": reference_hash,
    }
    monkeypatch.setattr(
        collaborative_validation,
        "verify_selected_v0r_reference_receipt",
        lambda path: passing_report,
    )
    assert collaborative_validation._validate_v0_final_evidence(receipt, "receipt") == []

    receipt["release_candidate_id"] = "sha256:" + ("0" * 64)
    assert collaborative_validation._validate_v0_final_evidence(receipt, "receipt")[0]["code"] == "V0R_REFERENCE_EVIDENCE_HASH_MISMATCH"


def _current_generic_receipt() -> dict:
    receipt = load_json(
        collaborative_validation.DEFAULT_RECEIPTS_ROOT / "t0b-p3-l4-cross-process-browser-20260824.json"
    )
    receipt.update({
        "source_manifest_sha256": collaborative_validation.sha256_file(DEFAULT_CATALOG),
        "validation_manifest_sha256": collaborative_validation.sha256_file(DEFAULT_LADDER),
        "schema_catalog_sha256": collaborative_validation.sha256_file(DEFAULT_MATRIX),
        "current_schema_lineage_sha256": collaborative_validation.sha256_file(
            collaborative_validation.KNOWLEDGE_DOCS_ROOT / "second_brain_evolution" / "FORWARD_SCHEMA.md"
        ),
        "agents_md_sha256": collaborative_validation.sha256_file(collaborative_validation.PROJECT_ROOT / "AGENTS.md"),
    })
    return receipt


@pytest.mark.unit
@pytest.mark.parametrize("field,value", [
    ("packet_ids", None), ("packet_ids", [{}]), ("matrix_row_ids", None),
    ("lane_id", {}), ("artifact_hashes", None),
])
def test_execution_receipt_schema_errors_stop_unsafe_semantic_validation(field: str, value: object) -> None:
    receipt = _current_generic_receipt()
    receipt[field] = value
    valid, errors = validate_execution_receipts(
        [receipt], load_json(DEFAULT_CATALOG), load_json(DEFAULT_LADDER), load_json(DEFAULT_MATRIX),
    )
    assert valid == []
    assert errors
    assert all(item["code"].startswith("RECEIPT_") for item in errors)


@pytest.fixture
def selected_v0_evidence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    receipt = _current_generic_receipt()
    root = tmp_path / "receipts"
    root.mkdir()
    relative = "receipts/final.json"
    release = "sha256:" + "1" * 64
    selected = {
        "contract": collaborative_validation.V0B_FINAL_RECEIPT_CONTRACT,
        "schema_version": 1, "packet_id": "V0B-P1", "receipt_id": "selected-final",
        "release_candidate": {"source_build_sha256": release},
    }
    path = _write_json(root, "final.json", selected)
    digest = collaborative_validation.sha256_file(path)
    receipt.update({
        "rung_id": "L6-VISUAL-ACCESSIBILITY", "lane_id": "L6-EXACT-RGBA",
        "packet_ids": ["V0R-P1", "V0B-P1"], "matrix_row_ids": ["PIX-001"],
        "release_candidate_id": release,
        "v0b_final_receipt": {"path": relative, "sha256": digest, "receipt_id": "selected-final"},
        "artifact_hashes": {relative: digest},
    })
    report = {
        "contract": "code_assist.notion.internal_collaborative_v0b_p1.fresh_gate_audit.v1",
        "status": "pass", "error_count": 0, "errors": [], "gate_passed": True,
        "core_test_count": 21, "performance_run_count": 2,
        "visual_similarity_pass_count": 36,
        "exact_pixel_pass_count": 0, "accessibility_pass_count": 36,
        "receipt_id": "selected-final", "receipt_sha256": digest,
    }
    calls: list[Path] = []

    def verify_selected(selected_path: Path) -> dict:
        calls.append(selected_path)
        return report

    monkeypatch.setattr(collaborative_validation, "REPOSITORY_ROOT", tmp_path)
    monkeypatch.setattr(collaborative_validation, "DEFAULT_RECEIPTS_ROOT", root)
    monkeypatch.setattr(collaborative_validation, "verify_selected_v0b_final_receipt", verify_selected)
    return SimpleNamespace(receipt=receipt, path=path, selected=selected, report=report, calls=calls)


def _validate_v0_wrapper(receipt: dict) -> tuple[list, list]:
    return validate_execution_receipts(
        [receipt], load_json(DEFAULT_CATALOG), load_json(DEFAULT_LADDER), load_json(DEFAULT_MATRIX),
    )


@pytest.mark.unit
def test_v0_final_wrapper_requires_dedicated_verification(selected_v0_evidence: SimpleNamespace) -> None:
    evidence = selected_v0_evidence
    valid, errors = _validate_v0_wrapper(evidence.receipt)
    assert errors == []
    assert valid == [evidence.receipt]
    assert evidence.calls == [evidence.path]


@pytest.mark.unit
@pytest.mark.parametrize("field,value", [
    ("status", "fail"), ("error_count", 1), ("errors", [{"code": "tamper"}]),
    ("gate_passed", False), ("core_test_count", 20), ("performance_run_count", 1),
    ("visual_similarity_pass_count", 35), ("exact_pixel_pass_count", -1),
    ("accessibility_pass_count", 35),
    ("receipt_id", "another"), ("receipt_sha256", "sha256:" + "0" * 64),
])
def test_v0_final_wrapper_rejects_failed_or_mismatched_verifier(
    selected_v0_evidence: SimpleNamespace, field: str, value: object,
) -> None:
    selected_v0_evidence.report[field] = value
    valid, errors = _validate_v0_wrapper(selected_v0_evidence.receipt)
    assert valid == []
    assert [item["code"] for item in errors] == ["V0_FINAL_EVIDENCE_VERIFICATION_FAILED"]


@pytest.mark.unit
@pytest.mark.parametrize("exact_pixel_pass_count", [0, 1, 35, 36])
def test_v0_final_wrapper_keeps_exact_pixel_count_diagnostic(
    selected_v0_evidence: SimpleNamespace, exact_pixel_pass_count: int,
) -> None:
    selected_v0_evidence.report["exact_pixel_pass_count"] = exact_pixel_pass_count
    valid, errors = _validate_v0_wrapper(selected_v0_evidence.receipt)
    assert errors == []
    assert valid == [selected_v0_evidence.receipt]


@pytest.mark.unit
@pytest.mark.parametrize("mutation,code", [
    ("missing_declaration", "V0_FINAL_EVIDENCE_REQUIRED"),
    ("missing_file", "V0_FINAL_EVIDENCE_PATH_INVALID"),
    ("outside_root", "V0_FINAL_EVIDENCE_PATH_INVALID"),
    ("tampered_file", "V0_FINAL_EVIDENCE_HASH_MISMATCH"),
    ("missing_artifact_hash", "V0_FINAL_EVIDENCE_HASH_MISMATCH"),
    ("wrong_identity", "V0_FINAL_EVIDENCE_IDENTITY_MISMATCH"),
    ("wrong_release", "V0_FINAL_EVIDENCE_RELEASE_MISMATCH"),
])
def test_v0_final_wrapper_rejects_unbound_evidence_before_verification(
    selected_v0_evidence: SimpleNamespace, mutation: str, code: str,
) -> None:
    evidence = selected_v0_evidence
    if mutation == "missing_declaration":
        evidence.receipt.pop("v0b_final_receipt")
    elif mutation == "missing_file":
        evidence.receipt["v0b_final_receipt"]["path"] = "receipts/missing.json"
    elif mutation == "outside_root":
        evidence.receipt["v0b_final_receipt"]["path"] = "../outside.json"
    elif mutation == "tampered_file":
        evidence.path.write_text("{}", encoding="utf-8")
    elif mutation == "missing_artifact_hash":
        evidence.receipt["artifact_hashes"] = {}
    elif mutation == "wrong_identity":
        evidence.receipt["v0b_final_receipt"]["receipt_id"] = "another"
    elif mutation == "wrong_release":
        evidence.receipt["release_candidate_id"] = "sha256:" + "2" * 64
    valid, errors = _validate_v0_wrapper(evidence.receipt)
    assert valid == []
    assert [item["code"] for item in errors] == [code]
    assert evidence.calls == []


@pytest.mark.unit
def test_v0_checkpoint_alone_cannot_claim_completion() -> None:
    catalog = load_json(DEFAULT_CATALOG)
    for packet in catalog["packets"]:
        if packet["id"] in {"V0R-P1", "V0B-P1"}:
            packet["status"] = "gate_passed"
    checkpoints = [
        {"packet_id": packet, "packet_status": "gate_passed", "validation_receipt": {"status": "pass"}}
        for packet in ("V0R-P1", "V0B-P1")
    ]
    _, errors = collaborative_validation.completion_state(
        catalog, load_json(DEFAULT_LADDER), load_json(DEFAULT_MATRIX), checkpoints, {}, [],
    )
    assert {item["subject"] for item in errors if item["code"] == "PACKET_FALSE_GREEN"} >= {
        "packet:V0R-P1", "packet:V0B-P1",
    }


@pytest.mark.unit
@pytest.mark.parametrize(
    "packet_id",
    ["O1B-P1", "Q0-P1", "R0A-P1", "R0B-P1", "R1-P1", "Z0-P1"],
)
def test_dedicated_evidence_checkpoint_alone_cannot_claim_completion(
    packet_id: str,
) -> None:
    catalog = load_json(DEFAULT_CATALOG)
    packet = next(item for item in catalog["packets"] if item["id"] == packet_id)
    packet["status"] = "gate_passed"
    checkpoints = [
        {
            "packet_id": packet_id,
            "packet_status": "gate_passed",
            "validation_receipt": {"status": "pass"},
        }
    ]

    _, errors = collaborative_validation.completion_state(
        catalog,
        load_json(DEFAULT_LADDER),
        load_json(DEFAULT_MATRIX),
        checkpoints,
        {},
        [],
    )

    assert f"packet:{packet_id}" in {
        item["subject"]
        for item in errors
        if item["code"] == "PACKET_FALSE_GREEN"
    }


@pytest.mark.unit
def test_v0_passing_evidence_cannot_override_sbe_audit_failure(
    selected_v0_evidence: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence = selected_v0_evidence
    # Isolate this dependency assertion from unrelated historical hash audits.
    monkeypatch.setattr(collaborative_validation, "validate_catalog", lambda *args: [])
    monkeypatch.setattr(collaborative_validation, "validate_ladder", lambda *args: [])
    monkeypatch.setattr(collaborative_validation, "validate_checkpoint", lambda *args, **kwargs: [])
    monkeypatch.setattr(collaborative_validation, "load_execution_receipts", lambda *args: [evidence.receipt])
    checkpoint = load_json(collaborative_validation.DEFAULT_U0A_CHECKPOINT)
    monkeypatch.setattr(collaborative_validation, "load_current_checkpoint", lambda *args: (checkpoint, collaborative_validation.DEFAULT_U0A_CHECKPOINT, None))
    monkeypatch.setattr(collaborative_validation, "load_checkpoint_chain", lambda *args, **kwargs: ())
    sbe = copy.deepcopy(SBE_PASS)
    sbe.update({"status": "fail", "error_count": 1})
    report, exit_code = verify_bundle(mode="audit", sbe_report=sbe)
    assert evidence.calls == [evidence.path]
    assert report["summary"]["valid_execution_receipts"] == 1
    assert exit_code == 1
    assert "SBE_CONTRACT_AUDIT_FAILED" in _codes(report)


@pytest.mark.unit
def test_duplicate_packet_and_prerequisite_cycle_are_rejected(tmp_path: Path) -> None:
    catalog = copy.deepcopy(load_json(DEFAULT_CATALOG))
    catalog["packets"][1]["id"] = catalog["packets"][0]["id"]
    report, _ = verify_bundle(catalog_path=_write_json(tmp_path, "duplicate-packet.json", catalog), sbe_report=SBE_PASS)
    assert "DUPLICATE_ID" in _codes(report)

    catalog = copy.deepcopy(load_json(DEFAULT_CATALOG))
    catalog["packets"][0]["prerequisites"] = ["U0A-P2"]
    report, _ = verify_bundle(catalog_path=_write_json(tmp_path, "cycle.json", catalog), sbe_report=SBE_PASS)
    assert "PREREQUISITE_CYCLE" in _codes(report)


@pytest.mark.unit
def test_authority_hash_and_sbe_count_drift_are_rejected(tmp_path: Path) -> None:
    ladder = copy.deepcopy(load_json(DEFAULT_LADDER))
    ladder["authority_inputs"]["packet_catalog"]["sha256"] = "0" * 64
    report, _ = verify_bundle(ladder_path=_write_json(tmp_path, "hash-drift.json", ladder), sbe_report=SBE_PASS)
    assert "AUTHORITY_INPUT_HASH_MISMATCH" in _codes(report)

    catalog = copy.deepcopy(load_json(DEFAULT_CATALOG))
    catalog["sbe_alignment_contract"]["current_goal_count"] = 14
    report, _ = verify_bundle(catalog_path=_write_json(tmp_path, "stale-sbe.json", catalog), sbe_report=SBE_PASS)
    assert "SBE_GOAL_COUNT_STALE" in _codes(report)


@pytest.mark.unit
def test_g0_receipt_hash_aggregate_and_matrix_decisions_fail_closed(tmp_path: Path) -> None:
    catalog = copy.deepcopy(load_json(DEFAULT_CATALOG))
    catalog["decision_freeze"]["subgates"][0]["receipt"]["sha256"] = "sha256:" + ("0" * 64)
    report, _ = verify_bundle(catalog_path=_write_json(tmp_path, "g0-receipt-drift.json", catalog), sbe_report=SBE_PASS)
    assert "G0_RECEIPT_HASH" in _codes(report)

    catalog = copy.deepcopy(load_json(DEFAULT_CATALOG))
    catalog["decision_freeze"]["status"] = "open"
    report, _ = verify_bundle(catalog_path=_write_json(tmp_path, "g0-open.json", catalog), sbe_report=SBE_PASS)
    assert "G0_AGGREGATE_NOT_FROZEN" in _codes(report)

    matrix = copy.deepcopy(load_json(DEFAULT_MATRIX))
    matrix["open_decisions"] = ["offline_editing_policy"]
    report, _ = verify_bundle(matrix_path=_write_json(tmp_path, "g0-open-matrix.json", matrix), sbe_report=SBE_PASS)
    assert "G0_MATRIX_DECISIONS_OPEN" in _codes(report)


@pytest.mark.unit
def test_false_completion_claim_cannot_turn_audit_green(tmp_path: Path) -> None:
    catalog = copy.deepcopy(load_json(DEFAULT_CATALOG))
    catalog["claims"]["product_complete"] = True
    report, exit_code = verify_bundle(catalog_path=_write_json(tmp_path, "false-green.json", catalog), sbe_report=SBE_PASS)
    assert exit_code == 1
    assert "PRODUCT_FALSE_GREEN" in _codes(report)


@pytest.mark.unit
def test_status_integrator_owner_path_and_post_hash_tamper_fail(tmp_path: Path) -> None:
    checkpoint = copy.deepcopy(load_current_checkpoint(DEFAULT_CHECKPOINT)[0])
    checkpoint["owner_agent"] = "/not-owner"
    checkpoint["status_integrator_assignment"]["exact_hot_contract_paths"].append("code_assist_v1/AGENTS.md")
    first_path = checkpoint["status_integrator_assignment"]["exact_hot_contract_paths"][0]
    checkpoint["post_edit_sha256"][first_path] = "sha256:" + ("0" * 64)
    report, exit_code = verify_bundle(checkpoint_path=_write_json(tmp_path, "tampered-checkpoint.json", checkpoint), sbe_report=SBE_PASS)
    assert exit_code == 1
    assert {
        "CHECKPOINT_OWNER_INVALID",
        "STATUS_INTEGRATOR_OWNER_MISMATCH",
        "STATUS_INTEGRATOR_PATHS_MISMATCH",
        "POST_EDIT_HASH_MISMATCH",
    }.issubset(_codes(report))


@pytest.mark.unit
def test_current_checkpoint_pointer_and_predecessor_chain_tamper_fail(tmp_path: Path) -> None:
    pointer = copy.deepcopy(load_json(DEFAULT_CHECKPOINT))
    pointer["checkpoint_sha256"] = "sha256:" + ("0" * 64)
    report, exit_code = verify_bundle(
        checkpoint_path=_write_json(tmp_path, "pointer.json", pointer),
        sbe_report=SBE_PASS,
    )
    assert exit_code == 1
    assert "CHECKPOINT_READ_FAILED" in _codes(report)

    checkpoint = copy.deepcopy(load_current_checkpoint(DEFAULT_CHECKPOINT)[0])
    checkpoint["predecessor_checkpoint"]["sha256"] = "sha256:" + ("0" * 64)
    report, exit_code = verify_bundle(
        checkpoint_path=_write_json(tmp_path, "chain.json", checkpoint),
        sbe_report=SBE_PASS,
    )
    assert exit_code == 1
    assert "CHECKPOINT_CHAIN_RECEIPT_DRIFT" in _codes(report)


@pytest.mark.unit
def test_recursive_checkpoint_chain_rejects_cycle(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    first_path = tmp_path / "first.json"
    second_path = tmp_path / "second.json"
    second = {
        "packet_id": "D0A-P1",
        "packet_status": "gate_passed",
        "validation_receipt": {"status": "pass", "receipt_id": "second-receipt"},
        "predecessor_checkpoint": {
            "path": "first.json",
            "sha256": "sha256:" + ("0" * 64),
            "packet_id": "D0A-P2",
            "receipt_id": "first-receipt",
        },
        "post_edit_sha256": {},
        "pre_edit_sha256": {},
        "status_integrator_assignment": {"accepted_packet_receipt_ids": ["first-receipt"]},
    }
    _write_json(tmp_path, second_path.name, second)
    first = {
        "packet_id": "D0A-P2",
        "packet_status": "gate_passed",
        "validation_receipt": {"status": "pass", "receipt_id": "first-receipt"},
        "predecessor_checkpoint": {
            "path": "second.json",
            "sha256": collaborative_validation.sha256_file(second_path),
            "packet_id": "D0A-P1",
            "receipt_id": "second-receipt",
        },
        "post_edit_sha256": {},
        "pre_edit_sha256": {},
        "status_integrator_assignment": {"accepted_packet_receipt_ids": ["second-receipt"]},
    }
    _write_json(tmp_path, first_path.name, first)
    monkeypatch.setattr(collaborative_validation, "CHECKPOINT_ROOT", tmp_path)
    monkeypatch.setattr(collaborative_validation, "repository_path", lambda value: tmp_path / value)
    errors: list[dict[str, str]] = []
    load_checkpoint_chain(
        first,
        current_path=first_path,
        u0a_checkpoint=load_json(collaborative_validation.DEFAULT_U0A_CHECKPOINT),
        u0a_checkpoint_path=collaborative_validation.DEFAULT_U0A_CHECKPOINT,
        errors=errors,
    )
    assert "CHECKPOINT_CHAIN_CYCLE" in {item["code"] for item in errors}


@pytest.mark.unit
def test_expired_active_lease_is_rejected(tmp_path: Path) -> None:
    catalog = copy.deepcopy(load_json(DEFAULT_CATALOG))
    checkpoint = copy.deepcopy(load_current_checkpoint(DEFAULT_CHECKPOINT)[0])
    next(item for item in catalog["packets"] if item["id"] == checkpoint["packet_id"])["status"] = "in_progress"
    catalog_path = _write_json(tmp_path, "active-catalog.json", catalog)
    checkpoint["packet_status"] = "in_progress"
    checkpoint["checkpoint_kind"] = "active_status_integration"
    checkpoint["ownership_lease"]["status"] = "active"
    checkpoint["ownership_lease"]["expires_at"] = "2000-01-01T00:00:00Z"
    checkpoint_path = _write_json(tmp_path, "expired-checkpoint.json", checkpoint)
    report, exit_code = verify_bundle(catalog_path=catalog_path, checkpoint_path=checkpoint_path, sbe_report=SBE_PASS)
    assert exit_code == 1
    assert "OWNERSHIP_LEASE_EXPIRED" in _codes(report)


@pytest.mark.unit
def test_runner_uses_argv_without_shell_and_receipt_matches_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    observed: list[dict[str, object]] = []

    def fake_run(argv, **kwargs):
        observed.append({"argv": argv, "shell": kwargs.get("shell")})
        return SimpleNamespace(returncode=0, stdout="ok\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    rung = {"id": "L0-CONTRACT-BOOT", "packets": ["U0A-P2"]}
    lane = {
        "id": "L0-TEST-NO-SHELL",
        "state": "existing_baseline",
        "runner": "process",
        "working_directory": ".",
        "argv": [sys.executable, "-c", "print('ok')"],
        "evidence_types": ["contract"],
    }
    args = SimpleNamespace(
        release_candidate_id="",
        realm_id="synthetic_isolated",
        realm_config_fingerprint="",
    )
    report, exit_code = _execute_lane("audit", rung, lane, args)
    assert exit_code == 0, report
    lane_calls = [item for item in observed if item["argv"] == lane["argv"]]
    assert lane_calls == [{"argv": lane["argv"], "shell": False}]
    schema_errors = JsonSchemaValidator(load_json(DEFAULT_RECEIPT_SCHEMA)).validate(report["receipt"])
    assert schema_errors == []

    tampered = copy.deepcopy(report["receipt"])
    tampered["source_manifest_sha256"] = "sha256:" + ("0" * 64)
    _, receipt_errors = validate_execution_receipts(
        [tampered], load_json(DEFAULT_CATALOG), load_json(DEFAULT_LADDER), load_json(DEFAULT_MATRIX)
    )
    assert "RECEIPT_BINDING_DRIFT" in {item["code"] for item in receipt_errors}


@pytest.mark.unit
def test_packet_receipt_may_bind_an_explicit_cross_rung_validation_lane() -> None:
    receipt_path = (
        Path(__file__).resolve().parents[2]
        / "docs"
        / "notion_parity"
        / "evidence"
        / "internal_collaboration_receipts"
        / "t0b-p3-l4-cross-process-browser-20260824.json"
    )
    receipt = load_json(receipt_path)
    checkpoint = load_json(
        Path(__file__).resolve().parents[2]
        / "docs"
        / "notion_parity"
        / "checkpoints"
        / "ui-internal-collaboration-t0b-p3-cross-process-browser-20260824.json"
    )
    valid, errors = validate_execution_receipts(
        [receipt],
        load_json(DEFAULT_CATALOG),
        load_json(DEFAULT_LADDER),
        load_json(DEFAULT_MATRIX),
        [checkpoint],
    )
    assert errors == []
    assert [item["receipt_id"] for item in valid] == [receipt["receipt_id"]]

    tampered = copy.deepcopy(receipt)
    tampered["rung_id"] = "L0-CONTRACT-BOOT"
    tampered["lane_id"] = "L0-PIP-CHECK"
    _, errors = validate_execution_receipts(
        [tampered],
        load_json(DEFAULT_CATALOG),
        load_json(DEFAULT_LADDER),
        load_json(DEFAULT_MATRIX),
        [checkpoint],
    )
    assert "RECEIPT_PACKET_RUNG_MISMATCH" in {item["code"] for item in errors}


@pytest.mark.unit
def test_t0b_p3_successor_verifier_owns_live_gateway_source(monkeypatch: pytest.MonkeyPatch) -> None:
    def stale_p1_verifier_must_not_run():
        raise AssertionError("T0B-P1 live-source verifier ran after its declared successor closed")

    def stale_p2_verifier_must_not_run():
        raise AssertionError("T0B-P2 live-source verifier ran after its declared successor closed")

    monkeypatch.setattr(collaborative_validation, "verify_transport_authority", stale_p1_verifier_must_not_run)
    monkeypatch.setattr(collaborative_validation, "verify_runtime_gateway", stale_p2_verifier_must_not_run)
    report, exit_code = verify_bundle(mode="audit", sbe_report=SBE_PASS)
    assert exit_code == 0, report

    monkeypatch.setattr(
        collaborative_validation,
        "verify_cross_process_gateway",
        lambda: {"status": "fail", "error_count": 1, "gate_passed": False},
    )
    report, exit_code = verify_bundle(mode="audit", sbe_report=SBE_PASS)
    assert exit_code == 1
    assert "T0B_CROSS_PROCESS_GATEWAY_RECEIPT_INVALID" in _codes(report)


@pytest.mark.unit
def test_e1_p3_gate_requires_completed_convergence_verifier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        collaborative_validation,
        "verify_convergence_corpus",
        lambda: {
            "status": "fail",
            "error_count": 1,
            "gate_passed": False,
            "native_ime_pending": True,
            "scenario_count": 4,
            "source_count": 14,
            "test_count": 10,
            "product_complete": False,
        },
    )

    report, exit_code = verify_bundle(mode="audit", sbe_report=SBE_PASS)

    assert exit_code == 1
    assert "E1_CONVERGENCE_CORPUS_RECEIPT_INVALID" in _codes(report)


@pytest.mark.unit
def test_current_sbe_audit_successor_is_exact_and_fail_closed(
    tmp_path: Path,
) -> None:
    current, exit_code = collaborative_validation.audit_contracts(
        collaborative_validation.KNOWLEDGE_DOCS_ROOT.resolve()
    )
    checkpoint = load_current_checkpoint(DEFAULT_CHECKPOINT)[0]
    snapshot = checkpoint["cross_catalog_ownership_snapshot"]

    assert exit_code == 0, current
    assert collaborative_validation._accept_current_sbe_audit_successor(
        snapshot, current
    )

    changed_report = copy.deepcopy(current)
    changed_report["source_file_count"] = 32
    assert not collaborative_validation._accept_current_sbe_audit_successor(
        snapshot, changed_report
    )

    changed_successor = load_json(collaborative_validation.SBE_AUDIT_SUCCESSOR_PATH)
    changed_successor["claims"]["product_complete"] = True
    changed_path = _write_json(tmp_path, "changed-sbe-successor.json", changed_successor)
    assert not collaborative_validation._accept_current_sbe_audit_successor(
        snapshot,
        current,
        successor_path=changed_path,
    )


@pytest.mark.unit
def test_cli_audit_is_zero_and_gate_is_nonzero() -> None:
    root = Path(__file__).resolve().parents[3]
    audit = subprocess.run(
        [sys.executable, "-E", "-m", "code_assist_v1.tools.verify_internal_collaborative_validation_ladder", "--mode", "audit", "--compact"],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
        timeout=120,
    )
    assert audit.returncode == 0, audit.stdout + audit.stderr
    assert json.loads(audit.stdout)["completion_state"] == "incomplete"

    gate = subprocess.run(
        [sys.executable, "-E", "-m", "code_assist_v1.tools.verify_internal_collaborative_completion", "--mode", "gate", "--compact"],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
        timeout=120,
    )
    assert gate.returncode == 1, gate.stdout + gate.stderr
    assert _codes(json.loads(gate.stdout)) == {"STRICT_COMPLETION_INCOMPLETE"}
