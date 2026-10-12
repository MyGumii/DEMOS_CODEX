from __future__ import annotations

import copy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from code_assist_v1.tools.verify_internal_collaborative_product_decision import ADR, PACKAGE_ROOT, verify


@pytest.mark.unit
def test_g0_u_freezes_approved_profile_and_aggregate_g0_only() -> None:
    report = verify()
    assert report["status"] == "pass", report
    assert report["error_count"] == 0
    assert report["load_profile"] == {
        "registered_users": 100,
        "simultaneous_connections": 30,
        "simultaneous_editors_per_page": 10,
    }
    assert report["claims"] == {
        "G0_U_frozen": True,
        "G0_E_frozen": True,
        "G0_aggregate_frozen": True,
        "product_complete": False,
    }


@pytest.mark.unit
def test_g0_u_identity_is_jit_but_workspace_and_page_visibility_remain_invite_bound() -> None:
    value = json.loads(ADR.read_text(encoding="utf-8"))
    identity = value["decisions"]["development_identity_import_scope"]
    assert identity["provisioning"] == "just_in_time_shadow_profile_for_every_active_human_account"
    assert identity["workspace_membership"] == "invitation_only_except_creator_owns_new_workspace"
    assert identity["workspace_visibility"] == "owned_or_membership_granted_only"
    assert identity["page_only_share_visibility"] == "shared_page_and_authorized_descendants_only"
    assert identity["password_or_login_authority_copied_to_notion"] is False
    assert identity["actual_10111_import_requires_approval"] == "AP-DEV-PAIR"


@pytest.mark.unit
def test_g0_u_scale_floors_are_not_product_quotas_and_feature_sets_are_disjoint() -> None:
    value = json.loads(ADR.read_text(encoding="utf-8"))
    scale = value["decisions"]["target_scale_and_deployment_topology"]
    assert scale["product_quota_mode"] == "no_plan_or_user_feature_storage_quota"
    assert (scale["registered_users"], scale["simultaneous_connections"], scale["simultaneous_editors_per_page"]) == (100, 30, 10)
    for field in ("pages_per_workspace", "blocks_per_page", "rows_per_database"):
        assert scale[field]["product_limit"] is None
        assert scale[field]["acceptance_fixture_floor"] > 0
    features = value["decisions"]["adopted_feature_denominator"]
    adopted = set(features["adopted"])
    deferred = set(features["deferred_and_hidden"])
    excluded = set(features["excluded_and_hidden"])
    assert not (adopted & deferred or adopted & excluded or deferred & excluded)
    assert "public_web_publish_and_public_links" in excluded
    assert "live_presence_cursor_selection_and_convergent_multi_user_editing" in adopted


@pytest.mark.unit
def test_g0_u_recovery_and_level_two_offline_are_exact_and_do_not_authorize_operations() -> None:
    value = json.loads(ADR.read_text(encoding="utf-8"))
    recovery = value["decisions"]["recovery_backup_retention_and_history_policy"]
    assert (recovery["rpo_seconds"], recovery["rto_seconds"], recovery["write_drain_timeout_seconds"]) == (900, 3600, 60)
    assert recovery["backup_retention"]["daily_generations"] == 30
    assert recovery["backup_retention"]["monthly_generations"] == 12
    assert recovery["page_history_retention"] == "no_automatic_deletion_of_user_visible_revisions"
    assert "AP-OPS-TARGET" in recovery["external_destination"]
    offline = value["decisions"]["offline_editing_policy"]
    assert offline["selected_level"] == 2
    assert offline["full_offline_workspace_browse_search_or_bulk_sync_adopted"] is False
    assert value["approval_receipt"]["authorizes_identity_import"] is False
    assert value["approval_receipt"]["authorizes_production_deployment"] is False


@pytest.mark.unit
def test_g0_u_tamper_duplicate_nonfinite_and_cli_fail_closed(tmp_path: Path) -> None:
    value = json.loads(ADR.read_text(encoding="utf-8"))
    tampered = copy.deepcopy(value)
    tampered["decisions"]["target_scale_and_deployment_topology"]["simultaneous_connections"] = 300
    path = tmp_path / "tampered-product-decision.json"
    path.write_text(json.dumps(tampered), encoding="utf-8")
    assert "SCALE_CORE" in {item["code"] for item in verify(path)["errors"]}

    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")
    assert verify(duplicate)["errors"][0]["code"] == "ADR_READ_FAILED"
    nonfinite = tmp_path / "nonfinite.json"
    nonfinite.write_text('{"schema_version":NaN}', encoding="utf-8")
    assert verify(nonfinite)["errors"][0]["code"] == "ADR_READ_FAILED"

    completed = subprocess.run(
        [sys.executable, "-E", "-m", "code_assist_v1.tools.verify_internal_collaborative_product_decision", "--compact"],
        cwd=PACKAGE_ROOT.parent,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert json.loads(completed.stdout)["status"] == "pass"
