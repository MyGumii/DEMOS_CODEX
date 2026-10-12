"""Create-only compiler for an O1B target-specific public trust policy.

This tool performs no backup, restore, network, credential, signing, approval,
or activation action.  It accepts only public trust anchors plus an exact
release candidate, verifies the current pending readiness policy, and emits one
bound successor policy for independent review.  The successor still cannot
open the O1B gate until its exact hash is deliberately pinned in the verifier
and fresh signed approval/handoff/evidence artifacts are supplied.
"""

from __future__ import annotations

import argparse
import base64
import binascii
from copy import deepcopy
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Mapping, Sequence

from code_assist_v1.tools.verify_internal_collaborative_target_restore import (
    POLICY_PATH,
    verify,
)


BINDING_INPUT_CONTRACT = (
    "code_assist.notion.internal_collaborative_target_policy_binding_input."
    "o1b_p1.v1"
)
_ROLES = ("approval", "handoff", "evidence_index", "receipt_reviewer")
_SHA256_RE = re.compile(r"sha256:[0-9a-f]{64}\Z", re.ASCII)
_OPAQUE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z", re.ASCII)
_RELEASE_KEYS = {
    "build_sha256",
    "configuration_sha256",
    "catalog_sha256",
    "schema_sha256",
    "sqlite_version",
    "sqlite_source_id_sha256",
    "successor_flags_default_off",
    "one_durable_worker",
    "one_projector",
}


class PolicyBindingError(ValueError):
    """A public policy-binding input failed its exact contract."""


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        del message
        raise PolicyBindingError("invalid_arguments")


def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        if key in result:
            raise PolicyBindingError("duplicate_json_key")
        result[key] = value
    return result


