"""Compile one immutable, non-authorizing O1B target execution plan.

The compiler accepts only an already reviewed policy and a still-live signed
approval/handoff pair.  It runs the fail-closed pre-execution verifier, binds
the exact raw artifact hashes, and writes a sanitized create-only plan.  It
does not consume the single-use nonce, contact an external system, start a
backup or restore, or make the plan reusable as execution authority.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Sequence

import code_assist_v1.tools.verify_internal_collaborative_target_restore as target_restore_verifier


PLAN_CONTRACT = (
    "code_assist.notion.internal_collaborative_target_execution_plan.o1b_p1.v1"
)
PLAN_RESULT_CONTRACT = (
    "code_assist.notion.internal_collaborative_target_execution_plan_result."
    "o1b_p1.v1"
)
NONCE_BINDING_DOMAIN = "code-assist:o1b-p1:single-use-nonce-binding:v1"
REQUIRED_TRANSITION_ORDER = [
    "fresh_pre_execution_recheck",
    "durable_nonce_compare_exchange_zero_to_one",
    "encrypted_off_host_generation_publish",
    "retention_key_destination_and_acl_validation",
    "distinct_windows_target_new_root_restore",
    "integrity_rpo_rto_and_canary_measurement",
    "twenty_one_artifact_evidence_index_seal",
    "external_reviewer_attestation_and_receipt_registration",
]
_SHA256_RE = re.compile(r"sha256:[0-9a-f]{64}\Z", re.ASCII)


class ExecutionPlanError(ValueError):
    """The execution plan could not be compiled without widening authority."""


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        del message
        raise ExecutionPlanError("invalid_arguments")


def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in items:
        if key in value:
            raise ExecutionPlanError("duplicate_json_key")
        value[key] = item
    return value


def _load_once(path: Path) -> tuple[Any, str]:
    raw = path.read_bytes()
    value = json.loads(
        raw.decode("utf-8"),
        object_pairs_hook=_pairs,
        parse_constant=lambda item: (_ for _ in ()).throw(
            ExecutionPlanError(f"invalid_json_constant:{item}")
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


def _nonce_binding(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise ExecutionPlanError("approval_nonce_missing")
    payload = NONCE_BINDING_DOMAIN.encode("ascii") + b"\x00" + value.encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _observed_at(value: datetime | None) -> tuple[datetime, str]:
    observed = value if value is not None else datetime.now(timezone.utc)
    if observed.tzinfo is None or observed.utcoffset() is None:
        raise ExecutionPlanError("plan_time_not_timezone_aware")
    utc_value = observed.astimezone(timezone.utc)
    return utc_value, utc_value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def compile_execution_plan(
    *,
    policy_path: Path,
    approval_path: Path,
    handoff_path: Path,
    expected_policy_sha256: str,
    output_path: Path,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Write one sanitized plan after verifying exact, live signed authority."""

    if _SHA256_RE.fullmatch(expected_policy_sha256) is None:
        raise ExecutionPlanError("expected_policy_sha256_invalid")
    observed, compiled_at = _observed_at(now)
    preflight = target_restore_verifier.verify_pre_execution(
        policy_path,
        approval_path=approval_path,
        handoff_path=handoff_path,
        expected_policy_sha256=expected_policy_sha256,
        now=observed,
    )
    if not (
        preflight.get("status") == "pass"
        and preflight.get("pre_execution_authorized") is True
        and preflight.get("nonce_consumed") is False
        and preflight.get("external_action_started") is False
        and preflight.get("target_action_executed") is False
        and preflight.get("gate_passed") is False
    ):
        raise ExecutionPlanError("pre_execution_not_authorized")

    policy, policy_sha256 = _load_once(policy_path)
    approval, approval_sha256 = _load_once(approval_path)
    handoff, handoff_sha256 = _load_once(handoff_path)
    if not all(isinstance(value, dict) for value in (policy, approval, handoff)):
        raise ExecutionPlanError("execution_artifact_shape_invalid")
    if not (
        policy_sha256 == expected_policy_sha256 == preflight.get("policy_sha256")
        and approval_sha256 == preflight.get("approval_sha256")
        and handoff_sha256 == preflight.get("handoff_sha256")
    ):
        raise ExecutionPlanError("artifact_changed_after_pre_execution")

    execution_id = approval.get("authorized_execution_id")
    scope_sha256 = approval.get("scope_sha256")
    release_sha256 = approval.get("release_candidate_sha256")
    if not (
        execution_id == preflight.get("authorized_execution_id")
        and scope_sha256 == preflight.get("scope_sha256")
        and release_sha256 == preflight.get("release_candidate_sha256")
        and handoff.get("authorized_execution_id") == execution_id
        and handoff.get("scope_sha256") == scope_sha256
        and handoff.get("release_candidate_sha256") == release_sha256
    ):
        raise ExecutionPlanError("verified_execution_binding_mismatch")

    seed = {
        "compiled_at": compiled_at,
        "policy_sha256": policy_sha256,
        "approval_sha256": approval_sha256,
        "handoff_sha256": handoff_sha256,
        "authorized_execution_id": execution_id,
        "scope_sha256": scope_sha256,
        "release_candidate_sha256": release_sha256,
    }
    plan: dict[str, Any] = {
        "schema_version": 1,
        "contract": PLAN_CONTRACT,
        "plan_id": "O1B-P1-PLAN-" + _sha256_value(seed)[7:23].upper(),
        "compiled_at": compiled_at,
        "immutable_after_creation": True,
        "artifact_bindings": {
            "policy_sha256": policy_sha256,
            "approval_sha256": approval_sha256,
            "handoff_sha256": handoff_sha256,
        },
        "execution_binding": {
            "authorized_execution_id": execution_id,
            "scope_sha256": scope_sha256,
            "release_candidate_sha256": release_sha256,
            "single_use_nonce_binding_sha256": _nonce_binding(
                approval.get("single_use_nonce")
            ),
            "approval_expires_at": approval.get("expires_at"),
            "handoff_expires_at": handoff.get("expires_at"),
        },
        "required_transition_order": REQUIRED_TRANSITION_ORDER,
        "execution_invariants": {
            "plan_is_execution_authority": False,
            "fresh_pre_execution_recheck_before_nonce_cas": True,
            "nonce_compare_exchange_required": True,
            "same_volume_backup_allowed": False,
            "distinct_windows_target_required": True,
            "production_restore_authorized": False,
            "raw_locator_or_secret_embedded": False,
            "post_execution_evidence_artifact_count": 21,
            "post_execution_verifier_contract": (
                target_restore_verifier.AUDIT_CONTRACT
            ),
        },
        "claims": {
            "nonce_consumed": False,
            "external_action_started": False,
            "target_backup_restore_executed": False,
            "gate_passed": False,
            "production_restore_executed": False,
            "product_complete": False,
            "active_shared_goal_complete": False,
        },
    }
    plan["plan_sha256"] = _sha256_value(plan)
    payload = _pretty_bytes(plan)

    # Confirm the three authority artifacts are still byte-identical immediately
    # before publishing the create-only plan.
    if not (
        _load_once(policy_path)[1] == policy_sha256
        and _load_once(approval_path)[1] == approval_sha256
        and _load_once(handoff_path)[1] == handoff_sha256
    ):
        raise ExecutionPlanError("artifact_changed_before_plan_publish")

    output_path.parent.resolve(strict=True)
    with output_path.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    return {
        "contract": PLAN_RESULT_CONTRACT,
        "status": "pass",
        "plan_id": plan["plan_id"],
        "plan_sha256": "sha256:" + hashlib.sha256(payload).hexdigest(),
        "record_sha256": plan["plan_sha256"],
        "pre_execution_authorized_at_compile_time": True,
        "fresh_pre_execution_recheck_required": True,
        "nonce_consumed": False,
        "external_action_started": False,
        "target_action_executed": False,
        "gate_passed": False,
        "product_complete": False,
    }


