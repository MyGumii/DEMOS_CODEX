"""Capture/replay proven parent structural edits and validate native child append.

Capture uses the caller's authorized snapshot. Independent replay precedes any
writer, whose existing source stamp/authority checks remain mandatory.
Page lifecycle callers reuse the witness/base through page_link_parent_rebase.
"""
import json

from .canonical_authority import AuthorityUnavailable
from . import search_projection, parent_historical_structure, parent_restore_recovery


def capture_insert_ids(con, document_id, generation, snapshot):
    if not con.in_transaction:
        raise ValueError("parent insert capture requires a snapshot")
    sequence = con.execute(
        "SELECT COALESCE(MAX(u.document_sequence),0) FROM workspace_collaboration_crdt_updates u "
        "JOIN workspace_collaboration_crdt_update_bindings b ON b.update_id=u.id "
        "WHERE b.document_id=? AND b.generation=?", (document_id, generation),
    ).fetchone()[0]
    current = {block["id"] for block in snapshot["blocks"]}
    return tuple(sorted({row[0] for row in con.execute(
        "SELECT target_id FROM workspace_collaboration_typed_commands "
        "WHERE document_id=? AND document_sequence>? AND command_kind='block.insert'",
        (document_id, sequence),
    ) if row[0] in current}))


STRUCTURE_CONTRACT = "code_assist.notion.parent_structure_witness.v1"
MAX_STRUCTURAL_COMMANDS = 4096


def capture_witness(con, document_id, generation, snapshot):
    """Bind the exact immutable structural tail in the authorized reader.

    This is internal preparation data, never a client-supplied repair request.
    A bounded immutable anchor can also certify older deletion/order divergence.
    Current-tail restoration binds its separate exact rich recovery range.
    """
    if not con.in_transaction:
        raise ValueError("parent structure capture requires a snapshot")
    through = int(con.execute(
        "SELECT COALESCE(MAX(u.document_sequence),0) FROM workspace_collaboration_crdt_updates u "
        "JOIN workspace_collaboration_crdt_update_bindings b ON b.update_id=u.id "
        "WHERE b.document_id=? AND b.generation=?", (document_id, generation),
    ).fetchone()[0])
    rows = con.execute(
        "SELECT id,document_sequence,command_kind,target_id,payload_json,result_json "
        "FROM workspace_collaboration_typed_commands WHERE document_id=? AND document_sequence>? "
        "AND command_kind IN ('block.insert','block.delete','block.move') "
        "ORDER BY document_sequence LIMIT ?", (document_id, through, MAX_STRUCTURAL_COMMANDS + 1),
    ).fetchall()
    if len(rows) > MAX_STRUCTURAL_COMMANDS:
        raise AuthorityUnavailable("parent structural tail requires compaction")
    commands, restores, recovery_bytes = [], [], 0
    for row in rows:
        payload = json.loads(row["payload_json"])
        if payload.get("restore_deleted") is True:
            recovery = parent_restore_recovery.capture(con, document_id, generation, row)
            restores.append(recovery)
            recovery_bytes += recovery["frame_bytes"]
            if len(restores) > parent_restore_recovery.MAX_RESTORES or recovery_bytes > parent_restore_recovery.MAX_RECOVERY_BYTES:
                raise AuthorityUnavailable("parent restoration preparation budget exceeded")
        if row["command_kind"] == "block.delete":
            result = json.loads(row["result_json"])
            if result["recovery"]["rich_text_recovery"]["crdt_generation"] != generation:
                raise AuthorityUnavailable("parent deletion generation differs")
        commands.append({"command_id": str(row["id"]), "document_sequence": int(row["document_sequence"]),
            "kind": str(row["command_kind"]), "target_id": str(row["target_id"]),
            "after_block_id": payload.get("after_block_id"), "restore_deleted": payload.get("restore_deleted") is True})
    current = {block["id"] for block in snapshot["blocks"]}
    retired = sorted(str(row[0]) for row in con.execute(
        "SELECT block_id FROM workspace_collaboration_block_tombstones WHERE document_id=?",
        (document_id,)) if row[0] not in current)
    moved = sorted({str(row[0]) for row in con.execute(
        "SELECT target_id FROM workspace_collaboration_typed_commands WHERE document_id=? "
        "AND document_sequence<=? AND command_kind='block.move'", (document_id, through)) if row[0] in current})
    if len(retired) > 100000:
        raise AuthorityUnavailable("parent deletion history requires compaction")
    historical = parent_historical_structure.capture(con, document_id, generation, snapshot)
    if historical is not None:
        recovered = {item["command_id"]: item for item in restores}
        for item in historical.pop("_restore_ranges", []):
            recovered.setdefault(item["command_id"], item)
        combined = list(recovered.values())
        if (len(combined) > parent_restore_recovery.MAX_RESTORES
                or sum(item["frame_bytes"] for item in combined) > parent_restore_recovery.MAX_RECOVERY_BYTES):
            historical = None  # Preserve the independently bounded current tail.
        else:
            restores = combined
    restores.sort(key=lambda item: item["document_sequence"])
    for item in restores:
        item["historical"] = item["document_sequence"] <= through
    return {"post_frame_insert_ids": tuple(sorted({item["target_id"] for item in commands
                if item["kind"] == "block.insert" and item["target_id"] in current})),
        "post_frame_structure": {"contract": STRUCTURE_CONTRACT, "document_id": document_id,
            "generation": generation, "through_document_sequence": through, "commands": commands,
            "retired_ids": retired, "historical_move_ids": moved,
            **({"restores": restores} if restores else {}),
            **({"historical_structure": historical} if historical is not None else {})}}


