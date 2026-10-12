"""E1-P1 canonical collaborative-editing authority.

The Python service never decodes Yjs updates.  It owns authorization,
idempotency, durable ordering, typed structural/database commands, and history
identity.  The selected Node/Yjs child remains the only CRDT compactor.
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import re
import sqlite3
import time
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from code_assist_v1 import notion_workspace_editing_authority_migration as migration
from code_assist_v1 import notion_workspace_healing_migration as migration_core
from code_assist_v1 import notion_workspace_store as nws
from code_assist_v1.notion_collaboration import (
    database_row_page_placement, database_row_trash, database_trash,
)
from code_assist_v1.notion_collaboration.database_file_links import clean_external_file_link
from code_assist_v1.notion_collaboration.canonical_authority import (
    AuthorityUnavailable,
    CanonicalAuthority,
    NotFoundOrDenied,
    OperationConflict,
    _canonical_json,
    _identifier,
    _operation_id,
    _request_sha256,
    _require_unlocked_or_temporary,
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


def _date_reminder_moment(start: str, clock: str | None, zone_name: str, preset: str) -> tuple[datetime, int]:
    """Resolve a date-cell reminder in its named zone, including DST boundaries."""
    try:
        zone = ZoneInfo(zone_name)
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise ValueError("unsupported date reminder time zone") from exc
    if preset in {"day_0", "day_1", "day_2", "day_7"}:
        if clock is not None and preset == "day_7":
            raise ValueError("unsupported timed date reminder")
        day = date.fromisoformat(start) - timedelta(days=int(preset[4:]))
        wall = datetime.fromisoformat(f"{day.isoformat()}T09:00")
        lead = 0
    elif preset in {"minute_0", "minute_5", "minute_10", "minute_15", "minute_30", "minute_60", "minute_120"} and clock is not None:
        wall = datetime.fromisoformat(f"{start}T{clock}")
        lead = int(preset[7:])
    else:
        raise ValueError("unsupported date reminder preset")
    moment = wall.replace(tzinfo=zone).astimezone(timezone.utc)
    if moment.astimezone(zone).replace(tzinfo=None) != wall:
        raise ValueError("date reminder time does not exist in the selected time zone")
    return moment, lead


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
    from code_assist_v1.notion_collaboration import database_row_content
    if version in {database_row_content.migration.TARGET_VERSION,
                   database_row_content.appearance_migration.TARGET_VERSION,
                   database_row_content.same_file_migration.TARGET_VERSION,
                   database_row_content.activity_migration.TARGET_VERSION,
                   database_row_content.trash_migration.TARGET_VERSION,
                   database_row_content.row_trash_migration.TARGET_VERSION,
                   database_row_content.placement_migration.TARGET_VERSION,
                   database_row_content.custom_emoji_migration.TARGET_VERSION,
                   database_row_content.item_layout_migration.TARGET_VERSION,
                   database_row_content.template_default_migration.TARGET_VERSION}:
        return database_row_content.installed(con)
    from code_assist_v1 import notion_workspace_native_team_migration as team_migration
    if version == team_migration.TARGET_VERSION:
        marker = con.execute("SELECT name,checksum,actual_schema_sha256 FROM workspace_schema_migrations WHERE version=?", (version,)).fetchone()
        return marker is not None and tuple(marker) == (team_migration.TARGET_NAME, team_migration.TARGET_CHECKSUM, team_migration.TARGET_ACTUAL_SCHEMA_SHA256)
    from code_assist_v1 import notion_workspace_file_copy_migration as file_copy_migration
    if version == file_copy_migration.TARGET_VERSION:
        marker = con.execute("SELECT name,checksum,actual_schema_sha256 FROM workspace_schema_migrations WHERE version=?", (version,)).fetchone()
        return marker is not None and tuple(marker) == (
            file_copy_migration.TARGET_NAME, file_copy_migration.TARGET_CHECKSUM,
            file_copy_migration.TARGET_ACTUAL_SCHEMA_SHA256,
        )
    from code_assist_v1 import notion_workspace_member_navigation_migration as member_navigation_migration
    if version == member_navigation_migration.TARGET_VERSION:
        marker = con.execute("SELECT name,checksum,actual_schema_sha256 FROM workspace_schema_migrations WHERE version=?", (version,)).fetchone()
        return marker is not None and tuple(marker) == (
            member_navigation_migration.TARGET_NAME, member_navigation_migration.TARGET_CHECKSUM,
            member_navigation_migration.TARGET_ACTUAL_SCHEMA_SHA256,
        )
    from code_assist_v1 import notion_workspace_native_page_migration as native_page_migration
    if version == native_page_migration.TARGET_VERSION:
        marker = con.execute("SELECT name,checksum,actual_schema_sha256 FROM workspace_schema_migrations WHERE version=?", (version,)).fetchone()
        return marker is not None and tuple(marker) == (
            native_page_migration.TARGET_NAME, native_page_migration.TARGET_CHECKSUM,
            native_page_migration.TARGET_ACTUAL_SCHEMA_SHA256,
        )
    from code_assist_v1 import notion_workspace_page_archive_migration as archive_migration
    if version == archive_migration.TARGET_VERSION:
        marker = con.execute("SELECT name,checksum,actual_schema_sha256 FROM workspace_schema_migrations WHERE version=?", (version,)).fetchone()
        return marker is not None and tuple(marker) == (
            archive_migration.TARGET_NAME, archive_migration.TARGET_CHECKSUM,
            archive_migration.TARGET_ACTUAL_SCHEMA_SHA256,
        )
    from code_assist_v1 import notion_workspace_guest_navigation_migration as guest_navigation_migration
    if version == guest_navigation_migration.TARGET_VERSION:
        marker = con.execute("SELECT name,checksum,actual_schema_sha256 FROM workspace_schema_migrations WHERE version=?", (version,)).fetchone()
        return marker is not None and tuple(marker) == (
            guest_navigation_migration.TARGET_NAME, guest_navigation_migration.TARGET_CHECKSUM,
            guest_navigation_migration.TARGET_ACTUAL_SCHEMA_SHA256,
        )
    from code_assist_v1 import notion_workspace_icon_image_migration as image_migration
    if version == image_migration.TARGET_VERSION:
        marker = con.execute("SELECT name,checksum,actual_schema_sha256 FROM workspace_schema_migrations WHERE version=?", (version,)).fetchone()
        return marker is not None and tuple(marker) == (
            image_migration.TARGET_NAME, image_migration.TARGET_CHECKSUM, image_migration.TARGET_ACTUAL_SCHEMA_SHA256,
        )
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
        initial_update_sequence: int = 0,
    ) -> None:
        if type(initial_update_sequence) is not int or initial_update_sequence not in {0, 1}:
            raise ValueError('initial copied update sequence must be zero or one')
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
            "VALUES(?,?,?,1,1,1,NULL,?,0,?,?,?,'initial',NULL,?)",
            (
                f"hist:{document_id}:r1",
                workspace_id,
                document_id,
                initial_update_sequence,
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

    def _extend_save_descriptor(self, con, document, descriptor):
        from code_assist_v1.notion_collaboration import database_row_content
        if database_row_content.installed(con):
            # A normal page save must retain the DB bodies (and file metadata)
            # from the same transaction. Submitted core fields replace only core fields.
            return {**self._editing_snapshot(con, document), **descriptor}
        return descriptor

    @staticmethod
    def _editing_snapshot(con: sqlite3.Connection, document: sqlite3.Row, *,
                          include_trashed_databases: frozenset[str] = frozenset()) -> dict[str, Any]:
        descriptor = CanonicalAuthority._snapshot_on_connection(con, document)
        from code_assist_v1.notion_collaboration import (
            database_trash, database_row_trash, database_row_page_placement,
        )
        trashed = database_trash.document_members(con, str(document["id"]))
        trashed_database_ids = {database_id for database_id, _ in trashed}
        trashed_block_ids = {block_id for _, block_id in trashed}
        if trashed_block_ids:
            descriptor["blocks"] = [block for block in descriptor["blocks"]
                if block["id"] not in trashed_block_ids]
        from code_assist_v1.notion_collaboration import database_row_content
        row_bodies = database_row_content.snapshot_bodies(con, str(document["id"]))
        from code_assist_v1.notion_collaboration import database_row_appearance
        row_appearance = database_row_appearance.snapshot_appearance(con, str(document["id"]))
        from code_assist_v1.notion_collaboration import database_item_page_layout
        from code_assist_v1.notion_collaboration import database_template_default
        item_layouts = {}
        template_defaults_installed = database_template_default.installed(con)
        if database_item_page_layout.installed(con):
            item_layouts = {
                str(row["database_id"]): {
                    "value": json.loads(str(row["layout_json"])),
                    "version": int(row["row_version"]),
                }
                for row in con.execute(
                    "SELECT layout.database_id,layout.layout_json,layout.row_version "
                    "FROM workspace_collaboration_database_item_page_layouts layout "
                    "JOIN workspace_collaboration_databases database "
                    "ON database.id=layout.database_id WHERE database.document_id=?",
                    (str(document["id"]),),
                ).fetchall()
            }
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
            if database_id in trashed_database_ids and database_id not in include_trashed_databases:
                continue
            hidden_row_ids = (database_row_trash.archived_row_ids(con, database_id)
                | database_row_page_placement.page_row_ids(con, database_id))
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
                if row_id in hidden_row_ids:
                    continue
                cell_rows = con.execute(
                    "SELECT cell.* FROM workspace_collaboration_database_cells cell "
                    "JOIN workspace_collaboration_database_properties property "
                    "ON property.id=cell.property_id "
                    "WHERE cell.row_id=? AND property.database_id=? "
                    "ORDER BY cell.property_id",
                    (row_id, database_id),
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
                        **({"body": row_bodies[row_id]} if row_id in row_bodies else {}),
                        **({"appearance":row_appearance[row_id]} if row_id in row_appearance else {}),
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
                    **({"default_template": database_template_default.read(con, database_id)}
                       if template_defaults_installed else {}),
                    **({"item_page_layout": item_layouts[database_id]}
                       if database_id in item_layouts else {}),
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
        previous_value: Any = None,
        target_database_access=None,
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
        if property_type == "date" and isinstance(value, Mapping):
            allowed = {"start", "end", "time", "end_time", "date_format", "time_format", "time_zone", "reminder"}
            if set(value) - allowed or "start" not in value:
                raise ValueError("date value has unsupported fields or lacks start")
            def clean_day(raw: Any, name: str) -> str:
                if not isinstance(raw, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
                    raise ValueError(f"date range {name} requires YYYY-MM-DD")
                try:
                    date.fromisoformat(raw)
                except ValueError as exc:
                    raise ValueError(f"date range {name} is invalid") from exc
                return raw
            start = clean_day(value["start"], "start")
            end = None if value.get("end") is None else clean_day(value["end"], "end")
            if end is not None and end < start:
                raise ValueError("date range end precedes start")
            clean: dict[str, Any] = {"start": start, "end": end}
            def clean_time(raw: Any, name: str) -> str:
                if not isinstance(raw, str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", raw):
                    raise ValueError(f"date {name} requires HH:mm")
                return raw
            if "time" in value:
                clean["time"] = clean_time(value["time"], "time")
            if "end_time" in value:
                if end is None or "time" not in clean:
                    raise ValueError("date end_time requires end and time")
                clean["end_time"] = clean_time(value["end_time"], "end_time")
            if end == start and clean.get("end_time", clean.get("time", "")) < clean.get("time", ""):
                raise ValueError("date range end time precedes start time")
            if "date_format" in value:
                if not isinstance(value["date_format"], str) or value["date_format"] not in {"full", "short", "mdy", "dmy", "ymd", "relative"}:
                    raise ValueError("unsupported date format")
                clean["date_format"] = value["date_format"]
            if "time_format" in value:
                if "time" not in clean or not isinstance(value["time_format"], str) or value["time_format"] not in {"hidden", "12h", "24h"}:
                    raise ValueError("unsupported time format")
                clean["time_format"] = value["time_format"]
            if "time_zone" in value:
                zone_name = value["time_zone"]
                if "time" not in clean or not isinstance(zone_name, str) or not zone_name or len(zone_name) > 100:
                    raise ValueError("date time zone requires a timed date")
                try:
                    zone = ZoneInfo(zone_name)
                except (ValueError, ZoneInfoNotFoundError) as exc:
                    raise ValueError("unsupported date time zone") from exc
                for day, clock in ((start, clean["time"]), (end, clean.get("end_time", clean["time"]))):
                    if day is None:
                        continue
                    wall = datetime.fromisoformat(f"{day}T{clock}")
                    if wall.replace(tzinfo=zone).astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) != wall:
                        raise ValueError("date time does not exist in the selected time zone")
                clean["time_zone"] = zone_name
            if "reminder" in value:
                reminder = value["reminder"]
                legacy_fields = {"at", "lead", "user_id"}
                tracked_fields = legacy_fields | {"preset", "zone"}
                if not isinstance(reminder, Mapping) or set(reminder) not in (legacy_fields, tracked_fields):
                    raise ValueError("date reminder has unsupported fields")
                at = reminder["at"]
                if not isinstance(at, str) or len(at) > 64:
                    raise ValueError("date reminder requires an ISO timestamp")
                try:
                    moment = datetime.fromisoformat(at.replace("Z", "+00:00"))
                except ValueError as exc:
                    raise ValueError("date reminder requires an ISO timestamp") from exc
                if moment.tzinfo is None or moment.utcoffset() is None:
                    raise ValueError("date reminder requires a timezone offset")
                lead = reminder["lead"]
                if isinstance(lead, bool) or not isinstance(lead, int) or not 0 <= lead <= 525600:
                    raise ValueError("date reminder lead is invalid")
                recipient = _identifier(str(reminder["user_id"]), field="reminder_user_id")
                previous_reminder = previous_value.get("reminder") if isinstance(previous_value, Mapping) else None
                if set(reminder) == tracked_fields:
                    preset, supplied_zone = reminder["preset"], reminder["zone"]
                    if not isinstance(preset, str) or not isinstance(supplied_zone, str) or not supplied_zone or len(supplied_zone) > 100:
                        raise ValueError("date reminder preset or zone is invalid")
                    effective_zone = clean.get("time_zone") or supplied_zone
                    expected, expected_lead = _date_reminder_moment(start, clean.get("time"), effective_zone, preset)
                    if lead != expected_lead:
                        raise ValueError("date reminder lead does not match its preset")
                    same_source = (isinstance(previous_reminder, Mapping)
                                   and previous_reminder.get("user_id") == recipient
                                   and previous_reminder.get("preset") == preset)
                    if recipient != actor_subject_id and (
                        not same_source or supplied_zone not in {previous_reminder.get("zone"), effective_zone}
                    ):
                        raise ValueError("cannot set another user's date reminder")
                    previous_at = None
                    if same_source:
                        try:
                            previous_at = datetime.fromisoformat(str(previous_reminder["at"]).replace("Z", "+00:00"))
                        except (KeyError, TypeError, ValueError):
                            pass
                    if moment.astimezone(timezone.utc) != expected and moment != previous_at:
                        raise ValueError("date reminder time does not match its preset")
                    clean["reminder"] = {
                        "at": expected.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                        "lead": lead, "user_id": recipient, "preset": preset, "zone": effective_zone,
                    }
                else:
                    if recipient != actor_subject_id and reminder != previous_reminder:
                        raise ValueError("cannot set another user's date reminder")
                    clean["reminder"] = {"at": at, "lead": lead, "user_id": recipient}
            return clean
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
            if property_type == "files":
                if len(clean) > 20:
                    raise ValueError("files property exceeds 20 files")
                database = con.execute(
                    "SELECT document_id FROM workspace_collaboration_databases WHERE id=?",
                    (str(property_row["database_id"]),),
                ).fetchone()
                if database is None:
                    raise NotFoundOrDenied("not found or permission denied")
                validated = []
                for item in clean:
                    if not isinstance(item, Mapping) or ("file_id" in item) == ("url" in item):
                        raise ValueError("files property requires a file or link")
                    if "url" in item:
                        validated.append(clean_external_file_link(item))
                        continue
                    if not set(item) <= {"file_id", "name", "original_name", "mime_type", "size"}:
                        raise ValueError("file has unsupported fields")
                    file_id = _identifier(item["file_id"], field="file_id")
                    exists = con.execute(
                        "SELECT 1 FROM workspace_collaboration_files "
                        "WHERE id=? AND document_id=? AND state='active'",
                        (file_id, str(database["document_id"])),
                    ).fetchone()
                    if exists is None:
                        raise NotFoundOrDenied("not found or permission denied")
                    validated.append({**item, "file_id": file_id})
                clean = validated
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
        database_trash.require_active(con, target_database_id)
        if target_database_access is not None:
            target_database_access(target_database_id, "view")
        else:
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
            if database_row_trash.archived_row_ids(con, target_database_id).intersection(related_row_ids):
                raise NotFoundOrDenied("not found or permission denied")
            from . import database_row_page_placement
            if database_row_page_placement.page_row_ids(
                    con, target_database_id).intersection(related_row_ids):
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
        target_database_access=None,
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
                tombstone = con.execute(
                    "SELECT * FROM workspace_collaboration_block_tombstones WHERE block_id=?",
                    (target_id,),
                ).fetchone()
                if target_id in ordered or (tombstone is not None and payload.get("restore_deleted") is not True):
                    raise ValueError("block identity already exists")
                target_version = 1
                restored_command = None
                if payload.get("restore_deleted") is True:
                    if tombstone is None or str(tombstone["document_id"]) != document_id:
                        raise NotFoundOrDenied("not found or permission denied")
                    # The first tombstone is immutable. Later deletion snapshots live
                    # in the equally immutable typed-command ledger, never overwrite it.
                    deletion = con.execute(
                        "SELECT id,result_json FROM workspace_collaboration_typed_commands "
                        "WHERE document_id=? AND target_id=? AND command_kind='block.delete' "
                        "ORDER BY document_sequence DESC LIMIT 1", (document_id, target_id),
                    ).fetchone()
                    if deletion is None:
                        raise AuthorityUnavailable("deleted block history is unavailable")
                    deleted_version = int(json.loads(str(deletion["result_json"]))["target_version"])
                    self._check_target_version({"row_version": deleted_version}, expected_target_version)
                    target_version = deleted_version + 1
                    restored_command = str(deletion["id"])
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
                    "VALUES(?,?,?,?,?,?,?,?,?)",
                    (
                        target_id,
                        document_id,
                        insert_at,
                        block_type,
                        content,
                        _canonical_json(dict(props)),
                        target_version,
                        created_at,
                        created_at,
                    ),
                )
                self._rewrite_block_positions(con, document_id, ordered, created_at)
                result = {"target_version": target_version, "position": insert_at}
                if restored_command:
                    result["restored_deletion_command_id"] = restored_command
                return result

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
                "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(block_id) DO NOTHING",
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
                "recovery": recovery,
            }

        if command_kind == "database.property.rename":
            row = con.execute(
                "SELECT property.*,database.id AS bound_database_id "
                "FROM workspace_collaboration_database_properties property "
                "JOIN workspace_collaboration_databases database ON database.id=property.database_id "
                "WHERE database.document_id=? AND property.id=?",
                (document_id, target_id),
            ).fetchone()
            if row is None:
                raise NotFoundOrDenied("not found or permission denied")
            database_trash.require_active(con, str(row["bound_database_id"]))
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
            "SELECT property.*,row_item.id AS bound_row_id,database.id AS bound_database_id "
            "FROM workspace_collaboration_database_rows row_item "
            "JOIN workspace_collaboration_databases database ON database.id=row_item.database_id "
            "JOIN workspace_collaboration_database_properties property "
            "ON property.database_id=database.id "
            "WHERE database.document_id=? AND row_item.id=? AND property.id=?",
            (document_id, row_id, property_id),
        ).fetchone()
        if bound is None:
            raise NotFoundOrDenied("not found or permission denied")
        database_trash.require_active(con, str(bound["bound_database_id"]))
        database_row_trash.require_active(con, str(bound["bound_database_id"]), row_id)
        database_row_page_placement.require_database_placement(
            con, str(bound["bound_database_id"]), row_id)
        cell = con.execute(
            "SELECT * FROM workspace_collaboration_database_cells "
            "WHERE row_id=? AND property_id=?",
            (row_id, property_id),
        ).fetchone()
        current_version = 0 if cell is None else int(cell["row_version"])
        if expected_target_version is not None and current_version != expected_target_version:
            raise TargetVersionConflict(current_version)
        next_version = current_version + 1
        previous_value = json.loads(str(cell["value_json"])) if cell is not None else None
        clean_value = self._clean_database_cell_value(
            con,
            actor_subject_id=actor_subject_id,
            property_row=bound,
            value=payload.get("value"),
            previous_value=previous_value,
            target_database_access=target_database_access,
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
        from .database_relation_pair import apply_reciprocal_cell_edit
        reciprocal = apply_reciprocal_cell_edit(
            self, con, actor=actor_subject_id, source_document_id=document_id,
            source_row_id=row_id, source_property=bound,
            previous_value=previous_value, new_value=clean_value,
            now=created_at,
            target_database_access=target_database_access,
        )
        result = {"target_version": next_version, "row_id": row_id,
                  "property_id": property_id}
        if reciprocal is not None:
            result["_reciprocal_document_id"] = reciprocal[0]
            result["_reciprocal_database_id"] = reciprocal[1]
        return result

    def typed_command_receipt(self, actor_subject_id: str, document_id: str, operation_id: str) -> dict[str, Any] | None:
        actor = _identifier(actor_subject_id, field="actor_subject_id")
        document = _identifier(document_id, field="document_id")
        operation = _operation_id(operation_id)
        with self._reader() as con:
            con.execute("BEGIN")
            page = self._document(con, document)
            workspace = self._workspace(con, str(page["workspace_id"]))
            row = con.execute(
                "SELECT * FROM workspace_collaboration_typed_commands "
                "WHERE document_id=? AND actor_subject_id=? AND operation_id=?",
                (document, actor, operation),
            ).fetchone()
            self._exact_access(con, actor, document,
                minimum_role=COMMAND_MINIMUM_ROLE[str(row["command_kind"])] if row else "edit_content",
                expected_workspace_epoch=int(workspace["security_epoch"]), expected_document_epoch=int(page["access_epoch"]))
            if row is None:
                return None
            committed = con.execute(
                "SELECT result_json FROM workspace_collaboration_operations "
                "WHERE actor_subject_id=? AND action='collaboration.typed_command' AND operation_id=?",
                (actor, operation),
            ).fetchone()
            if committed is None:
                raise AuthorityUnavailable("typed command receipt is unavailable")
            self._require_active(actor)
            return {"command": {
                "command_kind": str(row["command_kind"]), "target_id": str(row["target_id"]),
                "payload": json.loads(str(row["payload_json"])), "operation_id": operation,
                "expected_document_sequence": int(row["expected_document_sequence"]),
                "expected_target_version": row["expected_target_version"],
                "expected_workspace_epoch": int(row["workspace_epoch"]),
                "expected_document_epoch": int(row["document_epoch"]),
            }, "result": json.loads(str(committed["result_json"]))}

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
        _linked_source_scope=None,
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
        if _linked_source_scope is not None:
            from .linked_databases import _LinkedSourceInsertScope
            if kind != "block.insert" or not isinstance(
                    _linked_source_scope, _LinkedSourceInsertScope):
                raise ValueError("prepared linked source insert scope required")
            props = clean_payload.get("props")
            if (not isinstance(props, Mapping)
                    or props.get("linked_database") != {
                        "database_id": _linked_source_scope.database}):
                raise ValueError("database-only linked reference required")
            request["linked_source"] = _linked_source_scope.request_context()
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
                if _linked_source_scope is not None:
                    _linked_source_scope.authorize(self, con, actor)

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
            if _linked_source_scope is not None:
                _linked_source_scope.authorize(self, con, actor)
            _require_unlocked_or_temporary(con, document_row, actor)
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
            reciprocal_document = domain_result.pop("_reciprocal_document_id", None)
            reciprocal_database = domain_result.pop("_reciprocal_database_id", None)
            refreshed = self._document(con, document)
            content_sha256 = _sha256(_canonical_json(self._editing_snapshot(con, refreshed)))
            con.execute(
                "UPDATE workspace_collaboration_documents SET document_sequence=?,"
                "content_sha256=?,row_version=row_version+1,updated_at=? WHERE id=?",
                (next_document_sequence, content_sha256, now, document),
            )
            reciprocal_effects = []
            if reciprocal_document is not None:
                from .database_relation_pair import commit_reciprocal_documents
                reciprocal_effects = commit_reciprocal_documents(
                    self, con, actor=actor, source_document_id=document,
                    operation_id=operation,
                    affected={str(reciprocal_document): {str(reciprocal_database)}},
                    now=now,
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
            if reciprocal_effects:
                result["reciprocal_documents"] = reciprocal_effects
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
