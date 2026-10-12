"""Bounded, current-generation structural certificates from immutable history.

This reader-only certificate permits proven older deletion/order/restoration.
It does not infer a full save or an unexplained CRDT-only structure.
"""
import json
import re

from .canonical_authority import AuthorityUnavailable, _canonical_json, _sha256
from . import parent_restore_recovery

CONTRACT = "code_assist.notion.parent_historical_structure.v1"
MAX_COMMANDS = 4096
MAX_ANCHOR_BYTES = 2 * 1024 * 1024
MAX_HISTORY_BYTES = 8 * 1024 * 1024
MAX_BLOCKS = 100000


def _ids(values):
    if (not isinstance(values, list) or len(values) > MAX_BLOCKS
            or any(not isinstance(value, str) or not value for value in values)
            or len(set(values)) != len(values)):
        raise AuthorityUnavailable("historical parent identities differ")
    return values[:]


def certified_order(proof, document_id, generation, canonical_ids):
    """Validate the entire anchor-to-current history; return affected identities."""
    anchor = proof["anchor"]
    if (proof["contract"] != CONTRACT or proof["document_id"] != document_id
            or proof["generation"] != generation or type(anchor["document_sequence"]) is not int
            or anchor["document_sequence"] < 1 or type(anchor["revision"]) is not int
            or anchor["revision"] < 1 or not isinstance(anchor["checkpoint_id"], str)
            or not anchor["checkpoint_id"] or not isinstance(anchor["content_sha256"], str)
            or re.fullmatch(r"sha256:[a-f0-9]{64}", anchor["content_sha256"]) is None
            or type(proof["through_document_sequence"]) is not int
            or proof["through_document_sequence"] < anchor["document_sequence"]
            or not isinstance(proof["commands"], list) or len(proof["commands"]) > MAX_COMMANDS):
        raise AuthorityUnavailable("historical parent scope differs")
    ordered = _ids(anchor["block_ids"])
    known, positioned, deleted, inserted, seen = set(ordered), set(), set(), set(), set()
    last_deletions = {}
    recovery_source = {"id": document_id, "generation": generation,
        "post_frame_structure": {"restores": proof.get("restore_bindings", [])}}
    sequence = anchor["document_sequence"]
    for item in proof["commands"]:
        kind, identity, after = item["kind"], item["target_id"], item["after_block_id"]
        if (not isinstance(item["command_id"], str) or not item["command_id"] or item["command_id"] in seen
                or type(item["document_sequence"]) is not int or not sequence < item["document_sequence"] <= proof["through_document_sequence"]
                or kind not in {"block.insert", "block.delete", "block.move"}
                or not isinstance(identity, str) or not identity or type(item["restore_deleted"]) is not bool
                or (after is not None and (not isinstance(after, str) or not after))):
            raise AuthorityUnavailable("historical parent command differs")
        seen.add(item["command_id"])
        sequence = item["document_sequence"]
        if kind == "block.insert":
            if item["restore_deleted"]:
                restoration = parent_restore_recovery.validate_entry(recovery_source, item)
                deletion = last_deletions.get(identity)
                if (identity not in known or identity in ordered or deletion is None
                        or deletion["command_id"] != restoration["deletion_command_id"]
                        or deletion["document_sequence"] != restoration["deletion_document_sequence"]
                        or deletion.get("target_version") != restoration["deleted_version"]):
                    raise AuthorityUnavailable("historical restoration deletion/version differs")
            elif identity in known:
                raise AuthorityUnavailable("historical parent identity restoration requires recovery")
            known.add(identity)
            positioned.add(identity)
            inserted.add(identity)
        else:
            if item["restore_deleted"]:
                raise AuthorityUnavailable("historical restoration kind differs")
            if identity not in ordered:
                raise AuthorityUnavailable("historical parent target differs")
            ordered.remove(identity)
            if kind == "block.delete":
                deleted.add(identity)
                last_deletions[identity] = item
                continue
            positioned.add(identity)
        if after is not None and after not in ordered:
            raise AuthorityUnavailable("historical parent anchor differs")
        ordered.insert(0 if after is None else ordered.index(after) + 1, identity)
    if ordered != _ids(canonical_ids):
        raise AuthorityUnavailable("historical parent commands do not explain canonical order")
    return known, positioned, deleted, inserted


def recovery_order(source, original_ids, canonical_ids):
    """Authorize only the identity/order changes explained by the certificate."""
    proof = source["post_frame_structure"]["historical_structure"]
    known, positioned, deleted, inserted = certified_order(proof, source["id"], source["generation"], canonical_ids)
    original_ids = _ids(original_ids)
    original, canonical = set(original_ids), set(canonical_ids)
    if not original <= known or not (original - canonical) <= deleted:
        raise AuthorityUnavailable("historical parent projected identities are unexplained")
    # A unique, never-restored insertion can use current canonical values just as
    # the current-tail contract does. Existing/orphan rich maps are not replaced.
    allowed = (set(source["post_frame_insert_ids"]) | inserted) & canonical
    if canonical - original != allowed - original:
        raise AuthorityUnavailable("historical parent missing content requires recovery")
    stable = (original & canonical) - positioned
    if [key for key in original_ids if key in stable] != [key for key in canonical_ids if key in stable]:
        raise AuthorityUnavailable("historical parent untouched order differs")
    return canonical_ids[:]


