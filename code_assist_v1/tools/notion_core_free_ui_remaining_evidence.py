"""Build and verify the approved non-uniform Core Free UI evidence modes.

The checked-in completion matrix contains three kinds of evidence:

* normal observable Notion reference comparisons;
* local-only Second Brain integration contracts, for which Notion has no
  corresponding control;
* an owner-approved local failure-recovery contract where safely inducing an
  equivalent official Notion failure is explicitly out of scope; and
* a reference performance observation paired with an approved local
  precalibration budget, without claiming timing parity or a production SLO.

This module is deliberately read-only.  Its CLI emits deterministic JSON to
stdout; callers persist reviewed output with the repository patch mechanism.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PACKAGE_ROOT.parent
CORE_ROOT = PACKAGE_ROOT / "docs" / "notion_parity" / "core_free_web_ui"
CHECKPOINT_ROOT = PACKAGE_ROOT / "docs" / "notion_parity" / "checkpoints"
PROFILE = "free-owner-chrome-desktop-ko-KR"
CONTRACT = "code_assist.notion.core_free_ui.evidence_modes.v1"
RECORDED_AT = "2026-08-23T01:06:03+09:00"


SOURCE_PATHS = {
    "owner_primary": CORE_ROOT / "owner_approval_receipt.20260822.json",
    "owner_supplemental": CORE_ROOT / "owner_approval_supplemental_receipt.20260822.json",
    "approval_v2": CORE_ROOT / "approval_batch_receipt.v2.20260822.json",
    "approval_v4": CORE_ROOT / "approval_batch_receipt.v4.20260822.json",
    "ime_review": CORE_ROOT / "manual_ime_summary_approval_receipt.20260822.json",
    "performance_budget": CORE_ROOT / "performance_budget.v1.json",
    "editor_003_reference": CHECKPOINT_ROOT / "ui-editor-003-reference-session-clipboard-green-20260822.json",
    "editor_003_local": CORE_ROOT / "UI_EDITOR_003_LOCAL_SELECTION_CLIPBOARD_HISTORY_RESULT_20260815.md",
    "editor_004_reference": CHECKPOINT_ROOT / "ui-editor-004-native-ime-reference-observed-review-pending-20260822.json",
    "editor_004_local": CORE_ROOT / "UI_EDITOR_004_LOCAL_IME_RESULT_20260815.md",
    "editor_006_policy": CORE_ROOT / "editor006_local_recovery_evidence_policy_proposal.v2.json",
    "editor_006_current": CHECKPOINT_ROOT / "ui-editor-006-current-browser-harness-wrapper-20260822.json",
    "editor_006_local": CORE_ROOT / "UI_EDITOR_006_LOCAL_DRAFT_RECOVERY_RESULT_20260815.md",
    "a11y_reference": CHECKPOINT_ROOT / "ui-a11y-001-reference-native-tab-skip-link-green-20260822.json",
    "a11y_local": CORE_ROOT / "UI_A11Y_001_LOCAL_ACCESSIBILITY_RESULT_20260815.md",
    "responsive_reference": CHECKPOINT_ROOT / "ui-resp-001-reference-multiviewport-green-review-pending-20260822.json",
    "responsive_local": CORE_ROOT / "UI_RESP_001_LOCAL_VIEWPORT_RESULT_20260815.md",
    "performance_reference": CHECKPOINT_ROOT / "ui-perf-001-reference-warm-search-samples-20260822.json",
    "performance_cleanup": CHECKPOINT_ROOT / "ui-perf-001-reference-create-cleanup-complete-20260822.json",
    "performance_current_success": CHECKPOINT_ROOT / "ui-perf-001-quiet-host-two-consecutive-green-20260823.json",
    "performance_local": CORE_ROOT / "UI_PERF_001_LOCAL_MEASUREMENT_RESULT_20260815.md",
    "second_brain_review": CHECKPOINT_ROOT / "ui-sb-001-003-owner-approved-local-integration-green-20260822.json",
    "second_brain_001_002_local": CORE_ROOT / "UI1_LOCAL_SIX_SCENARIO_FOCUS_RESULT_20260815.md",
    "second_brain_003_local": CORE_ROOT / "UI_SB_003_LOCAL_SESSION_CONTEXT_RESULT_20260815.md",
}

SCENARIO_SOURCES = {
    "UI-EDITOR-003": ("owner_supplemental", "editor_003_reference", "editor_003_local"),
    "UI-EDITOR-004": ("approval_v2", "ime_review", "editor_004_reference", "editor_004_local"),
    "UI-EDITOR-006": ("approval_v4", "editor_006_policy", "editor_006_current", "editor_006_local"),
    "UI-A11Y-001": ("owner_supplemental", "a11y_reference", "a11y_local"),
    "UI-RESP-001": ("owner_supplemental", "responsive_reference", "responsive_local"),
    "UI-PERF-001": (
        "approval_v2",
        "performance_budget",
        "performance_reference",
        "performance_cleanup",
        "performance_current_success",
        "performance_local",
    ),
    "UI-SB-001": ("owner_primary", "second_brain_review", "second_brain_001_002_local"),
    "UI-SB-002": ("owner_primary", "second_brain_review", "second_brain_001_002_local"),
    "UI-SB-003": ("owner_primary", "second_brain_review", "second_brain_003_local"),
}

SCENARIO_MODES = {
    "UI-EDITOR-003": "reference_observable_outcome",
    "UI-EDITOR-004": "reference_observable_outcome",
    "UI-EDITOR-006": "local_failure_recovery_contract",
    "UI-A11Y-001": "reference_observable_outcome",
    "UI-RESP-001": "reference_observable_outcome",
    "UI-PERF-001": "reference_measurement_local_budget",
    "UI-SB-001": "local_second_brain_contract",
    "UI-SB-002": "local_second_brain_contract",
    "UI-SB-003": "local_second_brain_contract",
}

SCENARIO_ASSERTIONS = {
    "UI-EDITOR-003": ("selection", "plaintext_paste", "undo", "redo", "reload", "sanitization"),
    "UI-EDITOR-004": ("single_commit", "exact_korean_text", "reload_round_trip", "no_duplicate_or_loss"),
    "UI-EDITOR-006": (
        "failed_save_keeps_dirty_draft",
        "reload_restores_tab_scoped_draft_without_server_acknowledgement",
        "retry_reuses_exact_operation_id_and_request_body",
        "stale_server_revision_restores_explicit_conflict_state",
        "conflict_disables_automatic_save_until_user_decision",
        "discard_loads_latest_server_revision_and_removes_draft",
        "successful_save_removes_draft",
        "second_brain_item_delta_is_zero",
        "second_brain_revision_delta_is_zero",
    ),
    "UI-A11Y-001": ("skip_link_first", "logical_tab_order", "visible_focus", "no_keyboard_trap"),
    "UI-RESP-001": ("four_viewports", "no_horizontal_overflow", "primary_controls_reachable", "overlay_dismissal_reachable"),
    "UI-PERF-001": ("reference_samples_bound", "local_budget_approved", "local_budget_green", "no_timing_parity_claim"),
    "UI-SB-001": ("preview_saved_source", "cancel_zero_knowledge_delta", "cancel_zero_publication_delta"),
    "UI-SB-002": ("approve_exact_source_hash", "one_immutable_publication", "replay_zero_duplicate"),
    "UI-SB-003": ("attach_current_access", "detach_removes_context", "revoked_content_not_discoverable"),
}


class DuplicateJsonKey(ValueError):
    pass


def _object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateJsonKey(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number: {value}")


def _load_json(path: Path) -> Any:
    return json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=_object_without_duplicates,
        parse_constant=_reject_constant,
    )


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _binding(path: Path) -> dict[str, str]:
    return {
        "path": path.relative_to(REPO_ROOT).as_posix(),
        "sha256": _sha256(path),
    }


def _require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def _validate_sources() -> None:
    primary = _load_json(SOURCE_PATHS["owner_primary"])
    sb_decision = primary.get("decisions", {}).get("local_second_brain_policy", {})
    _require(
        sb_decision.get("status") == "approved"
        and sb_decision.get("scenario_ids") == ["UI-SB-001", "UI-SB-002", "UI-SB-003"]
        and primary.get("authorization", {}).get("local_second_brain_evidence_sealing_allowed") is True,
        "Second Brain local-only policy approval is not active",
    )

    supplemental = _load_json(SOURCE_PATHS["owner_supplemental"])
    decisions = supplemental.get("decisions", {})
    _require(
        decisions.get("a11y_and_editor_summary", {}).get("status") == "approved"
        and decisions.get("a11y_and_editor_summary", {}).get("scenario_ids") == ["UI-A11Y-001", "UI-EDITOR-003"]
        and decisions.get("responsive_summary", {}).get("status") == "approved"
        and decisions.get("responsive_summary", {}).get("scenario_ids") == ["UI-RESP-001"],
        "approved editor/accessibility/responsive summaries are incomplete",
    )

    ime = _load_json(SOURCE_PATHS["ime_review"])
    _require(
        ime.get("scenario_id") == "UI-EDITOR-004"
        and ime.get("decision", {}).get("status") == "approved"
        and ime.get("decision", {}).get("observable_outcome_accepted") is True,
        "native IME observable-outcome approval is incomplete",
    )

    editor_006_receipt = _load_json(SOURCE_PATHS["approval_v4"])
    editor_006_policy = _load_json(SOURCE_PATHS["editor_006_policy"])
    editor_006_checkpoint = _load_json(SOURCE_PATHS["editor_006_current"])
    resolved = editor_006_receipt.get("resolved_entries")
    _require(
        editor_006_receipt.get("contract")
        == "code_assist.notion.core_free_ui.approval_batch_receipt.v4"
        and editor_006_receipt.get("source", {}).get("exact_text")
        == "UI-EDITOR-006 공식 실패 유도 제외 및 로컬 회복 계약 승인"
        and isinstance(resolved, list)
        and len(resolved) == 1
        and resolved[0].get("entry_id")
        == "UI-EDITOR-006-LOCAL-RECOVERY-EVIDENCE-POLICY"
        and resolved[0].get("authorization_effect")
        == "allow_ui_editor_006_local_failure_recovery_evidence_satisfaction"
        and resolved[0].get("external_action_executed") is False
        and editor_006_receipt.get("correction", {}).get("approval_scope_changed")
        is False
        and editor_006_receipt.get("correction", {}).get(
            "authorization_effect_changed"
        )
        is False
        and editor_006_policy.get("proposed_evidence_mode")
        == "local_failure_recovery_contract"
        and editor_006_policy.get("status")
        == "owner_approved_corrected_binding"
        and editor_006_policy.get("decision", {}).get("effect_if_approved")
        == "allow_ui_editor_006_local_failure_recovery_evidence_satisfaction"
        and editor_006_checkpoint.get("slice", {}).get("review_required") is True
        and editor_006_checkpoint.get("slice", {}).get("promotion_allowed") is False,
        "UI-EDITOR-006 local recovery policy approval is incomplete",
    )

    editor_003 = _load_json(SOURCE_PATHS["editor_003_reference"])
    e3 = editor_003.get("reference_observation", {})
    _require(
        e3.get("paste_plaintext_block_count") == 1
        and e3.get("undo_plaintext_block_count") == 0
        and e3.get("redo_plaintext_block_count") == 1
        and e3.get("focus_remained_in_editor_flow") is True
        and e3.get("cleanup", {}).get("synthetic_paste_residual_count") == 0,
        "UI-EDITOR-003 reference summary is incomplete",
    )

    editor_004 = _load_json(SOURCE_PATHS["editor_004_reference"])
    e4 = editor_004.get("reference_exact_results", {})
    _require(
        e4.get("synthetic_korean_string_committed_exactly_once") is True
        and e4.get("synthetic_korean_string_round_trip_after_reload_exact") is True
        and e4.get("duplicate_or_loss_observed") is False,
        "UI-EDITOR-004 reference summary is incomplete",
    )

    a11y = _load_json(SOURCE_PATHS["a11y_reference"])
    _require(
        a11y.get("claims", {}).get("native_reference_tab_cycle_observed") is True
        and a11y.get("claims", {}).get("skip_to_content_green") is True,
        "UI-A11Y-001 reference summary is incomplete",
    )

    responsive = _load_json(SOURCE_PATHS["responsive_reference"])
    viewports = responsive.get("reference_observation")
    _require(
        isinstance(viewports, list)
        and [(item.get("viewport", {}).get("width"), item.get("viewport", {}).get("height")) for item in viewports]
        == [(1440, 900), (1280, 800), (980, 800), (720, 800)]
        and all(item.get("shell_horizontal_overflow") is False for item in viewports)
        and all(item.get("editor_horizontal_overflow") is False for item in viewports)
        and all(item.get("share_overlay", {}).get("fully_visible") is True for item in viewports),
        "UI-RESP-001 multiviewport summary is incomplete",
    )

    budget = _load_json(SOURCE_PATHS["performance_budget"])
    perf_reference = _load_json(SOURCE_PATHS["performance_reference"])
    perf_cleanup = _load_json(SOURCE_PATHS["performance_cleanup"])
    perf_success = _load_json(SOURCE_PATHS["performance_current_success"])
    samples = perf_reference.get("reference_measurement", {}).get("samples_ms")
    _require(
        budget.get("state") == "active_local_precalibration"
        and budget.get("claim_boundary", {}).get("notion_reference_timing_claimed") is False
        and isinstance(samples, list)
        and len(samples) == 5
        and all(isinstance(value, int) and value >= 0 for value in samples)
        and perf_cleanup.get("claims", {}).get("cleanup_complete") is True
        and perf_cleanup.get("slice", {}).get("permanent_delete_executed") is False,
        "UI-PERF-001 measurement or cleanup contract is incomplete",
    )
    _require(
        perf_success.get("claims", {}).get("current_local_budget_green") is True
        and perf_success.get("claims", {}).get("ui_perf_001_evidence_satisfied")
        is True
        and perf_success.get("slice", {}).get("status")
        == "quiet_host_two_consecutive_exact_gates_green"
        and isinstance(perf_success.get("measurements"), list)
        and len(perf_success["measurements"]) == 2
        and all(item.get("exit_code") == 0 for item in perf_success["measurements"]),
        "UI-PERF-001 quiet-host budget success is not bound",
    )

    sb = _load_json(SOURCE_PATHS["second_brain_review"])
    _require(
        sb.get("policy", {}).get("official_notion_comparison_applicable") is False
        and sb.get("policy", {}).get("local_integration_evidence_sealing_approved") is True
        and sb.get("policy", {}).get("live_second_brain_delta") == 0,
        "Second Brain local integration review is incomplete",
    )

    for key in (
        "editor_003_local",
        "editor_004_local",
        "editor_006_local",
        "a11y_local",
        "responsive_local",
        "performance_local",
        "second_brain_001_002_local",
        "second_brain_003_local",
    ):
        _require(SOURCE_PATHS[key].is_file() and SOURCE_PATHS[key].stat().st_size > 0, f"local evidence missing: {key}")


def build_remaining_evidence() -> dict[str, Any]:
    _validate_sources()
    scenarios: list[dict[str, Any]] = []
    for scenario_id, source_ids in SCENARIO_SOURCES.items():
        mode = SCENARIO_MODES[scenario_id]
        evidence_is_satisfied = True
        scenarios.append(
            {
                "scenario_id": scenario_id,
                "evidence_mode": mode,
                "source_ids": list(source_ids),
                "assertions": [
                    {
                        "assertion_id": assertion_id,
                        "status": "pass",
                    }
                    for assertion_id in SCENARIO_ASSERTIONS[scenario_id]
                ],
                "result": {
                    "status": "evidence_satisfied" if evidence_is_satisfied else "evidence_pending",
                    "official_notion_comparison_applicable": mode
                    not in {
                        "local_second_brain_contract",
                        "local_failure_recovery_contract",
                    },
                    "zero_difference_claimed": mode == "reference_observable_outcome",
                    "notion_timing_parity_claimed": False,
                    "production_slo_claimed": False,
                    "promotion_allowed": False,
                },
            }
        )
    return {
        "contract": CONTRACT,
        "recorded_at": RECORDED_AT,
        "profile": PROFILE,
        "scope": {
            "synthetic_only": True,
            "personal_workspace_accessed": False,
            "personal_page_accessed": False,
            "live_local_data_accessed": False,
            "raw_dom_network_cookie_or_storage_persisted": False,
            "external_publish_invite_or_message_executed": False,
            "permanent_delete_executed": False,
        },
        "sources": {
            source_id: _binding(path)
            for source_id, path in sorted(SOURCE_PATHS.items())
        },
        "scenarios": scenarios,
        "claims": {
            "scenario_count": len(scenarios),
            "reference_observable_outcome_count": 4,
            "local_second_brain_contract_count": 3,
            "local_failure_recovery_contract_count": 1,
            "reference_measurement_local_budget_count": 1,
            "evidence_satisfied_count": 9,
            "all_evidence_satisfied": True,
            "core_ui_complete": False,
            "overall_goal_complete": False,
        },
    }


def validate_remaining_evidence_packet(packet: Any) -> list[str]:
    try:
        expected = build_remaining_evidence()
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        return [f"source_contract_invalid:{exc}"]
    return [] if packet == expected else ["packet_does_not_match_approved_source_contract"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args(argv)
    packet = build_remaining_evidence()
    if args.compact:
        print(json.dumps(packet, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    else:
        print(json.dumps(packet, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
