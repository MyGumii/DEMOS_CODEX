from __future__ import annotations

import copy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from code_assist_v1.tools.internal_collaborative_validation import (
    DEFAULT_CATALOG,
    DEFAULT_LADDER,
)
from code_assist_v1.tools.verify_internal_collaborative_backup_adr import (
    DEFAULT_ADR,
    verify_backup_adr,
)
from code_assist_v1.tools.verify_notion_parity_catalog import load_json


def _write_json(tmp_path: Path, name: str, value: object) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _codes(report: dict[str, object]) -> set[str]:
    return {item["code"] for item in report["errors"]}  # type: ignore[index]


def _assert_frozen_adr_rejects_current_authority(report: dict[str, object]) -> None:
    assert report["status"] == "fail"
    assert report["error_count"] == 17
    assert _codes(report) == {
        "AUTHORITY_HASH_DRIFT",
        "SBE_HASH_DRIFT",
        "SCHEMA_LINEAGE_DRIFT",
    }
    assert report["summary"] == {
        "packet_id": "O0A-P1",
        "sbe_manifest_count": 18,
        "mandatory_component_count": 11,
        "rebuildable_component_count": 6,
        "never_restore_component_count": 6,
        "production_recovery_contract_frozen": False,
        "runtime_implemented": False,
        "product_complete": False,
    }


@pytest.mark.unit
def test_o0a_p1_frozen_backup_adr_rejects_later_authority_inputs() -> None:
    report, exit_code = verify_backup_adr()
    assert exit_code == 1
    _assert_frozen_adr_rejects_current_authority(report)


@pytest.mark.unit
def test_o0a_p1_strict_json_rejects_duplicate_keys_and_nonfinite_values(tmp_path: Path) -> None:
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")
    report, exit_code = verify_backup_adr(adr_path=duplicate)
    assert exit_code == 1
    assert _codes(report) == {"ADR_READ_FAILED"}

    nonfinite = tmp_path / "nonfinite.json"
    nonfinite.write_text('{"schema_version":NaN}', encoding="utf-8")
    report, exit_code = verify_backup_adr(adr_path=nonfinite)
    assert exit_code == 1
    assert _codes(report) == {"ADR_READ_FAILED"}


@pytest.mark.unit
def test_o0a_p1_authority_sbe_and_l7_hash_or_order_drift_fail_closed(tmp_path: Path) -> None:
    adr = copy.deepcopy(load_json(DEFAULT_ADR))
    adr["authority_inputs"]["p7_4_implementation_documents"][0]["sha256"] = "sha256:" + ("0" * 64)
    report, exit_code = verify_backup_adr(adr_path=_write_json(tmp_path, "p7-drift.json", adr))
    assert exit_code == 1
    assert "AUTHORITY_HASH_DRIFT" in _codes(report)

    adr = copy.deepcopy(load_json(DEFAULT_ADR))
    manifest = adr["authority_inputs"]["sbe_instruction_manifest"]
    manifest[0], manifest[1] = manifest[1], manifest[0]
    report, exit_code = verify_backup_adr(adr_path=_write_json(tmp_path, "sbe-order.json", adr))
    assert exit_code == 1
    assert "SBE_MANIFEST_ORDER_DRIFT" in _codes(report)

    adr = copy.deepcopy(load_json(DEFAULT_ADR))
    adr["validation_contract"]["existing_baseline_tests"][0]["sha256"] = "sha256:" + ("0" * 64)
    report, exit_code = verify_backup_adr(adr_path=_write_json(tmp_path, "l7-hash.json", adr))
    assert exit_code == 1
    assert "AUTHORITY_HASH_DRIFT" in _codes(report)


@pytest.mark.unit
def test_o0a_p1_component_denominator_and_classification_overlap_fail_closed(tmp_path: Path) -> None:
    adr = copy.deepcopy(load_json(DEFAULT_ADR))
    adr["component_inventory"]["mandatory_non_rebuildable"].pop()
    adr["component_inventory"]["rebuildable"][0]["id"] = "sessions_csrf_tokens_tickets_and_bearer_tokens"
    report, exit_code = verify_backup_adr(adr_path=_write_json(tmp_path, "inventory-gap.json", adr))
    assert exit_code == 1
    assert {
        "MANDATORY_COMPONENT_DENOMINATOR_DRIFT",
        "REBUILDABLE_COMPONENT_DENOMINATOR_DRIFT",
        "COMPONENT_CLASSIFICATION_OVERLAP",
    }.issubset(_codes(report))


