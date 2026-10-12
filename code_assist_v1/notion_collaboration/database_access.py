"""Database-scoped access resolution for the staged 2428 sharing successor.

Selected content-free HTTP discovery surfaces use this staged authority, but
direct grants remain unavailable to users until the remaining UI, realtime,
search, and export surfaces share its authorization boundary.
"""
from __future__ import annotations

from dataclasses import dataclass
from contextlib import nullcontext
from html import unescape
import json
import re
import time
from urllib.parse import unquote

from code_assist_v1 import notion_workspace_database_sharing_migration as migration
from code_assist_v1 import notion_workspace_database_upload_scope_migration as upload_scope_migration
from code_assist_v1 import notion_workspace_direct_database_favorite_migration as direct_favorite_migration

from .canonical_authority import (
    AuthorityUnavailable, NotFoundOrDenied, _identifier, _operation_id,
    _request_sha256, _canonical_json, _sha256, _stable_id,
    _require_unlocked_or_temporary,
)
from .iam_authority import (
    CollaborativeIAMAuthority, PAGE_ROLE_LEVEL, _expected_epoch, _page_role,
    _principal_type,
)
from . import database_trash


@dataclass(frozen=True)
class DatabaseAccess:
    workspace_id: str
    document_id: str
    database_id: str
    role: str
    sources: tuple[dict, ...]
    workspace_epoch: int
    document_epoch: int
    database_epoch: int


CONTRACT = "code_assist.notion.database_sharing.v1"
_DIRECT_COMMENT_FILE_MARKDOWN = re.compile(
    r"\[[^\]\n]{1,200}\]\(/api/notion/workspaces/([A-Za-z0-9_-]+)/"
    r"databases/([A-Za-z0-9_-]+)/files/([A-Za-z0-9_-]+)\)"
)
_UNSCOPED_PAGE_LINK = re.compile(
    r"(?:^|[\s=\(\[\"'])"
    r"(?:https?://[^\s/\"'<>]+)?"
    r"/(?:page|api/notion/pages)/[A-Za-z0-9_%.-]+",
    re.IGNORECASE,
)


def _has_unscoped_page_link(value: str) -> bool:
    # HTML and percent escaping can be nested by imports or rich-text
    # serialization. A single decoding pass can expose only another escape,
    # so keep decoding until stable and fail closed on excessive nesting.
    text = value
    for _ in range(16):
        if _UNSCOPED_PAGE_LINK.search(text):
            return True
        decoded = unquote(unescape(text))
        if decoded == text:
            return False
        text = decoded
    return True


def installed(con) -> bool:
    marker = con.execute(
        "SELECT version,name,checksum,actual_schema_sha256 "
        "FROM workspace_schema_migrations ORDER BY version DESC LIMIT 1"
    ).fetchone()
    return marker is not None and tuple(marker) in {
        (item.TARGET_VERSION, item.TARGET_NAME,
         item.TARGET_CHECKSUM, item.TARGET_ACTUAL_SCHEMA_SHA256)
        for item in (migration, upload_scope_migration, direct_favorite_migration)
    }


def upload_scope_installed(con) -> bool:
    marker = con.execute(
        "SELECT version,name,checksum,actual_schema_sha256 "
        "FROM workspace_schema_migrations ORDER BY version DESC LIMIT 1"
    ).fetchone()
    return marker is not None and tuple(marker) in {
        (item.TARGET_VERSION, item.TARGET_NAME, item.TARGET_CHECKSUM,
         item.TARGET_ACTUAL_SCHEMA_SHA256)
        for item in (upload_scope_migration, direct_favorite_migration)
    }


def resolve_on_connection(authority, con, actor_subject_id: str,
                          workspace_id: str, database_id: str) -> DatabaseAccess:
    """Resolve one database without treating its owner's page as disclosed."""
    actor = authority._require_active(actor_subject_id)
    workspace = _identifier(workspace_id, field="workspace_id")
    database = _identifier(database_id, field="database_id")
    if not installed(con):
        raise AuthorityUnavailable("database sharing is not installed")
    row = con.execute(
        "SELECT db.document_id,doc.archived,doc.access_epoch,"
        "ws.security_epoch FROM workspace_collaboration_databases db "
        "JOIN workspace_collaboration_documents doc ON doc.id=db.document_id "
        "JOIN workspaces ws ON ws.id=doc.workspace_id "
        "WHERE db.id=? AND doc.workspace_id=?",
        (database, workspace),
    ).fetchone()
    if row is None or row["archived"] or database_trash.is_trashed(con, database):
        raise NotFoundOrDenied("not found or permission denied")
    hidden_ancestor = con.execute(
        "WITH RECURSIVE ancestors(id,parent_document_id,archived) AS ("
        "SELECT id,parent_document_id,archived FROM workspace_collaboration_documents "
        "WHERE id=? AND workspace_id=? "
        "UNION SELECT parent.id,parent.parent_document_id,parent.archived "
        "FROM workspace_collaboration_documents parent JOIN ancestors child "
        "ON child.parent_document_id=parent.id WHERE parent.workspace_id=?) "
        "SELECT 1 FROM ancestors WHERE archived=1 LIMIT 1",
        (row["document_id"], workspace, workspace),
    ).fetchone()
    if hidden_ancestor:
        raise NotFoundOrDenied("not found or permission denied")
    _parent_role, parent_sources = CollaborativeIAMAuthority._resolution_on_connection(
        con, actor, workspace, str(row["document_id"])
    )
    sources = [{**item, "scope": "parent_page"} for item in parent_sources]
    grants = con.execute(
        "SELECT grant.principal_type,grant.principal_id,grant.role FROM "
        "workspace_collaboration_database_access_grants grant "
        "WHERE grant.database_id=? AND grant.active=1 AND ("
        "(grant.principal_type='subject' AND grant.principal_id=?) OR "
        "(grant.principal_type='group' AND EXISTS ("
        "SELECT 1 FROM workspace_group_members gm JOIN workspace_memberships wm "
        "ON wm.workspace_id=gm.workspace_id AND wm.subject_id=gm.subject_id "
        "AND wm.revoked_at IS NULL WHERE gm.workspace_id=? "
        "AND gm.group_id=grant.principal_id AND gm.subject_id=?)) OR "
        "(grant.principal_type='teamspace' AND EXISTS ("
        "SELECT 1 FROM teamspace_memberships tm JOIN teamspaces ts "
        "ON ts.workspace_id=tm.workspace_id AND ts.id=tm.teamspace_id "
        "AND ts.archived_at IS NULL JOIN workspace_memberships wm "
        "ON wm.workspace_id=tm.workspace_id AND wm.subject_id=tm.subject_id "
        "AND wm.revoked_at IS NULL WHERE tm.workspace_id=? "
        "AND tm.teamspace_id=grant.principal_id AND tm.subject_id=? "
        "AND tm.revoked_at IS NULL))) "
        "ORDER BY grant.principal_type,grant.principal_id",
        (database, actor, workspace, actor, workspace, actor),
    ).fetchall()
    for grant in grants:
        sources.append({"scope": "database", "kind": f"{grant['principal_type']}_grant",
                        "principal_id": str(grant["principal_id"]),
                        "role": str(grant["role"]), "inherited": False})
    if not sources:
        raise NotFoundOrDenied("not found or permission denied")
    role = max((str(item["role"]) for item in sources), key=PAGE_ROLE_LEVEL.__getitem__)
    state = con.execute(
        "SELECT access_epoch FROM workspace_collaboration_database_access_state "
        "WHERE database_id=?", (database,),
    ).fetchone()
    return DatabaseAccess(
        workspace, str(row["document_id"]), database, role, tuple(sources),
        int(row["security_epoch"]), int(row["access_epoch"]),
        int(state["access_epoch"]) if state else 1,
    )


