"""Inbox notifications for row comments shared without their owner page."""
from __future__ import annotations

import time

from .canonical_authority import (
    NotFoundOrDenied, _identifier, _operation_id, _positive, _request_sha256,
)
from .database_access import CONTRACT, _expected_epoch, resolve_on_connection
from .database_row_comments import require_binding, target
from .database_row_page_placement import require_database_placement
from .live_authority import _json_object


def _row_target(row):
    try:
        return target(_json_object(str(row["anchor_json"]), field="comment anchor"))
    except ValueError:
        return None


def _authorized_notification(authority, con, actor, workspace, row, *,
                             expected_workspace_epoch=None,
                             expected_document_epoch=None,
                             expected_database_epoch=None):
    anchor = _row_target(row)
    if anchor is None or str(row["workspace_id"]) != workspace:
        raise NotFoundOrDenied("not found or permission denied")
    access = resolve_on_connection(
        authority, con, actor, workspace, anchor["database_id"])
    if access.document_id != str(row["current_document_id"]):
        raise NotFoundOrDenied("not found or permission denied")
    require_binding(con, access.document_id, anchor)
    require_database_placement(con, anchor["database_id"], anchor["row_id"])
    authority._comment(con, access.document_id, str(row["comment_id"]))
    if (expected_workspace_epoch is not None
            and (access.workspace_epoch != expected_workspace_epoch
                 or access.document_epoch != expected_document_epoch
                 or access.database_epoch != expected_database_epoch)):
        raise NotFoundOrDenied("not found or permission denied")
    return access, anchor


def list_direct_inbox(authority, actor_subject_id: str, workspace_id: str,
                      *, limit: int = 100) -> dict:
    actor = authority._require_active(actor_subject_id)
    workspace = _identifier(workspace_id, field="workspace_id")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
        raise ValueError("inbox limit is invalid")
    with authority._reader() as con:
        con.execute("BEGIN")
        rows = con.execute(
            "SELECT n.*,c.document_id AS current_document_id,c.anchor_json "
            "FROM workspace_collaboration_notifications n "
            "JOIN workspace_collaboration_comments c ON c.id=n.comment_id "
            "WHERE n.recipient_subject_id=? AND n.workspace_id=? "
            "AND n.redacted_at IS NULL AND c.deleted_at IS NULL "
            "ORDER BY n.created_at DESC,n.id DESC LIMIT ?",
            (actor, workspace, limit * 4),
        ).fetchall()
        items = []
        titles = {}
        for row in rows:
            if len(items) >= limit:
                break
            anchor = _row_target(row)
            if anchor is None:
                continue
            try:
                authority._access(
                    con, actor, str(row["current_document_id"]),
                    minimum_role="view")
            except NotFoundOrDenied:
                pass
            else:
                # The ordinary inbox preserves the page-shaped response when
                # the recipient is allowed to see that page.
                continue
            try:
                access, anchor = _authorized_notification(
                    authority, con, actor, workspace, row)
            except NotFoundOrDenied:
                continue
            if access.database_id not in titles:
                database = con.execute(
                    "SELECT title FROM workspace_collaboration_databases WHERE id=?",
                    (access.database_id,),
                ).fetchone()
                titles[access.database_id] = str(database["title"]) if database else "데이터베이스"
            payload = {
                "notification_id": str(row["id"]),
                "kind": str(row["kind"]),
                "workspace_id": workspace,
                "database_id": access.database_id,
                "row_id": anchor["row_id"],
                "comment_id": str(row["comment_id"]),
                "actor_subject_id": str(row["actor_subject_id"]),
            }
            items.append({
                "id": str(row["id"]), "kind": str(row["kind"]),
                "workspace_id": workspace,
                "database_id": access.database_id,
                "database_title": titles[access.database_id],
                "row_id": anchor["row_id"],
                "item_target": anchor,
                "comment_id": str(row["comment_id"]),
                "actor_subject_id": str(row["actor_subject_id"]),
                "payload": payload,
                "workspace_epoch": access.workspace_epoch,
                "document_epoch": access.document_epoch,
                "database_epoch": access.database_epoch,
                "notification_epoch": int(row["notification_epoch"]),
                "read": row["read_at"] is not None,
                "read_at": row["read_at"],
                "created_at": int(row["created_at"]),
            })
        return {"contract": CONTRACT, "workspace_id": workspace,
                "items": items, "count": len(items),
                "unread_count": sum(not item["read"] for item in items)}


def mark_direct_inbox_read(authority, actor_subject_id: str,
                           workspace_id: str, notification_id: str, *,
                           expected_workspace_epoch: int,
                           expected_document_epoch: int,
                           expected_database_epoch: int,
                           expected_notification_epoch: int,
                           operation_id: str, fault: str | None = None) -> dict:
    actor = authority._require_active(actor_subject_id)
    workspace = _identifier(workspace_id, field="workspace_id")
    notification = _identifier(notification_id, field="notification_id")
    workspace_epoch = _expected_epoch(expected_workspace_epoch, field="workspace_epoch")
    document_epoch = _expected_epoch(expected_document_epoch, field="document_epoch")
    database_epoch = _expected_epoch(expected_database_epoch, field="database_epoch")
    notification_epoch = _positive(expected_notification_epoch, field="notification_epoch")
    operation = _operation_id(operation_id)
    action = "database.notification_read"
    digest = _request_sha256(actor, action, {
        "workspace_id": workspace, "notification_id": notification,
        "expected_workspace_epoch": workspace_epoch,
        "expected_document_epoch": document_epoch,
        "expected_database_epoch": database_epoch,
        "expected_notification_epoch": notification_epoch,
    })
    with authority._transaction() as con:
        row = con.execute(
            "SELECT n.*,c.document_id AS current_document_id,c.anchor_json "
            "FROM workspace_collaboration_notifications n "
            "JOIN workspace_collaboration_comments c ON c.id=n.comment_id "
            "WHERE n.id=? AND n.recipient_subject_id=? AND n.workspace_id=? "
            "AND n.redacted_at IS NULL AND c.deleted_at IS NULL",
            (notification, actor, workspace),
        ).fetchone()
        if row is None:
            raise NotFoundOrDenied("not found or permission denied")

        def authorize():
            return _authorized_notification(
                authority, con, actor, workspace, row,
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
        access, anchor = authorize()
        if int(row["notification_epoch"]) != notification_epoch:
            raise NotFoundOrDenied("not found or permission denied")
        now = int(time.time())
        con.execute(
            "UPDATE workspace_collaboration_notifications SET "
            "read_at=COALESCE(read_at,?),notification_epoch=notification_epoch+1,"
            "updated_at=? WHERE id=?",
            (now, now, notification),
        )
        result = {
            "contract": CONTRACT, "workspace_id": workspace,
            "database_id": access.database_id, "row_id": anchor["row_id"],
            "notification_id": notification, "read": True,
            "notification_epoch": notification_epoch + 1,
            "workspace_epoch": access.workspace_epoch,
            "document_epoch": access.document_epoch,
            "database_epoch": access.database_epoch,
        }
        receipt = authority._finish(
            con, actor=actor, action=action, operation_id=operation,
            request_sha256=digest, workspace_id=workspace,
            document_id=access.document_id,
            workspace_epoch=access.workspace_epoch,
            document_epoch=access.document_epoch, document_sequence=None,
            event_type="notification.read",
            event_payload={"notification_id": notification,
                           "recipient_subject_id": actor,
                           "workspace_id": workspace,
                           "database_id": access.database_id,
                           "row_id": anchor["row_id"]},
            result=result, fault=fault,
        )
        return {**receipt, "replayed": False}