def _parser() -> _Parser:
    parser = _Parser(prog="compile-internal-collaborative-target-execution-plan")
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--approval", type=Path, required=True)
    parser.add_argument("--handoff", type=Path, required=True)
    parser.add_argument("--expected-policy-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--compact", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    compact = False
    try:
        arguments = _parser().parse_args(argv)
        compact = arguments.compact
        result = compile_execution_plan(
            policy_path=arguments.policy,
            approval_path=arguments.approval,
            handoff_path=arguments.handoff,
            expected_policy_sha256=arguments.expected_policy_sha256,
            output_path=arguments.output,
        )
        exit_code = 0
    except FileExistsError:
        result = {
            "contract": PLAN_RESULT_CONTRACT,
            "status": "fail",
            "error": {"code": "OUTPUT_ALREADY_EXISTS"},
            "nonce_consumed": False,
            "external_action_started": False,
            "target_action_executed": False,
            "gate_passed": False,
            "product_complete": False,
        }
        exit_code = 1
    except (OSError, UnicodeError, json.JSONDecodeError, ExecutionPlanError) as exc:
        result = {
            "contract": PLAN_RESULT_CONTRACT,
            "status": "fail",
            "error": {"code": str(exc) or "EXECUTION_PLAN_COMPILE_FAILED"},
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
    "ExecutionPlanError",
    "NONCE_BINDING_DOMAIN",
    "PLAN_CONTRACT",
    "PLAN_RESULT_CONTRACT",
    "REQUIRED_TRANSITION_ORDER",
    "compile_execution_plan",
    "main",
]