def resolve(authority, actor_subject_id: str, workspace_id: str,
            database_id: str) -> DatabaseAccess:
    with authority._reader() as con:
        con.execute("BEGIN")
        return resolve_on_connection(authority, con, actor_subject_id,
                                     workspace_id, database_id)


def require_write_on_connection(authority, con, actor_subject_id: str,
                                workspace_id: str, database_id: str, *,
                                minimum_role: str,
                                expected_workspace_epoch: int,
                                expected_document_epoch: int,
                                expected_database_epoch: int) -> DatabaseAccess:
    """Fence a database mutation against its role and all three access epochs."""
    required = _page_role(minimum_role)
    if required not in {"comment", "edit_content", "edit", "full_access"}:
        raise ValueError("database write role required")
    workspace_epoch = _expected_epoch(expected_workspace_epoch, field="workspace_epoch")
    document_epoch = _expected_epoch(expected_document_epoch, field="document_epoch")
    database_epoch = _expected_epoch(expected_database_epoch, field="database_epoch")
    current = resolve_on_connection(
        authority, con, actor_subject_id, workspace_id, database_id,
    )
    if (PAGE_ROLE_LEVEL[current.role] < PAGE_ROLE_LEVEL[required]
            or current.workspace_epoch != workspace_epoch
            or current.document_epoch != document_epoch
            or current.database_epoch != database_epoch):
        raise NotFoundOrDenied("not found or permission denied")
    return current


def _visible_file_ids_on_connection(con, database_id: str, *,
                                    actor_subject_id: str | None = None) -> frozenset[str]:
    """Collect live references and this actor's pending database uploads."""
    from . import database_row_page_placement, database_row_trash
    from .database_move import _file_ids

    hidden = (database_row_trash.archived_row_ids(con, database_id)
              | database_row_page_placement.page_row_ids(con, database_id))
    references: set[str] = set()
    for row in con.execute(
        "SELECT item.id,property.property_type,cell.value_json "
        "FROM workspace_collaboration_database_rows item "
        "JOIN workspace_collaboration_database_cells cell ON cell.row_id=item.id "
        "JOIN workspace_collaboration_database_properties property "
        "ON property.id=cell.property_id AND property.database_id=item.database_id "
        "WHERE item.database_id=? AND property.property_type='files'",
        (database_id,),
    ).fetchall():
        if str(row["id"]) in hidden:
            continue
        try:
            value = json.loads(str(row["value_json"]))
        except (TypeError, ValueError) as exc:
            raise AuthorityUnavailable("database file reference is invalid") from exc
        if not isinstance(value, list):
            raise AuthorityUnavailable("database file reference is invalid")
        references.update(_file_ids(value))
    for table, column in (
            ("workspace_collaboration_database_row_bodies", "blocks_json"),
            ("workspace_collaboration_database_row_appearance", "appearance_json")):
        for row in con.execute(
            f"SELECT item.id,source.{column} FROM {table} source "
            "JOIN workspace_collaboration_database_rows item ON item.id=source.row_id "
            "WHERE item.database_id=?", (database_id,),
        ).fetchall():
            if str(row["id"]) in hidden:
                continue
            try:
                references.update(_file_ids(json.loads(str(row[column]))))
            except (TypeError, ValueError) as exc:
                raise AuthorityUnavailable("database file reference is invalid") from exc
    for row in con.execute(
        "SELECT body_json,default_values_json FROM workspace_collaboration_database_templates "
        "WHERE database_id=? AND state='active'", (database_id,),
    ).fetchall():
        try:
            references.update(_file_ids(json.loads(str(row["body_json"]))))
            references.update(_file_ids(json.loads(str(row["default_values_json"]))))
        except (TypeError, ValueError) as exc:
            raise AuthorityUnavailable("database template file reference is invalid") from exc
    if upload_scope_installed(con):
        owner = con.execute(
            "SELECT db.document_id,doc.workspace_id FROM "
            "workspace_collaboration_databases db JOIN "
            "workspace_collaboration_documents doc ON doc.id=db.document_id "
            "WHERE db.id=?", (database_id,),
        ).fetchone()
        if owner is not None:
            comment_files: set[str] = set()
            for comment in con.execute(
                "SELECT c.content,r.id AS row_id FROM workspace_collaboration_comments c "
                "JOIN workspace_collaboration_database_rows r "
                "ON r.id=json_extract(c.anchor_json,'$.row_id') "
                "WHERE c.document_id=? AND c.deleted_at IS NULL "
                "AND r.database_id=? "
                "AND json_extract(c.anchor_json,'$.kind')='database_row' "
                "AND json_extract(c.anchor_json,'$.database_id')=? "
                "AND instr(c.content,'/api/notion/workspaces/')>0",
                (owner["document_id"], database_id, database_id),
            ).fetchall():
                if str(comment["row_id"]) in hidden:
                    continue
                for file_workspace, file_database, file_id in (
                        _DIRECT_COMMENT_FILE_MARKDOWN.findall(str(comment["content"]))):
                    if file_workspace == str(owner["workspace_id"]) and file_database == database_id:
                        comment_files.add(file_id)
            file_ids = sorted(comment_files)
            for start in range(0, len(file_ids), 500):
                batch = file_ids[start:start + 500]
                placeholders = ",".join("?" for _ in batch)
                for scoped in con.execute(
                    "SELECT file.id FROM workspace_collaboration_files file "
                    "JOIN workspace_collaboration_database_upload_scopes scope "
                    "ON scope.session_id=file.upload_session_id "
                    f"WHERE file.id IN ({placeholders}) AND file.state='active' "
                    "AND file.document_id=? AND file.workspace_id=? "
                    "AND scope.database_id=?",
                    (*batch, owner["document_id"], owner["workspace_id"], database_id),
                ).fetchall():
                    references.add(str(scoped["id"]))
    if actor_subject_id is not None and upload_scope_installed(con):
        for row in con.execute(
            "SELECT file.id FROM workspace_collaboration_files file "
            "JOIN workspace_collaboration_database_upload_scopes scope "
            "ON scope.session_id=file.upload_session_id "
            "JOIN workspace_collaboration_databases db ON db.id=scope.database_id "
            "JOIN workspace_collaboration_documents doc ON doc.id=db.document_id "
            "WHERE scope.database_id=? AND file.state='active' "
            "AND file.document_id=db.document_id "
            "AND file.workspace_id=doc.workspace_id "
            "AND file.uploader_subject_id=?",
            (database_id, actor_subject_id),
        ).fetchall():
            references.add(str(row["id"]))
    return frozenset(references)


