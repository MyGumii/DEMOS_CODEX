"""Prepare current canonical page-copy sources without creating any documents.

Yjs replay and blob verification happen outside database transactions. A future
copy commit must call revalidate_copy_source again inside its write transaction;
the successful preparation is not a lease or an authorization cache.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json

from .canonical_authority import AuthorityUnavailable, EpochConflict, NotFoundOrDenied, _canonical_json, _identifier, _positive
from .editing_authority import DocumentSequenceConflict
from . import page_archive, page_move, search_projection, page_copy_rich
from .title_projection import _frames, _generation
from .parent_historical_structure import needs_recovery, finalize_witness

MAX_SOURCE_DOCUMENTS = 128
MAX_SOURCE_BYTES = 64 * 1024 * 1024


class CopySourceChanged(RuntimeError):
    """The copy must be prepared again because its source or destination changed."""


@dataclass(frozen=True)
class CopySourceDocument:
    document_id: str
    parent_id: str | None
    position: int
    stamp: tuple
    descriptor_json: str
    files_json: str
    rich_text_json: str = ''

    def descriptor(self):
        # The remapping stage works on its own value, never on the CAS witness.
        return json.loads(self.descriptor_json)


@dataclass(frozen=True)
class PreparedPageCopy:
    actor: str
    workspace_id: str
    source_page_id: str
    workspace_stamp: tuple
    documents: tuple[CopySourceDocument, ...]
    parent_id: str | None
    parent_stamp: tuple | None


def _owner(authority, con, actor, workspace_id, document_id=None):
    """Keep page-copy ownership exact while staging the 2423 source schema."""
    from . import database_row_trash

    if not database_row_trash.installed(con):
        return page_archive._owner(authority, con, actor, workspace_id, document_id)
    authority._require_active(actor)
    workspace = authority._workspace(con, workspace_id)
    if authority._workspace_role(con, workspace_id, actor) != "owner":
        raise NotFoundOrDenied("not found or permission denied")
    page = authority._document(con, document_id) if document_id else None
    if page is not None and (page["workspace_id"] != workspace_id
            or page["legacy_owner_subject_id"] != actor):
        raise NotFoundOrDenied("not found or permission denied")
    return workspace, page


def _ordered_sources(authority, con, actor, workspace_id, source_page_id):
    workspace, root = _owner(authority, con, actor, workspace_id, source_page_id)
    page_move._authorize(authority, con, actor, workspace_id, source_page_id, "view")
    rows = con.execute(
        "WITH RECURSIVE tree(id) AS (SELECT ? UNION SELECT d.id "
        "FROM workspace_collaboration_documents d JOIN tree t ON d.parent_document_id=t.id "
        "WHERE d.workspace_id=? AND d.archived=0) SELECT d.* "
        "FROM workspace_collaboration_documents d JOIN tree t ON t.id=d.id LIMIT ?",
        (source_page_id, workspace_id, MAX_SOURCE_DOCUMENTS + 1),
    ).fetchall()
    if len(rows) > MAX_SOURCE_DOCUMENTS:
        raise ValueError("page copy exceeds the subtree limit")
    ids = {row["id"] for row in rows}
    if root["parent_document_id"] in ids:
        raise ValueError("cyclic page hierarchy")
    children = {}
    for row in rows:
        _owner(authority, con, actor, workspace_id, row["id"])
        page_move._authorize(authority, con, actor, workspace_id, row["id"], "view")
        children.setdefault(row["parent_document_id"], []).append(row)
    for items in children.values():
        items.sort(key=lambda row: (row["position"], row["id"]))
    ordered, stack = [], [root]
    while stack:
        row = stack.pop()
        ordered.append(row)
        stack.extend(reversed(children.get(row["id"], [])))
    if len(ordered) != len(rows):
        raise ValueError("page copy hierarchy is inconsistent")
    parent = None
    if root["parent_document_id"]:
        parent = page_move._authorize(authority, con, actor, workspace_id, root["parent_document_id"], "edit_content")
        page_move._unlocked(con, parent, actor)
    return workspace, ordered, parent


def _workspace_stamp(workspace):
    return (int(workspace["security_epoch"]), int(workspace["row_version"]))


def _document_stamp(con, row):
    return page_move._stamp(row, _generation(con, row["id"]))


def revalidate_copy_source(authority, con, prepared: PreparedPageCopy):
    """Recheck inside the caller's transaction immediately before copy inserts."""
    if not con.in_transaction:
        raise ValueError("copy source validation requires a transaction")
    workspace, rows, parent = _ordered_sources(authority, con, prepared.actor,
        prepared.workspace_id, prepared.source_page_id)
    if (_workspace_stamp(workspace) != prepared.workspace_stamp
            or tuple(row["id"] for row in rows) != tuple(item.document_id for item in prepared.documents)):
        raise CopySourceChanged("page copy scope changed; prepare it again")
    for row, item in zip(rows, prepared.documents):
        if _document_stamp(con, row) != item.stamp:
            raise CopySourceChanged("page copy content changed; prepare it again")
    if (parent["id"] if parent else None) != prepared.parent_id:
        raise CopySourceChanged("page copy destination changed; prepare it again")
    if parent is not None and _document_stamp(con, parent) != prepared.parent_stamp:
        raise CopySourceChanged("page copy parent changed; prepare it again")
    return workspace, rows, parent


