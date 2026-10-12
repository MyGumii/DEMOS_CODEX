"""Assemble externally signed O1B approval and handoff artifacts.

The assembler loads no private key and performs no target action.  It accepts
one caller-pinned signing-request bundle plus two detached Ed25519 signature
responses, verifies each response against the policy-bound public key, and
creates a new directory containing the sealed approval, handoff, and an
immutable manifest.  Existing output is never overwritten.
"""

from __future__ import annotations

import argparse
import base64
import binascii
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

from code_assist_v1.tools.compile_internal_collaborative_target_signing_requests import (
    SIGNING_REQUEST_CONTRACT,
)
import code_assist_v1.tools.verify_internal_collaborative_target_restore as target_restore_verifier


SIGNATURE_RESPONSE_CONTRACT = (
    "code_assist.notion.internal_collaborative_target_signature_response."
    "o1b_p1.v1"
)
AUTHORITY_MANIFEST_CONTRACT = (
    "code_assist.notion.internal_collaborative_target_authority_manifest."
    "o1b_p1.v1"
)
ASSEMBLY_RESULT_CONTRACT = (
    "code_assist.notion.internal_collaborative_target_authority_assembly_result."
    "o1b_p1.v1"
)
_SHA256_RE = re.compile(r"sha256:[0-9a-f]{64}\Z", re.ASCII)
_OPAQUE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z", re.ASCII)


class AuthorityAssemblyError(ValueError):
    """A signature response or its bound request failed closed."""


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        del message
        raise AuthorityAssemblyError("invalid_arguments")


def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in items:
        if key in value:
            raise AuthorityAssemblyError("duplicate_json_key")
        value[key] = item
    return value


