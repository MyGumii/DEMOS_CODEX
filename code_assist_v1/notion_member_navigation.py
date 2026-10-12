"""Member navigation after the offline cutover, across both page origins."""
from __future__ import annotations

import json
import time

from code_assist_v1 import notion_workspace_store as nws
from code_assist_v1 import notion_workspace_member_navigation_migration as migration
from code_assist_v1 import notion_workspace_file_copy_migration as file_copy_migration
from code_assist_v1 import notion_workspace_native_team_migration as team_migration
from code_assist_v1 import notion_workspace_row_body_migration as row_body_migration
from code_assist_v1 import notion_workspace_row_appearance_migration as row_appearance_migration
from code_assist_v1 import notion_workspace_same_document_file_copy_migration as same_file_migration
from code_assist_v1 import notion_workspace_database_activity_migration as activity_migration
from code_assist_v1 import notion_workspace_database_trash_migration as trash_migration
from code_assist_v1.notion_collaboration.canonical_authority import (
    AuthorityUnavailable, NotFoundOrDenied, _canonical_json, _request_sha256,
)
from code_assist_v1.notion_collaboration import database_favorite_projection


def marker_current(con):
    row = con.execute("SELECT version,name,checksum,actual_schema_sha256 FROM workspace_schema_migrations "
                      "ORDER BY version DESC LIMIT 1").fetchone()
    return row is not None and tuple(row) in {
        (item.TARGET_VERSION, item.TARGET_NAME, item.TARGET_CHECKSUM, item.TARGET_ACTUAL_SCHEMA_SHA256)
        for item in (migration, file_copy_migration, team_migration, row_body_migration, row_appearance_migration, same_file_migration, activity_migration, trash_migration)
    }


def _authorize(store, con, actor, workspace):
    store.iam._require_active(actor)
    if not marker_current(con):
        raise AuthorityUnavailable("member navigation is not installed")
    return int(store._require_member(con, workspace, actor)["membership_epoch"])


def _state(con, actor, workspace):
    row = con.execute("SELECT * FROM workspace_member_navigation_states WHERE workspace_id=? AND subject_id=?",
                      (workspace, actor)).fetchone()
    if row is None:
        return {"workspace_id": workspace, "subject_id": actor, "preference_version": 0, "favorite_version": 0,
            "sidebar": {"width_px": nws.UNCONFIGURED_SIDEBAR_WIDTH_PX, "collapsed": False},
            "sections": nws._default_navigation_sections(), "favorites": []}
    return {"workspace_id": workspace, "subject_id": actor, "preference_version": row["preference_version"],
        "favorite_version": row["favorite_version"],
        "sidebar": {"width_px": nws._visible_sidebar_width(row["preference_version"], row["sidebar_width_px"]),
                    "collapsed": bool(row["sidebar_collapsed"])},
        "sections": nws._normalize_navigation_sections(json.loads(row["sections_json"])),
        "favorites": json.loads(row["favorites_json"])}


def _visible(store, con, actor, workspace, item, *, database_ids=None):
    page_id, owner = item["source_page_id"], item["source_owner_subject_id"]
    page = con.execute("SELECT legacy_owner_subject_id,archived FROM workspace_collaboration_documents "
                       "WHERE workspace_id=? AND id=?", (workspace, page_id)).fetchone()
    if page is None or page["archived"] or page["legacy_owner_subject_id"] != owner:
        return False
    # Existing unbound canonical documents are not silently adopted as pages.
    registered = con.execute("SELECT 1 FROM workspace_collaboration_native_pages WHERE workspace_id=? AND document_id=?",
                             (workspace, page_id)).fetchone()
    if registered is None:
        registered = con.execute("SELECT 1 FROM workspace_page_sources WHERE workspace_id=? AND source_page_id=? "
            "AND source_owner_subject_id=? AND state='active'", (workspace, page_id, owner)).fetchone()
    if registered is None:
        return False
    role, _ = store.iam._resolution_on_connection(con, actor, workspace, page_id)
    if role is None:
        return False
    block_id = item.get("database_block_id")
    if block_id is None:
        return True
    if database_ids is not None:
        return block_id in database_ids
    block = con.execute("SELECT block_type,props_json FROM workspace_collaboration_blocks "
                        "WHERE document_id=? AND id=?", (page_id, block_id)).fetchone()
    return bool(block and block["block_type"] in {"table", "board", "calendar", "list", "gallery", "timeline"}
                and not json.loads(str(block["props_json"])).get("linked_database"))


