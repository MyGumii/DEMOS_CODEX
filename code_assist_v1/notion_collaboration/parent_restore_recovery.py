"""Capture exact immutable deletion ranges for current-tail identity restoration."""
import base64
import hashlib
import json

from .canonical_authority import AuthorityUnavailable
from .durable_transport import YJS_UPDATE_ENCODING

CONTRACT = "code_assist.notion.parent_restore_recovery.v1"
MAX_RECOVERY_BYTES = 8 * 1024 * 1024
MAX_RECOVERY_FRAMES = 4096
MAX_RESTORES = 256


class RecoveryBudgetExceeded(AuthorityUnavailable):
    """The exact range cannot be prepared within the restoration budget."""


def frame_bytes(source):
    return sum(int(item["frame_bytes"]) for item in source.get("post_frame_structure", {}).get("restores", []))


def frames_at(con, document_id, generation, through):
    """Read only checksumed bound frames at/before the recorded deletion range."""
    if not con.in_transaction or type(through) is not int or through < 0:
        raise ValueError("authorized recovery snapshot and valid range required")
    snapshot = con.execute("SELECT s.through_update_sequence,s.snapshot_sha256,length(s.snapshot) AS size,"
        "CASE WHEN length(s.snapshot)<=? THEN s.snapshot END AS snapshot FROM workspace_collaboration_crdt_snapshots s "
        "JOIN workspace_collaboration_crdt_snapshot_bindings b ON b.snapshot_id=s.id "
        "WHERE b.document_id=? AND b.generation=? AND s.through_update_sequence<=? "
        "ORDER BY s.through_update_sequence DESC LIMIT 1", (MAX_RECOVERY_BYTES, document_id, generation, through)).fetchone()
    if snapshot is not None and snapshot["snapshot"] is None:
        raise RecoveryBudgetExceeded("parent rich recovery snapshot budget exceeded")
    after = 0 if snapshot is None else int(snapshot["through_update_sequence"])
    query = ("FROM workspace_collaboration_crdt_updates u JOIN workspace_collaboration_crdt_update_bindings b ON b.update_id=u.id "
        "WHERE b.document_id=? AND b.generation=? AND u.update_sequence>? AND u.update_sequence<=? ORDER BY u.update_sequence LIMIT ?")
    params = (document_id, generation, after, through, MAX_RECOVERY_FRAMES + 1)
    sizes = con.execute("SELECT length(u.payload) " + query, params).fetchall()
    size = sum(int(row[0]) for row in sizes) + (len(snapshot["snapshot"]) if snapshot is not None else 0)
    if len(sizes) > MAX_RECOVERY_FRAMES or size > MAX_RECOVERY_BYTES:
        raise RecoveryBudgetExceeded("parent rich recovery requires a smaller exact range")
    frames = []
    def checked(payload, checksum):
        value = bytes(payload)
        if 'sha256:' + hashlib.sha256(value).hexdigest() != checksum:
            raise AuthorityUnavailable("parent rich recovery checksum differs")
        frames.append({"payload": base64.b64encode(value).decode('ascii'), "sha256": checksum})
    if snapshot is not None:
        checked(snapshot["snapshot"], snapshot["snapshot_sha256"])
    high_water = after
    for row in con.execute("SELECT u.update_sequence,u.payload,u.payload_sha256,u.payload_encoding " + query, params):
        if row["payload_encoding"] != YJS_UPDATE_ENCODING:
            raise AuthorityUnavailable("parent rich recovery encoding differs")
        high_water = int(row["update_sequence"])
        checked(row["payload"], row["payload_sha256"])
    if high_water != through:
        raise AuthorityUnavailable("parent rich recovery range is incomplete")
    return frames, size


def capture(con, document_id, generation, row):
    if not con.in_transaction:
        raise ValueError("parent restoration capture requires an authorized reader")
    payload, result = json.loads(row["payload_json"]), json.loads(row["result_json"])
    deletion = con.execute("SELECT id,document_sequence,result_json FROM workspace_collaboration_typed_commands "
        "WHERE document_id=? AND target_id=? AND command_kind='block.delete' AND document_sequence<? "
        "ORDER BY document_sequence DESC LIMIT 1", (document_id, row["target_id"], row["document_sequence"])).fetchone()
    if deletion is None or result.get("restored_deletion_command_id") != deletion["id"]:
        raise AuthorityUnavailable("parent restoration deletion binding differs")
    deleted = json.loads(deletion["result_json"])
    recovery = deleted["recovery"]
    rich = recovery["rich_text_recovery"]
    if (rich["crdt_generation"] != generation or recovery["block"]["id"] != row["target_id"]
            or recovery["block"]["version"] != deleted["target_version"]
            or result["target_version"] != deleted["target_version"] + 1):
        raise AuthorityUnavailable("parent restoration generation/version differs")
    frames, size = frames_at(con, document_id, generation, rich["through_update_sequence"])
    values = {"id": row["target_id"], "type": str(payload.get("type") or "paragraph").strip(),
        "content": str(payload.get("content") or ""), "props": payload.get("props", {})}
    return {"contract": CONTRACT, "document_id": document_id, "generation": generation,
        "command_id": row["id"], "document_sequence": int(row["document_sequence"]), "target_id": row["target_id"],
        "deletion_command_id": deletion["id"], "deletion_document_sequence": int(deletion["document_sequence"]),
        "deleted_version": int(deleted["target_version"]), "restored_version": int(result["target_version"]),
        "through_update_sequence": rich["through_update_sequence"], "values": values,
        "frames": frames, "frame_bytes": size}