def _load_once(path: Path) -> tuple[Any, str]:
    raw = path.read_bytes()
    value = json.loads(
        raw.decode("utf-8"),
        object_pairs_hook=_pairs,
        parse_constant=lambda item: (_ for _ in ()).throw(
            AuthorityAssemblyError(f"invalid_json_constant:{item}")
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


def _sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _anchor(policy: Mapping[str, Any], role: str) -> Mapping[str, Any]:
    trust = policy.get("trust_anchor_policy")
    anchor = trust.get(role) if isinstance(trust, dict) else None
    if not (
        isinstance(anchor, dict)
        and set(anchor) == {"key_id", "public_key_base64", "public_key_sha256"}
    ):
        raise AuthorityAssemblyError(f"bound_{role}_trust_anchor_required")
    return anchor


def _message(value: Any, *, subject: str) -> bytes:
    if not isinstance(value, dict) or set(value) != {
        "canonicalization",
        "message_sha256",
        "message_byte_count",
        "message_base64",
    }:
        raise AuthorityAssemblyError(f"{subject}_message_shape_invalid")
    encoded = value.get("message_base64")
    try:
        decoded = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError, binascii.Error) as exc:
        raise AuthorityAssemblyError(f"{subject}_message_base64_invalid") from exc
    if not (
        value.get("canonicalization") == "json-utf8-sort-keys-compact-no-nan-v1"
        and value.get("message_sha256") == _sha256_bytes(decoded)
        and value.get("message_byte_count") == len(decoded)
    ):
        raise AuthorityAssemblyError(f"{subject}_message_binding_invalid")
    return decoded


def _signature_response(
    value: Any,
    *,
    request_id: Any,
    role: str,
    key_id: Any,
    message_sha256: str,
) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != {
        "schema_version",
        "contract",
        "request_id",
        "role",
        "key_id",
        "algorithm",
        "message_sha256",
        "signature_base64",
    }:
        raise AuthorityAssemblyError(f"{role}_signature_response_shape_invalid")
    if not (
        value.get("schema_version") == 1
        and value.get("contract") == SIGNATURE_RESPONSE_CONTRACT
        and value.get("request_id") == request_id
        and value.get("role") == role
        and value.get("key_id") == key_id
        and value.get("algorithm") == "ed25519"
        and value.get("message_sha256") == message_sha256
        and isinstance(value.get("signature_base64"), str)
    ):
        raise AuthorityAssemblyError(f"{role}_signature_response_binding_invalid")
    return {
        "key_id": value["key_id"],
        "algorithm": value["algorithm"],
        "signature_base64": value["signature_base64"],
    }


def _validate_request(
    value: Any,
    *,
    policy: Mapping[str, Any],
    policy_sha256: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], bytes, bytes]:
    if not isinstance(value, dict) or set(value) != {
        "schema_version",
        "contract",
        "request_id",
        "immutable_after_creation",
        "policy_binding",
        "source_bindings",
        "approval_request",
        "handoff_request",
        "assembly_contract",
        "claims",
        "request_sha256",
    }:
        raise AuthorityAssemblyError("signing_request_shape_invalid")
    unsigned = {key: item for key, item in value.items() if key != "request_sha256"}
    if not (
        value.get("schema_version") == 1
        and value.get("contract") == SIGNING_REQUEST_CONTRACT
        and isinstance(value.get("request_id"), str)
        and _OPAQUE_ID_RE.fullmatch(value["request_id"]) is not None
        and value.get("immutable_after_creation") is True
        and value.get("request_sha256") == _sha256_value(unsigned)
    ):
        raise AuthorityAssemblyError("signing_request_identity_invalid")
    approval_anchor = _anchor(policy, "approval")
    handoff_anchor = _anchor(policy, "handoff")
    policy_binding = value.get("policy_binding")
    if policy_binding != {
        "policy_sha256": policy_sha256,
        "approval_key_id": approval_anchor["key_id"],
        "approval_public_key_sha256": approval_anchor["public_key_sha256"],
        "handoff_key_id": handoff_anchor["key_id"],
        "handoff_public_key_sha256": handoff_anchor["public_key_sha256"],
    }:
        raise AuthorityAssemblyError("signing_request_policy_binding_invalid")
    if value.get("assembly_contract") != {
        "approval_signature_inserted_before_approval_self_hash": True,
        "handoff_attestation_signature_inserted_before_handoff_self_hash": True,
        "signature_encoding": "raw-base64",
        "private_key_material_allowed": False,
        "assembled_artifacts_require_fresh_pre_execution_verification": True,
    }:
        raise AuthorityAssemblyError("signing_request_assembly_contract_invalid")
    claims = value.get("claims")
    if not isinstance(claims, dict) or any(item is not False for item in claims.values()):
        raise AuthorityAssemblyError("signing_request_claims_invalid")

    approval_request = value.get("approval_request")
    handoff_request = value.get("handoff_request")
    if not isinstance(approval_request, dict) or set(approval_request) != {
        "document",
        "signature_algorithm",
        "signature_key_id",
        "signing_message",
    }:
        raise AuthorityAssemblyError("approval_request_shape_invalid")
    if not isinstance(handoff_request, dict) or set(handoff_request) != {
        "document",
        "evidence_document_sha256",
        "reviewer_attestation_payload",
        "signature_algorithm",
        "signature_key_id",
        "signing_message",
    }:
        raise AuthorityAssemblyError("handoff_request_shape_invalid")
    approval_document = approval_request.get("document")
    handoff_document = handoff_request.get("document")
    attestation_payload = handoff_request.get("reviewer_attestation_payload")
    approval_message = _message(
        approval_request.get("signing_message"), subject="approval"
    )
    handoff_message = _message(
        handoff_request.get("signing_message"), subject="handoff"
    )
    if not (
        isinstance(approval_document, dict)
        and isinstance(handoff_document, dict)
        and isinstance(attestation_payload, dict)
        and approval_request.get("signature_algorithm") == "ed25519"
        and approval_request.get("signature_key_id") == approval_anchor["key_id"]
        and handoff_request.get("signature_algorithm") == "ed25519"
        and handoff_request.get("signature_key_id") == handoff_anchor["key_id"]
        and approval_message == _canonical_bytes(approval_document)
        and handoff_message == _canonical_bytes(attestation_payload)
        and handoff_request.get("evidence_document_sha256")
        == _sha256_value(handoff_document)
        and attestation_payload.get("evidence_document_sha256")
        == _sha256_value(handoff_document)
    ):
        raise AuthorityAssemblyError("signing_request_message_binding_invalid")
    return (
        deepcopy(approval_document),
        deepcopy(handoff_document),
        deepcopy(attestation_payload),
        approval_message,
        handoff_message,
    )


