"""Create a native page, initial history and current parent link atomically."""
from __future__ import annotations

import time

from code_assist_v1 import notion_workspace_native_page_migration as migration
from code_assist_v1 import notion_workspace_member_navigation_migration as navigation_migration
from code_assist_v1 import notion_workspace_file_copy_migration as file_copy_migration
from code_assist_v1 import notion_workspace_native_team_migration as team_migration
from code_assist_v1 import notion_workspace_row_body_migration as row_body_migration
from code_assist_v1 import notion_workspace_row_appearance_migration as row_appearance_migration
from code_assist_v1 import notion_workspace_same_document_file_copy_migration as same_file_migration
from code_assist_v1 import notion_workspace_database_activity_migration as activity_migration
from code_assist_v1 import notion_workspace_database_trash_migration as trash_migration
from code_assist_v1 import notion_workspace_database_row_trash_migration as row_trash_migration
from code_assist_v1 import notion_workspace_database_row_page_placement_migration as placement_migration
from code_assist_v1 import notion_workspace_custom_emoji_migration as custom_emoji_migration
from code_assist_v1 import notion_workspace_database_item_page_layout_migration as item_layout_migration
from code_assist_v1 import notion_workspace_database_template_default_migration as template_default_migration
from code_assist_v1 import notion_workspace_database_sharing_migration as database_sharing_migration
from code_assist_v1 import notion_workspace_database_upload_scope_migration as database_upload_scope_migration
from code_assist_v1 import notion_workspace_direct_database_favorite_migration as direct_favorite_migration
from code_assist_v1.notion_workspace_store import WorkspaceRevisionConflict, WorkspaceStore
from .canonical_authority import (
    AuthorityUnavailable, NotFoundOrDenied, _canonical_json, _clean_blocks, _content_descriptor,
    _identifier, _operation_id, _positive, _request_sha256, _sha256, _stable_id,
)
from .editing_authority import DocumentSequenceConflict
from .page_appearance import _metadata
from . import database_access, database_activity, database_trash, linked_databases, page_move, native_team_location as team_location, page_templates
from .title_projection import _frames, _generation
from . import page_creation_parent_rebase as parent_rebase

CONTRACT = "code_assist.notion.native_page_creation.v1"
ACTION = "collaboration.native_page_create"


def marker_current(con):
    row = con.execute("SELECT version,name,checksum,actual_schema_sha256 FROM workspace_schema_migrations ORDER BY version DESC LIMIT 1").fetchone()
    return row is not None and tuple(row) in {
        (item.TARGET_VERSION, item.TARGET_NAME, item.TARGET_CHECKSUM, item.TARGET_ACTUAL_SCHEMA_SHA256)
        for item in (migration, navigation_migration, file_copy_migration, team_migration, row_body_migration, row_appearance_migration, same_file_migration, activity_migration, trash_migration, row_trash_migration, placement_migration, custom_emoji_migration, item_layout_migration, template_default_migration, database_sharing_migration, database_upload_scope_migration, direct_favorite_migration)
    }


def _owner(authority, con, actor, workspace_id):
    authority._require_active(actor)
    if not marker_current(con):
        raise AuthorityUnavailable("native page creation is not installed")
    workspace = authority._workspace(con, workspace_id)
    if authority._workspace_role(con, workspace_id, actor) != "owner":
        raise NotFoundOrDenied("not found or permission denied")
    return workspace


