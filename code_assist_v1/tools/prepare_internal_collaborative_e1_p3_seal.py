"""Read-only E1-P3 seal preflight and sealed-state audit."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Sequence

from code_assist_v1.tools.contract_audit import audit_contracts
from code_assist_v1.tools.internal_collaborative_validation import (
    _inherit_validated_manifest_drift,
)
from code_assist_v1.tools.verify_internal_collaborative_convergence_corpus import (
    CONTRACT_PATH as CONVERGENCE_CONTRACT_PATH,
    verify as verify_convergence_contract,
)
from code_assist_v1.tools.verify_internal_collaborative_native_ime_receipt import (
    EXPECTED_CONFIRMATION,
    RECEIPT_PATH,
    REQUEST_PATH,
    verify as verify_native_ime_receipt,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = PROJECT_ROOT.parent
CONTRACT_ROOT = PROJECT_ROOT / "docs" / "notion_parity" / "core_free_web_ui"
CATALOG_PATH = CONTRACT_ROOT / "internal_collaborative_packets.v2.json"
LADDER_PATH = CONTRACT_ROOT / "internal_collaborative_validation_ladder.v1.json"
MATRIX_PATH = CONTRACT_ROOT / "internal_collaborative_completion_matrix.v1.json"
POINTER_PATH = CONTRACT_ROOT / "internal_collaborative_current_checkpoint.v1.json"
SBE_ROOT = PROJECT_ROOT / "docs" / "knowledge_spaces"
EXPECTED_PREDECESSOR_PACKET = "E1-P2"
EXPECTED_PACKET = "E1-P3"


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


def _packet(catalog: dict[str, Any], packet_id: str) -> dict[str, Any] | None:
    for item in catalog.get("packets", []):
        if isinstance(item, dict) and item.get("id") == packet_id:
            return item
    return None


def prepare(
    *,
    receipt_path: Path = RECEIPT_PATH,
    catalog_path: Path = CATALOG_PATH,
    ladder_path: Path = LADDER_PATH,
    matrix_path: Path = MATRIX_PATH,
    pointer_path: Path = POINTER_PATH,
    convergence_contract_path: Path = CONVERGENCE_CONTRACT_PATH,
) -> dict[str, Any]:
    """Return a redacted, non-mutating seal-readiness report."""

    errors: list[dict[str, str]] = []
    blockers: list[dict[str, str]] = []

    def error(code: str, subject: str, message: str) -> None:
        errors.append({"code": code, "subject": subject, "message": message})

    try:
        catalog = _load(catalog_path)
        ladder = _load(ladder_path)
        matrix = _load(matrix_path)
        pointer = _load(pointer_path)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        return {
            "contract": "code_assist.notion.e1_p3_seal_preflight.v1",
            "status": "fail",
            "error_count": 1,
            "errors": [
                {"code": "HOT_CONTRACT_READ_FAILED", "subject": "hot_chain", "message": str(exc)}
            ],
            "blocker_count": 0,
            "blockers": [],
            "ready_to_apply_seal": False,
            "mutated": False,
            "product_complete": False,
        }
    if not all(isinstance(item, dict) for item in (catalog, ladder, matrix, pointer)):
        error("HOT_CONTRACT_SHAPE", "hot_chain", "all hot contracts must be objects")

    convergence_report = _inherit_validated_manifest_drift(
        verify_convergence_contract(convergence_contract_path),
        "E1-P3",
    )
    if convergence_report.get("status") != "pass":
        error(
            "CONVERGENCE_CONTRACT_INVALID",
            "E1-P3",
            f"error_count={convergence_report.get('error_count')}",
        )

    current = _packet(catalog, EXPECTED_PACKET)
    predecessor = _packet(catalog, EXPECTED_PREDECESSOR_PACKET)
    packet_common_state_valid = False
    pre_seal_state = False
    sealed_state = False
    if current is None:
        error("PACKET_MISSING", EXPECTED_PACKET, "packet is absent from the catalog")
    else:
        packet_common_state_valid = bool(
            current.get("required") is True
        and current.get("ownership_lane") == "DOC_HARNESS"
        and current.get("prerequisites") == ["E1-P2", "H0A-P2"]
        and current.get("matrix_closes") == ["EDIT-001", "EDIT-002", "EDIT-003"]
        and current.get("gate_ids") == ["E1-GATE-001"]
        )
        pre_seal_state = packet_common_state_valid and current.get("status") == "planned"
        sealed_state = packet_common_state_valid and current.get("status") == "gate_passed"
        if not (pre_seal_state or sealed_state):
            error(
                "PACKET_STATE_DRIFT",
                EXPECTED_PACKET,
                "exact planned pre-seal or gate-passed sealed packet state required",
            )
    if predecessor is None or predecessor.get("status") != "gate_passed":
        error(
            "PREDECESSOR_NOT_GATE_PASSED",
            EXPECTED_PREDECESSOR_PACKET,
            "E1-P2 must remain gate-passed",
        )

    pointer_checkpoint = None
    if isinstance(pointer, dict):
        relative = pointer.get("checkpoint_path")
        if isinstance(relative, str) and "\\" not in relative:
            candidate = (REPOSITORY_ROOT / relative).resolve(strict=False)
            try:
                candidate.relative_to(REPOSITORY_ROOT.resolve())
            except ValueError:
                candidate = None
            if candidate is not None and candidate.is_file():
                pointer_checkpoint = candidate
    pointer_valid = bool(
        pointer.get("contract")
        == "code_assist.notion.internal_collaborative_current_checkpoint.v1"
        and pointer_checkpoint is not None
        and pointer.get("checkpoint_sha256") == _sha256_file(pointer_checkpoint)
    )
    expected_pointer_packet = EXPECTED_PACKET if sealed_state else EXPECTED_PREDECESSOR_PACKET
    if not pointer_valid or pointer.get("packet_id") != expected_pointer_packet:
        error(
            "CURRENT_POINTER_DRIFT",
            "current_checkpoint",
            f"{expected_pointer_packet} must be the exact current checkpoint for this packet state",
        )

    sbe_report, sbe_exit = audit_contracts(SBE_ROOT)
    if sbe_exit != 0 or sbe_report.get("status") != "pass" or sbe_report.get("error_count") != 0:
        error(
            "SBE_DYNAMIC_BASELINE_INVALID",
            "knowledge_spaces",
            f"error_count={sbe_report.get('error_count')}",
        )
    current_sbe_hash = str(sbe_report.get("input_sha256") or "")
    if current_sbe_hash and not current_sbe_hash.startswith("sha256:"):
        current_sbe_hash = "sha256:" + current_sbe_hash
    checkpoint_document: dict[str, Any] = {}
    if pointer_checkpoint is not None:
        try:
            loaded_checkpoint = _load(pointer_checkpoint)
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
            loaded_checkpoint = {}
        if isinstance(loaded_checkpoint, dict):
            checkpoint_document = loaded_checkpoint
    snapshot = checkpoint_document.get("cross_catalog_ownership_snapshot", {})
    if not isinstance(snapshot, dict):
        snapshot = {}
    bound_sbe_hash = snapshot.get("sbe_contract_audit_input_sha256")
    sbe_rebase_required = bound_sbe_hash != current_sbe_hash

    receipt_present = receipt_path.is_file()
    receipt_report: dict[str, Any] | None = None
    if not receipt_present:
        blockers.append(
            {
                "code": "NATIVE_IME_RECEIPT_MISSING",
                "subject": EXPECTED_PACKET,
                "message": "human Windows Korean IME confirmation and finalization receipt are pending",
            }
        )
    else:
        receipt_report = verify_native_ime_receipt(receipt_path)
        if receipt_report.get("status") != "pass":
            error(
                "NATIVE_IME_RECEIPT_INVALID",
                str(receipt_path.name),
                f"error_count={receipt_report.get('error_count')}",
            )

    hashes = {}
    for label, target in (
        ("catalog_sha256", catalog_path),
        ("ladder_sha256", ladder_path),
        ("matrix_sha256", matrix_path),
        ("pointer_sha256", pointer_path),
        ("convergence_contract_sha256", convergence_contract_path),
        ("manual_request_sha256", REQUEST_PATH),
    ):
        if target.is_file():
            hashes[label] = _sha256_file(target)
    if receipt_present:
        hashes["native_ime_receipt_sha256"] = _sha256_file(receipt_path)

    errors = sorted(errors, key=lambda item: (item["code"], item["subject"], item["message"]))
    blockers = sorted(
        blockers,
        key=lambda item: (item["code"], item["subject"], item["message"]),
    )
    ready = pre_seal_state and not errors and not blockers
    sealed = sealed_state and not errors and not blockers
    status = "sealed" if sealed else "ready_to_apply_seal" if ready else "pending_native_ime"
    if errors:
        status = "fail"
    return {
        "schema_version": 1,
        "contract": "code_assist.notion.e1_p3_seal_preflight.v1",
        "status": status,
        "error_count": len(errors),
        "errors": errors,
        "blocker_count": len(blockers),
        "blockers": blockers,
        "ready_to_apply_seal": ready,
        "seal_applied": sealed,
        "current_packet": EXPECTED_PACKET,
        "current_predecessor_packet": EXPECTED_PREDECESSOR_PACKET,
        "current_pointer_packet": pointer.get("packet_id"),
        "native_ime_receipt_present": receipt_present,
        "native_ime_receipt_verified": bool(
            receipt_report and receipt_report.get("native_ime_verified") is True
        ),
        "sbe_dynamic_baseline": {
            "status": sbe_report.get("status"),
            "error_count": sbe_report.get("error_count"),
            "input_sha256": current_sbe_hash,
            "catalog_bound_input_sha256": bound_sbe_hash,
            "hot_chain_rebase_required": sbe_rebase_required,
            "goal_count": sbe_report.get("summary", {}).get("goal", {}).get("count"),
            "scenario_count": sbe_report.get("summary", {})
            .get("scenarios", {})
            .get("count"),
        },
        "current_hashes": hashes,
        "seal_mutation_plan": {
            "writes_performed_by_preflight": False,
            "finalize_convergence_contract": not sealed_state,
            "publish_native_ime_receipt": not sealed_state,
            "set_catalog_packet_E1_P3_gate_passed": not sealed_state,
            "close_phase_gate": None if sealed_state else "E1-GATE-001",
            "matrix_rows_closed_by_catalog_derivation": [
                "EDIT-001",
                "EDIT-002",
                "EDIT-003",
            ],
            "matrix_semantic_mutation_required": False,
            "rebase_current_sbe_dynamic_snapshot": sbe_rebase_required,
            "publish_execution_receipt_and_checkpoint": not sealed_state,
            "advance_current_pointer_to": None if sealed_state else EXPECTED_PACKET,
            "next_packet_after_seal": "S0A-P1",
        },
        "expected_user_confirmation_sha256": "sha256:"
        + hashlib.sha256(EXPECTED_CONFIRMATION.encode("utf-8")).hexdigest(),
        "mutated": False,
        "production_10011_10012_changed": False,
        "product_complete": False,
        "active_shared_goal_complete": False,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", type=Path, default=RECEIPT_PATH)
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args(argv)
    report = prepare(receipt_path=args.receipt)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=None if args.compact else 2))
    return 1 if report.get("status") == "fail" else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
