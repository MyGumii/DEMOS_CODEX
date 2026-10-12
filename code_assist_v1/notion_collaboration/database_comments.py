"""Comment reads scoped to a directly shared database item.

The parent document is an internal storage detail. A database grant must never
turn a row-comment read into a parent-page read or a parent-file download.
"""
from __future__ import annotations

import html
import re
import time
from collections.abc import Sequence
from collections.abc import Callable
from urllib.parse import unquote

from .canonical_authority import (
    AuthorityUnavailable, NotFoundOrDenied, _canonical_json, _identifier,
    _operation_id, _positive, _request_sha256, _stable_id,
)
from .database_access import (
    CONTRACT, _expected_epoch, _visible_file_ids_on_connection,
    _file_on_connection, require_write_on_connection, resolve_on_connection,
)
from .database_row_comments import require_binding, target
from .database_row_page_placement import page_row_ids, require_database_placement
from .iam_authority import PAGE_ROLE_LEVEL
from . import database_row_trash, database_trash
from .live_authority import (
    MAX_MENTIONS, CommentRevisionConflict, _comment_text, _json_object, _reaction,
)


_PAGE_FILE_URL = re.compile(
    r"(?:https?://[^/\s)]+)?/api/notion/(?:collaboration/)?files/"
    r"([A-Za-z0-9_-]+)(?:\?[^\s)]*)?"
)
_DATABASE_FILE_URL = re.compile(
    r"(?:https?://[^/\s)]+)?/api/notion/workspaces/([A-Za-z0-9_-]+)/"
    r"databases/([A-Za-z0-9_-]+)/files/([A-Za-z0-9_-]+)(?:\?[^\s)]*)?"
)
_MARKDOWN_FILE = re.compile(
    r"\[[^\]\n]*\]\(((?:https?://[^/\s)]+)?/api/notion/"
    r"(?:(?:collaboration/)?files/[A-Za-z0-9_-]+|"
    r"workspaces/[A-Za-z0-9_-]+/databases/[A-Za-z0-9_-]+/"
    r"files/[A-Za-z0-9_-]+)(?:\?[^\s)]*)?)\)"
)
_REDACTED = "[접근할 수 없는 첨부 파일]"
_MAX_LINK_DECODE_PASSES = 16
_DIRECT_FILE_MARKDOWN = re.compile(
    r"\[([^\]\n]{1,200})\]\((/api/notion/workspaces/([A-Za-z0-9_-]+)/"
    r"databases/([A-Za-z0-9_-]+)/files/([A-Za-z0-9_-]+))\)"
)


def _safe_content(con, access, content: str, visible: frozenset[str]) -> str:
    """Convert old page-authorized file links or suppress their whole comment."""
    def scoped_url(file_id: str) -> str | None:
        if file_id not in visible:
            return None
        row = con.execute(
            "SELECT 1 FROM workspace_collaboration_files WHERE id=? AND "
            "workspace_id=? AND document_id=? AND state='active'",
            (file_id, access.workspace_id, access.document_id),
        ).fetchone()
        if row is None:
            return None
        return (f"/api/notion/workspaces/{access.workspace_id}/databases/"
                f"{access.database_id}/files/{file_id}")

    def replace_markdown(match: re.Match[str]) -> str:
        file_match = _PAGE_FILE_URL.fullmatch(match.group(1))
        db_match = _DATABASE_FILE_URL.fullmatch(match.group(1))
        if file_match:
            url = scoped_url(file_match.group(1))
        elif (db_match and db_match.group(1) == access.workspace_id
              and db_match.group(2) == access.database_id):
            url = scoped_url(db_match.group(3))
        else:
            url = None
        return f"{match.group(0).split('](', 1)[0]}]({url})" if url else _REDACTED

    safe = _MARKDOWN_FILE.sub(replace_markdown, content)
    safe = _PAGE_FILE_URL.sub(
        lambda match: scoped_url(match.group(1)) or _REDACTED, safe)
    safe = _DATABASE_FILE_URL.sub(
        lambda match: (scoped_url(match.group(3))
                       if match.group(1) == access.workspace_id
                       and match.group(2) == access.database_id else None)
        or _REDACTED, safe)
    # Other page-shaped links, including encoded variants, are not part of the
    # database contract. Suppress the entire content rather than guessing.
    decoded = safe
    for _ in range(_MAX_LINK_DECODE_PASSES):
        previous_links = decoded.count("/api/notion/")
        next_value = html.unescape(unquote(decoded))
        if (next_value.count("/api/notion/") > previous_links
                or access.document_id in next_value or "/api/notion/" in next_value.replace(
                f"/api/notion/workspaces/{access.workspace_id}/databases/"
                f"{access.database_id}/files/", "")):
            return _REDACTED
        if next_value == decoded:
            return safe
        decoded = next_value
    return _REDACTED