def allowed_insert_ids(source):
    """Response envelope bound; independent replay still validates exact IDs."""
    allowed = set(source.get("post_frame_insert_ids", ()))
    proof = source.get("post_frame_structure", {}).get("historical_structure")
    if proof:
        allowed.update(item["target_id"] for item in proof["commands"] if item["kind"] == "block.insert")
    return allowed


def needs_recovery(source, original_ids, canonical_ids):
    """Leave legitimate CRDT-only ahead/explicit-empty compatibility untouched."""
    structure = source.get("post_frame_structure", {})
    proof = structure.get("historical_structure")
    if proof is None:
        return False
    original, canonical = set(original_ids), set(canonical_ids)
    common = original & canonical
    deleted = {item["target_id"] for item in proof["commands"] if item["kind"] == "block.delete"} - canonical
    return bool((set(structure["retired_ids"]) | deleted) & original
        or structure["historical_move_ids"] and [key for key in original_ids if key in common]
            != [key for key in canonical_ids if key in common]
        or (canonical - original) & {item["target_id"] for item in proof["commands"] if item["kind"] == "block.insert"})


def finalize_witness(structure, snapshot, document_id, generation):
    """Certify the captured candidate only after the authorized reader closes."""
    proof = structure.get("historical_structure")
    if proof is None:
        return
    try:
        certified_order(proof, document_id, generation, [block["id"] for block in snapshot["blocks"]])
    except AuthorityUnavailable:
        # Full saves/other domain histories cannot be guessed from this ledger.
        # The previous exact current-tail path remains available independently.
        structure.pop("historical_structure")
        # An uncertified older restoration cannot become standalone repair proof.
        if "restores" in structure:
            structure["restores"] = [item for item in structure["restores"] if not item.get("historical")]


def _database_archive_commands(con, document_id, anchor_sequence, through, command_count, used_bytes):
    """Normalize checksum-bound immutable DB Trash receipts as ordered deletes.

    Historical DB restores need their placement/content contract and are refused.
    Current restore requests still use database_trash's exact snapshot/anchor.
    """
    query = ("FROM workspace_collaboration_events e WHERE e.document_id=? "
        "AND e.document_sequence>? AND e.document_sequence<=? "
        "AND e.event_type IN ('database.trashed','database.restored') ORDER BY e.document_sequence LIMIT ?")
    params = (document_id, anchor_sequence, through, MAX_COMMANDS + 1)
    sizes = con.execute("SELECT length(CAST(e.payload_json AS BLOB)) " + query, params).fetchall()
    if command_count + len(sizes) > MAX_COMMANDS or used_bytes + sum(int(item[0]) for item in sizes) > MAX_HISTORY_BYTES:
        return None
    events = con.execute("SELECT e.* " + query, params).fetchall()
    commands = []
    for event in events:
        action = "collaboration.database_trash" if event["event_type"] == "database.trashed" else "collaboration.database_restore"
        event_bytes = len(event["payload_json"].encode("utf-8"))
        receipt = con.execute("SELECT workspace_id,document_id,request_sha256,"
            "length(CAST(result_json AS BLOB)) AS size,"
            "CASE WHEN length(CAST(result_json AS BLOB))<=? THEN result_json END AS result_json "
            "FROM workspace_collaboration_operations "
            "WHERE actor_subject_id=? AND action=? AND operation_id=?",
            (max(0, MAX_HISTORY_BYTES - used_bytes - event_bytes), event["actor_subject_id"], action, event["operation_id"])).fetchone()
        if receipt is None:
            raise AuthorityUnavailable("historical database receipt is missing")
        used_bytes += int(receipt["size"]) + event_bytes
        if used_bytes > MAX_HISTORY_BYTES or receipt["result_json"] is None:
            return None
        try:
            payload, result = json.loads(event["payload_json"]), json.loads(receipt["result_json"])
        except (TypeError, ValueError) as exc:
            raise AuthorityUnavailable("historical database receipt is invalid") from exc
        if not isinstance(payload, dict) or not isinstance(result, dict):
            raise AuthorityUnavailable("historical database receipt is invalid")
        fields = ("contract", "workspace_id", "document_id", "document_sequence", "database_id", "block_id", "archived",
            "workspace_epoch", "document_epoch")
        if (payload.get("contract") != "code_assist.notion.owned_database_trash.v1"
                or payload.get("document_id") != document_id or receipt["document_id"] != document_id
                or payload.get("workspace_id") != event["workspace_id"] or receipt["workspace_id"] != event["workspace_id"]
                or payload.get("document_sequence") != event["document_sequence"]
                or result.get("event_id") != event["id"] or result.get("request_sha256") != receipt["request_sha256"]
                or result.get("operation_id") != event["operation_id"]
                or any(payload.get(key) != result.get(key) for key in fields)
                or not isinstance(payload.get("block_id"), str) or not payload["block_id"]
                or payload.get("archived") is not (event["event_type"] == "database.trashed")):
            raise AuthorityUnavailable("historical database receipt differs")
        if event["event_type"] == "database.restored":
            return None
        commands.append({"command_id": "event:" + event["id"], "document_sequence": int(event["document_sequence"]),
            "kind": "block.delete", "target_id": payload["block_id"], "after_block_id": None,
            "restore_deleted": False, "origin": "owned_database_trash"})
    return commands


