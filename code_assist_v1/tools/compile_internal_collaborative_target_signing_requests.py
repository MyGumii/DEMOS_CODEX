"""Compile canonical, unsigned O1B approval and handoff signing requests.

This create-only tool handles no private key and creates no approval.  It
validates the complete unsigned documents against the same structural checks
used by the target verifier, then emits the exact canonical bytes an external
Ed25519 signer must review and sign.  A signing-request bundle is not execution
authority and cannot be passed to the pre-execution verifier as an approval or
handoff artifact.
"""

from __future__ import annotations

import argparse
import base64
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

import code_assist_v1.tools.verify_internal_collaborative_target_restore as target_restore_verifier


SIGNING_REQUEST_CONTRACT = (
    "code_assist.notion.internal_collaborative_target_signing_requests."
    "o1b_p1.v1"
)
SIGNING_REQUEST_RESULT_CONTRACT = (
    "code_assist.notion.internal_collaborative_target_signing_requests_result."
    "o1b_p1.v1"
)
REVIEWER_METADATA_CONTRACT = (
    "code_assist.notion.internal_collaborative_target_handoff_reviewer_metadata."
    "o1b_p1.v1"
)
_SHA256_RE = re.compile(r"sha256:[0-9a-f]{64}\Z", re.ASCII)
_DUMMY_SIGNATURE_BASE64 = base64.b64encode(bytes(64)).decode("ascii")


class SigningRequestError(ValueError):
    """An unsigned authority document is unsafe or structurally invalid."""


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        del message
        raise SigningRequestError("invalid_arguments")


def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in items:
        if key in value:
            raise SigningRequestError("duplicate_json_key")
        value[key] = item
    return value


