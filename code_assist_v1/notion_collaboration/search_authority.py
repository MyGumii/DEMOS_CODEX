"""Permission-aware search, backlinks, reminders, and transfer authority."""

from __future__ import annotations

import base64
from contextlib import contextmanager
import csv
from datetime import datetime, timedelta
from html import escape
from html.parser import HTMLParser
import io
import json
import os
from pathlib import Path
import re
import sqlite3
import time
from typing import Any, Iterable, Mapping, Sequence

from code_assist_v1 import notion_workspace_healing_migration as migration_core
from code_assist_v1 import notion_workspace_search_authority_migration as schema_2410
from code_assist_v1 import notion_workspace_database_activity_migration as schema_2421
from code_assist_v1 import notion_workspace_database_trash_migration as schema_2422
from code_assist_v1 import notion_workspace_database_row_trash_migration as schema_2423
from code_assist_v1 import notion_workspace_database_row_page_placement_migration as schema_2424
from code_assist_v1 import notion_workspace_custom_emoji_migration as schema_2425
from code_assist_v1 import notion_workspace_database_item_page_layout_migration as schema_2426
from code_assist_v1 import notion_workspace_database_template_default_migration as schema_2427
from code_assist_v1 import notion_icons
from code_assist_v1 import notion_html_blocks
from code_assist_v1 import notion_html_database
from code_assist_v1 import notion_database_export_bundle
from code_assist_v1 import notion_database_export_assets
from code_assist_v1.notion_collaboration.canonical_authority import (
    AuthorityUnavailable,
    NotFoundOrDenied,
    _canonical_json,
    _identifier,
    _sha256,
    _stable_id,
)
from code_assist_v1.notion_collaboration.history_authority import (
    CollaborativeHistoryAuthority,
)
from code_assist_v1.notion_collaboration.iam_authority import (
    CollaborativeIAMAuthority,
    InMemoryIdentityDirectory,
)
from code_assist_v1.notion_collaboration.title_projection import read_current_titles, retry_title_projection
from code_assist_v1.notion_collaboration.title_projection import _frames, _generation
from code_assist_v1.notion_collaboration import search_projection, visible_text


AUTHORITY_CONTRACT = "code_assist.notion.collaborative_search_authority.v1"
TRANSFER_CONTRACT = "code_assist.notion.collaborative_transfer_bundle.v1"
MAX_QUERY_CHARS = 500
MAX_RESULT_LIMIT = 100
MAX_TRANSFER_BYTES = 2_000_000
MAX_INDEX_TEXT_CHARS = 2_000_000
SNAPSHOT_MARKER_RE = re.compile(
    r"<!--\s*code-assist-notion-snapshot-v1:([A-Za-z0-9_-]+)\s*-->",
    re.IGNORECASE,
)
TAG_RE = re.compile(r"<[^>]+>")


def _marker_current(con: sqlite3.Connection) -> bool:
    row = con.execute(
        "SELECT version,name,checksum,actual_schema_sha256 "
        "FROM workspace_schema_migrations ORDER BY version DESC LIMIT 1"
    ).fetchone()
    return row is not None and tuple(row) in {
        (migration.TARGET_VERSION, migration.TARGET_NAME,
         migration.TARGET_CHECKSUM, migration.TARGET_ACTUAL_SCHEMA_SHA256)
        for migration in (schema_2410, schema_2421, schema_2422, schema_2423, schema_2424, schema_2425, schema_2426, schema_2427)
    }


def _text(value: Any, *, limit: int = MAX_INDEX_TEXT_CHARS) -> str:
    parts: list[str] = []
    size = 0

    def visit(item: Any) -> None:
        nonlocal size
        if size >= limit or item is None or isinstance(item, bool):
            return
        if isinstance(item, (str, int, float)):
            raw = TAG_RE.sub(" ", str(item))
            clean = " ".join(raw.split())
            if clean:
                chunk = clean[: max(0, limit - size)]
                parts.append(chunk)
                size += len(chunk) + 1
            return
        if isinstance(item, Mapping):
            for key in sorted(item, key=str):
                if str(key) in {"content_sha256", "blob_relative_path"}:
                    continue
                visit(item[key])
            return
        if isinstance(item, Sequence) and not isinstance(item, (bytes, bytearray)):
            for child in item:
                visit(child)

    visit(value)
    return "\n".join(parts)[:limit]


def _snippet(text: str, terms: Sequence[str], *, limit: int = 240) -> str:
    clean = " ".join(str(text or "").split())
    if not clean:
        return ""
    folded = clean.casefold()
    positions = [folded.find(term) for term in terms if term and folded.find(term) >= 0]
    start = max(0, min(positions) - 70) if positions else 0
    end = min(len(clean), start + limit)
    prefix = "…" if start else ""
    suffix = "…" if end < len(clean) else ""
    return prefix + clean[start:end] + suffix


def _parse_iso_timestamp(value: Any, lead: Any) -> tuple[str, int, int] | None:
    raw = str(value or "").strip()
    if not raw or len(raw) > 64:
        return None
    try:
        moment = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        lead_minutes = max(0, min(525600, int(lead or 0)))
    except (TypeError, ValueError, OverflowError):
        return None
    return raw, int((moment - timedelta(minutes=lead_minutes)).timestamp()), lead_minutes


class _ReminderParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.items: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "span":
            return
        value = {str(key).lower(): str(item or "") for key, item in attrs}
        if value.get("data-notion-reminder"):
            self.items.append(value)


def _nested_reminders(value: Any, path: str = "$") -> Iterable[tuple[str, Mapping[str, Any]]]:
    if isinstance(value, Mapping):
        if "at" in value and "user_id" in value and (
            "lead" in value or "id" in value
        ):
            yield path, value
        for key in sorted(value, key=str):
            yield from _nested_reminders(value[key], f"{path}.{key}")
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, item in enumerate(value):
            yield from _nested_reminders(item, f"{path}[{index}]")


