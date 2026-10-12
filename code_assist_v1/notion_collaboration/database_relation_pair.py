"""Atomic reciprocal effects for an explicitly paired database relation.

The ordinary relation cell remains canonical on each side. A paired edit
changes the inverse cells in the same SQLite transaction, then records a
document change for every other document whose cells changed.
"""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any, Mapping

from . import database_activity, database_trash
from .canonical_authority import (
    AuthorityUnavailable, NotFoundOrDenied, _canonical_json, _identifier,
    _operation_id,
    _request_sha256, _require_unlocked_or_temporary, _sha256, _stable_id,
)


def _relation_config(row: sqlite3.Row) -> dict[str, Any]:
    try:
        value = json.loads(str(row["config_json"]))
    except (TypeError, ValueError) as exc:
        raise AuthorityUnavailable("relation property configuration is invalid") from exc
    if not isinstance(value, dict):
        raise AuthorityUnavailable("relation property configuration is invalid")
    return value


def _relation_ids(value: Any) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise AuthorityUnavailable("stored relation cell is invalid")
    if len(value) != len(set(value)) or len(value) > 100:
        raise AuthorityUnavailable("stored relation cell is invalid")
    return value


def apply_reciprocal_cell_edit(
    authority, con: sqlite3.Connection, *, actor: str,
    source_document_id: str, source_row_id: str,
    source_property: sqlite3.Row, previous_value: Any, new_value: Any,
    now: int,
    target_database_access=None,
) -> tuple[str, str] | None:
    """Write inverse cells and return the affected document/database pair."""
    if str(source_property["property_type"]) != "relation":
        return None
    config = _relation_config(source_property)
    inverse_property_id = str(config.get("reciprocal_property_id") or "")
    if not inverse_property_id:
        return None
    source_database_id = str(source_property["bound_database_id"])
    target_database_id = str(config.get("target_database_id") or "")
    inverse = con.execute(
        "SELECT property.*,database.document_id "
        "FROM workspace_collaboration_database_properties property "
        "JOIN workspace_collaboration_databases database "
        "ON database.id=property.database_id "
        "WHERE property.id=? AND property.database_id=?",
        (inverse_property_id, target_database_id),
    ).fetchone()
    if inverse is None or str(inverse["property_type"]) != "relation":
        raise AuthorityUnavailable("paired relation property is missing")
    inverse_config = _relation_config(inverse)
    if (str(inverse_config.get("target_database_id") or "") != source_database_id
            or str(inverse_config.get("reciprocal_property_id") or "")
            != str(source_property["id"])):
        raise AuthorityUnavailable("paired relation properties disagree")
    target_document_id = str(inverse["document_id"])
    database_trash.require_active(con, target_database_id)
    target_document = authority._document(con, target_document_id)
    source_document = authority._document(con, source_document_id)
    if str(target_document["workspace_id"]) != str(source_document["workspace_id"]):
        raise ValueError("paired relations require one workspace")
    if target_database_access is not None:
        target_database_access(target_database_id, "edit_content")
    else:
        authority._exact_access(
            con, actor, target_document_id, minimum_role="edit_content",
            expected_workspace_epoch=int(authority._workspace(
                con, str(target_document["workspace_id"]))["security_epoch"]),
            expected_document_epoch=int(target_document["access_epoch"]),
        )
    _require_unlocked_or_temporary(con, target_document, actor)
    old_ids = _relation_ids([] if previous_value is None else previous_value)
    new_ids = _relation_ids(new_value)
    removed = set(old_ids) - set(new_ids)
    added = set(new_ids) - set(old_ids)
    if not removed and not added:
        return None
    for row_id in sorted(removed | added):
        row = con.execute(
            "SELECT 1 FROM workspace_collaboration_database_rows WHERE id=?",
            (row_id,),
        ).fetchone()
        if row is None:
            raise NotFoundOrDenied("not found or permission denied")
        cell = con.execute(
            "SELECT value_json,row_version FROM workspace_collaboration_database_cells "
            "WHERE row_id=? AND property_id=?",
            (row_id, inverse_property_id),
        ).fetchone()
        values = _relation_ids(json.loads(str(cell["value_json"]))) if cell else []
        if row_id in removed:
            values = [item for item in values if item != source_row_id]
        elif source_row_id not in values:
            if len(values) >= 100:
                raise ValueError("paired relation row limit exceeded")
            values.append(source_row_id)
        if cell is None:
            con.execute(
                "INSERT INTO workspace_collaboration_database_cells("
                "row_id,property_id,value_json,row_version,updated_at) "
                "VALUES(?,?,?,?,?)",
                (row_id, inverse_property_id, _canonical_json(values), 1, now),
            )
        else:
            con.execute(
                "UPDATE workspace_collaboration_database_cells SET "
                "value_json=?,row_version=row_version+1,updated_at=? "
                "WHERE row_id=? AND property_id=?",
                (_canonical_json(values), now, row_id, inverse_property_id),
            )
    return target_document_id, target_database_id


