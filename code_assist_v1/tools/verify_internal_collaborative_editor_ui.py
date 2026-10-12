"""Verify the E1-P2 host-bound collaborative editor UI contract offline."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Sequence

from code_assist_v1.tools.database_feature_successor import owns_current_artifact as f0_owns_current_artifact


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = PROJECT_ROOT.parent
CONTRACT_PATH = (
    PROJECT_ROOT
    / "docs"
    / "notion_parity"
    / "core_free_web_ui"
    / "internal_collaborative_editor_ui.v1.json"
)
SUCCESSOR_CONTRACT_PATH = (
    PROJECT_ROOT
    / "docs"
    / "notion_parity"
    / "core_free_web_ui"
    / "internal_collaborative_convergence_corpus.v1.json"
)
SECOND_BRAIN_SUCCESSOR_CONTRACT_PATH = (
    PROJECT_ROOT
    / "docs"
    / "notion_parity"
    / "core_free_web_ui"
    / "internal_collaborative_second_brain_publication.v1.json"
)
EXPECTED_CONTRACT = "code_assist.notion.internal_collaborative_editor_ui.e1_p2.v1"
EXPECTED_PREREQUISITES = {"E1-P1", "UI0-P1"}
EXPECTED_SOURCE_PATHS = (
    "code_assist_v1/notion_static/notion.js",
    "code_assist_v1/notion_static/notion_collaboration_editor.js",
    "code_assist_v1/notion_static/notion_editor_core.js",
    "code_assist_v1/notion_static/notion.css",
    "code_assist_v1/notion_static/index.html",
    "code_assist_v1/notion_static/vendor/yjs-13.6.32.bundle.js",
    "code_assist_v1/notion_static/vendor/yjs-lib0.LICENSE.txt",
    "code_assist_v1/tools/build_browser_yjs_bundle.py",
    "code_assist_v1/tools/internal_collaborative_validation.py",
    "code_assist_v1/collaboration_runtime/node/package.json",
    "code_assist_v1/collaboration_runtime/node/package-lock.json",
    "code_assist_v1/notion_collaboration/e1_p2_browser.py",
    "code_assist_v1/notion_collaboration/canonical_authority.py",
    "code_assist_v1/notion_collaboration/editing_authority.py",
    "code_assist_v1/notion_collaboration/live_authority.py",
)
EXPECTED_TEST_PATHS = (
    "code_assist_v1/tests/test_notion_collaboration_editor.js",
    "code_assist_v1/tests/test_notion_editor_core.js",
    "code_assist_v1/tests/test_notion_ui_runtime.js",
    "code_assist_v1/tests/test_notion_atomic_save.js",
    "code_assist_v1/tests/test_notion_navigation_ui.js",
    "code_assist_v1/tests/test_notion_publication_ui.js",
    "code_assist_v1/tests/notion_collaboration_test_runtime.js",
    "code_assist_v1/tests/notion_collaboration/browser/test_e1_p2_collaborative_editor.py",
    "code_assist_v1/tests/notion_collaboration/test_collaborative_editing_authority.py",
    "code_assist_v1/tests/notion_collaboration/test_live_collaboration_authority.py",
    "code_assist_v1/tests/notion_collaboration/test_internal_collaborative_editor_ui.py",
)
SUCCESSOR_OWNED_PATHS = {
    "code_assist_v1/tools/internal_collaborative_validation.py",
    "code_assist_v1/tests/notion_collaboration/test_internal_collaborative_editor_ui.py",
}
SECOND_BRAIN_SUCCESSOR_OWNED_PATHS = {
    "code_assist_v1/notion_collaboration/editing_authority.py",
    "code_assist_v1/tools/internal_collaborative_validation.py",
}


def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in items:
        if key in value:
            raise ValueError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _load(path: Path) -> Any:
    return json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=_pairs,
        parse_constant=lambda value: (_ for _ in ()).throw(
            ValueError(f"non-finite JSON number: {value}")
        ),
    )


def _sha256_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _repository_file(relative: Any) -> Path | None:
    if not isinstance(relative, str) or not relative or "\\" in relative:
        return None
    candidate = (REPOSITORY_ROOT / relative).resolve(strict=False)
    try:
        candidate.relative_to(REPOSITORY_ROOT.resolve())
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


def _successor_owns_current_artifact(relative: str, target: Path) -> bool:
    if f0_owns_current_artifact(relative, target):
        return True
    contracts = (
        (SUCCESSOR_CONTRACT_PATH, SUCCESSOR_OWNED_PATHS, "E1-P3", "E1_P3_gate_passed"),
        (
            SECOND_BRAIN_SUCCESSOR_CONTRACT_PATH,
            SECOND_BRAIN_SUCCESSOR_OWNED_PATHS,
            "S0A-P2",
            "S0A_P2_gate_passed",
        ),
    )
    for contract_path, owned_paths, packet_id, gate_claim in contracts:
        if relative not in owned_paths:
            continue
        try:
            successor = _load(contract_path)
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
            continue
        if not isinstance(successor, dict):
            continue
        rows = [
            *successor.get("source_manifest", []),
            *successor.get("test_manifest", []),
        ]
        binding = next(
            (
                item
                for item in rows
                if isinstance(item, dict) and item.get("path") == relative
            ),
            None,
        )
        if (
            successor.get("packet_id") == packet_id
            and successor.get("status") == "implemented_and_validated"
            and successor.get("claims", {}).get(gate_claim) is True
            and isinstance(binding, dict)
            and binding.get("sha256") == _sha256_file(target)
        ):
            return True
    return False


def verify(path: Path = CONTRACT_PATH) -> dict[str, Any]:
    errors: list[dict[str, str]] = []

    def check(condition: bool, code: str, subject: str, message: str) -> None:
        if not condition:
            errors.append({"code": code, "subject": subject, "message": message})

    try:
        value = _load(path)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        return {
            "contract": "code_assist.notion.internal_collaborative_editor_ui.audit.v1",
            "status": "fail",
            "error_count": 1,
            "errors": [
                {"code": "CONTRACT_READ_FAILED", "subject": str(path), "message": str(exc)}
            ],
            "gate_passed": False,
            "product_complete": False,
        }
    check(isinstance(value, dict), "CONTRACT_SHAPE", "$", "contract must be an object")
    if not isinstance(value, dict):
        return {"status": "fail", "error_count": len(errors), "errors": errors}
    check(
        value.get("schema_version") == 1
        and value.get("contract") == EXPECTED_CONTRACT
        and value.get("packet_id") == "E1-P2"
        and value.get("status") == "implemented_and_validated",
        "CONTRACT_IDENTITY",
        "$",
        "E1-P2 contract identity or status drift",
    )

    prerequisites = value.get("prerequisite_evidence", {})
    check(
        isinstance(prerequisites, dict) and set(prerequisites) == EXPECTED_PREREQUISITES,
        "PREREQUISITE_DENOMINATOR",
        "$.prerequisite_evidence",
        "exact E1-P2 prerequisite set required",
    )
    if isinstance(prerequisites, dict):
        for identifier, item in prerequisites.items():
            relative = item.get("path") if isinstance(item, dict) else None
            target = (CONTRACT_PATH.parent / str(relative or "")).resolve(strict=False)
            try:
                target.relative_to(REPOSITORY_ROOT.resolve())
            except ValueError:
                target = None
            else:
                if not target.is_file():
                    target = None
            check(target is not None, "PREREQUISITE_MISSING", identifier, "checkpoint missing")
            if target is not None:
                check(
                    _sha256_file(target) == item.get("sha256"),
                    "PREREQUISITE_HASH_DRIFT",
                    identifier,
                    "checkpoint hash drift",
                )
            check(
                isinstance(item, dict) and item.get("status") == "gate_passed",
                "PREREQUISITE_STATUS",
                identifier,
                "gate-passed prerequisite required",
            )

    runtime = value.get("runtime_contract", {})
    check(
        isinstance(runtime, dict)
        and runtime.get("selected_runtime_candidate") == "PY_OPAQUE_NODE_COMPACTOR"
        and runtime.get("browser_crdt") == "Yjs"
        and runtime.get("yjs_version") == "13.6.32"
        and runtime.get("lib0_version") == "0.2.117"
        and runtime.get("yjs_wire_version") == 1
        and runtime.get("browser_bundle_contract")
        == "code_assist.notion.browser_yjs_bundle.v1"
        and runtime.get("development_ports") == [10111, 10112, 10113]
        and runtime.get("approval_id") == "AP-DEV-PAIR"
        and runtime.get("approved_principals") == ["admin", "user01"]
        and runtime.get("persistent_personal_browser_profile_used") is False
        and runtime.get("production_ports_changed") is False
        and runtime.get("production_mutation_allowed") is False,
        "RUNTIME_BOUNDARY",
        "$.runtime_contract",
        "pinned runtime or development/production boundary drift",
    )

    host = value.get("host_adapter_contract", {})
    check(
        isinstance(host, dict)
        and all(
            host.get(field) is True
            for field in (
                "editor_core_host_adapter_is_the_dom_boundary",
                "crdt_document_lifetime_is_bound_to_active_page",
                "canonical_baseline_seeds_an_empty_crdt_deterministically_once",
                "reconnect_never_requeues_the_deterministic_seed",
                "local_host_mutations_emit_yjs_updates",
                "remote_and_replayed_updates_project_through_the_host_adapter",
                "block_structure_diffs_emit_typed_server_commands",
                "python_does_not_decode_or_transform_yjs_payloads",
                "server_durable_ack_precedes_peer_broadcast",
                "new_post_2405_documents_receive_generation_and_checkpoint_atomically",
                "live_domain_events_replay_through_the_transport_canonical_adapter",
            )
        ),
        "HOST_ADAPTER_BOUNDARY",
        "$.host_adapter_contract",
        "host adapter, seed, durable ACK, or canonical integration drift",
    )

    interaction = value.get("interaction_contract", {})
    check(
        isinstance(interaction, dict)
        and all(
            interaction.get(field) is True
            for field in (
                "native_korean_ime_composition_is_not_replaced_mid_composition",
                "deferred_remote_projection_applies_after_compositionend",
                "selection_is_captured_and_restored_across_remote_projection",
                "focus_is_preserved_by_the_host_adapter",
                "undo_redo_tracks_only_local_origin",
                "same_block_concurrent_korean_edits_converge",
                "remote_presence_and_cursor_are_ephemeral",
                "remote_cursor_is_rendered_for_the_other_principal",
            )
        ),
        "INTERACTION_BOUNDARY",
        "$.interaction_contract",
        "IME, selection, undo, convergence, or cursor behavior drift",
    )

    offline = value.get("offline_recovery_contract", {})
    check(
        isinstance(offline, dict)
        and offline.get("contract") == "code_assist.notion.encrypted_browser_outbox.v1"
        and offline.get("storage") == "IndexedDB"
        and offline.get("encryption") == "AES-GCM-256"
        and offline.get("per_subject_crypto_key_extractable") is False
        and offline.get("plaintext_identity_workspace_page_or_content_persisted") is False
        and all(
            offline.get(field) is True
            for field in (
                "authenticated_scope_additional_data",
                "disconnected_edits_buffer_only_when_encrypted_outbox_is_available",
                "reconnect_replays_durable_server_frames_then_pending_local_updates",
                "acknowledged_outbox_entries_are_deleted",
                "authentication_or_storage_failure_enters_recovery_only_mode",
                "recovery_only_mode_disables_further_editing",
                "local_recovery_export_is_available",
                "logout_clears_subject_outbox_entries",
            )
        ),
        "OFFLINE_RECOVERY_BOUNDARY",
        "$.offline_recovery_contract",
        "encrypted outbox or recovery-only policy drift",
    )

    validation = value.get("validation_contract", {})
    check(
        isinstance(validation, dict)
        and validation.get("node_ui_lanes_passed") == 6
        and validation.get("python_authority_live_browser_tests_passed") == 14
        and validation.get("independent_ephemeral_browser_contexts") == 2
        and validation.get("principal_count") == 2
        and validation.get("encrypted_outbox_record_count_observed") >= 1
        and validation.get("non_extractable_key_count_observed") >= 1
        and validation.get("plaintext_scope_or_content_leak_count") == 0
        and validation.get("browser_page_error_count") == 0
        and validation.get("final_projection_sha256")
        == "sha256:28b88e3d4840d0666f19a536047c32574ffa918688cfffeca929db35e15ad97f"
        and validation.get("production_10011_10012_changed") is False
        and all(
            validation.get(field) is True
            for field in (
                "same_workspace_convergence",
                "close_reopen_recovery",
                "remote_cursor_visible",
                "test_page_moved_to_trash",
                "post_2405_document_first_crdt_update_acknowledged",
                "transport_adapter_live_replay_verified",
            )
        ),
        "VALIDATION_DENOMINATOR",
        "$.validation_contract",
        "E1-P2 validation denominator drift",
    )

    for key, expected_paths in (
        ("source_manifest", EXPECTED_SOURCE_PATHS),
        ("test_manifest", EXPECTED_TEST_PATHS),
    ):
        manifest = value.get(key)
        paths = (
            tuple(
                item.get("path")
                for item in manifest
                if isinstance(item, dict) and isinstance(item.get("path"), str)
            )
            if isinstance(manifest, list)
            else ()
        )
        check(paths == expected_paths, "MANIFEST_DENOMINATOR", f"$.{key}", "path set or order drift")
        if isinstance(manifest, list):
            for index, item in enumerate(manifest):
                target = _repository_file(item.get("path")) if isinstance(item, dict) else None
                check(target is not None, "MANIFEST_FILE_MISSING", f"$.{key}[{index}]", "file missing")
                if target is not None:
                    check(
                        _sha256_file(target) == item.get("sha256")
                        or _successor_owns_current_artifact(str(item.get("path")), target),
                        "MANIFEST_HASH_DRIFT",
                        str(item.get("path")),
                        "bound file hash drift",
                    )

    try:
        package_lock = _load(PROJECT_ROOT / "collaboration_runtime" / "node" / "package-lock.json")
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        package_lock = {}
    packages = package_lock.get("packages", {}) if isinstance(package_lock, dict) else {}
    check(
        packages.get("node_modules/yjs", {}).get("version") == "13.6.32"
        and packages.get("node_modules/lib0", {}).get("version") == "0.2.117",
        "PINNED_BROWSER_CRDT_DRIFT",
        "collaboration_runtime/node/package-lock.json",
        "Yjs/lib0 versions are not exact",
    )
    bundle_source = (PROJECT_ROOT / "notion_static" / "vendor" / "yjs-13.6.32.bundle.js").read_text(
        encoding="utf-8"
    )
    check(
        '"contract":"code_assist.notion.browser_yjs_bundle.v1"' in bundle_source
        and '"yjs_version":"13.6.32"' in bundle_source
        and '"lib0_version":"0.2.117"' in bundle_source,
        "BROWSER_BUNDLE_METADATA_DRIFT",
        "notion_static/vendor/yjs-13.6.32.bundle.js",
        "browser bundle metadata is not exact",
    )
    browser_harness = (PROJECT_ROOT / "notion_collaboration" / "e1_p2_browser.py").read_text(
        encoding="utf-8"
    )
    check(
        "crypto.subtle.decrypt" not in browser_harness,
        "BROWSER_HARNESS_PLAINTEXT_DIAGNOSTIC",
        "notion_collaboration/e1_p2_browser.py",
        "browser evidence harness must not decrypt outbox content for diagnostics",
    )

    claims = value.get("claims", {})
    check(
        isinstance(claims, dict)
        and all(
            claims.get(field) is True
            for field in (
                "E1_P2_gate_passed",
                "EDIT_001_contributed",
                "EDIT_002_contributed",
                "EDIT_003_contributed",
                "A11Y_001_contributed",
                "PIX_001_contributed",
            )
        )
        and all(
            claims.get(field) is False
            for field in (
                "EDIT_001_closed",
                "EDIT_002_closed",
                "EDIT_003_closed",
                "A11Y_001_closed",
                "PIX_001_closed",
                "E1_phase_gate_closed",
                "production_changed",
                "product_complete",
                "active_shared_goal_complete",
            )
        ),
        "CLAIMS_FALSE_GREEN",
        "$.claims",
        "E1-P2 contribution or non-completion claims drift",
    )

    normalized = sorted(
        {(item["code"], item["subject"], item["message"]): item for item in errors}.values(),
        key=lambda item: (item["code"], item["subject"], item["message"]),
    )
    return {
        "contract": "code_assist.notion.internal_collaborative_editor_ui.audit.v1",
        "status": "pass" if not normalized else "fail",
        "error_count": len(normalized),
        "errors": normalized,
        "contract_sha256": _sha256_file(path),
        "source_count": len(EXPECTED_SOURCE_PATHS),
        "test_count": len(EXPECTED_TEST_PATHS),
        "node_ui_lane_count": validation.get("node_ui_lanes_passed") if isinstance(validation, dict) else None,
        "python_test_count": validation.get("python_authority_live_browser_tests_passed")
        if isinstance(validation, dict)
        else None,
        "gate_passed": not normalized,
        "product_complete": False,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, default=CONTRACT_PATH)
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args(argv)
    report = verify(args.contract)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=None if args.compact else 2))
    return 0 if report.get("status") == "pass" else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
