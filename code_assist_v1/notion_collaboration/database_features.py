"""Canonical adopted database-feature authority for packet F0-P1.

Schema mutations require the full ``edit`` role and commit their domain change,
document sequence, operation result, audit event, and outbox event in one SQLite
transaction.  Content-only actors continue to use ``database.cell.set`` and can
never mutate schema.  Formula evaluation is a small deterministic expression
language; it never executes Python or user-provided code.
"""

from __future__ import annotations

import ast
import json
import os
from pathlib import Path
import sqlite3
import time
from typing import Any, Callable, Mapping, Sequence

from code_assist_v1 import notion_workspace_database_features_migration as migration
from code_assist_v1 import notion_workspace_healing_migration as migration_core
from code_assist_v1.notion_collaboration.canonical_authority import (
    AuthorityUnavailable,
    CanonicalAuthority,
    NotFoundOrDenied,
    _canonical_json,
    _identifier,
    _operation_id,
    _request_sha256,
    _sha256,
    _stable_id,
)
from code_assist_v1.notion_collaboration.editing_authority import (
    CollaborativeEditingAuthority,
    DocumentSequenceConflict,
    TargetVersionConflict,
)
from code_assist_v1.notion_collaboration.iam_authority import (
    CollaborativeIAMAuthority,
    InMemoryIdentityDirectory,
)


DATABASE_FEATURE_CONTRACT = "code_assist.notion.collaborative_database_features.v1"
DATABASE_SURFACE_COMMAND_CONTRACT = (
    "code_assist.notion.collaborative_database_surface_command.v1"
)
SCHEMA_COMMAND_KINDS = frozenset(
    {
        "database.create",
        "database.property.create",
        "database.property.update",
        "database.view.create",
        "database.view.update",
        "database.view.archive",
        "database.template.create",
        "database.template.update",
        "database.template.archive",
    }
)
PROPERTY_TYPES = frozenset(
    {
        "title",
        "text",
        "number",
        "checkbox",
        "date",
        "select",
        "multi_select",
        "status",
        "person",
        "files",
        "url",
        "email",
        "phone",
        "relation",
        "rollup",
        "formula",
        "created_time",
        "last_edited_time",
    }
)
VIEW_TYPES = frozenset({"table", "board", "list", "gallery", "calendar", "timeline"})
ROLLUP_AGGREGATIONS = frozenset(
    {"count", "count_values", "sum", "average", "min", "max", "show_unique"}
)
MAX_SCHEMA_PAYLOAD_BYTES = 1_048_576
SURFACE_COMMAND_KINDS = frozenset(
    {
        "database.update",
        "database.property.delete",
        "database.row.create",
        "database.row.delete",
    }
)
SURFACE_COMMAND_MINIMUM_ROLE = {
    "database.update": "edit",
    "database.property.delete": "edit",
    "database.row.create": "edit_content",
    "database.row.delete": "edit_content",
}


def _marker_current(con: sqlite3.Connection) -> bool:
    marker = con.execute(
        "SELECT name,checksum,actual_schema_sha256 FROM workspace_schema_migrations "
        "WHERE version=(SELECT MAX(version) FROM workspace_schema_migrations)"
    ).fetchone()
    return bool(
        marker is not None
        and tuple(marker)
        == (
            migration.TARGET_NAME,
            migration.TARGET_CHECKSUM,
            migration.TARGET_ACTUAL_SCHEMA_SHA256,
        )
    )


def _clean_object(value: Any, *, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} object required")
    clean = dict(value)
    if len(_canonical_json(clean).encode("utf-8")) > MAX_SCHEMA_PAYLOAD_BYTES:
        raise ValueError(f"{field} is too large")
    return clean


def _clean_name(value: Any, *, field: str = "name") -> str:
    clean = str(value or "").strip()
    if not clean or len(clean) > 1000:
        raise ValueError(f"valid {field} required")
    return clean


def _property_config(property_type: str, value: Any) -> dict[str, Any]:
    clean = _clean_object(value, field="property config")
    if property_type == "relation":
        clean["target_database_id"] = _identifier(
            str(clean.get("target_database_id") or ""), field="target_database_id"
        )
    elif property_type == "rollup":
        clean["relation_property_id"] = _identifier(
            str(clean.get("relation_property_id") or ""), field="relation_property_id"
        )
        clean["target_property_id"] = _identifier(
            str(clean.get("target_property_id") or ""), field="target_property_id"
        )
        aggregation = str(clean.get("aggregation") or "").strip().lower()
        if aggregation not in ROLLUP_AGGREGATIONS:
            raise ValueError("supported rollup aggregation required")
        clean["aggregation"] = aggregation
    elif property_type == "formula":
        expression = str(clean.get("expression") or "").strip()
        if not expression or len(expression) > 4000:
            raise ValueError("valid formula expression required")
        _parse_formula(expression)
        clean["expression"] = expression
    return clean


