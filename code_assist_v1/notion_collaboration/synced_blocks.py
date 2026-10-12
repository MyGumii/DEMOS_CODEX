"""Explicit synced originals/instances and a current-authority read boundary.

Legacy sync_id-only blocks have no reliable original identity; adoption must
resolve that before using this model.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json

from .canonical_authority import NotFoundOrDenied, _canonical_json, _identifier, _stable_id
from . import page_move, search_projection
from .page_copy_source import MAX_SOURCE_BYTES
from .title_projection import _frames, _generation
from .synced_tree import content_tree

CONTRACT = "code_assist.notion.synced_block.v1"


class SyncedShapeUnavailable(ValueError):
    """The block does not have a complete explicit original/instance identity."""


class SyncedSourceChanged(RuntimeError):
    """Retry the read from current instance and original snapshots."""


def _exact_id(value, field):
    try:
        if not isinstance(value, str) or _identifier(value, field=field) != value:
            raise ValueError("noncanonical identity")
    except ValueError as exc:
        raise SyncedShapeUnavailable("invalid synced " + field) from exc
    return value


def origin_of(block, document_id):
    """None marks an original; an instance contains only its target and layout."""
    if block.get("type") != "synced_block":
        raise SyncedShapeUnavailable("a synced block is required")
    own = (_exact_id(document_id, "document_id"), _exact_id(block.get("id"), "block_id"))
    props = block.get("props", {})
    if not isinstance(props, dict) or "synced_from" not in props:
        raise SyncedShapeUnavailable("legacy synced identity requires an explicit original")
    reference = props["synced_from"]
    if reference is None:
        return own
    if not isinstance(reference, dict) or set(reference) != {"document_id", "block_id"}:
        raise SyncedShapeUnavailable("invalid synced original reference")
    target = (_exact_id(reference["document_id"], "document_id"), _exact_id(reference["block_id"], "block_id"))
    if target == own:
        raise SyncedShapeUnavailable("a synced instance cannot reference itself")
    # An instance must not become an independently editable/stale content copy.
    if block.get("content", "") != "" or props.get("rich_text_html") or props.get("children"):
        raise SyncedShapeUnavailable("a synced instance cannot store original content")
    return target


def group_id(document_id, block_id):
    return _stable_id("sync", CONTRACT, _exact_id(document_id, "document_id"), _exact_id(block_id, "block_id"))


def index_blocks(blocks):
    """Index the document body, including inline child descriptors."""
    indexed, stack = {}, list(reversed(blocks))
    while stack:
        block = stack.pop()
        if not isinstance(block, dict):
            raise SyncedShapeUnavailable("invalid nested block")
        identity = _exact_id(block.get("id"), "block_id")
        if identity in indexed:
            raise SyncedShapeUnavailable("duplicate synced source block identity")
        indexed[identity] = block
        children = block.get("props", {}).get("children", [])
        if children and all(isinstance(child, dict) for child in children):
            stack.extend(reversed(children))
        elif children and not all(isinstance(child, str) for child in children):
            raise SyncedShapeUnavailable("mixed nested block representation")
    return indexed


@dataclass(frozen=True)
class BodyLocation:
    """Storage location; the public reference remains a document/block pair."""
    kind: str
    database_id: str = ""
    row_id: str = ""
    template_id: str = ""

    def payload(self):
        return {key: value for key, value in vars(self).items() if value}


def body_entries(descriptor):
    """Yield visible bodies without collapsing their independent ID namespaces.

    The editing snapshot has already excluded trashed databases/rows and rows
    promoted to pages. Archived templates are still present in that snapshot.
    """
    yield BodyLocation("page"), descriptor["blocks"]
    for database in descriptor.get("databases", []):
        if database.get("state") == "archived":
            continue
        database_id = _exact_id(database.get("id"), "database_id")
        for row in database.get("rows", []):
            if row.get("state") == "archived" or "body" not in row:
                continue
            body = row["body"]
            if not isinstance(body, dict) or set(body) != {"blocks"} or not isinstance(body["blocks"], list):
                raise SyncedShapeUnavailable("typed database item body required")
            yield BodyLocation("row", database_id, _exact_id(row.get("id"), "row_id")), body["blocks"]
        for template in database.get("templates", []):
            if template.get("state") == "archived":
                continue
            body = template.get("body", [])
            if not isinstance(body, list):
                raise SyncedShapeUnavailable("typed database template body required")
            yield BodyLocation("template", database_id, template_id=_exact_id(template.get("id"), "template_id")), body


def locate_block(descriptor, block_id):
    """Resolve an exact pair only when it names one visible body location.

    Reused ordinary block IDs in separate bodies are allowed. A public synced
    endpoint cannot select one of those bodies by guessing which copy was meant.
    """
    matches = []
    for location, blocks in body_entries(descriptor):
        block = index_blocks(blocks).get(block_id)
        if block is not None:
            matches.append((location, blocks, block))
    if not matches:
        raise NotFoundOrDenied("not found or permission denied")
    if len(matches) != 1:
        raise SyncedShapeUnavailable("ambiguous synced body identity")
    return matches[0]


@dataclass(frozen=True)
class _Source:
    document_id: str
    stamp: tuple
    descriptor_json: str
    size: int
    frames: tuple[bytes, ...]
    main_structure_verified: bool = True


def authorize_edit_locations(authority, con, actor, workspace_id, document_id, locations):
    """Enforce the strongest owning-body role without granting page authority."""
    minimum = "edit" if any(location.kind == "template" for location in locations) else "edit_content"
    page = page_move._authorize(authority, con, actor, workspace_id, document_id, minimum)
    page_move._unlocked(con, page, actor)
    epochs = {}
    from . import database_access
    for location in locations:
        if location.kind == "page":
            continue
        authority._database_for_document(con, document_id, location.database_id)
        if database_access.installed(con):
            access = database_access.resolve_on_connection(authority, con, actor, workspace_id, location.database_id)
            required = "edit" if location.kind == "template" else "edit_content"
            if database_access.PAGE_ROLE_LEVEL[access.role] < database_access.PAGE_ROLE_LEVEL[required]:
                raise NotFoundOrDenied("not found or permission denied")
            epochs[location.database_id] = access.database_epoch
    return epochs


def _authorize(authority, con, actor, document_id):
    authority._require_active(actor)
    workspace, document, _role = authority._authorized_document(con, actor, document_id)
    if document["archived"]:
        raise NotFoundOrDenied("not found or permission denied")
    stamp = (workspace["id"], workspace["security_epoch"], workspace["row_version"],
        *page_move._stamp(document, _generation(con, document_id)))
    return document, stamp


def _current(authority, actor, document_id, remaining, workspace_id=None, *, strict_structure=False, include_title=False):
    with authority._reader() as con:
        con.execute("BEGIN")
        document, stamp = _authorize(authority, con, actor, document_id)
        if workspace_id is not None and stamp[0] != workspace_id:
            raise NotFoundOrDenied("not found or permission denied")
        descriptor = _canonical_json(authority._editing_snapshot(con, document))
        frames = _frames(con, document_id, stamp[-1])
        # Typed block commands update the canonical SQL body but do not append
        # a Yjs frame. Capture only inserts newer than the last frame, so a
        # detach can reconcile those exact blocks without discarding them.
        last_frame_sequence = con.execute(
            "SELECT COALESCE(MAX(update_row.document_sequence),0) "
            "FROM workspace_collaboration_crdt_updates update_row "
            "JOIN workspace_collaboration_crdt_update_bindings binding "
            "ON binding.update_id=update_row.id "
            "WHERE binding.document_id=? AND binding.generation=?",
            (document_id, stamp[-1]),
        ).fetchone()[0]
        post_frame_inserts = {str(row[0]) for row in con.execute(
            "SELECT target_id FROM workspace_collaboration_typed_commands "
            "WHERE document_id=? AND document_sequence>? AND command_kind='block.insert'",
            (document_id, last_frame_sequence),
        )}
        size = len(descriptor.encode("utf-8")) + sum(map(len, frames))
        if size > remaining:
            raise SyncedShapeUnavailable("synced source exceeds the preparation budget")
    # Node must not hold either a SQLite reader or writer open.
    projected = search_projection.materialize([frames])[0]
    current = json.loads(descriptor)
    if include_title and projected['title'] is not None:
        current['title'] = projected['title']
    main_structure_verified = True
    if projected["blocks"] is not None:
        canonical = current["blocks"]
        replayed = projected["blocks"]
        canonical_ids = [block["id"] for block in canonical]
        replayed_ids = [block["id"] for block in replayed]
        if canonical_ids == replayed_ids:
            current["blocks"] = replayed
        else:
            replayed_by_id = {block["id"]:block for block in replayed}
            missing = set(canonical_ids) - set(replayed_ids)
            # A canonical-only insertion is safe to rebase only when every
            # other Yjs block is still present in the same relative order.
            if (missing and missing == post_frame_inserts
                    and [identity for identity in canonical_ids if identity not in missing] == replayed_ids
                    and len(replayed_by_id) == len(replayed_ids)):
                current["blocks"] = [replayed_by_id.get(block["id"],
                    {key:value for key,value in block.items() if key != "position"}) for block in canonical]
            elif strict_structure:
                # A mutation must not overwrite a canonical block merely
                # because a collaborative frame failed to include it.
                raise SyncedShapeUnavailable("canonical and collaborative block structures differ")
            else:
                # Ordinary synced reads retain the existing live Yjs view;
                # a CRDT-only structural edit may legitimately lead SQL.
                current["blocks"] = replayed
                main_structure_verified = False
    descriptor = _canonical_json(current)
    size = max(size, len(descriptor.encode("utf-8")))
    if size > remaining:
        raise SyncedShapeUnavailable("synced source exceeds the preparation budget")
    return _Source(document_id, stamp, descriptor, size, tuple(frames), main_structure_verified)


def _resolve(authority, actor, document_id, block_id, workspace_id=None, *, strict_structure=False,
             allow_database_bodies=False):
    """Prepare the pair outside writers; callers must recheck all stamps."""
    instance = _current(authority, actor, document_id, MAX_SOURCE_BYTES, workspace_id,
                        strict_structure=strict_structure)
    instance_location, _blocks, instance_block = locate_block(json.loads(instance.descriptor_json), block_id)
    origin_document, origin_block = origin_of(instance_block, document_id)
    sources = [instance]
    source = instance
    if origin_document != document_id:
        source = _current(authority, actor, origin_document, MAX_SOURCE_BYTES - instance.size,
                          strict_structure=strict_structure)
        sources.append(source)
    origin_location, blocks, original = locate_block(json.loads(source.descriptor_json), origin_block)
    if strict_structure and not allow_database_bodies and (instance_location.kind != "page" or origin_location.kind != "page"):
        # The existing mutation delta edits the main page Yjs body. It must not
        # manufacture a main-body copy of a row/template-owned original.
        raise SyncedShapeUnavailable("database body synced mutation requires a scoped writer")
    if origin_of(original, origin_document) != (origin_document, origin_block):
        raise SyncedShapeUnavailable("a synced reference must target an original")
    try:
        tree = content_tree(blocks, origin_block)
    except ValueError as exc:
        raise SyncedShapeUnavailable("synced child hierarchy is unavailable") from exc
    return sources, original, tree, origin_document, origin_block


def read_synced_block(authority, actor_subject_id, document_id, block_id, *, workspace_id=None):
    """Read current original content after reauthorizing both documents."""
    actor = authority._require_active(actor_subject_id)
    document_id = _exact_id(document_id, "document_id")
    block_id = _exact_id(block_id, "block_id")
    if workspace_id is not None:
        workspace_id = _exact_id(workspace_id, "workspace_id")
    sources, original, tree, origin_document, origin_block = _resolve(authority, actor, document_id, block_id, workspace_id)
    descriptors = {source.document_id: json.loads(source.descriptor_json) for source in sources}
    instance_location = locate_block(descriptors[document_id], block_id)[0]
    origin_location = locate_block(descriptors[origin_document], origin_block)[0]
    editable = origin_location.kind != "page" or all(source.main_structure_verified for source in sources)
    locations = {}
    for document, location in ((document_id, instance_location), (origin_document, origin_location)):
        locations.setdefault(document, []).append(location)
    with authority._reader() as con:
        con.execute("BEGIN")
        for source in sources:
            _document, stamp = _authorize(authority, con, actor, source.document_id)
            if stamp != source.stamp:
                raise SyncedSourceChanged("synced content changed during preparation")
            try:
                authorize_edit_locations(authority, con, actor, stamp[0], source.document_id, locations[source.document_id])
            except PermissionError:
                editable = False
    return {"contract": CONTRACT, "instance": {"document_id": document_id, "block_id": block_id},
        "origin": {"document_id": origin_document, "block_id": origin_block},
        "sync_id": group_id(origin_document, origin_block), "block": deepcopy(original), "tree": tree,
        "locations": {"instance": instance_location.payload(), "origin": origin_location.payload()},
        "capabilities": {"edit_text": editable},
        "witnesses": [{"document_id": item.document_id, "workspace_id": item.stamp[0],
            "workspace_epoch": item.stamp[1], "document_sequence": item.stamp[4],
            "document_epoch": item.stamp[5], "generation": item.stamp[6]} for item in sources]}