def _file_on_connection(authority, con, actor_subject_id: str,
                        workspace_id: str, database_id: str, file_id: str):
    file_identity = _identifier(file_id, field="file_id")
    access = resolve_on_connection(
        authority, con, actor_subject_id, workspace_id, database_id,
    )
    if file_identity not in _visible_file_ids_on_connection(
            con, access.database_id, actor_subject_id=actor_subject_id):
        raise NotFoundOrDenied("not found or permission denied")
    row = con.execute(
        "SELECT id,workspace_id,document_id,original_name,mime_type,"
        "size_bytes,content_sha256,blob_relative_path "
        "FROM workspace_collaboration_files WHERE id=? AND state='active'",
        (file_identity,),
    ).fetchone()
    if (row is None or str(row["workspace_id"]) != access.workspace_id
            or str(row["document_id"]) != access.document_id):
        raise NotFoundOrDenied("not found or permission denied")
    return access, row


def _file_metadata(access, row) -> dict:
    return {
        "contract": CONTRACT, "workspace_id": access.workspace_id,
        "database_id": access.database_id, "file_id": str(row["id"]),
        "original_name": str(row["original_name"]),
        "mime_type": str(row["mime_type"]),
        "size_bytes": int(row["size_bytes"]),
        "content_sha256": str(row["content_sha256"]),
    }


def read_file_metadata(authority, actor_subject_id: str, workspace_id: str,
                       database_id: str, file_id: str) -> dict:
    """Authorize one active attachment through its live database reference."""
    with authority._reader() as con:
        con.execute("BEGIN")
        access, row = _file_on_connection(
            authority, con, actor_subject_id, workspace_id, database_id, file_id,
        )
        return _file_metadata(access, row)


def get_file(authority, actor_subject_id: str, workspace_id: str,
             database_id: str, file_id: str) -> dict:
    """Resolve a database attachment's content path in one authorization snapshot.

    This is an internal staged service. A future streaming route must recheck
    access immediately before sending bytes, including after any async work.
    """
    with authority._reader() as con:
        con.execute("BEGIN")
        access, row = _file_on_connection(
            authority, con, actor_subject_id, workspace_id, database_id, file_id,
        )
        try:
            root = authority.storage_root.resolve(strict=True)
            path = (root / str(row["blob_relative_path"])).resolve(strict=True)
            path.relative_to(root)
        except (OSError, ValueError) as exc:
            raise AuthorityUnavailable("authorized file content is unavailable") from exc
        return {**_file_metadata(access, row), "path": path}


def _redact_filter_group(group, denied_properties: frozenset[str]):
    if not isinstance(group, dict) or not isinstance(group.get("children"), list):
        return None
    children = []
    for child in group["children"]:
        if not isinstance(child, dict):
            continue
        if "children" in child:
            nested = _redact_filter_group(child, denied_properties)
            if nested is not None:
                children.append(nested)
        elif str(child.get("property") or "") not in denied_properties:
            children.append(child)
    logic = group.get("logic")
    return {"logic": logic if isinstance(logic, str) and logic in {"and", "or"}
            else "and", "children": children}


def _template_body_has_unscoped_reference(value) -> bool:
    """Recognize page links and embeds a database grant cannot authorize."""
    pending = [value]
    seen = 0
    while pending:
        item = pending.pop()
        seen += 1
        if seen > 10000:
            raise ValueError("database template body is too complex")
        if isinstance(item, dict):
            if (item.get("type") in ("child_page", "synced_block")
                    or any(key in item for key in (
                        "linked_database", "source_page_id", "page_id",
                        "document_id", "source_document_id", "synced_from",
                        "database_id", "target_database_id"))):
                return True
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)
        elif isinstance(item, str):
            if _has_unscoped_page_link(item):
                return True
    return False


def _redact_inaccessible_relation_settings(database: dict,
                                           denied_property_ids: set[str]) -> None:
    if not denied_property_ids:
        return
    denied = frozenset(denied_property_ids | {
        str(item["name"]) for item in database["properties"]
        if item["id"] in denied_property_ids
    })
    for template in database["templates"]:
        defaults = template.get("default_values")
        if isinstance(defaults, dict):
            for property_id in denied_property_ids:
                if property_id in defaults:
                    defaults.pop(property_id)
                    template["default_unavailable"] = True
    for view in database["views"]:
        config = view.get("config")
        if not isinstance(config, dict):
            view["config"] = {}
            continue
        filters = config.get("filters")
        if isinstance(filters, list):
            config["filters"] = [rule for rule in filters if isinstance(rule, dict)
                                 and str(rule.get("property") or "") not in denied]
        if "filter_group" in config:
            config["filter_group"] = _redact_filter_group(
                config["filter_group"], denied)
        sorts = config.get("sorts")
        if isinstance(sorts, list):
            config["sorts"] = [rule for rule in sorts if (
                str(rule.get("property") or rule.get("property_id") or "") not in denied
                if isinstance(rule, dict) else str(rule) not in denied)]
        visibility = config.get("property_visibility")
        if isinstance(visibility, dict):
            config["property_visibility"] = {
                key: value for key, value in visibility.items()
                if key not in denied
            }
        order = config.get("property_order")
        if isinstance(order, list):
            config["property_order"] = [value for value in order if value not in denied]
        if any(isinstance(config.get(key), str) and config[key] in denied
               for key in ("group_property", "board_group_property")):
            config.pop("group_order", None)
        for key in ("group_property", "board_group_property",
                    "calendar_date_property", "timeline_date_property",
                    "timeline_anchor"):
            if isinstance(config.get(key), str) and config[key] in denied:
                config.pop(key, None)


