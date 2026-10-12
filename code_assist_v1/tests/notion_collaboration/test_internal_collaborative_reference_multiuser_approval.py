from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from code_assist_v1.tools.internal_collaborative_reference_multiuser_approval import (
    CONTRACT,
    EXACT_APPROVAL_TEXT,
    compile_approval_receipt,
    verify_approval_receipt,
)


RECORDED_AT = "2026-08-25T20:20:00+09:00"


def _write(path: Path, value: object) -> Path:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


@pytest.mark.unit
def test_v0r_p1_exact_owner_text_compiles_a_scoped_non_evidentiary_receipt(
    tmp_path: Path,
) -> None:
    receipt = compile_approval_receipt(
        approval_text=EXACT_APPROVAL_TEXT,
        recorded_at=RECORDED_AT,
    )
    path = _write(tmp_path / "receipt.json", receipt)
    report, exit_code = verify_approval_receipt(path)

    assert receipt["contract"] == CONTRACT
    assert receipt["authorization"]["operator_action_ids"][0] == "V0R-PREFLIGHT"
    assert receipt["authorization"]["operator_action_ids"][-1] == "V0R-CLEANUP"
    assert len(receipt["authorization"]["capture_state_ids"]) == 18
    assert receipt["authorization"]["expected_screenshot_count"] == 36
    assert receipt["claims"]["owner_approval_received"] is True
    assert receipt["claims"]["execution_authorized"] is True
    assert receipt["claims"]["external_reference_mutation_executed"] is False
    assert receipt["claims"]["V0R_P1_gate_passed"] is False
    assert report["status"] == "pass"
    assert report["approval_received"] is True
    assert report["execution_authorized"] is True
    assert report["approval_is_test_evidence"] is False
    assert exit_code == 0


@pytest.mark.unit
def test_v0r_p1_receipt_compiler_rejects_paraphrase_or_invalid_time() -> None:
    with pytest.raises(ValueError, match="exact AP-REFERENCE-MULTIUSER"):
        compile_approval_receipt(
            approval_text="AP-REFERENCE-MULTIUSER 승인",
            recorded_at=RECORDED_AT,
        )
    with pytest.raises(ValueError, match="RFC 3339"):
        compile_approval_receipt(
            approval_text=EXACT_APPROVAL_TEXT,
            recorded_at="today",
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("mutate", "error_code"),
    [
        (
            lambda value: value["execution_manifest"].__setitem__(
                "sha256", "sha256:" + "0" * 64
            ),
            "MANIFEST_BINDING",
        ),
        (
            lambda value: value["authorization"].__setitem__(
                "identity_aliases", ["REF-OWNER", "OTHER"]
            ),
            "AUTHORIZED_SCOPE",
        ),
        (
            lambda value: value["authorization"].__setitem__(
                "public_publish_or_public_link_allowed", True
            ),
            "FORBIDDEN_SCOPE",
        ),
        (
            lambda value: value["validity"].__setitem__(
                "expires_on_manifest_hash_drift", False
            ),
            "VALIDITY_BOUNDARY",
        ),
        (
            lambda value: value["claims"].__setitem__(
                "external_reference_mutation_executed", True
            ),
            "CLAIMS_FALSE_GREEN",
        ),
    ],
)
def test_v0r_p1_receipt_verifier_rejects_scope_hash_validity_and_false_green_tamper(
    tmp_path: Path,
    mutate,
    error_code: str,
) -> None:
    receipt = compile_approval_receipt(
        approval_text=EXACT_APPROVAL_TEXT,
        recorded_at=RECORDED_AT,
    )
    candidate = copy.deepcopy(receipt)
    mutate(candidate)
    report, exit_code = verify_approval_receipt(
        _write(tmp_path / "candidate.json", candidate)
    )

    assert report["status"] == "fail"
    assert error_code in {item["code"] for item in report["errors"]}
    assert report["execution_authorized"] is False
    assert exit_code == 1


@pytest.mark.unit
def test_v0r_p1_receipt_verifier_rejects_duplicate_json_keys(tmp_path: Path) -> None:
    candidate = tmp_path / "duplicate.json"
    candidate.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")

    report, exit_code = verify_approval_receipt(candidate)

    assert report["status"] == "fail"
    assert report["errors"][0]["code"] == "RECEIPT_READ_FAILED"
    assert exit_code == 1