def validate_entry(source, entry):
    restores = source["post_frame_structure"].get("restores", [])
    values = [item for item in restores if item["command_id"] == entry["command_id"]]
    if len(values) != 1:
        raise AuthorityUnavailable("parent restoration witness is missing")
    proof = values[0]
    if (proof["contract"] != CONTRACT or proof["document_id"] != source["id"] or proof["generation"] != source["generation"]
            or proof["target_id"] != entry["target_id"] or proof["document_sequence"] != entry["document_sequence"]
            or proof["deletion_document_sequence"] >= proof["document_sequence"]
            or proof["restored_version"] != proof["deleted_version"] + 1):
        raise AuthorityUnavailable("parent restoration witness differs")
    structure = source["post_frame_structure"]
    if ("through_document_sequence" in structure and proof.get("historical", False)
            is not (proof["document_sequence"] <= structure["through_document_sequence"])):
        raise AuthorityUnavailable("parent restoration frame boundary differs")
    bindings = structure.get("historical_structure", {}).get("restore_bindings", [])
    matches = [item for item in bindings if item["command_id"] == entry["command_id"]]
    if matches and (len(matches) != 1 or matches[0] != {key: value for key, value in proof.items()
            if key not in {"frames", "frame_bytes", "historical"}}):
        raise AuthorityUnavailable("parent historical restoration binding differs")
    return proof


def commands(source):
    """Use the certified complete history and current tail once per command."""
    structure = source.get("post_frame_structure", {})
    complete = structure.get("historical_structure", {}).get("commands", [])
    entries = {item["command_id"]: item for item in [*complete, *structure.get("commands", [])]}
    return sorted(entries.values(), key=lambda item: item["document_sequence"])


def keep_current(proof, current, archived, identity, vector):
    """Keep later text only when restoration content/identity explains it."""
    if current is None:
        return False
    if current == proof["values"]:
        return True
    if proof.get("historical") is not True:
        return False
    if archived == proof["values"]:
        return True  # Original-value restore retains later edits on its old map.
    if identity is not None and identity["clock"] >= vector.get(str(identity["client"]), 0):
        return True  # The integrated map was created after the deletion archive.
    raise AuthorityUnavailable("historical replacement has an unexplained older map")


def projection(source, blocks):
    """Prepare exact restored fields after independently decoding recovery ranges."""
    from . import search_projection
    restorations = {}
    for entry in commands(source):
        if entry["restore_deleted"]:
            restorations[entry["target_id"]] = validate_entry(source, entry)
    if not restorations:
        return blocks
    canonical = {block["id"]: {key: value for key, value in block.items() if key != "position"}
        for block in source["snapshot"]["blocks"]}
    items = [item for identity, item in restorations.items() if identity in canonical]
    current, *archives = search_projection.materialize([source["frames"],
        *[[base64.b64decode(frame["payload"], validate=True) for frame in item["frames"]] for item in items]], identities=True)
    visible = {block["id"]: block for block in current["blocks"] or []}
    replacements = {}
    for item, decoded in zip(items, archives):
        identity = item["target_id"]
        archived = next((block for block in decoded["blocks"] or [] if block["id"] == identity), None)
        if item["frames"] and archived is None:
            raise AuthorityUnavailable("deleted rich parent body is unavailable")
        if canonical[identity] != item["values"]:
            raise AuthorityUnavailable("restored parent canonical fields changed")
        if not keep_current(item, visible.get(identity), archived, current["block_identities"].get(identity), decoded["state_vector"]):
            replacements[identity] = item["values"]
    return [replacements.get(block["id"], block) for block in blocks]


def validate_rich(source, payload):
    """Independently check attributed restored text and every surviving text run."""
    from . import search_projection
    proof = source.get("post_frame_structure", {})
    restores = proof.get("restores", [])
    if not restores:
        return
    latest = {item["target_id"]: item for item in restores}
    canonical = {block["id"] for block in source["snapshot"]["blocks"]}
    items = [item for identity, item in latest.items() if identity in canonical]
    projections = search_projection.materialize([source["frames"], [*source["frames"], payload],
        *[[base64.b64decode(frame["payload"], validate=True) for frame in item["frames"]] for item in items]], rich=True, identities=True)
    old, updated, *archives = projections
    old_blocks = {block["id"]: block for block in old["blocks"] or []}
    expected = dict(old["rich_text"]["blocks"])
    for item, archived in zip(items, archives):
        identity = item["target_id"]
        block = next((block for block in archived["blocks"] or [] if block["id"] == identity), None)
        if keep_current(item, old_blocks.get(identity), block, old["block_identities"].get(identity), archived["state_vector"]):
            continue
        expected[identity] = archived["rich_text"]["blocks"][identity] if block == item["values"] else (
            [{"insert": item["values"]["content"]}] if item["values"]["content"] else [])
    current = updated["rich_text"]
    if old["rich_text"]["title"] is not None and old["rich_text"]["title"] != current["title"]:
        raise AuthorityUnavailable("parent restoration changed title formatting")
    for identity, delta in current["blocks"].items():
        if identity in expected and delta != expected[identity]:
            raise AuthorityUnavailable("parent restoration changed unrelated or restored text formatting")