def read_projection(authority, actor_subject_id: str, workspace_id: str,
                    database_id: str, *, _connection=None) -> dict:
    """Read exactly one authorized database without returning its owner page.

    This is a staged service, not an HTTP endpoint. The relation resolver uses
    the target database's own access decision inside the same SQLite snapshot.
    """
    from .database_features import CollaborativeDatabaseAuthority, _database_last_edit
    from . import database_activity

    with (authority._reader() if _connection is None else nullcontext(_connection)) as con:
        if _connection is None:
            con.execute("BEGIN")
        access = resolve_on_connection(
            authority, con, actor_subject_id, workspace_id, database_id,
        )
        document = authority._document(con, access.document_id)
        descriptor = authority._editing_snapshot(con, document)
        database = next((item for item in descriptor["databases"]
                         if item["id"] == access.database_id), None)
        if database is None:
            raise NotFoundOrDenied("not found or permission denied")
        target_roles: dict[str, str | None] = {}

        def target_role(target_id: str) -> str | None:
            if target_id not in target_roles:
                try:
                    target_roles[target_id] = resolve_on_connection(
                        authority, con, actor_subject_id, workspace_id, target_id,
                    ).role
                except NotFoundOrDenied:
                    target_roles[target_id] = None
            return target_roles[target_id]

        def can_read_target(target_id: str) -> bool:
            return target_role(target_id) is not None

        database["rows"] = CollaborativeDatabaseAuthority._materialize_rows(
            con, actor_subject_id, database, target_access=can_read_target,
        )
        parent_page_access = any(source["scope"] == "parent_page"
                                 for source in access.sources)
        if not parent_page_access:
            for row in database["rows"]:
                body = row.get("body")
                blocks = body.get("blocks") if isinstance(body, dict) else None
                if not isinstance(blocks, list):
                    continue
                visible_blocks = [block for block in blocks
                                  if not _template_body_has_unscoped_reference(block)]
                if len(visible_blocks) != len(blocks):
                    row["body"] = {"blocks": visible_blocks}
                    row["body_unavailable"] = True
        denied_property_ids: set[str] = set()
        for property_item in database["properties"]:
            if property_item["type"] == "relation":
                target_id = str(property_item["config"].get("target_database_id") or "")
                if not can_read_target(target_id):
                    denied_property_ids.add(str(property_item["id"]))
                    property_item["config"] = {"unavailable": True}
        for property_item in database["properties"]:
            if (property_item["type"] == "rollup"
                    and str(property_item["config"].get("relation_property_id") or "")
                    in denied_property_ids):
                denied_property_ids.add(str(property_item["id"]))
                property_item["config"] = {"unavailable": True}
        _redact_inaccessible_relation_settings(database, denied_property_ids)
        paired_edit_denied: set[str] = set()
        for property_item in database["properties"]:
            config = property_item["config"]
            if (property_item["type"] != "relation"
                    or not config.get("reciprocal_property_id")):
                continue
            role = target_role(str(config.get("target_database_id") or ""))
            if PAGE_ROLE_LEVEL.get(role or "", 0) < PAGE_ROLE_LEVEL["edit_content"]:
                paired_edit_denied.add(str(property_item["id"]))
        for template in database["templates"]:
            defaults = template.get("default_values")
            if isinstance(defaults, dict) and paired_edit_denied.intersection(defaults):
                template["default_unavailable"] = True
        if not parent_page_access:
            for template in database["templates"]:
                if _template_body_has_unscoped_reference(template.get("body", [])):
                    template["body"] = []
                    template["body_unavailable"] = True
        database["effective_role"] = access.role
        if database_activity.installed(con):
            activity = con.execute(
                "SELECT last_editor_subject_id,last_edited_at,document_sequence "
                "FROM workspace_collaboration_database_activity WHERE database_id=?",
                (access.database_id,),
            ).fetchone()
            database["last_edit"] = _database_last_edit(activity)
        return {
            "contract": CONTRACT,
            "workspace_id": access.workspace_id,
            "database_id": access.database_id,
            "role": access.role,
            "workspace_epoch": access.workspace_epoch,
            "document_epoch": access.document_epoch,
            "database_epoch": access.database_epoch,
            "document_sequence": int(document["document_sequence"]),
            "database": database,
        }


