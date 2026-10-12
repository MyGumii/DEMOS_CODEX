"""Verify the forward-only E0 global-runtime-lock successor.

The historical E0 candidate and winner artifacts stay byte-for-byte frozen.
This verifier accepts only one pinned successor checkpoint proving that a later
SBE-owned application lock did not absorb any CRDT candidate dependency.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = PROJECT_ROOT.parent
CHECKPOINT_PATH = (
    PROJECT_ROOT
    / "docs"
    / "notion_parity"
    / "checkpoints"
    / "ui-internal-collaboration-e0-runtime-lock-sbe-8-13-successor-20260909.json"
)
CHECKPOINT_SHA256 = (
    "sha256:75b325c7f8f358d338c6b0a37894f0cec733bf3ab4ea2fb6bdcf02ab1b4ed0b3"
)
RUNTIME_LOCK_PATH = PROJECT_ROOT / "requirements-runtime.lock"
FROZEN_RUNTIME_LOCK_SHA256 = (
    "sha256:fc5522dcbf576d8dcfbbf51d6349dbc007bc5438dcc396cba913f54aa22f0bd0"
)
CURRENT_RUNTIME_LOCK_SHA256 = (
    "sha256:eb2c677bd3865d81b16dc5018babdd52d01bab39ba0f1e1aa6ad0f5645e19788"
)
CHECKPOINT_CONTRACT = "code_assist.notion.e0.runtime_lock_successor_checkpoint.v1"
VERIFY_CONTRACT = "code_assist.notion.e0.runtime_lock_successor_verification.v1"
CHECKPOINT_ID = (
    "UI-INTERNAL-COLLABORATION-E0-RUNTIME-LOCK-SBE-8-13-SUCCESSOR-20260909"
)
RUNTIME_LOCK_RELATIVE = "code_assist_v1/requirements-runtime.lock"
FORBIDDEN_GLOBAL_PACKAGES = frozenset(
    {
        "@hocuspocus/server",
        "pycrdt",
        "pycrdt-websocket",
        "uvicorn",
        "websockets",
        "yjs",
    }
)
EXPECTED_AUTHORITY_PATHS = {
    "agents_md": "code_assist_v1/AGENTS.md",
    "candidate_matrix": (
        "code_assist_v1/docs/notion_parity/core_free_web_ui/"
        "internal_collaborative_crdt_candidate_matrix.v1.json"
    ),
    "winner_adr": (
        "code_assist_v1/docs/notion_parity/core_free_web_ui/"
        "internal_collaborative_crdt_winner_adr.v1.json"
    ),
    "selected_runtime_artifact_manifest": (
        "code_assist_v1/collaboration_runtime/ARTIFACT_MANIFEST.json"
    ),
    "selected_python_lock": (
        "code_assist_v1/collaboration_runtime/python-requirements.lock"
    ),
    "selected_node_lock": (
        "code_assist_v1/collaboration_runtime/node/package-lock.json"
    ),
}
_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_LOCK_ROW_RE = re.compile(r"^([A-Za-z0-9_.-]+)==([^\s;]+)$")


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


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _repository_file(relative: Any) -> Path | None:
    if not isinstance(relative, str) or not relative or "\\" in relative:
        return None
    candidate = (REPOSITORY_ROOT / relative).resolve(strict=False)
    try:
        candidate.relative_to(REPOSITORY_ROOT.resolve())
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


def _normalized_lock_packages(path: Path) -> list[str]:
    packages: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        row = raw.strip()
        if not row or row.startswith("#"):
            continue
        match = _LOCK_ROW_RE.fullmatch(row)
        if match is None:
            raise ValueError("runtime lock must contain exact name==version rows")
        name = re.sub(r"[-_.]+", "-", match.group(1)).lower()
        if name in packages:
            raise ValueError("duplicate normalized runtime package")
        packages.append(name)
    return sorted(packages)


def _error(code: str, subject: str, message: str) -> dict[str, str]:
    return {"code": code, "subject": subject, "message": message}


def verify(
    checkpoint_path: Path = CHECKPOINT_PATH,
    *,
    runtime_lock_path: Path = RUNTIME_LOCK_PATH,
) -> dict[str, Any]:
    errors: list[dict[str, str]] = []

    def check(condition: bool, code: str, subject: str, message: str) -> None:
        if not condition:
            errors.append(_error(code, subject, message))

    try:
        checkpoint = _load(checkpoint_path)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        return {
            "contract": VERIFY_CONTRACT,
            "status": "fail",
            "error_count": 1,
            "errors": [_error("CHECKPOINT_READ_FAILED", str(checkpoint_path), str(exc))],
            "compatibility_passed": False,
            "new_packet_or_gate_credit_awarded": False,
            "product_complete": False,
        }
    if not isinstance(checkpoint, dict):
        errors.append(_error("CHECKPOINT_SHAPE", "$", "checkpoint must be an object"))
        checkpoint = {}

    required_fields = {
        "schema_version",
        "contract",
        "checkpoint_id",
        "recorded_at",
        "status",
        "immutable_after_recording",
        "authority_bindings",
        "runtime_lock_transition",
        "candidate_isolation",
        "ownership_handoff",
        "claims",
        "record_sha256",
    }
    check(set(checkpoint) == required_fields, "CHECKPOINT_FIELDS", "$", "exact fields required")
    check(
        _sha256_file(checkpoint_path) == CHECKPOINT_SHA256,
        "CHECKPOINT_HASH",
        str(checkpoint_path),
        "caller-pinned checkpoint bytes required",
    )
    check(
        checkpoint.get("schema_version") == 1
        and checkpoint.get("contract") == CHECKPOINT_CONTRACT
        and checkpoint.get("checkpoint_id") == CHECKPOINT_ID
        and checkpoint.get("status") == "validated_unrelated_global_runtime_lock_successor"
        and checkpoint.get("immutable_after_recording") is True,
        "CHECKPOINT_IDENTITY",
        "$",
        "successor identity or status drift",
    )
    check(
        checkpoint.get("record_sha256")
        == _canonical_sha256(
            {key: value for key, value in checkpoint.items() if key != "record_sha256"}
        ),
        "CHECKPOINT_SELF_HASH",
        "$.record_sha256",
        "canonical self hash drift",
    )

    authority = checkpoint.get("authority_bindings")
    check(
        isinstance(authority, dict) and set(authority) == set(EXPECTED_AUTHORITY_PATHS),
        "AUTHORITY_DENOMINATOR",
        "$.authority_bindings",
        "exact authority bindings required",
    )
    if isinstance(authority, dict):
        for label, relative in EXPECTED_AUTHORITY_PATHS.items():
            binding = authority.get(label)
            target = _repository_file(relative)
            check(
                isinstance(binding, dict)
                and set(binding) == {"path", "sha256"}
                and binding.get("path") == relative
                and _SHA256_RE.fullmatch(str(binding.get("sha256"))) is not None
                and target is not None
                and binding.get("sha256") == _sha256_file(target),
                "AUTHORITY_HASH",
                f"$.authority_bindings.{label}",
                "exact current authority bytes required",
            )

    transition = checkpoint.get("runtime_lock_transition")
    try:
        packages = _normalized_lock_packages(runtime_lock_path)
        current_sha256 = _sha256_file(runtime_lock_path)
    except (OSError, UnicodeError, ValueError):
        packages = []
        current_sha256 = None
        errors.append(_error("RUNTIME_LOCK_READ_FAILED", RUNTIME_LOCK_RELATIVE, "exact lock rows required"))
    check(
        isinstance(transition, dict)
        and set(transition)
        == {
            "path",
            "frozen_e0_sha256",
            "current_sha256",
            "changed_since_e0",
            "change_owner",
            "change_reason",
            "exact_version_row_count",
            "normalized_package_names",
        }
        and transition.get("path") == RUNTIME_LOCK_RELATIVE
        and transition.get("frozen_e0_sha256") == FROZEN_RUNTIME_LOCK_SHA256
        and transition.get("current_sha256") == CURRENT_RUNTIME_LOCK_SHA256
        and transition.get("changed_since_e0") is True
        and transition.get("change_owner") == "SBE-8.13"
        and transition.get("exact_version_row_count") == len(packages)
        and transition.get("normalized_package_names") == packages
        and current_sha256 == CURRENT_RUNTIME_LOCK_SHA256,
        "RUNTIME_LOCK_TRANSITION",
        "$.runtime_lock_transition",
        "exact handed-off runtime lock transition required",
    )

    isolation = checkpoint.get("candidate_isolation")
    forbidden_present = sorted(FORBIDDEN_GLOBAL_PACKAGES.intersection(packages))
    check(
        isinstance(isolation, dict)
        and isolation
        == {
            "forbidden_global_package_ids": sorted(FORBIDDEN_GLOBAL_PACKAGES),
            "forbidden_global_package_count": 0,
            "selected_candidate_id": "PY_OPAQUE_NODE_COMPACTOR",
            "selected_runtime_remains_separately_locked": True,
            "global_runtime_is_not_candidate_runtime": True,
        }
        and forbidden_present == [],
        "CANDIDATE_ISOLATION",
        "$.candidate_isolation",
        "CRDT candidate dependencies must remain outside the global runtime lock",
    )

    handoff = checkpoint.get("ownership_handoff")
    check(
        isinstance(handoff, dict)
        and handoff
        == {
            "source_thread_id": "019fec6f-60b0-7f53-876d-ba9b7acb0c51",
            "source_packet": "SBE-8.13",
            "scope": RUNTIME_LOCK_RELATIVE,
            "final_sha256": CURRENT_RUNTIME_LOCK_SHA256,
            "source_will_not_modify_in_current_packet": True,
            "future_change_requires_recoordination": True,
            "host_runtime_activation_proven": False,
            "notion_parity_files_modified_by_source": False,
        },
        "OWNERSHIP_HANDOFF",
        "$.ownership_handoff",
        "exact bounded ownership handoff required",
    )
    check(
        checkpoint.get("claims")
        == {
            "historical_e0_contract_overwritten": False,
            "runtime_lock_reverted": False,
            "candidate_dependency_installed": False,
            "runtime_enabled": False,
            "new_packet_or_gate_credit_awarded": False,
            "product_complete": False,
            "shared_goal_complete": False,
        },
        "CLAIMS",
        "$.claims",
        "successor must not claim execution or completion",
    )
    errors = sorted(errors, key=lambda item: (item["code"], item["subject"], item["message"]))
    status = "pass" if not errors else "fail"
    return {
        "contract": VERIFY_CONTRACT,
        "status": status,
        "error_count": len(errors),
        "errors": errors,
        "compatibility_passed": status == "pass",
        "checkpoint_sha256": _sha256_file(checkpoint_path),
        "frozen_runtime_lock_sha256": FROZEN_RUNTIME_LOCK_SHA256,
        "current_runtime_lock_sha256": current_sha256,
        "runtime_package_count": len(packages),
        "forbidden_global_package_count": len(forbidden_present),
        "selected_runtime_remains_separately_locked": status == "pass",
        "runtime_enabled": False,
        "new_packet_or_gate_credit_awarded": False,
        "product_complete": False,
    }


def accepts_frozen_runtime_lock_binding(relative: str, digest: str) -> bool:
    """Accept only the historical E0 binding plus the pinned clean successor."""

    if relative != RUNTIME_LOCK_RELATIVE or digest != FROZEN_RUNTIME_LOCK_SHA256:
        return False
    report = verify()
    return bool(
        report.get("status") == "pass"
        and report.get("compatibility_passed") is True
        and report.get("current_runtime_lock_sha256") == CURRENT_RUNTIME_LOCK_SHA256
        and report.get("forbidden_global_package_count") == 0
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="verify-e0-runtime-lock-successor")
    parser.add_argument("--checkpoint", type=Path, default=CHECKPOINT_PATH)
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args(argv)
    report = verify(args.checkpoint)
    print(
        json.dumps(
            report,
            ensure_ascii=False,
            sort_keys=True,
            indent=None if args.compact else 2,
        )
    )
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "CHECKPOINT_PATH",
    "CHECKPOINT_SHA256",
    "CURRENT_RUNTIME_LOCK_SHA256",
    "FROZEN_RUNTIME_LOCK_SHA256",
    "RUNTIME_LOCK_RELATIVE",
    "accepts_frozen_runtime_lock_binding",
    "main",
    "verify",
]