def _verify_blobs(authority, sources):
    checked = set()
    for source in sources:
        for item in json.loads(source.files_json):
            identity = (item["content_sha256"], item["blob_relative_path"], item["size_bytes"])
            if identity in checked:
                continue
            try:
                candidate, relative = authority._blob_path(item["content_sha256"])
                if item["blob_relative_path"] != relative or candidate.is_symlink() or not candidate.is_file():
                    raise ValueError("invalid copy source blob")
                size, digest = 0, hashlib.sha256()
                with candidate.open("rb") as stream:
                    while chunk := stream.read(1024 * 1024):
                        size += len(chunk)
                        if size > item["size_bytes"]:
                            raise ValueError("copy source blob size mismatch")
                        digest.update(chunk)
                if size != item["size_bytes"] or "sha256:" + digest.hexdigest() != item["content_sha256"]:
                    raise ValueError("copy source blob checksum mismatch")
                checked.add(identity)
            except (OSError, ValueError) as exc:
                raise AuthorityUnavailable("a page copy attachment is unavailable") from exc


def _exclude_archived_row_only_files(con, source: CopySourceDocument) -> CopySourceDocument:
    """Retain live shared files without copying Trash-only provenance."""
    from . import database_trash
    from .database_move import _file_ids

    held = database_trash.trashed_file_ids(con, source.document_id)
    if not held:
        return source
    descriptor = source.descriptor()
    active = _file_ids({key: value for key, value in descriptor.items()
        if key != "files"})
    if source.rich_text_json:
        active.update(_file_ids(json.loads(source.rich_text_json)))
    hidden = held - active
    if not hidden:
        return source
    files = json.loads(source.files_json)
    descriptor["files"] = [item for item in descriptor.get("files", [])
        if item.get("id") not in hidden]
    return replace(source, descriptor_json=_canonical_json(descriptor),
        files_json=_canonical_json([item for item in files
            if item["id"] not in hidden]))


