"""Bind later legacy members to an explicitly chosen, committed original.

An explicit marker or matching text alone is insufficient. Only the immutable
server adoption receipt (or its matching older registered intent) establishes
which legacy group this original was designated for. No original is inferred.
"""
from __future__ import annotations

import json
import re

from . import synced_blocks as synced
from .canonical_authority import AuthorityUnavailable, _canonical_json, _request_sha256, _sha256

ACTION = "collaboration.synced_legacy_adoption"
CONTRACT = "code_assist.notion.synced_legacy_adoption_commit.v1"
INTENT_ACTION = "collaboration.synced_legacy_adoption_intent"
INTENT_CONTRACT = "code_assist.notion.synced_legacy_adoption_intent.v1"


def _pair(value):
    if not isinstance(value, dict) or set(value) != {"document_id", "block_id"}:
        raise ValueError("invalid stored adoption endpoint")
    return tuple(synced._exact_id(value[field], field) for field in ("document_id", "block_id"))


def _receipt(row, actor, workspace, original):
    value = json.loads(row["result_json"])
    if (value.get("contract") != CONTRACT or value.get("workspace_id") != workspace
            or value.get("operation_id") != row["operation_id"] or _pair(value.get("origin")) != original
            or value.get("sync_id") != synced.group_id(*original)
            or not isinstance(value.get("preparation_sha256"), str)
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", value["preparation_sha256"])
            or not isinstance(value.get("references"), list) or len(value["references"]) > 127):
        raise ValueError("invalid stored original adoption receipt")
    references = tuple(sorted(_pair(item) for item in value["references"]))
    if len(set(references)) != len(references) or original in references:
        raise ValueError("invalid stored original reference selection")
    digest = _request_sha256(actor, ACTION, {"workspace_id": workspace,
        "origin": original, "references": references, "preparation_sha256": value["preparation_sha256"]})
    if digest != row["request_sha256"]:
        raise ValueError("stored original adoption request differs")
    return value


def original_provenance(authority, con, actor, workspace, original, *, legacy_id=None):
    """Return an immutable initial designation proof under caller's snapshot.

    Later attachment receipts are excluded, so retries and new attachments do
    not change this proof. Current owner/access/content checks belong to caller.
    """
    if not con.in_transaction:
        raise ValueError("original provenance requires a read/write snapshot")
    rows = con.execute("SELECT * FROM workspace_collaboration_operations WHERE actor_subject_id=? "
        "AND workspace_id=? AND action=? AND json_extract(result_json,'$.contract')=? "
        "AND json_extract(result_json,'$.origin.document_id')=? "
        "AND json_extract(result_json,'$.origin.block_id')=? "
        "AND json_extract(result_json,'$.origin_adoption') IS NULL ORDER BY created_at,operation_id",
        (actor, workspace, ACTION, CONTRACT, original[0], original[1])).fetchall()
    proofs = []
    for row in rows:
        try:
            result = _receipt(row, actor, workspace, original)
            old_group = result.get("legacy_sync_id")
            intent_digest = None
            if old_group is None:
                # The first HTTP packet recorded the old group in its intent.
                # Internal receipts without either fact do not prove a mapping.
                intent_row = con.execute("SELECT * FROM workspace_collaboration_operations "
                    "WHERE actor_subject_id=? AND action=? AND operation_id=?",
                    (actor, INTENT_ACTION, row["operation_id"])).fetchone()
                if intent_row is None:
                    continue
                intent = json.loads(intent_row["result_json"])
                expected = {key: result[key] for key in ("origin", "references", "preparation_sha256", "operation_id")}
                if (intent_row["workspace_id"] != workspace or intent_row["request_sha256"] != row["request_sha256"]
                        or intent.get("contract") != INTENT_CONTRACT or intent.get("workspace_id") != workspace
                        or intent.get("request") != expected):
                    raise ValueError("stored original intent differs from committed receipt")
                old_group = intent.get("legacy_sync_id")
                intent_digest = _sha256(intent_row["result_json"])
            old_group = synced._exact_id(old_group, "legacy_sync_id")
            if legacy_id is not None and old_group != legacy_id:
                continue
            proofs.append({"actor_subject_id": actor, "workspace_id": workspace,
                "origin": {"document_id": original[0], "block_id": original[1]},
                "legacy_sync_id": old_group, "operation_id": row["operation_id"],
                "request_sha256": row["request_sha256"], "result_sha256": _sha256(row["result_json"]),
                "intent_sha256": intent_digest})
        except (ValueError, TypeError, KeyError) as exc:
            raise AuthorityUnavailable("stored original designation is invalid") from exc
    if not proofs:
        raise synced.SyncedShapeUnavailable("original has no committed designation for this legacy group")
    if len({item["legacy_sync_id"] for item in proofs}) != 1:
        raise synced.SyncedShapeUnavailable("original designation has ambiguous legacy groups")
    return _canonical_json(proofs[0])