def change_cells(authority, actor_subject_id: str, workspace_id: str,
                 database_id: str, *, cells: list[dict],
                 expected_document_sequence: int,
                 expected_workspace_epoch: int,
                 expected_document_epoch: int,
                 expected_database_epoch: int,
                 operation_id: str, fault: str | None = None,
                 _linked_scope=None) -> dict:
    """Stage one DB-scoped cell batch without disclosing its owner page.

    Existing database attachments and external links may be edited. Attaching
    a new file still requires the separate database-scoped upload flow.
    """
    from .database_features import _clean_object
    from .editing_authority import DocumentSequenceConflict

    actor = authority._require_active(actor_subject_id)
    workspace = _identifier(workspace_id, field="workspace_id")
    database = _identifier(database_id, field="database_id")
    sequence = _expected_epoch(expected_document_sequence, field="document_sequence")
    workspace_epoch = _expected_epoch(expected_workspace_epoch, field="workspace_epoch")
    document_epoch = _expected_epoch(expected_document_epoch, field="document_epoch")
    database_epoch = _expected_epoch(expected_database_epoch, field="database_epoch")
    operation = _operation_id(operation_id)
    clean_payload = _clean_object({"cells": cells}, field="database cell batch")
    if not isinstance(cells, list) or not 1 <= len(cells) <= 1000:
        raise ValueError("database cell batch requires 1 to 1000 cells")
    clean_cells = []
    seen = set()
    for cell in clean_payload["cells"]:
        if not isinstance(cell, dict) or set(cell) != {
                "row_id", "property_id", "value", "expected_target_version"}:
            raise ValueError("database cell entry fields do not match the contract")
        row_id = _identifier(cell["row_id"], field="row_id")
        property_id = _identifier(cell["property_id"], field="property_id")
        version = cell["expected_target_version"]
        if isinstance(version, bool) or not isinstance(version, int) or version < 0:
            raise ValueError("database cell version must be non-negative")
        if (row_id, property_id) in seen:
            raise ValueError("database cell targets must be unique")
        seen.add((row_id, property_id))
        clean_cells.append({**cell, "row_id": row_id, "property_id": property_id})
    action = "database.cells_set"
    request = {
        "workspace_id": workspace, "database_id": database,
        "cells": clean_cells, "expected_document_sequence": sequence,
        "expected_workspace_epoch": workspace_epoch,
        "expected_document_epoch": document_epoch,
        "expected_database_epoch": database_epoch,
    }
    if _linked_scope is not None:
        request["linked_instance"] = _linked_scope.request_context()
    digest = _request_sha256(actor, action, request)
    with authority._transaction() as con:
        def authorize(*, replay: bool):
            access = require_write_on_connection(
                authority, con, actor, workspace, database,
                minimum_role="edit_content",
                expected_workspace_epoch=workspace_epoch,
                expected_document_epoch=document_epoch,
                expected_database_epoch=database_epoch,
            )
            if _linked_scope is not None:
                _linked_scope.authorize(authority, con, actor, access,
                                        replay=replay)
            return access

        replay = authority._replay(
            con, actor=actor, action=action, operation_id=operation,
            request_sha256=digest, authorize=lambda: authorize(replay=True),
        )
        if replay is not None:
            return {**replay, "replayed": True}
        access = authorize(replay=False)
        document = authority._document(con, access.document_id)
        _require_unlocked_or_temporary(con, document, actor)
        if int(document["document_sequence"]) != sequence:
            raise DocumentSequenceConflict(int(document["document_sequence"]))
        next_sequence, now = sequence + 1, int(time.time())
        changed = []
        reciprocal_documents: dict[str, set[str]] = {}
        visible_files = _visible_file_ids_on_connection(
            con, database, actor_subject_id=actor)

        def require_target(target_id: str, minimum_role: str) -> None:
            target = resolve_on_connection(
                authority, con, actor, workspace, target_id,
            )
            if PAGE_ROLE_LEVEL[target.role] < PAGE_ROLE_LEVEL[minimum_role]:
                raise NotFoundOrDenied("not found or permission denied")

        for cell in clean_cells:
            bound = con.execute(
                "SELECT property.property_type FROM workspace_collaboration_database_rows row_item "
                "JOIN workspace_collaboration_database_properties property "
                "ON property.database_id=row_item.database_id "
                "WHERE row_item.database_id=? AND row_item.id=? AND property.id=?",
                (database, cell["row_id"], cell["property_id"]),
            ).fetchone()
            if bound is None:
                raise NotFoundOrDenied("not found or permission denied")
            if str(bound["property_type"]) == "files" and isinstance(cell["value"], list):
                for item in cell["value"]:
                    if isinstance(item, dict) and "file_id" in item:
                        if item["file_id"] not in visible_files:
                            raise NotFoundOrDenied("not found or permission denied")
            cell_result = authority._apply_typed_domain(
                con, actor_subject_id=actor, document_id=access.document_id,
                command_id=_stable_id("dbcell", actor, database, operation,
                                      cell["row_id"], cell["property_id"]),
                command_kind="database.cell.set",
                target_id=f"{cell['row_id']}:{cell['property_id']}",
                payload={key: cell[key] for key in ("row_id", "property_id", "value")},
                expected_target_version=cell["expected_target_version"],
                document_sequence=next_sequence, created_at=now,
                target_database_access=require_target,
            )
            reciprocal_document = cell_result.pop("_reciprocal_document_id", None)
            reciprocal_database = cell_result.pop("_reciprocal_database_id", None)
            if reciprocal_document is not None:
                reciprocal_documents.setdefault(str(reciprocal_document), set()).add(
                    str(reciprocal_database))
            changed.append(cell_result)
        checksum = _sha256(_canonical_json(
            authority._editing_snapshot(con, authority._document(con, access.document_id))))
        con.execute(
            "UPDATE workspace_collaboration_documents SET document_sequence=?,"
            "content_sha256=?,row_version=row_version+1,updated_at=? WHERE id=?",
            (next_sequence, checksum, now, access.document_id),
        )
        if reciprocal_documents:
            from .database_relation_pair import commit_reciprocal_documents
            commit_reciprocal_documents(
                authority, con, actor=actor, source_document_id=access.document_id,
                operation_id=operation, affected=reciprocal_documents, now=now,
                effect_kind="database_scoped_batch",
            )
        result = {
            "contract": CONTRACT, "workspace_id": workspace,
            "database_id": database, "effective_role": access.role,
            "database_epoch": access.database_epoch,
            "document_sequence": next_sequence, "cells": changed,
        }
        receipt = authority._finish(
            con, actor=actor, action=action, operation_id=operation,
            request_sha256=digest, workspace_id=workspace,
            document_id=access.document_id,
            workspace_epoch=workspace_epoch,
            document_epoch=document_epoch,
            document_sequence=next_sequence,
            event_type="database.surface.committed", event_payload=result,
            result=result, fault=fault,
        )
        return {**receipt, "replayed": False}