def native_page_items(authority, actor, workspace_id):
    """Discover only native registry entries; no adoption of unbound old sources."""
    actor = authority._require_active(actor)
    with authority._reader() as con:
        con.execute("BEGIN")
        if not marker_current(con):
            return []
        authority._workspace(con, workspace_id)
        rows = con.execute(
            "SELECT d.id,d.parent_document_id,d.row_version FROM workspace_collaboration_native_pages n "
            "JOIN workspace_collaboration_documents d ON d.id=n.document_id AND d.workspace_id=n.workspace_id "
            "WHERE n.workspace_id=? AND d.archived=0 ORDER BY d.position,d.created_at,d.id", (workspace_id,)
        ).fetchall()
        items = []
        for row in rows:
            role, _ = authority._resolution_on_connection(con, actor, workspace_id, row["id"])
            if role is None:
                continue
            team_root = team_location.inherited_root(con, workspace_id, row["id"])
            if team_root is not None:
                items.append({"id":row["id"], "workspace_id":workspace_id, "placement": {
                    "scope":"teamspace", "teamspace_id":team_root["teamspace_id"], "binding_version":row["row_version"]}})
                continue
            # Derive placement from the current ancestor chain, so moving a
            # native page never leaves behind a second stale placement record.
            cursor, seen, placement = row["parent_document_id"], {row["id"]}, None
            while cursor:
                if cursor in seen:
                    raise AuthorityUnavailable("cyclic native page hierarchy")
                seen.add(cursor)
                if len(seen) > 100:
                    raise AuthorityUnavailable("native page hierarchy exceeds the navigation depth")
                placement = con.execute(
                    "SELECT placement,teamspace_id,row_version FROM workspace_page_sources "
                    "WHERE workspace_id=? AND source_page_id=? AND state='active'", (workspace_id, cursor)
                ).fetchone()
                if placement is not None:
                    break
                ancestor = authority._document(con, cursor)
                if ancestor["workspace_id"] != workspace_id:
                    raise AuthorityUnavailable("native page ancestor workspace mismatch")
                cursor = ancestor["parent_document_id"]
            items.append({"id": row["id"], "workspace_id": workspace_id, "placement": {
                "scope": placement["placement"] if placement else "private",
                "teamspace_id": str(placement["teamspace_id"] or "") if placement else "",
                "binding_version": int(placement["row_version"]) if placement else 1,
            }})
        authority._require_active(actor)
        return items


