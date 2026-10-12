"""Verify the approval-free V0R-P1 external reference execution contract."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = PROJECT_ROOT.parent
CONTRACT_PATH = (
    PROJECT_ROOT
    / "docs"
    / "notion_parity"
    / "core_free_web_ui"
    / "internal_collaborative_reference_multiuser_execution.v1.json"
)
EXPECTED_CONTRACT = (
    "code_assist.notion.internal_collaborative_reference_multiuser_execution.v0r_p1.v1"
)
EXACT_APPROVAL_TEXT = (
    "AP-REFERENCE-MULTIUSER 승인. REF-FREE-A/B에서 테스트 계정과 합성 페이지만 사용해 "
    "초대·공동 편집·권한 회수·화면 캡처를 수행하고, 생성 항목은 Trash로 정리해도 됨. "
    "개인 콘텐츠·공개 게시·영구 삭제는 금지."
)
EXPECTED_SOURCE_BINDINGS = (
    "code_assist_v1/docs/notion_parity/core_free_web_ui/scenarios.v1.json",
    "code_assist_v1/docs/notion_parity/core_free_web_ui/internal_collaborative_adopted_surface.v1.json",
    "code_assist_v1/docs/notion_parity/core_free_web_ui/internal_collaborative_product_decision.v1.json",
    "code_assist_v1/docs/notion_parity/checkpoints/ui-internal-collaboration-v0a-p1-exact-rgba-20260824.json",
    "code_assist_v1/tools/notion_parity/exact_pixel.py",
)
ADOPTED_SURFACE_REPO_PATH = (
    "code_assist_v1/docs/notion_parity/core_free_web_ui/"
    "internal_collaborative_adopted_surface.v1.json"
)
ADOPTED_SURFACE_TRANSITION_CHECKPOINT_REPO_PATH = (
    "code_assist_v1/docs/notion_parity/checkpoints/"
    "ui-internal-collaboration-v0r-p1-adopted-surface-lineage-successor-v3-verified-20260907.json"
)
ADOPTED_SURFACE_TRANSITION_CHECKPOINT_SHA256 = (
    "sha256:58d99425c53c7253a9bfdd2910c5e28a690d53b408025ce800d3a7566abf043b"
)
EXPECTED_ACTIONS = (
    ("V0R-PREFLIGHT", None, "REF-OWNER", "read_only"),
    ("V0R-CREATE-A", "REF-FREE-A", "REF-OWNER", "synthetic_mutation"),
    ("V0R-CREATE-B", "REF-FREE-B", "REF-OWNER", "synthetic_mutation"),
    (
        "V0R-WORKSPACE-INVITE",
        "REF-FREE-A",
        "REF-OWNER",
        "test_identity_membership_mutation",
    ),
    (
        "V0R-PAGE-SHARE",
        "REF-FREE-B",
        "REF-OWNER",
        "test_identity_page_grant_mutation",
    ),
    (
        "V0R-MULTIUSER-CAPTURE",
        "REF-FREE-A",
        "REF-OWNER+REF-COLLABORATOR",
        "synthetic_mutation_and_capture",
    ),
    (
        "V0R-PAGE-ROLE-COMMENT",
        "REF-FREE-B",
        "REF-OWNER",
        "test_identity_page_grant_mutation",
    ),
    (
        "V0R-PAGE-ROLE-VIEW",
        "REF-FREE-B",
        "REF-OWNER",
        "test_identity_page_grant_mutation",
    ),
    (
        "V0R-PAGE-REVOKE",
        "REF-FREE-B",
        "REF-OWNER",
        "test_identity_page_grant_mutation",
    ),
    (
        "V0R-WORKSPACE-REVOKE",
        "REF-FREE-A",
        "REF-OWNER",
        "test_identity_membership_mutation",
    ),
    (
        "V0R-CLEANUP",
        "REF-FREE-A+REF-FREE-B",
        "REF-OWNER",
        "recoverable_cleanup",
    ),
)
EXPECTED_STATES = (
    ("V0R-MU-001", "REF-FREE-A", "REF-OWNER", "workspace_share_dialog", "invite_ready_no_raw_identifier_visible"),
    ("V0R-MU-002", "REF-FREE-A", "REF-OWNER", "workspace_member_list", "collaborator_active"),
    ("V0R-MU-003", "REF-FREE-A", "REF-COLLABORATOR", "workspace_shell", "shared_workspace_visible"),
    ("V0R-MU-004", "REF-FREE-B", "REF-OWNER", "page_share_dialog", "edit_access_active"),
    ("V0R-MU-005", "REF-FREE-B", "REF-COLLABORATOR", "page_canvas", "page_edit_access_visible"),
    ("V0R-MU-006", "REF-FREE-A", "REF-COLLABORATOR", "page_share_dialog", "inherited_workspace_access_explained"),
    ("V0R-MU-007", "REF-FREE-A", "REF-OWNER", "page_header", "two_principal_presence_visible"),
    ("V0R-MU-008", "REF-FREE-A", "REF-OWNER", "editor", "remote_cursor_and_selection_visible"),
    ("V0R-MU-009", "REF-FREE-A", "REF-OWNER+REF-COLLABORATOR", "editor", "simultaneous_text_edit_converged"),
    ("V0R-MU-010", "REF-FREE-A", "REF-OWNER+REF-COLLABORATOR", "editor", "simultaneous_block_edit_converged"),
    ("V0R-MU-011", "REF-FREE-A", "REF-OWNER+REF-COLLABORATOR", "database", "simultaneous_database_edit_converged"),
    ("V0R-MU-012", "REF-FREE-A", "REF-OWNER+REF-COLLABORATOR", "comment_thread", "comment_reply_and_reaction_visible"),
    ("V0R-MU-013", "REF-FREE-A", "REF-COLLABORATOR", "inbox", "synthetic_mention_notification_visible"),
    ("V0R-MU-014", "REF-FREE-A", "REF-COLLABORATOR", "page_canvas", "reconnect_catch_up_complete"),
    ("V0R-MU-015", "REF-FREE-B", "REF-COLLABORATOR", "page_canvas", "comment_only_access_visible"),
    ("V0R-MU-016", "REF-FREE-B", "REF-COLLABORATOR", "page_canvas", "view_only_access_visible"),
    ("V0R-MU-017", "REF-FREE-B", "REF-COLLABORATOR", "access_boundary", "page_access_revoked_non_oracle"),
    ("V0R-MU-018", "REF-FREE-A", "REF-COLLABORATOR", "access_boundary", "workspace_access_revoked_non_oracle"),
)
EXPECTED_CAPTURE_FIELDS = (
    "state_id",
    "workspace_alias",
    "actor_alias",
    "viewport",
    "environment_sha256",
    "screenshot_sha256",
    "reference_build_identifier",
    "focus",
    "accessible_name_role_state",
    "interaction_outcome",
    "captured_at",
)
EXPECTED_RUN_ARTIFACTS = (
    "owner_approval_receipt",
    "reference_action_log",
    "capture_environment_receipts",
    "screenshot_hash_manifest",
    "reference_cleanup_receipt",
)
EXPECTED_STOP_CONDITIONS = (
    "owner_approval_receipt_missing_or_scope_mismatch",
    "workspace_alias_not_exactly_REF-FREE-A_or_REF-FREE-B",
    "personal_or_non_synthetic_content_visible_in_active_capture_path",
    "identity_is_not_one_of_the_two_dedicated_test_accounts",
    "browser_control_or_identity_certainty_lost",
    "capture_environment_drift",
    "raw_private_identifier_would_be_persisted",
    "public_publish_permanent_delete_or_non_test_message_path_reached",
)
_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _load(path: Path) -> Any:
    return json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=_pairs,
        parse_constant=lambda value: (_ for _ in ()).throw(
            ValueError(f"non-finite JSON number: {value}")
        ),
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _repository_file(relative: Any) -> Path | None:
    if not isinstance(relative, str) or not relative or "\\" in relative:
        return None
    candidate = (REPOSITORY_ROOT / relative).resolve(strict=False)
    try:
        candidate.relative_to(REPOSITORY_ROOT.resolve())
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


def _historical_source_transition_valid(
    relative: Any,
    bound_sha256: Any,
    current_sha256: str,
) -> bool:
    """Accept only the immutable V0R adopted-surface corrective lineage."""

    if relative != ADOPTED_SURFACE_REPO_PATH or not isinstance(bound_sha256, str):
        return False
    checkpoint_path = _repository_file(ADOPTED_SURFACE_TRANSITION_CHECKPOINT_REPO_PATH)
    if checkpoint_path is None:
        return False
    try:
        if _sha256_file(checkpoint_path) != ADOPTED_SURFACE_TRANSITION_CHECKPOINT_SHA256:
            return False
        checkpoint = _load(checkpoint_path)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        return False
    if not isinstance(checkpoint, dict):
        return False
    if (
        checkpoint.get("schema_version") != 1
        or checkpoint.get("checkpoint_id")
        != "UI-INTERNAL-COLLABORATION-V0R-P1-ADOPTED-SURFACE-LINEAGE-SUCCESSOR-V3-VERIFIED-20260907"
        or checkpoint.get("transition_path") != relative
        or checkpoint.get("historical_receipt_bound_sha256") != bound_sha256
        or checkpoint.get("current_sha256") != current_sha256
    ):
        return False
    predecessor = checkpoint.get("predecessor_transition_checkpoint")
    if (
        not isinstance(predecessor, dict)
        or predecessor.get("path")
        != "code_assist_v1/docs/notion_parity/checkpoints/ui-internal-collaboration-v0r-p1-adopted-surface-lineage-successor-v2-verified-20260907.json"
        or predecessor.get("sha256")
        != "sha256:39585b28e6747105b77cfba66b5749c4f2b08adb9575ce48c832bf3ac6a12ce6"
        or predecessor.get("pre_sha256") != bound_sha256
        or predecessor.get("post_sha256")
        != "sha256:8bc53f2cd7fb07d272c6a04db99a02f86f7995ccf9f21491e8d42fef7fee501d"
    ):
        return False
    predecessor_path = _repository_file(predecessor["path"])
    if (
        predecessor_path is None
        or _sha256_file(predecessor_path) != predecessor["sha256"]
    ):
        return False
    chain = checkpoint.get("transition_chain")
    if not isinstance(chain, list) or len(chain) != 3:
        return False
    cursor = bound_sha256
    for link in chain:
        if (
            not isinstance(link, dict)
            or link.get("pre_sha256") != cursor
            or not isinstance(link.get("post_sha256"), str)
            or _SHA256_RE.fullmatch(link["post_sha256"]) is None
        ):
            return False
        checkpoint_fields = [
            value
            for key, value in link.items()
            if key.endswith("checkpoint") and isinstance(value, dict)
        ]
        if not checkpoint_fields:
            return False
        for evidence_checkpoint in checkpoint_fields:
            evidence_path = _repository_file(evidence_checkpoint.get("path"))
            if (
                evidence_path is None
                or _sha256_file(evidence_path) != evidence_checkpoint.get("sha256")
            ):
                return False
        cursor = link["post_sha256"]
    if cursor != current_sha256:
        return False
    evidence = checkpoint.get("immutable_evidence")
    if not isinstance(evidence, dict) or evidence.get("modified") is not False:
        return False
    for prefix in ("v0r", "v0b_revocation"):
        evidence_path = _repository_file(evidence.get(f"{prefix}_receipt_path"))
        if (
            evidence_path is None
            or _sha256_file(evidence_path) != evidence.get(f"{prefix}_receipt_sha256")
        ):
            return False
    return True


def verify(path: Path = CONTRACT_PATH) -> dict[str, Any]:
    errors: list[dict[str, str]] = []

    def check(condition: bool, code: str, subject: str, message: str) -> None:
        if not condition:
            errors.append({"code": code, "subject": subject, "message": message})

    try:
        value = _load(path)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        return {
            "contract": "code_assist.notion.internal_collaborative_reference_multiuser_execution.audit.v1",
            "status": "fail",
            "error_count": 1,
            "errors": [
                {"code": "CONTRACT_READ_FAILED", "subject": str(path), "message": str(exc)}
            ],
        }
    check(isinstance(value, dict), "CONTRACT_SHAPE", "$", "contract must be an object")
    if not isinstance(value, dict):
        return {"status": "fail", "error_count": len(errors), "errors": errors}

    check(
        value.get("schema_version") == 1
        and value.get("contract") == EXPECTED_CONTRACT
        and value.get("packet_id") == "V0R-P1"
        and value.get("status") == "awaiting_owner_approval",
        "CONTRACT_IDENTITY",
        "$",
        "V0R-P1 pre-approval identity or status drift",
    )

    bindings = value.get("source_bindings")
    paths = tuple(
        item.get("path")
        for item in bindings
        if isinstance(item, dict) and isinstance(item.get("path"), str)
    ) if isinstance(bindings, list) else ()
    check(
        paths == EXPECTED_SOURCE_BINDINGS,
        "SOURCE_DENOMINATOR",
        "$.source_bindings",
        "source path set or order drift",
    )
    if isinstance(bindings, list):
        for index, item in enumerate(bindings):
            target = _repository_file(item.get("path")) if isinstance(item, dict) else None
            check(
                target is not None,
                "SOURCE_MISSING",
                f"$.source_bindings[{index}]",
                "bound source file missing or outside repository",
            )
            expected_hash = item.get("sha256") if isinstance(item, dict) else None
            check(
                isinstance(expected_hash, str) and _SHA256_RE.fullmatch(expected_hash) is not None,
                "SOURCE_HASH_FORMAT",
                f"$.source_bindings[{index}].sha256",
                "canonical sha256 required",
            )
            if target is not None:
                current_hash = _sha256_file(target)
                check(
                    current_hash == expected_hash
                    or _historical_source_transition_valid(
                        item.get("path"), expected_hash, current_hash
                    ),
                    "SOURCE_HASH_DRIFT",
                    str(item.get("path")),
                    "bound source hash drift",
                )

    approval = value.get("approval", {})
    check(
        isinstance(approval, dict)
        and approval.get("id") == "AP-REFERENCE-MULTIUSER"
        and approval.get("registry_status") == "pending"
        and approval.get("required") is True
        and approval.get("exact_requested_text") == EXACT_APPROVAL_TEXT
        and approval.get("owner_receipt") is None
        and approval.get("external_mutation_authorized") is False
        and approval.get("approval_is_test_evidence") is False,
        "APPROVAL_BOUNDARY",
        "$.approval",
        "pending exact approval with zero execution authority is required",
    )

    scope = value.get("scope", {})
    check(
        isinstance(scope, dict)
        and scope.get("workspace_aliases") == ["REF-FREE-A", "REF-FREE-B"]
        and scope.get("identity_aliases") == ["REF-OWNER", "REF-COLLABORATOR"]
        and scope.get("workspace_assignment") == {
            "REF-FREE-A": "workspace_member_invite_edit_and_revoke",
            "REF-FREE-B": "page_share_role_transition_and_revoke",
        }
        and scope.get("synthetic_only") is True
        and scope.get("existing_page_reuse_allowed") is False,
        "SCOPE_DENOMINATOR",
        "$.scope",
        "exact two-workspace, two-identity synthetic scope required",
    )
    identity = scope.get("identity_requirements", {}) if isinstance(scope, dict) else {}
    check(
        isinstance(identity, dict)
        and identity.get("independent_authenticated_browser_sessions") == 2
        and identity.get("dedicated_test_accounts_only") is True
        and identity.get("raw_email_or_account_identifier_persisted") is False
        and identity.get("credentials_cookie_token_or_storage_persisted") is False,
        "IDENTITY_BOUNDARY",
        "$.scope.identity_requirements",
        "two independent redacted test identities required",
    )
    forbidden_scope_fields = (
        "personal_workspace_access_allowed",
        "personal_page_access_allowed",
        "non_test_identity_invite_allowed",
        "public_publish_or_public_link_allowed",
        "message_or_notification_outside_test_identities_allowed",
        "permanent_delete_allowed",
        "production_or_second_brain_mutation_allowed",
    )
    check(
        isinstance(scope, dict)
        and all(scope.get(field) is False for field in forbidden_scope_fields),
        "FORBIDDEN_SCOPE",
        "$.scope",
        "personal, non-test, public, permanent-delete, production, and Second Brain scope must remain forbidden",
    )

    environment = value.get("capture_environment", {})
    check(
        isinstance(environment, dict)
        and environment.get("viewports") == [
            {"width": 1440, "height": 900},
            {"width": 1280, "height": 800},
        ]
        and environment.get("device_scale_factor") == 1
        and environment.get("zoom") == 1
        and environment.get("locale") == "ko-KR"
        and environment.get("timezone_id") == "Asia/Seoul"
        and environment.get("color_scheme") == "light"
        and environment.get("reduced_motion") == "reduce"
        and all(
            environment.get(field) is True
            for field in (
                "animations_disabled",
                "caret_hidden_for_capture",
                "font_manifest_required",
                "chrome_major_version_required",
                "reference_build_identifier_required",
                "one_environment_receipt_per_screenshot",
            )
        ),
        "CAPTURE_ENVIRONMENT",
        "$.capture_environment",
        "frozen adopted desktop capture environment required",
    )

    fixtures = value.get("synthetic_fixtures")
    check(
        isinstance(fixtures, list)
        and [(item.get("alias"), item.get("workspace"), item.get("owner")) for item in fixtures if isinstance(item, dict)]
        == [
            ("V0R-A-ROOT", "REF-FREE-A", "REF-OWNER"),
            ("V0R-B-ROOT", "REF-FREE-B", "REF-OWNER"),
        ]
        and all("trash" in str(item.get("cleanup", "")) for item in fixtures if isinstance(item, dict)),
        "FIXTURE_BOUNDARY",
        "$.synthetic_fixtures",
        "exact disposable fixture roots and Trash cleanup required",
    )

    actions = value.get("operator_actions")
    actual_actions = tuple(
        (item.get("id"), item.get("workspace"), item.get("actor"), item.get("mutation_class"))
        for item in actions
        if isinstance(item, dict)
    ) if isinstance(actions, list) else ()
    check(
        actual_actions == EXPECTED_ACTIONS
        and isinstance(actions, list)
        and [item.get("order") for item in actions if isinstance(item, dict)]
        == list(range(1, len(EXPECTED_ACTIONS) + 1))
        and all(isinstance(item.get("required_result"), str) and item.get("required_result") for item in actions if isinstance(item, dict)),
        "ACTION_DENOMINATOR",
        "$.operator_actions",
        "ordered invite, capture, role, revoke, and cleanup actions drift",
    )

    states = value.get("capture_states")
    actual_states = tuple(
        (
            item.get("id"),
            item.get("workspace"),
            item.get("actor"),
            item.get("surface"),
            item.get("state"),
        )
        for item in states
        if isinstance(item, dict)
    ) if isinstance(states, list) else ()
    check(
        actual_states == EXPECTED_STATES,
        "CAPTURE_STATE_DENOMINATOR",
        "$.capture_states",
        "exact 18-state multi-user reference denominator required",
    )

    capture = value.get("capture_contract", {})
    check(
        isinstance(capture, dict)
        and capture.get("capture_state_count") == len(EXPECTED_STATES)
        and capture.get("viewport_count") == 2
        and capture.get("expected_screenshot_count") == len(EXPECTED_STATES) * 2
        and capture.get("one_png_per_state_and_viewport") is True
        and capture.get("raw_dom_network_cookie_token_or_storage_capture_allowed") is False
        and capture.get("raw_email_or_non_alias_label_persistence_allowed") is False
        and tuple(capture.get("required_per_capture_fields", ())) == EXPECTED_CAPTURE_FIELDS
        and tuple(capture.get("required_run_artifacts", ())) == EXPECTED_RUN_ARTIFACTS
        and capture.get("screenshot_binary_directory")
        == "code_assist_v1/docs/notion_parity/evidence/internal_collaboration_reference/v0r-p1/screenshots"
        and capture.get("receipt_path")
        == "code_assist_v1/docs/notion_parity/evidence/internal_collaboration_receipts/v0r-p1-l6-reference-multiuser-20260825.json",
        "CAPTURE_CONTRACT",
        "$.capture_contract",
        "36 screenshot and receipt denominator drift",
    )

    cleanup = value.get("cleanup_contract", {})
    check(
        isinstance(cleanup, dict)
        and cleanup.get("runs_after_success_or_failure") is True
        and cleanup.get("order") == [
            "revoke_V0R-B-ROOT_page_grant",
            "revoke_REF-COLLABORATOR_from_REF-FREE-A",
            "move_V0R-A-ROOT_to_trash",
            "move_V0R-B-ROOT_to_trash",
            "confirm_zero_active_test_grants_and_zero_active_fixture_roots",
        ]
        and cleanup.get("mode") == "trash_only_recoverable"
        and cleanup.get("permanent_delete_allowed") is False
        and cleanup.get("cleanup_receipt_required") is True
        and cleanup.get("cleanup_failure_blocks_packet") is True,
        "CLEANUP_CONTRACT",
        "$.cleanup_contract",
        "revoke-before-Trash recoverable cleanup contract required",
    )
    check(
        tuple(value.get("stop_conditions", ())) == EXPECTED_STOP_CONDITIONS,
        "STOP_CONDITIONS",
        "$.stop_conditions",
        "fail-closed stop conditions drift",
    )

    claims = value.get("claims", {})
    false_claims = (
        "owner_approval_received",
        "external_reference_mutation_executed",
        "reference_screenshots_captured",
        "cleanup_executed",
        "V0R_P1_gate_passed",
        "PIX_001_closed",
        "A11Y_001_closed",
        "UI_CORE_25_closed",
        "production_changed",
        "second_brain_changed",
        "product_complete",
        "active_shared_goal_complete",
    )
    check(
        isinstance(claims, dict)
        and claims.get("execution_contract_prepared") is True
        and all(claims.get(field) is False for field in false_claims),
        "CLAIMS_FALSE_GREEN",
        "$.claims",
        "pre-approval contract may claim preparation only",
    )

    normalized = sorted(
        {
            (item["code"], item["subject"], item["message"]): item
            for item in errors
        }.values(),
        key=lambda item: (item["code"], item["subject"], item["message"]),
    )
    return {
        "contract": "code_assist.notion.internal_collaborative_reference_multiuser_execution.audit.v1",
        "status": "pass" if not normalized else "fail",
        "error_count": len(normalized),
        "errors": normalized,
        "manifest_sha256": _sha256_file(path),
        "action_count": len(EXPECTED_ACTIONS),
        "capture_state_count": len(EXPECTED_STATES),
        "expected_screenshot_count": len(EXPECTED_STATES) * 2,
        "ready_for_owner_approval": not normalized,
        "execution_authorized": False,
        "external_mutation_executed": False,
        "product_complete": False,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=CONTRACT_PATH)
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args(argv)
    report = verify(args.manifest)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=None if args.compact else 2))
    return 0 if report.get("status") == "pass" else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = [
    "CONTRACT_PATH",
    "EXACT_APPROVAL_TEXT",
    "EXPECTED_ACTIONS",
    "EXPECTED_STATES",
    "verify",
]
