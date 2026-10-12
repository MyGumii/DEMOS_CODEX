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
        "SELECT MAX(version) FROM workspace_schema_migrations"
    ).fetchone()
    if row is None or row[0] != schema_2410.TARGET_VERSION:
        return False
    marker = con.execute(
        "SELECT name,checksum,actual_schema_sha256 FROM workspace_schema_migrations WHERE version=?",
        (schema_2410.TARGET_VERSION,),
    ).fetchone()
    return bool(
        marker is not None
        and tuple(marker)
        == (
            schema_2410.TARGET_NAME,
            schema_2410.TARGET_CHECKSUM,
            schema_2410.TARGET_ACTUAL_SCHEMA_SHA256,
        )
    )


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

    def _rebuild_workspace(self, con: sqlite3.Connection, workspace_id: str) -> dict[str, Any]:
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
        documents = con.execute(
            "SELECT * FROM workspace_collaboration_documents "
            "WHERE workspace_id=? AND archived=0 ORDER BY id",
            (workspace_id,),
        ).fetchall()
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
        descriptors: dict[str, dict[str, Any]] = {}
        for document in documents:
            document_id = str(document["id"])
            descriptor = self._editing_snapshot(con, document)
            descriptors[document_id] = descriptor
            body = _text(
                {
                    "blocks": descriptor.get("blocks", []),
                    "databases": descriptor.get("databases", []),
                }
            )
            title = str(descriptor.get("title") or document["title"])
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
                context = _snippet(_text({"content": content, "props": props}), (), limit=500)
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
                    parser.feed(content)
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

    def _converge(self, con: sqlite3.Connection, workspace_id: str) -> dict[str, Any]:
        self._workspace(con, workspace_id)
        if self._state_needs_rebuild(con, workspace_id):
            return self._rebuild_workspace(con, workspace_id)
        row = con.execute(
            "SELECT * FROM workspace_collaboration_search_state WHERE workspace_id=?",
            (workspace_id,),
        ).fetchone()
        assert row is not None
        return {
            "workspace_id": workspace_id,
            "desired_generation": int(row["desired_generation"]),
            "built_generation": int(row["built_generation"]),
            "source_document_count": int(row["source_document_count"]),
            "indexed_document_count": int(row["indexed_document_count"]),
            "backlink_count": int(row["backlink_count"]),
            "reminder_count": int(row["reminder_count"]),
            "projection_sha256": str(row["last_rebuild_sha256"]),
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
                with self._transaction() as writable:
                    self._require_workspace_visibility(
                        writable, actor_subject_id, workspace_id
                    )
                    self._converge(writable, workspace_id)
        raise AuthorityUnavailable("collaborative search index did not converge")

    def rebuild_search_index(self, actor_subject_id: str, workspace_id: str) -> dict[str, Any]:
        actor = self._require_active(actor_subject_id)
        workspace = _identifier(workspace_id, field="workspace_id")
        with self._transaction() as con:
            self._workspace(con, workspace)
            visible = any(
                self._resolution_on_connection(con, actor, workspace, str(row["id"]))[0]
                is not None
                for row in con.execute(
                    "SELECT id FROM workspace_collaboration_documents WHERE workspace_id=?",
                    (workspace,),
                ).fetchall()
            )
            membership = con.execute(
                "SELECT 1 FROM workspace_memberships WHERE workspace_id=? AND subject_id=? "
                "AND revoked_at IS NULL",
                (workspace, actor),
            ).fetchone()
            if not visible and membership is None:
                raise NotFoundOrDenied("not found or permission denied")
            return self._rebuild_workspace(con, workspace)

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
                title_folded = str(row["title"]).casefold()
                haystack = title_folded if title_only else str(row["search_text"])
                if terms and not all(term in haystack for term in terms):
                    continue
                score = sum(4 if term in title_folded else 1 for term in terms)
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
                        "score": score,
                    }
                )
            if clean_sort == "title":
                candidates.sort(key=lambda item: (item["title"].casefold(), item["id"]))
            elif clean_sort == "oldest":
                candidates.sort(key=lambda item: (item["updated_at"], item["id"]))
            elif clean_sort == "recent":
                candidates.sort(key=lambda item: (-item["updated_at"], item["id"]))
            else:
                candidates.sort(
                    key=lambda item: (-item["score"], -item["updated_at"], item["id"])
                )
            return {
                "contract": AUTHORITY_CONTRACT,
                "workspace_id": workspace,
                "workspace_epoch": int(workspace_row["security_epoch"]),
                "query": clean_query,
                "items": candidates[:requested_limit],
                "count": min(len(candidates), requested_limit),
                "has_more": len(candidates) > requested_limit,
                "index": state,
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

    def export_html(self, actor_subject_id: str, document_id: str) -> dict[str, Any]:
        bundle = self.export_bundle(actor_subject_id, document_id)
        snapshot = bundle["snapshot"]
        sections = "".join(
            f"<section data-block-type=\"{escape(str(block.get('type') or 'paragraph'))}\">"
            f"{escape(_text(block.get('content', ''), limit=100_000))}</section>"
            for block in snapshot.get("blocks", [])
            if isinstance(block, Mapping)
        )
        return {
            "bundle": bundle,
            "html": "<!doctype html><meta charset=\"utf-8\"><title>"
            + escape(str(snapshot.get("title") or "Untitled"))
            + "</title><main><h1>"
            + escape(str(snapshot.get("title") or "Untitled"))
            + "</h1>"
            + sections
            + "</main>",
        }

    def export_csv(
        self, actor_subject_id: str, document_id: str, *, block_id: str
    ) -> dict[str, Any]:
        bundle = self.export_bundle(actor_subject_id, document_id)
        blocks = bundle["snapshot"].get("blocks", [])
        block = next(
            (
                item
                for item in blocks
                if isinstance(item, Mapping)
                and str(item.get("id") or "") == str(block_id or "")
            ),
            None,
        )
        if block is None:
            raise NotFoundOrDenied("not found or permission denied")
        props = block.get("props") if isinstance(block.get("props"), Mapping) else {}
        properties = [
            item
            for item in props.get("properties", [])
            if isinstance(item, Mapping) and item.get("id")
        ]
        rows = [item for item in props.get("rows", []) if isinstance(item, Mapping)]
        output = io.StringIO(newline="")
        writer = csv.writer(output, lineterminator="\n")
        writer.writerow([str(item.get("name") or item["id"]) for item in properties])
        for row in rows:
            writer.writerow(
                [
                    _canonical_json(row.get(str(item["id"])))
                    if isinstance(row.get(str(item["id"])), (dict, list))
                    else str(row.get(str(item["id"])) or "")
                    for item in properties
                ]
            )
        return {"bundle": bundle, "csv": "\ufeff" + output.getvalue()}


__all__ = [
    "AUTHORITY_CONTRACT",
    "CollaborativeSearchAuthority",
    "MAX_RESULT_LIMIT",
    "TRANSFER_CONTRACT",
]
