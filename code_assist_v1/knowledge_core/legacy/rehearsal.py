"""Deterministic P2.8 rehearsal over two disposable canonical targets.

The runner consumes one exact reviewed plan and two already-migrated, empty
rehearsal targets.  It applies and verifies the primary target, proves an exact
same-target replay, applies the same plan to a distinct fresh target, and
requires the complete strict verification artifacts to match.  It never
creates a source snapshot, migrates schema, writes an evidence file, or touches
feature flags.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import ntpath
from pathlib import Path
import re
from typing import Any

from ..db import ConnectionFactory
from ..errors import KnowledgeCoreError
from ..migrations import CANONICAL_MIGRATION_CATALOG, verify_canonical_integrity
from ..migrations.storage_preflight import (
    MigrationStorageBoundary,
    MigrationStorageLayout,
    verify_migration_storage_layout,
)
from ..migrations.v001_seed import RESPONSIBILITY_ROOT_ID
from ..migrations.v010_semantic_graph_contracts import (
    SEMANTIC_RELATION_TYPE_SEED_ROWS,
)
from .importer import _reviewed_plan
from .migration_apply import (
    MAX_CONTENT_EFFECTS_PER_TRANSACTION,
    LegacyMigrationApplyResult,
    LegacyMigrationApplyService,
)
from .migration_verify import (
    LegacyMigrationVerifier,
    verification_contract_errors,
)
from .plan_contract import sha256_canonical


REHEARSAL_REPORT_SCHEMA = "code-assist.legacy-migration-rehearsal"
REHEARSAL_SCHEMA_VERSION = 2

_SHA_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_TOP_KEYS = frozenset(
    {
        "report_schema",
        "schema_version",
        "stage",
        "plan",
        "execution",
        "runs",
        "targets",
        "reproducibility",
        "verification",
        "rehearsal_sha256",
    }
)
_EXECUTION_KEYS = frozenset({"batch_size"})
_TARGET_KEYS = frozenset(
    {
        "catalog_checksum_sha256",
        "database_file_identity_sha256",
        "schema_version",
        "storage_layout_sha256",
    }
)
_TARGET_NAMES = frozenset({"fresh", "primary"})
_PLAN_KEYS = frozenset(
    {
        "inventory_document_sha256",
        "inventory_manifest_sha256",
        "organization_mapping_document_sha256",
        "plan_sha256",
        "reviewed_plan_document_sha256",
        "review_attestation_sha256",
        "root_binding_sha256",
        "snapshot_attestation_sha256",
        "snapshot_id",
        "snapshot_kind",
        "technical_plan_sha256",
    }
)
_RUN_KEYS = frozenset({"apply", "verification_sha256"})
_RUN_NAMES = frozenset({"primary_first", "primary_rerun", "fresh_first"})
_APPLY_KEYS = frozenset(
    {
        "content_batch_count",
        "imported_record_count",
        "imported_relationship_count",
        "imported_user_count",
        "inserted_mapping_count",
        "p24_batch_count",
        "p25_batch_count",
    }
)
_REPRODUCIBILITY_KEYS = frozenset(
    {
        "canonical_targets_sha256",
        "fresh_target_equal",
        "fts_expected_sha256",
        "observed_records_sha256",
        "observed_relationships_sha256",
        "primary_rerun_zero_insertions",
        "projection_spaces_sha256",
        "verification_sha256",
    }
)


class LegacyMigrationRehearsalError(KnowledgeCoreError):
    code = "LEGACY_MIGRATION_REHEARSAL_FAILED"


class LegacyMigrationRehearsalPlanError(LegacyMigrationRehearsalError):
    code = "LEGACY_MIGRATION_REHEARSAL_PLAN_INVALID"


class LegacyMigrationRehearsalTargetError(LegacyMigrationRehearsalError):
    code = "LEGACY_MIGRATION_REHEARSAL_TARGET_INVALID"


class LegacyMigrationRehearsalMismatchError(LegacyMigrationRehearsalError):
    code = "LEGACY_MIGRATION_REHEARSAL_MISMATCH"


@dataclass(frozen=True, slots=True)
class LegacyMigrationRehearsalTarget:
    factory: ConnectionFactory
    layout: MigrationStorageLayout


def _hash_payload(value: Mapping[str, object]) -> dict[str, object]:
    payload = dict(value)
    payload.pop("rehearsal_sha256", None)
    return payload


def rehearsal_sha256(value: Mapping[str, object]) -> str:
    return sha256_canonical(_hash_payload(value))


def _norm_path(value: str | Path) -> str:
    return ntpath.normcase(ntpath.normpath(str(value).replace("/", "\\")))


def _paths_overlap(left: str, right: str) -> bool:
    left = _norm_path(left)
    right = _norm_path(right)
    try:
        return ntpath.commonpath((left, right)) in {left, right}
    except ValueError:
        return False


def _apply_summary(result: LegacyMigrationApplyResult) -> dict[str, int]:
    return {
        "content_batch_count": len(result.content_batches),
        "imported_record_count": result.imported_record_count,
        "imported_relationship_count": result.imported_relationship_count,
        "imported_user_count": result.identity.imported_user_count,
        "inserted_mapping_count": result.inserted_mapping_count,
        "p24_batch_count": len(result.p24_batches)
        + len(result.p24_history_batches),
        "p25_batch_count": len(result.p25_batches)
        + len(result.p25_notion_batches),
    }


def _target_boundary(target: LegacyMigrationRehearsalTarget) -> MigrationStorageBoundary:
    if not isinstance(target, LegacyMigrationRehearsalTarget):
        raise TypeError("target must be LegacyMigrationRehearsalTarget.")
    if not isinstance(target.factory, ConnectionFactory):
        raise TypeError("target factory must be ConnectionFactory.")
    if not isinstance(target.layout, MigrationStorageLayout):
        raise TypeError("target layout must be MigrationStorageLayout.")
    catalog = target.factory.migration_catalog
    if (
        catalog.latest_version != CANONICAL_MIGRATION_CATALOG.latest_version
        or catalog.checksum_sha256
        != CANONICAL_MIGRATION_CATALOG.checksum_sha256
    ):
        raise LegacyMigrationRehearsalTargetError(
            "A rehearsal target does not use the canonical migration catalog."
        )
    try:
        boundary = verify_migration_storage_layout(
            target.layout, require_database=True
        )
    except KnowledgeCoreError as exc:
        raise LegacyMigrationRehearsalTargetError(
            "A rehearsal target storage boundary is invalid."
        ) from exc
    if _norm_path(target.factory.database_path) != _norm_path(
        boundary.database.resolved_path
    ):
        raise LegacyMigrationRehearsalTargetError(
            "A rehearsal target factory and layout bind different databases."
        )
    return boundary


def rehearsal_target_binding(
    target: LegacyMigrationRehearsalTarget,
) -> dict[str, object]:
    """Return a path-body-free binding for one existing canonical target."""

    boundary = _target_boundary(target)
    identity = boundary.database_file_identity
    if identity is None:
        raise LegacyMigrationRehearsalTargetError(
            "A rehearsal target database identity is unavailable."
        )
    return {
        "catalog_checksum_sha256": target.factory.migration_catalog.checksum_sha256,
        "database_file_identity_sha256": sha256_canonical(list(identity)),
        "schema_version": target.factory.migration_catalog.latest_version,
        "storage_layout_sha256": sha256_canonical(
            {
                "blob_root": boundary.blob_root.resolved_path,
                "data_root": boundary.data_root.resolved_path,
                "database": boundary.database.resolved_path,
                "projection_root": boundary.projection_root.resolved_path,
            }
        ),
    }


def _targets_are_distinct(
    primary: MigrationStorageBoundary,
    fresh: MigrationStorageBoundary,
) -> bool:
    primary_paths = {
        primary.data_root.resolved_path,
        primary.database.resolved_path,
        primary.blob_root.resolved_path,
        primary.projection_root.resolved_path,
    }
    fresh_paths = {
        fresh.data_root.resolved_path,
        fresh.database.resolved_path,
        fresh.blob_root.resolved_path,
        fresh.projection_root.resolved_path,
    }
    for left in primary_paths:
        for right in fresh_paths:
            if _paths_overlap(left, right):
                return False
    return True


def verify_empty_rehearsal_target(
    target: LegacyMigrationRehearsalTarget,
) -> None:
    """Require one complete current-catalog bootstrap with no migration effects."""

    _target_boundary(target)
    connection = target.factory.open()
    try:
        try:
            verify_canonical_integrity(
                connection, target.factory.migration_catalog
            )
        except KnowledgeCoreError as exc:
            raise LegacyMigrationRehearsalTargetError(
                "A rehearsal target is not a complete canonical schema."
            ) from exc
        tables = [
            str(row["name"])
            for row in connection.execute(
                "SELECT name FROM sqlite_schema "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        has_v9_native_spaces = "higher_scope_role_assignment" in tables
        has_v10_semantic_relations = "semantic_relation_type" in tables
        dirty: list[str] = []
        for table in tables:
            if table == "schema_migration" or table.startswith("knowledge_fts_"):
                continue
            count = int(
                connection.execute(
                    f'SELECT count(*) AS n FROM "{table}"'
                ).fetchone()["n"]
            )
            expected = (
                1 if table == "org_unit"
                or (has_v9_native_spaces and table == "knowledge_space")
                else len(SEMANTIC_RELATION_TYPE_SEED_ROWS)
                if has_v10_semantic_relations and table == "semantic_relation_type"
                else 0
            )
            if count != expected:
                dirty.append(table)
        root = connection.execute(
            "SELECT id,kind,parent_id,status,row_version FROM org_unit"
        ).fetchone()
        if (
            root is None
            or root["id"] != RESPONSIBILITY_ROOT_ID
            or root["kind"] != "responsibility"
            or root["parent_id"] is not None
            or root["status"] != "active"
            or root["row_version"] != 1
        ):
            dirty.append("org_unit")
        if has_v9_native_spaces:
            native_root = connection.execute(
                """SELECT id,space_kind,owner_user_id,owner_org_unit_id,status,
                          write_enabled,generation,row_version
                     FROM knowledge_space"""
            ).fetchone()
            if (
                native_root is None
                or native_root["id"] != RESPONSIBILITY_ROOT_ID
                or native_root["space_kind"] != "responsibility"
                or native_root["owner_user_id"] is not None
                or native_root["owner_org_unit_id"] != RESPONSIBILITY_ROOT_ID
                or native_root["status"] != "active"
                or native_root["write_enabled"] != 0
                or native_root["generation"] != 0
                or native_root["row_version"] != 1
            ):
                dirty.append("knowledge_space")
        if has_v10_semantic_relations:
            semantic_relations = tuple(
                tuple(row)
                for row in connection.execute(
                    """SELECT relation_type,vocabulary_version,direction_policy,
                              active,created_at,row_version
                         FROM semantic_relation_type ORDER BY relation_type"""
                )
            )
            if semantic_relations != SEMANTIC_RELATION_TYPE_SEED_ROWS:
                dirty.append("semantic_relation_type")
        if dirty:
            raise LegacyMigrationRehearsalTargetError(
                "A rehearsal target is not an empty canonical bootstrap: "
                + ",".join(sorted(set(dirty)))
            )
    finally:
        connection.close()


def rehearsal_plan_binding(
    plan: Mapping[str, object], *, reviewed_plan: bytes
) -> dict[str, object]:
    draft = plan.get("draft")
    review = plan.get("review")
    if not isinstance(draft, Mapping) or not isinstance(review, Mapping):
        raise LegacyMigrationRehearsalPlanError(
            "The reviewed rehearsal plan is invalid."
        )
    inputs = draft.get("inputs")
    if not isinstance(inputs, Mapping):
        raise LegacyMigrationRehearsalPlanError(
            "The reviewed rehearsal plan inputs are invalid."
        )
    inventory = inputs.get("inventory")
    organization = inputs.get("organization_mapping")
    if not isinstance(inventory, Mapping) or not isinstance(organization, Mapping):
        raise LegacyMigrationRehearsalPlanError(
            "The reviewed rehearsal plan bindings are invalid."
        )
    return {
        "inventory_document_sha256": inventory["document_sha256"],
        "inventory_manifest_sha256": inventory["manifest_sha256"],
        "organization_mapping_document_sha256": organization["document_sha256"],
        "plan_sha256": plan["plan_sha256"],
        "reviewed_plan_document_sha256": hashlib.sha256(reviewed_plan).hexdigest(),
        "review_attestation_sha256": review["review_attestation_sha256"],
        "root_binding_sha256": inventory["root_binding_sha256"],
        "snapshot_attestation_sha256": inventory[
            "snapshot_attestation_sha256"
        ],
        "snapshot_id": inventory["snapshot_id"],
        "snapshot_kind": inventory["snapshot_kind"],
        "technical_plan_sha256": draft["technical_plan_sha256"],
    }


def rehearsal_contract_errors(
    value: object,
    *,
    verify_hash: bool = True,
    reviewed_plan: bytes | None = None,
) -> tuple[str, ...]:
    """Return stable errors for any JSON-shaped rehearsal artifact."""

    if not isinstance(value, Mapping) or frozenset(value) != _TOP_KEYS:
        return ("rehearsal_shape",)
    errors: list[str] = []
    if (
        value.get("report_schema") != REHEARSAL_REPORT_SCHEMA
        or value.get("schema_version") != REHEARSAL_SCHEMA_VERSION
        or value.get("stage") != "frozen_source_rehearsal"
    ):
        errors.append("rehearsal_header")
    plan = value.get("plan")
    if (
        not isinstance(plan, Mapping)
        or frozenset(plan) != _PLAN_KEYS
        or not all(
            isinstance(plan.get(key), str)
            and _SHA_RE.fullmatch(str(plan.get(key))) is not None
            for key in _PLAN_KEYS
            if key not in {"snapshot_id", "snapshot_kind"}
        )
        or not isinstance(plan.get("snapshot_id"), str)
        or not str(plan.get("snapshot_id"))
        or plan.get("snapshot_kind") not in {"frozen_copy", "vss"}
    ):
        errors.append("rehearsal_plan")
    execution = value.get("execution")
    if (
        not isinstance(execution, Mapping)
        or frozenset(execution) != _EXECUTION_KEYS
        or not isinstance(execution.get("batch_size"), int)
        or isinstance(execution.get("batch_size"), bool)
        or not 1
        <= int(execution.get("batch_size", 0))
        <= MAX_CONTENT_EFFECTS_PER_TRANSACTION
    ):
        errors.append("rehearsal_execution")
    runs = value.get("runs")
    valid_runs = isinstance(runs, Mapping) and frozenset(runs) == _RUN_NAMES
    if valid_runs:
        for run in runs.values():
            if not isinstance(run, Mapping) or frozenset(run) != _RUN_KEYS:
                valid_runs = False
                break
            apply = run.get("apply")
            if (
                not isinstance(apply, Mapping)
                or frozenset(apply) != _APPLY_KEYS
                or any(
                    not isinstance(apply.get(key), int)
                    or isinstance(apply.get(key), bool)
                    or int(apply.get(key, -1)) < 0
                    for key in _APPLY_KEYS
                )
                or not isinstance(run.get("verification_sha256"), str)
                or _SHA_RE.fullmatch(str(run.get("verification_sha256"))) is None
            ):
                valid_runs = False
                break
    if not valid_runs:
        errors.append("rehearsal_runs")
    targets = value.get("targets")
    valid_targets = isinstance(targets, Mapping) and frozenset(targets) == _TARGET_NAMES
    if valid_targets:
        for target in targets.values():
            if (
                not isinstance(target, Mapping)
                or frozenset(target) != _TARGET_KEYS
                or target.get("schema_version")
                != CANONICAL_MIGRATION_CATALOG.latest_version
                or target.get("catalog_checksum_sha256")
                != CANONICAL_MIGRATION_CATALOG.checksum_sha256
                or any(
                    not isinstance(target.get(key), str)
                    or _SHA_RE.fullmatch(str(target.get(key))) is None
                    for key in _TARGET_KEYS
                    if key != "schema_version"
                )
            ):
                valid_targets = False
                break
        if valid_targets and (
            targets["primary"]["storage_layout_sha256"]
            == targets["fresh"]["storage_layout_sha256"]
            or targets["primary"]["database_file_identity_sha256"]
            == targets["fresh"]["database_file_identity_sha256"]
            or targets["primary"]["catalog_checksum_sha256"]
            != targets["fresh"]["catalog_checksum_sha256"]
        ):
            valid_targets = False
    if not valid_targets:
        errors.append("rehearsal_targets")
    verification = value.get("verification")
    verification_errors = verification_contract_errors(verification)
    if verification_errors:
        errors.append("rehearsal_verification")
    reproducibility = value.get("reproducibility")
    if (
        not isinstance(reproducibility, Mapping)
        or frozenset(reproducibility) != _REPRODUCIBILITY_KEYS
        or reproducibility.get("fresh_target_equal") is not True
        or reproducibility.get("primary_rerun_zero_insertions") is not True
        or any(
            not isinstance(reproducibility.get(key), str)
            or _SHA_RE.fullmatch(str(reproducibility.get(key))) is None
            for key in _REPRODUCIBILITY_KEYS
            if key
            not in {"fresh_target_equal", "primary_rerun_zero_insertions"}
        )
    ):
        errors.append("rehearsal_reproducibility")
    if (
        valid_runs
        and not verification_errors
        and isinstance(verification, Mapping)
        and isinstance(reproducibility, Mapping)
    ):
        verification_hash = verification.get("verification_sha256")
        primary_first = runs["primary_first"]["apply"]
        primary_rerun = runs["primary_rerun"]["apply"]
        fresh_first = runs["fresh_first"]["apply"]
        expected_replay = dict(primary_first)
        expected_replay["inserted_mapping_count"] = 0
        reconciliation = verification.get("reconciliation")
        rebuild = verification.get("rebuild")
        if (
            any(
                run["verification_sha256"] != verification_hash
                for run in runs.values()
            )
            or fresh_first != primary_first
            or primary_rerun != expected_replay
            or not isinstance(reconciliation, Mapping)
            or not isinstance(rebuild, Mapping)
            or reproducibility.get("verification_sha256") != verification_hash
            or reproducibility.get("canonical_targets_sha256")
            != reconciliation.get("canonical_targets_sha256")
            or reproducibility.get("observed_records_sha256")
            != reconciliation.get("observed_records_sha256")
            or reproducibility.get("observed_relationships_sha256")
            != reconciliation.get("observed_relationships_sha256")
            or reproducibility.get("projection_spaces_sha256")
            != rebuild.get("projection_spaces_sha256")
            or reproducibility.get("fts_expected_sha256")
            != rebuild.get("expected_sha256")
        ):
            errors.append("rehearsal_reproducibility_binding")
        if isinstance(plan, Mapping):
            verification_plan = verification.get("plan")
            if (
                not isinstance(verification_plan, Mapping)
                or plan.get("plan_sha256")
                != verification_plan.get("plan_sha256")
                or plan.get("inventory_manifest_sha256")
                != verification_plan.get("inventory_manifest_sha256")
                or plan.get("root_binding_sha256")
                != verification_plan.get("root_binding_sha256")
                or plan.get("technical_plan_sha256")
                != verification_plan.get("technical_plan_sha256")
                or plan.get("review_attestation_sha256")
                != verification_plan.get("review_attestation_sha256")
            ):
                errors.append("rehearsal_plan_binding")
    if verify_hash:
        try:
            valid_hash = (
                isinstance(value.get("rehearsal_sha256"), str)
                and _SHA_RE.fullmatch(str(value.get("rehearsal_sha256")))
                is not None
                and value.get("rehearsal_sha256") == rehearsal_sha256(value)
            )
        except (TypeError, ValueError):
            valid_hash = False
        if not valid_hash:
            errors.append("rehearsal_hash")
    if reviewed_plan is not None:
        if not isinstance(reviewed_plan, bytes) or not isinstance(plan, Mapping):
            errors.append("rehearsal_plan_document_binding")
        else:
            try:
                reviewed = _reviewed_plan(
                    reviewed_plan,
                    expected_plan_sha256=str(plan.get("plan_sha256")),
                )
                expected_plan_binding = rehearsal_plan_binding(
                    reviewed,
                    reviewed_plan=reviewed_plan,
                )
            except (KnowledgeCoreError, KeyError, TypeError, ValueError):
                errors.append("rehearsal_plan_document_binding")
            else:
                if dict(plan) != expected_plan_binding:
                    errors.append("rehearsal_plan_document_binding")
    return tuple(sorted(set(errors)))


class LegacyMigrationRehearsal:
    """Apply, replay, and reproduce one reviewed plan on two empty targets."""

    __slots__ = ("_fresh", "_primary")

    def __init__(
        self,
        primary: LegacyMigrationRehearsalTarget,
        fresh: LegacyMigrationRehearsalTarget,
    ) -> None:
        primary_boundary = _target_boundary(primary)
        fresh_boundary = _target_boundary(fresh)
        if not _targets_are_distinct(primary_boundary, fresh_boundary):
            raise LegacyMigrationRehearsalTargetError(
                "Primary and fresh rehearsal targets must be disjoint."
            )
        self._primary = primary
        self._fresh = fresh

    def run(
        self,
        reviewed_plan: bytes,
        *,
        expected_plan_sha256: str,
        batch_size: int = 100,
    ) -> dict[str, object]:
        if (
            not isinstance(batch_size, int)
            or isinstance(batch_size, bool)
            or not 1 <= batch_size <= MAX_CONTENT_EFFECTS_PER_TRANSACTION
        ):
            raise LegacyMigrationRehearsalPlanError(
                "batch_size must be an integer from 1 through 100."
            )
        if not isinstance(reviewed_plan, bytes):
            raise TypeError("reviewed_plan must be canonical artifact bytes.")
        try:
            plan = _reviewed_plan(
                reviewed_plan, expected_plan_sha256=expected_plan_sha256
            )
        except KnowledgeCoreError as exc:
            raise LegacyMigrationRehearsalPlanError(
                "The reviewed rehearsal plan is invalid."
            ) from exc
        try:
            plan_binding = rehearsal_plan_binding(
                plan, reviewed_plan=reviewed_plan
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise LegacyMigrationRehearsalPlanError(
                "The reviewed rehearsal plan bindings are invalid."
            ) from exc
        verify_empty_rehearsal_target(self._primary)
        verify_empty_rehearsal_target(self._fresh)
        target_bindings = {
            "fresh": rehearsal_target_binding(self._fresh),
            "primary": rehearsal_target_binding(self._primary),
        }

        primary_apply = LegacyMigrationApplyService(
            self._primary.factory, self._primary.layout
        )
        primary_verify = LegacyMigrationVerifier(
            self._primary.factory, self._primary.layout
        )
        first_result = primary_apply.apply(
            reviewed_plan,
            expected_plan_sha256=expected_plan_sha256,
            batch_size=batch_size,
        )
        first_verification = primary_verify.verify(
            reviewed_plan, expected_plan_sha256=expected_plan_sha256
        )
        rerun_result = primary_apply.apply(
            reviewed_plan,
            expected_plan_sha256=expected_plan_sha256,
            batch_size=batch_size,
        )
        rerun_verification = primary_verify.verify(
            reviewed_plan, expected_plan_sha256=expected_plan_sha256
        )
        if rerun_result.inserted_mapping_count != 0:
            raise LegacyMigrationRehearsalMismatchError(
                "The primary rehearsal replay inserted a new mapping."
            )
        if rerun_verification != first_verification:
            raise LegacyMigrationRehearsalMismatchError(
                "The primary rehearsal replay changed strict verification."
            )

        fresh_apply = LegacyMigrationApplyService(
            self._fresh.factory, self._fresh.layout
        )
        fresh_verify = LegacyMigrationVerifier(
            self._fresh.factory, self._fresh.layout
        )
        fresh_result = fresh_apply.apply(
            reviewed_plan,
            expected_plan_sha256=expected_plan_sha256,
            batch_size=batch_size,
        )
        fresh_verification = fresh_verify.verify(
            reviewed_plan, expected_plan_sha256=expected_plan_sha256
        )
        if _apply_summary(fresh_result) != _apply_summary(first_result):
            raise LegacyMigrationRehearsalMismatchError(
                "The fresh rehearsal target produced different apply counts."
            )
        if fresh_verification != first_verification:
            raise LegacyMigrationRehearsalMismatchError(
                "The fresh rehearsal target produced different strict verification."
            )

        verification_hash = str(first_verification["verification_sha256"])
        reconciliation = first_verification["reconciliation"]
        rebuild = first_verification["rebuild"]
        assert isinstance(reconciliation, Mapping)
        assert isinstance(rebuild, Mapping)
        report: dict[str, object] = {
            "report_schema": REHEARSAL_REPORT_SCHEMA,
            "schema_version": REHEARSAL_SCHEMA_VERSION,
            "stage": "frozen_source_rehearsal",
            "plan": plan_binding,
            "execution": {"batch_size": batch_size},
            "runs": {
                "primary_first": {
                    "apply": _apply_summary(first_result),
                    "verification_sha256": verification_hash,
                },
                "primary_rerun": {
                    "apply": _apply_summary(rerun_result),
                    "verification_sha256": verification_hash,
                },
                "fresh_first": {
                    "apply": _apply_summary(fresh_result),
                    "verification_sha256": verification_hash,
                },
            },
            "targets": target_bindings,
            "reproducibility": {
                "canonical_targets_sha256": reconciliation[
                    "canonical_targets_sha256"
                ],
                "fresh_target_equal": True,
                "fts_expected_sha256": rebuild["expected_sha256"],
                "observed_records_sha256": reconciliation[
                    "observed_records_sha256"
                ],
                "observed_relationships_sha256": reconciliation[
                    "observed_relationships_sha256"
                ],
                "primary_rerun_zero_insertions": True,
                "projection_spaces_sha256": rebuild[
                    "projection_spaces_sha256"
                ],
                "verification_sha256": verification_hash,
            },
            "verification": first_verification,
            "rehearsal_sha256": "",
        }
        report["rehearsal_sha256"] = rehearsal_sha256(report)
        errors = rehearsal_contract_errors(
            report,
            reviewed_plan=reviewed_plan,
        )
        if errors:
            raise LegacyMigrationRehearsalMismatchError(
                "The frozen rehearsal report does not close."
            )
        return report


__all__ = [
    "LegacyMigrationRehearsal",
    "LegacyMigrationRehearsalError",
    "LegacyMigrationRehearsalMismatchError",
    "LegacyMigrationRehearsalPlanError",
    "LegacyMigrationRehearsalTarget",
    "LegacyMigrationRehearsalTargetError",
    "REHEARSAL_REPORT_SCHEMA",
    "REHEARSAL_SCHEMA_VERSION",
    "rehearsal_contract_errors",
    "rehearsal_plan_binding",
    "rehearsal_sha256",
    "rehearsal_target_binding",
    "verify_empty_rehearsal_target",
]
