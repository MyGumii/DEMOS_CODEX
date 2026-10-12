"""Read-only preparation for explicitly selected legacy synced endpoints.

Legacy groups have no trustworthy original marker. A caller must name the
original and every reference being considered. This module neither discovers an
original nor writes operating data; a later atomic writer must revalidate the
immutable preparation in its transaction.
"""
from __future__ import annotations

from dataclasses import dataclass
import json

from . import page_move, synced_blocks as synced
from .canonical_authority import _canonical_json, _sha256
from .page_copy_source import MAX_SOURCE_BYTES, MAX_SOURCE_DOCUMENTS, _owner
from .synced_detach_delta import _frames_digest
from .synced_tree import content_tree
from .synced_legacy_adoption_provenance import original_provenance
from .title_projection import _frames

CONTRACT = "code_assist.notion.synced_legacy_adoption_prepare.v1"
# These fields describe the endpoint's surrounding placement, not shared text.
PLACEMENT_FIELDS = frozenset({"indent", "layout_group", "layout_column", "tab_group", "tab_id"})


@dataclass(frozen=True)
class AdoptionDocument:
    source: synced._Source
    blocks_json: str
    frames_sha256: str

    def blocks(self):
        return json.loads(self.blocks_json)


@dataclass(frozen=True)
class PreparedLegacyAdoption:
    actor: str
    workspace_id: str
    origin: tuple[str, str]
    references: tuple[tuple[str, str], ...]
    documents: tuple[AdoptionDocument, ...]
    payload_json: str
    payload_sha256: str
    provenance_json: str | None = None

    def value(self):
        return json.loads(self.payload_json)


def _endpoint(value):
    if not isinstance(value, dict) or set(value) != {"document_id", "block_id"}:
        raise ValueError("an explicit document and block endpoint is required")
    return (synced._exact_id(value["document_id"], "document_id"),
            synced._exact_id(value["block_id"], "block_id"))


def _selection(origin, references):
    selected = _endpoint(origin)
    if not isinstance(references, list) or len(references) >= MAX_SOURCE_DOCUMENTS:
        raise ValueError("zero to 127 explicitly selected references are required")
    targets = tuple(sorted(_endpoint(item) for item in references))
    if len(set(targets)) != len(targets) or selected in targets:
        raise ValueError("a legacy endpoint may only be selected once")
    return selected, targets


def _authorize(authority, con, actor, workspace_id, source):
    _owner(authority, con, actor, workspace_id, source.document_id)
    page, stamp = synced._authorize(authority, con, actor, source.document_id)
    page_move._authorize(authority, con, actor, workspace_id, source.document_id, "edit_content")
    page_move._unlocked(con, page, actor)
    if stamp != source.stamp:
        raise synced.SyncedSourceChanged("legacy synced endpoint changed during preparation")
    if _frames_digest(_frames(con, source.document_id, stamp[-1])) != _frames_digest(source.frames):
        raise synced.SyncedSourceChanged("legacy synced endpoint frames changed during preparation")


def _legacy_block(source, identity):
    block, signature = _flat_block(source, identity, explicit_original=False)
    return block, synced._exact_id(block["props"].get("sync_id"), "sync_id"), signature


def _original_block(source, identity):
    return _flat_block(source, identity, explicit_original=True)


def _flat_block(source, identity, *, explicit_original):
    blocks = json.loads(source.descriptor_json)["blocks"]
    # Item/template bodies and inline descriptors require their own scope model.
    block = next((item for item in blocks if item.get("id") == identity), None)
    if block is None:
        raise synced.SyncedShapeUnavailable("legacy endpoint is not in the document body")
    props = block.get("props", {})
    if block.get("type") != "synced_block" or not isinstance(props, dict):
        raise synced.SyncedShapeUnavailable("selected endpoint is not a synced block")
    if explicit_original:
        if ("synced_from" not in props or props["synced_from"] is not None
                or props.get("sync_id") != synced.group_id(source.document_id, identity)):
            raise synced.SyncedShapeUnavailable("selected endpoint is not the designated original")
    elif "synced_from" in props:
        raise synced.SyncedShapeUnavailable("selected endpoint is not a legacy synced block")
    try:
        tree = content_tree(blocks, identity)
    except ValueError as exc:
        raise synced.SyncedShapeUnavailable("legacy synced hierarchy is unavailable") from exc
    if props.get("children") or tree["children"]:
        raise synced.SyncedShapeUnavailable("legacy nested content needs its explicit adoption contract")
    content = block.get("content", "")
    if not isinstance(content, str):
        raise synced.SyncedShapeUnavailable("legacy synced content must be text")
    signature = _canonical_json({"content": content,
        "props": {key: value for key, value in props.items()
                  if key not in {"sync_id", "synced_from"} and key not in PLACEMENT_FIELDS}})
    return block, signature