def create_blank_row(authority, actor_subject_id: str, workspace_id: str,
                     database_id: str, *, title: str,
                     use_default_template: bool = True,
                     template_id: str | None = None,
                     expected_document_sequence: int,
                     expected_workspace_epoch: int,
                     expected_document_epoch: int,
                     expected_database_epoch: int,
                     operation_id: str, fault: str | None = None,
                     _linked_scope=None) -> dict:
    """Create an item under current source and optional linked-instance grants."""
    from .editing_authority import DocumentSequenceConflict
    from . import database_activity, database_template_default
    from .database_move import _file_ids

    actor = authority._require_active(actor_subject_id)
    workspace = _identifier(workspace_id, field="workspace_id")
    database = _identifier(database_id, field="database_id")
    sequence = _expected_epoch(expected_document_sequence, field="document_sequence")
    workspace_epoch = _expected_epoch(expected_workspace_epoch, field="workspace_epoch")
    document_epoch = _expected_epoch(expected_document_epoch, field="document_epoch")
    database_epoch = _expected_epoch(expected_database_epoch, field="database_epoch")
    operation = _operation_id(operation_id)
    if not isinstance(title, str) or len(title) > 1000:
        raise ValueError("database item title must be text of at most 1000 characters")
    if not isinstance(use_default_template, bool):
        raise ValueError("use_default_template must be a boolean")
    if template_id is not None:
        template_id = _identifier(template_id, field="template_id")
        if not use_default_template:
            raise ValueError("explicit template conflicts with blank-item choice")
    request = {"workspace_id": workspace, "database_id": database,
               "title": title, "expected_document_sequence": sequence,
               "expected_workspace_epoch": workspace_epoch,
               "expected_document_epoch": document_epoch,
               "expected_database_epoch": database_epoch}
    if not use_default_template:
        request["use_default_template"] = False
    if template_id is not None:
        request["template_id"] = template_id
    if _linked_scope is not None:
        request["linked_instance"] = _linked_scope.request_context()
    action = "database.row_create_blank"
    digest = _request_sha256(actor, action, request)
    row_id = _stable_id("dbrow", actor, database, operation)
    with authority._transaction() as con:
        def authorize(*, replay: bool):
            access = require_write_on_connection(
                authority, con, actor, workspace, database,
                minimum_role="edit_content",
                expected_workspace_epoch=workspace_epoch,
                expected_document_epoch=document_epoch,
                expected_database_epoch=database_epoch,
            )
            if _linked_scope is not None:
                _linked_scope.authorize(authority, con, actor, access,
                                        replay=replay)
            return access

        replay = authority._replay(
            con, actor=actor, action=action, operation_id=operation,
            request_sha256=digest, authorize=lambda: authorize(replay=True),
        )
        if replay is not None:
            return {**replay, "replayed": True}
        access = authorize(replay=False)
        document = authority._document(con, access.document_id)
        _require_unlocked_or_temporary(con, document, actor)
        if int(document["document_sequence"]) != sequence:
            raise DocumentSequenceConflict(int(document["document_sequence"]))
        title_row = con.execute(
            "SELECT id FROM workspace_collaboration_database_properties "
            "WHERE database_id=? AND property_type='title' ORDER BY position,id LIMIT 1",
            (database,),
        ).fetchone()
        if title_row is None:
            raise AuthorityUnavailable("database title property is missing")
        title_id = str(title_row["id"])
        selected_template = (template_id if template_id is not None else
                             database_template_default.read(con, database)["template_id"]
                             if use_default_template else None)
        if selected_template is not None:
            template = con.execute(
                "SELECT body_json FROM workspace_collaboration_database_templates "
                "WHERE database_id=? AND id=? AND state='active'",
                (database, selected_template),
            ).fetchone()
            if template is None:
                raise NotFoundOrDenied("not found or permission denied")
            body = json.loads(str(template["body_json"]))
            # A direct table grant does not confer access to page or cross-table
            # embeds carried by a template body.
            if _template_body_has_unscoped_reference(body):
                raise NotFoundOrDenied("not found or permission denied")
            for file_id in _file_ids(body):
                file = con.execute(
                    "SELECT 1 FROM workspace_collaboration_files "
                    "WHERE id=? AND document_id=? AND state='active'",
                    (file_id, access.document_id),
                ).fetchone()
                if file is None:
                    raise NotFoundOrDenied("not found or permission denied")

        def require_target(target_id: str, minimum_role: str) -> None:
            target = resolve_on_connection(
                authority, con, actor, workspace, target_id,
            )
            if PAGE_ROLE_LEVEL[target.role] < PAGE_ROLE_LEVEL[minimum_role]:
                raise NotFoundOrDenied("not found or permission denied")

        next_sequence, now = sequence + 1, int(time.time())
        created = authority._apply_surface_domain(
            con, document_id=access.document_id, database_id=database,
            command_kind="database.row.create", target_id=row_id,
            payload=({"template_id": template_id} if template_id is not None else
                     {} if use_default_template else {"template_id": None}),
            expected_target_version=0,
            created_at=now, actor=actor, target_database_access=require_target,
        )
        reciprocal_documents = created.pop("_reciprocal_documents", {})
        title_default = title_id in created.get("applied_defaults", {})
        if title or not title_default:
            cell = authority._apply_typed_domain(
                con, actor_subject_id=actor, document_id=access.document_id,
                command_id=_stable_id("dbcell", actor, database, operation, row_id,
                                      title_id),
                command_kind="database.cell.set", target_id=f"{row_id}:{title_id}",
                payload={"row_id": row_id, "property_id": title_id, "value": title},
                expected_target_version=1 if title_default else 0,
                document_sequence=next_sequence, created_at=now,
            )
            title_version = cell["target_version"]
        else:
            title_version = 1
        checksum = _sha256(_canonical_json(
            authority._editing_snapshot(con, authority._document(con, access.document_id))))
        con.execute(
            "UPDATE workspace_collaboration_documents SET document_sequence=?,"
            "content_sha256=?,row_version=row_version+1,updated_at=? WHERE id=?",
            (next_sequence, checksum, now, access.document_id),
        )
        if database_activity.installed(con):
            database_activity.touch(
                con, database_ids=(database,), document_id=access.document_id,
                actor_subject_id=actor, edited_at=now,
                document_sequence=next_sequence,
            )
        if reciprocal_documents:
            from .database_relation_pair import commit_reciprocal_documents
            commit_reciprocal_documents(
                authority, con, actor=actor, source_document_id=access.document_id,
                operation_id=operation, affected=reciprocal_documents, now=now,
                effect_kind="database_scoped_row_create",
            )
        result = {"contract": CONTRACT, "workspace_id": workspace,
                  "database_id": database, "row_id": row_id,
                  "command_kind": "database.row.create",
                  "effective_role": access.role,
                  "database_epoch": access.database_epoch,
                  "document_sequence": next_sequence,
                  "title_cell_version": title_version,
                  **({"linked_instance": {"document_id": _linked_scope.instance_document,
                                           "block_id": _linked_scope.block}}
                     if _linked_scope is not None else {}),
                  **({"template_id": created["template_id"]}
                     if "template_id" in created else {})}
        receipt = authority._finish(
            con, actor=actor, action=action, operation_id=operation,
            request_sha256=digest, workspace_id=workspace,
            document_id=access.document_id,
            workspace_epoch=workspace_epoch,
            document_epoch=document_epoch,
            document_sequence=next_sequence,
            event_type="database.surface.committed", event_payload=result,
            result=result, fault=fault,
        )
        return {**receipt, "replayed": False}