def read(store, actor_subject_id, workspace_id):
    actor = nws._subject(actor_subject_id)
    workspace = nws._identifier(workspace_id, prefix="ws", field="workspace_id")
    with store.iam._reader() as con:
        con.execute("BEGIN")
        _authorize(store, con, actor, workspace)
        database_pages = {item["source_page_id"] for item in _state(con, actor, workspace)["favorites"]
                          if item.get("database_block_id") and _visible(store, con, actor, workspace,
                              {"source_owner_subject_id": item["source_owner_subject_id"],
                               "source_page_id": item["source_page_id"]})}
    proofs = {page_id: database_favorite_projection.prepare(store, workspace, page_id)
              for page_id in database_pages}
    with store.iam._reader() as con:
        con.execute("BEGIN")
        epoch = _authorize(store, con, actor, workspace)
        result = _state(con, actor, workspace)
        visible = []
        for item in result["favorites"]:
            block_id = item.get("database_block_id")
            if _visible(store, con, actor, workspace, item,
                    database_ids=(proofs.get(item["source_page_id"]) or {"ids": set()})["ids"]
                    if block_id else None):
                visible.append(item)
            elif block_id and _visible(store, con, actor, workspace,
                    {key: item[key] for key in ("source_owner_subject_id", "source_page_id")}):
                archive = database_favorite_projection.archived(con, workspace,
                    item["source_page_id"], block_id)
                if archive is not None:
                    visible.append({**item, "archived_database": archive})
        result["favorites"] = [{**item, "position": position} for position, item in enumerate(visible)]
        result["membership_epoch"] = epoch
        store.iam._require_active(actor)
        return result


def _save(con, state):
    con.execute("INSERT INTO workspace_member_navigation_states(workspace_id,subject_id,preference_version,"
        "favorite_version,sidebar_width_px,sidebar_collapsed,sections_json,favorites_json,updated_at) "
        "VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(workspace_id,subject_id) DO UPDATE SET "
        "preference_version=excluded.preference_version,favorite_version=excluded.favorite_version,"
        "sidebar_width_px=excluded.sidebar_width_px,sidebar_collapsed=excluded.sidebar_collapsed,"
        "sections_json=excluded.sections_json,favorites_json=excluded.favorites_json,updated_at=excluded.updated_at",
        (state["workspace_id"], state["subject_id"], state["preference_version"], state["favorite_version"],
         nws.DEFAULT_SIDEBAR_WIDTH_PX if state["preference_version"] == 0 else state["sidebar"]["width_px"],
         int(state["sidebar"]["collapsed"]), _canonical_json(state["sections"]),
         _canonical_json(state["favorites"]), int(time.time())))


def _legacy_replay(store, con, actor, operation, action, request):
    # A response lost before cutover still belongs to the original immutable
    # receipt. Validate its original audit/outbox rather than reapplying it.
    def validate(connection, previous, result):
        return store._validate_navigation_operation_commitment(connection, previous,
            actor_subject_id=actor, operation_id=operation, expected_action=action,
            expected_workspace_id=request["workspace_id"], expected_subject_id=actor, parsed_result=result)
    return store._replay_from_ledger_on_connection(con, actor_subject_id=actor, operation_id=operation,
        action=action, request_hash=nws._request_hash(actor, action, request), result_validator=validate)


def _mutate(store, actor, workspace, operation, action, request, mutate, *, target=None, fault=None,
            database_proof=None, allow_archived_database=False):
    actor = nws._subject(actor)
    workspace = nws._identifier(workspace, prefix="ws", field="workspace_id")
    operation = nws._operation_id(operation)
    request = {"workspace_id": workspace, "subject_id": actor, **request}
    request_hash = _request_sha256(actor, action, request)
    authority = store.iam
    with authority._transaction() as con:
        def authorize():
            _authorize(store, con, actor, workspace)
            if target is not None and target.get("database_block_id"):
                if database_proof is None:
                    raise NotFoundOrDenied("not found or permission denied")
                if not database_favorite_projection.current(con, workspace, target["source_page_id"], database_proof):
                    raise AuthorityUnavailable("database favorite source changed during navigation write")
            visible = target is None or _visible(store, con, actor, workspace, target,
                database_ids=database_proof["ids"] if database_proof is not None else None)
            if not visible and allow_archived_database and target.get("database_block_id"):
                parent = {key: target[key] for key in ("source_owner_subject_id", "source_page_id")}
                visible = _visible(store, con, actor, workspace, parent) and bool(
                    database_favorite_projection.archived(con, workspace,
                        target["source_page_id"], target["database_block_id"]))
            if not visible:
                raise NotFoundOrDenied("not found or permission denied")
        authorize()
        if action in nws.NAVIGATION_ACTIONS:
            old_replayed, result = _legacy_replay(store, con, actor, operation, action, request)
            if old_replayed:
                return result
        # The previous navigation API has one operation namespace across its
        # three actions. Preserve that contract in the canonical ledger.
        navigation_actions = sorted(nws.NAVIGATION_ACTIONS | {
            nws.NAVIGATION_DATABASE_FAVORITE_ADDED_ACTION,
            nws.NAVIGATION_DATABASE_FAVORITE_REMOVED_ACTION,
        })
        previous = con.execute("SELECT action FROM workspace_collaboration_operations "
            "WHERE actor_subject_id=? AND operation_id=? AND action IN (?,?,?,?,?)",
            (actor, operation, *navigation_actions)).fetchall()
        if any(row["action"] != action for row in previous):
            raise nws.WorkspaceOperationConflict(operation)
        replay = authority._replay(con, actor=actor, action=action, operation_id=operation,
            request_sha256=request_hash, authorize=authorize)
        if replay is not None:
            return replay
        state = _state(con, actor, workspace)
        result = nws._normalize_navigation_operation_result(action, mutate(state))
        _save(con, state)
        authorize()
        # Personal settings and saved page IDs do not enter workspace events.
        return authority._finish(con, actor=actor, action=action, operation_id=operation, request_sha256=request_hash,
            workspace_id=workspace, document_id=None, document_epoch=None, document_sequence=None,
            workspace_epoch=authority._workspace(con, workspace)["security_epoch"], event_type=action,
            event_payload={"subject_id": actor, "preference_version": state["preference_version"],
                "favorite_version": state["favorite_version"]}, result=result, fault=fault)