def commit_reciprocal_documents(
    authority, con: sqlite3.Connection, *, actor: str,
    source_document_id: str, operation_id: str,
    affected: Mapping[str, set[str]], now: int,
    effect_kind: str = "cell",
) -> list[dict[str, Any]]:
    """Advance other documents and emit their durable refresh events."""
    result: list[dict[str, Any]] = []
    for document_id, database_ids in sorted(affected.items()):
        if not database_ids:
            continue
        if document_id == source_document_id:
            source = authority._document(con, document_id)
            database_activity.touch(
                con, database_ids=database_ids, document_id=document_id,
                actor_subject_id=actor, edited_at=now,
                document_sequence=int(source["document_sequence"]),
            )
            continue
        document = authority._document(con, document_id)
        workspace = authority._workspace(con, str(document["workspace_id"]))
        sequence = int(document["document_sequence"]) + 1
        checksum = _sha256(_canonical_json(authority._editing_snapshot(
            con, document)))
        con.execute(
            "UPDATE workspace_collaboration_documents SET document_sequence=?,"
            "content_sha256=?,row_version=row_version+1,updated_at=? WHERE id=?",
            (sequence, checksum, now, document_id),
        )
        database_activity.touch(
            con, database_ids=database_ids, document_id=document_id,
            actor_subject_id=actor, edited_at=now,
            document_sequence=sequence,
        )
        effect_operation = _stable_id(
            "relop", actor, source_document_id, effect_kind,
            operation_id, document_id)
        effect = {
            "contract": "code_assist.notion.reciprocal_relation_effect.v1",
            "source_document_id": source_document_id,
            "document_id": document_id,
            "database_ids": sorted(database_ids),
            "document_sequence": sequence,
            "content_sha256": checksum,
        }
        receipt = authority._finish(
            con, actor=actor, action="collaboration.reciprocal_relation_effect",
            operation_id=effect_operation,
            request_sha256=_request_sha256(actor,
                "collaboration.reciprocal_relation_effect", effect),
            workspace_id=str(workspace["id"]), document_id=document_id,
            workspace_epoch=int(workspace["security_epoch"]),
            document_epoch=int(document["access_epoch"]),
            document_sequence=sequence,
            event_type="database.surface.committed", event_payload=effect,
            result=effect, fault=None,
        )
        result.append({key: receipt[key] for key in (
            "document_id", "document_sequence", "content_sha256",
            "event_id", "outbox_id")})
    return result


