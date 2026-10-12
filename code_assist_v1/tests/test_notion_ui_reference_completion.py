from __future__ import annotations

import copy
import hashlib
import json

from code_assist_v1.tools.notion_core_free_ui_reference_completion import (
    CORE_ROOT,
    MANIFEST_PATH,
    PACKET_PATHS,
    SCENARIO_CHECKPOINTS,
    build_matrix,
    build_packet,
    build_summary,
    manifest_worksheet_sha256,
    pretty_sha256,
)


def test_reference_completion_builds_zero_difference_packets() -> None:
    report = build_summary()
    assert report["status"] == "pass"
    assert report["scenario_count"] == 11
    assert report["action_count"] == 22
    assert report["all_differentials_zero"] is True
    assert report["cleanup_complete"] is True
    assert report["promotion_allowed"] is False
    assert report["core_ui_complete"] is False
    assert report["overall_goal_complete"] is False

    for scenario_id in SCENARIO_CHECKPOINTS:
        packet = build_packet(scenario_id)
        assert packet["review"] == {
            "approved": True,
            "approved_by": "workspace_owner",
            "approval_scope": "redacted_summary_only",
        }
        assert packet["differential"]["all_actions_passed"] is True
        assert all(
            item["status"] == "pass" and item["difference_count"] == 0
            for item in packet["differential"]["comparisons"].values()
        )


def test_persisted_packets_and_matrix_match_deterministic_build() -> None:
    report = build_summary()
    for scenario_id, relative_path in PACKET_PATHS.items():
        path = CORE_ROOT.parents[3] / relative_path
        assert path.is_file()
        assert "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest() == report[
            "packet_pretty_sha256"
        ][scenario_id]

    matrix = build_matrix()
    assert matrix["summary"]["reference_characterized"] == 16
    assert matrix["summary"]["differential_passed"] == 16
    assert matrix["summary"]["promoted"] == 0
    assert matrix["completion_claims"]["core_ui_complete"] is False
    assert matrix["completion_claims"]["overall_goal_complete"] is False
    assert pretty_sha256(matrix) == report["matrix_pretty_sha256"]


def test_manifest_worksheet_hash_breaks_transitive_cycle_but_binds_actions() -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    baseline = manifest_worksheet_sha256(manifest)

    rebound = copy.deepcopy(manifest)
    rebound["inputs"]["scenario_plan"]["sha256"] = "sha256:" + ("0" * 64)
    rebound["inputs"]["pending_approval_batch"]["sha256"] = "sha256:" + ("1" * 64)
    rebound["claims"]["approval_received"] = not rebound["claims"]["approval_received"]
    assert manifest_worksheet_sha256(rebound) == baseline

    changed_action = copy.deepcopy(manifest)
    changed_action["actions"][0]["order"] = 999
    assert manifest_worksheet_sha256(changed_action) != baseline
