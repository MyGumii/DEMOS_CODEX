"""Independently verify page move/copy/archive/restore parent link deltas.

The authorized reader captures the structural witness. Replay runs outside SQLite;
the owning service must still revalidate its source stamps before committing.
Owned database callers use their exact current block/source identity as well.
"""
from .canonical_authority import AuthorityUnavailable
from .page_creation_parent_rebase import capture_insert_ids, capture_witness, rebased_projection, validate_structure_flag
from . import search_projection
from .parent_restore_recovery import validate_rich


def _matches(block, identity, kind="child_page"):
    if kind == "child_page":
        return block["type"] == "child_page" and block["props"].get("page_id") == identity
    if kind == "owned_database":
        from .page_copy_remap import DATABASE_BLOCKS
        return (block["type"] in DATABASE_BLOCKS and not block["props"].get("linked_database")
                and (block["props"].get("database_id") or block["id"]) == identity)
    raise AuthorityUnavailable("unsupported parent link kind")


def _validate(source, delta, child_id, old, updated):
    base, title, reconciled = rebased_projection(source, old)
    validate_structure_flag(source, delta, old, base)
    validate_rich(source, delta["payload"])
    if (source.get("match_kind", "child_page") not in {"child_page", "owned_database"}
            or set(delta.get("reconciled_insert_ids", ())) != reconciled):
        raise AuthorityUnavailable("page parent insert proof differs")
    if "insert_many" in source:
        _validate_batch(source, delta, base, title, updated)
        return
    identity = source.get("match_id", child_id)
    matches = [block for block in base if _matches(block, identity, source.get("match_kind", "child_page"))]
    matched_ids = {block["id"] for block in matches}
    keep = matches[0] if source.get("relocate") and matches else None
    keep_id = keep["id"] if keep else None
    base_ids = [block["id"] for block in base]
    removed = set(delta["removed"])
    # The Yjs map can also contain unordered links. These may be cleaned up,
    # but every visible deletion must be an exact current link match.
    if removed & set(base_ids) != matched_ids - ({keep_id} if keep else set()):
        raise AuthorityUnavailable("page link deletion differs from current body")
    restore_anchor = None
    if matches:
        at = base_ids.index(matches[0]["id"])
        restore_anchor = {
            "previous_block_id": next((key for key in reversed(base_ids[:at]) if key not in matched_ids), None),
            "next_block_id": next((key for key in base_ids[at + 1:] if key not in matched_ids), None),
            "index": at,
        }
    expected = [block for block in base if block["id"] not in matched_ids]
    placed, after, inserted = None, None, False
    if source["insert"]:
        at = len(expected)
        if source.get("relative_page_id"):
            anchor = next((index for index, block in enumerate(expected)
                           if _matches(block, source["relative_page_id"])), None)
            if anchor is None:
                raise AuthorityUnavailable("page position anchor is no longer present")
            at = anchor + (source.get("relative_position") == "after")
        elif source.get("restore_anchor"):
            anchor = source["restore_anchor"]
            ids = [block["id"] for block in expected]
            if anchor["next_block_id"] in ids:
                at = ids.index(anchor["next_block_id"])
            elif anchor["previous_block_id"] in ids:
                at = ids.index(anchor["previous_block_id"]) + 1
            else:
                at = max(0, min(len(expected), anchor["index"]))
        block = keep or {key: source["insert"][key] for key in ("id", "type", "content", "props")}
        placed, inserted = block["id"], keep is None
        canonical_ids = {block["id"] for block in source["snapshot"]["blocks"]}
        after = next((block["id"] for block in reversed(expected[:at])
                      if block["id"] in canonical_ids), None)
        expected.insert(at, block)
    elif keep:
        # No owning service uses relocation without an insertion target.
        raise AuthorityUnavailable("page relocation requires a target link")
    changed = bool(reconciled or delta.get("reconciled_structure_changed") or old["blocks"] != base or removed
                   or [block["id"] for block in expected] != base_ids)
    if (delta["placed"] != placed or delta["inserted"] != inserted
            or delta["after_block_id"] != after or delta["restore_anchor"] != restore_anchor
            or delta["placed_many"] is not None or delta["changed"] != changed):
        raise AuthorityUnavailable("page link placement differs from current body")
    if updated["blocks"] != expected or updated["title"] != title:
        raise AuthorityUnavailable("page link changed unrelated parent content")


def _validate_batch(source, delta, base, title, updated):
    """Verify the exact selected child links and their structural anchor chain."""
    links = source["insert_many"]
    if (source.get("match_kind", "child_page") != "child_page" or source["insert"]
            or source.get("relocate") or source.get("relative_page_id") or source.get("restore_anchor")
            or not isinstance(links, list) or not 1 <= len(links) <= 100):
        raise AuthorityUnavailable("unsupported batch child links")
    existing_ids = {block["id"] for block in base}
    existing_pages = {block["props"]["page_id"] for block in base if block["type"] == "child_page"
                      and isinstance(block["props"].get("page_id"), str)}
    canonical_ids = {block["id"] for block in source["snapshot"]["blocks"]}
    after = next((block["id"] for block in reversed(base) if block["id"] in canonical_ids), None)
    inserted, placed = [], []
    for link in links:
        if (link["type"] != "child_page" or link["id"] in existing_ids
                or not isinstance(link["content"], str) or not isinstance(link["props"].get("page_id"), str)
                or not link["props"]["page_id"] or link["props"]["page_id"] in existing_pages):
            raise AuthorityUnavailable("batch child link identity differs")
        inserted.append({key: link[key] for key in ("id", "type", "content", "props")})
        placed.append({"id": link["id"], "after_block_id": after, "page_id": link["props"]["page_id"]})
        existing_ids.add(link["id"])
        existing_pages.add(link["props"]["page_id"])
        after = link["id"]
    if (delta["removed"] or delta["placed"] is not None or delta["inserted"]
            or delta["after_block_id"] is not None or delta["restore_anchor"] is not None
            or delta["placed_many"] != placed or not delta["changed"]):
        raise AuthorityUnavailable("batch link placement differs from current body")
    if updated["blocks"] != [*base, *inserted] or updated["title"] != title:
        raise AuthorityUnavailable("batch links changed unrelated parent content")


def validate_deltas(sources, deltas, child_id):
    """Check the old and new streams in one bounded independent replay batch."""
    if not sources:
        return
    documents = []
    for key, source in sources.items():
        documents.extend((source["frames"], [*source["frames"], deltas[key]["payload"]]))
    projections = search_projection.materialize(documents)
    for index, (key, source) in enumerate(sources.items()):
        _validate(source, deltas[key], child_id, *projections[index * 2:index * 2 + 2])