def _parse_formula(expression: str) -> ast.Expression:
    try:
        parsed = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise ValueError("valid formula expression required") from exc
    allowed = (
        ast.Expression,
        ast.Constant,
        ast.BinOp,
        ast.UnaryOp,
        ast.BoolOp,
        ast.Compare,
        ast.IfExp,
        ast.Call,
        ast.Name,
        ast.Load,
        ast.Add,
        ast.Sub,
        ast.Mult,
        ast.Div,
        ast.Mod,
        ast.Pow,
        ast.USub,
        ast.UAdd,
        ast.Not,
        ast.And,
        ast.Or,
        ast.Eq,
        ast.NotEq,
        ast.Lt,
        ast.LtE,
        ast.Gt,
        ast.GtE,
    )
    for node in ast.walk(parsed):
        if not isinstance(node, allowed):
            raise ValueError("unsupported formula syntax")
        if isinstance(node, ast.Name) and node.id not in {"prop", "abs", "min", "max", "round"}:
            raise ValueError("unsupported formula name")
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in {"prop", "abs", "min", "max", "round"}:
                raise ValueError("unsupported formula call")
            if node.keywords:
                raise ValueError("formula keyword arguments are unsupported")
    return parsed


def _evaluate_formula(expression: str, resolver: Callable[[str], Any]) -> Any:
    root = _parse_formula(expression).body

    def visit(node: ast.AST) -> Any:
        if isinstance(node, ast.Constant):
            if isinstance(node.value, (str, int, float, bool)) or node.value is None:
                return node.value
            raise ValueError("unsupported formula constant")
        if isinstance(node, ast.Name):
            return {"abs": abs, "min": min, "max": max, "round": round}.get(node.id)
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name):
                raise ValueError("unsupported formula call")
            args = [visit(item) for item in node.args]
            if node.func.id == "prop":
                if len(args) != 1 or not isinstance(args[0], str):
                    raise ValueError("prop() requires one property id or name")
                return resolver(args[0])
            function = {"abs": abs, "min": min, "max": max, "round": round}[node.func.id]
            return function(*args)
        if isinstance(node, ast.UnaryOp):
            value = visit(node.operand)
            if isinstance(node.op, ast.USub):
                return -value
            if isinstance(node.op, ast.UAdd):
                return +value
            if isinstance(node.op, ast.Not):
                return not value
        if isinstance(node, ast.BinOp):
            left, right = visit(node.left), visit(node.right)
            operations = {
                ast.Add: lambda: left + right,
                ast.Sub: lambda: left - right,
                ast.Mult: lambda: left * right,
                ast.Div: lambda: left / right,
                ast.Mod: lambda: left % right,
                ast.Pow: lambda: left**right,
            }
            return operations[type(node.op)]()
        if isinstance(node, ast.BoolOp):
            values = [visit(item) for item in node.values]
            return all(values) if isinstance(node.op, ast.And) else any(values)
        if isinstance(node, ast.Compare):
            left = visit(node.left)
            for operator, comparator_node in zip(node.ops, node.comparators, strict=True):
                right = visit(comparator_node)
                checks = {
                    ast.Eq: left == right,
                    ast.NotEq: left != right,
                    ast.Lt: left < right,
                    ast.LtE: left <= right,
                    ast.Gt: left > right,
                    ast.GtE: left >= right,
                }
                if not checks[type(operator)]:
                    return False
                left = right
            return True
        if isinstance(node, ast.IfExp):
            return visit(node.body if visit(node.test) else node.orelse)
        raise ValueError("unsupported formula syntax")

    try:
        return visit(root)
    except (ArithmeticError, TypeError, ValueError) as exc:
        return {"error": "formula_evaluation_failed", "detail": type(exc).__name__}


