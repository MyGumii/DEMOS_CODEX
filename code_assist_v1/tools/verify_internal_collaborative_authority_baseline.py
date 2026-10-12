"""Verify the checked-in A0B-P1 canonical-authority baseline contract."""

from __future__ import annotations

import argparse
import hashlib
import inspect
import json
from pathlib import Path
import sys
from typing import Any, Sequence

from code_assist_v1 import notion_workspace_collaboration_migration as migration
from code_assist_v1 import notion_workspace_store as workspace_store
from code_assist_v1.notion_collaboration import canonical_authority, cutover, legacy_snapshot


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = PACKAGE_ROOT.parent
DEFAULT_CONTRACT = (
    PACKAGE_ROOT
    / "docs/notion_parity/core_free_web_ui/internal_collaborative_authority_baseline.v1.json"
)
SUCCESSOR_CONTRACT_PATH = (
    PACKAGE_ROOT
    / "docs/notion_parity/core_free_web_ui/internal_collaborative_editor_ui.v1.json"
)
REPORT_SCHEMA_VERSION = 1
EXPECTED_STORE_SHA256 = "sha256:19de52d282b6d3adbad2aa72a128a56f9525fdb7f82748138d4e1a009705a979"
SUCCESSOR_OWNED_PATH = "code_assist_v1/notion_collaboration/canonical_authority.py"


def _duplicates(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _load(path: Path) -> Any:
    return json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=_duplicates,
        parse_constant=lambda value: (_ for _ in ()).throw(
            ValueError(f"non-finite number: {value}")
        ),
    )


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _successor_owns_current_artifact(relative: str, target: Path) -> bool:
    if relative != SUCCESSOR_OWNED_PATH:
        return False
    try:
        successor = _load(SUCCESSOR_CONTRACT_PATH)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        return False
    if not isinstance(successor, dict):
        return False
    binding = next(
        (
            item
            for item in successor.get("source_manifest", [])
            if isinstance(item, dict) and item.get("path") == relative
        ),
        None,
    )
    return bool(
        successor.get("packet_id") == "E1-P2"
        and successor.get("status") == "implemented_and_validated"
        and successor.get("claims", {}).get("E1_P2_gate_passed") is True
        and isinstance(binding, dict)
        and binding.get("sha256") == _sha256(target)
    )