def _load_once(path: Path) -> tuple[Any, str]:
    raw = path.read_bytes()
    value = json.loads(
        raw.decode("utf-8"),
        object_pairs_hook=_pairs,
        parse_constant=lambda item: (_ for _ in ()).throw(
            SigningRequestError(f"invalid_json_constant:{item}")
        ),
    )
    return value, "sha256:" + hashlib.sha256(raw).hexdigest()


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _pretty_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=False,
            indent=2,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _sha256_value(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _signing_message(value: Any) -> dict[str, Any]:
    canonical = _canonical_bytes(value)
    return {
        "canonicalization": "json-utf8-sort-keys-compact-no-nan-v1",
        "message_sha256": "sha256:" + hashlib.sha256(canonical).hexdigest(),
        "message_byte_count": len(canonical),
        "message_base64": base64.b64encode(canonical).decode("ascii"),
    }


def _trust_anchor(policy: Mapping[str, Any], role: str) -> Mapping[str, Any]:
    trust = policy.get("trust_anchor_policy")
    anchor = trust.get(role) if isinstance(trust, dict) else None
    if not (
        isinstance(anchor, dict)
        and set(anchor) == {"key_id", "public_key_base64", "public_key_sha256"}
    ):
        raise SigningRequestError(f"bound_{role}_trust_anchor_required")
    return anchor


def _validate_unsigned_approval(
    document: Any,
    *,
    policy: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(document, dict):
        raise SigningRequestError("unsigned_approval_object_required")
    if "signature" in document or "receipt_sha256" in document:
        raise SigningRequestError("unsigned_approval_must_not_contain_seal")
    anchor = _trust_anchor(policy, "approval")
    candidate = deepcopy(document)
    candidate["signature"] = {
        "key_id": anchor["key_id"],
        "algorithm": "ed25519",
        "signature_base64": _DUMMY_SIGNATURE_BASE64,
    }
    candidate["receipt_sha256"] = _sha256_value(candidate)
    errors: list[dict[str, str]] = []
    target_restore_verifier._verify_approval(  # noqa: SLF001
        candidate,
        policy=policy,
        errors=errors,
    )
    if {item.get("code") for item in errors} != {"APPROVAL_SIGNATURE_INVALID"}:
        raise SigningRequestError("unsigned_approval_contract_invalid")
    return deepcopy(document)


def _reviewer_metadata(value: Any) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != {
        "schema_version",
        "contract",
        "reviewer_id",
        "attestation_system_id",
        "trust_policy_id",
        "signature_record_id",
    }:
        raise SigningRequestError("handoff_reviewer_metadata_shape_invalid")
    if not (
        value.get("schema_version") == 1
        and value.get("contract") == REVIEWER_METADATA_CONTRACT
        and all(
            isinstance(value.get(name), str) and value.get(name)
            for name in (
                "reviewer_id",
                "attestation_system_id",
                "trust_policy_id",
                "signature_record_id",
            )
        )
    ):
        raise SigningRequestError("handoff_reviewer_metadata_identity_invalid")
    return {
        name: value[name]
        for name in (
            "reviewer_id",
            "attestation_system_id",
            "trust_policy_id",
            "signature_record_id",
        )
    }


def _validate_unsigned_handoff(
    document: Any,
    *,
    metadata: Mapping[str, str],
    policy: Mapping[str, Any],
    approval: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(document, dict):
        raise SigningRequestError("unsigned_handoff_object_required")
    if "reviewer_attestation" in document or "receipt_sha256" in document:
        raise SigningRequestError("unsigned_handoff_must_not_contain_seal")
    accepted_at = document.get("accepted_at")
    if not isinstance(accepted_at, str):
        raise SigningRequestError("handoff_accepted_at_required")
    anchor = _trust_anchor(policy, "handoff")
    evidence_sha256 = _sha256_value(document)
    attestation_payload = {
        "reviewer_id": metadata["reviewer_id"],
        "attestation_system_id": metadata["attestation_system_id"],
        "trust_policy_id": metadata["trust_policy_id"],
        "signature_key_id": anchor["key_id"],
        "signature_algorithm": "ed25519",
        "signature_record_id": metadata["signature_record_id"],
        "decision": "accepted",
        "evidence_document_sha256": evidence_sha256,
        "signed_at": accepted_at,
    }
    candidate = deepcopy(document)
    candidate["reviewer_attestation"] = {
        **attestation_payload,
        "signature_base64": _DUMMY_SIGNATURE_BASE64,
        "private_key_material_in_receipt": False,
    }
    candidate["receipt_sha256"] = _sha256_value(candidate)
    errors: list[dict[str, str]] = []
    target_restore_verifier._verify_handoff(  # noqa: SLF001
        candidate,
        policy=policy,
        approval=approval,
        errors=errors,
    )
    if {item.get("code") for item in errors} != {"HANDOFF_ATTESTATION"}:
        raise SigningRequestError("unsigned_handoff_contract_invalid")
    return deepcopy(document), attestation_payload


def compile_signing_requests(
    *,
    policy_path: Path,
    expected_policy_sha256: str,
    approval_document_path: Path,
    handoff_document_path: Path,
    reviewer_metadata_path: Path,
    output_path: Path,
) -> dict[str, Any]:
    """Write canonical external-signing messages without creating authority."""

    if _SHA256_RE.fullmatch(expected_policy_sha256) is None:
        raise SigningRequestError("expected_policy_sha256_invalid")
    policy, policy_sha256 = _load_once(policy_path)
    if policy_sha256 != expected_policy_sha256 or not isinstance(policy, dict):
        raise SigningRequestError("reviewed_policy_hash_mismatch")
    policy_report = target_restore_verifier.verify(
        policy_path,
        expected_policy_sha256=expected_policy_sha256,
    )
    if not (
        policy_report.get("status") == "pass"
        and policy_report.get("readiness_contract_passed") is True
        and policy_report.get("gate_passed") is False
        and policy_report.get("caller_pinned_policy") is True
    ):
        raise SigningRequestError("bound_policy_readiness_invalid")

    approval_value, approval_document_sha256 = _load_once(approval_document_path)
    handoff_value, handoff_document_sha256 = _load_once(handoff_document_path)
    reviewer_value, reviewer_metadata_sha256 = _load_once(reviewer_metadata_path)
    approval = _validate_unsigned_approval(approval_value, policy=policy)
    metadata = _reviewer_metadata(reviewer_value)
    handoff, attestation_payload = _validate_unsigned_handoff(
        handoff_value,
        metadata=metadata,
        policy=policy,
        approval=approval,
    )
    approval_anchor = _trust_anchor(policy, "approval")
    handoff_anchor = _trust_anchor(policy, "handoff")
    bundle: dict[str, Any] = {
        "schema_version": 1,
        "contract": SIGNING_REQUEST_CONTRACT,
        "request_id": (
            "O1B-P1-SIGNING-"
            + _sha256_value(
                {
                    "policy_sha256": policy_sha256,
                    "approval_document_sha256": approval_document_sha256,
                    "handoff_document_sha256": handoff_document_sha256,
                    "reviewer_metadata_sha256": reviewer_metadata_sha256,
                }
            )[7:23].upper()
        ),
        "immutable_after_creation": True,
        "policy_binding": {
            "policy_sha256": policy_sha256,
            "approval_key_id": approval_anchor["key_id"],
            "approval_public_key_sha256": approval_anchor["public_key_sha256"],
            "handoff_key_id": handoff_anchor["key_id"],
            "handoff_public_key_sha256": handoff_anchor["public_key_sha256"],
        },
        "source_bindings": {
            "approval_document_sha256": approval_document_sha256,
            "handoff_document_sha256": handoff_document_sha256,
            "reviewer_metadata_sha256": reviewer_metadata_sha256,
        },
        "approval_request": {
            "document": approval,
            "signature_algorithm": "ed25519",
            "signature_key_id": approval_anchor["key_id"],
            "signing_message": _signing_message(approval),
        },
        "handoff_request": {
            "document": handoff,
            "evidence_document_sha256": _sha256_value(handoff),
            "reviewer_attestation_payload": attestation_payload,
            "signature_algorithm": "ed25519",
            "signature_key_id": handoff_anchor["key_id"],
            "signing_message": _signing_message(attestation_payload),
        },
        "assembly_contract": {
            "approval_signature_inserted_before_approval_self_hash": True,
            "handoff_attestation_signature_inserted_before_handoff_self_hash": True,
            "signature_encoding": "raw-base64",
            "private_key_material_allowed": False,
            "assembled_artifacts_require_fresh_pre_execution_verification": True,
        },
        "claims": {
            "approval_granted": False,
            "handoff_accepted_by_this_compiler": False,
            "private_key_loaded": False,
            "signature_created": False,
            "nonce_consumed": False,
            "external_action_started": False,
            "target_action_executed": False,
            "gate_passed": False,
            "product_complete": False,
        },
    }
    bundle["request_sha256"] = _sha256_value(bundle)
    payload = _pretty_bytes(bundle)

    if not (
        _load_once(policy_path)[1] == policy_sha256
        and _load_once(approval_document_path)[1] == approval_document_sha256
        and _load_once(handoff_document_path)[1] == handoff_document_sha256
        and _load_once(reviewer_metadata_path)[1] == reviewer_metadata_sha256
    ):
        raise SigningRequestError("source_changed_before_request_publish")

    output_path.parent.resolve(strict=True)
    with output_path.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    return {
        "contract": SIGNING_REQUEST_RESULT_CONTRACT,
        "status": "pass",
        "request_id": bundle["request_id"],
        "request_file_sha256": "sha256:" + hashlib.sha256(payload).hexdigest(),
        "request_record_sha256": bundle["request_sha256"],
        "approval_granted": False,
        "signature_created": False,
        "private_key_loaded": False,
        "external_action_started": False,
        "target_action_executed": False,
        "gate_passed": False,
        "product_complete": False,
    }


def _parser() -> _Parser:
    parser = _Parser(prog="compile-internal-collaborative-target-signing-requests")
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--expected-policy-sha256", required=True)
    parser.add_argument("--approval-document", type=Path, required=True)
    parser.add_argument("--handoff-document", type=Path, required=True)
    parser.add_argument("--reviewer-metadata", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--compact", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    compact = False
    try:
        arguments = _parser().parse_args(argv)
        compact = arguments.compact
        result = compile_signing_requests(
            policy_path=arguments.policy,
            expected_policy_sha256=arguments.expected_policy_sha256,
            approval_document_path=arguments.approval_document,
            handoff_document_path=arguments.handoff_document,
            reviewer_metadata_path=arguments.reviewer_metadata,
            output_path=arguments.output,
        )
        exit_code = 0
    except FileExistsError:
        result = {
            "contract": SIGNING_REQUEST_RESULT_CONTRACT,
            "status": "fail",
            "error": {"code": "OUTPUT_ALREADY_EXISTS"},
            "approval_granted": False,
            "signature_created": False,
            "external_action_started": False,
            "target_action_executed": False,
            "gate_passed": False,
            "product_complete": False,
        }
        exit_code = 1
    except (OSError, UnicodeError, json.JSONDecodeError, SigningRequestError) as exc:
        result = {
            "contract": SIGNING_REQUEST_RESULT_CONTRACT,
            "status": "fail",
            "error": {"code": str(exc) or "SIGNING_REQUEST_COMPILE_FAILED"},
            "approval_granted": False,
            "signature_created": False,
            "external_action_started": False,
            "target_action_executed": False,
            "gate_passed": False,
            "product_complete": False,
        }
        exit_code = 1
    print(
        json.dumps(
            result,
            ensure_ascii=False,
            sort_keys=True,
            indent=None if compact else 2,
        )
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "REVIEWER_METADATA_CONTRACT",
    "SIGNING_REQUEST_CONTRACT",
    "SIGNING_REQUEST_RESULT_CONTRACT",
    "SigningRequestError",
    "compile_signing_requests",
    "main",
]
