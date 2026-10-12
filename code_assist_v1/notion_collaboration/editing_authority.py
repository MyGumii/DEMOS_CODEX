"""E1-P1 canonical collaborative-editing authority.

The Python service never decodes Yjs updates.  It owns authorization,
idempotency, durable ordering, typed structural/database commands, and history
identity.  The selected Node/Yjs child remains the only CRDT compactor.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import time
from typing import Any, Mapping, Sequence

from code_assist_v1 import notion_workspace_editing_authority_migration as migration
from code_assist_v1 import notion_workspace_healing_migration as migration_core
from code_assist_v1 import notion_workspace_store as nws
from code_assist_v1.notion_collaboration.canonical_authority import (
    AuthorityUnavailable,
    CanonicalAuthority,
    NotFoundOrDenied,
    OperationConflict,
    _canonical_json,
    _identifier,
    _operation_id,
    _request_sha256,
    _sha256,
    _stable_id,
)
from code_assist_v1.notion_collaboration.durable_transport import DurableTransportAuthority
from code_assist_v1.notion_collaboration.iam_authority import (
    CollaborativeIAMAuthority,
    InMemoryIdentityDirectory,
    PAGE_ROLE_LEVEL,
)
from code_assist_v1.notion_collaboration.live_authority import (
    CollaborativeLiveAuthority,
    LiveDomainReplayBatch,
    replay_live_domain_events_on_connection,
)
from code_assist_v1.notion_collaboration.protocol_contract import ProtocolEnvelope


EDITING_AUTHORITY_CONTRACT = "code_assist.notion.collaborative_editing_authority.v1"
EDITING_TRANSPORT_CONTRACT = "code_assist.notion.collaborative_editing_transport.v1"
COMMAND_KINDS = frozenset(
    {
        "block.insert",
        "block.move",
        "block.delete",
        "database.cell.set",
        "database.property.rename",
    }
)
COMMAND_MINIMUM_ROLE = {
    "block.insert": "edit_content",
    "block.move": "edit_content",
    "block.delete": "edit_content",
    "database.cell.set": "edit_content",
    "database.property.rename": "edit",
}
MAX_COMMAND_JSON_BYTES = 1_048_576


class DocumentSequenceConflict(RuntimeError):
    def __init__(self, current_document_sequence: int) -> None:
        self.current_document_sequence = int(current_document_sequence)
        super().__init__("document sequence conflict")


class TargetVersionConflict(RuntimeError):
    def __init__(self, current_target_version: int) -> None:
        self.current_target_version = int(current_target_version)
        super().__init__("typed command target version conflict")


class CheckpointConflict(RuntimeError):
    pass


def _marker_current(con: sqlite3.Connection) -> bool:
    latest = con.execute("SELECT MAX(version) FROM workspace_schema_migrations").fetchone()
    if latest is None or latest[0] is None:
        return False
    version = int(latest[0])
    expected = (
        migration.TARGET_NAME,
        migration.TARGET_CHECKSUM,
        migration.TARGET_ACTUAL_SCHEMA_SHA256,
    )
    if version != migration.TARGET_VERSION:
        from code_assist_v1 import (
            notion_workspace_second_brain_publication_migration as publication_migration,
        )

        if version == publication_migration.TARGET_VERSION:
            expected = (
                publication_migration.TARGET_NAME,
                publication_migration.TARGET_CHECKSUM,
                publication_migration.TARGET_ACTUAL_SCHEMA_SHA256,
            )
        else:
            from code_assist_v1 import notion_workspace_database_features_migration

            if version == notion_workspace_database_features_migration.TARGET_VERSION:
                expected = (
                    notion_workspace_database_features_migration.TARGET_NAME,
                    notion_workspace_database_features_migration.TARGET_CHECKSUM,
                    notion_workspace_database_features_migration.TARGET_ACTUAL_SCHEMA_SHA256,
                )
            else:
                from code_assist_v1 import notion_workspace_file_authority_migration

                if version == notion_workspace_file_authority_migration.TARGET_VERSION:
                    expected = (
                        notion_workspace_file_authority_migration.TARGET_NAME,
                        notion_workspace_file_authority_migration.TARGET_CHECKSUM,
                        notion_workspace_file_authority_migration.TARGET_ACTUAL_SCHEMA_SHA256,
                    )
                else:
                    from code_assist_v1 import notion_workspace_history_authority_migration

                    if version == notion_workspace_history_authority_migration.TARGET_VERSION:
                        expected = (
                            notion_workspace_history_authority_migration.TARGET_NAME,
                            notion_workspace_history_authority_migration.TARGET_CHECKSUM,
                            notion_workspace_history_authority_migration.TARGET_ACTUAL_SCHEMA_SHA256,
                        )
                    else:
                        from code_assist_v1 import notion_workspace_search_authority_migration

                        if version != notion_workspace_search_authority_migration.TARGET_VERSION:
                            return False
                        expected = (
                            notion_workspace_search_authority_migration.TARGET_NAME,
                            notion_workspace_search_authority_migration.TARGET_CHECKSUM,
                            notion_workspace_search_authority_migration.TARGET_ACTUAL_SCHEMA_SHA256,
                        )
    marker = con.execute(
        "SELECT name,checksum,actual_schema_sha256 FROM workspace_schema_migrations WHERE version=?",
        (version,),
    ).fetchone()
    return bool(
        marker is not None
        and tuple(marker)
        == expected
    )


def _command_payload(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("typed command payload object required")
    clean = dict(value)
    encoded = _canonical_json(clean).encode("utf-8")
    if len(encoded) > MAX_COMMAND_JSON_BYTES:
        raise ValueError("typed command payload is too large")
    return clean


def _nonnegative(value: int, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"valid {field} required")
    return value


class CollaborativeEditingAuthority(CollaborativeLiveAuthority):
    """2405 editing authority with the additive 2406 publication extension."""

    @staticmethod
    def _record_document_creation_successor_state(
        con: sqlite3.Connection,
        *,
        workspace_id: str,
        document_id: str,
        content_sha256: str,
        actor_subject_id: str,
        operation_id: str,
        created_at: int,
    ) -> None:
        con.execute(
            "INSERT INTO workspace_collaboration_crdt_generations("
            "document_id,generation,yjs_document_key,state,created_at,retired_at) "
            "VALUES(?,1,?,'current',?,NULL)",
            (document_id, f"document:{document_id}:rich-text:g1", created_at),
        )
        con.execute(
            "INSERT INTO workspace_collaboration_revision_checkpoints("
            "id,workspace_id,document_id,revision,document_sequence,crdt_generation,"
            "crdt_snapshot_id,through_update_sequence,through_structure_command_sequence,"
            "content_sha256,actor_subject_id,operation_id,checkpoint_reason,"
            "publication_revision_id,created_at) "
            "VALUES(?,?,?,1,1,1,NULL,0,0,?,?,?,'initial',NULL,?)",
            (
                f"hist:{document_id}:r1",
                workspace_id,
                document_id,
                content_sha256,
                actor_subject_id,
                operation_id,
                created_at,
            ),
        )

    @classmethod
    def open(
        cls,
        catalog_path: str | os.PathLike[str],
        user_data_root: str | os.PathLike[str],
        identity_directory: InMemoryIdentityDirectory,
    ) -> "CollaborativeEditingAuthority":
        try:
            catalog, root = migration_core._resolved_target(catalog_path, user_data_root)
            con = sqlite3.connect(f"{catalog.as_uri()}?mode=ro", uri=True)
            con.row_factory = sqlite3.Row
            try:
                con.execute("PRAGMA query_only=ON")
                if not _marker_current(con):
                    raise AuthorityUnavailable("collaborative editing schema is not current")
            finally:
                con.close()
        except AuthorityUnavailable:
            raise
        except (OSError, sqlite3.Error, RuntimeError) as exc:
            raise AuthorityUnavailable("collaborative editing authority is unavailable") from exc
        return cls(catalog, root, identity_directory)

    def _connect(self, *, query_only: bool) -> sqlite3.Connection:
        try:
            if query_only:
                con = sqlite3.connect(
                    f"{self.catalog_path.as_uri()}?mode=ro",
                    uri=True,
                    timeout=5.0,
                    isolation_level=None,
                )
            else:
                con = sqlite3.connect(self.catalog_path, timeout=5.0, isolation_level=None)
            con.row_factory = sqlite3.Row
            con.execute("PRAGMA foreign_keys=ON")
            con.execute("PRAGMA busy_timeout=5000")
            if query_only:
                con.execute("PRAGMA query_only=ON")
            if not _marker_current(con):
                con.close()
                raise AuthorityUnavailable("collaborative editing schema marker mismatch")
            return con
        except AuthorityUnavailable:
            raise
        except sqlite3.Error as exc:
            raise AuthorityUnavailable("collaborative editing authority is unavailable") from exc

    def _exact_access(
        self,
        con: sqlite3.Connection,
        subject_id: str,
        document_id: str,
        *,
        minimum_role: str,
        expected_workspace_epoch: int,
        expected_document_epoch: int,
    ) -> tuple[sqlite3.Row, sqlite3.Row, str]:
        subject = self._require_active(subject_id)
        document = self._document(con, document_id)
        workspace = self._workspace(con, str(document["workspace_id"]))
        role, _sources = CollaborativeIAMAuthority._resolution_on_connection(
            con,
            subject,
            str(workspace["id"]),
            document_id,
        )
        if role is None or PAGE_ROLE_LEVEL[role] < PAGE_ROLE_LEVEL[minimum_role]:
            raise NotFoundOrDenied("not found or permission denied")
        if (
            int(workspace["security_epoch"]) != expected_workspace_epoch
            or int(document["access_epoch"]) != expected_document_epoch
        ):
            from .canonical_authority import EpochConflict

            raise EpochConflict(int(workspace["security_epoch"]), int(document["access_epoch"]))
        return workspace, document, role

    @staticmethod
    def _generation(con: sqlite3.Connection, document_id: str) -> sqlite3.Row:
        row = con.execute(
            "SELECT * FROM workspace_collaboration_crdt_generations "
            "WHERE document_id=? AND state='current'",
            (document_id,),
        ).fetchone()
        if row is None:
            raise AuthorityUnavailable("current CRDT generation is unavailable")
        return row

    @staticmethod
    def _editing_snapshot(con: sqlite3.Connection, document: sqlite3.Row) -> dict[str, Any]:
        descriptor = CanonicalAuthority._snapshot_on_connection(con, document)
        databases: list[dict[str, Any]] = []
        successor_tables = {
            str(row[0])
            for row in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name IN (?,?)",
                (
                    "workspace_collaboration_database_views",
                    "workspace_collaboration_database_templates",
                ),
            ).fetchall()
        }
        for database in con.execute(
            "SELECT * FROM workspace_collaboration_databases WHERE document_id=? ORDER BY id",
            (str(document["id"]),),
        ).fetchall():
            database_id = str(database["id"])
            properties = [
                {
                    "id": str(row["id"]),
                    "name": str(row["name"]),
                    "type": str(row["property_type"]),
                    "position": int(row["position"]),
                    "config": json.loads(str(row["config_json"])),
                    "version": int(row["row_version"]),
                }
                for row in con.execute(
                    "SELECT * FROM workspace_collaboration_database_properties "
                    "WHERE database_id=? ORDER BY position,id",
                    (database_id,),
                ).fetchall()
            ]
            rows: list[dict[str, Any]] = []
            for row in con.execute(
                "SELECT * FROM workspace_collaboration_database_rows "
                "WHERE database_id=? ORDER BY position,id",
                (database_id,),
            ).fetchall():
                row_id = str(row["id"])
                cell_rows = con.execute(
                    "SELECT * FROM workspace_collaboration_database_cells "
                    "WHERE row_id=? ORDER BY property_id",
                    (row_id,),
                ).fetchall()
                cells = {
                    str(cell["property_id"]): json.loads(str(cell["value_json"]))
                    for cell in cell_rows
                }
                cell_versions = {
                    str(cell["property_id"]): int(cell["row_version"])
                    for cell in cell_rows
                }
                rows.append(
                    {
                        "id": row_id,
                        "position": int(row["position"]),
                        "version": int(row["row_version"]),
                        "cells": cells,
                        "cell_versions": cell_versions,
                    }
                )
            views: list[dict[str, Any]] = []
            if "workspace_collaboration_database_views" in successor_tables:
                views = [
                    {
                        "id": str(row["id"]),
                        "name": str(row["name"]),
                        "type": str(row["view_type"]),
                        "position": int(row["position"]),
                        "config": json.loads(str(row["config_json"])),
                        "state": str(row["state"]),
                        "version": int(row["row_version"]),
                    }
                    for row in con.execute(
                        "SELECT * FROM workspace_collaboration_database_views "
                        "WHERE database_id=? ORDER BY position,id",
                        (database_id,),
                    ).fetchall()
                ]
            templates: list[dict[str, Any]] = []
            if "workspace_collaboration_database_templates" in successor_tables:
                templates = [
                    {
                        "id": str(row["id"]),
                        "name": str(row["name"]),
                        "body": json.loads(str(row["body_json"])),
                        "default_values": json.loads(str(row["default_values_json"])),
                        "state": str(row["state"]),
                        "version": int(row["row_version"]),
                    }
                    for row in con.execute(
                        "SELECT * FROM workspace_collaboration_database_templates "
                        "WHERE database_id=? ORDER BY created_at,id",
                        (database_id,),
                    ).fetchall()
                ]
            databases.append(
                {
                    "id": database_id,
                    "title": str(database["title"]),
                    "version": int(database["row_version"]),
                    "properties": properties,
                    "rows": rows,
                    "views": views,
                    "templates": templates,
                }
            )
        descriptor["databases"] = databases
        return descriptor

    @staticmethod
    def _check_target_version(row: sqlite3.Row, expected: int | None) -> None:
        if expected is None:
            return
        current = int(row["row_version"])
        if current != expected:
            raise TargetVersionConflict(current)

    @staticmethod
    def _rewrite_block_positions(
        con: sqlite3.Connection,
        document_id: str,
        ordered_ids: list[str],
        now: int,
    ) -> None:
        for position, block_id in enumerate(ordered_ids):
            con.execute(
                "UPDATE workspace_collaboration_blocks SET position=?,updated_at=? "
                "WHERE document_id=? AND id=?",
                (position, now, document_id, block_id),
            )

    @staticmethod
    def _clean_database_cell_value(
        con: sqlite3.Connection,
        *,
        actor_subject_id: str,
        property_row: sqlite3.Row,
        value: Any,
    ) -> Any:
        property_type = str(property_row["property_type"])
        if property_type in {"rollup", "formula", "created_time", "last_edited_time"}:
            raise ValueError("computed database property is read only")
        if property_type == "number":
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))):
                raise ValueError("number property requires a number or null")
            return value
        if property_type == "checkbox":
            if not isinstance(value, bool):
                raise ValueError("checkbox property requires a boolean")
            return value
        if property_type in {"title", "text", "date", "select", "status", "url", "email", "phone"}:
            if value is not None and not isinstance(value, str):
                raise ValueError(f"{property_type} property requires text or null")
            return value
        if property_type in {"multi_select", "person", "files"}:
            if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
                raise ValueError(f"{property_type} property requires an array")
            clean = list(value)
            if property_type == "multi_select" and any(not isinstance(item, str) for item in clean):
                raise ValueError("multi_select values must be text")
            return clean
        if property_type != "relation":
            return value
        if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
            raise ValueError("relation property requires an array of row ids")
        related_row_ids = [
            _identifier(str(item), field="related_row_id") for item in value
        ]
        if len(related_row_ids) != len(set(related_row_ids)) or len(related_row_ids) > 100:
            raise ValueError("relation row ids must be unique and bounded")
        try:
            config = json.loads(str(property_row["config_json"]))
        except (ValueError, json.JSONDecodeError) as exc:
            raise AuthorityUnavailable("relation property configuration is invalid") from exc
        target_database_id = _identifier(
            str(config.get("target_database_id") or ""), field="target_database_id"
        )
        target_database = con.execute(
            "SELECT database.id,database.document_id,document.workspace_id "
            "FROM workspace_collaboration_databases database "
            "JOIN workspace_collaboration_documents document ON document.id=database.document_id "
            "WHERE database.id=?",
            (target_database_id,),
        ).fetchone()
        if target_database is None:
            raise NotFoundOrDenied("not found or permission denied")
        role, _sources = CollaborativeIAMAuthority._resolution_on_connection(
            con,
            actor_subject_id,
            str(target_database["workspace_id"]),
            str(target_database["document_id"]),
        )
        if role is None:
            raise NotFoundOrDenied("not found or permission denied")
        if related_row_ids:
            placeholders = ",".join("?" for _item in related_row_ids)
            found = int(
                con.execute(
                    "SELECT COUNT(*) FROM workspace_collaboration_database_rows "
                    f"WHERE database_id=? AND id IN ({placeholders})",
                    (target_database_id, *related_row_ids),
                ).fetchone()[0]
            )
            if found != len(related_row_ids):
                raise NotFoundOrDenied("not found or permission denied")
        return related_row_ids

    def _apply_typed_domain(
        self,
        con: sqlite3.Connection,
        *,
        actor_subject_id: str,
        document_id: str,
        command_id: str,
        command_kind: str,
        target_id: str,
        payload: Mapping[str, Any],
        expected_target_version: int | None,
        document_sequence: int,
        created_at: int,
    ) -> dict[str, Any]:
        if command_kind.startswith("block."):
            ordered = [
                str(row["id"])
                for row in con.execute(
                    "SELECT id FROM workspace_collaboration_blocks "
                    "WHERE document_id=? ORDER BY position,id",
                    (document_id,),
                ).fetchall()
            ]
            if command_kind == "block.insert":
                if target_id in ordered or con.execute(
                    "SELECT 1 FROM workspace_collaboration_block_tombstones WHERE block_id=?",
                    (target_id,),
                ).fetchone() is not None:
                    raise ValueError("block identity already exists")
                after = payload.get("after_block_id")
                if after is not None:
                    after = _identifier(str(after), field="after_block_id")
                    if after not in ordered:
                        raise ValueError("after block does not exist")
                block_type = str(payload.get("type") or "paragraph").strip()
                if not block_type or len(block_type) > 100:
                    raise ValueError("valid block type required")
                content = str(payload.get("content") or "")
                props = payload.get("props", {})
                if not isinstance(props, Mapping):
                    raise ValueError("block props object required")
                insert_at = 0 if after is None else ordered.index(after) + 1
                ordered.insert(insert_at, target_id)
                con.execute(
                    "INSERT INTO workspace_collaboration_blocks("
                    "id,document_id,position,block_type,content,props_json,row_version,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?,1,?,?)",
                    (
                        target_id,
                        document_id,
                        insert_at,
                        block_type,
                        content,
                        _canonical_json(dict(props)),
                        created_at,
                        created_at,
                    ),
                )
                self._rewrite_block_positions(con, document_id, ordered, created_at)
                return {"target_version": 1, "position": insert_at}

            row = con.execute(
                "SELECT * FROM workspace_collaboration_blocks WHERE document_id=? AND id=?",
                (document_id, target_id),
            ).fetchone()
            if row is None:
                raise NotFoundOrDenied("not found or permission denied")
            self._check_target_version(row, expected_target_version)
            if command_kind == "block.move":
                after = payload.get("after_block_id")
                if after is not None:
                    after = _identifier(str(after), field="after_block_id")
                    if after == target_id or after not in ordered:
                        raise ValueError("valid move anchor required")
                ordered.remove(target_id)
                insert_at = 0 if after is None else ordered.index(after) + 1
                ordered.insert(insert_at, target_id)
                con.execute(
                    "UPDATE workspace_collaboration_blocks SET row_version=row_version+1,updated_at=? "
                    "WHERE document_id=? AND id=?",
                    (created_at, document_id, target_id),
                )
                self._rewrite_block_positions(con, document_id, ordered, created_at)
                return {"target_version": int(row["row_version"]) + 1, "position": insert_at}

            generation = self._generation(con, document_id)
            through_update = int(
                con.execute(
                    "SELECT COALESCE(MAX(update_row.update_sequence),0) "
                    "FROM workspace_collaboration_crdt_updates update_row "
                    "JOIN workspace_collaboration_crdt_update_bindings binding "
                    "ON binding.update_id=update_row.id "
                    "WHERE binding.document_id=? AND binding.generation=?",
                    (document_id, int(generation["generation"])),
                ).fetchone()[0]
            )
            recovery = {
                "block": {
                    "id": target_id,
                    "position": int(row["position"]),
                    "type": str(row["block_type"]),
                    "content": str(row["content"]),
                    "props": json.loads(str(row["props_json"])),
                    "version": int(row["row_version"]),
                },
                "rich_text_recovery": {
                    "crdt_generation": int(generation["generation"]),
                    "through_update_sequence": through_update,
                    "node_materialization_required": True,
                },
            }
            con.execute(
                "INSERT INTO workspace_collaboration_block_tombstones("
                "block_id,document_id,deletion_command_id,deleted_document_sequence,"
                "crdt_generation,through_update_sequence,recovery_json,created_at) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (
                    target_id,
                    document_id,
                    command_id,
                    document_sequence,
                    int(generation["generation"]),
                    through_update,
                    _canonical_json(recovery),
                    created_at,
                ),
            )
            con.execute(
                "DELETE FROM workspace_collaboration_blocks WHERE document_id=? AND id=?",
                (document_id, target_id),
            )
            ordered.remove(target_id)
            self._rewrite_block_positions(con, document_id, ordered, created_at)
            return {
                "target_version": int(row["row_version"]),
                "tombstoned": True,
                "recovery_through_update_sequence": through_update,
            }

        if command_kind == "database.property.rename":
            row = con.execute(
                "SELECT property.* FROM workspace_collaboration_database_properties property "
                "JOIN workspace_collaboration_databases database ON database.id=property.database_id "
                "WHERE database.document_id=? AND property.id=?",
                (document_id, target_id),
            ).fetchone()
            if row is None:
                raise NotFoundOrDenied("not found or permission denied")
            self._check_target_version(row, expected_target_version)
            name = str(payload.get("name") or "").strip()
            if not name or len(name) > 1000:
                raise ValueError("valid property name required")
            next_version = int(row["row_version"]) + 1
            con.execute(
                "UPDATE workspace_collaboration_database_properties "
                "SET name=?,row_version=?,updated_at=? WHERE id=?",
                (name, next_version, created_at, target_id),
            )
            return {"target_version": next_version, "name": name}

        row_id = _identifier(str(payload.get("row_id") or ""), field="row_id")
        property_id = _identifier(
            str(payload.get("property_id") or ""), field="property_id"
        )
        if target_id != f"{row_id}:{property_id}":
            raise ValueError("database cell target binding mismatch")
        bound = con.execute(
            "SELECT property.*,row_item.id AS bound_row_id "
            "FROM workspace_collaboration_database_rows row_item "
            "JOIN workspace_collaboration_databases database ON database.id=row_item.database_id "
            "JOIN workspace_collaboration_database_properties property "
            "ON property.database_id=database.id "
            "WHERE database.document_id=? AND row_item.id=? AND property.id=?",
            (document_id, row_id, property_id),
        ).fetchone()
        if bound is None:
            raise NotFoundOrDenied("not found or permission denied")
        cell = con.execute(
            "SELECT * FROM workspace_collaboration_database_cells "
            "WHERE row_id=? AND property_id=?",
            (row_id, property_id),
        ).fetchone()
        current_version = 0 if cell is None else int(cell["row_version"])
        if expected_target_version is not None and current_version != expected_target_version:
            raise TargetVersionConflict(current_version)
        next_version = current_version + 1
        clean_value = self._clean_database_cell_value(
            con,
            actor_subject_id=actor_subject_id,
            property_row=bound,
            value=payload.get("value"),
        )
        value_json = _canonical_json(clean_value)
        if cell is None:
            con.execute(
                "INSERT INTO workspace_collaboration_database_cells("
                "row_id,property_id,value_json,row_version,updated_at) VALUES(?,?,?,?,?)",
                (row_id, property_id, value_json, next_version, created_at),
            )
        else:
            con.execute(
                "UPDATE workspace_collaboration_database_cells "
                "SET value_json=?,row_version=?,updated_at=? WHERE row_id=? AND property_id=?",
                (value_json, next_version, created_at, row_id, property_id),
            )
        return {"target_version": next_version, "row_id": row_id, "property_id": property_id}

    def execute_typed_command(
        self,
        actor_subject_id: str,
        document_id: str,
        *,
        command_kind: str,
        target_id: str,
        payload: Mapping[str, Any],
        expected_document_sequence: int,
        expected_workspace_epoch: int,
        expected_document_epoch: int,
        operation_id: str,
        expected_target_version: int | None = None,
        fault: str | None = None,
        runtime_generation_id: str | None = None,
    ) -> dict[str, Any]:
        actor = _identifier(actor_subject_id, field="actor_subject_id")
        document = _identifier(document_id, field="document_id")
        kind = str(command_kind or "").strip().lower()
        if kind not in COMMAND_KINDS:
            raise ValueError("supported typed command kind required")
        target = _identifier(target_id, field="target_id")
        clean_payload = _command_payload(payload)
        expected_sequence = _nonnegative(
            expected_document_sequence, field="expected_document_sequence"
        )
        if expected_sequence < 1:
            raise ValueError("valid expected_document_sequence required")
        workspace_epoch = _nonnegative(expected_workspace_epoch, field="workspace_epoch")
        document_epoch = _nonnegative(expected_document_epoch, field="document_epoch")
        if workspace_epoch < 1 or document_epoch < 1:
            raise ValueError("positive access epochs required")
        target_version = (
            None
            if expected_target_version is None
            else _nonnegative(expected_target_version, field="expected_target_version")
        )
        operation = _operation_id(operation_id)
        action = "collaboration.typed_command"
        request = {
            "document_id": document,
            "command_kind": kind,
            "target_id": target,
            "payload": clean_payload,
            "expected_document_sequence": expected_sequence,
            "expected_target_version": target_version,
            "expected_workspace_epoch": workspace_epoch,
            "expected_document_epoch": document_epoch,
        }
        request_sha256 = _request_sha256(actor, action, request)
        with self._transaction() as con:
            def authorize() -> None:
                self._exact_access(
                    con,
                    actor,
                    document,
                    minimum_role=COMMAND_MINIMUM_ROLE[kind],
                    expected_workspace_epoch=workspace_epoch,
                    expected_document_epoch=document_epoch,
                )

            replay = self._replay(
                con,
                actor=actor,
                action=action,
                operation_id=operation,
                request_sha256=request_sha256,
                authorize=authorize,
            )
            if replay is not None:
                return replay
            workspace, document_row, role = self._exact_access(
                con,
                actor,
                document,
                minimum_role=COMMAND_MINIMUM_ROLE[kind],
                expected_workspace_epoch=workspace_epoch,
                expected_document_epoch=document_epoch,
            )
            current_sequence = int(document_row["document_sequence"])
            if current_sequence != expected_sequence:
                raise DocumentSequenceConflict(current_sequence)
            command_sequence = 1 + int(
                con.execute(
                    "SELECT COALESCE(MAX(command_sequence),0) "
                    "FROM workspace_collaboration_typed_commands WHERE document_id=?",
                    (document,),
                ).fetchone()[0]
            )
            next_document_sequence = current_sequence + 1
            command_id = _stable_id("tcmd", actor, document, operation)
            now = int(time.time())
            domain_result = self._apply_typed_domain(
                con,
                actor_subject_id=actor,
                document_id=document,
                command_id=command_id,
                command_kind=kind,
                target_id=target,
                payload=clean_payload,
                expected_target_version=target_version,
                document_sequence=next_document_sequence,
                created_at=now,
            )
            refreshed = self._document(con, document)
            content_sha256 = _sha256(_canonical_json(self._editing_snapshot(con, refreshed)))
            con.execute(
                "UPDATE workspace_collaboration_documents SET document_sequence=?,"
                "content_sha256=?,row_version=row_version+1,updated_at=? WHERE id=?",
                (next_document_sequence, content_sha256, now, document),
            )
            result = {
                "contract": EDITING_AUTHORITY_CONTRACT,
                "workspace_id": str(workspace["id"]),
                "document_id": document,
                "command_id": command_id,
                "command_kind": kind,
                "command_sequence": command_sequence,
                "document_sequence": next_document_sequence,
                "target_id": target,
                "effective_role": role,
                "content_sha256": content_sha256,
                **domain_result,
            }
            con.execute(
                "INSERT INTO workspace_collaboration_typed_commands("
                "id,workspace_id,document_id,command_sequence,document_sequence,"
                "actor_subject_id,operation_id,command_kind,target_id,"
                "expected_document_sequence,expected_target_version,payload_json,result_json,"
                "workspace_epoch,document_epoch,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    command_id,
                    str(workspace["id"]),
                    document,
                    command_sequence,
                    next_document_sequence,
                    actor,
                    operation,
                    kind,
                    target,
                    expected_sequence,
                    target_version,
                    _canonical_json(clean_payload),
                    _canonical_json(result),
                    workspace_epoch,
                    document_epoch,
                    now,
                ),
            )
            return self._finish(
                con,
                actor=actor,
                action=action,
                operation_id=operation,
                request_sha256=request_sha256,
                workspace_id=str(workspace["id"]),
                document_id=document,
                workspace_epoch=workspace_epoch,
                document_epoch=document_epoch,
                document_sequence=next_document_sequence,
                event_type="typed.command.committed",
                event_payload=result,
                result=result,
                fault=fault,
                runtime_generation_id=runtime_generation_id,
            )

    def create_revision_checkpoint(
        self,
        actor_subject_id: str,
        document_id: str,
        *,
        expected_revision: int,
        expected_document_sequence: int,
        expected_workspace_epoch: int,
        expected_document_epoch: int,
        operation_id: str,
        checkpoint_reason: str = "manual",
        publication_revision_id: str | None = None,
        fault: str | None = None,
        runtime_generation_id: str | None = None,
    ) -> dict[str, Any]:
        actor = _identifier(actor_subject_id, field="actor_subject_id")
        document = _identifier(document_id, field="document_id")
        revision = _nonnegative(expected_revision, field="expected_revision")
        sequence = _nonnegative(
            expected_document_sequence, field="expected_document_sequence"
        )
        workspace_epoch = _nonnegative(expected_workspace_epoch, field="workspace_epoch")
        document_epoch = _nonnegative(expected_document_epoch, field="document_epoch")
        if min(revision, sequence, workspace_epoch, document_epoch) < 1:
            raise ValueError("positive checkpoint versions required")
        reason = str(checkpoint_reason or "").strip().lower()
        if reason not in {"manual", "automatic", "pre_restore", "restore"}:
            raise ValueError("valid checkpoint reason required")
        publication = (
            None
            if publication_revision_id is None
            else _identifier(publication_revision_id, field="publication_revision_id")
        )
        operation = _operation_id(operation_id)
        action = "collaboration.revision_checkpointed"
        request = {
            "document_id": document,
            "expected_revision": revision,
            "expected_document_sequence": sequence,
            "expected_workspace_epoch": workspace_epoch,
            "expected_document_epoch": document_epoch,
            "checkpoint_reason": reason,
            "publication_revision_id": publication,
        }
        request_sha256 = _request_sha256(actor, action, request)
        with self._transaction() as con:
            def authorize() -> None:
                self._exact_access(
                    con,
                    actor,
                    document,
                    minimum_role="edit_content",
                    expected_workspace_epoch=workspace_epoch,
                    expected_document_epoch=document_epoch,
                )

            replay = self._replay(
                con,
                actor=actor,
                action=action,
                operation_id=operation,
                request_sha256=request_sha256,
                authorize=authorize,
            )
            if replay is not None:
                return replay
            workspace, document_row, _role = self._exact_access(
                con,
                actor,
                document,
                minimum_role="edit_content",
                expected_workspace_epoch=workspace_epoch,
                expected_document_epoch=document_epoch,
            )
            if int(document_row["document_sequence"]) != sequence:
                raise DocumentSequenceConflict(int(document_row["document_sequence"]))
            if int(document_row["revision"]) != revision:
                from .canonical_authority import RevisionConflict

                raise RevisionConflict(int(document_row["revision"]))
            generation_row = self._generation(con, document)
            generation = int(generation_row["generation"])
            update_high_water = int(
                con.execute(
                    "SELECT COALESCE(MAX(update_row.update_sequence),0) "
                    "FROM workspace_collaboration_crdt_updates update_row "
                    "JOIN workspace_collaboration_crdt_update_bindings binding "
                    "ON binding.update_id=update_row.id "
                    "WHERE binding.document_id=? AND binding.generation=?",
                    (document, generation),
                ).fetchone()[0]
            )
            snapshot_row = con.execute(
                "SELECT snapshot.* FROM workspace_collaboration_crdt_snapshots snapshot "
                "JOIN workspace_collaboration_crdt_snapshot_bindings binding "
                "ON binding.snapshot_id=snapshot.id "
                "WHERE binding.document_id=? AND binding.generation=? "
                "ORDER BY snapshot.through_update_sequence DESC LIMIT 1",
                (document, generation),
            ).fetchone()
            snapshot_high_water = 0 if snapshot_row is None else int(
                snapshot_row["through_update_sequence"]
            )
            if snapshot_high_water != update_high_water:
                raise CheckpointConflict("uncompacted rich-text updates")
            command_high_water = int(
                con.execute(
                    "SELECT COALESCE(MAX(command_sequence),0) "
                    "FROM workspace_collaboration_typed_commands WHERE document_id=?",
                    (document,),
                ).fetchone()[0]
            )
            snapshot = self._editing_snapshot(con, document_row)
            content_sha256 = _sha256(_canonical_json(snapshot))
            next_revision = revision + 1
            next_sequence = sequence + 1
            checkpoint_id = _stable_id("hist", document, str(next_revision), operation)
            now = int(time.time())
            con.execute(
                "UPDATE workspace_collaboration_documents SET revision=?,document_sequence=?,"
                "content_sha256=?,row_version=row_version+1,updated_at=? WHERE id=?",
                (next_revision, next_sequence, content_sha256, now, document),
            )
            con.execute(
                "INSERT INTO workspace_collaboration_revisions("
                "document_id,revision,document_sequence,content_sha256,snapshot_json,"
                "actor_subject_id,operation_id,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (
                    document,
                    next_revision,
                    next_sequence,
                    content_sha256,
                    _canonical_json(snapshot),
                    actor,
                    operation,
                    now,
                ),
            )
            con.execute(
                "INSERT INTO workspace_collaboration_revision_checkpoints("
                "id,workspace_id,document_id,revision,document_sequence,crdt_generation,"
                "crdt_snapshot_id,through_update_sequence,through_structure_command_sequence,"
                "content_sha256,actor_subject_id,operation_id,checkpoint_reason,"
                "publication_revision_id,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    checkpoint_id,
                    str(workspace["id"]),
                    document,
                    next_revision,
                    next_sequence,
                    generation,
                    None if snapshot_row is None else str(snapshot_row["id"]),
                    update_high_water,
                    command_high_water,
                    content_sha256,
                    actor,
                    operation,
                    reason,
                    publication,
                    now,
                ),
            )
            identities = {
                "client_operation_id": operation,
                "document_event_sequence": next_sequence,
                "crdt_generation": generation,
                "crdt_snapshot_id": None if snapshot_row is None else str(snapshot_row["id"]),
                "history_checkpoint_id": checkpoint_id,
                "user_revision": next_revision,
                "publication_revision_id": publication,
            }
            result = {
                "contract": EDITING_AUTHORITY_CONTRACT,
                "workspace_id": str(workspace["id"]),
                "document_id": document,
                "revision": next_revision,
                "document_sequence": next_sequence,
                "content_sha256": content_sha256,
                "through_update_sequence": update_high_water,
                "through_structure_command_sequence": command_high_water,
                "identities": identities,
            }
            return self._finish(
                con,
                actor=actor,
                action=action,
                operation_id=operation,
                request_sha256=request_sha256,
                workspace_id=str(workspace["id"]),
                document_id=document,
                workspace_epoch=workspace_epoch,
                document_epoch=document_epoch,
                document_sequence=next_sequence,
                event_type="revision.checkpointed",
                event_payload=result,
                result=result,
                fault=fault,
                runtime_generation_id=runtime_generation_id,
            )

    def list_revision_checkpoints(
        self, subject_id: str, document_id: str
    ) -> list[dict[str, Any]]:
        subject = self._require_active(subject_id)
        document = _identifier(document_id, field="document_id")
        with self._reader() as con:
            document_row = self._document(con, document)
            workspace = self._workspace(con, str(document_row["workspace_id"]))
            role, _sources = CollaborativeIAMAuthority._resolution_on_connection(
                con, subject, str(workspace["id"]), document
            )
            if role is None:
                raise NotFoundOrDenied("not found or permission denied")
            return [
                dict(row)
                for row in con.execute(
                    "SELECT id,revision,document_sequence,crdt_generation,crdt_snapshot_id,"
                    "through_update_sequence,through_structure_command_sequence,content_sha256,"
                    "actor_subject_id,operation_id,checkpoint_reason,publication_revision_id,created_at "
                    "FROM workspace_collaboration_revision_checkpoints "
                    "WHERE document_id=? ORDER BY revision DESC",
                    (document,),
                ).fetchall()
            ]


class EditingTransportCanonicalAuthority(CanonicalAuthority):
    """Legacy canonical access adapter that lets edit_content write CRDT bytes."""

    def _connect(self, *, query_only: bool) -> sqlite3.Connection:
        return CollaborativeEditingTransportAuthority(
            self.catalog_path, self.user_data_root
        )._connect(query_only=query_only)

    @classmethod
    def _effective_role(
        cls,
        con: sqlite3.Connection,
        workspace_id: str,
        document_id: str,
        subject_id: str,
    ) -> str | None:
        role, _sources = CollaborativeIAMAuthority._resolution_on_connection(
            con, subject_id, workspace_id, document_id
        )
        if role is None:
            return None
        return {
            "view": "view",
            "comment": "comment",
            "edit_content": "edit",
            "edit": "edit",
            "full_access": "owner",
        }[role]

    def replay_domain_events(
        self,
        subject_id: str,
        document_id: str,
        *,
        after_document_sequence: int,
        limit: int = 100,
    ) -> LiveDomainReplayBatch:
        """Expose live replay through the 2405 transport-safe canonical adapter."""

        subject = _identifier(subject_id, field="subject_id")
        document = _identifier(document_id, field="document_id")
        with self._reader() as con:
            workspace, page, _role = self._access(
                con, subject, document, minimum_role="view"
            )
            return replay_live_domain_events_on_connection(
                con,
                workspace=workspace,
                document=page,
                document_id=document,
                after_document_sequence=after_document_sequence,
                limit=limit,
            )


class CollaborativeEditingTransportAuthority(DurableTransportAuthority):
    """2405 durable transport compatible with the additive 2406 extension."""

    @classmethod
    def open(
        cls,
        catalog_path: str | os.PathLike[str],
        user_data_root: str | os.PathLike[str],
    ) -> "CollaborativeEditingTransportAuthority":
        catalog = Path(catalog_path).resolve(strict=True)
        root = Path(user_data_root).resolve(strict=True)
        expected = Path(nws.catalog_path_for_user_data_root(root)).resolve(strict=False)
        if catalog != expected:
            raise AuthorityUnavailable("collaborative editing transport path binding mismatch")
        con = sqlite3.connect(f"{catalog.as_uri()}?mode=ro", uri=True)
        try:
            con.execute("PRAGMA query_only=ON")
            if not _marker_current(con):
                raise AuthorityUnavailable("collaborative editing transport schema is not current")
        finally:
            con.close()
        return cls(catalog, root)

    def _connect(self, *, query_only: bool) -> sqlite3.Connection:
        if query_only:
            con = sqlite3.connect(
                f"{self.catalog_path.as_uri()}?mode=ro",
                uri=True,
                timeout=5.0,
                isolation_level=None,
            )
        else:
            con = sqlite3.connect(self.catalog_path, timeout=5.0, isolation_level=None)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA busy_timeout=5000")
        if query_only:
            con.execute("PRAGMA query_only=ON")
        if not _marker_current(con):
            con.close()
            raise AuthorityUnavailable("collaborative editing transport schema marker mismatch")
        return con

    def canonical_authority(self) -> EditingTransportCanonicalAuthority:
        return EditingTransportCanonicalAuthority(self.catalog_path, self.user_data_root)

    @staticmethod
    def _current_generation(con: sqlite3.Connection, document_id: str) -> int:
        row = con.execute(
            "SELECT generation FROM workspace_collaboration_crdt_generations "
            "WHERE document_id=? AND state='current'",
            (document_id,),
        ).fetchone()
        if row is None:
            raise AuthorityUnavailable("current CRDT generation is unavailable")
        return int(row["generation"])

    def _record_crdt_update_binding(
        self,
        con: sqlite3.Connection,
        *,
        update_id: str,
        envelope: ProtocolEnvelope,
        created_at: int,
    ) -> None:
        generation = self._current_generation(con, envelope.page_id)
        con.execute(
            "INSERT INTO workspace_collaboration_crdt_update_bindings("
            "update_id,document_id,generation,rich_text_scope,created_at) "
            "VALUES(?,?,?,'document_rich_text_leaves',?)",
            (update_id, envelope.page_id, generation, created_at),
        )

    def _record_crdt_snapshot_binding(
        self,
        con: sqlite3.Connection,
        *,
        snapshot_id: str,
        document_id: str,
        created_at: int,
    ) -> None:
        generation = self._current_generation(con, document_id)
        con.execute(
            "INSERT INTO workspace_collaboration_crdt_snapshot_bindings("
            "snapshot_id,document_id,generation,created_at) VALUES(?,?,?,?)",
            (snapshot_id, document_id, generation, created_at),
        )


__all__ = [
    "CheckpointConflict",
    "CollaborativeEditingAuthority",
    "CollaborativeEditingTransportAuthority",
    "COMMAND_KINDS",
    "DocumentSequenceConflict",
    "EDITING_AUTHORITY_CONTRACT",
    "EDITING_TRANSPORT_CONTRACT",
    "EditingTransportCanonicalAuthority",
    "TargetVersionConflict",
]