def enable_relation_pair(
    authority, actor_subject_id: str, workspace_id: str,
    source_document_id: str, source_database_id: str,
    source_property_id: str, target_document_id: str,
    target_database_id: str, inverse_property_id: str,
    inverse_name: str, *, expected_source_sequence: int,
    expected_target_sequence: int, expected_workspace_epoch: int,
    expected_source_epoch: int, expected_target_epoch: int,
    expected_source_property_version: int, operation_id: str,
    fault: str | None = None,
) -> dict[str, Any]:
    """Create the inverse property and its existing links in one transaction."""
    from .database_features import _clean_name
    from .editing_authority import DocumentSequenceConflict, TargetVersionConflict

    actor = _identifier(actor_subject_id, field="actor_subject_id")
    workspace_id = _identifier(workspace_id, field="workspace_id")
    source_document = _identifier(source_document_id, field="source_document_id")
    source_database = _identifier(source_database_id, field="source_database_id")
    source_property = _identifier(source_property_id, field="source_property_id")
    target_document = _identifier(target_document_id, field="target_document_id")
    target_database = _identifier(target_database_id, field="target_database_id")
    inverse_property = _identifier(inverse_property_id, field="inverse_property_id")
    name = _clean_name(inverse_name, field="inverse property name")
    if source_property == inverse_property:
        raise ValueError("inverse property must have a separate id")
    source_sequence = int(expected_source_sequence)
    target_sequence = int(expected_target_sequence)
    workspace_epoch = int(expected_workspace_epoch)
    source_epoch = int(expected_source_epoch)
    target_epoch = int(expected_target_epoch)
    property_version = int(expected_source_property_version)
    if min(source_sequence, target_sequence, workspace_epoch,
           source_epoch, target_epoch, property_version) < 1:
        raise ValueError("positive relation pair versions required")
    if source_document == target_document and (
            source_sequence != target_sequence or source_epoch != target_epoch):
        raise ValueError("same-document relation pair versions disagree")
    operation = _operation_id(operation_id)
    action = "collaboration.database.relation_pair.enable"
    request = {
        "workspace_id": workspace_id,
        "source_document_id": source_document,
        "source_database_id": source_database,
        "source_property_id": source_property,
        "target_document_id": target_document,
        "target_database_id": target_database,
        "inverse_property_id": inverse_property,
        "inverse_name": name,
        "expected_source_sequence": source_sequence,
        "expected_target_sequence": target_sequence,
        "expected_workspace_epoch": workspace_epoch,
        "expected_source_epoch": source_epoch,
        "expected_target_epoch": target_epoch,
        "expected_source_property_version": property_version,
    }
    digest = _request_sha256(actor, action, request)
    with authority._transaction() as con:
        def authorize() -> tuple[sqlite3.Row, sqlite3.Row]:
            source_ws, source_page, _role = authority._exact_access(
                con, actor, source_document, minimum_role="edit",
                expected_workspace_epoch=workspace_epoch,
                expected_document_epoch=source_epoch)
            target_ws, target_page, _role = authority._exact_access(
                con, actor, target_document, minimum_role="edit",
                expected_workspace_epoch=workspace_epoch,
                expected_document_epoch=target_epoch)
            if (str(source_ws["id"]) != workspace_id
                    or str(target_ws["id"]) != workspace_id):
                raise NotFoundOrDenied("not found or permission denied")
            authority._database_for_document(con, source_document,
                                             source_database)
            authority._database_for_document(con, target_document,
                                             target_database)
            return source_page, target_page

        replay = authority._replay(
            con, actor=actor, action=action, operation_id=operation,
            request_sha256=digest, authorize=authorize)
        if replay is not None:
            return replay
        source_page, target_page = authorize()
        _require_unlocked_or_temporary(con, source_page, actor)
        _require_unlocked_or_temporary(con, target_page, actor)
        if int(source_page["document_sequence"]) != source_sequence:
            raise DocumentSequenceConflict(int(source_page["document_sequence"]))
        if int(target_page["document_sequence"]) != target_sequence:
            raise DocumentSequenceConflict(int(target_page["document_sequence"]))
        property_row = con.execute(
            "SELECT * FROM workspace_collaboration_database_properties "
            "WHERE id=? AND database_id=?",
            (source_property, source_database),
        ).fetchone()
        if property_row is None or str(property_row["property_type"]) != "relation":
            raise NotFoundOrDenied("not found or permission denied")
        if int(property_row["row_version"]) != property_version:
            raise TargetVersionConflict(int(property_row["row_version"]))
        config = _relation_config(property_row)
        if (str(config.get("target_database_id") or "") != target_database
                or config.get("reciprocal_property_id")):
            raise ValueError("source relation target or pair state changed")
        if con.execute(
                "SELECT 1 FROM workspace_collaboration_database_properties WHERE id=?",
                (inverse_property,)).fetchone() is not None:
            raise TargetVersionConflict(1)
        inverse_values: dict[str, list[str]] = {}
        for cell in con.execute(
                "SELECT row_id,value_json FROM workspace_collaboration_database_cells "
                "WHERE property_id=? ORDER BY row_id",
                (source_property,)).fetchall():
            source_row = str(cell["row_id"])
            for row_id in _relation_ids(json.loads(str(cell["value_json"]))):
                if con.execute(
                        "SELECT 1 FROM workspace_collaboration_database_rows WHERE id=?",
                        (row_id,)).fetchone() is None:
                    raise NotFoundOrDenied("not found or permission denied")
                inverse_values.setdefault(row_id, []).append(source_row)
        if any(len(items) > 100 for items in inverse_values.values()):
            raise ValueError("paired relation row limit exceeded")
        now = int(time.time())
        config["reciprocal_property_id"] = inverse_property
        inverse_config = {
            "target_database_id": source_database,
            "reciprocal_property_id": source_property,
        }
        position = 1 + int(con.execute(
            "SELECT COALESCE(MAX(position),-1) "
            "FROM workspace_collaboration_database_properties WHERE database_id=?",
            (target_database,),
        ).fetchone()[0])
        con.execute(
            "UPDATE workspace_collaboration_database_properties SET "
            "config_json=?,row_version=row_version+1,updated_at=? WHERE id=?",
            (_canonical_json(config), now, source_property),
        )
        con.execute(
            "INSERT INTO workspace_collaboration_database_properties("
            "id,database_id,name,property_type,position,config_json,row_version,"
            "created_at,updated_at) VALUES(?,?,?,'relation',?,?,1,?,?)",
            (inverse_property, target_database, name, position,
             _canonical_json(inverse_config), now, now),
        )
        for row_id, items in inverse_values.items():
            con.execute(
                "INSERT INTO workspace_collaboration_database_cells("
                "row_id,property_id,value_json,row_version,updated_at) "
                "VALUES(?,?,?,?,?)",
                (row_id, inverse_property, _canonical_json(items), 1, now),
            )
        next_source_sequence = source_sequence + 1
        source_hash = _sha256(_canonical_json(authority._editing_snapshot(
            con, authority._document(con, source_document))))
        con.execute(
            "UPDATE workspace_collaboration_documents SET document_sequence=?,"
            "content_sha256=?,row_version=row_version+1,updated_at=? WHERE id=?",
            (next_source_sequence, source_hash, now, source_document),
        )
        database_activity.touch(
            con, database_ids=(source_database,), document_id=source_document,
            actor_subject_id=actor, edited_at=now,
            document_sequence=next_source_sequence)
        reciprocal_effects = commit_reciprocal_documents(
            authority, con, actor=actor, source_document_id=source_document,
            operation_id=operation,
            affected={target_document: {target_database}}, now=now,
            effect_kind="enable")
        result = {
            "contract": "code_assist.notion.database_relation_pair.v1",
            "command_kind": "database.relation_pair.enable",
            "workspace_id": workspace_id,
            "document_id": source_document,
            "database_id": source_database,
            "property_id": source_property,
            "property_version": property_version + 1,
            "inverse_document_id": target_document,
            "inverse_database_id": target_database,
            "inverse_property_id": inverse_property,
            "inverse_property_version": 1,
            "document_sequence": next_source_sequence,
            "content_sha256": source_hash,
            "reciprocal_documents": reciprocal_effects,
        }
        return authority._finish(
            con, actor=actor, action=action, operation_id=operation,
            request_sha256=digest, workspace_id=workspace_id,
            document_id=source_document,
            workspace_epoch=workspace_epoch, document_epoch=source_epoch,
            document_sequence=next_source_sequence,
            event_type="database.schema.committed", event_payload=result,
            result=result, fault=fault)