def assemble_authority(
    *,
    policy_path: Path,
    expected_policy_sha256: str,
    signing_request_path: Path,
    expected_signing_request_sha256: str,
    approval_signature_path: Path,
    handoff_signature_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Verify detached signatures and publish sealed artifacts create-only."""

    if _SHA256_RE.fullmatch(expected_policy_sha256) is None:
        raise AuthorityAssemblyError("expected_policy_sha256_invalid")
    if _SHA256_RE.fullmatch(expected_signing_request_sha256) is None:
        raise AuthorityAssemblyError("expected_signing_request_sha256_invalid")
    policy, policy_sha256 = _load_once(policy_path)
    request, request_file_sha256 = _load_once(signing_request_path)
    approval_response, approval_response_sha256 = _load_once(approval_signature_path)
    handoff_response, handoff_response_sha256 = _load_once(handoff_signature_path)
    if not isinstance(policy, dict) or policy_sha256 != expected_policy_sha256:
        raise AuthorityAssemblyError("reviewed_policy_hash_mismatch")
    if request_file_sha256 != expected_signing_request_sha256:
        raise AuthorityAssemblyError("signing_request_file_hash_mismatch")
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
        raise AuthorityAssemblyError("bound_policy_readiness_invalid")
    (
        approval_document,
        handoff_document,
        attestation_payload,
        approval_message,
        handoff_message,
    ) = _validate_request(request, policy=policy, policy_sha256=policy_sha256)
    approval_anchor = _anchor(policy, "approval")
    handoff_anchor = _anchor(policy, "handoff")
    approval_signature = _signature_response(
        approval_response,
        request_id=request["request_id"],
        role="approval",
        key_id=approval_anchor["key_id"],
        message_sha256=_sha256_bytes(approval_message),
    )
    handoff_signature = _signature_response(
        handoff_response,
        request_id=request["request_id"],
        role="handoff",
        key_id=handoff_anchor["key_id"],
        message_sha256=_sha256_bytes(handoff_message),
    )
    if not target_restore_verifier._verify_ed25519(  # noqa: SLF001
        signature=approval_signature["signature_base64"],
        message=approval_message,
        anchor=approval_anchor,
        key_id=approval_signature["key_id"],
    ):
        raise AuthorityAssemblyError("approval_signature_invalid")
    if not target_restore_verifier._verify_ed25519(  # noqa: SLF001
        signature=handoff_signature["signature_base64"],
        message=handoff_message,
        anchor=handoff_anchor,
        key_id=handoff_signature["key_id"],
    ):
        raise AuthorityAssemblyError("handoff_signature_invalid")

    approval = approval_document
    approval["signature"] = approval_signature
    approval["receipt_sha256"] = _sha256_value(approval)
    handoff = handoff_document
    handoff["reviewer_attestation"] = {
        **attestation_payload,
        "signature_base64": handoff_signature["signature_base64"],
        "private_key_material_in_receipt": False,
    }
    handoff["receipt_sha256"] = _sha256_value(handoff)
    approval_errors: list[dict[str, str]] = []
    target_restore_verifier._verify_approval(  # noqa: SLF001
        approval,
        policy=policy,
        errors=approval_errors,
    )
    handoff_errors: list[dict[str, str]] = []
    target_restore_verifier._verify_handoff(  # noqa: SLF001
        handoff,
        policy=policy,
        approval=approval,
        errors=handoff_errors,
    )
    if approval_errors:
        raise AuthorityAssemblyError("assembled_approval_verification_failed")
    if handoff_errors:
        raise AuthorityAssemblyError("assembled_handoff_verification_failed")

    approval_payload = _pretty_bytes(approval)
    handoff_payload = _pretty_bytes(handoff)
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "contract": AUTHORITY_MANIFEST_CONTRACT,
        "request_id": request["request_id"],
        "policy_sha256": policy_sha256,
        "signing_request_sha256": request_file_sha256,
        "approval_signature_response_sha256": approval_response_sha256,
        "handoff_signature_response_sha256": handoff_response_sha256,
        "approval_file": "approval.json",
        "approval_sha256": _sha256_bytes(approval_payload),
        "handoff_file": "handoff.json",
        "handoff_sha256": _sha256_bytes(handoff_payload),
        "assembly_invariants": {
            "private_key_loaded": False,
            "detached_signatures_verified": True,
            "approval_self_hash_verified": True,
            "handoff_self_hash_verified": True,
            "fresh_pre_execution_verification_required": True,
        },
        "claims": {
            "nonce_consumed": False,
            "external_action_started": False,
            "target_action_executed": False,
            "gate_passed": False,
            "production_restore_executed": False,
            "product_complete": False,
        },
    }
    manifest["manifest_sha256"] = _sha256_value(manifest)
    manifest_payload = _pretty_bytes(manifest)

    if not (
        _load_once(policy_path)[1] == policy_sha256
        and _load_once(signing_request_path)[1] == request_file_sha256
        and _load_once(approval_signature_path)[1] == approval_response_sha256
        and _load_once(handoff_signature_path)[1] == handoff_response_sha256
    ):
        raise AuthorityAssemblyError("source_changed_before_authority_publish")

    output_dir.parent.resolve(strict=True)
    output_dir.mkdir(exist_ok=False)
    created: list[Path] = []
    try:
        for name, payload in (
            ("approval.json", approval_payload),
            ("handoff.json", handoff_payload),
            ("authority-manifest.json", manifest_payload),
        ):
            target = output_dir / name
            with target.open("xb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            created.append(target)
    except BaseException:
        for target in reversed(created):
            target.unlink(missing_ok=True)
        output_dir.rmdir()
        raise
    return {
        "contract": ASSEMBLY_RESULT_CONTRACT,
        "status": "pass",
        "request_id": request["request_id"],
        "approval_sha256": manifest["approval_sha256"],
        "handoff_sha256": manifest["handoff_sha256"],
        "manifest_file_sha256": _sha256_bytes(manifest_payload),
        "manifest_record_sha256": manifest["manifest_sha256"],
        "detached_signatures_verified": True,
        "private_key_loaded": False,
        "nonce_consumed": False,
        "external_action_started": False,
        "target_action_executed": False,
        "gate_passed": False,
        "product_complete": False,
    }


def _parser() -> _Parser:
    parser = _Parser(prog="assemble-internal-collaborative-target-authority")
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--expected-policy-sha256", required=True)
    parser.add_argument("--signing-request", type=Path, required=True)
    parser.add_argument("--expected-signing-request-sha256", required=True)
    parser.add_argument("--approval-signature", type=Path, required=True)
    parser.add_argument("--handoff-signature", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--compact", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    compact = False
    try:
        arguments = _parser().parse_args(argv)
        compact = arguments.compact
        result = assemble_authority(
            policy_path=arguments.policy,
            expected_policy_sha256=arguments.expected_policy_sha256,
            signing_request_path=arguments.signing_request,
            expected_signing_request_sha256=(
                arguments.expected_signing_request_sha256
            ),
            approval_signature_path=arguments.approval_signature,
            handoff_signature_path=arguments.handoff_signature,
            output_dir=arguments.output_dir,
        )
        exit_code = 0
    except FileExistsError:
        result = {
            "contract": ASSEMBLY_RESULT_CONTRACT,
            "status": "fail",
            "error": {"code": "OUTPUT_ALREADY_EXISTS"},
            "private_key_loaded": False,
            "nonce_consumed": False,
            "external_action_started": False,
            "target_action_executed": False,
            "gate_passed": False,
            "product_complete": False,
        }
        exit_code = 1
    except (OSError, UnicodeError, json.JSONDecodeError, AuthorityAssemblyError) as exc:
        result = {
            "contract": ASSEMBLY_RESULT_CONTRACT,
            "status": "fail",
            "error": {"code": str(exc) or "AUTHORITY_ASSEMBLY_FAILED"},
            "private_key_loaded": False,
            "nonce_consumed": False,
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
    "ASSEMBLY_RESULT_CONTRACT",
    "AUTHORITY_MANIFEST_CONTRACT",
    "AuthorityAssemblyError",
    "SIGNATURE_RESPONSE_CONTRACT",
    "assemble_authority",
    "main",
]