def create_page(authority, actor_subject_id, workspace_id, *, title, parent_id="", teamspace_id="", template="page",
                source_document_id="", source_database_id="", base_version, operation_id, fault=None):
    actor = authority._require_active(actor_subject_id)
    workspace_id = _identifier(workspace_id, field="workspace_id")
    operation = _operation_id(operation_id)
    version = _positive(base_version, field="base_version")
    template = page_templates.validate(template)
    if template == "linked_database":
        if not isinstance(source_document_id, str) or not isinstance(source_database_id, str) or not source_database_id:
            raise ValueError("linked database source required")
        if source_document_id:
            source_document_id = _identifier(source_document_id, field="source_document_id")
        source_database_id = _identifier(source_database_id, field="source_database_id")
        if source_document_id != source_document_id.strip() or source_database_id != source_database_id.strip():
            raise ValueError("canonical linked database source required")
    elif source_document_id or source_database_id:
        raise ValueError("linked database source requires linked_database template")
    if not isinstance(title, str) or not title.strip() or len(title.strip()) > 160:
        raise ValueError("page title must contain 1 to 160 characters")
    title = title.strip()
    if not isinstance(parent_id, str):
        raise ValueError("parent_id must be a string")
    parent_id = _identifier(parent_id, field="parent_id") if parent_id else None
    if not isinstance(teamspace_id, str):
        raise ValueError("teamspace_id must be a string")
    teamspace_id = _identifier(teamspace_id, field="teamspace_id") if teamspace_id else None
    if teamspace_id and parent_id:
        raise ValueError("choose a parent page or a root teamspace, not both")
    document_id = _stable_id("p", actor, workspace_id, operation, CONTRACT)
    link_operation = _stable_id("native_create_link", actor, workspace_id, operation)
    arguments = {"workspace_id": workspace_id, "title": title, "parent_id": parent_id or "", "base_version": version}
    if teamspace_id:
        arguments["teamspace_id"] = teamspace_id
    if template != "page":
        arguments["template"] = template
    if template == "linked_database":
        if source_document_id:
            arguments["source_document_id"] = source_document_id
        arguments["source_database_id"] = source_database_id
    request_hash = _request_sha256(actor, ACTION, arguments)

    def creator_workspace(con):
        inherited = team_location.inherited_root(con, workspace_id, parent_id) if parent_id else None
        team = teamspace_id or (inherited["teamspace_id"] if inherited else None)
        workspace = team_location.require_creator(authority, con, actor, workspace_id, team) if team else _owner(authority, con, actor, workspace_id)
        return workspace, team

    def require_linked_source(con):
        if template != "linked_database":
            return None
        if database_access.installed(con):
            access = database_access.resolve_on_connection(
                authority, con, actor, workspace_id, source_database_id,
            )
            if source_document_id and access.document_id != source_document_id:
                raise NotFoundOrDenied("not found or permission denied")
            return access.document_id
        if not source_document_id:
            raise ValueError("linked source page required on this schema")
        row = con.execute(
            "SELECT 1 FROM workspace_collaboration_databases db "
            "JOIN workspace_collaboration_documents doc ON doc.id=db.document_id "
            "WHERE db.id=? AND db.document_id=? AND doc.workspace_id=? AND doc.archived=0",
            (source_database_id, source_document_id, workspace_id),
        ).fetchone()
        if (row is None or database_trash.is_trashed(con, source_database_id)
                or authority._resolution_on_connection(con, actor, workspace_id, source_document_id)[0] is None):
            raise NotFoundOrDenied("not found or permission denied")
        return source_document_id

    def replay(con):
        def authorize():
            creator_workspace(con)
            if database_access.installed(con):
                require_linked_source(con)
            page = page_move._authorize(authority, con, actor, workspace_id, document_id, "view")
            if page["legacy_owner_subject_id"] != actor:
                raise NotFoundOrDenied("not found or permission denied")
        return authority._replay(con, actor=actor, action=ACTION, operation_id=operation,
            request_sha256=request_hash, authorize=authorize)

    def authorize(con):
        workspace, team = creator_workspace(con)
        resolved_source_document = require_linked_source(con)
        if workspace["row_version"] != version:
            raise WorkspaceRevisionConflict(workspace["row_version"])
        parent = None
        if parent_id:
            parent = page_move._authorize(authority, con, actor, workspace_id, parent_id, "edit_content")
            if not team and parent["legacy_owner_subject_id"] != actor:
                raise NotFoundOrDenied("not found or permission denied")
            page_move._unlocked(con, parent, actor)
        return workspace, parent, resolved_source_document

    sources = {}
    with authority._reader() as con:
        con.execute("BEGIN")
        previous = replay(con)
        if previous is not None:
            return {**previous, "replayed": True}
        workspace, parent, _resolved_source_document = authorize(con)
        if parent is not None:
            generation = _generation(con, parent_id)
            snapshot = authority._editing_snapshot(con, parent)
            sources[parent_id] = {"id": parent_id, "stamp": page_move._stamp(parent, generation),
                "generation": generation, "frames": _frames(con, parent_id, generation),
                "snapshot": snapshot,
                **parent_rebase.capture_witness(con, parent_id, generation, snapshot),
                "insert": {"id": _stable_id("child_link", actor, operation, document_id),
                    "type": "child_page", "content": title, "props": {"page_id": document_id}}}
    deltas = page_move._prepare_deltas(sources, document_id, link_operation)
    for document, source in sources.items():
        parent_rebase.validate_projection(source, deltas[document])
    with authority._transaction() as con:
        previous = replay(con)
        if previous is not None:
            return {**previous, "replayed": True}
        workspace, parent, resolved_source_document = authorize(con)
        if parent is not None and page_move._stamp(parent, _generation(con, parent_id)) != sources[parent_id]["stamp"]:
            raise DocumentSequenceConflict(parent["document_sequence"])
        if con.execute("SELECT 1 FROM workspace_collaboration_documents WHERE id=?", (document_id,)).fetchone():
            raise AuthorityUnavailable("native creation ID already exists without its receipt")
        now = int(time.time())
        linked_source = (({"document_id": source_document_id, "database_id": source_database_id}
            if source_document_id else {"database_id": source_database_id})
            if template == "linked_database" else None)
        blocks = page_templates.blocks(document_id, title, template, linked_source=linked_source)
        if template == "linked_database":
            if source_document_id:
                linked_databases.source_of(blocks[0])
            else:
                linked_databases._validate_scoped_source(
                    blocks[0], resolved_source_document, source_database_id,
                )
        descriptor = _content_descriptor(title=title, icon="", cover="", settings={}, blocks=blocks)
        checksum = _sha256(_canonical_json(descriptor))
        position = con.execute("SELECT COALESCE(MAX(position),-1)+1 FROM workspace_collaboration_documents WHERE workspace_id=? AND parent_document_id IS ? AND archived=0", (workspace_id, parent_id)).fetchone()[0]
        con.execute(
            "INSERT INTO workspace_collaboration_documents(id,workspace_id,legacy_owner_subject_id,parent_document_id,"
            "title,icon,cover,settings_json,position,archived,archived_at,revision,document_sequence,access_epoch,row_version,"
            "content_sha256,imported_snapshot_id,created_at,updated_at) VALUES(?,?,?,?,?,'','','{}',?,0,NULL,1,1,1,1,?,NULL,?,?)",
            (document_id, workspace_id, actor, parent_id, title, position, checksum, now, now))
        authority._insert_blocks(con, document_id, blocks, now=now)
        if page_templates.install_database(authority, con, document_id, title, template, now, actor):
            descriptor = authority._editing_snapshot(con, authority._document(con, document_id))
            checksum = _sha256(_canonical_json(descriptor))
            con.execute("UPDATE workspace_collaboration_documents SET content_sha256=? WHERE id=?", (checksum,document_id))
            database_activity.touch(
                con, database_ids=(database["id"] for database in descriptor["databases"]),
                document_id=document_id, actor_subject_id=actor, edited_at=now,
                document_sequence=1,
            )
        con.execute("INSERT INTO workspace_collaboration_revisions(document_id,revision,document_sequence,content_sha256,snapshot_json,actor_subject_id,operation_id,created_at) VALUES(?,1,1,?,?,?,?,?)",
            (document_id, checksum, _canonical_json(descriptor), actor, operation, now))
        authority._record_document_creation_successor_state(con, workspace_id=workspace_id, document_id=document_id,
            content_sha256=checksum, actor_subject_id=actor, operation_id=operation, created_at=now)
        con.execute("INSERT INTO workspace_collaboration_native_pages(document_id,workspace_id,creation_kind,source_document_id,created_by_subject_id,operation_id,created_at) VALUES(?,?,'create',NULL,?,?,?)",
            (document_id, workspace_id, actor, operation, now))
        if teamspace_id:
            team_location.replace_root(con, workspace_id, document_id, teamspace_id, actor, now)
        if parent is not None:
            delta, source = deltas[parent_id], sources[parent_id]
            if not delta["inserted"] or not delta["changed"] or delta["removed"]:
                raise AuthorityUnavailable("new page link was not prepared independently")
            page_move._structural_link(authority, con, actor, workspace, parent, "block.insert", delta["placed"],
                {**source["insert"], "after_block_id": delta["after_block_id"]}, link_operation, now)
            page_move._append_link_delta(authority, con, actor, workspace, parent, delta["payload"], source["generation"], link_operation, now)
        # Creating content changes navigation, not existing access grants/tickets.
        con.execute("UPDATE workspaces SET row_version=row_version+1,updated_at=? WHERE id=?", (now, workspace_id))
        workspace = authority._workspace(con, workspace_id)
        page = authority._document(con, document_id)
        result = {"contract": CONTRACT, "page": _metadata(page, workspace, "full_access", actor),
            "workspace": WorkspaceStore._workspace_public(workspace), "parent_link_saved": bool(parent_id),
            "refresh_page_ids": [document_id, *sources]}
        result = authority._finish(con, actor=actor, action=ACTION, operation_id=operation, request_sha256=request_hash,
            workspace_id=workspace_id, document_id=document_id, workspace_epoch=workspace["security_epoch"],
            document_epoch=1, document_sequence=1, event_type="page.created", event_payload=result, result=result, fault=fault)
        return {**result, "replayed": False}
