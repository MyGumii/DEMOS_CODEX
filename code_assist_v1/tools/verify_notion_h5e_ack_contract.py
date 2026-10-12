"""Read-only verifier for the proposed NP-2A H5e ack design contract.

This verifier deliberately imports no application, store, migration, route, or
configuration module.  It proves that the checked-in contract remains a
non-enabled design artifact with the minimum fencing, crash, and redaction
requirements needed before any destructive implementation can be proposed.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONTRACT = (
    PROJECT_ROOT / "docs" / "notion_parity" / "h5e_conditional_ack_contract.json"
)


class DuplicateJsonKeyError(ValueError):
    pass


def _object(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise DuplicateJsonKeyError(f"duplicate JSON object key: {key}")
        value[key] = item
    return value


def load_contract(path: str | Path = DEFAULT_CONTRACT) -> dict[str, Any]:
    value = json.loads(
        Path(path).read_text(encoding="utf-8"),
        object_pairs_hook=_object,
        parse_constant=lambda item: (_ for _ in ()).throw(
            ValueError(f"non-finite JSON number: {item}")
        ),
    )
    if not isinstance(value, dict):
        raise ValueError("H5e contract root must be an object")
    return value


def validate_contract(value: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []

    def require(condition: bool, message: str) -> None:
        if not condition:
            errors.append(message)

    require(value.get("contract_version") == 1, "contract_version must be 1")
    require(
        value.get("name") == "np2a_h5e_legacy_watermark_conditional_ack",
        "contract name is invalid",
    )
    require(
        value.get("status") == "proposed_not_authorized",
        "H5e must remain proposed_not_authorized",
    )

    implementation = value.get("implementation")
    require(isinstance(implementation, dict), "implementation must be an object")
    if isinstance(implementation, dict):
        require(
            implementation.get("source_schema_version") == 2026081304,
            "source schema candidate must be 2026081304",
        )
        for field in (
            "implemented",
            "migration_applied_to_live_data",
            "destructive_ack_enabled",
            "continuous_worker_enabled",
            "resolver_cutover_enabled",
            "ui_enabled",
        ):
            require(implementation.get(field) is False, f"{field} must be false")

    prerequisites = value.get("prerequisites")
    require(isinstance(prerequisites, dict), "prerequisites must be an object")
    if isinstance(prerequisites, dict):
        require(
            prerequisites.get("source_schema_version") == 2026081303,
            "frozen v3 prerequisite is missing",
        )
        require(
            prerequisites.get("workspace_schema_version") == 2026081309,
            "workspace 1309 prerequisite is missing",
        )
        for field in (
            "explicit_destructive_authorization_required",
            "exact_owner_path_required",
            "source_quiescence_required",
            "verified_backup_required",
            "restore_rehearsal_required",
            "single_logical_ack_consumer_required",
        ):
            require(prerequisites.get(field) is True, f"{field} must be true")

    bounds = value.get("bounds")
    require(isinstance(bounds, dict), "bounds must be an object")
    if isinstance(bounds, dict):
        require(bounds.get("minimum_batch_size") == 1, "minimum batch must be 1")
        require(bounds.get("maximum_batch_size") == 32, "maximum batch must be 32")
        require(
            bounds.get("catalog_io_inside_source_write_transaction") is False,
            "catalog I/O inside the source transaction is forbidden",
        )
        require(
            bounds.get("network_io_inside_source_write_transaction") is False,
            "network I/O inside the source transaction is forbidden",
        )

    eligibility = set(value.get("ack_eligibility_all", []))
    for item in (
        "source_file_identity_exact",
        "source_epoch_exact",
        "full_sweep_generation_exact",
        "pending_work_zero",
        "ignored_work_zero",
        "superseded_work_zero",
        "work_status_applied",
        "watermark_change_generation_exact",
        "watermark_change_mask_exact",
        "incarnation_id_exact",
        "receipt_sha256_exact",
        "catalog_effect_commitment_exact",
        "required_full_sweep_completion_exact",
        "workspace_completion_set_exact",
    ):
        require(item in eligibility, f"missing eligibility fence: {item}")

    never_ack = set(value.get("never_ack_dispositions", []))
    for item in ("superseded", "no_binding", "pending"):
        require(item in never_ack, f"missing never-ack disposition: {item}")

    identity = set(value.get("operation_identity_inputs", []))
    for item in (
        "consumer",
        "source_owner_id",
        "source_epoch",
        "full_sweep_generation",
        "source_device",
        "source_inode",
        "source_link_count",
        "page_id",
        "change_generation",
        "change_mask",
        "incarnation_id",
        "incarnation_generation",
        "receipt_sha256",
        "effect_operation_id",
        "completion_operation_id",
    ):
        require(item in identity, f"operation identity omits {item}")

    order = value.get("transaction_order")
    require(isinstance(order, list), "transaction_order must be a list")
    if isinstance(order, list):
        require(
            order[:1] == ["exact_replay_before_source_io"],
            "exact replay must precede source I/O",
        )
        require(
            "conditional_delete_exact_watermarks" in order,
            "conditional watermark delete step is missing",
        )
        require(order[-1:] == ["identifier_free_response"], "response must be redacted")

    generation = value.get("source_generation_policy")
    require(isinstance(generation, dict), "source_generation_policy must be an object")
    if isinstance(generation, dict):
        require(generation.get("ack_increments_source_epoch") is False, "ack must not bump epoch")
        require(
            generation.get("ack_increments_full_sweep_generation") is False,
            "ack must not bump full-sweep generation",
        )
        require(
            generation.get("operation_id_must_survive_generation_reset_to_one") is True,
            "operation identity must survive watermark generation reset",
        )

    forbidden = set(value.get("public_result_forbidden_data", []))
    for item in (
        "source_owner_id",
        "workspace_id",
        "page_id",
        "source_path",
        "content",
        "public_token",
        "lease_token",
        "incarnation_id",
        "receipt_sha256",
    ):
        require(item in forbidden, f"public redaction omits {item}")

    slices = value.get("implementation_slices")
    require(isinstance(slices, list), "implementation_slices must be a list")
    if isinstance(slices, list):
        ids = [item.get("id") for item in slices if isinstance(item, dict)]
        require(ids == ["H5e-0", "H5e-1", "H5e-2", "H5e-3", "H5e-4", "H5f"], "slice order is invalid")
        for item in slices[1:]:
            require(
                isinstance(item, dict) and item.get("authorization_required") is True,
                "every post-design slice requires authorization",
            )

    return {
        "valid": not errors,
        "error_count": len(errors),
        "errors": errors,
        "status": value.get("status"),
        "implemented": implementation.get("implemented") if isinstance(implementation, dict) else None,
        "destructive_ack_enabled": (
            implementation.get("destructive_ack_enabled")
            if isinstance(implementation, dict)
            else None
        ),
    }


def verify_contract(path: str | Path = DEFAULT_CONTRACT) -> dict[str, Any]:
    try:
        return validate_contract(load_contract(path))
    except Exception as exc:
        return {
            "valid": False,
            "error_count": 1,
            "errors": [str(exc)],
            "status": None,
            "implemented": None,
            "destructive_ack_enabled": None,
        }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", default=str(DEFAULT_CONTRACT))
    args = parser.parse_args(argv)
    report = verify_contract(args.contract)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    sys.exit(main())