def capture(con, document_id, generation, snapshot):
    """Capture a checksumed generation anchor and bounded exact typed history.

    Unsupported or over-budget histories leave the existing current-tail path
    available. A corrupt immutable anchor fails rather than being trusted.
    """
    if not con.in_transaction:
        raise ValueError("historical capture requires an authorized snapshot")
    row = con.execute(
        "SELECT c.id,c.revision,c.document_sequence,c.content_sha256 AS checkpoint_hash,"
        "r.content_sha256,CASE WHEN length(CAST(r.snapshot_json AS BLOB))<=? THEN r.snapshot_json END AS snapshot_json "
        "FROM workspace_collaboration_revision_checkpoints c JOIN workspace_collaboration_revisions r "
        "ON r.document_id=c.document_id AND r.revision=c.revision "
        "AND r.document_sequence=c.document_sequence "
        "WHERE c.document_id=? AND c.crdt_generation=? ORDER BY c.document_sequence,c.revision LIMIT 1",
        (MAX_ANCHOR_BYTES, document_id, generation),
    ).fetchone()
    if row is None or row["snapshot_json"] is None:
        return None
    try:
        anchor = json.loads(row["snapshot_json"])
        if (_sha256(_canonical_json(anchor)) != row["content_sha256"]
                or row["content_sha256"] != row["checkpoint_hash"]):
            raise AuthorityUnavailable("historical parent anchor hash differs")
        block_ids = _ids([block["id"] for block in anchor["blocks"]])
    except (ValueError, TypeError, KeyError) as exc:
        raise AuthorityUnavailable("historical parent anchor is invalid") from exc
    through = int(con.execute("SELECT document_sequence FROM workspace_collaboration_documents WHERE id=?",
        (document_id,)).fetchone()[0])
    query = (
        "FROM workspace_collaboration_typed_commands WHERE document_id=? AND document_sequence>? "
        "AND document_sequence<=? AND command_kind IN ('block.insert','block.delete','block.move') "
        "ORDER BY document_sequence LIMIT ?")
    params = (document_id, row["document_sequence"], through, MAX_COMMANDS + 1)
    sizes = con.execute("SELECT length(CAST(payload_json AS BLOB))+length(CAST(result_json AS BLOB)) "
        + query, params).fetchall()
    history_bytes = sum(int(item[0]) for item in sizes)
    if len(sizes) > MAX_COMMANDS or history_bytes > MAX_HISTORY_BYTES:
        return None
    rows = con.execute(
        "SELECT id,document_sequence,command_kind,target_id,payload_json,result_json "
        + query, params,
    ).fetchall()
    commands, restores, recovery_bytes = [], [], 0
    for item in rows:
        payload = json.loads(item["payload_json"])
        result = json.loads(item["result_json"])
        if payload.get("restore_deleted") is True:
            try:
                restoration = parent_restore_recovery.capture(con, document_id, generation, item)
            except parent_restore_recovery.RecoveryBudgetExceeded:
                return None
            restores.append(restoration)
            recovery_bytes += restoration["frame_bytes"]
            if len(restores) > parent_restore_recovery.MAX_RESTORES or recovery_bytes > parent_restore_recovery.MAX_RECOVERY_BYTES:
                return None
        if item["command_kind"] == "block.delete":
            recovery = result["recovery"]["rich_text_recovery"]
            if recovery["crdt_generation"] != generation:
                return None
        commands.append({"command_id": str(item["id"]), "document_sequence": int(item["document_sequence"]),
            "kind": str(item["command_kind"]), "target_id": str(item["target_id"]),
            "after_block_id": payload.get("after_block_id"), "restore_deleted": payload.get("restore_deleted") is True,
            **({"target_version": result["target_version"]} if item["command_kind"] == "block.delete" else {})})
    archive_commands = _database_archive_commands(con, document_id, int(row["document_sequence"]),
        through, len(commands), history_bytes)
    if archive_commands is None:
        return None
    commands.extend(archive_commands)
    commands.sort(key=lambda item: item["document_sequence"])
    proof = {"contract": CONTRACT, "document_id": document_id, "generation": generation,
        "through_document_sequence": through, "anchor": {"checkpoint_id": str(row["id"]),
            "revision": int(row["revision"]), "document_sequence": int(row["document_sequence"]),
            "content_sha256": str(row["content_sha256"]), "block_ids": block_ids}, "commands": commands}
    if restores:
        proof["restore_bindings"] = [{key: value for key, value in item.items() if key not in {"frames", "frame_bytes"}}
            for item in restores]
        proof["_restore_ranges"] = restores
    return proof
