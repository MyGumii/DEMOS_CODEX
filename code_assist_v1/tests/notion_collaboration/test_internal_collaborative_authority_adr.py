from __future__ import annotations

import copy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from code_assist_v1.tools.internal_collaborative_validation import DEFAULT_CATALOG, DEFAULT_LADDER
from code_assist_v1.tools.verify_internal_collaborative_authority_adr import (
    DEFAULT_ADR,
    verify_authority_adr,
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
    assert report["error_count"] == 20
    assert _codes(report) == {
        "AUTHORITY_HASH_DRIFT",
        "SBE_HASH_DRIFT",
        "SCHEMA_LINEAGE_DRIFT",
    }
    assert report["summary"] == {
        "packet_id": "A0A-P1",
        "route_family_count": 6,
        "sbe_manifest_count": 18,
        "selected_revoke_strategy": "authoritative_send_time_epoch_check",
        "runtime_implemented": False,
        "product_complete": False,
    }


@pytest.mark.unit
def test_a0a_p1_frozen_authority_adr_rejects_later_authority_inputs() -> None:
    report, exit_code = verify_authority_adr()
    assert exit_code == 1
    _assert_frozen_adr_rejects_current_authority(report)


@pytest.mark.unit
def test_a0a_p1_strict_json_rejects_duplicate_keys_and_nonfinite_values(tmp_path: Path) -> None:
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")
    report, exit_code = verify_authority_adr(adr_path=duplicate)
    assert exit_code == 1
    assert _codes(report) == {"ADR_READ_FAILED"}

    nonfinite = tmp_path / "nonfinite.json"
    nonfinite.write_text('{"schema_version":NaN}', encoding="utf-8")
    report, exit_code = verify_authority_adr(adr_path=nonfinite)
    assert exit_code == 1
    assert _codes(report) == {"ADR_READ_FAILED"}


@pytest.mark.unit
def test_a0a_p1_authority_and_sbe_hash_or_order_drift_fail_closed(tmp_path: Path) -> None:
    adr = copy.deepcopy(load_json(DEFAULT_ADR))
    adr["authority_inputs"]["source_documents"][0]["sha256"] = "sha256:" + ("0" * 64)
    report, exit_code = verify_authority_adr(adr_path=_write_json(tmp_path, "source-drift.json", adr))
    assert exit_code == 1
    assert "AUTHORITY_HASH_DRIFT" in _codes(report)

    adr = copy.deepcopy(load_json(DEFAULT_ADR))
    manifest = adr["authority_inputs"]["sbe_instruction_manifest"]
    manifest[0], manifest[1] = manifest[1], manifest[0]
    report, exit_code = verify_authority_adr(adr_path=_write_json(tmp_path, "sbe-order.json", adr))
    assert exit_code == 1
    assert "SBE_MANIFEST_ORDER_DRIFT" in _codes(report)


@pytest.mark.unit
def test_a0a_p1_route_atomic_and_epoch_tamper_fail_closed(tmp_path: Path) -> None:
    adr = copy.deepcopy(load_json(DEFAULT_ADR))
    adr["route_storage_map"] = adr["route_storage_map"][:-1]
    adr["atomic_transaction_contract"]["required_effects"].remove("audit_event")
    adr["permission_epoch_contract"]["current_epoch_required_for"].remove("search")
    report, exit_code = verify_authority_adr(adr_path=_write_json(tmp_path, "authority-gap.json", adr))
    assert exit_code == 1
    assert {
        "ROUTE_MAP_INCOMPLETE",
        "ATOMIC_EFFECT_SET_DRIFT",
        "EPOCH_USE_SET_DRIFT",
    }.issubset(_codes(report))


@pytest.mark.unit
def test_a0a_p1_revoke_cutover_and_sbe_boundary_tamper_fail_closed(tmp_path: Path) -> None:
    adr = copy.deepcopy(load_json(DEFAULT_ADR))
    adr["revoke_delivery_fence_contract"]["selected_strategy"] = "best_effort_disconnect"
    adr["revoke_delivery_fence_contract"]["post_response_old_epoch_protected_delivery_count"] = 1
    adr["legacy_cutover_contract"]["compatibility_dual_write"] = True
    adr["legacy_cutover_contract"]["request_time_directory_enumeration"] = True
    adr["second_brain_boundary"]["notion_and_sbe_database_authorities_separate"] = False
    adr["second_brain_boundary"]["later_notion_edit_auto_sync"] = True
    report, exit_code = verify_authority_adr(adr_path=_write_json(tmp_path, "unsafe.json", adr))
    assert exit_code == 1
    assert {
        "REVOKE_STRATEGY_DRIFT",
        "REVOKE_FALSE_GREEN",
        "LEGACY_CUTOVER_BOUNDARY_WEAKENED",
        "SBE_BOUNDARY_WEAKENED",
        "SBE_FALSE_AUTHORITY_CLAIM",
    }.issubset(_codes(report))


@pytest.mark.unit
def test_a0a_p1_false_runtime_or_product_completion_claim_is_schema_rejected(tmp_path: Path) -> None:
    adr = copy.deepcopy(load_json(DEFAULT_ADR))
    adr["claims"]["runtime_implemented"] = True
    adr["claims"]["schema_migration_allocated"] = True
    adr["claims"]["product_complete"] = True
    report, exit_code = verify_authority_adr(adr_path=_write_json(tmp_path, "false-green.json", adr))
    assert exit_code == 1
    assert any(code.startswith("ADR_SCHEMA_") for code in _codes(report))


@pytest.mark.unit
def test_a0a_p1_catalog_deliverable_and_l2_lane_drift_fail_closed(tmp_path: Path) -> None:
    catalog = copy.deepcopy(load_json(DEFAULT_CATALOG))
    packet = next(item for item in catalog["packets"] if item["id"] == "A0A-P1")
    packet["deliverables"].remove("second_brain_revision_boundary")
    report, exit_code = verify_authority_adr(
        catalog_path=_write_json(tmp_path, "catalog.json", catalog)
    )
    assert exit_code == 1
    assert "PACKET_DELIVERABLE_DRIFT" in _codes(report)

    ladder = copy.deepcopy(load_json(DEFAULT_LADDER))
    lane = next(
        lane
        for rung in ladder["rungs"]
        for lane in rung["lanes"]
        if lane["id"] == "L2-WORKSPACE-SUBSTRATE-BASELINE"
    )
    lane["argv"] = lane["argv"][:-1]
    report, exit_code = verify_authority_adr(
        ladder_path=_write_json(tmp_path, "ladder.json", ladder)
    )
    assert exit_code == 1
    assert "L2_LANE_TEST_DRIFT" in _codes(report)


@pytest.mark.unit
def test_a0a_p1_cli_preserves_frozen_adr_fail_closed_result() -> None:
    root = Path(__file__).resolve().parents[3]
    run = subprocess.run(
        [
            sys.executable,
            "-E",
            "-m",
            "code_assist_v1.tools.verify_internal_collaborative_authority_adr",
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
