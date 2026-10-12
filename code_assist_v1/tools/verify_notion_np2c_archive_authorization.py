"""Verify an external authorization record before NP2C-5a UI observation.

This preflight is read-only. It grants only the bounded characterization session
described by the record; it never authorizes implementation, migration, private
API access, production data access, or a parity-completion claim.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any, Sequence

from code_assist_v1.tools.verify_notion_np2c_archive_characterization import (
    DEFAULT_MATRIX,
    load_matrix,
    validate_matrix,
)
from code_assist_v1.tools.verify_notion_np2c_archive_evidence_bundle import (
    MAX_MANIFEST_BYTES,
    _load_json_bytes,
    _outside_repository_root,
    _read_explicit_file,
    _redacted_json,
    _relative_to_root,
    _sha256_bytes,
    validate_authorization,
)


AUTHORIZATION_CONTRACT = "np2c5a_archive_live_authorization_v1"
AUTHORIZATION_CLAIMS = {
    "characterization_session_authorized": True,
    "live_characterized": False,
    "np2c5a_complete": False,
    "differential_passed": False,
    "source_v5_implemented": False,
    "workspace_1318_implemented": False,
    "runtime_archive_enabled": False,
    "source_catalog_or_runtime_implementation_authorized": False,
    "np2c_complete": False,
    "overall_parity_complete": False,
}


def validate_authorization_record(
    record: Any,
    *,
    matrix_sha256: str,
    approved_authorization_sha256: str,
    execution_time: datetime,
) -> dict[str, Any]:
    result = _validate_authorization_record(
        record,
        matrix_sha256=matrix_sha256,
        approved_authorization_sha256=approved_authorization_sha256,
        require_approval_digest=True,
        execution_time=execution_time,
    )
    valid = not result["errors"]
    return {
        "valid": valid,
        "status": "authorized_preflight" if valid else "preflight_rejected",
        "authorized_to_begin_characterization": valid,
        "error_count": len(result["errors"]),
        "errors": result["errors"],
        "approved_tenant_count": result["approved_tenant_count"],
        "approved_plans": result["approved_plans"],
        "normal_public_ui_only": result["normal_public_ui_only"],
        "authorization_sha256": result["authorization_sha256"],
        "approval_digest_matched": result["approval_digest_matched"],
        "implementation_authorized": False,
        "completion_claimed": False,
    }


def _validate_authorization_record(
    record: Any,
    *,
    matrix_sha256: str,
    approved_authorization_sha256: str | None,
    require_approval_digest: bool,
    execution_time: datetime | None,
) -> dict[str, Any]:
    errors: list[str] = []

    def require(condition: bool, message: str) -> None:
        if not condition:
            errors.append(message)

    if not isinstance(record, dict):
        errors.append("authorization record root must be an object")
        authorization: Any = None
        claims: Any = None
    else:
        require(
            set(record)
            == {
                "schema_version",
                "contract",
                "matrix_sha256",
                "authorization",
                "claims",
            },
            "authorization record keys are invalid",
        )
        require(record.get("schema_version") == 1, "authorization record schema is invalid")
        require(record.get("contract") == AUTHORIZATION_CONTRACT, "authorization record contract is invalid")
        require(record.get("matrix_sha256") == matrix_sha256, "authorization record matrix hash is stale")
        authorization = record.get("authorization")
        claims = record.get("claims")

    authorization_result = validate_authorization(
        authorization,
        approved_authorization_sha256=approved_authorization_sha256,
        require_approval_digest=require_approval_digest,
        execution_time=execution_time,
    )
    errors.extend(authorization_result["errors"])
    require(claims == AUTHORIZATION_CLAIMS, "authorization record claims are invalid")
    require(
        isinstance(record, dict) and _redacted_json(record),
        "authorization record contains a secret, email, or absolute path",
    )

    unique_errors = sorted(set(errors))
    tenant_by_id = authorization_result["tenant_by_id"]
    plans = sorted(
        {
            str(item.get("plan"))
            for item in tenant_by_id.values()
            if isinstance(item, dict)
        }
    )
    return {
        "errors": unique_errors,
        "approved_tenant_count": len(tenant_by_id),
        "approved_plans": plans,
        "normal_public_ui_only": bool(
            isinstance(authorization, dict)
            and authorization.get("normal_public_ui_only") is True
        ),
        "authorization_sha256": authorization_result["authorization_sha256"],
        "approval_digest_matched": authorization_result["approval_digest_matched"],
    }


def validate_authorization_record_for_inspection(
    record: Any,
    *,
    matrix_sha256: str,
) -> dict[str, Any]:
    result = _validate_authorization_record(
        record,
        matrix_sha256=matrix_sha256,
        approved_authorization_sha256=None,
        require_approval_digest=False,
        execution_time=None,
    )
    inspection_valid = not result["errors"]
    return {
        "inspection_valid": inspection_valid,
        "valid": False,
        "status": (
            "inspection_only_not_authorized"
            if inspection_valid
            else "inspection_rejected"
        ),
        "authorized_to_begin_characterization": False,
        "error_count": len(result["errors"]),
        "errors": result["errors"],
        "approved_tenant_count": result["approved_tenant_count"],
        "approved_plans": result["approved_plans"],
        "normal_public_ui_only": result["normal_public_ui_only"],
        "authorization_sha256": result["authorization_sha256"],
        "approval_digest_matched": False,
        "implementation_authorized": False,
        "completion_claimed": False,
    }


def verify_authorization_record(
    record_path: str | Path,
    evidence_root: str | Path,
    *,
    approved_authorization_sha256: str,
    matrix_path: str | Path = DEFAULT_MATRIX,
    execution_time: datetime | None = None,
) -> dict[str, Any]:
    try:
        matrix_path_obj = Path(matrix_path)
        matrix = load_matrix(matrix_path_obj)
        if not validate_matrix(matrix)["valid"]:
            raise ValueError("Archive characterization matrix is invalid")
        matrix_sha256 = _sha256_bytes(matrix_path_obj.read_bytes())
        root = _outside_repository_root(Path(evidence_root))
        relative_record = _relative_to_root(root, Path(record_path))
        payload = _read_explicit_file(root, relative_record, MAX_MANIFEST_BYTES)
        record = _load_json_bytes(payload)
        report = validate_authorization_record(
            record,
            matrix_sha256=matrix_sha256,
            approved_authorization_sha256=approved_authorization_sha256,
            execution_time=execution_time or datetime.now(timezone.utc),
        )
        report["authorization_record_sha256"] = _sha256_bytes(payload)
        return report
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        return {
            "valid": False,
            "status": "preflight_rejected",
            "authorized_to_begin_characterization": False,
            "error_count": 1,
            "errors": ["authorization record could not be verified"],
            "approved_tenant_count": 0,
            "approved_plans": [],
            "normal_public_ui_only": False,
            "authorization_sha256": None,
            "approval_digest_matched": False,
            "implementation_authorized": False,
            "completion_claimed": False,
            "authorization_record_sha256": None,
        }


def inspect_authorization_record(
    record_path: str | Path,
    evidence_root: str | Path,
    *,
    matrix_path: str | Path = DEFAULT_MATRIX,
) -> dict[str, Any]:
    try:
        matrix_path_obj = Path(matrix_path)
        matrix = load_matrix(matrix_path_obj)
        if not validate_matrix(matrix)["valid"]:
            raise ValueError("Archive characterization matrix is invalid")
        matrix_sha256 = _sha256_bytes(matrix_path_obj.read_bytes())
        root = _outside_repository_root(Path(evidence_root))
        relative_record = _relative_to_root(root, Path(record_path))
        payload = _read_explicit_file(root, relative_record, MAX_MANIFEST_BYTES)
        record = _load_json_bytes(payload)
        report = validate_authorization_record_for_inspection(
            record,
            matrix_sha256=matrix_sha256,
        )
        report["authorization_record_sha256"] = _sha256_bytes(payload)
        return report
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        return {
            "inspection_valid": False,
            "valid": False,
            "status": "inspection_rejected",
            "authorized_to_begin_characterization": False,
            "error_count": 1,
            "errors": ["authorization record could not be inspected"],
            "approved_tenant_count": 0,
            "approved_plans": [],
            "normal_public_ui_only": False,
            "authorization_sha256": None,
            "approval_digest_matched": False,
            "implementation_authorized": False,
            "completion_claimed": False,
            "authorization_record_sha256": None,
        }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("record")
    parser.add_argument("--evidence-root", required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--inspect-only", action="store_true")
    mode.add_argument("--approved-authorization-sha256")
    parser.add_argument("--matrix", default=str(DEFAULT_MATRIX))
    args = parser.parse_args(argv)
    if args.inspect_only:
        report = inspect_authorization_record(
            args.record,
            args.evidence_root,
            matrix_path=args.matrix,
        )
    else:
        report = verify_authorization_record(
            args.record,
            args.evidence_root,
            approved_authorization_sha256=args.approved_authorization_sha256,
            matrix_path=args.matrix,
        )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    if args.inspect_only:
        return 0 if report["inspection_valid"] else 2
    return 0 if report["valid"] else 2


if __name__ == "__main__":
    sys.exit(main())