def replace_preferences(store, actor, workspace, *, expected_preference_version, sidebar_width_px,
                        sidebar_collapsed, sections, operation_id, fault=None):
    expected = nws._expected_navigation_version(expected_preference_version, field="expected_preference_version")
    if type(sidebar_width_px) is not int or not nws.MIN_SIDEBAR_WIDTH_PX <= sidebar_width_px <= nws.MAX_SIDEBAR_WIDTH_PX:
        raise ValueError("invalid sidebar_width_px")
    if type(sidebar_collapsed) is not bool:
        raise ValueError("sidebar_collapsed must be a boolean")
    normalized = nws._normalize_navigation_sections(sections)
    request = {"expected_preference_version": expected, "sidebar": {"width_px": sidebar_width_px,
        "collapsed": sidebar_collapsed}, "sections": normalized}
    def mutate(state):
        if state["preference_version"] != expected:
            raise nws.WorkspaceRevisionConflict(state["preference_version"])
        if expected >= nws.MAX_HEALING_SEQUENCE_VALUE:
            raise ValueError("navigation version exhausted")
        state.update(preference_version=expected + 1, sidebar=request["sidebar"], sections=normalized)
        return {key: value for key, value in state.items() if key != "favorites"}
    return _mutate(store, actor, workspace, operation_id, nws.NAVIGATION_PREFERENCES_ACTION, request, mutate, fault=fault)


def change_favorite(store, actor, workspace, *, source_owner_subject_id, source_page_id,
                    expected_favorite_version, operation_id, remove=False, fault=None,
                    database_block_id=None):
    expected = nws._expected_navigation_version(expected_favorite_version, field="expected_favorite_version")
    target = {"source_owner_subject_id": nws._subject(source_owner_subject_id),
              "source_page_id": nws._source_page_identifier(source_page_id)}
    if database_block_id is not None:
        target["database_block_id"] = nws._source_page_identifier(database_block_id)
    request = {"expected_favorite_version": expected, **target}
    def mutate(state):
        if state["favorite_version"] != expected:
            raise nws.WorkspaceRevisionConflict(state["favorite_version"])
        favorites = state["favorites"]
        exists = target in favorites
        changed = exists if remove else not exists
        if changed:
            if expected >= nws.MAX_HEALING_SEQUENCE_VALUE:
                raise ValueError("navigation version exhausted")
            if remove:
                favorites.remove(target)
            else:
                if len(favorites) >= nws.MAX_NAVIGATION_FAVORITES:
                    raise ValueError("too many saved favorites")
                favorites.append(target)
            state["favorite_version"] += 1
        return {"workspace_id": state["workspace_id"], "subject_id": state["subject_id"],
            "preference_version": state["preference_version"], "favorite_version": state["favorite_version"],
            "changed": changed, "favorite": target}
    action = (nws.NAVIGATION_DATABASE_FAVORITE_REMOVED_ACTION if remove
              else nws.NAVIGATION_DATABASE_FAVORITE_ADDED_ACTION) if database_block_id is not None else (
              nws.NAVIGATION_FAVORITE_REMOVED_ACTION if remove else nws.NAVIGATION_FAVORITE_ADDED_ACTION)
    proof = None
    if database_block_id is not None:
        with store.iam._reader() as con:
            con.execute("BEGIN")
            _authorize(store, con, nws._subject(actor), nws._identifier(workspace, prefix="ws", field="workspace_id"))
            if not _visible(store, con, actor, workspace, {key: target[key] for key in
                    ("source_owner_subject_id", "source_page_id")}):
                raise NotFoundOrDenied("not found or permission denied")
        proof = database_favorite_projection.prepare(store, workspace, target["source_page_id"])
    return _mutate(store, actor, workspace, operation_id, action, request, mutate,
                   target=target, fault=fault, database_proof=proof,
                   allow_archived_database=remove and database_block_id is not None)