def change_row_body(authority, actor_subject_id: str, workspace_id: str,
                    database_id: str, row_id: str, *, blocks: list[dict],
                    expected_target_version: int,
                    expected_document_sequence: int,
                    expected_workspace_epoch: int,
                    expected_document_epoch: int,
                    expected_database_epoch: int,
                    operation_id: str, fault: str | None = None) -> dict:
    """Save one authorized database item's body without parent-page authority."""
    from . import database_row_content
    from .database_move import _file_ids, _has_child_page
    from .editing_authority import DocumentSequenceConflict

    actor = authority._require_active(actor_subject_id)
    workspace = _identifier(workspace_id, field="workspace_id")
    database = _identifier(database_id, field="database_id")
    row = _identifier(row_id, field="row_id")
    version = _expected_epoch(expected_target_version, field="target_version")
    sequence = _expected_epoch(expected_document_sequence, field="document_sequence")
    workspace_epoch = _expected_epoch(expected_workspace_epoch, field="workspace_epoch")
    document_epoch = _expected_epoch(expected_document_epoch, field="document_epoch")
    database_epoch = _expected_epoch(expected_database_epoch, field="database_epoch")
    operation = _operation_id(operation_id)
    clean_blocks = database_row_content.clean_blocks(blocks)
    if _has_child_page(clean_blocks):
        raise ValueError("database-only item body cannot create child pages")
    request = {
        "workspace_id": workspace, "database_id": database, "row_id": row,
        "blocks": clean_blocks, "expected_target_version": version,
        "expected_document_sequence": sequence,
        "expected_workspace_epoch": workspace_epoch,
        "expected_document_epoch": document_epoch,
        "expected_database_epoch": database_epoch,
    }
    action = "database.row_body_set"
    digest = _request_sha256(actor, action, request)
    with authority._transaction() as con:
        def authorize():
            return require_write_on_connection(
                authority, con, actor, workspace, database,
                minimum_role="edit_content",
                expected_workspace_epoch=workspace_epoch,
                expected_document_epoch=document_epoch,
                expected_database_epoch=database_epoch,
            )

        replay = authority._replay(
            con, actor=actor, action=action, operation_id=operation,
            request_sha256=digest, authorize=authorize,
        )
        if replay is not None:
            return {**replay, "replayed": True}
        access = authorize()
        document = authority._document(con, access.document_id)
        _require_unlocked_or_temporary(con, document, actor)
        if int(document["document_sequence"]) != sequence:
            raise DocumentSequenceConflict(int(document["document_sequence"]))
        if not any(source["scope"] == "parent_page" for source in access.sources):
            current_body = con.execute(
                "SELECT b.blocks_json FROM workspace_collaboration_database_row_bodies b "
                "JOIN workspace_collaboration_database_rows r ON r.id=b.row_id "
                "WHERE r.database_id=? AND r.id=?", (database, row),
            ).fetchone()
            if (_template_body_has_unscoped_reference(clean_blocks)
                    or current_body is not None and _template_body_has_unscoped_reference(
                        json.loads(str(current_body["blocks_json"])))):
                raise NotFoundOrDenied("not found or permission denied")
        if not _file_ids(clean_blocks).issubset(_visible_file_ids_on_connection(
                con, database, actor_subject_id=actor)):
            raise NotFoundOrDenied("not found or permission denied")
        next_sequence, now = sequence + 1, int(time.time())
        changed = authority._apply_surface_domain(
            con, document_id=access.document_id, database_id=database,
            command_kind="database.row.content.set", target_id=row,
            payload={"blocks": clean_blocks}, expected_target_version=version,
            created_at=now, actor=actor,
        )
        checksum = _sha256(_canonical_json(
            authority._editing_snapshot(con, authority._document(con, access.document_id))))
        con.execute(
            "UPDATE workspace_collaboration_documents SET document_sequence=?,"
            "content_sha256=?,row_version=row_version+1,updated_at=? WHERE id=?",
            (next_sequence, checksum, now, access.document_id),
        )
        result = {
            "contract": CONTRACT, "workspace_id": workspace,
            "database_id": database, "row_id": row,
            "target_version": changed["target_version"],
            "document_sequence": next_sequence, "body": changed["body"],
        }
        receipt = authority._finish(
            con, actor=actor, action=action, operation_id=operation,
            request_sha256=digest, workspace_id=workspace,
            document_id=access.document_id,
            workspace_epoch=workspace_epoch,
            document_epoch=document_epoch,
            document_sequence=next_sequence,
            event_type="database.surface.committed",
            event_payload={key: result[key] for key in (
                "contract", "workspace_id", "database_id", "row_id",
                "target_version", "document_sequence")},
            result=result, fault=fault,
        )
        return {**receipt, "replayed": False}


def _require_sharer(authority, con, actor, workspace, database) -> DatabaseAccess:
    current = resolve_on_connection(authority, con, actor, workspace, database)
    if current.role != "full_access":
        raise NotFoundOrDenied("not found or permission denied")
    return current


def _validate_principal(authority, con, actor, workspace, kind, principal, now):
    if kind == "subject":
        authority._require_active(principal)
        member = con.execute(
            "SELECT 1 FROM workspace_memberships WHERE workspace_id=? "
            "AND subject_id=? AND revoked_at IS NULL", (workspace, principal),
        ).fetchone()
        if member is None:
            guest = con.execute(
                "SELECT guest_epoch FROM workspace_guests WHERE workspace_id=? AND subject_id=?",
                (workspace, principal),
            ).fetchone()
            epoch = int(guest["guest_epoch"]) + 1 if guest else 1
            con.execute(
                "INSERT INTO workspace_guests(workspace_id,subject_id,guest_epoch,"
                "invited_by_subject_id,created_at,updated_at,revoked_at) "
                "VALUES(?,?,?,?,?,?,NULL) ON CONFLICT(workspace_id,subject_id) "
                "DO UPDATE SET guest_epoch=excluded.guest_epoch,"
                "invited_by_subject_id=excluded.invited_by_subject_id,"
                "updated_at=excluded.updated_at,revoked_at=NULL",
                (workspace, principal, epoch, actor, now, now),
            )
        return
    if kind == "group":
        valid = con.execute(
            "SELECT 1 FROM workspace_groups WHERE workspace_id=? AND id=?",
            (workspace, principal),
        ).fetchone()
    else:
        valid = con.execute(
            "SELECT 1 FROM teamspaces WHERE workspace_id=? AND id=? "
            "AND archived_at IS NULL", (workspace, principal),
        ).fetchone()
    if valid is None:
        raise NotFoundOrDenied("not found or permission denied")


def _bump_epoch(con, database, current, now):
    next_epoch = current + 1
    con.execute(
        "INSERT INTO workspace_collaboration_database_access_state("
        "database_id,access_epoch,updated_at) VALUES(?,?,?) "
        "ON CONFLICT(database_id) DO UPDATE SET "
        "access_epoch=excluded.access_epoch,updated_at=excluded.updated_at",
        (database, next_epoch, now),
    )
    return next_epoch


def revoke_subject_grants_on_member_revoke(con, workspace, subject, now) -> list[str]:
    """Join the workspace-member revocation transaction, if 2428 is installed."""
    if not installed(con):
        return []
    rows = con.execute(
        "SELECT grant.database_id FROM workspace_collaboration_database_access_grants grant "
        "JOIN workspace_collaboration_databases db ON db.id=grant.database_id "
        "JOIN workspace_collaboration_documents doc ON doc.id=db.document_id "
        "WHERE doc.workspace_id=? AND grant.principal_type='subject' "
        "AND grant.principal_id=? AND grant.active=1 ORDER BY grant.database_id",
        (workspace, subject),
    ).fetchall()
    database_ids = [str(row["database_id"]) for row in rows]
    if not database_ids:
        return []
    con.execute(
        "UPDATE workspace_collaboration_database_access_grants SET "
        "active=0,grant_epoch=grant_epoch+1,revoked_at=?,updated_at=? "
        "WHERE principal_type='subject' AND principal_id=? AND active=1 "
        "AND database_id IN (SELECT db.id FROM workspace_collaboration_databases db "
        "JOIN workspace_collaboration_documents doc ON doc.id=db.document_id "
        "WHERE doc.workspace_id=?)",
        (now, now, subject, workspace),
    )
    for database in database_ids:
        state = con.execute(
            "SELECT access_epoch FROM workspace_collaboration_database_access_state "
            "WHERE database_id=?", (database,),
        ).fetchone()
        _bump_epoch(con, database, int(state["access_epoch"]) if state else 1, now)
    return database_ids