def disable_relation_pair(
    authority, actor_subject_id: str, workspace_id: str,
    retained_document_id: str, retained_database_id: str,
    retained_property_id: str, *, expected_retained_sequence: int,
    expected_inverse_sequence: int, expected_workspace_epoch: int,
    expected_retained_epoch: int, expected_inverse_epoch: int,
    expected_retained_property_version: int,
    expected_inverse_property_version: int, operation_id: str,
    fault: str | None = None,
) -> dict[str, Any]:
    """Remove the inverse property while preserving the selected side's links."""
    from .editing_authority import DocumentSequenceConflict, TargetVersionConflict

    actor = _identifier(actor_subject_id, field="actor_subject_id")
    workspace_id = _identifier(workspace_id, field="workspace_id")
    retained_document = _identifier(retained_document_id, field="retained_document_id")
    retained_database = _identifier(retained_database_id, field="retained_database_id")
    retained_property = _identifier(retained_property_id, field="retained_property_id")
    retained_sequence = int(expected_retained_sequence)
    inverse_sequence = int(expected_inverse_sequence)
    workspace_epoch = int(expected_workspace_epoch)
    retained_epoch = int(expected_retained_epoch)
    inverse_epoch = int(expected_inverse_epoch)
    retained_version = int(expected_retained_property_version)
    inverse_version = int(expected_inverse_property_version)
    if min(retained_sequence, inverse_sequence, workspace_epoch,
           retained_epoch, inverse_epoch, retained_version,
           inverse_version) < 1:
        raise ValueError("positive relation pair versions required")
    operation = _operation_id(operation_id)
    action = "collaboration.database.relation_pair.disable"
    request = {
        "workspace_id": workspace_id,
        "retained_document_id": retained_document,
        "retained_database_id": retained_database,
        "retained_property_id": retained_property,
        "expected_retained_sequence": retained_sequence,
        "expected_inverse_sequence": inverse_sequence,
        "expected_workspace_epoch": workspace_epoch,
        "expected_retained_epoch": retained_epoch,
        "expected_inverse_epoch": inverse_epoch,
        "expected_retained_property_version": retained_version,
        "expected_inverse_property_version": inverse_version,
    }
    digest = _request_sha256(actor, action, request)
    with authority._transaction() as con:
        authority._exact_access(
            con, actor, retained_document, minimum_role="edit",
            expected_workspace_epoch=workspace_epoch,
            expected_document_epoch=retained_epoch)
        stored = con.execute(
            "SELECT result_json FROM workspace_collaboration_operations "
            "WHERE actor_subject_id=? AND action=? AND operation_id=?",
            (actor, action, operation),
        ).fetchone()
        if stored is not None:
            prior = json.loads(str(stored["result_json"]))
            inverse_document = str(prior["deleted_inverse_document_id"])

            def authorize_replay() -> None:
                authority._exact_access(
                    con, actor, retained_document, minimum_role="edit",
                    expected_workspace_epoch=workspace_epoch,
                    expected_document_epoch=retained_epoch)
                authority._exact_access(
                    con, actor, inverse_document, minimum_role="edit",
                    expected_workspace_epoch=workspace_epoch,
                    expected_document_epoch=inverse_epoch)

            return authority._replay(
                con, actor=actor, action=action, operation_id=operation,
                request_sha256=digest, authorize=authorize_replay)
        retained = con.execute(
            "SELECT * FROM workspace_collaboration_database_properties "
            "WHERE id=? AND database_id=?",
            (retained_property, retained_database),
        ).fetchone()
        if retained is None or str(retained["property_type"]) != "relation":
            raise NotFoundOrDenied("not found or permission denied")
        config = _relation_config(retained)
        inverse_property = str(config.get("reciprocal_property_id") or "")
        inverse_database = str(config.get("target_database_id") or "")
        inverse = con.execute(
            "SELECT property.*,database.document_id FROM "
            "workspace_collaboration_database_properties property "
            "JOIN workspace_collaboration_databases database "
            "ON database.id=property.database_id "
            "WHERE property.id=? AND property.database_id=?",
            (inverse_property, inverse_database),
        ).fetchone()
        if inverse is None or str(inverse["property_type"]) != "relation":
            raise AuthorityUnavailable("paired relation property is missing")
        inverse_document = str(inverse["document_id"])
        inverse_config = _relation_config(inverse)
        if (str(inverse_config.get("target_database_id") or "")
                != retained_database
                or str(inverse_config.get("reciprocal_property_id") or "")
                != retained_property):
            raise AuthorityUnavailable("paired relation properties disagree")
        if retained_document == inverse_document and (
                retained_sequence != inverse_sequence
                or retained_epoch != inverse_epoch):
            raise ValueError("same-document relation pair versions disagree")

        def authorize() -> tuple[sqlite3.Row, sqlite3.Row]:
            retained_ws, retained_page, _role = authority._exact_access(
                con, actor, retained_document, minimum_role="edit",
                expected_workspace_epoch=workspace_epoch,
                expected_document_epoch=retained_epoch)
            inverse_ws, inverse_page, _role = authority._exact_access(
                con, actor, inverse_document, minimum_role="edit",
                expected_workspace_epoch=workspace_epoch,
                expected_document_epoch=inverse_epoch)
            if (str(retained_ws["id"]) != workspace_id
                    or str(inverse_ws["id"]) != workspace_id):
                raise NotFoundOrDenied("not found or permission denied")
            authority._database_for_document(con, retained_document,
                                             retained_database)
            authority._database_for_document(con, inverse_document,
                                             inverse_database)
            return retained_page, inverse_page

        replay = authority._replay(
            con, actor=actor, action=action, operation_id=operation,
            request_sha256=digest, authorize=authorize)
        if replay is not None:
            return replay
        retained_page, inverse_page = authorize()
        _require_unlocked_or_temporary(con, retained_page, actor)
        _require_unlocked_or_temporary(con, inverse_page, actor)
        if int(retained_page["document_sequence"]) != retained_sequence:
            raise DocumentSequenceConflict(int(retained_page["document_sequence"]))
        if int(inverse_page["document_sequence"]) != inverse_sequence:
            raise DocumentSequenceConflict(int(inverse_page["document_sequence"]))
        if int(retained["row_version"]) != retained_version:
            raise TargetVersionConflict(int(retained["row_version"]))
        if int(inverse["row_version"]) != inverse_version:
            raise TargetVersionConflict(int(inverse["row_version"]))
        for dependent in con.execute(
                "SELECT config_json FROM workspace_collaboration_database_properties "
                "WHERE database_id=? AND property_type='rollup'",
                (inverse_database,)).fetchall():
            dep_config = json.loads(str(dependent["config_json"]))
            if inverse_property in {
                    str(dep_config.get("relation_property_id") or ""),
                    str(dep_config.get("target_property_id") or "")}:
                raise ValueError("referenced database property cannot be deleted")
        now = int(time.time())
        config.pop("reciprocal_property_id", None)
        con.execute(
            "UPDATE workspace_collaboration_database_properties SET "
            "config_json=?,row_version=row_version+1,updated_at=? WHERE id=?",
            (_canonical_json(config), now, retained_property),
        )
        con.execute(
            "DELETE FROM workspace_collaboration_database_properties WHERE id=?",
            (inverse_property,),
        )
        next_retained_sequence = retained_sequence + 1
        retained_hash = _sha256(_canonical_json(authority._editing_snapshot(
            con, authority._document(con, retained_document))))
        con.execute(
            "UPDATE workspace_collaboration_documents SET document_sequence=?,"
            "content_sha256=?,row_version=row_version+1,updated_at=? WHERE id=?",
            (next_retained_sequence, retained_hash, now, retained_document),
        )
        database_activity.touch(
            con, database_ids=(retained_database,),
            document_id=retained_document, actor_subject_id=actor,
            edited_at=now, document_sequence=next_retained_sequence)
        reciprocal_effects = commit_reciprocal_documents(
            authority, con, actor=actor,
            source_document_id=retained_document, operation_id=operation,
            affected={inverse_document: {inverse_database}}, now=now,
            effect_kind="disable")
        result = {
            "contract": "code_assist.notion.database_relation_pair.v1",
            "command_kind": "database.relation_pair.disable",
            "workspace_id": workspace_id,
            "document_id": retained_document,
            "database_id": retained_database,
            "property_id": retained_property,
            "property_version": retained_version + 1,
            "deleted_inverse_document_id": inverse_document,
            "deleted_inverse_database_id": inverse_database,
            "deleted_inverse_property_id": inverse_property,
            "document_sequence": next_retained_sequence,
            "content_sha256": retained_hash,
            "reciprocal_documents": reciprocal_effects,
        }
        return authority._finish(
            con, actor=actor, action=action, operation_id=operation,
            request_sha256=digest, workspace_id=workspace_id,
            document_id=retained_document,
            workspace_epoch=workspace_epoch, document_epoch=retained_epoch,
            document_sequence=next_retained_sequence,
            event_type="database.schema.committed", event_payload=result,
            result=result, fault=fault)
