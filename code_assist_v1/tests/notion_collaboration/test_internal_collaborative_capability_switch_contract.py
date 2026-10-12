from __future__ import annotations

import copy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from code_assist_v1.tools.verify_internal_collaborative_capability_switch_contract import (
    CONTRACT_PATH,
    PACKAGE_ROOT,
    verify,
)


@pytest.mark.unit
def test_k0_p1_contract_binds_default_off_implementation_and_exact_capabilities() -> None:
    report = verify()
    assert report["status"] == "pass", report
    assert report["error_count"] == 0
    assert report["capability_count"] == 11
    assert report["claims"] == {
        "K0_P1_implemented_default_off": True,
        "runtime_or_feature_enabled": False,
        "schema_migration_implemented": False,
        "product_complete": False,
    }


@pytest.mark.unit
def test_k0_p1_contract_does_not_alias_sbe_or_claim_activation() -> None:
    value = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    namespace = value["namespace_contract"]
    assert namespace["prefix"] == "CODE_ASSIST_NOTION_COLLAB_"
    assert namespace["SBE_flag_alias_or_bypass_allowed"] is False
    assert namespace["existing_single_user_Notion_behavior_controlled_by_this_namespace"] is False
    assert value["claims"]["SBE_flags_unchanged"] is True
    assert value["claims"]["runtime_or_feature_enabled"] is False
    assert value["claims"]["service_or_port_started"] is False


@pytest.mark.unit
def test_k0_p1_contract_freezes_additive_rollback_without_schema_downgrade() -> None:
    value = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    schema = value["schema_and_rollback_contract"]
    assert schema["base_schema_version"] == 2026081317
    assert schema["parallel_archive_schema_version"] == 2026081318
    assert schema["reserved_collaboration_schema_version"] == 2026082401
    assert schema["schema_downgrade_required_by_code_rollback"] is False
    assert schema["migration_implemented_by_K0"] is False


@pytest.mark.unit
def test_k0_p1_contract_tamper_duplicate_nonfinite_and_cli_fail_closed(tmp_path: Path) -> None:
    value = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    tampered = copy.deepcopy(value)
    tampered["namespace_contract"]["SBE_flag_alias_or_bypass_allowed"] = True
    path = tmp_path / "tampered-k0-contract.json"
    path.write_text(json.dumps(tampered), encoding="utf-8")
    assert "NAMESPACE_BYPASS" in {item["code"] for item in verify(path)["errors"]}

    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")
    assert verify(duplicate)["errors"][0]["code"] == "CONTRACT_READ_FAILED"
    nonfinite = tmp_path / "nonfinite.json"
    nonfinite.write_text('{"schema_version":NaN}', encoding="utf-8")
    assert verify(nonfinite)["errors"][0]["code"] == "CONTRACT_READ_FAILED"

    completed = subprocess.run(
        [sys.executable, "-E", "-m", "code_assist_v1.tools.verify_internal_collaborative_capability_switch_contract", "--compact"],
        cwd=PACKAGE_ROOT.parent,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert json.loads(completed.stdout)["status"] == "pass"