def verify(contract_path: Path = DEFAULT_CONTRACT) -> dict[str, Any]:
    errors: list[dict[str, str]] = []

    def check(condition: bool, code: str, subject: str, message: str) -> None:
        if not condition:
            errors.append({"code": code, "subject": subject, "message": message})

    try:
        value = _load(contract_path)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        return {
            "report_schema_version": REPORT_SCHEMA_VERSION,
            "status": "fail",
            "error_count": 1,
            "errors": [
                {"code": "CONTRACT_READ_FAILED", "subject": str(contract_path), "message": str(exc)}
            ],
        }
    check(isinstance(value, dict), "CONTRACT_SHAPE", "$", "contract object required")
    if not isinstance(value, dict):
        value = {}
    check(value.get("schema_version") == 1, "SCHEMA_VERSION", "$.schema_version", "schema_version must be 1")
    check(
        value.get("contract") == "code_assist.notion.internal_collaborative_authority_baseline.v1",
        "CONTRACT_ID",
        "$.contract",
        "contract ID mismatch",
    )
    check(value.get("packet_id") == "A0B-P1", "PACKET_ID", "$.packet_id", "packet mismatch")
    check(
        value.get("status") == "implemented_and_locally_gate_verified_default_off",
        "STATUS",
        "$.status",
        "A0B status mismatch",
    )

    lineage = value.get("workspace_schema_lineage", {})
    observed_lineage = (
        migration.SOURCE_VERSION,
        migration.PARALLEL_RESERVED_VERSION,
        migration.TARGET_VERSION,
        migration.SOURCE_CHECKSUM,
        migration.SOURCE_ACTUAL_SCHEMA_SHA256,
        migration.TARGET_CHECKSUM,
        migration.TARGET_ACTUAL_SCHEMA_SHA256,
        len(migration.SOURCE_OBJECTS),
        len(migration.TARGET_OBJECTS),
    )
    declared_lineage = (
        lineage.get("source_version"),
        lineage.get("parallel_reserved_version"),
        lineage.get("target_version"),
        lineage.get("source_checksum"),
        lineage.get("source_actual_schema_sha256"),
        lineage.get("target_checksum"),
        lineage.get("target_actual_schema_sha256"),
        lineage.get("source_object_count"),
        lineage.get("target_object_count"),
    )
    check(
        declared_lineage == observed_lineage,
        "SCHEMA_LINEAGE",
        "$.workspace_schema_lineage",
        f"declared={declared_lineage!r}, observed={observed_lineage!r}",
    )
    check(
        workspace_store.SCHEMA_VERSION == migration.SOURCE_VERSION == 2026081317,
        "FROZEN_SOURCE_VERSION",
        "notion_workspace_store.py",
        "historical runtime source version changed",
    )
    check(
        _sha256(PACKAGE_ROOT / "notion_workspace_store.py") == EXPECTED_STORE_SHA256,
        "FROZEN_SOURCE_HASH",
        "notion_workspace_store.py",
        "historical workspace store changed",
    )
    check(
        migration.PARALLEL_RESERVED_VERSION == 2026081318
        and migration.TARGET_VERSION == 2026082401
        and migration.TARGET_VERSION != migration.PARALLEL_RESERVED_VERSION,
        "SCHEMA_COLLISION",
        "$.workspace_schema_lineage",
        "parallel archive reservation collided",
    )
    check(
        lineage.get("parallel_reserved_version") == 2026081318
        and lineage.get("target_version") == 2026082401
        and lineage.get("parallel_reserved_version") != lineage.get("target_version"),
        "SCHEMA_COLLISION",
        "$.workspace_schema_lineage",
        "declared parallel archive reservation collided",
    )
    check(
        lineage.get("historical_schema_or_migration_modified") is False
        and lineage.get("parallel_2026081318_reused") is False,
        "HISTORICAL_SCHEMA_CLAIM",
        "$.workspace_schema_lineage",
        "historical or parallel schema claim weakened",
    )

    objects = {item["name"] for item in migration.TARGET_OBJECTS}
    required_objects = value.get("required_schema_objects", [])
    check(
        isinstance(required_objects, list)
        and len(required_objects) == len(set(required_objects))
        and set(required_objects).issubset(objects),
        "REQUIRED_OBJECTS",
        "$.required_schema_objects",
        "required schema object is missing or duplicated",
    )
    check(
        "workspace_collaboration_documents" not in {item["name"] for item in migration.SOURCE_OBJECTS},
        "SOURCE_EXTENSION_LEAK",
        "migration.SOURCE_OBJECTS",
        "collaboration objects leaked into historical source",
    )

    migration_contract = value.get("migration_contract", {})
    for key in (
        "forward_only",
        "single_explicit_migrator",
        "exact_dry_run_checksum_required",
        "exact_offline_backup_required",
        "catalog_quiescence_required",
        "source_and_target_object_contracts_exact",
        "idempotent_already_current_plan",
    ):
        check(migration_contract.get(key) is True, "MIGRATION_GUARD", f"$.migration_contract.{key}", "required migration guard missing")
    check(
        migration_contract.get("application_request_path_may_apply") is False
        and migration_contract.get("schema_downgrade_for_code_rollback") is False,
        "MIGRATION_BOUNDARY",
        "$.migration_contract",
        "request-path apply or downgrade was claimed",
    )

    authority_contract = value.get("canonical_authority_contract", {})
    check(
        authority_contract.get("separate_from_central_SBE_database") is True
        and authority_contract.get("service_layer_only_mutations") is True
        and authority_contract.get("all_effects_commit_or_rollback_together") is True,
        "AUTHORITY_BOUNDARY",
        "$.canonical_authority_contract",
        "canonical transaction boundary weakened",
    )
    check(
        (
            authority_contract.get("request_time_directory_scan_count"),
            authority_contract.get("legacy_source_database_open_count"),
            authority_contract.get("dual_write_count"),
            authority_contract.get("post_revoke_old_epoch_protected_payload_count"),
        )
        == (0, 0, 0, 0),
        "ZERO_BOUNDARIES",
        "$.canonical_authority_contract",
        "directory, legacy-open, dual-write, or revoke leak count is nonzero",
    )

    importer = value.get("legacy_import_contract", {})
    check(
        importer.get("live_source_import") is False
        and importer.get("source_database_or_user_data_root_parameter") is False
        and importer.get("source_bytes_immutable") is True
        and importer.get("source_and_canonical_content_set_hash_equal") is True,
        "IMPORT_BOUNDARY",
        "$.legacy_import_contract",
        "legacy import boundary weakened",
    )
    snapshot_parameters = inspect.signature(legacy_snapshot.write_sanitized_snapshot).parameters
    check(
        not ({"source_root", "database_path", "user_data_root"} & set(snapshot_parameters)),
        "IMPORT_SOURCE_API",
        "write_sanitized_snapshot",
        "snapshot builder accepts a protected source",
    )

    cutover_contract = value.get("cutover_and_projection_contract", {})
    check(
        cutover_contract.get("aggregate_default_off") is True
        and cutover_contract.get("development_realm_only") is True
        and cutover_contract.get("exact_subject_and_workspace_canary") is True
        and cutover_contract.get("legacy_fallback_inside_candidate_resolver") is False
        and cutover_contract.get("compatibility_dual_write") is False
        and cutover_contract.get("production_cutover_authorized") is False,
        "CUTOVER_BOUNDARY",
        "$.cutover_and_projection_contract",
        "default-off development cutover boundary weakened",
    )
    resolver_source = inspect.getsource(cutover.DevelopmentCanonicalResolver)
    authority_source = inspect.getsource(canonical_authority.CanonicalAuthority)
    for forbidden in ("os.environ", "os.listdir", ".glob(", ".rglob(", "notion.db"):
        check(
            forbidden not in resolver_source and forbidden not in authority_source,
            "REQUEST_DISCOVERY_API",
            "canonical runtime source",
            f"forbidden request-time API found: {forbidden}",
        )

    hashes = value.get("artifact_sha256", {})
    check(isinstance(hashes, dict) and len(hashes) == 8, "ARTIFACT_SET", "$.artifact_sha256", "exact eight artifacts required")
    if isinstance(hashes, dict):
        for relative, expected in hashes.items():
            path = REPOSITORY_ROOT / str(relative)
            try:
                observed = _sha256(path)
            except OSError as exc:
                errors.append({"code": "ARTIFACT_READ_FAILED", "subject": str(relative), "message": str(exc)})
                continue
            check(
                observed == expected
                or _successor_owns_current_artifact(str(relative), path),
                "ARTIFACT_HASH",
                str(relative),
                f"expected {expected}, observed {observed}",
            )

    validation = value.get("validation_contract", {})
    check(
        validation.get("validation_ids")
        == [
            "L2-WORKSPACE-SUBSTRATE-BASELINE",
            "L2-FORWARD-MIGRATION-BASELINE",
            "L2-COLLAB-AUTHORITY",
        ],
        "VALIDATION_IDS",
        "$.validation_contract.validation_ids",
        "validation lane set drifted",
    )
    check(
        validation.get("workspace_and_forward_regression", {}).get("passed") == 225
        and validation.get("prototype_and_versioned_authority", {}).get("passed") == 31
        and validation.get("prototype_and_versioned_authority", {}).get("includes_versioned_A0B_tests") == 14
        and validation.get("synthetic_temporary_databases_only") is True
        and validation.get("live_or_existing_database_count") == 0
        and validation.get("service_or_port_start_count") == 0,
        "VALIDATION_RECEIPT",
        "$.validation_contract",
        "local validation receipt drifted",
    )
    closure = value.get("matrix_closure", {})
    check(
        closure.get("row_ids") == ["DATA-001", "DATA-002"]
        and closure.get("gate_ids") == ["A0-GATE-001"]
        and all(item is True for item in closure.get("DATA_001", {}).values())
        and all(item is True for item in closure.get("DATA_002", {}).values()),
        "MATRIX_CLOSURE",
        "$.matrix_closure",
        "DATA-001/DATA-002 closure evidence incomplete",
    )
    claims = value.get("claims", {})
    check(
        claims.get("A0B_P1_implemented") is True
        and claims.get("A0B_P1_locally_gate_verified") is True
        and claims.get("migration_applied_only_to_synthetic_temporary_catalogs") is True,
        "A0B_CLAIMS",
        "$.claims",
        "A0B implementation claim missing",
    )
    for key in (
        "actual_10111_or_10112_identity_import_executed",
        "actual_dev_pair_cutover_enabled",
        "live_workspace_schema_changed",
        "runtime_or_feature_enabled",
        "service_or_port_started",
        "production_changed",
        "product_complete",
    ):
        check(claims.get(key) is False, "FALSE_GREEN", f"$.claims.{key}", "forbidden completion or mutation claim")

    return {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "status": "pass" if not errors else "fail",
        "error_count": len(errors),
        "errors": errors,
        "packet_id": "A0B-P1",
        "source_schema_version": migration.SOURCE_VERSION,
        "target_schema_version": migration.TARGET_VERSION,
        "target_object_count": len(migration.TARGET_OBJECTS),
        "matrix_row_ids": ["DATA-001", "DATA-002"],
        "product_complete": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    args = parser.parse_args(argv)
    report = verify(args.contract)
    json.dump(report, sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