def _load_once(path: Path) -> tuple[Any, str]:
    raw = path.read_bytes()
    value = json.loads(
        raw.decode("utf-8"),
        object_pairs_hook=_pairs,
        parse_constant=lambda item: (_ for _ in ()).throw(
            PolicyBindingError(f"invalid_json_constant:{item}")
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


def _sha(value: Any) -> bool:
    return isinstance(value, str) and _SHA256_RE.fullmatch(value) is not None


def _opaque_id(value: Any) -> bool:
    return isinstance(value, str) and _OPAQUE_ID_RE.fullmatch(value) is not None


def _canonical_bound_at(value: Any) -> bool:
    if not isinstance(value, str) or value.endswith("Z"):
        return False
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return False
    return (
        parsed.tzinfo is not None
        and parsed.utcoffset() is not None
        and parsed.isoformat(timespec="seconds") == value
    )


def _public_anchor(value: Any) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != {
        "key_id",
        "public_key_base64",
        "public_key_sha256",
    }:
        raise PolicyBindingError("public_anchor_shape_invalid")
    key_id = value.get("key_id")
    encoded = value.get("public_key_base64")
    expected_hash = value.get("public_key_sha256")
    if not _opaque_id(key_id) or not isinstance(encoded, str) or not _sha(expected_hash):
        raise PolicyBindingError("public_anchor_identity_invalid")
    try:
        public_key = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise PolicyBindingError("public_anchor_encoding_invalid") from exc
    if len(public_key) != 32:
        raise PolicyBindingError("public_anchor_length_invalid")
    actual_hash = "sha256:" + hashlib.sha256(public_key).hexdigest()
    if expected_hash != actual_hash:
        raise PolicyBindingError("public_anchor_hash_mismatch")
    return {
        "key_id": key_id,
        "public_key_base64": encoded,
        "public_key_sha256": expected_hash,
    }


def _release_candidate(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != _RELEASE_KEYS:
        raise PolicyBindingError("release_candidate_shape_invalid")
    if not all(
        _sha(value.get(name))
        for name in (
            "build_sha256",
            "configuration_sha256",
            "catalog_sha256",
            "schema_sha256",
            "sqlite_source_id_sha256",
        )
    ):
        raise PolicyBindingError("release_candidate_hash_invalid")
    if not _opaque_id(value.get("sqlite_version")):
        raise PolicyBindingError("release_candidate_sqlite_version_invalid")
    if any(
        value.get(name) is not True
        for name in (
            "successor_flags_default_off",
            "one_durable_worker",
            "one_projector",
        )
    ):
        raise PolicyBindingError("release_candidate_topology_invalid")
    return dict(value)


def compile_bound_policy(
    base_policy: Mapping[str, Any], binding_input: Any
) -> dict[str, Any]:
    if not isinstance(binding_input, dict) or set(binding_input) != {
        "schema_version",
        "contract",
        "binding_record_id",
        "bound_at",
        "release_candidate",
        "trust_anchors",
    }:
        raise PolicyBindingError("binding_input_shape_invalid")
    if not (
        binding_input.get("schema_version") == 1
        and binding_input.get("contract") == BINDING_INPUT_CONTRACT
        and _opaque_id(binding_input.get("binding_record_id"))
        and _canonical_bound_at(binding_input.get("bound_at"))
    ):
        raise PolicyBindingError("binding_input_identity_invalid")
    anchors_value = binding_input.get("trust_anchors")
    if not isinstance(anchors_value, dict) or set(anchors_value) != set(_ROLES):
        raise PolicyBindingError("trust_anchor_denominator_invalid")
    anchors = {role: _public_anchor(anchors_value[role]) for role in _ROLES}
    if len({anchor["key_id"] for anchor in anchors.values()}) != len(_ROLES):
        raise PolicyBindingError("trust_anchor_key_id_not_distinct")
    if len({anchor["public_key_sha256"] for anchor in anchors.values()}) != len(_ROLES):
        raise PolicyBindingError("trust_anchor_public_key_not_distinct")
    release = _release_candidate(binding_input.get("release_candidate"))

    policy = deepcopy(dict(base_policy))
    trust = policy.get("trust_anchor_policy")
    if not isinstance(trust, dict) or not (
        trust.get("status") == "pending"
        and trust.get("authorized_release_candidate_sha256") is None
        and all(trust.get(role) is None for role in _ROLES)
    ):
        raise PolicyBindingError("base_policy_not_pending")
    policy["recorded_at"] = binding_input["bound_at"]
    trust.update(
        {
            "status": "bound",
            "authorized_release_candidate_sha256": _sha256_value(release),
            **anchors,
        }
    )
    return policy


def bind_policy(
    *,
    base_policy_path: Path,
    expected_base_policy_sha256: str,
    binding_input_path: Path,
    output_path: Path,
) -> dict[str, Any]:
    if not _sha(expected_base_policy_sha256):
        raise PolicyBindingError("expected_base_policy_sha256_invalid")
    base_policy, base_sha256 = _load_once(base_policy_path)
    if base_sha256 != expected_base_policy_sha256:
        raise PolicyBindingError("base_policy_hash_mismatch")
    binding_input, _ = _load_once(binding_input_path)
    if not isinstance(base_policy, dict):
        raise PolicyBindingError("base_policy_shape_invalid")
    bound_policy = compile_bound_policy(base_policy, binding_input)
    payload = _pretty_bytes(bound_policy)

    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", suffix=".json", delete=False
        ) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
            temporary_path = Path(handle.name)
        candidate_report = verify(temporary_path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    if not (
        candidate_report.get("status") == "pass"
        and candidate_report.get("readiness_contract_passed") is True
        and candidate_report.get("gate_passed") is False
    ):
        raise PolicyBindingError("bound_policy_readiness_invalid")

    output_path.parent.resolve(strict=True)
    with output_path.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    output_sha256 = "sha256:" + hashlib.sha256(payload).hexdigest()
    return {
        "contract": "code_assist.notion.internal_collaborative_target_policy_binding_result.o1b_p1.v1",
        "status": "pass",
        "base_policy_sha256": base_sha256,
        "binding_record_id": binding_input["binding_record_id"],
        "bound_policy_sha256": output_sha256,
        "authorized_release_candidate_sha256": bound_policy[
            "trust_anchor_policy"
        ]["authorized_release_candidate_sha256"],
        "trust_anchor_count": len(_ROLES),
        "policy_review_required": True,
        "code_pin_activation_required": True,
        "approval_required": True,
        "handoff_required": True,
        "target_action_executed": False,
        "gate_passed": False,
        "product_complete": False,
    }


def _parser() -> _Parser:
    parser = _Parser(prog="bind-internal-collaborative-target-policy")
    parser.add_argument("--base-policy", type=Path, default=POLICY_PATH)
    parser.add_argument("--expected-base-policy-sha256", required=True)
    parser.add_argument("--binding-input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--compact", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    compact = False
    try:
        arguments = _parser().parse_args(argv)
        compact = arguments.compact
        result = bind_policy(
            base_policy_path=arguments.base_policy,
            expected_base_policy_sha256=arguments.expected_base_policy_sha256,
            binding_input_path=arguments.binding_input,
            output_path=arguments.output,
        )
        exit_code = 0
    except FileExistsError:
        result = {
            "status": "fail",
            "error": {"code": "OUTPUT_ALREADY_EXISTS"},
            "target_action_executed": False,
            "gate_passed": False,
        }
        exit_code = 1
    except (OSError, UnicodeError, json.JSONDecodeError, PolicyBindingError) as exc:
        result = {
            "status": "fail",
            "error": {"code": str(exc) or "POLICY_BINDING_FAILED"},
            "target_action_executed": False,
            "gate_passed": False,
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
    "BINDING_INPUT_CONTRACT",
    "PolicyBindingError",
    "bind_policy",
    "compile_bound_policy",
    "main",
]
