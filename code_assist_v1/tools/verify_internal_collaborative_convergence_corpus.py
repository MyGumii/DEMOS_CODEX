"""Verify the fail-closed E1-P3 convergence corpus contract offline."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path
from typing import Any, Sequence

from code_assist_v1.tools.database_feature_successor import owns_current_artifact as f0_owns_current_artifact

from code_assist_v1.tools.verify_internal_collaborative_native_ime_receipt import (
    RECEIPT_PATH as NATIVE_IME_RECEIPT_PATH,
    verify as verify_native_ime_receipt,
)
from code_assist_v1.tools.verify_internal_collaborative_second_brain_boundary import (
    verify as verify_second_brain_boundary,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = PROJECT_ROOT.parent
CONTRACT_PATH = (
    PROJECT_ROOT
    / "docs"
    / "notion_parity"
    / "core_free_web_ui"
    / "internal_collaborative_convergence_corpus.v1.json"
)
SUCCESSOR_CONTRACT_PATH = (
    PROJECT_ROOT
    / "docs"
    / "notion_parity"
    / "core_free_web_ui"
    / "internal_collaborative_second_brain_boundary.v1.json"
)
PUBLICATION_SUCCESSOR_CONTRACT_PATH = (
    PROJECT_ROOT
    / "docs"
    / "notion_parity"
    / "core_free_web_ui"
    / "internal_collaborative_second_brain_publication.v1.json"
)
EXPECTED_CONTRACT = "code_assist.notion.internal_collaborative_convergence_corpus.e1_p3.v1"
EXPECTED_PREREQUISITES = {"E1-P2", "H0A-P2"}
EXPECTED_CORPUS_SHA256 = (
    "sha256:0ee53367d7b106a3b3223afbbd59ff074bd4567dc0e5a3fa12cdce3c3249eb8a"
)
EXPECTED_SCENARIO_HASHES = {
    "SAME-BLOCK-KOREAN": "sha256:bdf4445c36fa5aa108a23ae9b08a494bea5ea5ad0e5eb035279af5aaf16f175d",
    "DIFFERENT-BLOCKS": "sha256:2e2f7c24f441198f641a36495771165e612c7cae2f083a5daec6ba14a430dcf4",
    "MOVE-VS-EDIT": "sha256:bbc9f3174eee11687ec4bef643c8a85f800b650ab200d842f29a5d14e6a9a142",
    "CONCURRENT-BLOCK-INSERT": "sha256:98af80c6519c2ff5ac5753d99649502a53443de137280fa3e0583efd0b79e63d",
}
EXPECTED_SOURCE_PATHS = (
    "code_assist_v1/notion_static/notion_collaboration_editor.js",
    "code_assist_v1/notion_static/notion_editor_core.js",
    "code_assist_v1/notion_static/vendor/yjs-13.6.32.bundle.js",
    "code_assist_v1/notion_collaboration/canonical_authority.py",
    "code_assist_v1/notion_collaboration/editing_authority.py",
    "code_assist_v1/notion_collaboration/durable_transport.py",
    "code_assist_v1/notion_collaboration/cross_process_gateway.py",
    "code_assist_v1/notion_collaboration/node_compactor.py",
    "code_assist_v1/notion_collaboration/e1_p2_browser.py",
    "code_assist_v1/notion_collaboration/e1_p3_manual_ime.py",
    "code_assist_v1/tools/verify_internal_collaborative_native_ime_receipt.py",
    "code_assist_v1/tools/prepare_internal_collaborative_e1_p3_seal.py",
    "code_assist_v1/tools/internal_collaborative_validation.py",
    "code_assist_v1/tools/verify_internal_collaborative_editor_ui.py",
)
EXPECTED_TEST_PATHS = (
    "code_assist_v1/tests/notion_collaboration/e1_p3_host_editor_corpus.js",
    "code_assist_v1/tests/notion_collaboration/test_e1_p3_convergence_corpus.py",
    "code_assist_v1/tests/notion_collaboration/test_e1_p3_manual_ime.py",
    "code_assist_v1/tests/notion_collaboration/test_runtime_gateway_cross_process.py",
    "code_assist_v1/tests/notion_collaboration/browser/test_e1_p2_collaborative_editor.py",
    "code_assist_v1/tests/notion_collaboration/test_internal_collaborative_convergence_corpus.py",
    "code_assist_v1/tests/notion_collaboration/test_internal_collaborative_native_ime_receipt.py",
    "code_assist_v1/tests/notion_collaboration/test_prepare_internal_collaborative_e1_p3_seal.py",
    "code_assist_v1/tests/notion_collaboration/test_internal_collaborative_harness.py",
    "code_assist_v1/tests/notion_collaboration/test_internal_collaborative_editor_ui.py",
)
SUCCESSOR_OWNED_PATHS = {
    "code_assist_v1/tests/notion_collaboration/test_internal_collaborative_harness.py",
}
PUBLICATION_SUCCESSOR_OWNED_PATHS = {
    "code_assist_v1/notion_collaboration/editing_authority.py",
    "code_assist_v1/tools/internal_collaborative_validation.py",
    "code_assist_v1/tools/verify_internal_collaborative_editor_ui.py",
    "code_assist_v1/tests/notion_collaboration/test_internal_collaborative_harness.py",
}
EXPECTED_MANUAL_REQUEST_PATH = (
    "code_assist_v1/docs/notion_parity/evidence/internal_collaboration_operator/"
    "e1-p3-native-ime-manual-request-20260825.json"
)
EXPECTED_NATIVE_IME_RECEIPT_PATH = (
    "code_assist_v1/docs/notion_parity/evidence/internal_collaboration_operator/"
    "e1-p3-native-ime-finalization-20260825.json"
)
PENDING_STATUS = "automated_validated_native_ime_pending"
COMPLETED_STATUS = "implemented_and_validated"


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
        (
            SUCCESSOR_CONTRACT_PATH,
            SUCCESSOR_OWNED_PATHS,
            "S0A-P1",
            "S0A_P1_gate_passed",
            True,
        ),
        (
            PUBLICATION_SUCCESSOR_CONTRACT_PATH,
            PUBLICATION_SUCCESSOR_OWNED_PATHS,
            "S0A-P2",
            "S0A_P2_gate_passed",
            False,
        ),
    )
    for contract_path, owned_paths, packet_id, gate_claim, verify_boundary in contracts:
        if relative not in owned_paths:
            continue
        if verify_boundary:
            report = verify_second_brain_boundary(contract_path)
            if report.get("status") != "pass" or report.get("gate_passed") is not True:
                continue
        try:
            successor = _load(contract_path)
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
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


def _automated_input_calls(source: str) -> list[str]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return ["syntax_error"]
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr in {"fill", "insert_text", "press", "type"}:
            found.append(node.func.attr)
    return sorted(found)


def verify(path: Path = CONTRACT_PATH) -> dict[str, Any]:
    errors: list[dict[str, str]] = []

    def check(condition: bool, code: str, subject: str, message: str) -> None:
        if not condition:
            errors.append({"code": code, "subject": subject, "message": message})

    try:
        value = _load(path)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        return {
            "contract": "code_assist.notion.internal_collaborative_convergence_corpus.audit.v1",
            "status": "fail",
            "error_count": 1,
            "errors": [
                {"code": "CONTRACT_READ_FAILED", "subject": str(path), "message": str(exc)}
            ],
            "automated_validation_passed": False,
            "gate_passed": False,
            "native_ime_pending": True,
            "product_complete": False,
        }
    check(isinstance(value, dict), "CONTRACT_SHAPE", "$", "contract must be an object")
    if not isinstance(value, dict):
        return {"status": "fail", "error_count": len(errors), "errors": errors}
    contract_status = value.get("status")
    completed = contract_status == COMPLETED_STATUS
    pending = contract_status == PENDING_STATUS
    check(
        value.get("schema_version") == 1
        and value.get("contract") == EXPECTED_CONTRACT
        and value.get("packet_id") == "E1-P3"
        and (pending or completed),
        "CONTRACT_IDENTITY",
        "$",
        "E1-P3 contract identity or lifecycle status drift",
    )

    prerequisites = value.get("prerequisite_evidence", {})
    check(
        isinstance(prerequisites, dict) and set(prerequisites) == EXPECTED_PREREQUISITES,
        "PREREQUISITE_DENOMINATOR",
        "$.prerequisite_evidence",
        "exact E1-P3 prerequisite set required",
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
        and runtime.get("browser_crdt") == "Yjs"
        and runtime.get("yjs_version") == "13.6.32"
        and runtime.get("lib0_version") == "0.2.117"
        and runtime.get("selected_runtime_candidate") == "PY_OPAQUE_NODE_COMPACTOR"
        and runtime.get("development_ports") == [10111, 10112, 10113]
        and runtime.get("approval_id") == "AP-DEV-PAIR"
        and runtime.get("approved_principals") == ["admin", "user01"]
        and runtime.get("production_10011_10012_changed") is False
        and runtime.get("production_mutation_allowed") is False,
        "RUNTIME_BOUNDARY",
        "$.runtime_contract",
        "pinned runtime or approved dev boundary drift",
    )

    corpus = value.get("convergence_corpus", {})
    scenario_items = corpus.get("scenarios", []) if isinstance(corpus, dict) else []
    scenario_hashes = {
        item.get("id"): item.get("canonical_projection_sha256")
        for item in scenario_items
        if isinstance(item, dict)
    }
    check(
        isinstance(corpus, dict)
        and corpus.get("receipt_contract")
        == "code_assist.notion.e1_p3_host_editor_corpus_receipt.v1"
        and corpus.get("scenario_count") == 4
        and corpus.get("schedule_count_per_scenario") == 4
        and corpus.get("deterministic_process_repeat_count") == 3
        and corpus.get("canonical_corpus_sha256") == EXPECTED_CORPUS_SHA256
        and corpus.get("acknowledged_character_loss_count") == 0
        and corpus.get("duplicate_acknowledged_fragment_count") == 0
        and corpus.get("convergence_mismatch_count") == 0
        and corpus.get("different_block_edits_survive") is True
        and corpus.get("same_block_character_loss_zero") is True
        and corpus.get("local_undo_preserves_remote") is True
        and corpus.get("typed_structure_commands_observed") is True
        and scenario_hashes == EXPECTED_SCENARIO_HASHES,
        "CORPUS_DENOMINATOR",
        "$.convergence_corpus",
        "deterministic zero-loss corpus denominator drift",
    )

    transport = value.get("canonical_transport_and_compaction", {})
    check(
        isinstance(transport, dict)
        and all(
            transport.get(field) is True
            for field in (
                "actual_product_yjs_updates_accepted",
                "exact_duplicate_update_idempotent",
                "actual_node_compactor_used",
                "server_snapshot_projects_through_actual_client_bridge",
                "server_and_clients_share_canonical_projection_hash",
                "both_acknowledged_korean_fragments_survive",
                "close_reopen_update_and_snapshot_counts_match",
            )
        )
        and transport.get("acknowledged_character_loss_count") == 0,
        "TRANSPORT_COMPACTION_DENOMINATOR",
        "$.canonical_transport_and_compaction",
        "canonical transport, compaction, or reopen proof drift",
    )

    conflicts = value.get("structure_database_conflict_contract", {})
    check(
        isinstance(conflicts, dict)
        and all(
            conflicts.get(field) is True
            for field in (
                "delete_tombstone_binds_crdt_generation_and_update_sequence",
                "stale_move_rejected",
                "database_cell_first_writer_commits",
                "stale_document_sequence_rejected",
                "stale_target_version_rejected",
                "content_only_schema_mutation_rejected",
                "after_outbox_fault_rolls_back",
                "canonical_structure_hash_survives_restart",
            )
        ),
        "STRUCTURE_DATABASE_DENOMINATOR",
        "$.structure_database_conflict_contract",
        "structure, database, fault, or restart contract drift",
    )

    multiprocess = value.get("real_multiprocess_validation", {})
    check(
        isinstance(multiprocess, dict)
        and multiprocess.get("selected_test_count") == 5
        and multiprocess.get("selected_test_pass_count") == 5
        and multiprocess.get("measured_duration_seconds") == 86.10
        and multiprocess.get("python_node_websocket_process_boundary") is True
        and multiprocess.get("restart_order_backpressure_and_revoke") is True
        and multiprocess.get("two_principal_browser_disconnect_offline_recovery") is True
        and multiprocess.get("remote_cursor_visible") is True
        and multiprocess.get("browser_page_error_count") == 0
        and multiprocess.get("production_10011_10012_changed") is False,
        "REAL_MULTIPROCESS_DENOMINATOR",
        "$.real_multiprocess_validation",
        "real process/browser validation denominator drift",
    )

    manual = value.get("native_ime_gate", {})
    request_path = _repository_file(manual.get("request_path")) if isinstance(manual, dict) else None
    receipt_path = (
        _repository_file(manual.get("finalization_receipt_path"))
        if isinstance(manual, dict)
        else None
    )
    if completed:
        check(
            isinstance(manual, dict)
            and manual.get("status") == "complete"
            and manual.get("request_path") == EXPECTED_MANUAL_REQUEST_PATH
            and request_path is not None
            and manual.get("user_confirmation_received") is True
            and manual.get("native_os_ime_verified") is True
            and manual.get("two_principal_server_convergence_verified_after_human_input") is True
            and manual.get("test_page_moved_to_trash_after_verification") is True
            and manual.get("finalization_receipt_path") == EXPECTED_NATIVE_IME_RECEIPT_PATH
            and receipt_path == NATIVE_IME_RECEIPT_PATH.resolve()
            and manual.get("automated_text_input_performed") is False,
            "NATIVE_IME_COMPLETED_BOUNDARY",
            "$.native_ime_gate",
            "completed native IME gate identity or proof boundary drift",
        )
        if receipt_path is not None:
            receipt_report = verify_native_ime_receipt(receipt_path)
            check(
                receipt_report.get("status") == "pass"
                and receipt_report.get("gate_passed") is True
                and receipt_report.get("native_ime_verified") is True
                and receipt_report.get("product_complete") is False,
                "NATIVE_IME_RECEIPT_INVALID",
                EXPECTED_NATIVE_IME_RECEIPT_PATH,
                "completed native IME receipt must pass the strict receipt verifier",
            )
            check(
                _sha256_file(receipt_path) == manual.get("finalization_receipt_sha256"),
                "NATIVE_IME_RECEIPT_HASH_DRIFT",
                EXPECTED_NATIVE_IME_RECEIPT_PATH,
                "completed native IME receipt hash drift",
            )
    else:
        check(
            pending
            and isinstance(manual, dict)
            and manual.get("status") == "pending_human_os_ime_input"
            and manual.get("request_path") == EXPECTED_MANUAL_REQUEST_PATH
            and request_path is not None
            and manual.get("user_confirmation_received") is False
            and manual.get("native_os_ime_verified") is False
            and manual.get("two_principal_server_convergence_verified_after_human_input") is False
            and manual.get("test_page_moved_to_trash_after_verification") is False
            and manual.get("finalization_receipt_path") is None
            and manual.get("finalization_receipt_sha256") is None
            and manual.get("automated_text_input_performed") is False,
            "NATIVE_IME_PENDING_BOUNDARY",
            "$.native_ime_gate",
            "native IME gate must remain explicitly pending",
        )
    if request_path is not None:
        check(
            _sha256_file(request_path) == manual.get("request_sha256"),
            "NATIVE_IME_REQUEST_HASH_DRIFT",
            EXPECTED_MANUAL_REQUEST_PATH,
            "manual request hash drift",
        )
        try:
            request = _load(request_path)
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
            request = {}
        request_claims = request.get("claims", {}) if isinstance(request, dict) else {}
        check(
            isinstance(request, dict)
            and request.get("status") == "ready_for_single_manual_input"
            and request.get("development_origin") == "http://127.0.0.1:10112"
            and request.get("approved_subject_id") == "admin"
            and request.get("required_input") == "한글 조합 테스트"
            and request.get("required_user_completion_text")
            == "E1-P3 네이티브 IME 입력 완료"
            and isinstance(request_claims, dict)
            and all(
                request_claims.get(field) is False
                for field in (
                    "native_ime_verified",
                    "E1_P3_gate_passed",
                    "E1_phase_gate_closed",
                    "product_complete",
                )
            ),
            "NATIVE_IME_REQUEST_FALSE_GREEN",
            EXPECTED_MANUAL_REQUEST_PATH,
            "manual request identity or pending claims drift",
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
                        or _successor_owns_current_artifact(
                            str(item.get("path")), target
                        ),
                        "MANIFEST_HASH_DRIFT",
                        str(item.get("path")),
                        "bound file hash drift",
                    )

    finalizer_path = PROJECT_ROOT / "notion_collaboration" / "e1_p3_manual_ime.py"
    finalizer_source = finalizer_path.read_text(encoding="utf-8")
    check(
        _automated_input_calls(finalizer_source) == [],
        "AUTOMATED_NATIVE_IME_FORBIDDEN",
        "notion_collaboration/e1_p3_manual_ime.py",
        "manual native IME finalizer must not type, fill, press, or insert text",
    )
    try:
        convergence_position = finalizer_source.index("if len(set(texts)) != 1:")
        occurrence_position = finalizer_source.index(
            'if final_text.count(request["required_input"]) != 1:'
        )
        trash_position = finalizer_source.index(
            'f"/api/notion/workspaces/{workspace_id}/pages/{page_id}/trash"'
        )
    except ValueError:
        finalizer_order_valid = False
    else:
        finalizer_order_valid = convergence_position < occurrence_position < trash_position
    check(
        finalizer_order_valid,
        "FINALIZER_CLEANUP_ORDER",
        "notion_collaboration/e1_p3_manual_ime.py",
        "Trash cleanup must follow two-client convergence and exact occurrence checks",
    )

    claims = value.get("claims", {})
    completion_claims = (
        "native_ime_verified",
        "E1_P3_gate_passed",
        "EDIT_001_closed",
        "EDIT_002_closed",
        "EDIT_003_closed",
        "E1_phase_gate_closed",
    )
    terminal_non_completion_claims = (
        "production_changed",
        "product_complete",
        "active_shared_goal_complete",
    )
    check(
        isinstance(claims, dict)
        and claims.get("automated_E1_P3_validation_complete") is True
        and all(claims.get(field) is completed for field in completion_claims)
        and all(claims.get(field) is False for field in terminal_non_completion_claims),
        "CLAIMS_FALSE_GREEN",
        "$.claims",
        "E1-P3 lifecycle, closure, or non-product-completion claims drift",
    )

    normalized = sorted(
        {(item["code"], item["subject"], item["message"]): item for item in errors}.values(),
        key=lambda item: (item["code"], item["subject"], item["message"]),
    )
    return {
        "contract": "code_assist.notion.internal_collaborative_convergence_corpus.audit.v1",
        "status": "pass" if not normalized else "fail",
        "error_count": len(normalized),
        "errors": normalized,
        "contract_sha256": _sha256_file(path),
        "source_count": len(EXPECTED_SOURCE_PATHS),
        "test_count": len(EXPECTED_TEST_PATHS),
        "scenario_count": corpus.get("scenario_count") if isinstance(corpus, dict) else None,
        "automated_validation_passed": not normalized,
        "gate_passed": completed and not normalized,
        "native_ime_pending": not completed,
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