def has_active_subject_grant(con, workspace, subject) -> bool:
    """Keep a guest active while any staged database grant still names them."""
    if not installed(con):
        return False
    return con.execute(
        "SELECT 1 FROM workspace_collaboration_database_access_grants grant "
        "JOIN workspace_collaboration_databases db ON db.id=grant.database_id "
        "JOIN workspace_collaboration_documents doc ON doc.id=db.document_id "
        "WHERE doc.workspace_id=? AND grant.principal_type='subject' "
        "AND grant.principal_id=? AND grant.active=1 LIMIT 1",
        (workspace, subject),
    ).fetchone() is not None


def _retire_orphan_guest(con, workspace, subject, now) -> None:
    if has_active_subject_grant(con, workspace, subject):
        return
    if con.execute(
        "SELECT 1 FROM workspace_memberships WHERE workspace_id=? "
        "AND subject_id=? AND revoked_at IS NULL LIMIT 1",
        (workspace, subject),
    ).fetchone() is not None:
        return
    if con.execute(
        "SELECT 1 FROM workspace_collaboration_access_grants "
        "WHERE workspace_id=? AND principal_type='subject' "
        "AND principal_id=? AND active=1 LIMIT 1",
        (workspace, subject),
    ).fetchone() is not None:
        return
    if con.execute(
        "SELECT 1 FROM workspace_collaboration_page_grants legacy "
        "JOIN workspace_collaboration_documents doc ON doc.id=legacy.document_id "
        "WHERE doc.workspace_id=? AND legacy.subject_id=? "
        "AND legacy.active=1 LIMIT 1",
        (workspace, subject),
    ).fetchone() is not None:
        return
    con.execute(
        "UPDATE workspace_guests SET revoked_at=?,updated_at=?,"
        "guest_epoch=guest_epoch+1 WHERE workspace_id=? AND subject_id=? "
        "AND revoked_at IS NULL",
        (now, now, workspace, subject),
    )


def change_grant(authority, actor_subject_id: str, workspace_id: str,
                 database_id: str, principal_type: str, principal_id: str, *,
                 role: str | None, expected_workspace_epoch: int,
                 expected_document_epoch: int, expected_database_epoch: int,
                 operation_id: str, fault: str | None = None) -> dict:
    """Set or revoke one direct database grant with one canonical receipt.

    ``role=None`` revokes. No HTTP route calls this until every downstream
    database reader and writer applies the same database-scoped decision.
    """
    actor = authority._require_active(actor_subject_id)
    workspace = _identifier(workspace_id, field="workspace_id")
    database = _identifier(database_id, field="database_id")
    kind = _principal_type(principal_type)
    principal = _identifier(principal_id, field="principal_id")
    selected_role = None if role is None else _page_role(role)
    expected_workspace = _expected_epoch(expected_workspace_epoch, field="workspace_epoch")
    expected_document = _expected_epoch(expected_document_epoch, field="document_epoch")
    expected_database = _expected_epoch(expected_database_epoch, field="database_epoch")
    operation = _operation_id(operation_id)
    action = "database.grant_revoke" if selected_role is None else "database.grant_set"
    request_hash = _request_sha256(actor, action, {
        "workspace_id": workspace, "database_id": database,
        "principal_type": kind, "principal_id": principal, "role": selected_role,
        "expected_workspace_epoch": expected_workspace,
        "expected_document_epoch": expected_document,
        "expected_database_epoch": expected_database,
    })
    with authority._transaction() as con:
        current = _require_sharer(authority, con, actor, workspace, database)
        previous = authority._replay(
            con, actor=actor, action=action, operation_id=operation,
            request_sha256=request_hash,
            authorize=lambda: _require_sharer(authority, con, actor, workspace, database),
        )
        if previous is not None:
            return {**previous, "replayed": True}
        current = require_write_on_connection(
            authority, con, actor, workspace, database,
            minimum_role="full_access",
            expected_workspace_epoch=expected_workspace,
            expected_document_epoch=expected_document,
            expected_database_epoch=expected_database,
        )
        now = int(time.time())
        if selected_role is not None:
            _validate_principal(authority, con, actor, workspace, kind, principal, now)
            old = con.execute(
                "SELECT grant_epoch,created_at FROM workspace_collaboration_database_access_grants "
                "WHERE database_id=? AND principal_type=? AND principal_id=?",
                (database, kind, principal),
            ).fetchone()
            grant_epoch = int(old["grant_epoch"]) + 1 if old else 1
            created_at = int(old["created_at"]) if old else now
            con.execute(
                "INSERT INTO workspace_collaboration_database_access_grants("
                "database_id,principal_type,principal_id,role,active,grant_epoch,"
                "granted_by_subject_id,created_at,updated_at,revoked_at) "
                "VALUES(?,?,?,?,1,?,?,?,?,NULL) "
                "ON CONFLICT(database_id,principal_type,principal_id) DO UPDATE SET "
                "role=excluded.role,active=1,grant_epoch=excluded.grant_epoch,"
                "granted_by_subject_id=excluded.granted_by_subject_id,"
                "updated_at=excluded.updated_at,revoked_at=NULL",
                (database, kind, principal, selected_role, grant_epoch, actor, created_at, now),
            )
        else:
            old = con.execute(
                "SELECT grant_epoch FROM workspace_collaboration_database_access_grants "
                "WHERE database_id=? AND principal_type=? AND principal_id=? AND active=1",
                (database, kind, principal),
            ).fetchone()
            if old is None:
                raise NotFoundOrDenied("not found or permission denied")
            grant_epoch = int(old["grant_epoch"]) + 1
            con.execute(
                "UPDATE workspace_collaboration_database_access_grants SET "
                "active=0,grant_epoch=?,revoked_at=?,updated_at=? "
                "WHERE database_id=? AND principal_type=? AND principal_id=? AND active=1",
                (grant_epoch, now, now, database, kind, principal),
            )
            if kind == "subject":
                _retire_orphan_guest(con, workspace, principal, now)
        next_epoch = _bump_epoch(con, database, current.database_epoch, now)
        authority._require_active(actor)
        if selected_role is not None and kind == "subject":
            authority._require_active(principal)
        result = {
            "contract": CONTRACT, "workspace_id": workspace,
            "document_id": current.document_id, "database_id": database,
            "principal_type": kind, "principal_id": principal,
            "role": selected_role, "grant_epoch": grant_epoch,
            "workspace_epoch": current.workspace_epoch,
            "document_epoch": current.document_epoch,
            "database_epoch": next_epoch,
        }
        receipt = authority._finish(
            con, actor=actor, action=action, operation_id=operation,
            request_sha256=request_hash, workspace_id=workspace,
            document_id=current.document_id,
            workspace_epoch=current.workspace_epoch,
            document_epoch=current.document_epoch,
            document_sequence=None, event_type=action, event_payload={
                "database_id": database, "principal_type": kind,
                "principal_id": principal, "role": selected_role,
                "database_epoch": next_epoch,
            }, result=result, fault=fault,
        )
        return {**receipt, "replayed": False}
