"""Atomic collaborative page moves, including parent links and access fencing.

Node reconciles proven structural history and prepares links outside the transaction. The service then
revalidates the exact sources before committing metadata, structural commands,
durable deltas, receipts, audit and outbox together. No legacy store is written.
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

from .canonical_authority import (
    AuthorityUnavailable, NotFoundOrDenied, _canonical_json, _identifier,
    _operation_id, _positive, _request_sha256, _sha256, _stable_id,
)
from .durable_transport import YJS_UPDATE_ENCODING
from .editing_authority import DocumentSequenceConflict, EDITING_AUTHORITY_CONTRACT
from .iam_authority import PAGE_ROLE_LEVEL
from .selected_runtime_contract import runtime_binding_sha256
from .title_projection import _frames, _generation, MAX_DOCUMENT_BYTES
from . import native_team_location as team_location
from . import page_link_parent_rebase as parent_rebase
from .parent_historical_structure import allowed_insert_ids, finalize_witness
from .parent_restore_recovery import frame_bytes as restore_frame_bytes

CONTRACT = "code_assist.notion.page_move.v1"
DELTA_CONTRACT = "code_assist.notion.page_move_delta.v1"
ACTION = "collaboration.page_move"


def _prepare_deltas(sources, child_id, operation):
    documents = []
    for source in sources.values():
        if "post_frame_structure" in source:
            finalize_witness(source["post_frame_structure"], source["snapshot"], source["id"], source["generation"])
        if sum(map(len, source["frames"])) + restore_frame_bytes(source) > MAX_DOCUMENT_BYTES:
            raise AuthorityUnavailable("page link replay requires compaction")
        documents.append({
            "document_id": source["id"], "generation": source["generation"],
            "child_id": child_id, "seed": source["snapshot"],
            "match_kind": source.get("match_kind", "child_page"),
            "match_id": source.get("match_id", child_id),
            "expected_match_count": source.get("expected_match_count"),
            "insert": source["insert"],
            **({"insert_many": source["insert_many"]} if "insert_many" in source else {}),
            **({"post_frame_insert_ids": list(source["post_frame_insert_ids"])} if "post_frame_insert_ids" in source else {}),
            **({"post_frame_structure": source["post_frame_structure"]} if "post_frame_structure" in source else {}),
            "relocate": source.get("relocate", False),
            "relative_page_id": source.get("relative_page_id"),
            "relative_position": source.get("relative_position"),
            "restore_anchor": source.get("restore_anchor"),
            "frames": [base64.b64encode(frame).decode("ascii") for frame in source["frames"]],
        })
    if not documents:
        return {}
    node = shutil.which("node")
    if not node:
        raise AuthorityUnavailable("page link preparation requires Node.js")
    try:
        result = subprocess.run(
            [node, "--max-old-space-size=256", str(Path(__file__).with_name("page_move_delta.cjs"))],
            input=json.dumps({"contract": DELTA_CONTRACT, "documents": documents}).encode(),
            capture_output=True, timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        response = json.loads(result.stdout) if result.returncode == 0 else {}
        if response.get("contract") != DELTA_CONTRACT or len(response["documents"]) != len(sources):
            raise ValueError("invalid preparation result")
        deltas = {}
        for item in response["documents"]:
            doc = item["document_id"]
            if doc not in sources or doc in deltas or not isinstance(item["removed"], list):
                raise ValueError("invalid prepared document")
            payload = base64.b64decode(item["update"], validate=True)
            if not 0 < len(payload) <= 1_048_576:
                raise ValueError("invalid prepared update")
            placed = item.get("placed")
            after = item.get("after_block_id")
            if placed is not None:
                _identifier(placed, field="placed")
            if after is not None:
                _identifier(after, field="after_block_id")
            placed_many = item.get("placed_many")
            if placed_many is not None:
                if (not isinstance(placed_many, list) or not 1 <= len(placed_many) <= 100
                        or placed is not None or item["removed"]):
                    raise ValueError("invalid prepared batch links")
                seen: set[str] = set()
                for link in placed_many:
                    if not isinstance(link, dict) or set(link) != {
                            "id", "after_block_id", "page_id"}:
                        raise ValueError("invalid prepared batch link")
                    link_id = _identifier(link["id"], field="batch_link_id")
                    _identifier(link["page_id"], field="batch_page_id")
                    if link["after_block_id"] is not None:
                        _identifier(link["after_block_id"], field="batch_after_block_id")
                    if link_id in seen:
                        raise ValueError("duplicate prepared batch link")
                    seen.add(link_id)
            if not isinstance(item.get("changed"), bool) or not isinstance(item.get("inserted"), bool):
                raise ValueError("invalid prepared placement")
            reconciled = item.get("reconciled_insert_ids", [])
            if (not isinstance(reconciled, list) or any(not isinstance(identity, str) for identity in reconciled)
                    or len(set(reconciled)) != len(reconciled)
                    or not set(reconciled).issubset(allowed_insert_ids(sources[doc]))):
                raise ValueError("invalid parent insert reconciliation")
            if "post_frame_structure" in sources[doc] and not isinstance(item.get("reconciled_structure_changed"), bool):
                raise ValueError("invalid parent structural reconciliation")
            anchor = item.get("restore_anchor")
            if anchor is not None:
                if not isinstance(anchor, dict) or set(anchor) != {"previous_block_id", "next_block_id", "index"}:
                    raise ValueError("invalid restore anchor")
                if type(anchor["index"]) is not int or not 0 <= anchor["index"] <= 100000:
                    raise ValueError("invalid restore index")
                for key in ("previous_block_id", "next_block_id"):
                    if anchor[key] is not None:
                        _identifier(anchor[key], field=key)
            deltas[doc] = {"payload": payload, "removed": item["removed"], "placed": placed,
                           "inserted": item["inserted"], "after_block_id": after,
                           "placed_many": placed_many, "changed": item["changed"],
                           "restore_anchor": anchor,
                           **({"reconciled_insert_ids": reconciled} if "post_frame_insert_ids" in sources[doc] else {}),
                           **({"reconciled_structure_changed": item["reconciled_structure_changed"]}
                              if "post_frame_structure" in sources[doc] else {})}
        return deltas
    except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired) as exc:
        raise AuthorityUnavailable("collaborative page links could not be prepared") from exc


def _authorize(authority, con, actor, workspace_id, document_id, minimum="full_access"):
    authority._require_active(actor)
    page = authority._document(con, document_id)
    if page["workspace_id"] != workspace_id or page["archived"]:
        raise NotFoundOrDenied("not found or permission denied")
    role, _ = authority._resolution_on_connection(con, actor, workspace_id, document_id)
    if role is None or PAGE_ROLE_LEVEL[role] < PAGE_ROLE_LEVEL[minimum]:
        raise NotFoundOrDenied("not found or permission denied")
    return page


def _unlocked(con, page, actor):
    from .canonical_authority import _require_unlocked_or_temporary
    _require_unlocked_or_temporary(con, page, actor)


def _stamp(page, generation):
    return (page["row_version"], page["document_sequence"], page["access_epoch"], generation)


def _structural_link(authority, con, actor, workspace, page, kind, target, payload, operation, now, *,
                     operation_action=ACTION, receipt_action="collaboration.page_move_link",
                     identity_prefix="pmcmd"):
    """Use the existing structural ledger and tombstone/recovery contract."""
    doc = page["id"]
    sequence = int(authority._document(con, doc)["document_sequence"]) + 1
    command_sequence = 1 + con.execute(
        "SELECT COALESCE(MAX(command_sequence),0) FROM workspace_collaboration_typed_commands WHERE document_id=?",
        (doc,),
    ).fetchone()[0]
    op = _stable_id(identity_prefix, operation, doc, kind, target)
    command_id = _stable_id("tcmd", actor, doc, op)
    domain = authority._apply_typed_domain(
        con, actor_subject_id=actor, document_id=doc, command_id=command_id,
        command_kind=kind, target_id=target, payload=payload, expected_target_version=None,
        document_sequence=sequence, created_at=now,
    )
    checksum = _sha256(_canonical_json(authority._editing_snapshot(con, authority._document(con, doc))))
    con.execute("UPDATE workspace_collaboration_documents SET document_sequence=?,content_sha256=?,row_version=row_version+1,updated_at=? WHERE id=?",
                (sequence, checksum, now, doc))
    result = {"contract": EDITING_AUTHORITY_CONTRACT, "document_id": doc, "workspace_id": workspace["id"],
              "command_id": command_id, "command_kind": kind, "command_sequence": command_sequence,
              "document_sequence": sequence, "target_id": target, "content_sha256": checksum, **domain}
    con.execute(
        "INSERT INTO workspace_collaboration_typed_commands(id,workspace_id,document_id,command_sequence,document_sequence,"
        "actor_subject_id,operation_id,command_kind,target_id,expected_document_sequence,expected_target_version,payload_json,"
        "result_json,workspace_epoch,document_epoch,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,NULL,?,?,?,?,?)",
        (command_id, workspace["id"], doc, command_sequence, sequence, actor, op, kind, target, sequence - 1,
         _canonical_json(payload), _canonical_json(result), workspace["security_epoch"], page["access_epoch"], now),
    )
    authority._finish(con, actor=actor, action=receipt_action, operation_id=op,
        request_sha256=_request_sha256(actor, operation_action, {"operation": operation, "document": doc, "kind": kind, "target": target}),
        workspace_id=workspace["id"], document_id=doc, workspace_epoch=workspace["security_epoch"],
        document_epoch=page["access_epoch"], document_sequence=sequence, event_type="typed.command",
        event_payload=result, result=result, fault=None)


def _append_link_delta(authority, con, actor, workspace, page, delta, generation, operation, now, *,
                       operation_action=ACTION, receipt_action="collaboration.page_move_delta",
                       client_id="page-move-service", binding_contract=CONTRACT,
                       identity_prefix="pmupd"):
    """Persist a service-authored update; no browser ticket or ACK is fabricated."""
    doc = page["id"]
    sequence = int(authority._document(con, doc)["document_sequence"]) + 1
    update_sequence = 1 + con.execute(
        "SELECT COALESCE(MAX(update_sequence),0) FROM workspace_collaboration_crdt_updates WHERE document_id=?", (doc,),
    ).fetchone()[0]
    op = _stable_id(identity_prefix, operation, doc)
    update_id = _stable_id("cupd", actor, doc, op)
    checksum = _sha256(delta)
    binding = runtime_binding_sha256()
    con.execute("UPDATE workspace_collaboration_documents SET document_sequence=?,row_version=row_version+1,updated_at=? WHERE id=?",
                (sequence, now, doc))
    con.execute(
        "INSERT INTO workspace_collaboration_crdt_updates(id,workspace_id,document_id,update_sequence,document_sequence,"
        "actor_subject_id,client_id,session_binding_sha256,operation_id,client_sequence,payload_encoding,payload_bytes,"
        "payload_sha256,payload,workspace_epoch,document_epoch,runtime_binding_sha256,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (update_id, workspace["id"], doc, update_sequence, sequence, actor, client_id,
         _sha256(binding_contract + workspace["id"]), op, update_sequence, YJS_UPDATE_ENCODING, len(delta), checksum, delta,
         workspace["security_epoch"], page["access_epoch"], binding, now),
    )
    con.execute("INSERT INTO workspace_collaboration_crdt_update_bindings(update_id,document_id,generation,rich_text_scope,created_at) VALUES(?,?,?,'document_rich_text_leaves',?)",
                (update_id, doc, generation, now))
    result = {"workspace_id": workspace["id"], "page_id": doc, "document_sequence": sequence,
              "update_sequence": update_sequence, "subject_id": actor, "client_id": client_id,
              "client_sequence": update_sequence, "operation_id": op, "message_type": "crdt.update",
              "payload_encoding": YJS_UPDATE_ENCODING, "payload_sha256": checksum,
              "workspace_epoch": workspace["security_epoch"], "page_epoch": page["access_epoch"], "runtime_binding_sha256": binding}
    authority._finish(con, actor=actor, action=receipt_action, operation_id=op,
        request_sha256=_request_sha256(actor, operation_action, {"operation": operation, "document": doc}),
        workspace_id=workspace["id"], document_id=doc, workspace_epoch=workspace["security_epoch"],
        document_epoch=page["access_epoch"], document_sequence=sequence, event_type="crdt.update",
        event_payload=result, result=result, fault=None)


def move_page(authority, actor_subject_id, workspace_id, document_id, *, parent_id=None, before_id=None, after_id=None,
              expected_document_sequence, expected_workspace_epoch, expected_document_epoch,
              operation_id, fault=None):
    actor = authority._require_active(actor_subject_id)
    workspace_id = _identifier(workspace_id, field="workspace_id")
    doc = _identifier(document_id, field="document_id")
    destinations = {key: value for key, value in (("parent_id", parent_id), ("before_id", before_id), ("after_id", after_id)) if value is not None}
    if len(destinations) != 1 or not isinstance(next(iter(destinations.values())), str):
        raise ValueError("exactly one string parent_id, before_id or after_id is required")
    field, target = next(iter(destinations.items()))
    target = _identifier(target, field=field) if target or field != "parent_id" else None
    relative_id = target if field != "parent_id" else None
    relative_position = "before" if field == "before_id" else "after" if field == "after_id" else None
    operation = _operation_id(operation_id)
    seq = _positive(expected_document_sequence, field="expected_document_sequence")
    we = _positive(expected_workspace_epoch, field="expected_workspace_epoch")
    de = _positive(expected_document_epoch, field="expected_document_epoch")
    request_hash = _request_sha256(actor, ACTION, {"workspace_id": workspace_id, "document_id": doc,
        **destinations, "expected_document_sequence": seq, "expected_workspace_epoch": we, "expected_document_epoch": de})

    def replay(con):
        return authority._replay(con, actor=actor, action=ACTION, operation_id=operation,
            request_sha256=request_hash, authorize=lambda: _authorize(authority, con, actor, workspace_id, doc, "view"))

    def authorize(con):
        workspace, page, _ = authority._exact_access(con, actor, doc, minimum_role="full_access",
            expected_workspace_epoch=we, expected_document_epoch=de)
        if workspace["id"] != workspace_id or page["archived"]:
            raise NotFoundOrDenied("not found or permission denied")
        _unlocked(con, page, actor)
        if page["document_sequence"] != seq:
            raise DocumentSequenceConflict(page["document_sequence"])
        anchor = _authorize(authority, con, actor, workspace_id, relative_id, "view") if relative_id else None
        if relative_id == doc:
            raise ValueError("a page cannot be positioned relative to itself")
        parent = anchor["parent_document_id"] if anchor is not None else target
        if parent == doc:
            raise ValueError("a page cannot be its own parent")
        # UNION terminates even if an imported hierarchy is already cyclic.
        descendants = [row[0] for row in con.execute(
            "WITH RECURSIVE subtree(id) AS (SELECT id FROM workspace_collaboration_documents WHERE workspace_id=? AND id=? "
            "UNION SELECT d.id FROM workspace_collaboration_documents d JOIN subtree s ON d.parent_document_id=s.id WHERE d.workspace_id=?) "
            "SELECT id FROM subtree", (workspace_id, doc, workspace_id))]
        if parent in descendants:
            raise ValueError("a page cannot be moved into its descendant")
        parents = {}
        for key in dict.fromkeys(p for p in (page["parent_document_id"], parent) if p):
            row = _authorize(authority, con, actor, workspace_id, key, "edit_content")
            _unlocked(con, row, actor)
            parents[key] = row
        if not parent and authority._workspace_role(con, workspace_id, actor) is None:
            raise NotFoundOrDenied("workspace membership required for a root page")
        return workspace, page, parents, descendants, parent, anchor

    with authority._reader() as con:
        con.execute("BEGIN")
        previous = replay(con)
        if previous is not None:
            return previous
        workspace, page, parents, _, parent, anchor = authorize(con)
        anchor_stamp = tuple(anchor) if anchor is not None else None
        old_parent = page["parent_document_id"]
        sources = {}
        if old_parent != parent or relative_id:
            for key, row in parents.items():
                generation = _generation(con, key)
                snapshot = authority._editing_snapshot(con, row)
                sources[key] = {"id": key, "stamp": _stamp(row, generation), "generation": generation,
                    "frames": _frames(con, key, generation), "snapshot": snapshot,
                    **parent_rebase.capture_witness(con, key, generation, snapshot),
                    "relocate": key == parent and old_parent == parent,
                    "relative_page_id": relative_id if key == parent else None,
                    "relative_position": relative_position if key == parent else None,
                    "insert": {"id": _stable_id("child_link", actor, operation, doc), "type": "child_page",
                               "content": page["title"], "props": {"page_id": doc}} if key == parent else None}
    deltas = _prepare_deltas(sources, doc, operation)
    parent_rebase.validate_deltas(sources, deltas, doc)
    with authority._transaction() as con:
        previous = replay(con)
        if previous is not None:
            return previous
        workspace, page, parents, descendants, current_parent, anchor = authorize(con)
        if current_parent != parent or (tuple(anchor) if anchor is not None else None) != anchor_stamp:
            raise DocumentSequenceConflict(anchor["document_sequence"] if anchor is not None else page["document_sequence"])
        if page["parent_document_id"] != old_parent:
            raise DocumentSequenceConflict(page["document_sequence"])
        for key, source in sources.items():
            if _stamp(parents[key], _generation(con, key)) != source["stamp"]:
                raise DocumentSequenceConflict(parents[key]["document_sequence"])
        now = int(time.time())
        old_team = team_location.root_record(con, workspace_id, doc)
        destination_team = team_location.root_record(con, workspace_id, relative_id) if parent is None and relative_id else None
        team_id = destination_team["teamspace_id"] if destination_team else None
        # Explicit root destinations mean personal root. Relative root moves
        # inherit the anchor's real native team, rather than only its ordering.
        location_changed = (old_team["teamspace_id"] if old_team else None) != team_id
        if team_id:
            team_location.require_creator(authority, con, actor, workspace_id, team_id)
            if not con.execute("SELECT 1 FROM workspace_collaboration_native_pages WHERE document_id=?", (doc,)).fetchone():
                raise ValueError("legacy page team-root placement requires native adoption")
        root_order = None
        reordered_ids = []
        if parent is None and (old_parent != parent or relative_id):
            roots = con.execute("SELECT id,position FROM workspace_collaboration_documents WHERE workspace_id=? AND parent_document_id IS NULL AND archived=0 ORDER BY position,created_at,id", (workspace_id,)).fetchall()
            existing = [row["id"] for row in roots]
            root_order = [key for key in existing if key != doc]
            at = root_order.index(relative_id) + (relative_position == "after") if relative_id else len(root_order)
            root_order.insert(at, doc)
            if root_order == existing:
                root_order = None
        changed = location_changed or old_parent != parent or root_order is not None or any(delta["changed"] for delta in deltas.values())
        if changed:
            con.execute("UPDATE workspace_collaboration_documents SET parent_document_id=?,document_sequence=document_sequence+1,row_version=row_version+1,updated_at=? WHERE id=?",
                        (parent, now, doc))
            if old_parent != parent:
                from . import database_row_page_placement
                if database_row_page_placement.installed(con):
                    con.execute(
                        "UPDATE workspace_collaboration_database_row_page_placements "
                        "SET parent_document_id=?,row_version=row_version+1,updated_at=? "
                        "WHERE row_id=? AND placement='page'",
                        (parent, now, doc),
                    )
            if location_changed:
                team_location.replace_root(con, workspace_id, doc, team_id, actor, now)
            # Both inherited and explicit readers must discard stale access tickets.
            if old_parent != parent or location_changed:
                for start in range(0, len(descendants), 400):
                    authority._bump_document_epochs(con, descendants[start:start + 400])
                authority._bump_workspace(con, workspace_id)
                workspace = authority._workspace(con, workspace_id)
            if root_order is not None:
                for position, key in enumerate(root_order):
                    if con.execute("UPDATE workspace_collaboration_documents SET position=?,row_version=row_version+1 WHERE id=? AND position<>?", (position, key, position)).rowcount:
                        reordered_ids.append(key)
            for key, source in sources.items():
                if not deltas[key]["changed"]:
                    continue
                row = authority._document(con, key)
                canonical = {b["id"]: b for b in source["snapshot"]["blocks"]}
                removed = set(deltas[key]["removed"])
                # Current Yjs leaves own type/props. An old structural child_page
                # may now be a paragraph; do not delete that user's live block.
                for block_id in sorted(removed & canonical.keys()):
                    _structural_link(authority, con, actor, workspace, row, "block.delete", block_id, {}, operation, now)
                if deltas[key]["placed"]:
                    target_id = deltas[key]["placed"]
                    payload = {"after_block_id": deltas[key]["after_block_id"]}
                    if deltas[key]["inserted"]:
                        payload.update(source["insert"])
                        kind = "block.insert"
                    elif target_id in canonical:
                        kind = "block.move"
                    else:
                        raise AuthorityUnavailable("current child link requires structural reconciliation")
                    _structural_link(authority, con, actor, workspace, row, kind, target_id, payload, operation, now)
                _append_link_delta(authority, con, actor, workspace, row, deltas[key]["payload"], source["generation"], operation, now)
            refreshed = authority._document(con, doc)
            checksum = _sha256(_canonical_json(authority._editing_snapshot(con, refreshed)))
            con.execute("UPDATE workspace_collaboration_documents SET content_sha256=? WHERE id=?", (checksum, doc))
        else:
            # Each new command receipt occupies one document event sequence,
            # even when its requested parent is already current. Keep edit time
            # and access epochs unchanged for this no-op.
            con.execute("UPDATE workspace_collaboration_documents SET document_sequence=document_sequence+1,row_version=row_version+1 WHERE id=?", (doc,))
        page = authority._document(con, doc)
        result = {"contract": CONTRACT, "workspace_id": workspace_id, "document_id": doc,
                  "parent_id": parent or "", "previous_parent_id": old_parent or "",
                  "document_sequence": page["document_sequence"], "workspace_epoch": workspace["security_epoch"],
                  "document_epoch": page["access_epoch"], "changed": changed,
                  "refresh_page_ids": list(dict.fromkeys([doc, *parents, *reordered_ids]))}
        return authority._finish(con, actor=actor, action=ACTION, operation_id=operation,
            request_sha256=request_hash, workspace_id=workspace_id, document_id=doc,
            workspace_epoch=workspace["security_epoch"], document_epoch=page["access_epoch"],
            document_sequence=page["document_sequence"], event_type="page.moved", event_payload=result,
            result=result, fault=fault)
