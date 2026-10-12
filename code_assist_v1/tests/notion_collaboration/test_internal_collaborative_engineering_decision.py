from __future__ import annotations

import copy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from code_assist_v1.tools.verify_internal_collaborative_engineering_decision import (
    ADR,
    PACKAGE_ROOT,
    verify,
)


@pytest.mark.unit
def test_g0_e_verifier_freezes_exact_four_engineering_decisions_only() -> None:
    report = verify()
    assert report["status"] == "pass", report
    assert report["error_count"] == 0
    assert report["selected_forward_schema_version"] == 2026082401
    assert report["claims"] == {
        "G0_E_frozen": True,
        "G0_U_frozen": False,
        "G0_aggregate_frozen": False,
        "schema_migration_implemented": False,
        "product_complete": False,
    }


@pytest.mark.unit
def test_g0_e_keeps_canonical_notion_and_second_brain_authorities_separate() -> None:
    value = json.loads(ADR.read_text(encoding="utf-8"))
    canonical = value["decisions"]["canonical_authority_architecture"]
    assert canonical["selected"] == "workspace_canonical_collaborative_documents"
    assert canonical["canonical_database"] == "one_Notion_workspace_SQLite_authority"
    assert canonical["dual_write_allowed"] is False
    assert canonical["second_brain_database_merged_into_notion"] is False
    assert "separate_SBE_authority" in canonical["second_brain_boundary"]


@pytest.mark.unit
def test_g0_e_preserves_archive_1318_and_reserves_noncolliding_2026082401_without_ddl() -> None:
    value = json.loads(ADR.read_text(encoding="utf-8"))
    schema = value["decisions"]["next_available_forward_schema_version"]
    assert schema["current_version"] == 2026081317
    assert schema["parallel_reserved_version"] == 2026081318
    assert schema["selected_version"] == 2026082401
    assert schema["selected_version"] != schema["parallel_reserved_version"]
    assert schema["migration_file_created"] is False
    assert schema["DDL_frozen"] is False
    assert schema["runtime_schema_changed"] is False
    assert "2026082401" not in (PACKAGE_ROOT / "notion_workspace_store.py").read_text(encoding="utf-8")


@pytest.mark.unit
def test_g0_e_does_not_false_freeze_g0_u_or_aggregate() -> None:
    value = json.loads(ADR.read_text(encoding="utf-8"))
    boundary = value["g0_aggregate_boundary"]
    assert boundary["G0_E_status"] == "frozen"
    assert boundary["G0_U_status"] == "open"
    assert boundary["G0_status"] == "open"
    assert boundary["implementation_packets_requiring_G0_may_start"] is False
    assert value["claims"]["product_complete"] is False
    assert value["claims"]["active_shared_goal_complete"] is False


@pytest.mark.unit
def test_g0_e_tamper_and_cli_fail_closed(tmp_path: Path) -> None:
    value = json.loads(ADR.read_text(encoding="utf-8"))
    tampered = copy.deepcopy(value)
    tampered["decisions"]["next_available_forward_schema_version"]["selected_version"] = 2026081318
    path = tmp_path / "tampered-engineering-decision.json"
    path.write_text(json.dumps(tampered), encoding="utf-8")
    report = verify(path)
    assert report["status"] == "fail"
    assert {item["code"] for item in report["errors"]} >= {"SCHEMA_SELECTED", "SCHEMA_COLLISION"}

    completed = subprocess.run(
        [
            sys.executable,
            "-E",
            "-m",
            "code_assist_v1.tools.verify_internal_collaborative_engineering_decision",
            "--compact",
        ],
        cwd=PACKAGE_ROOT.parent,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert json.loads(completed.stdout)["status"] == "pass"