def _checked_text(content: str, *, private_document_id: str | None = None,
                  attachment: Callable[[str, str, str], None] | None = None) -> str:
    text = _comment_text(content)
    def strip_valid_link(match: re.Match[str]) -> str:
        if attachment is not None:
            attachment(match.group(3), match.group(4), match.group(5))
        return match.group(1)

    decoded = _DIRECT_FILE_MARKDOWN.sub(strip_valid_link, text)
    for _ in range(_MAX_LINK_DECODE_PASSES):
        next_value = html.unescape(unquote(decoded))
        if "/api/notion/" in next_value:
            raise ValueError("database comment cannot contain an internal file or page link")
        if private_document_id is not None and private_document_id in next_value:
            raise ValueError("database comment cannot contain a private page identifier")
        if next_value == decoded:
            return text
        decoded = next_value
    raise ValueError("database comment link encoding is too deeply nested")


def _mention_request(values: Sequence[str] | None) -> tuple[str, ...]:
    if values is None:
        return ()
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise ValueError("mention_subject_ids must be an array")
    raw = list(values)
    if len(raw) > MAX_MENTIONS:
        raise ValueError("too many mentions")
    return tuple(sorted({_identifier(value, field="mentioned_subject_id")
                         for value in raw}))


def _database_mentions(authority, con, subjects: tuple[str, ...],
                       workspace_id: str, database_id: str) -> tuple[str, ...]:
    for subject in subjects:
        authority._require_active(subject)
        resolve_on_connection(authority, con, subject, workspace_id, database_id)
    return subjects


def eligible_mention_subject_ids(authority, actor_subject_id: str,
                                 workspace_id: str, database_id: str,
                                 subject_ids: Sequence[str]) -> dict:
    """Filter known users through the same live database access as a mention write."""
    actor = authority._require_active(actor_subject_id)
    if (isinstance(subject_ids, (str, bytes))
            or not isinstance(subject_ids, Sequence)
            or len(subject_ids) > 500):
        raise ValueError("subject_ids must be an array of at most 500 users")
    candidates = tuple(dict.fromkeys(
        _identifier(subject, field="subject_id") for subject in subject_ids
    ))
    with authority._reader() as con:
        con.execute("BEGIN")
        access = resolve_on_connection(
            authority, con, actor, workspace_id, database_id)
        if PAGE_ROLE_LEVEL[access.role] < PAGE_ROLE_LEVEL["comment"]:
            raise NotFoundOrDenied("not found or permission denied")
        eligible = []
        for subject in candidates:
            try:
                authority._require_active(subject)
                resolve_on_connection(
                    authority, con, subject, access.workspace_id, access.database_id)
            except NotFoundOrDenied:
                continue
            eligible.append(subject)
        return {
            "contract": CONTRACT, "workspace_id": access.workspace_id,
            "database_id": access.database_id, "subject_ids": eligible,
            "workspace_epoch": access.workspace_epoch,
            "document_epoch": access.document_epoch,
            "database_epoch": access.database_epoch,
        }