def prepare_page_copy(authority, actor_subject_id, workspace_id, source_page_id, *,
                      expected_document_sequence, expected_workspace_epoch, expected_document_epoch):
    actor = authority._require_active(actor_subject_id)
    workspace_id = _identifier(workspace_id, field="workspace_id")
    source_page_id = _identifier(source_page_id, field="source_page_id")
    sequence = _positive(expected_document_sequence, field="expected_document_sequence")
    workspace_epoch = _positive(expected_workspace_epoch, field="expected_workspace_epoch")
    document_epoch = _positive(expected_document_epoch, field="expected_document_epoch")
    sources, updates, post_frame_inserts, typed_insert_ids, witnesses, size = [], [], {}, {}, {}, 0
    with authority._reader() as con:
        con.execute("BEGIN")
        workspace, rows, parent = _ordered_sources(authority, con, actor, workspace_id, source_page_id)
        root = rows[0]
        if workspace["security_epoch"] != workspace_epoch or root["access_epoch"] != document_epoch:
            raise EpochConflict(workspace["security_epoch"], root["access_epoch"])
        if root["document_sequence"] != sequence:
            raise DocumentSequenceConflict(root["document_sequence"])
        workspace_stamp = _workspace_stamp(workspace)
        parent_id, parent_stamp = (parent["id"], _document_stamp(con, parent)) if parent is not None else (None, None)
        for row in rows:
            stamp = _document_stamp(con, row)
            descriptor = _canonical_json(authority._editing_snapshot(con, row))
            files = _canonical_json([dict(item) for item in con.execute(
                "SELECT * FROM workspace_collaboration_files WHERE document_id=? AND state='active' ORDER BY id",
                (row["id"],),
            ).fetchall()])
            frames = _frames(con, row["id"], stamp[-1])
            witness = page_move.parent_rebase.capture_witness(con, row["id"], stamp[-1], json.loads(descriptor))
            witnesses[row["id"]] = {"id": row["id"], "generation": stamp[-1],
                "frames": frames, "snapshot": json.loads(descriptor), **witness}
            last_frame_sequence = witness["post_frame_structure"]["through_document_sequence"]
            typed_inserts = [(str(item[0]), int(item[1])) for item in con.execute(
                "SELECT target_id,document_sequence FROM workspace_collaboration_typed_commands "
                "WHERE document_id=? AND command_kind='block.insert'",
                (row["id"],),
            )]
            typed_insert_ids[row["id"]] = {identity for identity, _ in typed_inserts}
            post_frame_inserts[row["id"]] = {identity for identity, sequence in typed_inserts
                                              if sequence > last_frame_sequence}
            size += (len(descriptor.encode("utf-8")) + len(files.encode("utf-8")) + sum(map(len, frames))
                     + len(_canonical_json(witness).encode("utf-8")))
            if size > MAX_SOURCE_BYTES:
                raise ValueError("page copy source exceeds the preparation budget")
            sources.append(CopySourceDocument(row["id"], row["parent_document_id"], int(row["position"]),
                stamp, descriptor, files))
            updates.append(frames)
    # No SQLite reader or writer remains open while Node replays updates or the
    # filesystem verifies immutable blobs. The final writer must validate again.
    for witness in witnesses.values():
        finalize_witness(witness["post_frame_structure"], witness["snapshot"], witness["id"], witness["generation"])
    current = search_projection.materialize(updates)
    documents = []
    for source, projected in zip(sources, current):
        descriptor = source.descriptor()
        if projected["title"] is not None:
            descriptor["title"] = projected["title"]
        if projected["blocks"] is not None:
            canonical = descriptor["blocks"]
            replayed = projected["blocks"]
            canonical_ids = [block["id"] for block in canonical]
            replayed_ids = [block["id"] for block in replayed]
            missing = set(canonical_ids) - set(replayed_ids)
            witness = witnesses[source.document_id]
            structure = witness["post_frame_structure"]
            historical_order_differs = (structure["historical_move_ids"]
                and [key for key in canonical_ids if key in set(replayed_ids)]
                != [key for key in replayed_ids if key in set(canonical_ids)])
            if (structure["commands"] or structure.get("restores") or set(structure["retired_ids"]) & set(replayed_ids)
                    or historical_order_differs
                    or needs_recovery(witness, replayed_ids, canonical_ids)):
                try:
                    descriptor["blocks"], _title, _inserts = page_move.parent_rebase.rebased_projection(witness, projected)
                except AuthorityUnavailable as exc:
                    raise CopySourceChanged("canonical and collaborative copy source structures differ") from exc
            elif (missing and missing == post_frame_inserts[source.document_id]
                    and [identity for identity in canonical_ids if identity not in missing] == replayed_ids
                    and len(set(replayed_ids)) == len(replayed_ids)):
                replayed_by_id = {block["id"]:block for block in replayed}
                descriptor["blocks"] = [replayed_by_id.get(block["id"],
                    {key:value for key,value in block.items() if key != "position"}) for block in canonical]
            elif ((missing & typed_insert_ids[source.document_id])
                    or post_frame_inserts[source.document_id]) and canonical_ids != replayed_ids:
                raise CopySourceChanged("canonical and collaborative copy source structures differ")
            else:
                # Live CRDT-only edits can legitimately lead the SQL snapshot.
                descriptor["blocks"] = replayed
        rich_text_json = page_copy_rich.capture(witnesses[source.document_id], descriptor)
        size += len(rich_text_json.encode('utf-8'))
        if size > MAX_SOURCE_BYTES:
            raise ValueError('page copy source exceeds the preparation budget')
        documents.append(CopySourceDocument(source.document_id, source.parent_id, source.position,
            source.stamp, _canonical_json(descriptor), source.files_json, rich_text_json))
    prepared = PreparedPageCopy(actor, workspace_id, source_page_id, workspace_stamp,
        tuple(documents), parent_id, parent_stamp)
    with authority._reader() as con:
        con.execute("BEGIN")
        revalidate_copy_source(authority, con, prepared)
        filtered = tuple(_exclude_archived_row_only_files(con, source)
            for source in prepared.documents)
    prepared = replace(prepared, documents=filtered)
    _verify_blobs(authority, prepared.documents)
    with authority._reader() as con:
        con.execute("BEGIN")
        revalidate_copy_source(authority, con, prepared)
    return prepared
