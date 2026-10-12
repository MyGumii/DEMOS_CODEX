"""Prepare and commit only text leaves in one canonical database-owned body."""
from copy import deepcopy
from dataclasses import dataclass
import json

from . import database_activity, database_row_content, database_row_page_placement, database_row_trash
from . import synced_blocks as synced
from .canonical_authority import NotFoundOrDenied, _canonical_json, _sha256


@dataclass(frozen=True)
class PreparedBodyEdit:
    document_id: str
    origin_block_id: str
    location: synced.BodyLocation
    version: int
    source_body_json: str
    body_json: str
    body_sha256: str


def _rewrite(blocks, updates):
    result = deepcopy(blocks)
    indexed = synced.index_blocks(result)
    for update in updates:
        block = indexed[update["id"]]
        block["content"] = update["content"]
        props = block.setdefault("props", {})
        if update["rich_text_html"] is None:
            props.pop("rich_text_html", None)
        else:
            props["rich_text_html"] = update["rich_text_html"]
    # Validate within the existing body limits without normalizing template
    # positions or throwing away unrelated legacy metadata.
    database_row_content.clean_blocks(result)
    encoded = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    if len(encoded.encode("utf-8")) > database_row_content.MAX_BODY_BYTES:
        raise ValueError("synced database body is too large")
    return encoded


def _plan(source, origin_block_id, updates):
    descriptor = json.loads(source.descriptor_json)
    location, blocks, _original = synced.locate_block(descriptor, origin_block_id)
    if location.kind not in {"row", "template"}:
        raise synced.SyncedShapeUnavailable("a database-owned synced original is required")
    database = next(value for value in descriptor["databases"] if value["id"] == location.database_id)
    key, identity = ("rows", location.row_id) if location.kind == "row" else ("templates", location.template_id)
    value = next(value for value in database[key] if value["id"] == identity)
    version = value["version"]
    if type(version) is not int or version < 1:
        raise synced.SyncedShapeUnavailable("exact database body version required")
    encoded = _rewrite(blocks, updates)
    return PreparedBodyEdit(source.document_id, origin_block_id, location, version,
                            _canonical_json(blocks), encoded, _sha256(encoded))


def prepare(source, origin_block_id, updates):
    return _plan(source, origin_block_id, updates)


def validate(prepared, source, updates):
    expected = _plan(source, prepared.origin_block_id, updates)
    if prepared != expected:
        raise synced.SyncedShapeUnavailable("prepared database body edit changed")


def commit(authority, con, actor, page, prepared, now):
    if not con.in_transaction:
        raise ValueError("database synced edit requires a transaction")
    location, document = prepared.location, prepared.document_id
    if page["id"] != document:
        raise synced.SyncedShapeUnavailable("database body document mismatch")
    authority._database_for_document(con, document, location.database_id)
    if location.kind == "row":
        if not database_row_content.installed(con):
            raise synced.SyncedShapeUnavailable("database item body writer is unavailable")
        database_row_trash.require_active(con, location.database_id, location.row_id)
        if location.row_id in database_row_page_placement.page_row_ids(con, location.database_id):
            raise NotFoundOrDenied("not found or permission denied")
        row = con.execute("SELECT row.row_version,body.blocks_json FROM workspace_collaboration_database_rows row "
            "JOIN workspace_collaboration_database_row_bodies body ON body.row_id=row.id "
            "WHERE row.database_id=? AND row.id=?", (location.database_id,location.row_id)).fetchone()
        target, kind, field = location.row_id, "database.row.content.set", "blocks_json"
    elif location.kind == "template":
        row = con.execute("SELECT row_version,body_json FROM workspace_collaboration_database_templates "
            "WHERE database_id=? AND id=? AND state='active'", (location.database_id,location.template_id)).fetchone()
        target, kind, field = location.template_id, "database.template.update", "body_json"
    else:
        raise synced.SyncedShapeUnavailable("invalid database body location")
    if row is None:
        raise NotFoundOrDenied("not found or permission denied")
    if int(row["row_version"]) != prepared.version or _canonical_json(json.loads(row[field])) != prepared.source_body_json:
        raise synced.SyncedSourceChanged("database body changed during edit preparation")
    if _sha256(prepared.body_json) != prepared.body_sha256:
        raise synced.SyncedShapeUnavailable("prepared database body checksum changed")
    if location.kind == "row":
        con.execute("UPDATE workspace_collaboration_database_row_bodies SET blocks_json=? WHERE row_id=?",
                    (prepared.body_json,target))
        changed = con.execute("UPDATE workspace_collaboration_database_rows SET row_version=row_version+1,updated_at=? "
            "WHERE database_id=? AND id=? AND row_version=?", (now,location.database_id,target,prepared.version))
    else:
        changed = con.execute("UPDATE workspace_collaboration_database_templates SET body_json=?,row_version=row_version+1,updated_at=? "
            "WHERE database_id=? AND id=? AND state='active' AND row_version=?",
            (prepared.body_json,now,location.database_id,target,prepared.version))
    if changed.rowcount != 1:
        raise synced.SyncedSourceChanged("database body version changed")
    sequence = page["document_sequence"] + 1
    content_hash = _sha256(_canonical_json(authority._editing_snapshot(con,authority._document(con,document))))
    con.execute("UPDATE workspace_collaboration_documents SET document_sequence=?,content_sha256=?,"
        "row_version=row_version+1,updated_at=? WHERE id=?", (sequence,content_hash,now,document))
    database_activity.touch(con,database_ids=(location.database_id,),document_id=document,
        actor_subject_id=actor,edited_at=now,document_sequence=sequence)
    return {"storage":"database_body","location":location.payload(),"database_id":location.database_id,
            "command_kind":kind,"target_id":target,"target_version":prepared.version+1,
            "document_sequence":sequence,"content_sha256":content_hash,"body_sha256":prepared.body_sha256}