def list_row_comments(authority, actor_subject_id: str, workspace_id: str,
                      database_id: str, row_id: str) -> dict:
    actor = authority._require_active(actor_subject_id)
    row_identity = _identifier(row_id, field="row_id")
    with authority._reader() as con:
        con.execute("BEGIN")
        access = resolve_on_connection(authority, con, actor, workspace_id, database_id)
        anchor = target({"kind": "database_row", "database_id": access.database_id,
                         "row_id": row_identity})
        require_binding(con, access.document_id, anchor)
        if row_identity in page_row_ids(con, access.database_id):
            raise NotFoundOrDenied("not found or permission denied")
        document = authority._document(con, access.document_id)
        hidden = (database_trash.hidden_comment_ids(con, access.document_id)
                  | database_row_trash.hidden_comment_ids(con, access.document_id))
        visible = _visible_file_ids_on_connection(con, access.database_id)
        rows = con.execute(
            "SELECT c.* FROM workspace_collaboration_comments c WHERE c.document_id=? "
            "AND c.deleted_at IS NULL AND json_extract(c.anchor_json,'$.kind')='database_row' "
            "AND json_extract(c.anchor_json,'$.database_id')=? "
            "AND json_extract(c.anchor_json,'$.row_id')=? ORDER BY c.created_at,c.id",
            (access.document_id, access.database_id, row_identity),
        ).fetchall()
        items = []
        for comment in rows:
            if str(comment["id"]) in hidden:
                continue
            stored_anchor = _json_object(str(comment["anchor_json"]), field="comment anchor")
            if target(stored_anchor) != anchor:
                raise AuthorityUnavailable("stored item comment target is invalid")
            items.append({
                "id": str(comment["id"]),
                "author_subject_id": str(comment["author_subject_id"]),
                "content": _safe_content(con, access, str(comment["content"]), visible),
                "anchor": anchor,
                "parent_comment_id": comment["parent_comment_id"],
                "reactions": _json_object(str(comment["reactions_json"]), field="comment reactions"),
                "resolved": bool(comment["resolved"]),
                "resolved_by_subject_id": comment["resolved_by_subject_id"],
                "resolved_at": comment["resolved_at"],
                "row_version": int(comment["row_version"]),
                "created_at": int(comment["created_at"]),
                "updated_at": int(comment["updated_at"]),
            })
        return {
            "contract": CONTRACT, "workspace_id": access.workspace_id,
            "database_id": access.database_id, "row_id": row_identity,
            "role": access.role, "item_target": anchor,
            "workspace_epoch": access.workspace_epoch,
            "document_epoch": access.document_epoch,
            "database_epoch": access.database_epoch,
            "document_sequence": int(document["document_sequence"]),
            "items": items, "count": len(items),
        }