@pytest.mark.unit
def test_o0a_p1_manifest_and_write_barrier_tamper_fail_closed(tmp_path: Path) -> None:
    adr = copy.deepcopy(load_json(DEFAULT_ADR))
    adr["coordinated_generation_manifest_contract"]["required_authority_records"].remove("identity")
    adr["coordinated_generation_manifest_contract"]["cross_database_transaction_claimed"] = True
    states = adr["write_barrier_contract"]["ordered_states"]
    states[4], states[5] = states[5], states[4]
    adr["write_barrier_contract"]["false_completion_allowed"] = True
    report, exit_code = verify_backup_adr(adr_path=_write_json(tmp_path, "unsafe-generation.json", adr))
    assert exit_code == 1
    assert {
        "GENERATION_MANIFEST_CONTRACT_DRIFT",
        "WRITE_BARRIER_ORDER_DRIFT",
        "WRITE_BARRIER_FALSE_GREEN",
    }.issubset(_codes(report))


@pytest.mark.unit
def test_o0a_p1_restore_session_and_compaction_tamper_fail_closed(tmp_path: Path) -> None:
    adr = copy.deepcopy(load_json(DEFAULT_ADR))
    adr["restore_contract"]["target"] = "existing_live_root"
    adr["restore_contract"]["session_policy"] = "reuse_copied_sessions"
    adr["restore_contract"]["copied_session_registry_may_revive_sessions"] = True
    adr["event_snapshot_compaction_contract"]["event_deletion_allowed_only_after"].pop()
    adr["event_snapshot_compaction_contract"]["page_revision_auto_delete_allowed"] = True
    report, exit_code = verify_backup_adr(adr_path=_write_json(tmp_path, "unsafe-restore.json", adr))
    assert exit_code == 1
    assert {
        "RESTORE_SAFETY_BOUNDARY_DRIFT",
        "COMPACTION_FENCE_DENOMINATOR_DRIFT",
        "COMPACTION_SAFETY_BOUNDARY_DRIFT",
    }.issubset(_codes(report))


@pytest.mark.unit
def test_o0a_p1_g0_decision_and_false_completion_claim_fail_closed(tmp_path: Path) -> None:
    adr = copy.deepcopy(load_json(DEFAULT_ADR))
    adr["recovery_decision_boundary"]["status"] = "frozen"
    adr["recovery_decision_boundary"]["production_recovery_contract_frozen_by_O0A_P1"] = True
    adr["claims"]["collaborative_backup_runtime_implemented"] = True
    adr["claims"]["target_restore_executed"] = True
    adr["claims"]["product_complete"] = True
    report, exit_code = verify_backup_adr(adr_path=_write_json(tmp_path, "false-green.json", adr))
    assert exit_code == 1
    assert {
        "G0_U_DECISION_BOUNDARY_DRIFT",
        "RECOVERY_TARGET_FALSE_FREEZE",
        "CONTRACT_FALSE_GREEN",
    }.issubset(_codes(report))


@pytest.mark.unit
def test_o0a_p1_catalog_deliverable_and_l7_lane_drift_fail_closed(tmp_path: Path) -> None:
    catalog = copy.deepcopy(load_json(DEFAULT_CATALOG))
    packet = next(item for item in catalog["packets"] if item["id"] == "O0A-P1")
    packet["deliverables"].remove("event_snapshot_compaction_high_water_contract")
    report, exit_code = verify_backup_adr(catalog_path=_write_json(tmp_path, "catalog.json", catalog))
    assert exit_code == 1
    assert "PACKET_DELIVERABLE_DRIFT" in _codes(report)

    ladder = copy.deepcopy(load_json(DEFAULT_LADDER))
    lane = next(
        lane
        for rung in ladder["rungs"]
        for lane in rung["lanes"]
        if lane["id"] == "L7-KNOWLEDGE-BACKUP-BASELINE"
    )
    lane["argv"] = lane["argv"][:-1]
    report, exit_code = verify_backup_adr(ladder_path=_write_json(tmp_path, "ladder.json", ladder))
    assert exit_code == 1
    assert "L7_BASELINE_TEST_DRIFT" in _codes(report)


@pytest.mark.unit
def test_o0a_p1_cli_preserves_frozen_adr_fail_closed_result() -> None:
    root = Path(__file__).resolve().parents[3]
    run = subprocess.run(
        [
            sys.executable,
            "-E",
            "-m",
            "code_assist_v1.tools.verify_internal_collaborative_backup_adr",
            "--compact",
        ],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    assert run.returncode == 1, run.stdout + run.stderr
    _assert_frozen_adr_rejects_current_authority(json.loads(run.stdout))
