"""Verify the K0-P1 default-off Notion collaboration control plane."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path
from typing import Any, Sequence

from code_assist_v1.notion_collaboration.capability_switches import (
    AGGREGATE_KEY,
    BASE_SCHEMA_VERSION,
    CAPABILITIES,
    COLLABORATION_SCHEMA_VERSION,
    PARALLEL_ARCHIVE_SCHEMA_VERSION,
    PREFIX,
    evaluate_schema_gate,
    load_capability_switches,
)


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_ROOT = PACKAGE_ROOT / "docs" / "notion_parity" / "core_free_web_ui"
CONTRACT_PATH = CONTRACT_ROOT / "internal_collaborative_capability_switch_contract.v1.json"
EXPECTED_CONTRACT = "code_assist.notion.internal_collaborative_capability_switch_contract.k0_p1.v1"


def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in items:
        if key in value:
            raise ValueError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _reject(value: str) -> None:
    raise ValueError(f"non-finite JSON number: {value}")


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_pairs, parse_constant=_reject)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def verify(path: Path = CONTRACT_PATH) -> dict[str, Any]:
    errors: list[dict[str, str]] = []

    def check(condition: bool, code: str, subject: str, message: str) -> None:
        if not condition:
            errors.append({"code": code, "subject": subject, "message": message})

    try:
        value = _load(path)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        return {
            "contract": "code_assist.notion.internal_collaborative_capability_switch_verification.v1",
            "status": "fail",
            "error_count": 1,
            "errors": [{"code": "CONTRACT_READ_FAILED", "subject": str(path), "message": str(exc)}],
        }
    check(isinstance(value, dict), "CONTRACT_SHAPE", "$", "contract must be an object")
    if not isinstance(value, dict):
        return {"status": "fail", "error_count": len(errors), "errors": errors}
    check(value.get("schema_version") == 1 and value.get("contract") == EXPECTED_CONTRACT, "CONTRACT_ID", "$", "contract identity drift")
    check(value.get("packet_id") == "K0-P1" and value.get("status") == "implemented_default_off", "PACKET_STATUS", "$", "K0-P1 implementation status drift")

    prerequisites = value.get("prerequisite_evidence", {})
    check(isinstance(prerequisites, dict) and set(prerequisites) == {"G0", "G0-E", "UI0-P1"}, "PREREQUISITE_DENOMINATOR", "$.prerequisite_evidence", "G0, G0-E, and UI0-P1 required")
    if isinstance(prerequisites, dict):
        for key, item in prerequisites.items():
            if not isinstance(item, dict):
                errors.append({"code": "PREREQUISITE_SHAPE", "subject": key, "message": "prerequisite must be an object"})
                continue
            target = (CONTRACT_ROOT / str(item.get("path"))).resolve()
            check(target.is_file(), "PREREQUISITE_MISSING", key, "prerequisite missing")
            if target.is_file():
                check(_sha256_file(target) == item.get("sha256"), "PREREQUISITE_HASH", key, "prerequisite hash drift")

    implementation = value.get("implementation", {})
    implementation_path = (CONTRACT_ROOT / str(implementation.get("path"))).resolve()
    check(implementation_path.is_file(), "IMPLEMENTATION_MISSING", "$.implementation.path", "implementation missing")
    if implementation_path.is_file():
        check(_sha256_file(implementation_path) == implementation.get("sha256"), "IMPLEMENTATION_HASH", "$.implementation.sha256", "implementation hash drift")
        try:
            tree = ast.parse(implementation_path.read_text(encoding="utf-8"), filename=str(implementation_path))
        except (OSError, UnicodeError, SyntaxError) as exc:
            errors.append({"code": "IMPLEMENTATION_PARSE", "subject": str(implementation_path), "message": str(exc)})
        else:
            ambient_os = any(
                (isinstance(node, ast.Import) and any(alias.name == "os" for alias in node.names))
                or (isinstance(node, ast.ImportFrom) and node.module == "os")
                or (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "os")
                for node in ast.walk(tree)
            )
            check(not ambient_os, "AMBIENT_ENVIRONMENT_READ", "$.implementation", "implementation must not read ambient environment")

    namespace = value.get("namespace_contract", {})
    check(namespace.get("prefix") == PREFIX and namespace.get("aggregate_key") == AGGREGATE_KEY, "NAMESPACE", "$.namespace_contract", "Notion namespace drift")
    check(namespace.get("individual_capability_may_bypass_aggregate") is False and namespace.get("SBE_flag_alias_or_bypass_allowed") is False, "NAMESPACE_BYPASS", "$.namespace_contract", "aggregate or SBE boundary weakened")
    check(value.get("capability_registry") == list(CAPABILITIES), "CAPABILITY_DENOMINATOR", "$.capability_registry", "capability registry drift")
    check(not (set(value.get("non_adopted_capabilities_absent", [])) & set(CAPABILITIES)), "NON_ADOPTED_CAPABILITY", "$.non_adopted_capabilities_absent", "non-adopted capability is registered")

    disabled = load_capability_switches({"CODE_ASSIST_SBE_SUCCESSOR_ENABLED": "true"})
    check(disabled.enabled is False and disabled.state == "disabled", "DEFAULT_OFF", "runtime", "SBE or missing Notion aggregate enabled collaboration")

    schema = value.get("schema_and_rollback_contract", {})
    check((schema.get("base_schema_version"), schema.get("parallel_archive_schema_version"), schema.get("reserved_collaboration_schema_version")) == (BASE_SCHEMA_VERSION, PARALLEL_ARCHIVE_SCHEMA_VERSION, COLLABORATION_SCHEMA_VERSION), "SCHEMA_LINEAGE", "$.schema_and_rollback_contract", "schema lineage drift")
    rollback = evaluate_schema_gate(
        observed_schema_version=COLLABORATION_SCHEMA_VERSION,
        aggregate_enabled=False,
        additive_forward_compatibility_attested=True,
        collaboration_migration_implemented=True,
    )
    check(rollback["legacy_application_may_start"] is True and rollback["collaboration_may_start"] is False and rollback["schema_downgrade_required"] is False, "ROLLBACK_CONTRACT", "$.schema_and_rollback_contract", "forward rollback requires schema downgrade or enables collaboration")

    claims = value.get("claims", {})
    check(isinstance(claims, dict) and claims.get("aggregate_and_capability_switch_contract_implemented") is True and claims.get("default_off") is True and claims.get("SBE_flags_unchanged") is True, "CLAIMS_IMPLEMENTATION", "$.claims", "K0 implementation claim drift")
    if isinstance(claims, dict):
        check(all(claims.get(field) is False for field in ("schema_migration_implemented", "identity_import_executed", "runtime_or_feature_enabled", "service_or_port_started", "live_or_production_data_accessed", "product_complete", "active_shared_goal_complete")), "CLAIMS_FALSE_GREEN", "$.claims", "K0 falsely claims activation or completion")

    return {
        "contract": "code_assist.notion.internal_collaborative_capability_switch_verification.v1",
        "status": "pass" if not errors else "fail",
        "error_count": len(errors),
        "errors": errors,
        "contract_sha256": _sha256_file(path),
        "implementation_sha256": _sha256_file(implementation_path) if implementation_path.is_file() else None,
        "capability_count": len(CAPABILITIES),
        "claims": {
            "K0_P1_implemented_default_off": not errors,
            "runtime_or_feature_enabled": False,
            "schema_migration_implemented": False,
            "product_complete": False,
        },
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract-path", type=Path, default=CONTRACT_PATH)
    parser.add_argument("--compact", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = verify(args.contract_path)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=None if args.compact else 2))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
