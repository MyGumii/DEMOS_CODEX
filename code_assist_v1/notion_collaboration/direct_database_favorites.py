"""Personal favorites for databases shared without their parent page."""
from __future__ import annotations

import json
import time

from code_assist_v1 import notion_workspace_direct_database_favorite_migration as migration

from . import database_access
from .canonical_authority import (
    AuthorityUnavailable, NotFoundOrDenied, OperationConflict, _canonical_json,
    _identifier, _operation_id, _request_sha256,
)


CONTRACT = "code_assist.notion.direct_database_favorites.v1"
MAX_FAVORITES = 100
MAX_VERSION = 9007199254740991
ADD_ACTION = "database.favorite.add"
REMOVE_ACTION = "database.favorite.remove"


def _installed(con):
    marker = con.execute(
        "SELECT version,name,checksum,actual_schema_sha256 FROM "
        "workspace_schema_migrations ORDER BY version DESC LIMIT 1"
    ).fetchone()
    return marker is not None and tuple(marker) == (
        migration.TARGET_VERSION, migration.TARGET_NAME,
        migration.TARGET_CHECKSUM, migration.TARGET_ACTUAL_SCHEMA_SHA256,
    )


def installed(authority):
    with authority._reader() as con:
        con.execute("BEGIN")
        return _installed(con)


def _actor(authority, value):
    return authority._require_active(value)


def _version(value):
    if type(value) is not int or not 0 <= value <= MAX_VERSION:
        raise ValueError("valid expected_favorite_version required")
    return value


def _epoch(value, name):
    if type(value) is not int or not 1 <= value <= MAX_VERSION:
        raise ValueError(f"valid {name} required")
    return value


def _state(con, workspace, actor):
    row = con.execute(
        "SELECT favorite_version,database_ids_json FROM "
        "workspace_collaboration_direct_database_favorite_states "
        "WHERE workspace_id=? AND subject_id=?", (workspace, actor),
    ).fetchone()
    if row is None:
        return 0, []
    try:
        ids = json.loads(str(row["database_ids_json"]))
    except (TypeError, ValueError) as exc:
        raise AuthorityUnavailable("database favorite state is invalid") from exc
    if (not isinstance(ids, list) or len(ids) > MAX_FAVORITES
            or any(not isinstance(item, str) or not item for item in ids)
            or len(ids) != len(set(ids))):
        raise AuthorityUnavailable("database favorite state is invalid")
    return int(row["favorite_version"]), ids


def read(authority, actor_subject_id, workspace_id, *, access_database_id):
    """Return only favorites currently visible through database-scoped access."""
    actor = _actor(authority, actor_subject_id)
    workspace = _identifier(workspace_id, field="workspace_id")
    proof_id = _identifier(access_database_id, field="access_database_id")
    with authority._reader() as con:
        con.execute("BEGIN")
        if not _installed(con):
            raise AuthorityUnavailable("direct database favorites are not installed")
        database_access.resolve_on_connection(authority, con, actor, workspace, proof_id)
        version, ids = _state(con, workspace, actor)
        visible = []
        for database_id in ids:
            try:
                database_access.resolve_on_connection(
                    authority, con, actor, workspace, database_id)
            except NotFoundOrDenied:
                continue
            row = con.execute(
                "SELECT title FROM workspace_collaboration_databases WHERE id=?",
                (database_id,),
            ).fetchone()
            if row is None:
                raise AuthorityUnavailable("database favorite target is invalid")
            visible.append({"database_id": database_id, "title": str(row["title"]),
                            "position": len(visible)})
        _actor(authority, actor)
        return {"contract": CONTRACT, "workspace_id": workspace,
                "favorite_version": version, "items": visible}


def change(authority, actor_subject_id, workspace_id, database_id, *,
           remove, expected_favorite_version, expected_workspace_epoch,
           expected_document_epoch, expected_database_epoch, operation_id,
           fault=None):
    actor = _actor(authority, actor_subject_id)
    workspace = _identifier(workspace_id, field="workspace_id")
    database = _identifier(database_id, field="database_id")
    if type(remove) is not bool:
        raise ValueError("remove must be a boolean")
    expected = _version(expected_favorite_version)
    epochs = (
        _epoch(expected_workspace_epoch, "expected_workspace_epoch"),
        _epoch(expected_document_epoch, "expected_document_epoch"),
        _epoch(expected_database_epoch, "expected_database_epoch"),
    )
    operation = _operation_id(operation_id)
    action = REMOVE_ACTION if remove else ADD_ACTION
    request = {"workspace_id": workspace, "database_id": database,
               "expected_favorite_version": expected,
               "expected_workspace_epoch": epochs[0],
               "expected_document_epoch": epochs[1],
               "expected_database_epoch": epochs[2]}
    request_hash = _request_sha256(actor, action, request)
    with authority._transaction() as con:
        if not _installed(con):
            raise AuthorityUnavailable("direct database favorites are not installed")

        def authorize():
            access = database_access.resolve_on_connection(
                authority, con, actor, workspace, database)
            if (access.workspace_epoch, access.document_epoch,
                    access.database_epoch) != epochs:
                raise NotFoundOrDenied("not found or permission denied")
            return access

        access = authorize()
        other = con.execute(
            "SELECT action FROM workspace_collaboration_operations "
            "WHERE actor_subject_id=? AND operation_id=? AND action IN (?,?)",
            (actor, operation, ADD_ACTION, REMOVE_ACTION),
        ).fetchall()
        if any(row["action"] != action for row in other):
            raise OperationConflict("operation_id reused with a different request")
        replay = authority._replay(
            con, actor=actor, action=action, operation_id=operation,
            request_sha256=request_hash, authorize=authorize,
        )
        if replay is not None:
            return {**replay, "replayed": True}
        current_version, ids = _state(con, workspace, actor)
        if current_version != expected:
            raise OperationConflict("database favorite version changed")
        changed = (database in ids) if remove else (database not in ids)
        if changed:
            if current_version >= MAX_VERSION:
                raise ValueError("database favorite version exhausted")
            if remove:
                ids.remove(database)
            else:
                if len(ids) >= MAX_FAVORITES:
                    raise ValueError("too many database favorites")
                ids.append(database)
            current_version += 1
            con.execute(
                "INSERT INTO workspace_collaboration_direct_database_favorite_states "
                "(workspace_id,subject_id,favorite_version,database_ids_json,updated_at) "
                "VALUES(?,?,?,?,?) ON CONFLICT(workspace_id,subject_id) DO UPDATE SET "
                "favorite_version=excluded.favorite_version,"
                "database_ids_json=excluded.database_ids_json,"
                "updated_at=excluded.updated_at",
                (workspace, actor, current_version, _canonical_json(ids),
                 int(time.time())),
            )
        authorize()
        result = {"contract": CONTRACT, "workspace_id": workspace,
                  "database_id": database, "favorite_version": current_version,
                  "is_favorite": database in ids, "changed": changed,
                  "replayed": False}
        return authority._finish(
            con, actor=actor, action=action, operation_id=operation,
            request_sha256=request_hash, workspace_id=workspace,
            document_id=None, document_epoch=None, document_sequence=None,
            workspace_epoch=access.workspace_epoch, event_type=action,
            event_payload={"subject_id": actor,
                           "favorite_version": current_version},
            result=result, fault=fault,
        )