def _materialize(actor, workspace_id, origin, references, sources, provenance_json=None):
    indexed = {source.document_id: source for source in sources}
    if provenance_json is None:
        origin_block, legacy_id, signature = _legacy_block(indexed[origin[0]], origin[1])
    else:
        if not references:
            raise synced.SyncedShapeUnavailable("select at least one legacy reference for an existing original")
        proof = json.loads(provenance_json)
        if (proof.get("actor_subject_id") != actor or proof.get("workspace_id") != workspace_id
                or proof.get("origin") != {"document_id": origin[0], "block_id": origin[1]}):
            raise ValueError("original designation proof scope differs")
        legacy_id = synced._exact_id(proof.get("legacy_sync_id"), "legacy_sync_id")
        origin_block, signature = _original_block(indexed[origin[0]], origin[1])
    group = synced.group_id(*origin)
    output = {source.document_id: json.loads(source.descriptor_json)["blocks"] for source in sources}
    selected = (origin, *references) if provenance_json is None else references
    for endpoint in selected:
        block, candidate_id, candidate_signature = _legacy_block(indexed[endpoint[0]], endpoint[1])
        if candidate_id != legacy_id:
            raise synced.SyncedShapeUnavailable("selected endpoints belong to different legacy groups")
        if candidate_signature != signature:
            # Adoption is never permission to discard a divergent member's text.
            raise synced.SyncedShapeUnavailable("legacy synced content differs; resolve it before adoption")
        replacement = next(item for item in output[endpoint[0]] if item["id"] == endpoint[1])
        props = replacement["props"]
        props["sync_id"] = group
        props["synced_from"] = None if endpoint == origin else {
            "document_id": origin[0], "block_id": origin[1]}
        if endpoint != origin:
            replacement["content"] = ""
            props.pop("rich_text_html", None)
    documents = tuple(AdoptionDocument(source, _canonical_json(output[source.document_id]),
                        _frames_digest(source.frames)) for source in sources)
    value = {"contract": CONTRACT, "commit_ready": False,
        "scope": "explicitly_selected_flat_document_endpoints",
        "legacy_sync_id": legacy_id, "sync_id": group,
        "origin": {"document_id": origin[0], "block_id": origin[1]},
        "references": [{"document_id": doc, "block_id": block} for doc, block in references],
        "original_content_sha256": _sha256(signature),
        "documents": [{"document_id": item.source.document_id,
                       "witness": list(item.source.stamp),
                       "source_descriptor_sha256": _sha256(item.source.descriptor_json),
                       "source_frames_sha256": item.frames_sha256,
                       "blocks_sha256": _sha256(item.blocks_json)} for item in documents]}
    if provenance_json is not None:
        value["origin_adoption"] = _sha256(provenance_json)
    payload = _canonical_json(value)
    if sum(len(item.blocks_json.encode("utf-8")) for item in documents) + len(payload.encode("utf-8")) > MAX_SOURCE_BYTES:
        raise synced.SyncedShapeUnavailable("legacy adoption output exceeds the preparation budget")
    return PreparedLegacyAdoption(actor, workspace_id, origin, references, documents, payload, _sha256(payload), provenance_json)


def revalidate_legacy_adoption(authority, con, prepared):
    """Recheck all endpoints in the caller's transaction; never replay Yjs here."""
    if not con.in_transaction:
        raise ValueError("legacy adoption validation requires a transaction")
    if (not isinstance(prepared, PreparedLegacyAdoption)
            or _sha256(prepared.payload_json) != prepared.payload_sha256):
        raise ValueError("immutable legacy adoption preparation is required")
    expected_ids = tuple(sorted({doc for doc, _block in (prepared.origin, *prepared.references)}))
    if tuple(item.source.document_id for item in prepared.documents) != expected_ids:
        raise ValueError("legacy adoption source scope was modified")
    rebuilt = _materialize(prepared.actor, prepared.workspace_id, prepared.origin,
                           prepared.references, tuple(item.source for item in prepared.documents), prepared.provenance_json)
    if rebuilt != prepared:
        raise ValueError("legacy adoption preparation was modified")
    for item in prepared.documents:
        _authorize(authority, con, prepared.actor, prepared.workspace_id, item.source)
    if prepared.provenance_json is not None:
        current = original_provenance(authority, con, prepared.actor, prepared.workspace_id,
            prepared.origin, legacy_id=prepared.value()["legacy_sync_id"])
        if current != prepared.provenance_json:
            raise synced.SyncedSourceChanged("original designation changed after review")


def prepare_legacy_adoption(authority, actor_subject_id, workspace_id, *, origin, references):
    """Prepare only the caller's explicit selection, without writing any state.

    Unselected members, including archived/unreadable members, are not converted
    or claimed as a discovered complete group. Their adoption needs a separate
    explicit selection. The separately gated review API/UI owns dispatch; this
    preparation never performs an automatic migration.
    """
    actor = authority._require_active(actor_subject_id)
    workspace_id = synced._exact_id(workspace_id, "workspace_id")
    selected, targets = _selection(origin, references)
    documents = tuple(sorted({doc for doc, _block in (selected, *targets)}))
    with authority._reader() as con:
        con.execute("BEGIN")
        for document in documents:
            _owner(authority, con, actor, workspace_id, document)
            page = page_move._authorize(authority, con, actor, workspace_id, document, "edit_content")
            page_move._unlocked(con, page, actor)
    sources, remaining = [], MAX_SOURCE_BYTES
    for document in documents:
        source = synced._current(authority, actor, document, remaining, workspace_id, strict_structure=True)
        sources.append(source)
        remaining -= source.size
    indexed = {item.document_id: item for item in sources}
    original_blocks = json.loads(indexed[selected[0]].descriptor_json)["blocks"]
    original = next((item for item in original_blocks if item["id"] == selected[1]), None)
    proof = None
    if original is not None and original.get("props", {}).get("synced_from", False) is None:
        if not targets:
            raise synced.SyncedShapeUnavailable("select at least one legacy reference for an existing original")
        _block, legacy_id, _signature = _legacy_block(indexed[targets[0][0]], targets[0][1])
        with authority._reader() as con:
            con.execute("BEGIN")
            proof = original_provenance(authority, con, actor, workspace_id, selected, legacy_id=legacy_id)
    prepared = _materialize(actor, workspace_id, selected, targets, tuple(sources), proof)
    with authority._reader() as con:
        con.execute("BEGIN")
        revalidate_legacy_adoption(authority, con, prepared)
    return prepared
