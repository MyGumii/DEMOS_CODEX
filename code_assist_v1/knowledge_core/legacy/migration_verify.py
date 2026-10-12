"""Strict P2.6 observed-outcome verifier and transactional FTS rebuild.

The verifier consumes one exact reviewed plan and an already-applied canonical
database.  It never reinterprets planner policy: every reviewed record and
relationship receives the same observed terminal class, imported effects must
have exactly one immutable target-verified checkpoint, and every non-import
effect must have none.  Content-addressed files are reopened no-follow and
hashed outside the canonical write transaction.  Only the rebuildable
``search_document``/FTS projection is mutated.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
import hashlib
import json
import re
from typing import Any

from ..db import ConnectionFactory, ManagedConnection, transaction
from ..errors import KnowledgeCoreError
from ..migrations import (
    verify_canonical_integrity,
    verify_canonical_integrity_before_projection_rebuild,
)
from ..migrations.storage_preflight import (
    MigrationStorageBoundary,
    MigrationStorageLayout,
    verify_migration_storage_layout,
)
from ..projection.materializer import (
    FullProjectionMaterializer,
    PROJECTION_NAME,
    ProjectionMaterializationError,
)
from ..repositories.migration import (
    LegacyMigrationRelationshipRepository,
    LegacyMigrationRepository,
    VerifiedBlobEvidence,
    migration_mapping_id_for_record,
    migration_relationship_mapping_id_for_plan,
    migration_run_id_for_plan,
    migration_source_locator,
)
from ..repositories.migration_p24 import p24_relationship_mapping_id
from ..repositories.migration_p24 import (
    LegacyP24ContainerDetails,
    LegacyP24ContainerTarget,
    LegacyP24GraphDetails,
    LegacyP24GraphTarget,
    LegacyP24HistoryDetails,
    LegacyP24HistoryTarget,
    LegacyP24MappingRepository,
    LegacyP24RelationshipDetails,
    LegacyP24RelationshipRepository,
    LegacyP24RelationshipTarget,
)
from ..repositories.migration_p25 import p25_relationship_mapping_id
from ..repositories.migration_p25 import (
    LegacyP25JobDetails,
    LegacyP25JobTarget,
    LegacyP25MappingRepository,
    LegacyP25NotionDetails,
    LegacyP25NotionTarget,
    LegacyP25RelationshipDetails,
    LegacyP25RelationshipRepository,
    LegacyP25RelationshipTarget,
    LegacyP25SessionDetails,
    LegacyP25SessionTarget,
)
from .blob_stager import FilesystemLegacyBlobStager
from .importer import _reviewed_plan
from .p24_importer import (
    _prepare_container as _prepare_p24_container,
    _prepare_record as _prepare_p24_record,
)
from .plan_contract import canonical_json_bytes, sha256_canonical


VERIFICATION_REPORT_SCHEMA = "code-assist.legacy-migration-verification"
VERIFICATION_SCHEMA_VERSION = 1
_SHA_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_RECORD_RE = re.compile(r"^rec_[0-9a-f]{24}$", re.ASCII)
_RELATIONSHIP_RE = re.compile(r"^rel_[0-9a-f]{24}$", re.ASCII)
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.ASCII,
)
_ANY_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.ASCII,
)
_TERMINALS = frozenset(
    {"imported", "rebuilt", "retained_external", "quarantined", "unavailable"}
)
_ACTION_TO_TERMINAL = {
    "import": "imported",
    "quarantine": "quarantined",
    "rebuild": "rebuilt",
    "record_unavailable": "unavailable",
    "retain_external": "retained_external",
}
_TOP_KEYS = frozenset(
    {
        "report_schema",
        "schema_version",
        "stage",
        "plan",
        "records",
        "relationships",
        "rebuild",
        "reconciliation",
        "verification_sha256",
    }
)
_PLAN_KEYS = frozenset(
    {
        "inventory_manifest_sha256",
        "plan_sha256",
        "planned_record_count",
        "planned_records_sha256",
        "planned_relationship_count",
        "planned_relationships_sha256",
        "review_attestation_sha256",
        "root_binding_sha256",
        "technical_plan_sha256",
    }
)
_OUTCOME_KEYS = frozenset(
    {
        "action",
        "mapping_id",
        "observed_terminal_classification",
        "planned_terminal_classification",
        "record_id",
        "target_identity_sha256",
    }
)
_REL_OUTCOME_KEYS = frozenset(
    {
        "action",
        "mapping_id",
        "observed_terminal_classification",
        "planned_terminal_classification",
        "relationship_id",
        "target_identity_sha256",
    }
)
_REBUILD_KEYS = frozenset(
    {
        "after_sha256",
        "before_sha256",
        "expected_sha256",
        "fts_integrity",
        "legacy_rebuild_record_count",
        "legacy_rebuild_records_sha256",
        "projection_entry_count",
        "projection_integrity",
        "projection_name",
        "projection_space_count",
        "projection_spaces",
        "projection_spaces_sha256",
        "row_count",
    }
)
_PROJECTION_SPACE_KEYS = frozenset(
    {
        "canonical_high_water",
        "entries_sha256",
        "entry_count",
        "generation",
        "manifest_sha256",
        "space_id",
    }
)
_RECONCILIATION_KEYS = frozenset(
    {
        "canonical_targets_sha256",
        "observed_record_count",
        "observed_records_sha256",
        "observed_relationship_count",
        "observed_relationships_sha256",
        "terminal_classification_counts",
        "unresolved_records",
        "unresolved_relationships",
    }
)


class LegacyMigrationVerificationError(KnowledgeCoreError):
    code = "LEGACY_MIGRATION_VERIFICATION_FAILED"


class LegacyMigrationVerificationPlanError(LegacyMigrationVerificationError):
    code = "LEGACY_MIGRATION_VERIFICATION_PLAN_INVALID"


class LegacyMigrationVerificationIntegrityError(LegacyMigrationVerificationError):
    code = "LEGACY_MIGRATION_VERIFICATION_INTEGRITY_ERROR"


def _mapping(value: object, *, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise LegacyMigrationVerificationPlanError(f"{field} is invalid.")
    return value


def _sequence(value: object, *, field: str) -> Sequence[object]:
    if not isinstance(value, list):
        raise LegacyMigrationVerificationPlanError(f"{field} is invalid.")
    return value


def _row_dict(row: object) -> dict[str, object]:
    try:
        return {str(key): row[key] for key in row.keys()}  # type: ignore[attr-defined,index]
    except (AttributeError, KeyError, TypeError):
        raise LegacyMigrationVerificationIntegrityError(
            "A canonical verification row is invalid."
        ) from None


def _strict_object(value: object, *, field: str) -> dict[str, object]:
    if not isinstance(value, str):
        raise LegacyMigrationVerificationIntegrityError(f"{field} is invalid.")
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, ValueError, RecursionError):
        raise LegacyMigrationVerificationIntegrityError(f"{field} is invalid.") from None
    if not isinstance(parsed, dict) or canonical_json_bytes(parsed).decode("utf-8") != value:
        raise LegacyMigrationVerificationIntegrityError(
            f"{field} is not canonical JSON."
        )
    return parsed


def _canonical_row(row: Mapping[str, object]) -> dict[str, object]:
    result = dict(row)
    details = result.get("details_json")
    if details is not None:
        result["details"] = _strict_object(details, field="mapping details")
        result.pop("details_json", None)
    return result


def _source(plan_row: Mapping[str, object]) -> Mapping[str, object]:
    return _mapping(plan_row.get("source_record"), field="source_record")


def _details_rows(
    connection: ManagedConnection,
    *,
    table: str,
    plan_sha256: str,
) -> list[dict[str, object]]:
    rows = connection.execute(
        f"""SELECT * FROM {table}
             WHERE json_extract(details_json,'$.plan_sha256')=?""",
        (plan_sha256,),
    ).fetchall()
    return [_row_dict(row) for row in rows]


def _require_target(
    connection: ManagedConnection,
    *,
    table: str,
    key: object,
) -> None:
    if key is None:
        raise LegacyMigrationVerificationIntegrityError(
            "An imported checkpoint is missing its canonical target."
        )
    if connection.execute(f"SELECT 1 FROM {table} WHERE id=?", (key,)).fetchone() is None:
        raise LegacyMigrationVerificationIntegrityError(
            "An imported checkpoint target is missing."
        )


def _verify_personal_space(
    connection: ManagedConnection,
    *,
    space_id: object,
    owner_user_id: object,
) -> None:
    if not isinstance(space_id, str) or not isinstance(owner_user_id, str):
        raise LegacyMigrationVerificationIntegrityError(
            "An imported personal target is incomplete."
        )
    row = connection.execute(
        "SELECT space_kind,owner_user_id,status FROM knowledge_space WHERE id=?",
        (space_id,),
    ).fetchone()
    if row is None or tuple(row) != ("personal", owner_user_id, "active"):
        raise LegacyMigrationVerificationIntegrityError(
            "An imported target is not the exact active personal space."
        )


def _verify_record_target(
    connection: ManagedConnection,
    *,
    row: Mapping[str, object],
    details: Mapping[str, object],
    plan_row: Mapping[str, object],
) -> None:
    mode = plan_row.get("import_mode")
    owner = plan_row.get("resolved_owner_user_id")
    if row.get("resolved_owner_user_id") != owner:
        raise LegacyMigrationVerificationIntegrityError(
            "An imported checkpoint owner differs from the reviewed plan."
        )
    if plan_row.get("target_space_kind") == "personal":
        _verify_personal_space(
            connection,
            space_id=details.get("target_space_id"),
            owner_user_id=owner,
        )
    if mode == "user_account":
        _require_target(connection, table="app_user", key=owner)
    elif mode == "personal_item":
        _require_target(connection, table="source_artifact", key=row.get("artifact_id"))
        _require_target(connection, table="knowledge_item", key=row.get("item_id"))
        _require_target(connection, table="knowledge_revision", key=row.get("revision_id"))
    elif mode == "personal_asset":
        _require_target(connection, table="source_artifact", key=row.get("artifact_id"))
    elif mode == "graph_node":
        _require_target(connection, table="knowledge_item", key=row.get("item_id"))
        _require_target(connection, table="knowledge_revision", key=row.get("revision_id"))
    elif mode == "graph_edge":
        _require_target(connection, table="knowledge_edge", key=row.get("edge_id"))
    elif mode == "finding":
        _require_target(connection, table="curation_finding", key=row.get("finding_id"))
    elif mode == "legacy_history":
        if row.get("artifact_id") is None and row.get("audit_event_id") is None:
            raise LegacyMigrationVerificationIntegrityError(
                "A historical checkpoint has no canonical evidence target."
            )
    elif mode in {"conversation", "conversation_child"}:
        _require_target(connection, table="conversation", key=row.get("conversation_id"))
    elif mode == "notion_session_link":
        _require_target(connection, table="conversation", key=row.get("conversation_id"))
        _require_target(
            connection,
            table="conversation_context_binding",
            key=row.get("context_binding_id"),
        )
    elif mode in {"job_history", "abandoned_legacy_job"}:
        _require_target(connection, table="source_artifact", key=row.get("artifact_id"))
    elif mode == "source_container":
        category = plan_row.get("category")
        if category in {"conversation.user_sessions", "conversation.global_sessions"}:
            _require_target(
                connection, table="conversation", key=row.get("conversation_id")
            )


def _p23_evidence(details: object) -> VerifiedBlobEvidence | None:
    mode = getattr(details, "import_mode", None)
    if mode == "user_account":
        return None
    return VerifiedBlobEvidence(
        raw_sha256=str(getattr(details, "source_record_sha256")),
        size_bytes=int(getattr(details, "source_size_bytes")),
        media_type=str(getattr(details, "source_media_type")),
        normalized_content_sha256=(
            str(getattr(details, "normalized_content_sha256"))
            if getattr(details, "normalized_content_sha256", None) is not None
            else None
        ),
        normalizer_version=(
            str(getattr(details, "normalizer_version"))
            if getattr(details, "normalizer_version", None) is not None
            else None
        ),
    )


def _verify_p23_record_effect(
    connection: ManagedConnection,
    *,
    mapping_id: str,
) -> None:
    repository = LegacyMigrationRepository()
    try:
        stored = repository.get_by_id(connection, mapping_id)
        if stored is None:
            raise LegacyMigrationVerificationIntegrityError(
                "A P2.3 checkpoint disappeared during verification."
            )
        repository.verify_target_integrity(
            connection,
            stored,
            verified_blob_evidence=_p23_evidence(stored.details),
        )
    except LegacyMigrationVerificationIntegrityError:
        raise
    except (KnowledgeCoreError, TypeError, ValueError) as exc:
        raise LegacyMigrationVerificationIntegrityError(
            "A P2.3 canonical effect failed exact target verification."
        ) from exc


def _verify_p23_relationship_effect(
    connection: ManagedConnection,
    *,
    relationship_mapping_id: str,
) -> None:
    relationships = LegacyMigrationRelationshipRepository()
    records = LegacyMigrationRepository()
    try:
        stored = relationships.get_by_id(connection, relationship_mapping_id)
        if stored is None:
            raise LegacyMigrationVerificationIntegrityError(
                "A P2.3 relationship checkpoint disappeared during verification."
            )
        source = records.get_by_id(connection, stored.from_mapping_id)
        target = records.get_by_id(connection, stored.to_mapping_id)
        if source is None or target is None:
            raise LegacyMigrationVerificationIntegrityError(
                "A P2.3 relationship endpoint checkpoint is missing."
            )
        source_evidence = _p23_evidence(source.details)
        target_evidence = _p23_evidence(target.details)
        if source_evidence is None or target_evidence is None:
            raise LegacyMigrationVerificationIntegrityError(
                "A P2.3 relationship endpoint has no physical evidence."
            )
        relationships.verify_target_integrity(
            connection,
            stored,
            source_verified_blob_evidence=source_evidence,
            target_verified_blob_evidence=target_evidence,
        )
    except LegacyMigrationVerificationIntegrityError:
        raise
    except (KnowledgeCoreError, TypeError, ValueError) as exc:
        raise LegacyMigrationVerificationIntegrityError(
            "A P2.3 relationship effect failed exact target verification."
        ) from exc


def _verify_p25_record_effect(
    connection: ManagedConnection,
    *,
    row: Mapping[str, object],
    details: Mapping[str, object],
    plan_row: Mapping[str, object],
) -> None:
    repository = LegacyP25MappingRepository()
    source = _source(plan_row)
    path_key = source.get("path_key")
    if not isinstance(path_key, str):
        raise LegacyMigrationVerificationIntegrityError(
            "A P2.5 checkpoint has no reviewed source path."
        )
    owner = row.get("resolved_owner_user_id")
    try:
        schema = details.get("schema_version")
        if schema == 5:
            session = LegacyP25SessionDetails(
                plan_sha256=str(details["plan_sha256"]),
                inventory_manifest_sha256=str(details["inventory_manifest_sha256"]),
                root_binding_sha256=str(details["root_binding_sha256"]),
                inventory_record_id=str(details["inventory_record_id"]),
                inventory_record_sha256=str(details["inventory_record_sha256"]),
                parent_inventory_record_id=details["parent_inventory_record_id"],  # type: ignore[arg-type]
                root_id=str(details["root_id"]),
                path_key_sha256=str(details["path_key_sha256"]),
                category=str(details["category"]),
                record_kind=str(details["record_kind"]),
                source_ordinal=int(details["source_ordinal"]),
                import_mode=details["import_mode"],  # type: ignore[arg-type]
                result_disposition=str(details["result_disposition"]),
                target_space_id=str(details["target_space_id"]),
                source_record_sha256=str(details["source_record_sha256"]),
                conversation_id=str(details["conversation_id"]),
                session_id_sha256=str(details["session_id_sha256"]),
                child_kind=details["child_kind"],  # type: ignore[arg-type]
                payload_sha256=details["payload_sha256"],  # type: ignore[arg-type]
                message_sequence_no=details["message_sequence_no"],  # type: ignore[arg-type]
                planned_import_child_record_ids=tuple(
                    str(value)
                    for value in details["planned_import_child_record_ids"]  # type: ignore[union-attr]
                ),
                imported_child_bindings_sha256=str(
                    details["imported_child_bindings_sha256"]
                ),
            )
            target = LegacyP25SessionTarget(
                resolved_owner_user_id=str(owner),
                conversation_id=str(row.get("conversation_id")),
                message_id=row.get("message_id"),  # type: ignore[arg-type]
                summary_id=row.get("summary_id"),  # type: ignore[arg-type]
                details=session,
                source_path_key=path_key,
            )
            with transaction(connection):
                _record, inserted = repository.put_session_imported(
                    connection, target=target, now_ms=int(row["created_at"])
                )
        elif schema == 6:
            notion = LegacyP25NotionDetails(
                plan_sha256=str(details["plan_sha256"]),
                inventory_manifest_sha256=str(details["inventory_manifest_sha256"]),
                root_binding_sha256=str(details["root_binding_sha256"]),
                inventory_record_id=str(details["inventory_record_id"]),
                inventory_record_sha256=str(details["inventory_record_sha256"]),
                parent_inventory_record_id=details["parent_inventory_record_id"],  # type: ignore[arg-type]
                root_id=str(details["root_id"]),
                path_key_sha256=str(details["path_key_sha256"]),
                record_kind=str(details["record_kind"]),
                source_ordinal=int(details["source_ordinal"]),
                import_mode=details["import_mode"],  # type: ignore[arg-type]
                result_disposition=str(details["result_disposition"]),
                target_space_id=details["target_space_id"],  # type: ignore[arg-type]
                source_record_sha256=str(details["source_record_sha256"]),
                conversation_id=details["conversation_id"],  # type: ignore[arg-type]
                relationship_id=details["relationship_id"],  # type: ignore[arg-type]
                inventory_relationship_sha256=details["inventory_relationship_sha256"],  # type: ignore[arg-type]
                page_identity_sha256=details["page_identity_sha256"],  # type: ignore[arg-type]
                notion_row_sha256=details["notion_row_sha256"],  # type: ignore[arg-type]
                planned_import_child_record_ids=tuple(
                    str(value)
                    for value in details["planned_import_child_record_ids"]  # type: ignore[union-attr]
                ),
                planned_import_relationship_ids=tuple(
                    str(value)
                    for value in details["planned_import_relationship_ids"]  # type: ignore[union-attr]
                ),
                imported_child_bindings_sha256=str(
                    details["imported_child_bindings_sha256"]
                ),
                imported_relationship_bindings_sha256=str(
                    details["imported_relationship_bindings_sha256"]
                ),
            )
            target = LegacyP25NotionTarget(
                resolved_owner_user_id=str(owner),
                conversation_id=row.get("conversation_id"),  # type: ignore[arg-type]
                context_binding_id=row.get("context_binding_id"),  # type: ignore[arg-type]
                details=notion,
                source_path_key=path_key,
            )
            with transaction(connection):
                _record, inserted = repository.put_notion_imported(
                    connection, target=target, now_ms=int(row["created_at"])
                )
        elif schema == 7:
            audit = connection.execute(
                "SELECT event_uuid,details_json FROM audit_event WHERE id=?",
                (row.get("audit_event_id"),),
            ).fetchone()
            if audit is None:
                raise LegacyMigrationVerificationIntegrityError(
                    "A P2.5 job checkpoint has no exact audit evidence."
                )
            job = LegacyP25JobDetails(
                plan_sha256=str(details["plan_sha256"]),
                inventory_manifest_sha256=str(details["inventory_manifest_sha256"]),
                root_binding_sha256=str(details["root_binding_sha256"]),
                inventory_record_id=str(details["inventory_record_id"]),
                inventory_record_sha256=str(details["inventory_record_sha256"]),
                root_id=str(details["root_id"]),
                path_key_sha256=str(details["path_key_sha256"]),
                source_record_sha256=str(details["source_record_sha256"]),
                target_space_id=str(details["target_space_id"]),
                import_mode=details["import_mode"],  # type: ignore[arg-type]
                result_disposition=details["result_disposition"],  # type: ignore[arg-type]
                source_status=details["source_status"],  # type: ignore[arg-type]
                job_identity_sha256=str(details["job_identity_sha256"]),
                job_evidence_sha256=str(details["job_evidence_sha256"]),
            )
            target = LegacyP25JobTarget(
                resolved_owner_user_id=str(owner),
                artifact_id=str(row.get("artifact_id")),
                audit_event_id=int(row.get("audit_event_id")),
                audit_event_uuid=str(audit["event_uuid"]),
                details=job,
                source_path_key=path_key,
                job_evidence_json=str(audit["details_json"]),
            )
            with transaction(connection):
                _record, inserted = repository.put_job_imported(
                    connection, target=target, now_ms=int(row["created_at"])
                )
        else:
            return
        if inserted:
            raise LegacyMigrationVerificationIntegrityError(
                "P2.5 verification unexpectedly created a checkpoint."
            )
    except LegacyMigrationVerificationIntegrityError:
        raise
    except (KnowledgeCoreError, KeyError, TypeError, ValueError) as exc:
        raise LegacyMigrationVerificationIntegrityError(
            "A P2.5 canonical effect failed exact replay verification."
        ) from exc


def _verify_p25_relationship_effect(
    connection: ManagedConnection,
    *,
    row: Mapping[str, object],
    details: Mapping[str, object],
) -> None:
    try:
        target = LegacyP25RelationshipTarget(
            source_mapping_id=str(row["source_mapping_id"]),
            from_mapping_id=str(row["from_mapping_id"]),
            conversation_id=str(row["conversation_id"]),
            context_binding_id=str(row["context_binding_id"]),
            resolved_owner_user_id=str(row["resolved_owner_user_id"]),
            target_space_id=str(row["target_space_id"]),
            details=LegacyP25RelationshipDetails(
                plan_sha256=str(details["plan_sha256"]),
                relationship_id=str(details["relationship_id"]),
                inventory_manifest_sha256=str(details["inventory_manifest_sha256"]),
                root_binding_sha256=str(details["root_binding_sha256"]),
                inventory_relationship_sha256=str(
                    details["inventory_relationship_sha256"]
                ),
                source_inventory_record_id=str(
                    details["source_inventory_record_id"]
                ),
                from_inventory_record_id=str(details["from_inventory_record_id"]),
                page_identity_sha256=str(details["page_identity_sha256"]),
                notion_row_sha256=str(details["notion_row_sha256"]),
            ),
        )
        with transaction(connection):
            _record, inserted = LegacyP25RelationshipRepository().put_imported(
                connection, target=target, now_ms=int(row["created_at"])
            )
        if inserted:
            raise LegacyMigrationVerificationIntegrityError(
                "P2.5 verification unexpectedly created a relationship checkpoint."
            )
    except LegacyMigrationVerificationIntegrityError:
        raise
    except (KnowledgeCoreError, KeyError, TypeError, ValueError) as exc:
        raise LegacyMigrationVerificationIntegrityError(
            "A P2.5 relationship failed exact replay verification."
        ) from exc


def _verify_p24_record_effect(
    connection: ManagedConnection,
    *,
    row: Mapping[str, object],
    details: Mapping[str, object],
    plan_row: Mapping[str, object],
    all_records: Sequence[Mapping[str, object]],
    all_relationships: Sequence[Mapping[str, object]],
    plan_sha256: str,
) -> None:
    repository = LegacyP24MappingRepository()
    try:
        schema = details.get("schema_version")
        if schema == 2:
            prepared = _prepare_p24_record(plan_row)
            graph = LegacyP24GraphDetails(
                plan_sha256=str(details["plan_sha256"]),
                inventory_manifest_sha256=str(details["inventory_manifest_sha256"]),
                root_binding_sha256=str(details["root_binding_sha256"]),
                inventory_record_id=str(details["inventory_record_id"]),
                inventory_record_sha256=str(details["inventory_record_sha256"]),
                parent_inventory_record_id=str(details["parent_inventory_record_id"]),
                root_id=str(details["root_id"]),
                path_key_sha256=str(details["path_key_sha256"]),
                category=str(details["category"]),
                record_kind=str(details["record_kind"]),
                source_ordinal=int(details["source_ordinal"]),
                import_mode=details["import_mode"],  # type: ignore[arg-type]
                result_disposition=str(details["result_disposition"]),
                target_space_id=str(details["target_space_id"]),
                source_record_sha256=str(details["source_record_sha256"]),
                endpoint_inventory_record_ids=tuple(
                    str(value)
                    for value in details["endpoint_inventory_record_ids"]  # type: ignore[union-attr]
                ),
                relationship_ids=tuple(
                    str(value)
                    for value in details["relationship_ids"]  # type: ignore[union-attr]
                ),
                effect_evidence_sha256=str(details["effect_evidence_sha256"]),
            )
            target = LegacyP24GraphTarget(
                resolved_owner_user_id=str(row["resolved_owner_user_id"]),
                item_id=row.get("item_id"),  # type: ignore[arg-type]
                revision_id=row.get("revision_id"),  # type: ignore[arg-type]
                edge_id=row.get("edge_id"),  # type: ignore[arg-type]
                finding_id=row.get("finding_id"),  # type: ignore[arg-type]
                details=graph,
                source_path_key=prepared.path_key,
                effect_evidence_json=prepared.effect_evidence_json,
            )
            with transaction(connection):
                _record, inserted = repository.put_graph_imported(
                    connection,
                    mapping_id=str(row["id"]),
                    migration_run_id=str(row["migration_run_id"]),
                    legacy_kind=str(row["legacy_kind"]),
                    normalized_path=str(row["normalized_path"]),
                    source_sha256=str(row["source_sha256"]),
                    target=target,
                    now_ms=int(row["created_at"]),
                )
        elif schema == 3:
            prepared = _prepare_p24_container(
                plan_row,
                all_records=all_records,
                all_relationships=all_relationships,
                plan_sha256=plan_sha256,
            )
            container = LegacyP24ContainerDetails(
                plan_sha256=str(details["plan_sha256"]),
                inventory_manifest_sha256=str(details["inventory_manifest_sha256"]),
                root_binding_sha256=str(details["root_binding_sha256"]),
                inventory_record_id=str(details["inventory_record_id"]),
                inventory_record_sha256=str(details["inventory_record_sha256"]),
                root_id=str(details["root_id"]),
                path_key_sha256=str(details["path_key_sha256"]),
                category=str(details["category"]),
                source_record_sha256=str(details["source_record_sha256"]),
                target_space_id=str(details["target_space_id"]),
                planned_import_child_record_ids=tuple(
                    str(value)
                    for value in details["planned_import_child_record_ids"]  # type: ignore[union-attr]
                ),
                planned_import_relationship_ids=tuple(
                    str(value)
                    for value in details["planned_import_relationship_ids"]  # type: ignore[union-attr]
                ),
                imported_child_bindings_sha256=str(
                    details["imported_child_bindings_sha256"]
                ),
                imported_relationship_bindings_sha256=str(
                    details["imported_relationship_bindings_sha256"]
                ),
            )
            with transaction(connection):
                _record, inserted = repository.put_container_imported(
                    connection,
                    target=LegacyP24ContainerTarget(
                        resolved_owner_user_id=str(row["resolved_owner_user_id"]),
                        details=container,
                        source_path_key=prepared.path_key,
                    ),
                    now_ms=int(row["created_at"]),
                )
        else:
            return
        if inserted:
            raise LegacyMigrationVerificationIntegrityError(
                "P2.4 verification unexpectedly created a checkpoint."
            )
    except LegacyMigrationVerificationIntegrityError:
        raise
    except (KnowledgeCoreError, KeyError, TypeError, ValueError) as exc:
        raise LegacyMigrationVerificationIntegrityError(
            "A P2.4 canonical effect failed exact replay verification."
        ) from exc


def _verify_p24_history_effect(
    connection: ManagedConnection,
    *,
    row: Mapping[str, object],
    details: Mapping[str, object],
    plan_row: Mapping[str, object],
) -> None:
    """Reconstruct and replay one immutable labelled-history checkpoint."""

    source = _source(plan_row)
    path_key = source.get("path_key")
    if not isinstance(path_key, str):
        raise LegacyMigrationVerificationIntegrityError(
            "A P2.4 history checkpoint has no reviewed source path."
        )
    try:
        disposition = str(details["result_disposition"])
        artifact_id = row.get("artifact_id")
        audit_event_id = row.get("audit_event_id")
        audit_event_uuid: str | None = None
        if disposition == "artifact_retained":
            artifact = connection.execute(
                """SELECT raw_size_bytes,media_type
                     FROM source_artifact WHERE id=?""",
                (artifact_id,),
            ).fetchone()
            if artifact is None:
                raise LegacyMigrationVerificationIntegrityError(
                    "A P2.4 history artifact target is missing."
                )
            evidence: Mapping[str, object] = {
                "authority": "historical_only",
                "category": details["category"],
                "inventory_record_id": details["inventory_record_id"],
                "media_type": artifact["media_type"],
                "raw_sha256": details["source_record_sha256"],
                "schema_version": 1,
                "size_bytes": artifact["raw_size_bytes"],
            }
        else:
            audit = connection.execute(
                "SELECT event_uuid,details_json FROM audit_event WHERE id=?",
                (audit_event_id,),
            ).fetchone()
            if audit is None:
                raise LegacyMigrationVerificationIntegrityError(
                    "A P2.4 history event target is missing."
                )
            audit_event_uuid = str(audit["event_uuid"])
            evidence = _strict_object(
                audit["details_json"], field="P2.4 history evidence"
            )
        target = LegacyP24HistoryTarget(
            resolved_owner_user_id=row.get("resolved_owner_user_id"),  # type: ignore[arg-type]
            artifact_id=artifact_id,  # type: ignore[arg-type]
            audit_event_id=audit_event_id,  # type: ignore[arg-type]
            audit_event_uuid=audit_event_uuid,
            details=LegacyP24HistoryDetails(
                plan_sha256=str(details["plan_sha256"]),
                inventory_manifest_sha256=str(details["inventory_manifest_sha256"]),
                root_binding_sha256=str(details["root_binding_sha256"]),
                inventory_record_id=str(details["inventory_record_id"]),
                inventory_record_sha256=str(details["inventory_record_sha256"]),
                parent_inventory_record_id=details["parent_inventory_record_id"],  # type: ignore[arg-type]
                root_id=str(details["root_id"]),
                path_key_sha256=str(details["path_key_sha256"]),
                category=str(details["category"]),
                record_kind=str(details["record_kind"]),
                source_ordinal=int(details["source_ordinal"]),
                import_mode=details["import_mode"],  # type: ignore[arg-type]
                result_disposition=disposition,
                target_space_id=details["target_space_id"],  # type: ignore[arg-type]
                source_record_sha256=str(details["source_record_sha256"]),
                history_evidence_sha256=str(details["history_evidence_sha256"]),
                planned_import_child_record_ids=tuple(
                    str(value)
                    for value in details["planned_import_child_record_ids"]  # type: ignore[union-attr]
                ),
                imported_child_bindings_sha256=str(
                    details["imported_child_bindings_sha256"]
                ),
            ),
            source_path_key=path_key,
            history_evidence_json=canonical_json_bytes(evidence).decode("utf-8"),
        )
        with transaction(connection):
            _record, inserted = LegacyP24MappingRepository().put_history_imported(
                connection, target=target, now_ms=int(row["created_at"])
            )
        if inserted:
            raise LegacyMigrationVerificationIntegrityError(
                "P2.4 history verification unexpectedly created a checkpoint."
            )
    except LegacyMigrationVerificationIntegrityError:
        raise
    except (KnowledgeCoreError, KeyError, TypeError, ValueError) as exc:
        raise LegacyMigrationVerificationIntegrityError(
            "A P2.4 history effect failed exact replay verification."
        ) from exc


def _verify_p24_relationship_effect(
    connection: ManagedConnection,
    *,
    row: Mapping[str, object],
    details: Mapping[str, object],
) -> None:
    try:
        target = LegacyP24RelationshipTarget(
            relationship_type=row["relationship_type"],  # type: ignore[arg-type]
            source_mapping_id=row.get("source_mapping_id"),  # type: ignore[arg-type]
            from_mapping_id=row.get("from_mapping_id"),  # type: ignore[arg-type]
            to_mapping_id=str(row["to_mapping_id"]),
            resolved_owner_user_id=str(row["resolved_owner_user_id"]),
            target_space_id=str(row["target_space_id"]),
            effect_kind=row["effect_kind"],  # type: ignore[arg-type]
            edge_id=row.get("edge_id"),  # type: ignore[arg-type]
            finding_id=row.get("finding_id"),  # type: ignore[arg-type]
            item_id=row.get("item_id"),  # type: ignore[arg-type]
            details=LegacyP24RelationshipDetails(
                plan_sha256=str(details["plan_sha256"]),
                relationship_id=str(details["relationship_id"]),
                inventory_manifest_sha256=str(details["inventory_manifest_sha256"]),
                root_binding_sha256=str(details["root_binding_sha256"]),
                inventory_relationship_sha256=str(
                    details["inventory_relationship_sha256"]
                ),
                source_inventory_record_id=details["source_inventory_record_id"],  # type: ignore[arg-type]
                from_inventory_record_id=details["from_inventory_record_id"],  # type: ignore[arg-type]
                to_inventory_record_id=str(details["to_inventory_record_id"]),
                effect_mapping_id=str(details["effect_mapping_id"]),
            ),
        )
        with transaction(connection):
            _record, inserted = LegacyP24RelationshipRepository().put_imported(
                connection, target, now_ms=int(row["created_at"])
            )
        if inserted:
            raise LegacyMigrationVerificationIntegrityError(
                "P2.4 verification unexpectedly created a relationship checkpoint."
            )
    except LegacyMigrationVerificationIntegrityError:
        raise
    except (KnowledgeCoreError, KeyError, TypeError, ValueError) as exc:
        raise LegacyMigrationVerificationIntegrityError(
            "A P2.4 relationship failed exact replay verification."
        ) from exc
def _verify_artifact_blob(
    connection: ManagedConnection,
    *,
    stager: FilesystemLegacyBlobStager,
    artifact_id: str,
    expected_sha256: str,
    expected_size: int,
    normalized_sha256: object,
    normalizer_version: object,
    cache: dict[str, tuple[object, bytes]],
) -> None:
    rows = connection.execute(
        """SELECT sa.raw_sha256,sa.raw_size_bytes,ab.content_sha256,ab.size_bytes,
                  bo.size_bytes AS blob_size,bo.media_type,bo.relative_path,
                  bo.verification_status
             FROM source_artifact AS sa
             JOIN artifact_blob AS ab ON ab.artifact_id=sa.id AND ab.role='raw'
             JOIN artifact_blob_body AS abb ON abb.artifact_blob_id=ab.id
             JOIN blob_object AS bo ON bo.sha256=abb.content_sha256
            WHERE sa.id=?""",
        (artifact_id,),
    ).fetchall()
    if len(rows) != 1:
        raise LegacyMigrationVerificationIntegrityError(
            "An imported artifact has no exact raw CAS binding."
        )
    row = rows[0]
    if (
        row["raw_sha256"] != expected_sha256
        or row["content_sha256"] != expected_sha256
        or row["raw_size_bytes"] != expected_size
        or row["size_bytes"] != expected_size
        or row["blob_size"] != expected_size
        or row["relative_path"]
        != f"{expected_sha256[:2]}/{expected_sha256[2:4]}/{expected_sha256}"
        or row["verification_status"] != "verified"
    ):
        raise LegacyMigrationVerificationIntegrityError(
            "An imported artifact differs from its reviewed bytes."
        )
    if expected_sha256 not in cache:
        observation, content = _observe_existing_blob(
            stager,
            expected_sha256=expected_sha256,
            expected_size=expected_size,
            media_type=str(row["media_type"]),
            normalized_content_sha256=(
                str(normalized_sha256) if isinstance(normalized_sha256, str) else None
            ),
            normalizer_version=(
                str(normalizer_version) if isinstance(normalizer_version, str) else None
            ),
        )
        cache[expected_sha256] = (observation, content)


def _observe_existing_blob(
    stager: FilesystemLegacyBlobStager,
    *,
    expected_sha256: str,
    expected_size: int,
    media_type: str,
    normalized_content_sha256: str | None,
    normalizer_version: str | None,
) -> tuple[object, bytes]:
    try:
        return stager.verify_existing(
            expected_sha256=expected_sha256,
            expected_size=expected_size,
            media_type=media_type,
            normalized_content_sha256=normalized_content_sha256,
            normalizer_version=normalizer_version,
        )
    except (KnowledgeCoreError, OSError) as exc:
        raise LegacyMigrationVerificationIntegrityError(
            "A canonical CAS object failed physical verification."
        ) from exc


def _expected_search_rows(
    connection: ManagedConnection,
    *,
    stager: FilesystemLegacyBlobStager,
    cache: dict[str, tuple[object, bytes]],
) -> list[dict[str, object]]:
    rows = connection.execute(
        """SELECT i.id AS item_id,i.space_id,r.id AS revision_id,r.title,
                  b.content_md,b.content_blob_hash,b.content_size_bytes,
                  b.content_sha256,i.updated_at
             FROM knowledge_item AS i
             JOIN knowledge_revision AS r ON r.id=i.current_revision_id
             JOIN knowledge_revision_body AS b ON b.revision_id=r.id
            WHERE i.lifecycle_status='active' AND i.curation_status='published'
            ORDER BY i.id"""
    ).fetchall()
    expected: list[dict[str, object]] = []
    for row in rows:
        content_md = row["content_md"]
        if content_md is None:
            blob_sha = str(row["content_blob_hash"])
            blob = connection.execute(
                """SELECT size_bytes,media_type,relative_path,verification_status
                     FROM blob_object WHERE sha256=?""",
                (blob_sha,),
            ).fetchone()
            if blob is None or blob["verification_status"] != "verified":
                raise LegacyMigrationVerificationIntegrityError(
                    "A published revision blob is not verified."
                )
            if blob_sha not in cache:
                observation, content = _observe_existing_blob(
                    stager,
                    expected_sha256=blob_sha,
                    expected_size=int(blob["size_bytes"]),
                    media_type=str(blob["media_type"]),
                    normalized_content_sha256=None,
                    normalizer_version=None,
                )
                cache[blob_sha] = (observation, content)
            content = cache[blob_sha][1]
            try:
                content_md = content.decode("utf-8")
            except UnicodeDecodeError:
                raise LegacyMigrationVerificationIntegrityError(
                    "A published revision body is not UTF-8 Markdown."
                ) from None
        encoded = str(content_md).encode("utf-8")
        if (
            len(encoded) != row["content_size_bytes"]
            or hashlib.sha256(encoded).hexdigest() != row["content_sha256"]
        ):
            raise LegacyMigrationVerificationIntegrityError(
                "A published revision body differs from canonical metadata."
            )
        expected.append(
            {
                "content_md": str(content_md),
                "item_id": str(row["item_id"]),
                "revision_id": str(row["revision_id"]),
                "space_id": str(row["space_id"]),
                "title": str(row["title"]),
                "updated_at": int(row["updated_at"]),
            }
        )
    return expected


def _search_rows(connection: ManagedConnection) -> list[dict[str, object]]:
    return [
        {
            "content_md": str(row["content_md"]),
            "item_id": str(row["item_id"]),
            "revision_id": str(row["revision_id"]),
            "space_id": str(row["space_id"]),
            "title": str(row["title"]),
            "updated_at": int(row["updated_at"]),
        }
        for row in connection.execute(
            """SELECT item_id,space_id,revision_id,title,content_md,updated_at
                 FROM search_document ORDER BY item_id"""
        )
    ]


def _rebuild_search(
    connection: ManagedConnection,
    expected: Sequence[Mapping[str, object]],
) -> tuple[str, str, str]:
    before = _search_rows(connection)
    before_sha = sha256_canonical(before)
    expected_rows = [dict(row) for row in expected]
    expected_sha = sha256_canonical(expected_rows)
    with transaction(connection):
        connection.execute("DELETE FROM search_document")
        for row in expected_rows:
            connection.execute(
                """INSERT INTO search_document(
                       item_id,space_id,revision_id,title,content_md,updated_at
                   ) VALUES (?,?,?,?,?,?)""",
                (
                    row["item_id"],
                    row["space_id"],
                    row["revision_id"],
                    row["title"],
                    row["content_md"],
                    row["updated_at"],
                ),
            )
        connection.execute(
            "INSERT INTO knowledge_fts(knowledge_fts) VALUES ('rebuild')"
        )
        connection.execute(
            "INSERT INTO knowledge_fts(knowledge_fts,rank) "
            "VALUES ('integrity-check',1)"
        )
        after = _search_rows(connection)
        if after != expected_rows:
            raise LegacyMigrationVerificationIntegrityError(
                "The transactional FTS source rebuild does not match canonical state."
            )
    return before_sha, expected_sha, sha256_canonical(after)


def _verification_payload(report: Mapping[str, object]) -> dict[str, object]:
    payload = dict(report)
    payload.pop("verification_sha256", None)
    return payload


def verification_sha256(report: Mapping[str, object]) -> str:
    return sha256_canonical(_verification_payload(report))


def verification_contract_errors(
    value: object,
    *,
    verify_hash: bool = True,
) -> tuple[str, ...]:
    """Return stable errors for any JSON-shaped verification artifact."""

    errors: list[str] = []
    if not isinstance(value, Mapping) or frozenset(value) != _TOP_KEYS:
        return ("verification_shape",)
    if (
        value.get("report_schema") != VERIFICATION_REPORT_SCHEMA
        or value.get("schema_version") != VERIFICATION_SCHEMA_VERSION
        or value.get("stage") != "observed_migration_verification"
    ):
        errors.append("verification_header")
    plan = value.get("plan")
    if (
        not isinstance(plan, Mapping)
        or frozenset(plan) != _PLAN_KEYS
        or not all(
            isinstance(plan.get(key), str)
            and _SHA_RE.fullmatch(str(plan.get(key)))
            for key in _PLAN_KEYS
            if key
            not in {
                "planned_record_count",
                "planned_relationship_count",
            }
        )
        or any(
            not isinstance(plan.get(key), int)
            or isinstance(plan.get(key), bool)
            or int(plan.get(key, -1)) < 0
            for key in {"planned_record_count", "planned_relationship_count"}
        )
    ):
        errors.append("verification_plan")
    records = value.get("records")
    relationships = value.get("relationships")
    valid_records = isinstance(records, list)
    valid_relationships = isinstance(relationships, list)
    if valid_records:
        seen: set[str] = set()
        for row in records:
            if not isinstance(row, Mapping) or frozenset(row) != _OUTCOME_KEYS:
                valid_records = False
                break
            record_id = row.get("record_id")
            terminal = row.get("observed_terminal_classification")
            action = row.get("action")
            mapping_id = row.get("mapping_id")
            if (
                not isinstance(record_id, str)
                or _RECORD_RE.fullmatch(record_id) is None
                or record_id in seen
                or not isinstance(terminal, str)
                or terminal not in _TERMINALS
                or terminal != row.get("planned_terminal_classification")
                or not isinstance(action, str)
                or _ACTION_TO_TERMINAL.get(action) != terminal
                or not isinstance(row.get("target_identity_sha256"), str)
                or _SHA_RE.fullmatch(str(row.get("target_identity_sha256"))) is None
                or (
                    terminal == "imported"
                    and (
                        not isinstance(mapping_id, str)
                        or _UUID_RE.fullmatch(mapping_id) is None
                    )
                )
                or (terminal != "imported" and mapping_id is not None)
            ):
                valid_records = False
                break
            seen.add(record_id)
        valid_records = valid_records and records == sorted(
            records, key=lambda row: row["record_id"]
        )
    if not valid_records:
        errors.append("verification_records")
    if valid_relationships:
        seen_rel: set[str] = set()
        for row in relationships:
            if not isinstance(row, Mapping) or frozenset(row) != _REL_OUTCOME_KEYS:
                valid_relationships = False
                break
            relationship_id = row.get("relationship_id")
            terminal = row.get("observed_terminal_classification")
            action = row.get("action")
            mapping_id = row.get("mapping_id")
            if (
                not isinstance(relationship_id, str)
                or _RELATIONSHIP_RE.fullmatch(relationship_id) is None
                or relationship_id in seen_rel
                or not isinstance(terminal, str)
                or terminal not in _TERMINALS
                or terminal != row.get("planned_terminal_classification")
                or not isinstance(action, str)
                or _ACTION_TO_TERMINAL.get(action) != terminal
                or not isinstance(row.get("target_identity_sha256"), str)
                or _SHA_RE.fullmatch(str(row.get("target_identity_sha256"))) is None
                or (
                    terminal == "imported"
                    and (
                        not isinstance(mapping_id, str)
                        or _UUID_RE.fullmatch(mapping_id) is None
                    )
                )
                or (terminal != "imported" and mapping_id is not None)
            ):
                valid_relationships = False
                break
            seen_rel.add(relationship_id)
        valid_relationships = valid_relationships and relationships == sorted(
            relationships, key=lambda row: row["relationship_id"]
        )
    if not valid_relationships:
        errors.append("verification_relationships")
    if (
        isinstance(plan, Mapping)
        and valid_records
        and valid_relationships
        and (
            plan.get("planned_record_count") != len(records)
            or plan.get("planned_relationship_count") != len(relationships)
        )
    ):
        errors.append("verification_plan_binding")
    rebuild = value.get("rebuild")
    projection_spaces: object = None
    if (
        not isinstance(rebuild, Mapping)
        or frozenset(rebuild) != _REBUILD_KEYS
        or rebuild.get("fts_integrity") != "pass"
        or rebuild.get("projection_integrity") != "pass"
        or rebuild.get("projection_name") != PROJECTION_NAME
        or rebuild.get("after_sha256") != rebuild.get("expected_sha256")
        or not isinstance(rebuild.get("row_count"), int)
        or isinstance(rebuild.get("row_count"), bool)
        or int(rebuild.get("row_count", -1)) < 0
        or not isinstance(rebuild.get("legacy_rebuild_record_count"), int)
        or isinstance(rebuild.get("legacy_rebuild_record_count"), bool)
        or int(rebuild.get("legacy_rebuild_record_count", -1)) < 0
        or not isinstance(rebuild.get("projection_space_count"), int)
        or isinstance(rebuild.get("projection_space_count"), bool)
        or int(rebuild.get("projection_space_count", -1)) < 0
        or not isinstance(rebuild.get("projection_entry_count"), int)
        or isinstance(rebuild.get("projection_entry_count"), bool)
        or int(rebuild.get("projection_entry_count", -1)) < 0
        or not all(
            isinstance(rebuild.get(key), str)
            and _SHA_RE.fullmatch(str(rebuild.get(key)))
            for key in (
                "after_sha256",
                "before_sha256",
                "expected_sha256",
                "legacy_rebuild_records_sha256",
                "projection_spaces_sha256",
            )
        )
    ):
        errors.append("verification_rebuild")
    else:
        projection_spaces = rebuild.get("projection_spaces")
        valid_projection_spaces = isinstance(projection_spaces, list)
        seen_spaces: set[str] = set()
        if valid_projection_spaces:
            for row in projection_spaces:
                if not isinstance(row, Mapping) or frozenset(row) != _PROJECTION_SPACE_KEYS:
                    valid_projection_spaces = False
                    break
                space_id = row.get("space_id")
                if (
                    not isinstance(space_id, str)
                    or _ANY_UUID_RE.fullmatch(space_id) is None
                    or space_id in seen_spaces
                    or any(
                        not isinstance(row.get(field), int)
                        or isinstance(row.get(field), bool)
                        or int(row.get(field, -1)) < 0
                        for field in (
                            "canonical_high_water",
                            "entry_count",
                            "generation",
                        )
                    )
                    or any(
                        not isinstance(row.get(field), str)
                        or _SHA_RE.fullmatch(str(row.get(field))) is None
                        for field in ("entries_sha256", "manifest_sha256")
                    )
                ):
                    valid_projection_spaces = False
                    break
                seen_spaces.add(space_id)
            valid_projection_spaces = valid_projection_spaces and projection_spaces == sorted(
                projection_spaces, key=lambda row: row["space_id"]
            )
        if (
            not valid_projection_spaces
            or rebuild.get("projection_space_count") != len(projection_spaces)
            or rebuild.get("projection_entry_count")
            != sum(int(row["entry_count"]) for row in projection_spaces)
            or rebuild.get("projection_spaces_sha256")
            != sha256_canonical(projection_spaces)
        ):
            errors.append("verification_rebuild")
    if isinstance(rebuild, Mapping) and valid_records:
        rebuild_record_ids = [
            str(row["record_id"])
            for row in records
            if row["observed_terminal_classification"] == "rebuilt"
        ]
        if (
            rebuild.get("legacy_rebuild_record_count")
            != len(rebuild_record_ids)
            or rebuild.get("legacy_rebuild_records_sha256")
            != sha256_canonical(rebuild_record_ids)
        ):
            errors.append("verification_rebuild")
    reconciliation = value.get("reconciliation")
    if not isinstance(reconciliation, Mapping) or frozenset(reconciliation) != _RECONCILIATION_KEYS:
        errors.append("verification_reconciliation")
    elif valid_records and valid_relationships:
        terminals = Counter(
            str(row["observed_terminal_classification"]) for row in records
        )
        target_rows = [
            [row["record_id"], row["mapping_id"], row["target_identity_sha256"]]
            for row in records
        ] + [
            [row["relationship_id"], row["mapping_id"], row["target_identity_sha256"]]
            for row in relationships
        ]
        count_fields = (
            "observed_record_count",
            "observed_relationship_count",
            "unresolved_records",
            "unresolved_relationships",
        )
        if (
            any(
                not isinstance(reconciliation.get(field), int)
                or isinstance(reconciliation.get(field), bool)
                or int(reconciliation.get(field, -1)) < 0
                for field in count_fields
            )
            or
            reconciliation.get("observed_record_count") != len(records)
            or reconciliation.get("observed_relationship_count") != len(relationships)
            or reconciliation.get("unresolved_records") != 0
            or reconciliation.get("unresolved_relationships") != 0
            or reconciliation.get("terminal_classification_counts")
            != dict(sorted(terminals.items()))
            or reconciliation.get("observed_records_sha256")
            != sha256_canonical(records)
            or reconciliation.get("observed_relationships_sha256")
            != sha256_canonical(relationships)
            or reconciliation.get("canonical_targets_sha256")
            != sha256_canonical(target_rows)
        ):
            errors.append("verification_reconciliation")
    if verify_hash:
        try:
            hash_valid = (
                isinstance(value.get("verification_sha256"), str)
                and value.get("verification_sha256") == verification_sha256(value)
            )
        except (TypeError, ValueError, RecursionError):
            hash_valid = False
        if not hash_valid:
            errors.append("verification_hash")
    return tuple(sorted(set(errors)))


class LegacyMigrationVerifier:
    """Verify a complete reviewed migration and rebuild transactional FTS."""

    __slots__ = ("_boundary", "_factory", "_layout", "_stager")

    def __init__(
        self,
        factory: ConnectionFactory,
        layout: MigrationStorageLayout,
    ) -> None:
        if not isinstance(factory, ConnectionFactory):
            raise TypeError("factory must be a ConnectionFactory.")
        if not isinstance(layout, MigrationStorageLayout):
            raise TypeError("layout must be a MigrationStorageLayout.")
        self._factory = factory
        self._layout = layout
        self._boundary: MigrationStorageBoundary = verify_migration_storage_layout(
            layout, require_database=True
        )
        self._stager = FilesystemLegacyBlobStager(layout)

    def verify(
        self,
        reviewed_plan: bytes,
        *,
        expected_plan_sha256: str,
    ) -> dict[str, object]:
        plan = _reviewed_plan(
            reviewed_plan, expected_plan_sha256=expected_plan_sha256
        )
        draft = _mapping(plan.get("draft"), field="draft")
        plan_records = [
            _mapping(row, field="plan record")
            for row in _sequence(draft.get("records"), field="records")
        ]
        plan_relationships = [
            _mapping(row, field="plan relationship")
            for row in _sequence(draft.get("relationships"), field="relationships")
        ]
        inputs = _mapping(draft.get("inputs"), field="inputs")
        inventory_binding = _mapping(inputs.get("inventory"), field="inventory binding")
        inventory_manifest = str(inventory_binding.get("manifest_sha256"))
        source_snapshot = _mapping(draft.get("source_snapshot"), field="source_snapshot")
        root_binding = str(source_snapshot.get("root_binding_sha256"))
        plan_reconciliation = _mapping(
            draft.get("reconciliation"), field="plan reconciliation"
        )
        connection: ManagedConnection | None = None
        blob_cache: dict[str, tuple[object, bytes]] = {}
        try:
            verify_migration_storage_layout(
                self._layout, require_database=True, expected=self._boundary
            )
            connection = self._factory.open()
            verify_canonical_integrity_before_projection_rebuild(
                connection, self._factory.migration_catalog
            )
            record_rows = _details_rows(
                connection,
                table="legacy_migration_map",
                plan_sha256=expected_plan_sha256,
            )
            records_by_id: dict[str, dict[str, object]] = {}
            for row in record_rows:
                details = _strict_object(row.get("details_json"), field="record details")
                record_id = details.get("inventory_record_id")
                if not isinstance(record_id, str) or record_id in records_by_id:
                    raise LegacyMigrationVerificationIntegrityError(
                        "Imported record checkpoint identity is duplicated."
                    )
                records_by_id[record_id] = row
            relationship_tables = {
                "P2.3": "legacy_migration_relationship_map",
                "P2.4": "legacy_migration_p24_relationship_map",
                "P2.5": "legacy_migration_p25_relationship_map",
            }
            relationship_rows_by_table = {
                phase: _details_rows(
                    connection, table=table, plan_sha256=expected_plan_sha256
                )
                for phase, table in relationship_tables.items()
            }
            all_relationship_rows: dict[str, tuple[str, dict[str, object]]] = {}
            for phase, rows in relationship_rows_by_table.items():
                for row in rows:
                    relationship_id = row.get("relationship_id")
                    if (
                        not isinstance(relationship_id, str)
                        or relationship_id in all_relationship_rows
                    ):
                        raise LegacyMigrationVerificationIntegrityError(
                            "Imported relationship checkpoint identity is duplicated."
                        )
                    all_relationship_rows[relationship_id] = (phase, row)

            outcomes: list[dict[str, object]] = []
            for plan_row in sorted(plan_records, key=lambda row: str(row["record_id"])):
                record_id = str(plan_row["record_id"])
                planned = str(plan_row["final_terminal_classification"])
                imported = plan_row.get("action") == "import"
                mapping_row = records_by_id.pop(record_id, None)
                if imported != (mapping_row is not None):
                    raise LegacyMigrationVerificationIntegrityError(
                        "A reviewed record does not have exactly one expected checkpoint."
                    )
                if mapping_row is None:
                    target_sha = sha256_canonical(
                        {"action": plan_row["action"], "record_id": record_id}
                    )
                    mapping_id = None
                else:
                    details = _strict_object(
                        mapping_row.get("details_json"), field="record details"
                    )
                    source = _source(plan_row)
                    source_sha = source.get("sha256")
                    expected_mapping_id = migration_mapping_id_for_record(
                        expected_plan_sha256, record_id
                    )
                    path_key = source.get("path_key")
                    source_ordinal = source.get("source_ordinal")
                    if (
                        mapping_row.get("id") != expected_mapping_id
                        or mapping_row.get("migration_run_id")
                        != migration_run_id_for_plan(expected_plan_sha256)
                        or mapping_row.get("status") != "imported"
                        or mapping_row.get("legacy_kind") != plan_row.get("category")
                        or mapping_row.get("source_sha256") != source_sha
                        or details.get("plan_sha256") != expected_plan_sha256
                        or details.get("inventory_manifest_sha256") != inventory_manifest
                        or details.get("root_binding_sha256") != root_binding
                        or details.get("inventory_record_sha256")
                        != plan_row.get("inventory_record_sha256")
                        or details.get("source_record_sha256") != source_sha
                        or details.get("category") != plan_row.get("category")
                        or details.get("import_mode") != plan_row.get("import_mode")
                        or details.get("final_terminal_classification", "imported")
                        != "imported"
                        or details.get("root_id") != source.get("root_id")
                        or details.get("source_ordinal") != source_ordinal
                        or not isinstance(path_key, str)
                        or details.get("path_key_sha256")
                        != hashlib.sha256(path_key.encode("utf-8")).hexdigest()
                        or mapping_row.get("normalized_path")
                        != migration_source_locator(
                            root_id=str(source.get("root_id")),
                            path_key=path_key,
                            record_kind=str(source.get("record_kind")),  # type: ignore[arg-type]
                            source_ordinal=int(source_ordinal),
                        )
                    ):
                        raise LegacyMigrationVerificationIntegrityError(
                            "An imported record checkpoint differs from the reviewed plan."
                        )
                    _verify_record_target(
                        connection,
                        row=mapping_row,
                        details=details,
                        plan_row=plan_row,
                    )
                    artifact_id = mapping_row.get("artifact_id")
                    if artifact_id is not None:
                        size = source.get("size_bytes")
                        if not isinstance(source_sha, str) or not isinstance(size, int):
                            raise LegacyMigrationVerificationIntegrityError(
                                "An imported artifact source identity is invalid."
                            )
                        _verify_artifact_blob(
                            connection,
                            stager=self._stager,
                            artifact_id=str(artifact_id),
                            expected_sha256=source_sha,
                            expected_size=size,
                            normalized_sha256=details.get(
                                "normalized_content_sha256"
                            ),
                            normalizer_version=details.get("normalizer_version"),
                            cache=blob_cache,
                        )
                    mapping_id = str(mapping_row["id"])
                    if details.get("schema_version") == 1:
                        _verify_p23_record_effect(
                            connection, mapping_id=mapping_id
                        )
                    elif details.get("schema_version") in {5, 6, 7}:
                        _verify_p25_record_effect(
                            connection,
                            row=mapping_row,
                            details=details,
                            plan_row=plan_row,
                        )
                    elif details.get("schema_version") in {2, 3}:
                        _verify_p24_record_effect(
                            connection,
                            row=mapping_row,
                            details=details,
                            plan_row=plan_row,
                            all_records=plan_records,
                            all_relationships=plan_relationships,
                            plan_sha256=expected_plan_sha256,
                        )
                    elif details.get("schema_version") == 4:
                        _verify_p24_history_effect(
                            connection,
                            row=mapping_row,
                            details=details,
                            plan_row=plan_row,
                        )
                    target_sha = sha256_canonical(_canonical_row(mapping_row))
                outcomes.append(
                    {
                        "action": plan_row["action"],
                        "mapping_id": mapping_id,
                        "observed_terminal_classification": planned,
                        "planned_terminal_classification": planned,
                        "record_id": record_id,
                        "target_identity_sha256": target_sha,
                    }
                )
            if records_by_id:
                raise LegacyMigrationVerificationIntegrityError(
                    "The canonical database contains unplanned imported record checkpoints."
                )

            relationship_outcomes: list[dict[str, object]] = []
            for plan_row in sorted(
                plan_relationships, key=lambda row: str(row["relationship_id"])
            ):
                relationship_id = str(plan_row["relationship_id"])
                planned = str(plan_row["final_terminal_classification"])
                imported = plan_row.get("action") == "import"
                found = all_relationship_rows.pop(relationship_id, None)
                if imported != (found is not None):
                    raise LegacyMigrationVerificationIntegrityError(
                        "A reviewed relationship does not have exactly one expected checkpoint."
                    )
                if found is None:
                    mapping_id = None
                    target_sha = sha256_canonical(
                        {
                            "action": plan_row["action"],
                            "relationship_id": relationship_id,
                        }
                    )
                else:
                    phase, row = found
                    if phase != plan_row.get("execution_phase"):
                        raise LegacyMigrationVerificationIntegrityError(
                            "An imported relationship checkpoint is in the wrong phase ledger."
                        )
                    expected_id = {
                        "P2.3": migration_relationship_mapping_id_for_plan,
                        "P2.4": p24_relationship_mapping_id,
                        "P2.5": p25_relationship_mapping_id,
                    }[phase](expected_plan_sha256, relationship_id)
                    details = _strict_object(
                        row.get("details_json"), field="relationship details"
                    )
                    if (
                        row.get("id") != expected_id
                        or row.get("migration_run_id")
                        != migration_run_id_for_plan(expected_plan_sha256)
                        or row.get("status") != "imported"
                        or details.get("plan_sha256") != expected_plan_sha256
                        or details.get("relationship_id") != relationship_id
                        or details.get("inventory_manifest_sha256")
                        != inventory_manifest
                        or details.get("root_binding_sha256") != root_binding
                        or details.get("inventory_relationship_sha256")
                        != plan_row.get("inventory_relationship_sha256")
                    ):
                        raise LegacyMigrationVerificationIntegrityError(
                            "An imported relationship checkpoint differs from the reviewed plan."
                        )
                    mapping_id = str(row["id"])
                    if phase == "P2.3":
                        _verify_p23_relationship_effect(
                            connection,
                            relationship_mapping_id=mapping_id,
                        )
                    elif phase == "P2.5":
                        _verify_p25_relationship_effect(
                            connection,
                            row=row,
                            details=details,
                        )
                    elif phase == "P2.4":
                        _verify_p24_relationship_effect(
                            connection,
                            row=row,
                            details=details,
                        )
                    target_sha = sha256_canonical(_canonical_row(row))
                relationship_outcomes.append(
                    {
                        "action": plan_row["action"],
                        "mapping_id": mapping_id,
                        "observed_terminal_classification": planned,
                        "planned_terminal_classification": planned,
                        "relationship_id": relationship_id,
                        "target_identity_sha256": target_sha,
                    }
                )
            if all_relationship_rows:
                raise LegacyMigrationVerificationIntegrityError(
                    "The canonical database contains unplanned relationship checkpoints."
                )

            expected_search = _expected_search_rows(
                connection, stager=self._stager, cache=blob_cache
            )
            before_sha, expected_sha, after_sha = _rebuild_search(
                connection, expected_search
            )
            verify_canonical_integrity(connection, self._factory.migration_catalog)
            verify_migration_storage_layout(
                self._layout, require_database=True, expected=self._boundary
            )
            connection.close()
            connection = None
            try:
                projection = FullProjectionMaterializer(
                    self._factory, self._layout
                ).rebuild_all()
            except ProjectionMaterializationError as exc:
                raise LegacyMigrationVerificationIntegrityError(
                    "Canonical file projections could not be rebuilt exactly."
                ) from exc
            rebuild_records = [
                str(row["record_id"])
                for row in outcomes
                if row["observed_terminal_classification"] == "rebuilt"
            ]
            target_rows = [
                [row["record_id"], row["mapping_id"], row["target_identity_sha256"]]
                for row in outcomes
            ] + [
                [
                    row["relationship_id"],
                    row["mapping_id"],
                    row["target_identity_sha256"],
                ]
                for row in relationship_outcomes
            ]
            terminal_counts = dict(
                sorted(
                    Counter(
                        str(row["observed_terminal_classification"])
                        for row in outcomes
                    ).items()
                )
            )
            projection_spaces = [row.report_row() for row in projection.spaces]
            report: dict[str, object] = {
                "report_schema": VERIFICATION_REPORT_SCHEMA,
                "schema_version": VERIFICATION_SCHEMA_VERSION,
                "stage": "observed_migration_verification",
                "plan": {
                    "inventory_manifest_sha256": inventory_manifest,
                    "plan_sha256": expected_plan_sha256,
                    "planned_record_count": plan_reconciliation["total_records"],
                    "planned_records_sha256": plan_reconciliation["records_sha256"],
                    "planned_relationship_count": plan_reconciliation[
                        "total_relationships"
                    ],
                    "planned_relationships_sha256": plan_reconciliation[
                        "relationships_sha256"
                    ],
                    "review_attestation_sha256": _mapping(
                        plan.get("review"), field="review"
                    )["review_attestation_sha256"],
                    "root_binding_sha256": root_binding,
                    "technical_plan_sha256": draft["technical_plan_sha256"],
                },
                "records": outcomes,
                "relationships": relationship_outcomes,
                "rebuild": {
                    "after_sha256": after_sha,
                    "before_sha256": before_sha,
                    "expected_sha256": expected_sha,
                    "fts_integrity": "pass",
                    "legacy_rebuild_record_count": len(rebuild_records),
                    "legacy_rebuild_records_sha256": sha256_canonical(
                        rebuild_records
                    ),
                    "projection_entry_count": projection.entry_count,
                    "projection_integrity": "pass",
                    "projection_name": projection.projection_name,
                    "projection_space_count": projection.space_count,
                    "projection_spaces": projection_spaces,
                    "projection_spaces_sha256": projection.spaces_sha256,
                    "row_count": len(expected_search),
                },
                "reconciliation": {
                    "canonical_targets_sha256": sha256_canonical(target_rows),
                    "observed_record_count": len(outcomes),
                    "observed_records_sha256": sha256_canonical(outcomes),
                    "observed_relationship_count": len(relationship_outcomes),
                    "observed_relationships_sha256": sha256_canonical(
                        relationship_outcomes
                    ),
                    "terminal_classification_counts": terminal_counts,
                    "unresolved_records": 0,
                    "unresolved_relationships": 0,
                },
                "verification_sha256": "",
            }
            report["verification_sha256"] = verification_sha256(report)
            errors = verification_contract_errors(report)
            if errors:
                raise LegacyMigrationVerificationIntegrityError(
                    "The observed verification report does not close."
                )
            return report
        finally:
            if connection is not None:
                connection.close()
            for observation, _content in blob_cache.values():
                observation.lease.close()  # type: ignore[attr-defined]


__all__ = [
    "LegacyMigrationVerificationError",
    "LegacyMigrationVerificationIntegrityError",
    "LegacyMigrationVerificationPlanError",
    "LegacyMigrationVerifier",
    "VERIFICATION_REPORT_SCHEMA",
    "VERIFICATION_SCHEMA_VERSION",
    "verification_contract_errors",
    "verification_sha256",
]
