"""Explicit linked data sources and independent, document-owned view settings.

Only the reference and views belong to the instance document. Source rows,
properties, title and templates must never become an instance content cache.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json

from .canonical_authority import (
    AuthorityUnavailable, NotFoundOrDenied, _canonical_json, _identifier,
    _operation_id, _stable_id,
)
from .database_features import VIEW_TYPES
from .iam_authority import CollaborativeIAMAuthority
from .page_copy_source import MAX_SOURCE_BYTES
from . import synced_blocks, database_row_content, database_access

CONTRACT = "code_assist.notion.linked_database.v1"
CACHE_KEYS = frozenset({"rows", "properties", "templates", "_canonical_version",
    "rich_text_html", "children"})
VIEW_STATE_KEYS = frozenset({"filters", "filter_group", "sorts", "group_property", "group_sort", "group_order", "month", "timeline_anchor",
    "timeline_scale", "timeline_date_property", "board_group_property",
    "calendar_date_property", "property_visibility", "property_order",
    "show_source_title", "show_vertical_lines", "show_page_icons", "wrap_all_content"})


class LinkedDatabaseShapeUnavailable(ValueError):
    """An incomplete, cached or ambiguous source/view descriptor."""


class LinkedDatabaseSourceChanged(RuntimeError):
    """The instance changed while its durable body was being read."""


def _validate_filter_group(group):
    if group is None:
        return
    count = 0

    def visit(node, depth):
        nonlocal count
        if not isinstance(node, dict):
            raise LinkedDatabaseShapeUnavailable("invalid advanced filter node")
        if "children" in node:
            if (depth > 3 or set(node) != {"logic", "children"}
                    or not isinstance(node["logic"], str) or node["logic"] not in {"and", "or"}
                    or not isinstance(node["children"], list)
                    or len(node["children"]) > 16):
                raise LinkedDatabaseShapeUnavailable("invalid advanced filter group")
            for child in node["children"]:
                visit(child, depth + 1)
            return
        if (depth > 4 or set(node) - {"property", "operator", "value"}
                or not isinstance(node.get("property"), str) or not node["property"]
                or not isinstance(node.get("operator"), str)):
            raise LinkedDatabaseShapeUnavailable("invalid advanced filter rule")
        count += 1
        if count > 16:
            raise LinkedDatabaseShapeUnavailable("too many advanced filter rules")

    visit(group, 1)
    if "children" not in group:
        raise LinkedDatabaseShapeUnavailable("advanced filter root must be a group")


def _write_witness(value, document, workspace):
    fields = {"document_id", "workspace_id", "workspace_epoch", "document_sequence", "document_epoch", "generation"}
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError("exact linked write witness required")
    if value["document_id"] != document or value["workspace_id"] != workspace:
        raise ValueError("linked write witness scope mismatch")
    result = tuple(value[key] for key in ("workspace_epoch", "document_sequence", "document_epoch", "generation"))
    if any(isinstance(item, bool) or not isinstance(item, int) or item < 1 for item in result):
        raise ValueError("positive linked write witness values required")
    return result


@dataclass(frozen=True)
class _LinkedCellScope:
    actor: str
    workspace: str
    document: str
    block: str
    source_document: str
    database: str
    prepared_stamp: tuple
    instance_witness: tuple
    source_witness: tuple

    def request_context(self):
        # Only client-bound identities/versions enter the idempotency hash.
        # The freshly prepared server stamp can change between exact retries.
        return {"workspace_id": self.workspace, "document_id": self.document,
            "block_id": self.block, "instance_witness": list(self.instance_witness),
            "source_generation": self.source_witness[3]}

    def authorize(self, authority, con, actor, source, database, *, replay):
        if (actor, source, database) != (self.actor, self.source_document, self.database):
            raise NotFoundOrDenied("not found or permission denied")
        if authority._document(con, source)["workspace_id"] != self.workspace:
            raise NotFoundOrDenied("not found or permission denied")
        authority._exact_access(con, actor, self.document, minimum_role="edit_content",
            expected_workspace_epoch=self.instance_witness[0], expected_document_epoch=self.instance_witness[2])
        _document, stamp = synced_blocks._authorize(authority, con, actor, self.document)
        if stamp[0] != self.workspace or stamp != self.prepared_stamp:
            raise LinkedDatabaseSourceChanged("linked instance changed during write preparation")
        if not replay and (stamp[1], stamp[4], stamp[5], stamp[6]) != self.instance_witness:
            raise LinkedDatabaseSourceChanged("linked instance witness is stale")
        if synced_blocks._generation(con, source) != self.source_witness[3]:
            raise LinkedDatabaseSourceChanged("linked source generation changed")


def execute_linked_cells(authority, actor_subject_id, workspace_id, document_id, block_id, *,
        source, instance_witness, source_witness, cells, operation_id, fault=None):
    """Prepare durable reference outside SQLite; bind both documents at commit."""
    actor = authority._require_active(actor_subject_id)
    workspace = _exact_id(workspace_id, "workspace_id")
    document = _exact_id(document_id, "document_id")
    block = _exact_id(block_id, "block_id")
    if not isinstance(source, dict) or set(source) != {"document_id", "database_id"}:
        raise ValueError("exact linked source required")
    source_document = _exact_id(source["document_id"], "source_document_id")
    database = _exact_id(source["database_id"], "database_id")
    instance_expected = _write_witness(instance_witness, document, workspace)
    source_expected = _write_witness(source_witness, source_document, workspace)
    try:
        instance = synced_blocks._current(authority, actor, document, MAX_SOURCE_BYTES, workspace)
        body = synced_blocks.index_blocks(json.loads(instance.descriptor_json)["blocks"])
    except synced_blocks.SyncedShapeUnavailable as exc:
        raise LinkedDatabaseShapeUnavailable(str(exc)) from exc
    if block not in body:
        raise NotFoundOrDenied("not found or permission denied")
    if source_of(body[block]) != (source_document, database):
        raise LinkedDatabaseSourceChanged("linked source identity changed")
    scope = _LinkedCellScope(actor, workspace, document, block, source_document, database,
        instance.stamp, instance_expected, source_expected)
    return authority.execute_cell_batch(actor, source_document, database_id=database, target_id=database,
        payload={"cells": cells}, expected_document_sequence=source_expected[1],
        expected_workspace_epoch=source_expected[0], expected_document_epoch=source_expected[2],
        operation_id=operation_id, _linked_scope=scope, fault=fault)


def list_linkable_databases(authority, actor_subject_id, workspace_id, *, query="", after="", limit=50):
    """Page through currently visible sources without exposing denied titles/IDs."""
    actor = authority._require_active(actor_subject_id)
    workspace_id = _exact_id(workspace_id, "workspace_id")
    if not isinstance(query, str) or len(query) > 200:
        raise ValueError("source query must contain at most 200 characters")
    if after:
        after = _exact_id(after, "after")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50:
        raise ValueError("source limit must be between 1 and 50")
    with authority._reader() as con:
        con.execute("BEGIN")
        authority._workspace(con, workspace_id)
        from . import database_trash
        trash_filter = ("AND NOT EXISTS (SELECT 1 FROM "
            "workspace_collaboration_database_trash_state t WHERE t.database_id=db.id) "
            if database_trash.installed(con) else "")
        rows = con.execute(
            "SELECT db.id,db.document_id,db.title,doc.title AS page_title "
            "FROM workspace_collaboration_databases db "
            "JOIN workspace_collaboration_documents doc ON doc.id=db.document_id "
            "WHERE doc.workspace_id=? AND doc.archived=0 AND db.id>? "
            "AND (instr(lower(db.title),lower(?))>0 OR instr(lower(doc.title),lower(?))>0) "
            + trash_filter + "ORDER BY db.id",
            (workspace_id, after, query.strip(), query.strip()),
        )
        roles, items = {}, []
        for row in rows:
            document = row["document_id"]
            if document not in roles:
                roles[document] = CollaborativeIAMAuthority._resolution_on_connection(
                    con, actor, workspace_id, document)[0]
            if roles[document] is None:
                continue
            items.append({**dict(row), "effective_role": roles[document]})
            if len(items) > limit:
                break
        return {"contract": CONTRACT, "workspace_id": workspace_id, "items": items[:limit],
            "next_after": items[limit - 1]["id"] if len(items) > limit else None}


def read_linkable_database_source(authority, actor_subject_id, workspace_id, database_id):
    """Resolve one visible source without relying on a paginated title search."""
    actor = authority._require_active(actor_subject_id)
    workspace = _exact_id(workspace_id, "workspace_id")
    database = _exact_id(database_id, "database_id")
    with authority._reader() as con:
        con.execute("BEGIN")
        authority._workspace(con, workspace)
        row = con.execute(
            "SELECT db.id,db.document_id,db.title,doc.title AS page_title "
            "FROM workspace_collaboration_databases db "
            "JOIN workspace_collaboration_documents doc ON doc.id=db.document_id "
            "WHERE db.id=? AND doc.workspace_id=? AND doc.archived=0",
            (database, workspace),
        ).fetchone()
        if row is None:
            raise NotFoundOrDenied("not found or permission denied")
        from . import database_trash
        if database_trash.is_trashed(con, database):
            raise NotFoundOrDenied("not found or permission denied")
        role = CollaborativeIAMAuthority._resolution_on_connection(
            con, actor, workspace, str(row["document_id"])
        )[0]
        if role is None:
            raise NotFoundOrDenied("not found or permission denied")
        return {"contract": CONTRACT, "workspace_id": workspace,
            "source": {**dict(row), "effective_role": role}}


def _scoped_source(authority, con, actor, workspace, row):
    access = database_access.resolve_on_connection(
        authority, con, actor, workspace, str(row["id"]),
    )
    source = {"id": str(row["id"]), "title": str(row["title"]),
              "effective_role": access.role}
    page_role = CollaborativeIAMAuthority._resolution_on_connection(
        con, actor, workspace, access.document_id,
    )[0]
    if page_role is not None:
        source["document_id"] = access.document_id
        source["page_title"] = str(row["page_title"])
    return source


def list_linkable_databases_scoped(authority, actor_subject_id, workspace_id,
                                   *, query="", after="", limit=50):
    """Discover authorized sources without exposing an inaccessible parent."""
    actor = authority._require_active(actor_subject_id)
    workspace = _exact_id(workspace_id, "workspace_id")
    if not isinstance(query, str) or len(query) > 200:
        raise ValueError("source query must contain at most 200 characters")
    if after:
        after = _exact_id(after, "after")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50:
        raise ValueError("source limit must be between 1 and 50")
    term = query.strip()
    with authority._reader() as con:
        con.execute("BEGIN")
        authority._workspace(con, workspace)
        if not database_access.installed(con):
            raise AuthorityUnavailable("database sharing is not installed")
        rows = con.execute(
            "SELECT db.id,db.title,doc.title AS page_title,"
            "instr(lower(db.title),lower(?)) AS title_match,"
            "instr(lower(doc.title),lower(?)) AS page_match "
            "FROM workspace_collaboration_databases db "
            "JOIN workspace_collaboration_documents doc ON doc.id=db.document_id "
            "WHERE doc.workspace_id=? AND doc.archived=0 AND db.id>? "
            "AND (instr(lower(db.title),lower(?))>0 "
            "OR instr(lower(doc.title),lower(?))>0) ORDER BY db.id",
            (term, term, workspace, after, term, term),
        )
        items = []
        for row in rows:
            try:
                source = _scoped_source(authority, con, actor, workspace, row)
            except NotFoundOrDenied:
                continue
            if not row["title_match"] and (
                    not row["page_match"] or "page_title" not in source):
                continue
            items.append(source)
            if len(items) > limit:
                break
        return {"contract": CONTRACT, "workspace_id": workspace,
                "items": items[:limit],
                "next_after": items[limit - 1]["id"] if len(items) > limit else None}


def read_linkable_database_source_scoped(authority, actor_subject_id,
                                         workspace_id, database_id):
    """Resolve one source by database ID under its independent grant."""
    actor = authority._require_active(actor_subject_id)
    workspace = _exact_id(workspace_id, "workspace_id")
    database = _exact_id(database_id, "database_id")
    with authority._reader() as con:
        con.execute("BEGIN")
        authority._workspace(con, workspace)
        if not database_access.installed(con):
            raise AuthorityUnavailable("database sharing is not installed")
        row = con.execute(
            "SELECT db.id,db.title,doc.title AS page_title "
            "FROM workspace_collaboration_databases db "
            "JOIN workspace_collaboration_documents doc ON doc.id=db.document_id "
            "WHERE db.id=? AND doc.workspace_id=? AND doc.archived=0",
            (database, workspace),
        ).fetchone()
        if row is None:
            raise NotFoundOrDenied("not found or permission denied")
        return {"contract": CONTRACT, "workspace_id": workspace,
                "source": _scoped_source(authority, con, actor, workspace, row)}


def _exact_id(value, field):
    try:
        if not isinstance(value, str) or _identifier(value, field=field) != value:
            raise ValueError("noncanonical identity")
    except ValueError as exc:
        raise LinkedDatabaseShapeUnavailable("invalid linked " + field) from exc
    return value


def source_of(block):
    """Validate a stored reference; never infer an origin from a cache or title."""
    if not isinstance(block, dict) or block.get("type") not in VIEW_TYPES:
        raise LinkedDatabaseShapeUnavailable("supported linked database block required")
    _exact_id(block.get("id"), "block_id")
    props = block.get("props", {})
    if not isinstance(props, dict):
        raise LinkedDatabaseShapeUnavailable("linked database props required")
    source = props.get("linked_database")
    if not isinstance(source, dict) or set(source) != {"document_id", "database_id"}:
        raise LinkedDatabaseShapeUnavailable("explicit linked database source required")
    target = (_exact_id(source["document_id"], "document_id"),
        _exact_id(source["database_id"], "database_id"))
    if "database_id" in props and props["database_id"] != target[1]:
        raise LinkedDatabaseShapeUnavailable("conflicting linked database identity")
    if block.get("content", "") != "" or (CACHE_KEYS | VIEW_STATE_KEYS).intersection(props):
        raise LinkedDatabaseShapeUnavailable("a linked instance cannot store source or active-view caches")
    views = props.get("views")
    if not isinstance(views, list) or not 1 <= len(views) <= 64:
        raise LinkedDatabaseShapeUnavailable("one to 64 local views required")
    identities = set()
    for view in views:
        if not isinstance(view, dict) or set(view) != {"id", "name", "type", "config"}:
            raise LinkedDatabaseShapeUnavailable("explicit local view descriptor required")
        identity = _exact_id(view["id"], "view_id")
        if identity in identities or view["type"] not in VIEW_TYPES:
            raise LinkedDatabaseShapeUnavailable("unique supported local view required")
        identities.add(identity)
        if not isinstance(view["name"], str) or len(view["name"]) > 1000 or not isinstance(view["config"], dict):
            raise LinkedDatabaseShapeUnavailable("invalid local view settings")
        config = view["config"]
        if set(config) - VIEW_STATE_KEYS:
            raise LinkedDatabaseShapeUnavailable("unsupported local view setting")
        if any(key in config and not isinstance(config[key], bool) for key in
                ("show_source_title", "show_vertical_lines", "show_page_icons", "wrap_all_content")):
            raise LinkedDatabaseShapeUnavailable("invalid local view display setting")
        if "property_order" in config:
            order = config["property_order"]
            if (not isinstance(order, list) or len(order) > 500
                    or any(not isinstance(item, str) for item in order)
                    or len(set(order)) != len(order)):
                raise LinkedDatabaseShapeUnavailable("invalid local property order")
        if "group_sort" in config and (not isinstance(config["group_sort"], str)
                or config["group_sort"] not in {
                    "manual", "asc", "desc", "count_asc", "count_desc"}):
            raise LinkedDatabaseShapeUnavailable("invalid local group sort")
        if "group_order" in config:
            order = config["group_order"]
            if (not isinstance(order, list) or len(order) > 500
                    or any(not isinstance(item, str) or len(item) > 500 for item in order)
                    or len(set(order)) != len(order)):
                raise LinkedDatabaseShapeUnavailable("invalid local group order")
        if "filter_group" in config:
            _validate_filter_group(config["filter_group"])
        for key in ("filters", "sorts"):
            if key in config and (not isinstance(config[key], list)
                    or any(not isinstance(item, dict) for item in config[key])):
                raise LinkedDatabaseShapeUnavailable("invalid local view conditions")
    active = next((view for view in views if view["id"] == props.get("active_view_id")), None)
    if active is None or active["type"] != block["type"]:
        raise LinkedDatabaseShapeUnavailable("active local view required")
    return target


def _scoped_source_reference(block):
    """Accept legacy pairs or a database-only reference in staged services."""
    if not isinstance(block, dict) or block.get("type") not in VIEW_TYPES:
        raise LinkedDatabaseShapeUnavailable("supported linked database block required")
    props = block.get("props")
    source = props.get("linked_database") if isinstance(props, dict) else None
    if not isinstance(source, dict):
        raise LinkedDatabaseShapeUnavailable("explicit linked database source required")
    if set(source) == {"document_id", "database_id"}:
        return source_of(block)
    if set(source) != {"database_id"}:
        raise LinkedDatabaseShapeUnavailable("explicit linked database source required")
    return None, _exact_id(source["database_id"], "database_id")


def _validate_scoped_source(block, source_document, database_id):
    checked = deepcopy(block)
    checked["props"]["linked_database"] = {
        "document_id": source_document, "database_id": database_id,
    }
    if source_of(checked) != (source_document, database_id):
        raise LinkedDatabaseShapeUnavailable("linked source identity changed")


@dataclass(frozen=True)
class _LinkedSourceInsertScope:
    actor: str
    workspace: str
    database: str
    source_document: str
    workspace_epoch: int
    document_epoch: int
    database_epoch: int

    def request_context(self):
        return {"workspace_id": self.workspace, "database_id": self.database,
                "workspace_epoch": self.workspace_epoch,
                "document_epoch": self.document_epoch,
                "database_epoch": self.database_epoch}

    def authorize(self, authority, con, actor):
        if actor != self.actor:
            raise NotFoundOrDenied("not found or permission denied")
        current = database_access.resolve_on_connection(
            authority, con, actor, self.workspace, self.database,
        )
        if (current.document_id != self.source_document
                or current.workspace_epoch != self.workspace_epoch
                or current.document_epoch != self.document_epoch
                or current.database_epoch != self.database_epoch):
            raise NotFoundOrDenied("not found or permission denied")


def insert_linked_database_scoped(authority, actor_subject_id, workspace_id,
                                  document_id, database_id, block_id, *,
                                  expected_document_sequence,
                                  expected_workspace_epoch,
                                  expected_document_epoch, operation_id,
                                  after_block_id=None, view_type="table",
                                  view_name="표", view_config=None,
                                  layout=None, fault=None):
    """Insert a linked block using only a client-visible database identity."""
    actor = authority._require_active(actor_subject_id)
    workspace = _exact_id(workspace_id, "workspace_id")
    document = _exact_id(document_id, "document_id")
    database = _exact_id(database_id, "database_id")
    block_identity = _exact_id(block_id, "block_id")
    operation = _operation_id(operation_id)
    if after_block_id is not None:
        after_block_id = _exact_id(after_block_id, "after_block_id")
    if view_type not in VIEW_TYPES:
        raise ValueError("supported linked view type required")
    if not isinstance(view_name, str) or not view_name or len(view_name) > 1000:
        raise ValueError("linked view name required")
    if view_config is None:
        view_config = {}
    if (not isinstance(view_config, dict)
            or len(_canonical_json(view_config).encode("utf-8")) > 16384):
        raise ValueError("bounded linked view config required")
    if layout is None:
        layout = {}
    layout_keys = {"layout_group", "layout_column", "layout_columns",
                   "tab_group", "tab_id", "indent", "visual_indent"}
    if (not isinstance(layout, dict) or set(layout) - layout_keys
            or len(_canonical_json(layout).encode("utf-8")) > 8192):
        raise ValueError("bounded linked block layout required")
    source = database_access.resolve(authority, actor, workspace, database)
    scope = _LinkedSourceInsertScope(
        actor, workspace, database, source.document_id,
        source.workspace_epoch, source.document_epoch, source.database_epoch,
    )
    view_id = _stable_id("linkedview", actor, document, block_identity, operation)
    props = {**deepcopy(layout), "linked_database": {"database_id": database},
             "views": [{"id": view_id, "name": view_name, "type": view_type,
                        "config": deepcopy(view_config)}], "active_view_id": view_id}
    block = {"id": block_identity, "type": view_type, "content": "", "props": props}
    _validate_scoped_source(block, source.document_id, database)
    return authority.execute_typed_command(
        actor, document, command_kind="block.insert", target_id=block_identity,
        payload={"type": view_type, "content": "", "props": props,
                 "after_block_id": after_block_id},
        expected_document_sequence=expected_document_sequence,
        expected_workspace_epoch=expected_workspace_epoch,
        expected_document_epoch=expected_document_epoch,
        operation_id=operation, fault=fault, _linked_source_scope=scope,
    )


def read_linked_database(authority, actor_subject_id, document_id, block_id, *, workspace_id=None):
    """Read current shared rows through an authorized, durable instance.

    Replay the instance outside SQLite, then validate both documents and read
    the source DB in a single reader snapshot. No source content is persisted.
    """
    actor = authority._require_active(actor_subject_id)
    document_id = _exact_id(document_id, "document_id")
    block_id = _exact_id(block_id, "block_id")
    if workspace_id is not None:
        workspace_id = _exact_id(workspace_id, "workspace_id")
    try:
        instance = synced_blocks._current(authority, actor, document_id, MAX_SOURCE_BYTES, workspace_id)
        body = synced_blocks.index_blocks(json.loads(instance.descriptor_json)["blocks"])
    except synced_blocks.SyncedShapeUnavailable as exc:
        raise LinkedDatabaseShapeUnavailable(str(exc)) from exc
    if block_id not in body:
        raise NotFoundOrDenied("not found or permission denied")
    block = body[block_id]
    source_document, database_id = source_of(block)
    with authority._reader() as con:
        con.execute("BEGIN")
        _instance_document, stamp = synced_blocks._authorize(authority, con, actor, document_id)
        if stamp != instance.stamp:
            raise LinkedDatabaseSourceChanged("linked instance changed during preparation")
        original, source_stamp = synced_blocks._authorize(authority, con, actor, source_document)
        if source_stamp[0] != stamp[0]:
            raise NotFoundOrDenied("not found or permission denied")
        descriptor = authority._editing_snapshot(con, original)
        database = next((item for item in descriptor["databases"] if item["id"] == database_id), None)
        if database is None:
            raise NotFoundOrDenied("not found or permission denied")
        if instance.size + len(_canonical_json(database).encode("utf-8")) > MAX_SOURCE_BYTES:
            raise LinkedDatabaseShapeUnavailable("linked source exceeds the preparation budget")
        _workspace, _document, role = authority._authorized_document(con, actor, source_document)
        database["rows"] = authority._materialize_rows(con, actor, database)
        database["effective_role"] = role
        database["capabilities"] = {"atomic_cell_batch": True, "max_cell_batch": 1000}
        database["capabilities"]["row_comments"] = database_row_content.installed(con)
        result = {"contract": CONTRACT, "instance": {"document_id": document_id, "block_id": block_id},
            "source": {"document_id": source_document, "database_id": database_id},
            "database": database, "views": deepcopy(block["props"]["views"]),
            "active_view_id": block["props"]["active_view_id"],
            "witnesses": [{"document_id": identity, "workspace_id": witness[0],
                "workspace_epoch": witness[1], "document_sequence": witness[4],
                "document_epoch": witness[5], "generation": witness[6]}
                for identity, witness in dict([(document_id, stamp), (source_document, source_stamp)]).items()]}
        if instance.size + len(_canonical_json(result).encode("utf-8")) > MAX_SOURCE_BYTES:
            raise LinkedDatabaseShapeUnavailable("linked response exceeds the preparation budget")
        return result


def read_linked_database_scoped(authority, actor_subject_id, workspace_id,
                                document_id, block_id):
    """Staged linked read requiring instance-page and source-database access.

    The source owner's page is only checked as the stored identity. Its title,
    body and page role never enter the response. Both permissions are resolved
    in one SQLite snapshot after replaying the instance outside SQLite.
    """
    actor = authority._require_active(actor_subject_id)
    workspace = _exact_id(workspace_id, "workspace_id")
    document = _exact_id(document_id, "document_id")
    block_identity = _exact_id(block_id, "block_id")
    try:
        instance = synced_blocks._current(
            authority, actor, document, MAX_SOURCE_BYTES, workspace,
        )
        body = synced_blocks.index_blocks(
            json.loads(instance.descriptor_json)["blocks"],
        )
    except synced_blocks.SyncedShapeUnavailable as exc:
        raise LinkedDatabaseShapeUnavailable(str(exc)) from exc
    if block_identity not in body:
        raise NotFoundOrDenied("not found or permission denied")
    block = body[block_identity]
    source_document, database_id = _scoped_source_reference(block)
    with authority._reader() as con:
        con.execute("BEGIN")
        _instance_document, stamp = synced_blocks._authorize(
            authority, con, actor, document,
        )
        if stamp != instance.stamp:
            raise LinkedDatabaseSourceChanged(
                "linked instance changed during preparation",
            )
        if stamp[0] != workspace:
            raise NotFoundOrDenied("not found or permission denied")
        source_access = database_access.resolve_on_connection(
            authority, con, actor, workspace, database_id,
        )
        if source_document is not None and source_access.document_id != source_document:
            raise NotFoundOrDenied("not found or permission denied")
        _validate_scoped_source(block, source_access.document_id, database_id)
        projection = database_access.read_projection(
            authority, actor, workspace, database_id, _connection=con,
        )
        local_views = deepcopy(block["props"]["views"])
        denied_properties = {
            str(prop["id"]) for prop in projection["database"]["properties"]
            if isinstance(prop.get("config"), dict)
            and prop["config"].get("unavailable") is True
        }
        database_access._redact_inaccessible_relation_settings(
            {"properties": projection["database"]["properties"],
             "views": local_views, "templates": []}, denied_properties,
        )
        result = {
            "contract": CONTRACT,
            "instance": {"document_id": document, "block_id": block_identity},
            "instance_witness": {
                "workspace_epoch": stamp[1], "document_sequence": stamp[4],
                "document_epoch": stamp[5], "generation": stamp[6],
            },
            "source": {"database_id": database_id},
            "database": projection["database"],
            "views": local_views,
            "active_view_id": block["props"]["active_view_id"],
            "access": {key: projection[key] for key in (
                "workspace_epoch", "document_epoch", "database_epoch",
                "document_sequence")},
        }
        if instance.size + len(_canonical_json(result).encode("utf-8")) > MAX_SOURCE_BYTES:
            raise LinkedDatabaseShapeUnavailable(
                "linked response exceeds the preparation budget",
            )
        return result


def redact_linked_blocks_on_connection(authority, con, actor, workspace, blocks):
    """Project legacy links in one authorized read snapshot, without storage edits."""
    projections = {}
    for block in blocks:
        props = block.get("props")
        if not isinstance(props, dict) or "linked_database" not in props:
            continue
        try:
            source_document, database_id = _scoped_source_reference(block)
            access = database_access.resolve_on_connection(
                authority, con, actor, workspace, database_id,
            )
            if source_document is not None and access.document_id != source_document:
                raise NotFoundOrDenied("not found or permission denied")
            _validate_scoped_source(block, access.document_id, database_id)
            if database_id not in projections:
                projections[database_id] = database_access.read_projection(
                    authority, actor, workspace, database_id, _connection=con,
                )
            projection = projections[database_id]["database"]
        except (LinkedDatabaseShapeUnavailable, NotFoundOrDenied):
            block["type"] = "paragraph"
            block["content"] = ""
            block["props"] = {"linked_database_unavailable": True}
            continue
        denied = {
            str(prop["id"]) for prop in projection["properties"]
            if isinstance(prop.get("config"), dict)
            and prop["config"].get("unavailable") is True
        }
        local_views = deepcopy(props["views"])
        database_access._redact_inaccessible_relation_settings(
            {"properties": projection["properties"],
             "views": local_views, "templates": []}, denied,
        )
        page_role = (CollaborativeIAMAuthority._resolution_on_connection(
            con, actor, workspace, access.document_id,
        )[0] if source_document is not None else None)
        if page_role is None:
            props["linked_database"] = {"database_id": database_id}
        props["views"] = local_views


def read_linked_instance_page_scoped(authority, actor_subject_id, workspace_id,
                                     document_id):
    """Stage a page body with legacy linked references redacted per source grant."""
    actor = authority._require_active(actor_subject_id)
    workspace = _exact_id(workspace_id, "workspace_id")
    document = _exact_id(document_id, "document_id")
    try:
        instance = synced_blocks._current(
            authority, actor, document, MAX_SOURCE_BYTES, workspace,
        )
        snapshot = json.loads(instance.descriptor_json)
    except synced_blocks.SyncedShapeUnavailable as exc:
        raise LinkedDatabaseShapeUnavailable(str(exc)) from exc
    with authority._reader() as con:
        con.execute("BEGIN")
        _instance_document, stamp = synced_blocks._authorize(
            authority, con, actor, document,
        )
        if stamp != instance.stamp:
            raise LinkedDatabaseSourceChanged(
                "linked instance changed during preparation",
            )
        if stamp[0] != workspace:
            raise NotFoundOrDenied("not found or permission denied")
        _ws, _page, role = authority._authorized_document(con, actor, document)
        redact_linked_blocks_on_connection(
            authority, con, actor, workspace, snapshot["blocks"],
        )
        return {"contract": CONTRACT, "workspace_id": workspace,
                "document_id": document, "role": role,
                "document_sequence": stamp[4], "snapshot": snapshot}


@dataclass(frozen=True)
class _ScopedLinkedCellScope:
    actor: str
    workspace: str
    instance_document: str
    block: str
    source_document: str
    database: str
    prepared_stamp: tuple
    expected_witness: tuple[int, int, int, int]

    def request_context(self):
        return {"document_id": self.instance_document, "block_id": self.block,
                "instance_witness": list(self.expected_witness)}

    def authorize(self, authority, con, actor, access, *, replay):
        if (actor != self.actor or access.workspace_id != self.workspace
                or access.document_id != self.source_document
                or access.database_id != self.database):
            raise NotFoundOrDenied("not found or permission denied")
        authority._exact_access(
            con, actor, self.instance_document, minimum_role="edit_content",
            expected_workspace_epoch=self.expected_witness[0],
            expected_document_epoch=self.expected_witness[2],
        )
        _document, stamp = synced_blocks._authorize(
            authority, con, actor, self.instance_document,
        )
        if stamp[0] != self.workspace or stamp != self.prepared_stamp:
            raise LinkedDatabaseSourceChanged(
                "linked instance changed during write preparation",
            )
        if not replay and (
                stamp[1], stamp[4], stamp[5], stamp[6]) != self.expected_witness:
            raise LinkedDatabaseSourceChanged("linked instance witness is stale")


def _scoped_write_scope(authority, actor_subject_id, workspace_id,
                        document_id, block_id, source, instance_witness,
                        source_access):
    """Bind a linked mutation to current instance and source identities."""
    actor = authority._require_active(actor_subject_id)
    workspace = _exact_id(workspace_id, "workspace_id")
    document = _exact_id(document_id, "document_id")
    block_identity = _exact_id(block_id, "block_id")
    if not isinstance(source, dict) or set(source) != {"database_id"}:
        raise ValueError("exact scoped linked source required")
    database_id = _exact_id(source["database_id"], "database_id")
    fields = ("workspace_epoch", "document_sequence", "document_epoch",
              "generation")
    if not isinstance(instance_witness, dict) or set(instance_witness) != set(fields):
        raise ValueError("exact linked instance witness required")
    witness = tuple(instance_witness[key] for key in fields)
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 1
           for value in witness):
        raise ValueError("positive linked instance witness required")
    source_fields = {"workspace_epoch", "document_sequence",
                     "document_epoch", "database_epoch"}
    if not isinstance(source_access, dict) or set(source_access) != source_fields:
        raise ValueError("exact linked source access required")
    try:
        instance = synced_blocks._current(
            authority, actor, document, MAX_SOURCE_BYTES, workspace,
        )
        body = synced_blocks.index_blocks(
            json.loads(instance.descriptor_json)["blocks"],
        )
    except synced_blocks.SyncedShapeUnavailable as exc:
        raise LinkedDatabaseShapeUnavailable(str(exc)) from exc
    if block_identity not in body:
        raise NotFoundOrDenied("not found or permission denied")
    source_document, actual_database = _scoped_source_reference(
        body[block_identity],
    )
    if actual_database != database_id:
        raise LinkedDatabaseSourceChanged("linked source identity changed")
    with authority._reader() as con:
        con.execute("BEGIN")
        resolved_source = database_access.resolve_on_connection(
            authority, con, actor, workspace, database_id,
        )
        if source_document is not None and resolved_source.document_id != source_document:
            raise NotFoundOrDenied("not found or permission denied")
        _validate_scoped_source(body[block_identity], resolved_source.document_id,
                                database_id)
    return _ScopedLinkedCellScope(
        actor, workspace, document, block_identity, resolved_source.document_id,
        database_id, instance.stamp, witness,
    )


def execute_linked_cells_scoped(authority, actor_subject_id, workspace_id,
                                document_id, block_id, *, source,
                                instance_witness, source_access, cells,
                                operation_id, fault=None):
    """Stage linked cell writes without granting access to the source page."""
    scope = _scoped_write_scope(
        authority, actor_subject_id, workspace_id, document_id, block_id,
        source, instance_witness, source_access,
    )
    return database_access.change_cells(
        authority, scope.actor, scope.workspace, scope.database, cells=cells,
        expected_document_sequence=source_access["document_sequence"],
        expected_workspace_epoch=source_access["workspace_epoch"],
        expected_document_epoch=source_access["document_epoch"],
        expected_database_epoch=source_access["database_epoch"],
        operation_id=operation_id, fault=fault, _linked_scope=scope,
    )


def execute_linked_row_create_scoped(authority, actor_subject_id, workspace_id,
                                     document_id, block_id, *, source,
                                     instance_witness, source_access, title,
                                     operation_id, use_default_template=True,
                                     template_id=None, fault=None):
    """Create one source row only while the linked instance remains editable."""
    scope = _scoped_write_scope(
        authority, actor_subject_id, workspace_id, document_id, block_id,
        source, instance_witness, source_access,
    )
    return database_access.create_blank_row(
        authority, scope.actor, scope.workspace, scope.database,
        title=title, use_default_template=use_default_template,
        template_id=template_id,
        expected_document_sequence=source_access["document_sequence"],
        expected_workspace_epoch=source_access["workspace_epoch"],
        expected_document_epoch=source_access["document_epoch"],
        expected_database_epoch=source_access["database_epoch"],
        operation_id=operation_id, fault=fault, _linked_scope=scope,
    )


def execute_linked_template_create_scoped(authority, actor_subject_id, workspace_id,
                                          document_id, block_id, *, source,
                                          instance_witness, source_access, name,
                                          target_id, operation_id, fault=None):
    """Create a blank source template under both current linked grants."""
    scope = _scoped_write_scope(
        authority, actor_subject_id, workspace_id, document_id, block_id,
        source, instance_witness, source_access,
    )
    return authority.execute_schema_command(
        scope.actor, scope.source_document, database_id=scope.database,
        command_kind="database.template.create", target_id=target_id,
        payload={"name": name, "body": [], "default_values": {}},
        expected_target_version=0,
        expected_document_sequence=source_access["document_sequence"],
        expected_workspace_epoch=source_access["workspace_epoch"],
        expected_document_epoch=source_access["document_epoch"],
        expected_database_epoch=source_access["database_epoch"],
        operation_id=operation_id, fault=fault, _linked_scope=scope,
    )


def execute_linked_template_update_scoped(authority, actor_subject_id, workspace_id,
                                          document_id, block_id, *, source,
                                          instance_witness, source_access, name,
                                          target_id, body, default_values,
                                          expected_target_version, operation_id,
                                          fault=None):
    """Update a source template without disclosing its owning page."""
    scope = _scoped_write_scope(
        authority, actor_subject_id, workspace_id, document_id, block_id,
        source, instance_witness, source_access,
    )
    return authority.execute_schema_command(
        scope.actor, scope.source_document, database_id=scope.database,
        command_kind="database.template.update", target_id=target_id,
        payload={"name": name, "body": body, "default_values": default_values},
        expected_target_version=expected_target_version,
        expected_document_sequence=source_access["document_sequence"],
        expected_workspace_epoch=source_access["workspace_epoch"],
        expected_document_epoch=source_access["document_epoch"],
        expected_database_epoch=source_access["database_epoch"],
        operation_id=operation_id, fault=fault, _linked_scope=scope,
    )