def _structural_order(source, original_ids, canonical_ids):
    proof = source["post_frame_structure"]
    if (proof["contract"] != STRUCTURE_CONTRACT or proof["document_id"] != source["id"]
            or proof["generation"] != source["generation"] or len(proof["commands"]) > MAX_STRUCTURAL_COMMANDS):
        raise AuthorityUnavailable("parent structural witness differs")
    ordered = original_ids[:]
    sequence = proof["through_document_sequence"]
    commands_seen = set()
    for item in proof["commands"]:
        kind, identity, anchor = item["kind"], item["target_id"], item["after_block_id"]
        if (item["command_id"] in commands_seen or item["document_sequence"] <= sequence
                or kind not in {"block.insert", "block.delete", "block.move"}):
            raise AuthorityUnavailable("parent structural history requires recovery")
        if item["restore_deleted"]:
            if kind != "block.insert":
                raise AuthorityUnavailable("parent restoration kind differs")
            parent_restore_recovery.validate_entry(source, item)
        commands_seen.add(item["command_id"])
        sequence = item["document_sequence"]
        if kind == "block.delete":
            if identity in ordered:
                ordered.remove(identity)
            continue
        if kind == "block.insert" and identity in ordered:
            continue  # A complete frame may already include this insertion.
        if kind == "block.move":
            if identity not in ordered:
                raise AuthorityUnavailable("parent move target requires recovery")
            ordered.remove(identity)
        if anchor is not None and anchor not in ordered:
            raise AuthorityUnavailable("parent structural anchor requires recovery")
        ordered.insert(0 if anchor is None else ordered.index(anchor) + 1, identity)
    if proof["commands"] and ordered != canonical_ids:
        raise AuthorityUnavailable("parent structural history does not explain the current order")
    return ordered


def rebased_projection(source, old):
    """Return the current body plus exactly proven inserts, without link changes."""
    canonical = [{key: value for key, value in block.items() if key != "position"}
                 for block in source["snapshot"]["blocks"]]
    original = old["blocks"]
    if original is None:
        if source["frames"]:
            raise AuthorityUnavailable("native parent has no complete collaborative body")
        expected = canonical
        title = source["snapshot"]["title"] or None
        reconciled = set()
    else:
        original_ids = [block["id"] for block in original]
        canonical_ids = [block["id"] for block in canonical]
        if len(set(original_ids)) != len(original_ids) or len(set(canonical_ids)) != len(canonical_ids):
            raise AuthorityUnavailable("native parent has ambiguous block identities")
        missing = set(canonical_ids) - set(original_ids)
        allowed = parent_historical_structure.allowed_insert_ids(source) - set(original_ids)
        allowed &= set(canonical_ids)
        proof = source.get("post_frame_structure", {})
        historical_repair = parent_historical_structure.needs_recovery(source, original_ids, canonical_ids)
        if historical_repair or proof.get("commands"):
            if historical_repair:
                parent_historical_structure.recovery_order(source, original_ids, canonical_ids)
            else:
                _structural_order(source, original_ids, canonical_ids)
            if missing != allowed:
                raise AuthorityUnavailable("parent insert history differs from current body")
            current = {block["id"]: block for block in original}
            expected = [current.get(block["id"], block) for block in canonical]
        elif missing:
            if missing != allowed or [identity for identity in canonical_ids if identity not in missing] != original_ids:
                raise AuthorityUnavailable("native parent block structure requires reconciliation")
            current = {block["id"]: block for block in original}
            expected = [current.get(block["id"], block) for block in canonical]
        else:
            expected = original
        proof = source.get("post_frame_structure")
        if proof and not proof["commands"] and not historical_repair:
            # Older structural divergence has no current-tail repair proof.
            # Keep it out of a new durable link delta instead of resurrecting it.
            if set(proof["retired_ids"]) & set(original_ids):
                raise AuthorityUnavailable("older parent deletion requires recovery")
            common = set(canonical_ids) & set(original_ids)
            if (proof["historical_move_ids"] and [key for key in canonical_ids if key in common]
                    != [key for key in original_ids if key in common]):
                raise AuthorityUnavailable("older parent order requires recovery")
        title = old["title"]
        reconciled = missing
    expected = parent_restore_recovery.projection(source, expected)
    return expected, title, reconciled


def validate_structure_flag(source, delta, old, base):
    if "post_frame_structure" in source:
        changed = old["blocks"] is not None and [block["id"] for block in old["blocks"]] != [block["id"] for block in base]
        if delta.get("reconciled_structure_changed") is not changed:
            raise AuthorityUnavailable("parent structural change proof differs")


def validate_projection(source, delta):
    """Decode both streams without a reader and reject any unrelated mutation."""
    old, updated = search_projection.materialize([source["frames"], [*source["frames"], delta["payload"]]])
    expected, title, reconciled = rebased_projection(source, old)
    validate_structure_flag(source, delta, old, expected)
    if set(delta.get("reconciled_insert_ids", ())) != reconciled:
        raise AuthorityUnavailable("native parent insert proof differs")
    expected = [*expected, source["insert"]]
    if updated["blocks"] != expected or updated["title"] != title:
        raise AuthorityUnavailable("native child link changed unrelated parent content")
    parent_restore_recovery.validate_rich(source, delta["payload"])