def add_row_comment(authority, actor_subject_id: str, workspace_id: str,
                    database_id: str, row_id: str, *, comment_id: str,
                    content: str, expected_workspace_epoch: int,
                    expected_document_epoch: int, expected_database_epoch: int,
                    operation_id: str, parent_comment_id: str | None = None,
                    mention_subject_ids: Sequence[str] | None = None,
                    fault: str | None = None) -> dict:
    """Add a row comment with database permission and a database-only receipt.

    Replies notify their parent author. Direct mentions and attachments still
    need database-scoped transports.
    """
    actor = authority._require_active(actor_subject_id)
    workspace = _identifier(workspace_id, field="workspace_id")
    database = _identifier(database_id, field="database_id")
    row = _identifier(row_id, field="row_id")
    comment = _identifier(comment_id, field="comment_id")
    parent = None if parent_comment_id is None else _identifier(
        parent_comment_id, field="parent_comment_id")
    text = _checked_text(content)
    requested_mentions = _mention_request(mention_subject_ids)
    workspace_epoch = _expected_epoch(expected_workspace_epoch, field="workspace_epoch")
    document_epoch = _expected_epoch(expected_document_epoch, field="document_epoch")
    database_epoch = _expected_epoch(expected_database_epoch, field="database_epoch")
    operation = _operation_id(operation_id)
    anchor = {"kind": "database_row", "database_id": database, "row_id": row}
    action = "database.row_comment_added"
    digest = _request_sha256(actor, action, {
        "workspace_id": workspace, "database_id": database, "row_id": row,
        "comment_id": comment, "parent_comment_id": parent, "content": text,
        "mention_subject_ids": list(requested_mentions),
        "expected_workspace_epoch": workspace_epoch,
        "expected_document_epoch": document_epoch,
        "expected_database_epoch": database_epoch,
    })
    with authority._transaction() as con:
        def authorize():
            access = require_write_on_connection(
                authority, con, actor, workspace, database,
                minimum_role="comment", expected_workspace_epoch=workspace_epoch,
                expected_document_epoch=document_epoch,
                expected_database_epoch=database_epoch,
            )
            require_binding(con, access.document_id, anchor)
            require_database_placement(con, database, row)
            def check_attachment(file_workspace: str, file_database: str,
                                 file_id: str) -> None:
                if file_workspace != workspace or file_database != database:
                    raise NotFoundOrDenied("not found or permission denied")
                _file_on_connection(authority, con, actor, workspace, database, file_id)
            _checked_text(text, private_document_id=access.document_id,
                          attachment=check_attachment)
            return access

        replay = authority._replay(
            con, actor=actor, action=action, operation_id=operation,
            request_sha256=digest, authorize=authorize,
        )
        if replay is not None:
            return {**replay, "replayed": True}
        access = authorize()
        mentions = _database_mentions(
            authority, con, requested_mentions, workspace, database)
        parent_row = None if parent is None else authority._comment(
            con, access.document_id, parent)
        if parent_row is not None and target(_json_object(
                str(parent_row["anchor_json"]), field="comment anchor")) != anchor:
            raise NotFoundOrDenied("not found or permission denied")
        document = authority._document(con, access.document_id)
        now = int(time.time())
        sequence = authority._advance_document(con, document, now=now)
        con.execute(
            "INSERT INTO workspace_collaboration_comments("
            "id,document_id,block_id,author_subject_id,content,anchor_json,"
            "parent_comment_id,reactions_json,resolved,row_version,created_at,updated_at,"
            "deleted_at,resolved_by_subject_id,resolved_at) "
            "VALUES(?,?,NULL,?,?,?,?,'{}',0,1,?,?,NULL,NULL,NULL)",
            (comment, access.document_id, actor, text, _canonical_json(anchor),
             parent, now, now),
        )
        authority._insert_mentions(con, comment, mentions, now=now)
        recipients = ({str(parent_row["author_subject_id"]): "comment_replied"}
                      if parent_row is not None else {})
        recipients.update({subject: "comment_mentioned" for subject in mentions})
        authority._insert_notifications(
            con, actor=actor, workspace_id=workspace,
            document_id=access.document_id, comment_id=comment,
            source_event_id=_stable_id("cevt", actor, action, operation),
            recipients=recipients, workspace_epoch=workspace_epoch,
            document_epoch=document_epoch, now=now,
        )
        result = {
            "contract": CONTRACT, "workspace_id": workspace,
            "database_id": database, "row_id": row, "comment_id": comment,
            "document_sequence": sequence,
            "workspace_epoch": workspace_epoch,
            "document_epoch": document_epoch,
            "database_epoch": database_epoch,
            "notification_count": sum(recipient != actor for recipient in recipients),
        }
        receipt = authority._finish(
            con, actor=actor, action=action, operation_id=operation,
            request_sha256=digest, workspace_id=workspace,
            document_id=access.document_id, workspace_epoch=workspace_epoch,
            document_epoch=document_epoch, document_sequence=sequence,
            event_type="comment.added",
            event_payload={
                "workspace_id": workspace, "database_id": database,
                "row_id": row, "comment_id": comment,
                "parent_comment_id": parent, "author_subject_id": actor,
                "document_sequence": sequence,
            },
            result=result, fault=fault,
        )
        return {**receipt, "replayed": False}