class CollaborativeDatabaseAuthority(CollaborativeEditingAuthority):
    """2407 schema-role and adopted database round-trip authority."""

    @classmethod
    def open(
        cls,
        catalog_path: str | os.PathLike[str],
        user_data_root: str | os.PathLike[str],
        identity_directory: InMemoryIdentityDirectory,
    ) -> "CollaborativeDatabaseAuthority":
        try:
            catalog, root = migration_core._resolved_target(catalog_path, user_data_root)
            con = sqlite3.connect(f"{catalog.as_uri()}?mode=ro", uri=True)
            try:
                if not _marker_current(con):
                    raise AuthorityUnavailable("collaborative database schema is not current")
            finally:
                con.close()
        except AuthorityUnavailable:
            raise
        except (OSError, sqlite3.Error, RuntimeError) as exc:
            raise AuthorityUnavailable("collaborative database authority is unavailable") from exc
        return cls(Path(catalog), Path(root), identity_directory)

    @staticmethod
    def _database_for_document(
        con: sqlite3.Connection, document_id: str, database_id: str
    ) -> sqlite3.Row:
        row = con.execute(
            "SELECT * FROM workspace_collaboration_databases WHERE document_id=? AND id=?",
            (document_id, database_id),
        ).fetchone()
        if row is None:
            raise NotFoundOrDenied("not found or permission denied")
        return row

    @staticmethod
    def _next_position(con: sqlite3.Connection, table: str, database_id: str) -> int:
        if table not in {
            "workspace_collaboration_database_properties",
            "workspace_collaboration_database_views",
        }:
            raise ValueError("invalid positioned database table")
        return 1 + int(
            con.execute(
                f"SELECT COALESCE(MAX(position),-1) FROM {table} WHERE database_id=?",
                (database_id,),
            ).fetchone()[0]
        )

    def execute_schema_command(
        self,
        actor_subject_id: str,
        document_id: str,
        *,
        database_id: str,
        command_kind: str,
        target_id: str,
        payload: Mapping[str, Any],
        expected_document_sequence: int,
        expected_workspace_epoch: int,
        expected_document_epoch: int,
        operation_id: str,
        expected_target_version: int | None = None,
        fault: str | None = None,
        runtime_generation_id: str | None = None,
    ) -> dict[str, Any]:
        actor = _identifier(actor_subject_id, field="actor_subject_id")
        document = _identifier(document_id, field="document_id")
        database = _identifier(database_id, field="database_id")
        kind = str(command_kind or "").strip().lower()
        if kind not in SCHEMA_COMMAND_KINDS:
            raise ValueError("supported database schema command required")
        target = _identifier(target_id, field="target_id")
        clean_payload = _clean_object(payload, field="schema command payload")
        expected_sequence = int(expected_document_sequence)
        workspace_epoch = int(expected_workspace_epoch)
        document_epoch = int(expected_document_epoch)
        if min(expected_sequence, workspace_epoch, document_epoch) < 1:
            raise ValueError("positive document sequence and access epochs required")
        target_version = None if expected_target_version is None else int(expected_target_version)
        if target_version is not None and target_version < 0:
            raise ValueError("valid expected_target_version required")
        operation = _operation_id(operation_id)
        action = "collaboration.database_schema_command"
        request = {
            "document_id": document,
            "database_id": database,
            "command_kind": kind,
            "target_id": target,
            "payload": clean_payload,
            "expected_document_sequence": expected_sequence,
            "expected_target_version": target_version,
            "expected_workspace_epoch": workspace_epoch,
            "expected_document_epoch": document_epoch,
        }
        request_sha256 = _request_sha256(actor, action, request)
        with self._transaction() as con:
            def authorize() -> None:
                self._exact_access(
                    con,
                    actor,
                    document,
                    minimum_role="edit",
                    expected_workspace_epoch=workspace_epoch,
                    expected_document_epoch=document_epoch,
                )

            replay = self._replay(
                con,
                actor=actor,
                action=action,
                operation_id=operation,
                request_sha256=request_sha256,
                authorize=authorize,
            )
            if replay is not None:
                return replay
            workspace, document_row, role = self._exact_access(
                con,
                actor,
                document,
                minimum_role="edit",
                expected_workspace_epoch=workspace_epoch,
                expected_document_epoch=document_epoch,
            )
            current_sequence = int(document_row["document_sequence"])
            if current_sequence != expected_sequence:
                raise DocumentSequenceConflict(current_sequence)
            now = int(time.time())
            result_target_version = self._apply_schema_domain(
                con,
                document_id=document,
                database_id=database,
                command_kind=kind,
                target_id=target,
                payload=clean_payload,
                expected_target_version=target_version,
                created_at=now,
            )
            next_sequence = current_sequence + 1
            refreshed = self._document(con, document)
            content_sha256 = _sha256(_canonical_json(self._editing_snapshot(con, refreshed)))
            con.execute(
                "UPDATE workspace_collaboration_documents SET document_sequence=?,"
                "content_sha256=?,row_version=row_version+1,updated_at=? WHERE id=?",
                (next_sequence, content_sha256, now, document),
            )
            result = {
                "contract": DATABASE_FEATURE_CONTRACT,
                "workspace_id": str(workspace["id"]),
                "document_id": document,
                "database_id": database,
                "command_kind": kind,
                "target_id": target,
                "target_version": result_target_version,
                "document_sequence": next_sequence,
                "effective_role": role,
                "content_sha256": content_sha256,
            }
            command_id = _stable_id("dbcmd", actor, document, operation)
            con.execute(
                "INSERT INTO workspace_collaboration_database_schema_commands("
                "id,workspace_id,document_id,database_id,actor_subject_id,operation_id,"
                "command_kind,target_id,expected_document_sequence,expected_target_version,"
                "result_target_version,payload_json,result_json,document_sequence,"
                "workspace_epoch,document_epoch,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    command_id,
                    str(workspace["id"]),
                    document,
                    database,
                    actor,
                    operation,
                    kind,
                    target,
                    expected_sequence,
                    target_version,
                    result_target_version,
                    _canonical_json(clean_payload),
                    _canonical_json(result),
                    next_sequence,
                    workspace_epoch,
                    document_epoch,
                    now,
                ),
            )
            return self._finish(
                con,
                actor=actor,
                action=action,
                operation_id=operation,
                request_sha256=request_sha256,
                workspace_id=str(workspace["id"]),
                document_id=document,
                workspace_epoch=workspace_epoch,
                document_epoch=document_epoch,
                document_sequence=next_sequence,
                event_type="database.schema.committed",
                event_payload=result,
                result=result,
                fault=fault,
                runtime_generation_id=runtime_generation_id,
            )

    def execute_surface_command(
        self,
        actor_subject_id: str,
        document_id: str,
        *,
        database_id: str,
        command_kind: str,
        target_id: str,
        payload: Mapping[str, Any],
        expected_document_sequence: int,
        expected_workspace_epoch: int,
        expected_document_epoch: int,
        operation_id: str,
        expected_target_version: int | None = None,
        fault: str | None = None,
        runtime_generation_id: str | None = None,
    ) -> dict[str, Any]:
        """Commit adopted UI operations absent from the frozen 2407 ledger."""

        actor = _identifier(actor_subject_id, field="actor_subject_id")
        document = _identifier(document_id, field="document_id")
        database = _identifier(database_id, field="database_id")
        kind = str(command_kind or "").strip().lower()
        if kind not in SURFACE_COMMAND_KINDS:
            raise ValueError("supported database surface command required")
        target = _identifier(target_id, field="target_id")
        clean_payload = _clean_object(payload, field="surface command payload")
        expected_sequence = int(expected_document_sequence)
        workspace_epoch = int(expected_workspace_epoch)
        document_epoch = int(expected_document_epoch)
        if min(expected_sequence, workspace_epoch, document_epoch) < 1:
            raise ValueError("positive document sequence and access epochs required")
        target_version = None if expected_target_version is None else int(expected_target_version)
        if target_version is not None and target_version < 0:
            raise ValueError("valid expected_target_version required")
        operation = _operation_id(operation_id)
        action = "collaboration.database_surface_command"
        request_value = {
            "document_id": document,
            "database_id": database,
            "command_kind": kind,
            "target_id": target,
            "payload": clean_payload,
            "expected_document_sequence": expected_sequence,
            "expected_target_version": target_version,
            "expected_workspace_epoch": workspace_epoch,
            "expected_document_epoch": document_epoch,
        }
        request_sha256 = _request_sha256(actor, action, request_value)
        with self._transaction() as con:
            def authorize() -> None:
                self._exact_access(
                    con,
                    actor,
                    document,
                    minimum_role=SURFACE_COMMAND_MINIMUM_ROLE[kind],
                    expected_workspace_epoch=workspace_epoch,
                    expected_document_epoch=document_epoch,
                )

            replay = self._replay(
                con,
                actor=actor,
                action=action,
                operation_id=operation,
                request_sha256=request_sha256,
                authorize=authorize,
            )
            if replay is not None:
                return replay
            workspace, document_row, role = self._exact_access(
                con,
                actor,
                document,
                minimum_role=SURFACE_COMMAND_MINIMUM_ROLE[kind],
                expected_workspace_epoch=workspace_epoch,
                expected_document_epoch=document_epoch,
            )
            current_sequence = int(document_row["document_sequence"])
            if current_sequence != expected_sequence:
                raise DocumentSequenceConflict(current_sequence)
            now = int(time.time())
            domain = self._apply_surface_domain(
                con,
                document_id=document,
                database_id=database,
                command_kind=kind,
                target_id=target,
                payload=clean_payload,
                expected_target_version=target_version,
                created_at=now,
            )
            next_sequence = current_sequence + 1
            refreshed = self._document(con, document)
            content_sha256 = _sha256(_canonical_json(self._editing_snapshot(con, refreshed)))
            con.execute(
                "UPDATE workspace_collaboration_documents SET document_sequence=?,"
                "content_sha256=?,row_version=row_version+1,updated_at=? WHERE id=?",
                (next_sequence, content_sha256, now, document),
            )
            result = {
                "contract": DATABASE_SURFACE_COMMAND_CONTRACT,
                "workspace_id": str(workspace["id"]),
                "document_id": document,
                "database_id": database,
                "command_kind": kind,
                "target_id": target,
                "document_sequence": next_sequence,
                "effective_role": role,
                "content_sha256": content_sha256,
                **domain,
            }
            return self._finish(
                con,
                actor=actor,
                action=action,
                operation_id=operation,
                request_sha256=request_sha256,
                workspace_id=str(workspace["id"]),
                document_id=document,
                workspace_epoch=workspace_epoch,
                document_epoch=document_epoch,
                document_sequence=next_sequence,
                event_type="database.surface.committed",
                event_payload=result,
                result=result,
                fault=fault,
                runtime_generation_id=runtime_generation_id,
            )

    def _apply_surface_domain(
        self,
        con: sqlite3.Connection,
        *,
        document_id: str,
        database_id: str,
        command_kind: str,
        target_id: str,
        payload: Mapping[str, Any],
        expected_target_version: int | None,
        created_at: int,
    ) -> dict[str, Any]:
        database = self._database_for_document(con, document_id, database_id)
        if command_kind == "database.update":
            if target_id != database_id:
                raise ValueError("database target binding mismatch")
            self._check_target_version(database, expected_target_version)
            next_version = int(database["row_version"]) + 1
            title = _clean_name(payload.get("title"), field="title")
            con.execute(
                "UPDATE workspace_collaboration_databases SET title=?,row_version=?,updated_at=? WHERE id=?",
                (title, next_version, created_at, database_id),
            )
            return {"target_version": next_version, "title": title}

        if command_kind == "database.property.delete":
            row = con.execute(
                "SELECT * FROM workspace_collaboration_database_properties WHERE database_id=? AND id=?",
                (database_id, target_id),
            ).fetchone()
            if row is None:
                raise NotFoundOrDenied("not found or permission denied")
            self._check_target_version(row, expected_target_version)
            if str(row["property_type"]) == "title":
                raise ValueError("title property cannot be deleted")
            for dependent in con.execute(
                "SELECT property_type,config_json FROM workspace_collaboration_database_properties "
                "WHERE database_id=? AND property_type='rollup' AND id<>?",
                (database_id, target_id),
            ).fetchall():
                config = json.loads(str(dependent["config_json"]))
                if target_id in {
                    str(config.get("relation_property_id") or ""),
                    str(config.get("target_property_id") or ""),
                }:
                    raise ValueError("referenced database property cannot be deleted")
            deleted_version = int(row["row_version"])
            con.execute(
                "DELETE FROM workspace_collaboration_database_properties WHERE database_id=? AND id=?",
                (database_id, target_id),
            )
            return {"target_version": deleted_version, "deleted": True}

        if command_kind == "database.row.create":
            row = con.execute(
                "SELECT * FROM workspace_collaboration_database_rows WHERE database_id=? AND id=?",
                (database_id, target_id),
            ).fetchone()
            if row is not None:
                raise TargetVersionConflict(int(row["row_version"]))
            if expected_target_version not in {None, 0}:
                raise TargetVersionConflict(0)
            position = 1 + int(
                con.execute(
                    "SELECT COALESCE(MAX(position),-1) FROM workspace_collaboration_database_rows WHERE database_id=?",
                    (database_id,),
                ).fetchone()[0]
            )
            con.execute(
                "INSERT INTO workspace_collaboration_database_rows("
                "id,database_id,position,row_version,created_at,updated_at) VALUES(?,?,?,1,?,?)",
                (target_id, database_id, position, created_at, created_at),
            )
            return {"target_version": 1, "position": position}

        row = con.execute(
            "SELECT * FROM workspace_collaboration_database_rows WHERE database_id=? AND id=?",
            (database_id, target_id),
        ).fetchone()
        if row is None:
            raise NotFoundOrDenied("not found or permission denied")
        self._check_target_version(row, expected_target_version)
        deleted_version = int(row["row_version"])
        con.execute(
            "DELETE FROM workspace_collaboration_database_rows WHERE database_id=? AND id=?",
            (database_id, target_id),
        )
        ordered = con.execute(
            "SELECT id FROM workspace_collaboration_database_rows WHERE database_id=? ORDER BY position,id",
            (database_id,),
        ).fetchall()
        for position, item in enumerate(ordered):
            con.execute(
                "UPDATE workspace_collaboration_database_rows SET position=?,updated_at=? WHERE id=?",
                (position, created_at, str(item["id"])),
            )
        return {"target_version": deleted_version, "deleted": True}

    def _apply_schema_domain(
        self,
        con: sqlite3.Connection,
        *,
        document_id: str,
        database_id: str,
        command_kind: str,
        target_id: str,
        payload: Mapping[str, Any],
        expected_target_version: int | None,
        created_at: int,
    ) -> int:
        if command_kind == "database.create":
            if target_id != database_id:
                raise ValueError("database target binding mismatch")
            if expected_target_version not in {None, 0}:
                raise TargetVersionConflict(0)
            if con.execute(
                "SELECT 1 FROM workspace_collaboration_databases WHERE id=?", (database_id,)
            ).fetchone() is not None:
                raise TargetVersionConflict(1)
            con.execute(
                "INSERT INTO workspace_collaboration_databases("
                "id,document_id,title,row_version,created_at,updated_at) VALUES(?,?,?,1,?,?)",
                (database_id, document_id, _clean_name(payload.get("title"), field="title"), created_at, created_at),
            )
            return 1

        self._database_for_document(con, document_id, database_id)
        entity = command_kind.split(".")[1]
        verb = command_kind.split(".")[2]
        if entity == "property":
            table = "workspace_collaboration_database_properties"
            row = con.execute(
                f"SELECT * FROM {table} WHERE database_id=? AND id=?",
                (database_id, target_id),
            ).fetchone()
            if verb == "create":
                if row is not None:
                    raise TargetVersionConflict(int(row["row_version"]))
                if expected_target_version not in {None, 0}:
                    raise TargetVersionConflict(0)
                property_type = str(payload.get("type") or "").strip().lower()
                if property_type not in PROPERTY_TYPES:
                    raise ValueError("supported database property type required")
                config = _property_config(property_type, payload.get("config", {}))
                con.execute(
                    f"INSERT INTO {table}(id,database_id,name,property_type,position,"
                    "config_json,row_version,created_at,updated_at) VALUES(?,?,?,?,?,?,1,?,?)",
                    (
                        target_id,
                        database_id,
                        _clean_name(payload.get("name")),
                        property_type,
                        self._next_position(con, table, database_id),
                        _canonical_json(config),
                        created_at,
                        created_at,
                    ),
                )
                return 1
            if row is None:
                raise NotFoundOrDenied("not found or permission denied")
            self._check_target_version(row, expected_target_version)
            property_type = str(payload.get("type") or row["property_type"]).strip().lower()
            if property_type not in PROPERTY_TYPES:
                raise ValueError("supported database property type required")
            if property_type != str(row["property_type"]):
                used = con.execute(
                    "SELECT 1 FROM workspace_collaboration_database_cells WHERE property_id=? LIMIT 1",
                    (target_id,),
                ).fetchone()
                if used is not None:
                    raise ValueError("populated property type cannot change")
            config = _property_config(
                property_type,
                payload.get("config", json.loads(str(row["config_json"]))),
            )
            next_version = int(row["row_version"]) + 1
            con.execute(
                f"UPDATE {table} SET name=?,property_type=?,config_json=?,"
                "row_version=?,updated_at=? WHERE id=?",
                (
                    _clean_name(payload.get("name", row["name"])),
                    property_type,
                    _canonical_json(config),
                    next_version,
                    created_at,
                    target_id,
                ),
            )
            return next_version

        if entity == "view":
            table = "workspace_collaboration_database_views"
            row = con.execute(
                f"SELECT * FROM {table} WHERE database_id=? AND id=?",
                (database_id, target_id),
            ).fetchone()
            if verb == "create":
                if row is not None:
                    raise TargetVersionConflict(int(row["row_version"]))
                if expected_target_version not in {None, 0}:
                    raise TargetVersionConflict(0)
                view_type = str(payload.get("type") or "").strip().lower()
                if view_type not in VIEW_TYPES:
                    raise ValueError("supported database view type required")
                con.execute(
                    f"INSERT INTO {table}(id,database_id,name,view_type,position,config_json,"
                    "state,row_version,created_at,updated_at,archived_at) "
                    "VALUES(?,?,?,?,?,?,'active',1,?,?,NULL)",
                    (
                        target_id,
                        database_id,
                        _clean_name(payload.get("name")),
                        view_type,
                        self._next_position(con, table, database_id),
                        _canonical_json(_clean_object(payload.get("config", {}), field="view config")),
                        created_at,
                        created_at,
                    ),
                )
                return 1
            if row is None or str(row["state"]) != "active":
                raise NotFoundOrDenied("not found or permission denied")
            self._check_target_version(row, expected_target_version)
            next_version = int(row["row_version"]) + 1
            if verb == "archive":
                con.execute(
                    f"UPDATE {table} SET state='archived',archived_at=?,row_version=?,updated_at=? WHERE id=?",
                    (created_at, next_version, created_at, target_id),
                )
                return next_version
            view_type = str(payload.get("type") or row["view_type"]).strip().lower()
            if view_type not in VIEW_TYPES:
                raise ValueError("supported database view type required")
            config = _clean_object(
                payload.get("config", json.loads(str(row["config_json"]))), field="view config"
            )
            con.execute(
                f"UPDATE {table} SET name=?,view_type=?,config_json=?,row_version=?,updated_at=? WHERE id=?",
                (
                    _clean_name(payload.get("name", row["name"])),
                    view_type,
                    _canonical_json(config),
                    next_version,
                    created_at,
                    target_id,
                ),
            )
            return next_version

        table = "workspace_collaboration_database_templates"
        row = con.execute(
            f"SELECT * FROM {table} WHERE database_id=? AND id=?",
            (database_id, target_id),
        ).fetchone()
        if verb == "create":
            if row is not None:
                raise TargetVersionConflict(int(row["row_version"]))
            if expected_target_version not in {None, 0}:
                raise TargetVersionConflict(0)
            body = payload.get("body", [])
            defaults = payload.get("default_values", {})
            if isinstance(body, (str, bytes, bytearray)) or not isinstance(body, Sequence):
                raise ValueError("template body array required")
            con.execute(
                f"INSERT INTO {table}(id,database_id,name,body_json,default_values_json,"
                "state,row_version,created_at,updated_at,archived_at) "
                "VALUES(?,?,?,?,?,'active',1,?,?,NULL)",
                (
                    target_id,
                    database_id,
                    _clean_name(payload.get("name")),
                    _canonical_json(list(body)),
                    _canonical_json(_clean_object(defaults, field="template default values")),
                    created_at,
                    created_at,
                ),
            )
            return 1
        if row is None or str(row["state"]) != "active":
            raise NotFoundOrDenied("not found or permission denied")
        self._check_target_version(row, expected_target_version)
        next_version = int(row["row_version"]) + 1
        if verb == "archive":
            con.execute(
                f"UPDATE {table} SET state='archived',archived_at=?,row_version=?,updated_at=? WHERE id=?",
                (created_at, next_version, created_at, target_id),
            )
            return next_version
        body = payload.get("body", json.loads(str(row["body_json"])))
        if isinstance(body, (str, bytes, bytearray)) or not isinstance(body, Sequence):
            raise ValueError("template body array required")
        defaults = _clean_object(
            payload.get("default_values", json.loads(str(row["default_values_json"]))),
            field="template default values",
        )
        con.execute(
            f"UPDATE {table} SET name=?,body_json=?,default_values_json=?,"
            "row_version=?,updated_at=? WHERE id=?",
            (
                _clean_name(payload.get("name", row["name"])),
                _canonical_json(list(body)),
                _canonical_json(defaults),
                next_version,
                created_at,
                target_id,
            ),
        )
        return next_version

    def read_database(self, subject_id: str, database_id: str) -> dict[str, Any]:
        subject = self._require_active(subject_id)
        database = _identifier(database_id, field="database_id")
        with self._reader() as con:
            row = con.execute(
                "SELECT database.*,document.workspace_id FROM workspace_collaboration_databases database "
                "JOIN workspace_collaboration_documents document ON document.id=database.document_id "
                "WHERE database.id=?",
                (database,),
            ).fetchone()
            if row is None:
                raise NotFoundOrDenied("not found or permission denied")
            role, _sources = CollaborativeIAMAuthority._resolution_on_connection(
                con, subject, str(row["workspace_id"]), str(row["document_id"])
            )
            if role is None:
                raise NotFoundOrDenied("not found or permission denied")
            document = self._document(con, str(row["document_id"]))
            descriptor = self._editing_snapshot(con, document)
            raw = next(item for item in descriptor["databases"] if item["id"] == database)
            raw["effective_role"] = role
            raw["rows"] = self._materialize_rows(con, subject, raw)
            return raw

    def read_databases(self, subject_id: str, document_id: str) -> list[dict[str, Any]]:
        subject = self._require_active(subject_id)
        document = _identifier(document_id, field="document_id")
        with self._reader() as con:
            row = self._document(con, document)
            role, _sources = CollaborativeIAMAuthority._resolution_on_connection(
                con, subject, str(row["workspace_id"]), document
            )
            if role is None:
                raise NotFoundOrDenied("not found or permission denied")
            descriptor = self._editing_snapshot(con, row)
            result: list[dict[str, Any]] = []
            for item in descriptor["databases"]:
                value = dict(item)
                value["effective_role"] = role
                value["rows"] = self._materialize_rows(con, subject, value)
                result.append(value)
            return result

    @staticmethod
    def _materialize_rows(
        con: sqlite3.Connection, subject_id: str, database: Mapping[str, Any]
    ) -> list[dict[str, Any]]:
        properties = {str(item["id"]): item for item in database["properties"]}
        names = {str(item["name"]): str(item["id"]) for item in database["properties"]}
        raw_rows = {str(item["id"]): dict(item) for item in database["rows"]}
        relation_access: dict[str, bool] = {}
        for property_id, property_item in properties.items():
            if property_item["type"] != "relation":
                continue
            target_database_id = str(property_item["config"].get("target_database_id") or "")
            target = con.execute(
                "SELECT database.document_id,document.workspace_id "
                "FROM workspace_collaboration_databases database "
                "JOIN workspace_collaboration_documents document ON document.id=database.document_id "
                "WHERE database.id=?",
                (target_database_id,),
            ).fetchone()
            relation_access[property_id] = bool(
                target is not None
                and CollaborativeIAMAuthority._resolution_on_connection(
                    con,
                    subject_id,
                    str(target["workspace_id"]),
                    str(target["document_id"]),
                )[0]
                is not None
            )

        def raw_target_values(target_database_id: str, row_ids: Sequence[str], property_id: str) -> list[Any]:
            if not row_ids:
                return []
            placeholders = ",".join("?" for _item in row_ids)
            return [
                json.loads(str(item["value_json"]))
                for item in con.execute(
                    "SELECT cell.value_json FROM workspace_collaboration_database_cells cell "
                    "JOIN workspace_collaboration_database_rows row_item ON row_item.id=cell.row_id "
                    f"WHERE row_item.database_id=? AND cell.property_id=? AND cell.row_id IN ({placeholders}) "
                    "ORDER BY row_item.position,row_item.id",
                    (target_database_id, property_id, *row_ids),
                ).fetchall()
            ]

        result: list[dict[str, Any]] = []
        for row in raw_rows.values():
            cells = dict(row["cells"])
            for property_id, allowed in relation_access.items():
                if not allowed and property_id in cells:
                    cells[property_id] = []

            def resolve(key: str) -> Any:
                property_id = key if key in properties else names.get(key)
                return None if property_id is None else cells.get(property_id)

            computed: dict[str, Any] = {}
            for property_id, property_item in properties.items():
                if property_item["type"] == "formula":
                    computed[property_id] = _evaluate_formula(
                        str(property_item["config"]["expression"]), resolve
                    )
                elif property_item["type"] == "rollup":
                    config = property_item["config"]
                    relation_id = str(config["relation_property_id"])
                    relation_property = properties.get(relation_id)
                    if relation_property is None or not relation_access.get(relation_id, False):
                        computed[property_id] = None
                        continue
                    row_ids = cells.get(relation_id) or []
                    values = raw_target_values(
                        str(relation_property["config"]["target_database_id"]),
                        row_ids,
                        str(config["target_property_id"]),
                    )
                    aggregation = str(config["aggregation"])
                    if aggregation == "count":
                        value: Any = len(row_ids)
                    elif aggregation == "count_values":
                        value = len([item for item in values if item is not None])
                    elif aggregation == "show_unique":
                        value = list(dict.fromkeys(_canonical_json(item) for item in values))
                        value = [json.loads(item) for item in value]
                    else:
                        numeric = [item for item in values if isinstance(item, (int, float)) and not isinstance(item, bool)]
                        if not numeric:
                            value = None
                        elif aggregation == "sum":
                            value = sum(numeric)
                        elif aggregation == "average":
                            value = sum(numeric) / len(numeric)
                        elif aggregation == "min":
                            value = min(numeric)
                        else:
                            value = max(numeric)
                    computed[property_id] = value
            row["cells"] = cells
            row["computed"] = computed
            result.append(row)
        return result


__all__ = [
    "CollaborativeDatabaseAuthority",
    "DATABASE_FEATURE_CONTRACT",
    "DATABASE_SURFACE_COMMAND_CONTRACT",
    "PROPERTY_TYPES",
    "ROLLUP_AGGREGATIONS",
    "SCHEMA_COMMAND_KINDS",
    "SURFACE_COMMAND_KINDS",
    "VIEW_TYPES",
]