class CollaborativeSearchAuthority(CollaborativeHistoryAuthority):
    @classmethod
    def open(
        cls,
        catalog_path: str | os.PathLike[str],
        user_data_root: str | os.PathLike[str],
        identity_directory: InMemoryIdentityDirectory,
        **kwargs: Any,
    ) -> "CollaborativeSearchAuthority":
        try:
            catalog, root = migration_core._resolved_target(catalog_path, user_data_root)
            con = sqlite3.connect(f"{catalog.as_uri()}?mode=ro", uri=True)
            try:
                if not _marker_current(con):
                    raise AuthorityUnavailable("collaborative search schema is not current")
            finally:
                con.close()
        except AuthorityUnavailable:
            raise
        except (OSError, sqlite3.Error, RuntimeError) as exc:
            raise AuthorityUnavailable("collaborative search authority is unavailable") from exc
        authority = cls(Path(catalog), Path(root), identity_directory, **kwargs)
        authority._validate_storage()
        return authority

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
                con = sqlite3.connect(
                    self.catalog_path, timeout=5.0, isolation_level=None
                )
            con.row_factory = sqlite3.Row
            con.execute("PRAGMA foreign_keys=ON")
            con.execute("PRAGMA busy_timeout=5000")
            if query_only:
                con.execute("PRAGMA query_only=ON")
            if not _marker_current(con):
                con.close()
                raise AuthorityUnavailable("collaborative search schema marker mismatch")
            return con
        except AuthorityUnavailable:
            raise
        except (OSError, sqlite3.Error) as exc:
            raise AuthorityUnavailable("collaborative search authority is unavailable") from exc

    @classmethod
    def _require_workspace_visibility(
        cls, con: sqlite3.Connection, actor_subject_id: str, workspace_id: str
    ) -> sqlite3.Row:
        workspace = cls._workspace(con, workspace_id)
        direct = con.execute(
            "SELECT 1 FROM workspace_memberships WHERE workspace_id=? AND subject_id=? "
            "AND revoked_at IS NULL UNION ALL "
            "SELECT 1 FROM workspace_guests WHERE workspace_id=? AND subject_id=? "
            "AND revoked_at IS NULL LIMIT 1",
            (workspace_id, actor_subject_id, workspace_id, actor_subject_id),
        ).fetchone()
        if direct is not None:
            return workspace
        for row in con.execute(
            "SELECT id FROM workspace_collaboration_documents WHERE workspace_id=?",
            (workspace_id,),
        ).fetchall():
            role, _sources = CollaborativeIAMAuthority._resolution_on_connection(
                con, actor_subject_id, workspace_id, str(row["id"])
            )
            if role is not None:
                return workspace
        raise NotFoundOrDenied("not found or permission denied")

    @staticmethod
    def _projection_sha256(con: sqlite3.Connection, workspace_id: str) -> str:
        payload = {
            "text_contract": visible_text.CONTRACT,
            "body_contract": search_projection.CONTRACT,
            "documents": [
                dict(row)
                for row in con.execute(
                    "SELECT document_id,legacy_owner_subject_id,revision,document_sequence,"
                    "access_epoch,content_sha256,title,body_text,search_text,source_updated_at "
                    "FROM workspace_collaboration_search_documents WHERE workspace_id=? "
                    "ORDER BY document_id",
                    (workspace_id,),
                ).fetchall()
            ],
            "backlinks": [
                dict(row)
                for row in con.execute(
                    "SELECT target_document_id,source_document_id,source_block_id,"
                    "source_revision,context_text FROM workspace_collaboration_search_backlinks "
                    "WHERE workspace_id=? ORDER BY target_document_id,source_document_id,source_block_id",
                    (workspace_id,),
                ).fetchall()
            ],
            "reminders": [
                dict(row)
                for row in con.execute(
                    "SELECT reminder_id,document_id,block_id,source_locator,recipient_subject_id,"
                    "remind_at,trigger_at,lead_minutes,message,source_revision "
                    "FROM workspace_collaboration_search_reminders WHERE workspace_id=? "
                    "ORDER BY reminder_id",
                    (workspace_id,),
                ).fetchall()
            ],
        }
        return _sha256(_canonical_json(payload))

    @staticmethod
    def _state_needs_rebuild(con: sqlite3.Connection, workspace_id: str) -> bool:
        row = con.execute(
            "SELECT * FROM workspace_collaboration_search_state WHERE workspace_id=?",
            (workspace_id,),
        ).fetchone()
        if row is None or int(row["built_generation"]) != int(row["desired_generation"]):
            return True
        source_count = int(
            con.execute(
                "SELECT COUNT(*) FROM workspace_collaboration_documents "
                "WHERE workspace_id=? AND archived=0",
                (workspace_id,),
            ).fetchone()[0]
        )
        indexed_count = int(
            con.execute(
                "SELECT COUNT(*) FROM workspace_collaboration_search_documents WHERE workspace_id=?",
                (workspace_id,),
            ).fetchone()[0]
        )
        if source_count != indexed_count or source_count != int(row["source_document_count"]):
            return True
        return (
            not row["last_rebuild_sha256"]
            or str(row["last_rebuild_sha256"])
            != CollaborativeSearchAuthority._projection_sha256(con, workspace_id)
        )

    @staticmethod
    def _insert_reminder(
        con: sqlite3.Connection,
        *,
        workspace_id: str,
        document_id: str,
        block_id: str,
        source_locator: str,
        recipient: Any,
        remind_at: Any,
        lead: Any,
        supplied_id: Any,
        message: str,
        revision: int,
        indexed_at: int,
    ) -> None:
        parsed = _parse_iso_timestamp(remind_at, lead)
        subject = str(recipient or "").strip()
        if parsed is None or not subject or len(subject) > 200:
            return
        raw_at, trigger_at, lead_minutes = parsed
        supplied = str(supplied_id or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", supplied):
            supplied = ""
        # Client reminder IDs are labels, not globally authoritative keys.  Scope
        # them to the canonical source so two imported pages cannot replace one
        # another's projection row by reusing the same client-side ID.
        reminder_id = _stable_id(
            "search-reminder",
            workspace_id,
            document_id,
            block_id,
            source_locator,
            subject,
            raw_at,
            supplied,
        )
        con.execute(
            "INSERT OR REPLACE INTO workspace_collaboration_search_reminders("
            "reminder_id,workspace_id,document_id,block_id,source_locator,recipient_subject_id,"
            "remind_at,trigger_at,lead_minutes,message,source_revision,indexed_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                reminder_id,
                workspace_id,
                document_id,
                block_id,
                source_locator,
                subject,
                raw_at,
                trigger_at,
                lead_minutes,
                message[:500],
                revision,
                indexed_at,
            ),
        )

    def _search_source_stamp(self, con, workspace_id):
        workspace = self._workspace(con, workspace_id)
        state = con.execute(
            "SELECT desired_generation FROM workspace_collaboration_search_state WHERE workspace_id=?",
            (workspace_id,),
        ).fetchone()
        documents = con.execute(
            "SELECT * FROM workspace_collaboration_documents "
            "WHERE workspace_id=? AND archived=0 ORDER BY id", (workspace_id,),
        ).fetchall()
        stamp = (int(workspace["security_epoch"]), int(state[0]) if state else 0,
                 tuple((tuple(row), _generation(con, str(row["id"]))) for row in documents))
        return documents, stamp

    def _prepare_search_projection(self, actor, workspace_id):
        self._require_active(actor)
        with self._reader() as con:
            con.execute("BEGIN")
            self._require_workspace_visibility(con, actor, workspace_id)
            documents, stamp = self._search_source_stamp(con, workspace_id)
            descriptors, frame_sets, frame_ids = {}, [], []
            from . import database_trash
            hidden_blocks = {}
            for document in documents:
                document_id = str(document["id"])
                descriptors[document_id] = self._editing_snapshot(con, document)
                hidden_blocks[document_id] = {block_id for _, block_id in
                    database_trash.document_members(con, document_id)}
                frames = _frames(con, document_id, _generation(con, document_id))
                if frames:
                    frame_ids.append(document_id)
                    frame_sets.append(frames)
        # Replay may run Node.js; no database transaction is held here.
        for document_id, current in zip(frame_ids, search_projection.materialize(frame_sets)):
            if current["title"] is not None:
                descriptors[document_id]["title"] = current["title"]
            if current["blocks"] is not None:
                descriptors[document_id]["blocks"] = [block for block in current["blocks"]
                    if block.get("id") not in hidden_blocks[document_id]]
        return {"stamp": stamp, "descriptors": descriptors}

    def _refresh_search_index(self, actor, workspace_id, *, force=False):
        for _attempt in range(3):
            prepared = self._prepare_search_projection(actor, workspace_id)
            self._require_active(actor)
            try:
                with self._transaction() as con:
                    self._require_workspace_visibility(con, actor, workspace_id)
                    if not force and not self._state_needs_rebuild(con, workspace_id):
                        return self._state_descriptor(con, workspace_id)
                    return self._rebuild_workspace(con, workspace_id, prepared)
            except search_projection.SearchProjectionChanged:
                continue
        raise AuthorityUnavailable("document changed while rebuilding search; retry")

    def _rebuild_workspace(self, con: sqlite3.Connection, workspace_id: str, prepared) -> dict[str, Any]:
        documents, stamp = self._search_source_stamp(con, workspace_id)
        if stamp != prepared["stamp"]:
            raise search_projection.SearchProjectionChanged()
        state = con.execute(
            "SELECT desired_generation FROM workspace_collaboration_search_state WHERE workspace_id=?",
            (workspace_id,),
        ).fetchone()
        if state is None:
            con.execute(
                "INSERT INTO workspace_collaboration_search_state(workspace_id) VALUES(?)",
                (workspace_id,),
            )
            desired_generation = 1
        else:
            desired_generation = int(state["desired_generation"])
        con.execute(
            "DELETE FROM workspace_collaboration_search_reminders WHERE workspace_id=?",
            (workspace_id,),
        )
        con.execute(
            "DELETE FROM workspace_collaboration_search_backlinks WHERE workspace_id=?",
            (workspace_id,),
        )
        con.execute(
            "DELETE FROM workspace_collaboration_search_documents WHERE workspace_id=?",
            (workspace_id,),
        )
        now = int(time.time())
        descriptors = prepared["descriptors"]
        for document in documents:
            document_id = str(document["id"])
            descriptor = descriptors[document_id]
            body = visible_text.document_text(descriptor, limit=MAX_INDEX_TEXT_CHARS)
            title = str(descriptor.get("title", document["title"]))
            con.execute(
                "INSERT INTO workspace_collaboration_search_documents("
                "document_id,workspace_id,legacy_owner_subject_id,revision,document_sequence,"
                "access_epoch,content_sha256,title,body_text,search_text,indexed_at,source_updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    document_id,
                    workspace_id,
                    str(document["legacy_owner_subject_id"]),
                    int(document["revision"]),
                    int(document["document_sequence"]),
                    int(document["access_epoch"]),
                    str(document["content_sha256"]),
                    title,
                    (body or title)[:MAX_INDEX_TEXT_CHARS],
                    f"{title}\n{body}".casefold()[:MAX_INDEX_TEXT_CHARS],
                    now,
                    int(document["updated_at"]),
                ),
            )
        target_ids = sorted(descriptors)
        for source_id, descriptor in descriptors.items():
            source_revision = int(
                next(row["revision"] for row in documents if str(row["id"]) == source_id)
            )
            for block in descriptor.get("blocks", []):
                if not isinstance(block, Mapping):
                    continue
                block_id = str(block.get("id") or "")
                content = str(block.get("content") or "")
                props = block.get("props") if isinstance(block.get("props"), Mapping) else {}
                props_json = _canonical_json(props)
                context = _snippet(visible_text.block_text(block, limit=500), (), limit=500)
                for target_id in target_ids:
                    if (
                        target_id != source_id
                        and (
                            target_id in props_json
                            or f"/page/{target_id}" in content
                        )
                    ):
                        con.execute(
                            "INSERT OR IGNORE INTO workspace_collaboration_search_backlinks("
                            "workspace_id,target_document_id,source_document_id,source_block_id,"
                            "source_revision,context_text,indexed_at) VALUES(?,?,?,?,?,?,?)",
                            (
                                workspace_id,
                                target_id,
                                source_id,
                                block_id,
                                source_revision,
                                context,
                                now,
                            ),
                        )
                parser = _ReminderParser()
                try:
                    parser.feed(str(props.get("rich_text_html") or content))
                except (ValueError, RecursionError):
                    parser.items = []
                for index, item in enumerate(parser.items):
                    self._insert_reminder(
                        con,
                        workspace_id=workspace_id,
                        document_id=source_id,
                        block_id=block_id,
                        source_locator=f"inline:{index}",
                        recipient=item.get("data-notion-reminder-user"),
                        remind_at=item.get("data-notion-reminder"),
                        lead=item.get("data-notion-reminder-lead"),
                        supplied_id=item.get("data-notion-reminder-id"),
                        message=context,
                        revision=source_revision,
                        indexed_at=now,
                    )
                for locator, reminder in _nested_reminders(props):
                    self._insert_reminder(
                        con,
                        workspace_id=workspace_id,
                        document_id=source_id,
                        block_id=block_id,
                        source_locator=locator,
                        recipient=reminder.get("user_id"),
                        remind_at=reminder.get("at"),
                        lead=reminder.get("lead"),
                        supplied_id=reminder.get("id"),
                        message=context,
                        revision=source_revision,
                        indexed_at=now,
                    )
            from . import database_row_trash, database_row_page_placement, database_trash
            trash_filter = ("AND NOT EXISTS (SELECT 1 FROM "
                "workspace_collaboration_database_trash_state t "
                "WHERE t.database_id=database.id) "
                if database_trash.installed(con) else "")
            row_trash_filter = ("AND NOT EXISTS (SELECT 1 FROM "
                "workspace_collaboration_database_row_trash_state rt "
                "WHERE rt.database_id=database.id AND rt.row_id=row_item.id) "
                if database_row_trash.installed(con) else "")
            page_placement_filter = ("AND NOT EXISTS (SELECT 1 FROM "
                "workspace_collaboration_database_row_page_placements placement "
                "WHERE placement.row_id=row_item.id AND placement.placement='page') "
                if database_row_page_placement.installed(con) else "")
            for cell in con.execute(
                "SELECT database.id AS database_id,database.title AS database_title,"
                "row_item.id AS row_id,property.id AS property_id,property.name AS property_name,"
                "value.value_json "
                "FROM workspace_collaboration_databases database "
                "JOIN workspace_collaboration_database_rows row_item ON row_item.database_id=database.id "
                "JOIN workspace_collaboration_database_properties property "
                "ON property.database_id=database.id AND property.property_type='date' "
                "JOIN workspace_collaboration_database_cells value "
                "ON value.row_id=row_item.id AND value.property_id=property.id "
                "WHERE database.document_id=? " + trash_filter + row_trash_filter
                + page_placement_filter +
                "ORDER BY database.id,row_item.id,property.id",
                (source_id,),
            ).fetchall():
                try:
                    date_value = json.loads(str(cell["value_json"]))
                except (TypeError, ValueError):
                    continue
                reminder = date_value.get("reminder") if isinstance(date_value, Mapping) else None
                if not isinstance(reminder, Mapping):
                    continue
                self._insert_reminder(
                    con,
                    workspace_id=workspace_id,
                    document_id=source_id,
                    block_id=str(cell["database_id"]),
                    source_locator=f"database:{cell['row_id']}:{cell['property_id']}",
                    recipient=reminder.get("user_id"),
                    remind_at=reminder.get("at"),
                    lead=reminder.get("lead"),
                    supplied_id=None,
                    message=f"{cell['database_title']} · {cell['property_name']}",
                    revision=source_revision,
                    indexed_at=now,
                )
            if database_row_page_placement.installed(con):
                # A row placed as an ordinary page keeps its canonical date
                # cell, even though that property is hidden from the page UI.
                # Route its reminder to the live page identity, not the old DB.
                for cell in con.execute(
                    "SELECT property.id AS property_id,"
                    "property.name AS property_name,value.value_json "
                    "FROM workspace_collaboration_database_row_page_placements placement "
                    "JOIN workspace_collaboration_database_rows row_item "
                    "ON row_item.id=placement.row_id "
                    "JOIN workspace_collaboration_databases database "
                    "ON database.id=row_item.database_id "
                    "JOIN workspace_collaboration_documents database_document "
                    "ON database_document.id=database.document_id "
                    "JOIN workspace_collaboration_database_properties property "
                    "ON property.database_id=row_item.database_id "
                    "AND property.property_type='date' "
                    "JOIN workspace_collaboration_database_cells value "
                    "ON value.row_id=row_item.id AND value.property_id=property.id "
                    "WHERE placement.page_document_id=? "
                    "AND placement.placement='page' "
                    "AND database_document.workspace_id=? "
                    "ORDER BY property.id",
                    (source_id, workspace_id),
                ).fetchall():
                    try:
                        date_value = json.loads(str(cell["value_json"]))
                    except (TypeError, ValueError):
                        continue
                    reminder = (date_value.get("reminder")
                                if isinstance(date_value, Mapping) else None)
                    if not isinstance(reminder, Mapping):
                        continue
                    self._insert_reminder(
                        con,
                        workspace_id=workspace_id,
                        document_id=source_id,
                        block_id=source_id,
                        source_locator=f"database:{source_id}:{cell['property_id']}",
                        recipient=reminder.get("user_id"),
                        remind_at=reminder.get("at"),
                        lead=reminder.get("lead"),
                        supplied_id=None,
                        message=f"{descriptor.get('title', '')} · {cell['property_name']}",
                        revision=source_revision,
                        indexed_at=now,
                    )
        projection_hash = self._projection_sha256(con, workspace_id)
        indexed_count = len(documents)
        backlink_count = int(
            con.execute(
                "SELECT COUNT(*) FROM workspace_collaboration_search_backlinks WHERE workspace_id=?",
                (workspace_id,),
            ).fetchone()[0]
        )
        reminder_count = int(
            con.execute(
                "SELECT COUNT(*) FROM workspace_collaboration_search_reminders WHERE workspace_id=?",
                (workspace_id,),
            ).fetchone()[0]
        )
        con.execute(
            "UPDATE workspace_collaboration_search_state SET built_generation=?,"
            "source_document_count=?,indexed_document_count=?,backlink_count=?,reminder_count=?,"
            "last_rebuild_sha256=?,built_at=? WHERE workspace_id=?",
            (
                desired_generation,
                len(documents),
                indexed_count,
                backlink_count,
                reminder_count,
                projection_hash,
                now,
                workspace_id,
            ),
        )
        return {
            "workspace_id": workspace_id,
            "desired_generation": desired_generation,
            "built_generation": desired_generation,
            "source_document_count": len(documents),
            "indexed_document_count": indexed_count,
            "backlink_count": backlink_count,
            "reminder_count": reminder_count,
            "projection_sha256": projection_hash,
            "converged": True,
        }

    @staticmethod
    def _state_descriptor(con: sqlite3.Connection, workspace_id: str) -> dict[str, Any]:
        row = con.execute(
            "SELECT * FROM workspace_collaboration_search_state WHERE workspace_id=?",
            (workspace_id,),
        ).fetchone()
        if row is None:
            raise AuthorityUnavailable("collaborative search index state is unavailable")
        return {
            "workspace_id": workspace_id,
            "desired_generation": int(row["desired_generation"]),
            "built_generation": int(row["built_generation"]),
            "source_document_count": int(row["source_document_count"]),
            "indexed_document_count": int(row["indexed_document_count"]),
            "backlink_count": int(row["backlink_count"]),
            "reminder_count": int(row["reminder_count"]),
            "projection_sha256": str(row["last_rebuild_sha256"]),
            "converged": int(row["desired_generation"]) == int(row["built_generation"]),
        }

    @contextmanager
    def _converged_reader(self, actor_subject_id: str, workspace_id: str):
        for _attempt in range(3):
            con = self._connect(query_only=True)
            needs_rebuild = False
            try:
                con.execute("BEGIN")
                workspace = self._require_workspace_visibility(
                    con, actor_subject_id, workspace_id
                )
                needs_rebuild = self._state_needs_rebuild(con, workspace_id)
                if not needs_rebuild:
                    yield con, self._state_descriptor(con, workspace_id), workspace
                    return
            finally:
                con.close()
            if needs_rebuild:
                self._refresh_search_index(actor_subject_id, workspace_id)
        raise AuthorityUnavailable("collaborative search index did not converge")

    def rebuild_search_index(self, actor_subject_id: str, workspace_id: str) -> dict[str, Any]:
        actor = self._require_active(actor_subject_id)
        workspace = _identifier(workspace_id, field="workspace_id")
        return self._refresh_search_index(actor, workspace, force=True)

    @retry_title_projection
    def search(
        self,
        actor_subject_id: str,
        workspace_id: str,
        *,
        query: str = "",
        title_only: bool = False,
        creator: str = "",
        scope: str = "all",
        date_after: int = 0,
        sort: str = "relevance",
        limit: int = 50,
    ) -> dict[str, Any]:
        actor = self._require_active(actor_subject_id)
        workspace = _identifier(workspace_id, field="workspace_id")
        clean_query = " ".join(str(query or "").split())
        if len(clean_query) > MAX_QUERY_CHARS:
            raise ValueError("search query is too long")
        terms = [item.casefold() for item in clean_query.split() if item]
        if len(terms) > 32:
            raise ValueError("search query has too many terms")
        clean_scope = str(scope or "all").strip().lower()
        if clean_scope not in {"all", "own", "private", "shared"}:
            raise ValueError("supported search scope required")
        clean_sort = str(sort or "relevance").strip().lower()
        if clean_sort not in {"relevance", "recent", "oldest", "title"}:
            raise ValueError("supported search sort required")
        requested_limit = max(1, min(MAX_RESULT_LIMIT, int(limit)))
        creator_id = str(creator or "").strip()
        if creator_id == "me":
            creator_id = actor
        with self._converged_reader(actor, workspace) as (con, state, workspace_row):
            rows = con.execute(
                "SELECT * FROM workspace_collaboration_search_documents "
                "WHERE workspace_id=? ORDER BY source_updated_at DESC,document_id",
                (workspace,),
            ).fetchall()
            candidates: list[dict[str, Any]] = []
            for row in rows:
                document_id = str(row["document_id"])
                role, sources = CollaborativeIAMAuthority._resolution_on_connection(
                    con, actor, workspace, document_id
                )
                if role is None:
                    continue
                owner = str(row["legacy_owner_subject_id"])
                item_scope = "own" if owner == actor else "shared"
                if creator_id and owner != creator_id:
                    continue
                if clean_scope in {"own", "private"} and item_scope != "own":
                    continue
                if clean_scope == "shared" and item_scope != "shared":
                    continue
                if int(date_after or 0) and int(row["source_updated_at"]) < int(date_after):
                    continue
                candidates.append(
                    {
                        "id": document_id,
                        "document_id": document_id,
                        "workspace_id": workspace,
                        "title": str(row["title"]),
                        "snippet": _snippet(str(row["body_text"]), terms),
                        "owner_user_id": owner,
                        "scope": item_scope,
                        "role": role,
                        "access_sources": sources,
                        "revision": int(row["revision"]),
                        "document_sequence": int(row["document_sequence"]),
                        "document_epoch": int(row["access_epoch"]),
                        "workspace_epoch": int(workspace_row["security_epoch"]),
                        "content_sha256": str(row["content_sha256"]),
                        "updated_at": int(row["source_updated_at"]),
                        # Older index rows use the initial title as an empty-body
                        # snippet. Do not turn that fallback into searchable body.
                        "_body_text": "" if str(row["search_text"]) == str(row["title"]).casefold() + "\n"
                                      else str(row["body_text"]),
                    }
                )
        # Decode after closing the index read snapshot, and before title matching,
        # ranking or pagination. Otherwise renamed pages disappear from search.
        titles = read_current_titles(self, actor, workspace, candidates)
        matched = []
        for item in candidates:
            if item["id"] not in titles:
                continue
            item.update(titles[item["id"]])
            title_folded = item["title"].casefold()
            body = item.pop("_body_text")
            item["snippet"] = _snippet(body or item["title"], terms)
            haystack = title_folded if title_only else title_folded + "\n" + body.casefold()
            if terms and not all(term in haystack for term in terms):
                continue
            item["score"] = sum(4 if term in title_folded else 1 for term in terms)
            matched.append(item)
        if clean_sort == "title":
            matched.sort(key=lambda item: (item["title"].casefold(), item["id"]))
        elif clean_sort == "oldest":
            matched.sort(key=lambda item: (item["updated_at"], item["id"]))
        elif clean_sort == "recent":
            matched.sort(key=lambda item: (-item["updated_at"], item["id"]))
        else:
            matched.sort(key=lambda item: (-item["score"], -item["updated_at"], item["id"]))
        return {
            "contract": AUTHORITY_CONTRACT,
            "workspace_id": workspace,
            "workspace_epoch": int(workspace_row["security_epoch"]),
            "query": clean_query,
            "items": matched[:requested_limit],
            "count": min(len(matched), requested_limit),
            "has_more": len(matched) > requested_limit,
            "index": state,
            "cache_policy": "no_store_reauthorize_every_request",
        }

    @retry_title_projection
    def list_meetings(
        self, actor_subject_id: str, workspace_id: str, *,
        query: str = "", limit: int = 100, offset: int = 0,
    ) -> dict[str, Any]:
        """Read authorized note metadata with the current collaborative title."""
        actor = self._require_active(actor_subject_id)
        workspace = _identifier(workspace_id, field="workspace_id")
        folded = str(query or "").strip().casefold()
        if len(folded) > MAX_QUERY_CHARS:
            raise ValueError("meeting query is too long")
        con = self._connect(query_only=True)
        try:
            con.execute("BEGIN")
            workspace_row = self._require_workspace_visibility(con, actor, workspace)
            rows = con.execute(
                "SELECT d.*,COUNT(b.id) AS note_count,"
                "(SELECT first.id FROM workspace_collaboration_blocks first "
                "WHERE first.document_id=d.id AND first.block_type='ai_notes' "
                "ORDER BY first.position,first.id LIMIT 1) AS first_note_id "
                "FROM workspace_collaboration_documents d "
                "JOIN workspace_collaboration_blocks b ON b.document_id=d.id AND b.block_type='ai_notes' "
                "WHERE d.workspace_id=? AND d.archived=0 GROUP BY d.id ORDER BY d.updated_at DESC,d.id",
                (workspace,),
            ).fetchall()
            items = []
            for row in rows:
                role, _sources = self._resolution_on_connection(con, actor, workspace, str(row["id"]))
                if role is None:
                    continue
                items.append({
                    "id": str(row["id"]), "workspace_id": workspace,
                    "title": str(row["title"]), "icon": str(row["icon"]),
                    "owner_user_id": str(row["legacy_owner_subject_id"]),
                    "updated_at": int(row["updated_at"]), "iam_role": role,
                    "document_sequence": int(row["document_sequence"]),
                    "document_epoch": int(row["access_epoch"]),
                    "workspace_epoch": int(workspace_row["security_epoch"]),
                    "note_count": int(row["note_count"]), "block_id": str(row["first_note_id"]),
                })
        finally:
            con.close()
        titles = read_current_titles(self, actor, workspace, items)
        items = [{**item, **titles[item["id"]]} for item in items
                 if item["id"] in titles and (not folded or folded in titles[item["id"]]["title"].casefold())]
        size, skip = max(1, min(100, int(limit))), max(0, int(offset))
        return {
            "items": items[skip:skip + size], "count": len(items),
            "has_more": skip + size < len(items), "offset": skip,
            "workspace_id": workspace, "workspace_epoch": int(workspace_row["security_epoch"]),
            "cache_policy": "no_store_reauthorize_every_request",
        }

    def list_backlinks(
        self, actor_subject_id: str, workspace_id: str, document_id: str
    ) -> dict[str, Any]:
        actor = self._require_active(actor_subject_id)
        workspace = _identifier(workspace_id, field="workspace_id")
        document = _identifier(document_id, field="document_id")
        with self._converged_reader(actor, workspace) as (con, state, _visible_workspace):
            workspace_row, target, _role = self._access(
                con, actor, document, minimum_role="view"
            )
            if str(workspace_row["id"]) != workspace:
                raise NotFoundOrDenied("not found or permission denied")
            grouped: dict[str, dict[str, Any]] = {}
            for row in con.execute(
                "SELECT backlink.*,search.title,search.legacy_owner_subject_id,"
                "search.source_updated_at,search.access_epoch "
                "FROM workspace_collaboration_search_backlinks backlink "
                "JOIN workspace_collaboration_search_documents search "
                "ON search.document_id=backlink.source_document_id "
                "WHERE backlink.workspace_id=? AND backlink.target_document_id=? "
                "ORDER BY search.source_updated_at DESC,backlink.source_document_id,backlink.source_block_id",
                (workspace, document),
            ).fetchall():
                source_id = str(row["source_document_id"])
                role, sources = CollaborativeIAMAuthority._resolution_on_connection(
                    con, actor, workspace, source_id
                )
                if role is None:
                    continue
                item = grouped.get(source_id)
                if item is None:
                    item = {
                        "id": source_id,
                        "document_id": source_id,
                        "title": str(row["title"]),
                        "owner_user_id": str(row["legacy_owner_subject_id"]),
                        "matching_block_ids": [],
                        "match_count": 0,
                        "snippet": str(row["context_text"]),
                        "role": role,
                        "access_sources": sources,
                        "document_epoch": int(row["access_epoch"]),
                        "updated_at": int(row["source_updated_at"]),
                    }
                    grouped[source_id] = item
                block_id = str(row["source_block_id"])
                if block_id not in item["matching_block_ids"]:
                    item["matching_block_ids"].append(block_id)
                    item["match_count"] += 1
            items = list(grouped.values())
            return {
                "contract": AUTHORITY_CONTRACT,
                "workspace_id": workspace,
                "document_id": document,
                "document_epoch": int(target["access_epoch"]),
                "workspace_epoch": int(workspace_row["security_epoch"]),
                "items": items,
                "count": len(items),
                "index": state,
                "cache_policy": "no_store_reauthorize_every_request",
            }

    def list_reminders(
        self, actor_subject_id: str, workspace_id: str, *, limit: int = 200
    ) -> dict[str, Any]:
        actor = self._require_active(actor_subject_id)
        workspace = _identifier(workspace_id, field="workspace_id")
        requested_limit = max(1, min(500, int(limit)))
        with self._converged_reader(actor, workspace) as (con, state, workspace_row):
            now = int(time.time())
            items: list[dict[str, Any]] = []
            rows = con.execute(
                "SELECT reminder.*,search.title,search.access_epoch,search.source_updated_at "
                "FROM workspace_collaboration_search_reminders reminder "
                "JOIN workspace_collaboration_search_documents search "
                "ON search.document_id=reminder.document_id "
                "WHERE reminder.workspace_id=? AND reminder.recipient_subject_id=? "
                "ORDER BY reminder.trigger_at,reminder.reminder_id LIMIT ?",
                (workspace, actor, requested_limit + 1),
            ).fetchall()
            for row in rows:
                role, _sources = CollaborativeIAMAuthority._resolution_on_connection(
                    con, actor, workspace, str(row["document_id"])
                )
                if role is None:
                    continue
                items.append(
                    {
                        "id": str(row["reminder_id"]),
                        "kind": "reminder",
                        "page_id": str(row["document_id"]),
                        "page_title": str(row["title"]),
                        "block_id": str(row["block_id"]),
                        "message": str(row["message"]),
                        "remind_at": str(row["remind_at"]),
                        "trigger_at": int(row["trigger_at"]),
                        "due": now >= int(row["trigger_at"]),
                        "document_epoch": int(row["access_epoch"]),
                        "updated_at": int(row["source_updated_at"]),
                    }
                )
                if len(items) >= requested_limit:
                    break
            return {
                "contract": AUTHORITY_CONTRACT,
                "workspace_id": workspace,
                "workspace_epoch": int(workspace_row["security_epoch"]),
                "items": items,
                "count": len(items),
                "index": state,
                "cache_policy": "no_store_reauthorize_every_request",
            }

    def export_bundle(self, actor_subject_id: str, document_id: str) -> dict[str, Any]:
        actor = self._require_active(actor_subject_id)
        document = _identifier(document_id, field="document_id")
        with self._reader() as con:
            workspace, page, role = self._access(
                con, actor, document, minimum_role="view"
            )
            snapshot = self._editing_snapshot(con, page)
            transfer_snapshot = {
                "title": snapshot.get("title", ""),
                "icon": snapshot.get("icon", ""),
                "cover": snapshot.get("cover", ""),
                "settings": snapshot.get("settings", {}),
                "blocks": snapshot.get("blocks", []),
            }
            return {
                "contract": TRANSFER_CONTRACT,
                "source_workspace_id": str(workspace["id"]),
                "source_document_id": document,
                "source_revision": int(page["revision"]),
                "source_document_sequence": int(page["document_sequence"]),
                "source_content_sha256": str(page["content_sha256"]),
                "source_document_epoch": int(page["access_epoch"]),
                "source_workspace_epoch": int(workspace["security_epoch"]),
                "exported_by_subject_id": actor,
                "effective_role": role,
                "snapshot_sha256": _sha256(_canonical_json(transfer_snapshot)),
                "snapshot": transfer_snapshot,
                "database_restore_owned_by_F0_P5": True,
            }

    def export_markdown(self, actor_subject_id: str, document_id: str) -> dict[str, Any]:
        bundle = self.export_bundle(actor_subject_id, document_id)
        encoded = base64.urlsafe_b64encode(
            _canonical_json(bundle).encode("utf-8")
        ).decode("ascii").rstrip("=")
        snapshot = bundle["snapshot"]
        body = [f"# {snapshot.get('title') or 'Untitled'}"]
        for block in snapshot.get("blocks", []):
            if isinstance(block, Mapping):
                visible = _text(block.get("content", ""), limit=100_000)
                if visible:
                    body.append(visible)
        markdown = (
            f"<!-- code-assist-notion-snapshot-v1:{encoded} -->\n\n"
            + "\n\n".join(body)
            + "\n"
        )
        return {"bundle": bundle, "markdown": markdown}

    @staticmethod
    def _bundle_from_markdown(markdown: str) -> dict[str, Any] | None:
        match = SNAPSHOT_MARKER_RE.search(markdown)
        if match is None:
            return None
        encoded = match.group(1)
        padding = "=" * ((4 - len(encoded) % 4) % 4)
        try:
            value = json.loads(
                base64.urlsafe_b64decode(encoded + padding).decode("utf-8")
            )
        except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("invalid canonical Markdown snapshot") from exc
        if not isinstance(value, dict):
            raise ValueError("invalid canonical Markdown snapshot")
        return value

    @staticmethod
    def _fallback_markdown_blocks(markdown: str) -> list[dict[str, Any]]:
        lines = str(markdown or "").replace("\r\n", "\n").splitlines()
        blocks: list[dict[str, Any]] = []
        paragraph: list[str] = []

        def flush() -> None:
            if paragraph:
                blocks.append(
                    {
                        "id": f"import-block-{len(blocks) + 1}",
                        "type": "paragraph",
                        "content": "\n".join(paragraph).strip(),
                        "props": {},
                    }
                )
                paragraph.clear()

        for line in lines:
            stripped = line.strip()
            if not stripped:
                flush()
            elif stripped.startswith("# ") and not blocks and not paragraph:
                continue
            elif stripped.startswith("## "):
                flush()
                blocks.append(
                    {
                        "id": f"import-block-{len(blocks) + 1}",
                        "type": "heading_2",
                        "content": stripped[3:].strip(),
                        "props": {},
                    }
                )
            else:
                paragraph.append(line)
        flush()
        return blocks or [
            {"id": "import-block-1", "type": "paragraph", "content": "", "props": {}}
        ]

    def import_bundle(
        self,
        actor_subject_id: str,
        workspace_id: str,
        bundle: Mapping[str, Any],
        *,
        expected_workspace_epoch: int,
        operation_id: str,
        parent_document_id: str | None = None,
        title_override: str | None = None,
    ) -> dict[str, Any]:
        actor = self._require_active(actor_subject_id)
        workspace = _identifier(workspace_id, field="workspace_id")
        if not isinstance(bundle, Mapping) or bundle.get("contract") != TRANSFER_CONTRACT:
            raise ValueError("canonical transfer bundle required")
        snapshot = bundle.get("snapshot")
        if not isinstance(snapshot, Mapping):
            raise ValueError("canonical transfer snapshot required")
        if bundle.get("snapshot_sha256") != _sha256(_canonical_json(dict(snapshot))):
            raise ValueError("canonical transfer snapshot hash mismatch")
        encoded = _canonical_json(dict(bundle)).encode("utf-8")
        if len(encoded) > MAX_TRANSFER_BYTES:
            raise ValueError("canonical transfer bundle is too large")
        operation = str(operation_id or "").strip()
        if not operation or len(operation) > 200:
            raise ValueError("valid operation_id required")
        document_id = "import-" + _sha256(
            _canonical_json(
                {"actor": actor, "workspace": workspace, "operation_id": operation}
            )
        )[7:39]
        title = str(title_override or snapshot.get("title") or "Imported page").strip()
        blocks = snapshot.get("blocks")
        if not isinstance(blocks, Sequence) or isinstance(
            blocks, (str, bytes, bytearray)
        ):
            raise ValueError("canonical transfer blocks must be an array")
        source_blocks = [item for item in blocks if isinstance(item, Mapping)]
        if len(source_blocks) != len(blocks):
            raise ValueError("canonical transfer blocks must contain objects")
        source_block_ids = [
            str(item.get("id") or f"block-{index + 1}")
            for index, item in enumerate(source_blocks)
        ]
        if len(set(source_block_ids)) != len(source_block_ids):
            raise ValueError("canonical transfer block IDs must be unique")
        block_id_map = {
            source_block_ids[index]: _stable_id(
                "import-block",
                document_id,
                source_block_ids[index],
                str(index),
            )
            for index, item in enumerate(source_blocks)
        }

        def rewrite(value: Any) -> Any:
            if isinstance(value, str):
                return block_id_map.get(value, value)
            if isinstance(value, Mapping):
                return {str(key): rewrite(item) for key, item in value.items()}
            if isinstance(value, Sequence) and not isinstance(
                value, (str, bytes, bytearray)
            ):
                return [rewrite(item) for item in value]
            return value

        imported_blocks = [
            {
                **dict(item),
                "id": block_id_map[
                    str(item.get("id") or f"block-{index + 1}")
                ],
                "content": str(item.get("content", "")),
                "props": rewrite(
                    item.get("props") if isinstance(item.get("props"), Mapping) else {}
                ),
            }
            for index, item in enumerate(source_blocks)
        ]
        result = self.create_document(
            actor,
            workspace,
            document_id,
            title=title,
            icon=str(snapshot.get("icon") or ""),
            cover=str(snapshot.get("cover") or ""),
            settings=snapshot.get("settings") if isinstance(snapshot.get("settings"), Mapping) else {},
            blocks=imported_blocks,
            parent_document_id=parent_document_id,
            expected_workspace_epoch=int(expected_workspace_epoch),
            operation_id=operation,
        )
        return {
            **result,
            "imported_from_snapshot_sha256": str(bundle["snapshot_sha256"]),
            "block_id_map": block_id_map,
            "reminder_metadata_preserved": True,
            "database_restore_owned_by_F0_P5": True,
        }

    def import_markdown(
        self,
        actor_subject_id: str,
        workspace_id: str,
        markdown: str,
        *,
        title: str,
        expected_workspace_epoch: int,
        operation_id: str,
        parent_document_id: str | None = None,
    ) -> dict[str, Any]:
        raw = str(markdown or "")
        if not raw.strip() or len(raw.encode("utf-8")) > MAX_TRANSFER_BYTES:
            raise ValueError("valid Markdown payload required")
        bundle = self._bundle_from_markdown(raw)
        if bundle is None:
            snapshot = {
                "title": str(title or "Imported page"),
                "icon": "",
                "cover": "",
                "settings": {},
                "blocks": self._fallback_markdown_blocks(raw),
            }
            bundle = {
                "contract": TRANSFER_CONTRACT,
                "snapshot_sha256": _sha256(_canonical_json(snapshot)),
                "snapshot": snapshot,
            }
        return self.import_bundle(
            actor_subject_id,
            workspace_id,
            bundle,
            expected_workspace_epoch=expected_workspace_epoch,
            operation_id=operation_id,
            parent_document_id=parent_document_id,
            title_override=title or None,
        )

    def import_csv(
        self,
        actor_subject_id: str,
        workspace_id: str,
        csv_text: str,
        *,
        title: str,
        expected_workspace_epoch: int,
        operation_id: str,
        parent_document_id: str | None = None,
    ) -> dict[str, Any]:
        raw = str(csv_text or "").lstrip("\ufeff")
        if not raw.strip() or len(raw.encode("utf-8")) > MAX_TRANSFER_BYTES:
            raise ValueError("valid CSV payload required")
        rows = list(csv.reader(io.StringIO(raw)))
        if not rows or not rows[0] or len(rows) > 10_001 or len(rows[0]) > 200:
            raise ValueError("valid bounded CSV required")
        width = len(rows[0])
        if any(len(row) != width for row in rows):
            raise ValueError("CSV rows must have equal width")
        properties = [
            {"id": f"property-{index + 1}", "name": name or f"Column {index + 1}", "type": "text"}
            for index, name in enumerate(rows[0])
        ]
        data_rows = [
            {
                "id": f"row-{row_index + 1}",
                **{properties[index]["id"]: value for index, value in enumerate(row)},
            }
            for row_index, row in enumerate(rows[1:])
        ]
        snapshot = {
            "title": str(title or "Imported database"),
            "icon": "",
            "cover": "",
            "settings": {},
            "blocks": [
                {
                    "id": "import-database-1",
                    "type": "table",
                    "content": str(title or "Imported database"),
                    "props": {"properties": properties, "rows": data_rows, "views": []},
                }
            ],
        }
        bundle = {
            "contract": TRANSFER_CONTRACT,
            "snapshot_sha256": _sha256(_canonical_json(snapshot)),
            "snapshot": snapshot,
        }
        return self.import_bundle(
            actor_subject_id,
            workspace_id,
            bundle,
            expected_workspace_epoch=expected_workspace_epoch,
            operation_id=operation_id,
            parent_document_id=parent_document_id,
        )

    def _html_export_view(self, actor_subject_id: str, document_id: str):
        actor = self._require_active(actor_subject_id)
        document = _identifier(document_id, field="document_id")
        for _attempt in range(3):
            bundle = self.export_bundle(actor, document)
            with self._reader() as con:
                con.execute("BEGIN")
                workspace, page, _role = self._access(con, actor, document, minimum_role="view")
                generation = _generation(con, document)
                stamp = (tuple(workspace), tuple(page), generation)
                if (int(page["document_sequence"]), int(page["access_epoch"]), int(workspace["security_epoch"])) != (
                    bundle["source_document_sequence"], bundle["source_document_epoch"], bundle["source_workspace_epoch"]
                ):
                    continue
                frames = _frames(con, document, generation)
            # The transfer bundle remains an immutable structural snapshot. The HTML
            # view follows current durable Yjs edits without rewriting that snapshot.
            snapshot = dict(bundle["snapshot"])
            if frames:
                current = search_projection.materialize([frames])[0]
                for key in ("title", "blocks"):
                    if current[key] is not None:
                        snapshot[key] = current[key]
            def resolve_custom_emoji(emoji_id):
                with self._reader() as emoji_con:
                    emoji_con.execute("BEGIN")
                    icon_workspace, _icon_page, _icon_role = self._access(
                        emoji_con, actor, document, minimum_role="view")
                    row = emoji_con.execute(
                        "SELECT image_bytes,mime_type,size_bytes,content_sha256 "
                        "FROM workspace_custom_emojis WHERE id=? AND workspace_id=?",
                        (emoji_id, icon_workspace["id"]),
                    ).fetchone()
                    if row is None or len(row["image_bytes"]) != row["size_bytes"]:
                        raise FileNotFoundError("workspace custom emoji not found")
                    return {"raw": bytes(row["image_bytes"]), "mime_type": row["mime_type"],
                            "content_sha256": row["content_sha256"]}

            icon = notion_icons.export_icon_html(snapshot.get("icon"), page_id=document,
                resolve_file=lambda file_id: self.get_file(actor, file_id),
                resolve_custom_emoji=resolve_custom_emoji)
            self._require_active(actor)
            with self._reader() as con:
                con.execute("BEGIN")
                workspace, page, _role = self._access(con, actor, document, minimum_role="view")
                if stamp != (tuple(workspace), tuple(page), _generation(con, document)):
                    continue
            return bundle, snapshot, icon
        raise AuthorityUnavailable("document changed while exporting HTML; retry")

    def export_html(self, actor_subject_id: str, document_id: str) -> dict[str, Any]:
        from . import html_database_export
        for _attempt in range(3):
            bundle, snapshot, icon = self._html_export_view(actor_subject_id, document_id)
            try:
                models, witnesses = html_database_export.prepare(self, actor_subject_id, bundle, snapshot)
                result = self._export_html_document(bundle, snapshot, icon, models)
                html_database_export.verify(self, actor_subject_id, witnesses)
            except html_database_export.HtmlExportChanged:
                continue
            return result
        raise AuthorityUnavailable("export sources changed while rendering HTML; retry")

    @staticmethod
    def _database_export_view(block, model, requested_view, *, canonical):
        if requested_view is None:
            return {}, "table"
        props = block.get("props") if isinstance(block.get("props"), Mapping) else {}
        views = model["database"].get("views", []) if canonical else props.get("views", [])
        views = views if isinstance(views, list) else []
        view = next((item for item in views
            if isinstance(item, Mapping) and str(item.get("id") or "") == requested_view
            and item.get("state") != "archived"), None)
        if view is not None:
            config = view.get("config", {}) if canonical else view.get("config", view)
            kind = str(view.get("type") or "table")
        elif canonical and props.get("linked_database") and props.get("active_view_id") == requested_view:
            config = model.get("config", {})
            kind = str(model.get("type") or "table")
        else:
            raise NotFoundOrDenied("not found or permission denied")
        return (config if isinstance(config, Mapping) else {}), kind

    def export_database_html(
        self, actor_subject_id: str, document_id: str, *, block_id: str,
        view_id: str | None = None, include_media: bool = True,
        with_asset_bundle: bool = False,
    ) -> dict[str, Any]:
        from . import html_database_export

        requested_view = _identifier(view_id, field="view_id") if view_id is not None else None
        for _attempt in range(3):
            archive = None
            bundle, snapshot, _icon = self._html_export_view(actor_subject_id, document_id)
            block = next((item for item in snapshot.get("blocks", [])
                if isinstance(item, Mapping) and str(item.get("id") or "") == str(block_id or "")
                and str(item.get("type") or "") in html_database_export.DATABASE_TYPES), None)
            if block is None:
                raise NotFoundOrDenied("not found or permission denied")
            try:
                models, witnesses = html_database_export.prepare(self, actor_subject_id, bundle, snapshot)
                model = models.get(str(block_id))
                if model and not model.get("unavailable"):
                    canonical = True
                else:
                    props = block.get("props") if isinstance(block.get("props"), Mapping) else {}
                    if props.get("linked_database") or props.get("database_id") or props.get("_canonical_version"):
                        raise NotFoundOrDenied("not found or permission denied")
                    raw_properties = props.get("properties") if isinstance(props.get("properties"), list) else []
                    raw_rows = props.get("rows") if isinstance(props.get("rows"), list) else []
                    properties = [item for item in raw_properties
                        if isinstance(item, Mapping) and item.get("id")]
                    rows = [{"id": str(row.get("id") or ""), "cells": {
                        str(prop["id"]): row.get(str(prop["id"])) for prop in properties}}
                        for row in raw_rows if isinstance(row, Mapping) and not row.get("archived")]
                    model = {"database": {"title": block.get("content") or "Database",
                        "properties": properties, "rows": rows}, "type": "table", "config": {}}
                    canonical = False
                config, kind = self._database_export_view(block, model, requested_view, canonical=canonical)
                rendered_model = {**model, "config": config, "type": kind,
                    "include_media": include_media}
                manifest = notion_database_export_assets.authorized_manifest(
                    self, actor_subject_id, rendered_model) if with_asset_bundle and include_media else ()
                if manifest:
                    rendered_model["asset_paths"] = {
                        item["file_id"]: item["zip_path"] for item in manifest}
                title = str(model["database"].get("title") or block.get("content") or "Database")
                result = self._export_html_document(bundle,
                    {"title": title, "icon": "", "blocks": [block]}, "", {str(block_id): rendered_model})
                result["title"] = title
                result["csv"] = notion_database_export_bundle.database_csv(
                    rendered_model, current_view=requested_view is not None)
                result["all_csv"] = notion_database_export_bundle.database_csv(
                    rendered_model, current_view=False)
                if with_asset_bundle:
                    archive = notion_database_export_bundle.html_archive_with_assets(
                        title, result["html"], result["csv"], identity=str(block_id),
                        assets=manifest)
                html_database_export.verify(self, actor_subject_id, witnesses)
                if manifest:
                    notion_database_export_assets.verify_manifest(self, actor_subject_id, manifest)
            except html_database_export.HtmlExportChanged:
                if archive is not None:
                    archive.close()
                continue
            except notion_database_export_bundle.ExportAttachmentChanged as exc:
                if archive is not None:
                    archive.close()
                raise AuthorityUnavailable("exported attachment changed during download; retry") from exc
            except Exception:
                if archive is not None:
                    archive.close()
                raise
            if archive is not None:
                result["archive"] = archive
            return result
        raise AuthorityUnavailable("export sources changed while rendering database HTML; retry")

    @staticmethod
    def _export_html_document(bundle, snapshot, icon, models):
        def special(block):
            model = models.get(str(block.get('id')))
            return notion_html_database.render(model) if model is not None else f'<p>{notion_html_blocks.rich(block)}</p>'
        sections = notion_html_blocks.render(snapshot.get("blocks", []), render_fallback=special)
        title = escape(str(snapshot.get("title") or "Untitled"))
        icon_section = f'<div class="page-icon">{icon}</div>' if icon else ''
        credit = notion_icons.icon_attribution_html(snapshot.get("icon"))
        return {
            "bundle": bundle,
            "html": '<!doctype html><html lang="ko"><head><meta charset="utf-8">'
            + '<meta name="viewport" content="width=device-width,initial-scale=1">'
            + f'<title>{title}</title><style>'
            + 'body{margin:0;color:#37352f;background:#fff;font:16px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI","Malgun Gothic",sans-serif}'
            + 'main{width:min(760px,calc(100% - 48px));margin:72px auto 120px}'
            + 'h1{font-size:42px;line-height:1.18;margin:0 0 28px;overflow-wrap:anywhere}'
            + '.page-icon{font-size:72px;line-height:1;margin-bottom:14px}.page-icon img{display:block;max-width:100%}'
            + 'section{white-space:pre-wrap;overflow-wrap:anywhere;margin:8px 0}'
            + '.icon-credit{display:block;font-size:11px;color:#787774;margin-top:44px}.icon-credit a{color:inherit}'
            + notion_html_blocks.CSS
            + notion_html_database.CSS
            + f'</style></head><body><main>{icon_section}<h1>{title}</h1>{sections}{credit}</main>'
            + f'<script>{notion_html_blocks.TAB_SCRIPT}</script></body></html>',
        }

    def export_csv(
        self, actor_subject_id: str, document_id: str, *, block_id: str,
        view_id: str | None = None,
    ) -> dict[str, Any]:
        from . import html_database_export

        requested_view = _identifier(view_id, field="view_id") if view_id is not None else None
        for _attempt in range(3):
            bundle, snapshot, _icon = self._html_export_view(actor_subject_id, document_id)
            block = next((item for item in snapshot.get("blocks", [])
                if isinstance(item, Mapping) and str(item.get("id") or "") == str(block_id or "")
                and str(item.get("type") or "") in html_database_export.DATABASE_TYPES), None)
            if block is None:
                raise NotFoundOrDenied("not found or permission denied")
            try:
                models, witnesses = html_database_export.prepare(self, actor_subject_id, bundle, snapshot)
                model = models.get(str(block_id))
                if model and not model.get("unavailable"):
                    database = model["database"]
                    properties = database["properties"]
                    rows = database["rows"]
                    relation_labels = database.get("relation_labels", {})
                    canonical = True
                else:
                    props = block.get("props") if isinstance(block.get("props"), Mapping) else {}
                    # The old transfer format can contain an unadopted inline
                    # table. Never fall back to a cache for a canonical/linked DB.
                    if props.get("linked_database") or props.get("database_id") or props.get("_canonical_version"):
                        raise NotFoundOrDenied("not found or permission denied")
                    raw_properties = props.get("properties") if isinstance(props.get("properties"), list) else []
                    raw_rows = props.get("rows") if isinstance(props.get("rows"), list) else []
                    properties = [item for item in raw_properties
                        if isinstance(item, Mapping) and item.get("id")]
                    rows = [item for item in raw_rows if isinstance(item, Mapping)]
                    relation_labels = {}
                    canonical = False

                if requested_view is not None:
                    config, _kind = self._database_export_view(block,
                        model if canonical else {"database": {}}, requested_view, canonical=canonical)
                    properties = notion_html_database.visible_properties(properties, config)
                    source_rows = rows if canonical else [{"cells": row} for row in rows]
                    rows = notion_html_database.visible_rows(source_rows, config)
                    canonical = True

                output = io.StringIO(newline="")
                writer = csv.writer(output, lineterminator="\n")
                writer.writerow([str(item.get("name") or item["id"]) for item in properties])
                for row in rows:
                    cells = row.get("cells", {}) if canonical else row
                    computed = row.get("computed", {}) if canonical else {}
                    values = {**cells, **computed}
                    fields = []
                    for prop in properties:
                        prop_id = str(prop["id"])
                        value = values.get(prop_id)
                        labels = relation_labels.get(prop_id, {})
                        if prop.get("type") == "relation" and isinstance(value, list) and labels:
                            fields.append(", ".join(labels.get(str(identity), "연결된 페이지") for identity in value))
                        else:
                            fields.append(notion_html_database.display(value))
                    writer.writerow(fields)
                html_database_export.verify(self, actor_subject_id, witnesses)
            except html_database_export.HtmlExportChanged:
                continue
            return {"bundle": bundle, "csv": "\ufeff" + output.getvalue()}
        raise AuthorityUnavailable("export sources changed while rendering CSV; retry")


__all__ = [
    "AUTHORITY_CONTRACT",
    "CollaborativeSearchAuthority",
    "MAX_RESULT_LIMIT",
    "TRANSFER_CONTRACT",
]