def mutate_row_comment(authority, actor_subject_id: str, workspace_id: str,
                       database_id: str, row_id: str, comment_id: str, *,
                       mutation: str, expected_row_version: int,
                       expected_workspace_epoch: int,
                       expected_document_epoch: int,
                       expected_database_epoch: int, operation_id: str,
                       content: str | None = None, resolved: bool | None = None,
                       emoji: str | None = None,
                       mention_subject_ids: Sequence[str] | None = None,
                       fault: str | None = None) -> dict:
    """Mutate one row thread without granting access to its owner document."""
    if mutation not in {"edit", "delete", "resolve", "react"}:
        raise ValueError("unsupported database comment mutation")
    if mutation == "edit":
        if content is None or resolved is not None or emoji is not None:
            raise ValueError("invalid database comment edit")
        value = _checked_text(content)
        requested_mentions = _mention_request(mention_subject_ids)
    elif mutation == "resolve":
        if (not isinstance(resolved, bool) or content is not None or emoji is not None
                or mention_subject_ids is not None):
            raise ValueError("invalid database comment resolution")
        value = resolved
    elif mutation == "react":
        if (emoji is None or content is not None or resolved is not None
                or mention_subject_ids is not None):
            raise ValueError("invalid database comment reaction")
        value = _reaction(emoji)
    else:
        if (content is not None or resolved is not None or emoji is not None
                or mention_subject_ids is not None):
            raise ValueError("invalid database comment deletion")
        value = None
    actor = authority._require_active(actor_subject_id)
    workspace = _identifier(workspace_id, field="workspace_id")
    database = _identifier(database_id, field="database_id")
    row = _identifier(row_id, field="row_id")
    comment = _identifier(comment_id, field="comment_id")
    version = _positive(expected_row_version, field="row_version")
    workspace_epoch = _expected_epoch(expected_workspace_epoch, field="workspace_epoch")
    document_epoch = _expected_epoch(expected_document_epoch, field="document_epoch")
    database_epoch = _expected_epoch(expected_database_epoch, field="database_epoch")
    operation = _operation_id(operation_id)
    anchor = {"kind": "database_row", "database_id": database, "row_id": row}
    action = f"database.row_comment_{mutation}"
    request = {
        "workspace_id": workspace, "database_id": database, "row_id": row,
        "comment_id": comment, "expected_row_version": version,
        "expected_workspace_epoch": workspace_epoch,
        "expected_document_epoch": document_epoch,
        "expected_database_epoch": database_epoch,
    }
    if mutation != "delete":
        request["value"] = value
    if mutation == "edit":
        request["mention_subject_ids"] = list(requested_mentions)
    digest = _request_sha256(actor, action, request)
    with authority._transaction() as con:
        def authorize():
            access = require_write_on_connection(
                authority, con, actor, workspace, database,
                minimum_role="comment", expected_workspace_epoch=workspace_epoch,
                expected_document_epoch=document_epoch,
                expected_database_epoch=database_epoch,
            )
            require_binding(con, access.document_id, anchor)
            require_database_placement(con, database, row)
            item = authority._comment(
                con, access.document_id, comment,
                include_deleted=mutation == "delete",
            )
            if (item["block_id"] is not None or target(_json_object(
                    str(item["anchor_json"]), field="comment anchor")) != anchor):
                raise NotFoundOrDenied("not found or permission denied")
            if (mutation in {"edit", "delete"}
                    and str(item["author_subject_id"]) != actor
                    and access.role != "full_access"):
                raise NotFoundOrDenied("not found or permission denied")
            if mutation == "edit":
                def check_attachment(file_workspace: str, file_database: str,
                                     file_id: str) -> None:
                    if file_workspace != workspace or file_database != database:
                        raise NotFoundOrDenied("not found or permission denied")
                    _file_on_connection(authority, con, actor, workspace, database, file_id)
                _checked_text(value, private_document_id=access.document_id,
                              attachment=check_attachment)
            return access, item

        replay = authority._replay(
            con, actor=actor, action=action, operation_id=operation,
            request_sha256=digest, authorize=authorize,
        )
        if replay is not None:
            return {**replay, "replayed": True}
        access, item = authorize()
        if int(item["row_version"]) != version:
            raise CommentRevisionConflict(int(item["row_version"]))
        mentions = (_database_mentions(
            authority, con, requested_mentions, workspace, database)
            if mutation == "edit" else ())
        document = authority._document(con, access.document_id)
        now = int(time.time())
        sequence = authority._advance_document(con, document, now=now)
        result = {
            "contract": CONTRACT, "workspace_id": workspace,
            "database_id": database, "row_id": row, "comment_id": comment,
            "workspace_epoch": workspace_epoch,
            "document_epoch": document_epoch,
            "database_epoch": database_epoch,
            "document_sequence": sequence,
            "row_version": version + 1,
        }
        event_type = f"comment.{mutation}"
        if mutation == "edit":
            con.execute(
                "UPDATE workspace_collaboration_comments SET content=?,"
                "row_version=row_version+1,updated_at=? WHERE id=?",
                (value, now, comment),
            )
            con.execute(
                "DELETE FROM workspace_collaboration_comment_mentions WHERE comment_id=?",
                (comment,),
            )
            authority._insert_mentions(con, comment, mentions, now=now)
            authority._insert_notifications(
                con, actor=actor, workspace_id=workspace,
                document_id=access.document_id, comment_id=comment,
                source_event_id=_stable_id("cevt", actor, action, operation),
                recipients={subject: "comment_mentioned" for subject in mentions},
                workspace_epoch=workspace_epoch,
                document_epoch=document_epoch, now=now,
            )
            result["notification_count"] = sum(subject != actor for subject in mentions)
            event_type = "comment.updated"
        elif mutation == "delete":
            con.execute(
                "UPDATE workspace_collaboration_comments SET content='',reactions_json='{}',"
                "deleted_at=?,row_version=row_version+1,updated_at=? WHERE id=?",
                (now, now, comment),
            )
            con.execute(
                "DELETE FROM workspace_collaboration_comment_mentions WHERE comment_id=?",
                (comment,),
            )
            con.execute(
                "UPDATE workspace_collaboration_notifications SET "
                "redacted_at=COALESCE(redacted_at,?),notification_epoch=notification_epoch+1,"
                "updated_at=? WHERE comment_id=? AND redacted_at IS NULL",
                (now, now, comment),
            )
            result["deleted"] = True
        elif mutation == "resolve":
            con.execute(
                "UPDATE workspace_collaboration_comments SET resolved=?,"
                "resolved_by_subject_id=?,resolved_at=?,row_version=row_version+1,updated_at=? "
                "WHERE id=?",
                (int(value), actor if value else None, now if value else None, now, comment),
            )
            result["resolved"] = value
            event_type = "comment.resolved" if value else "comment.reopened"
        else:
            reactions = _json_object(str(item["reactions_json"]), field="comment reactions")
            members = reactions.get(value, [])
            if not isinstance(members, list) or not all(isinstance(member, str) for member in members):
                raise AuthorityUnavailable("stored comment reactions are invalid")
            updated = set(members)
            added = actor not in updated
            if added:
                updated.add(actor)
            else:
                updated.remove(actor)
            if updated:
                reactions[value] = sorted(updated)
            else:
                reactions.pop(value, None)
            con.execute(
                "UPDATE workspace_collaboration_comments SET reactions_json=?,"
                "row_version=row_version+1,updated_at=? WHERE id=?",
                (_canonical_json(reactions), now, comment),
            )
            result.update({"emoji": value, "added": added, "reactions": reactions})
            event_type = "comment.reaction_added" if added else "comment.reaction_removed"
            if added:
                authority._insert_notifications(
                    con, actor=actor, workspace_id=workspace,
                    document_id=access.document_id, comment_id=comment,
                    source_event_id=_stable_id("cevt", actor, action, operation),
                    recipients={str(item["author_subject_id"]): "comment_reacted"},
                    workspace_epoch=workspace_epoch,
                    document_epoch=document_epoch, now=now,
                )
        receipt = authority._finish(
            con, actor=actor, action=action, operation_id=operation,
            request_sha256=digest, workspace_id=workspace,
            document_id=access.document_id, workspace_epoch=workspace_epoch,
            document_epoch=document_epoch, document_sequence=sequence,
            event_type=event_type,
            event_payload={
                "workspace_id": workspace, "database_id": database,
                "row_id": row, "comment_id": comment,
                "actor_subject_id": actor, "document_sequence": sequence,
                "mutation": mutation,
            },
            result=result, fault=fault,
        )
        return {**receipt, "replayed": False}
