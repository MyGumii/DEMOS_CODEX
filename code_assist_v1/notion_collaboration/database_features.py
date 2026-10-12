"""Canonical adopted database-feature authority for packet F0-P1.

Schema mutations require the full ``edit`` role and commit their domain change,
document sequence, operation result, audit event, and outbox event in one SQLite
transaction.  Content-only actors continue to use ``database.cell.set`` and can
never mutate schema.  Formula evaluation is a small deterministic expression
language; it never executes Python or user-provided code.
"""

from __future__ import annotations

import ast
from copy import deepcopy
import json
import os
from pathlib import Path
import sqlite3
import time
from typing import Any, Callable, Mapping, Sequence

from code_assist_v1 import notion_workspace_database_features_migration as migration
from code_assist_v1 import notion_workspace_healing_migration as migration_core
from code_assist_v1.notion_collaboration import database_activity, database_item_page_layout, database_row_content, database_row_appearance, database_row_page_placement, database_row_trash, database_template_default, database_trash
from code_assist_v1.notion_collaboration.canonical_authority import (
    AuthorityUnavailable,
    CanonicalAuthority,
    NotFoundOrDenied,
    OperationConflict,
    _canonical_json,
    _identifier,
    _operation_id,
    _request_sha256,
    _require_unlocked_or_temporary,
    _sha256,
    _stable_id,
)
from code_assist_v1.notion_collaboration.editing_authority import (
    CollaborativeEditingAuthority,
    DocumentSequenceConflict,
    TargetVersionConflict,
)
from code_assist_v1.notion_collaboration.database_file_links import clean_external_file_link
from code_assist_v1.notion_collaboration.iam_authority import (
    CollaborativeIAMAuthority,
    InMemoryIdentityDirectory,
)


DATABASE_FEATURE_CONTRACT = "code_assist.notion.collaborative_database_features.v1"
DATABASE_SURFACE_COMMAND_CONTRACT = (
    "code_assist.notion.collaborative_database_surface_command.v1"
)


def _clean_template_file_defaults(
    con: sqlite3.Connection, document_id: str, value: Any,
) -> list[dict[str, Any]]:
    """Keep template file defaults bound to active uploads on their own page."""
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise ValueError("file template default requires an array")
    if len(value) > 20:
        raise ValueError("file template default exceeds 20 files")
    clean: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, Mapping) or ("file_id" in item) == ("url" in item):
            raise ValueError("file template default requires a file or link")
        if "url" in item:
            clean.append(clean_external_file_link(item))
            continue
        if not set(item) <= {"file_id", "name", "mime_type", "size"}:
            raise ValueError("file template default has unsupported fields")
        file_id = _identifier(item["file_id"], field="file_id")
        if file_id in seen:
            raise ValueError("file template default contains a duplicate file")
        file_row = con.execute(
            "SELECT original_name,mime_type,size_bytes FROM workspace_collaboration_files "
            "WHERE id=? AND document_id=? AND state='active'",
            (file_id, document_id),
        ).fetchone()
        if file_row is None:
            raise NotFoundOrDenied("not found or permission denied")
        seen.add(file_id)
        clean.append({"file_id": file_id, "name": str(file_row["original_name"]),
                      "mime_type": str(file_row["mime_type"]),
                      "size": int(file_row["size_bytes"])})
    return clean


def _clean_template_default_values(
    con: sqlite3.Connection, document_id: str, database_id: str, value: Any,
) -> dict[str, Any]:
    defaults = _clean_object(value, field="template default values")
    file_properties = {str(row["id"]) for row in con.execute(
        "SELECT id FROM workspace_collaboration_database_properties "
        "WHERE database_id=? AND property_type='files'", (database_id,),
    )}
    for property_id in defaults.keys() & file_properties:
        defaults[property_id] = _clean_template_file_defaults(
            con, document_id, defaults[property_id])
    return defaults


def _database_last_edit(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "actor_subject_id": str(row["last_editor_subject_id"]),
        "edited_at": int(row["last_edited_at"]),
        "document_sequence": int(row["document_sequence"]),
    }
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
        "database.item_layout.set",
        "database.template_default.set",
        "database.property.delete",
        "database.row.create",
        "database.row.template.apply",
        "database.row.delete",
        "database.row.content.set",
        "database.row.appearance.set",
    }
)
SURFACE_COMMAND_MINIMUM_ROLE = {
    "database.update": "edit",
    "database.item_layout.set": "edit",
    "database.template_default.set": "edit",
    "database.property.delete": "edit",
    "database.row.create": "edit_content",
    "database.row.template.apply": "edit_content",
    "database.row.delete": "edit_content",
    "database.row.content.set": "edit_content",
    "database.row.appearance.set": "edit_content",
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


def _duplicate_row_blocks(blocks: Any, *, actor: str, document: str,
                          database: str, operation: str, source_row: str,
                          template_references: bool = False) -> list[dict[str, Any]]:
    """Give a copied item's nested blocks independent identities.

    File and page references remain on the same owning document. References
    among blocks inside this item follow their copied block identities. Template
    instantiation instead retains synced originals in the template and creates
    empty references to them, without copying their owned content into the item.
    """
    clean = database_row_content.clean_blocks(blocks)
    identities: dict[str, str] = {}
    tab_ids: dict[tuple[str, str], str] = {}
    view_ids: dict[tuple[str, str], str] = {}
    layout_ids: dict[str, str] = {}

    def children_of(block: Mapping[str, Any]) -> list[dict[str, Any]]:
        children = block.get("props", {}).get("children", [])
        return children if isinstance(children, list) and all(isinstance(child, dict) for child in children) else []

    def scan(items: list[dict[str, Any]], depth: int = 0) -> None:
        if depth > 64:
            raise ValueError("copied item body nesting is too deep")
        for block in items:
            old = _identifier(block.get("id"), field="block_id")
            if old in identities:
                raise ValueError("duplicate block identity in copied item")
            identities[old] = _stable_id("rowblock", actor, document, database,
                                         operation, source_row, old)
            props = block.get("props", {})
            layout = props.get("layout_group")
            if isinstance(layout, str) and layout:
                layout_ids.setdefault(layout, _stable_id("rowlayout", actor,
                    document, database, operation, source_row, layout))
            for tab in props.get("tabs", []) if block.get("type") == "tabs" else []:
                tab_id = _identifier(tab.get("id") if isinstance(tab, dict) else None,
                                     field="tab_id")
                tab_ids[(old, tab_id)] = _stable_id("rowtab", actor, document,
                    database, operation, source_row, old, tab_id)
            for view in props.get("views", []) if isinstance(props.get("views"), list) else []:
                view_id = _identifier(view.get("id") if isinstance(view, dict) else None,
                                      field="view_id")
                view_ids[(old, view_id)] = _stable_id("rowview", actor, document,
                    database, operation, source_row, old, view_id)
            scan(children_of(block), depth + 1)

    scan(clean)
    from .synced_blocks import group_id, index_blocks, origin_of, SyncedShapeUnavailable
    source_blocks = index_blocks(clean)
    synced_origins = {}
    for identity, source_block in source_blocks.items():
        if source_block.get("type") != "synced_block":
            continue
        origin = origin_of(source_block, document)
        synced_origins[identity] = origin
        if origin[0] == document and origin[1] in source_blocks:
            original = source_blocks[origin[1]]
            if (original.get("type") != "synced_block"
                    or original.get("props", {}).get("synced_from", "missing") is not None):
                raise SyncedShapeUnavailable("a copied synced reference must target an original")

    shared_content_ids: set[str] = set()
    if template_references and synced_origins:
        from .synced_tree import content_hierarchy
        originals = {identity for identity in synced_origins
                     if source_blocks[identity]["props"]["synced_from"] is None}
        if originals:
            hierarchy = content_hierarchy(clean)
            # Inline children, flat children, indentation and tab members remain
            # owned by their original. They must not become ordinary ghost blocks
            # or receive link targets for copies that were never instantiated.
            shared_content_ids = {identity for identity in source_blocks
                if originals.intersection(hierarchy.ancestors(identity))}

    from urllib.parse import quote, unquote, urlsplit, urlunsplit

    def local_block_url(value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme or parsed.netloc or not parsed.fragment.startswith("block-"):
            return value
        old_target = unquote(parsed.fragment[6:])
        if old_target not in identities or old_target in shared_content_ids:
            return value
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path,
            parsed.query, "block-" + quote(identities[old_target], safe="")))

    def rewrite(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        result = []
        for source in items:
            if source["id"] in shared_content_ids:
                continue
            block = deepcopy(source)
            result.append(block)
            old = block["id"]
            block["id"] = identities[old]
            props = block.get("props", {})
            controller = props.get("tab_group")
            if isinstance(controller, str):
                if isinstance(props.get("tab_id"), str):
                    props["tab_id"] = tab_ids.get((controller, props["tab_id"]), props["tab_id"])
                props["tab_group"] = identities.get(controller, controller)
            if isinstance(props.get("layout_group"), str):
                props["layout_group"] = layout_ids.get(props["layout_group"], props["layout_group"])
            if template_references and block.get("type") == "synced_block":
                block["content"] = ""
                props.pop("children", None)
                props.pop("rich_text_html", None)
            if isinstance(props.get("children"), list):
                if all(isinstance(child, dict) for child in props["children"]):
                    props["children"] = rewrite(props["children"])
                elif all(isinstance(child, str) for child in props["children"]):
                    props["children"] = [identities.get(child, child) for child in props["children"]]
                else:
                    raise ValueError("mixed copied item children")
            if block.get("type") == "synced_block":
                origin_document, origin_block = synced_origins[old]
                if not template_references and origin_document == document and origin_block in identities:
                    origin_block = identities[origin_block]
                props["sync_id"] = group_id(origin_document, origin_block)
                if template_references or props["synced_from"] is not None:
                    props["synced_from"] = {"document_id": origin_document, "block_id": origin_block}
            if block.get("type") == "tabs":
                tabs = props.get("tabs", [])
                if not isinstance(tabs, list):
                    raise ValueError("copied item tabs must be an array")
                tabs_for_block = {tab.get("id"): tab_ids[(old, tab["id"])]
                    for tab in tabs if isinstance(tab, dict) and (old, tab.get("id")) in tab_ids}
                for tab in tabs:
                    if not isinstance(tab, dict) or tab.get("id") not in tabs_for_block:
                        raise ValueError("copied item tab identity required")
                    tab["id"] = tabs_for_block[tab["id"]]
                if props.get("active_tab_id") in tabs_for_block:
                    props["active_tab_id"] = tabs_for_block[props["active_tab_id"]]
            if isinstance(props.get("views"), list):
                views_for_block = {view.get("id"): view_ids[(old, view["id"])]
                    for view in props["views"] if isinstance(view, dict) and (old, view.get("id")) in view_ids}
                for view in props["views"]:
                    if isinstance(view, dict) and view.get("id") in views_for_block:
                        view["id"] = views_for_block[view["id"]]
                if props.get("active_view_id") in views_for_block:
                    props["active_view_id"] = views_for_block[props["active_view_id"]]
            for field in ("url", "src"):
                if isinstance(props.get(field), str):
                    props[field] = local_block_url(props[field])
            if isinstance(props.get("rich_text_html"), str):
                from .page_copy_remap import _RichLinks
                props["rich_text_html"] = _RichLinks(props["rich_text_html"], local_block_url).result()
        return result

    return database_row_content.clean_blocks(rewrite(clean))


def _formula_property_ids(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise ValueError("formula copy property ids must be an object")
    return {_identifier(key, field="source_property_id"): _identifier(target, field="copied_property_id")
        for key, target in value.items()}


def _property_config(property_type: str, value: Any) -> dict[str, Any]:
    clean = _clean_object(value, field="property config")
    if property_type == "relation":
        clean["target_database_id"] = _identifier(
            str(clean.get("target_database_id") or ""), field="target_database_id"
        )
        if "reciprocal_property_id" in clean:
            clean["reciprocal_property_id"] = _identifier(
                str(clean["reciprocal_property_id"]),
                field="reciprocal_property_id",
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
        if "copy_property_ids" in clean:
            clean["copy_property_ids"] = _formula_property_ids(clean["copy_property_ids"])
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
        database_trash.require_active(con, database_id)
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

    def execute_cell_batch(
        self,
        actor_subject_id: str,
        document_id: str,
        *,
        database_id: str,
        target_id: str,
        payload: Mapping[str, Any],
        expected_document_sequence: int,
        expected_workspace_epoch: int,
        expected_document_epoch: int,
        operation_id: str,
        expected_target_version: int | None = None,
        fault: str | None = None,
        _linked_scope=None,
    ) -> dict[str, Any]:
        """Commit a cell range, its versions, audit and outbox as one operation.

        This additive surface command uses the existing cell authority and
        schema. A failed cell or revoked grant rolls back the complete range.
        """
        actor = _identifier(actor_subject_id, field="actor_subject_id")
        document = _identifier(document_id, field="document_id")
        database = _identifier(database_id, field="database_id")
        if target_id != database or expected_target_version is not None:
            raise ValueError("cell batch must target its database with per-cell versions")
        clean_payload = _clean_object(payload, field="cell batch payload")
        if set(clean_payload) != {"cells"}:
            raise ValueError("cell batch payload requires only cells")
        cells = clean_payload["cells"]
        if not isinstance(cells, list) or not 1 <= len(cells) <= 1000:
            raise ValueError("cell batch requires 1 to 1000 cells")
        clean_cells = []
        seen = set()
        for cell in cells:
            if not isinstance(cell, dict) or set(cell) != {"row_id", "property_id", "value", "expected_target_version"}:
                raise ValueError("cell batch entry fields do not match the contract")
            row_id = _identifier(cell["row_id"], field="row_id")
            property_id = _identifier(cell["property_id"], field="property_id")
            version = cell["expected_target_version"]
            if isinstance(version, bool) or not isinstance(version, int) or version < 0:
                raise ValueError("cell batch requires a non-negative version for every cell")
            if (row_id, property_id) in seen:
                raise ValueError("cell batch targets must be unique")
            seen.add((row_id, property_id))
            clean_cells.append({**cell, "row_id": row_id, "property_id": property_id})
        for name, value in (
            ("document sequence", expected_document_sequence),
            ("workspace epoch", expected_workspace_epoch),
            ("document epoch", expected_document_epoch),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"positive {name} required")
        operation = _operation_id(operation_id)
        action = "collaboration.database.cell_batch"
        request = {
            "document_id": document, "database_id": database, "cells": clean_cells,
            "expected_document_sequence": expected_document_sequence,
            "expected_workspace_epoch": expected_workspace_epoch,
            "expected_document_epoch": expected_document_epoch,
        }
        if _linked_scope is not None:
            from .linked_databases import _LinkedCellScope
            if not isinstance(_linked_scope, _LinkedCellScope):
                raise ValueError("prepared linked cell scope required")
            request["linked_instance"] = _linked_scope.request_context()
        request_hash = _request_sha256(actor, action, request)
        with self._transaction() as con:
            def authorize() -> None:
                self._exact_access(
                    con, actor, document, minimum_role="edit_content",
                    expected_workspace_epoch=expected_workspace_epoch,
                    expected_document_epoch=expected_document_epoch,
                )
                self._database_for_document(con, document, database)
                if _linked_scope is not None:
                    _linked_scope.authorize(self, con, actor, document, database, replay=True)

            replay = self._replay(
                con, actor=actor, action=action, operation_id=operation,
                request_sha256=request_hash, authorize=authorize,
            )
            if replay is not None:
                return replay
            workspace, document_row, role = self._exact_access(
                con, actor, document, minimum_role="edit_content",
                expected_workspace_epoch=expected_workspace_epoch,
                expected_document_epoch=expected_document_epoch,
            )
            _require_unlocked_or_temporary(con, document_row, actor)
            self._database_for_document(con, document, database)
            if _linked_scope is not None:
                _linked_scope.authorize(self, con, actor, document, database, replay=False)
            sequence = int(document_row["document_sequence"])
            if sequence != expected_document_sequence:
                raise DocumentSequenceConflict(sequence)
            next_sequence, now = sequence + 1, int(time.time())
            changed = []
            reciprocal_documents: dict[str, set[str]] = {}
            for cell in clean_cells:
                bound = con.execute(
                    "SELECT 1 FROM workspace_collaboration_database_rows row_item "
                    "JOIN workspace_collaboration_database_properties property "
                    "ON property.database_id=row_item.database_id "
                    "WHERE row_item.database_id=? AND row_item.id=? AND property.id=?",
                    (database, cell["row_id"], cell["property_id"]),
                ).fetchone()
                if bound is None:
                    raise NotFoundOrDenied("not found or permission denied")
                cell_result = self._apply_typed_domain(
                    con, actor_subject_id=actor, document_id=document,
                    command_id=_stable_id("cellbatch", actor, document, operation),
                    command_kind="database.cell.set",
                    target_id=f"{cell['row_id']}:{cell['property_id']}",
                    payload={key: cell[key] for key in ("row_id", "property_id", "value")},
                    expected_target_version=cell["expected_target_version"],
                    document_sequence=next_sequence, created_at=now,
                )
                reciprocal_document = cell_result.pop("_reciprocal_document_id", None)
                reciprocal_database = cell_result.pop("_reciprocal_database_id", None)
                if reciprocal_document is not None:
                    reciprocal_documents.setdefault(str(reciprocal_document), set()).add(
                        str(reciprocal_database))
                changed.append(cell_result)
            content_hash = _sha256(_canonical_json(self._editing_snapshot(con, self._document(con, document))))
            con.execute(
                "UPDATE workspace_collaboration_documents SET document_sequence=?,"
                "content_sha256=?,row_version=row_version+1,updated_at=? WHERE id=?",
                (next_sequence, content_hash, now, document),
            )
            from .database_relation_pair import commit_reciprocal_documents
            reciprocal_effects = commit_reciprocal_documents(
                self, con, actor=actor, source_document_id=document,
                operation_id=operation, affected=reciprocal_documents, now=now,
                effect_kind="batch")
            result = {
                "contract": "code_assist.notion.collaborative_database_cell_batch.v1",
                "workspace_id": str(workspace["id"]), "document_id": document,
                "database_id": database, "target_id": database,
                "command_kind": "database.cells.set", "document_sequence": next_sequence,
                "effective_role": role, "content_sha256": content_hash, "cells": changed,
            }
            if reciprocal_effects:
                result["reciprocal_documents"] = reciprocal_effects
            if _linked_scope is not None:
                result["linked_instance"] = {"document_id": _linked_scope.document, "block_id": _linked_scope.block}
            return self._finish(
                con, actor=actor, action=action, operation_id=operation,
                request_sha256=request_hash, workspace_id=str(workspace["id"]),
                document_id=document, workspace_epoch=expected_workspace_epoch,
                document_epoch=expected_document_epoch, document_sequence=next_sequence,
                event_type="database.surface.committed", event_payload=result,
                result=result, fault=fault,
            )

    def execute_row_icon_batch(
        self, actor_subject_id: str, document_id: str, *, database_id: str,
        target_id: str, payload: Mapping[str, Any],
        expected_document_sequence: int, expected_workspace_epoch: int,
        expected_document_epoch: int, operation_id: str,
        expected_target_version: int | None = None, fault: str | None = None,
    ) -> dict[str, Any]:
        """Apply one icon to 1–100 active rows in a single replayable transaction."""
        actor = _identifier(actor_subject_id, field="actor_subject_id")
        document = _identifier(document_id, field="document_id")
        database = _identifier(database_id, field="database_id")
        if target_id != database or expected_target_version is not None:
            raise ValueError("row icon batch must target its database with per-row versions")
        clean_payload = _clean_object(payload, field="row icon batch payload")
        if set(clean_payload) != {"icon", "rows"}:
            raise ValueError("row icon batch requires icon and rows")
        icon = database_row_appearance.clean_updates({"icon": clean_payload["icon"]})["icon"]
        rows = clean_payload["rows"]
        if not isinstance(rows, list) or not 1 <= len(rows) <= 100:
            raise ValueError("row icon batch requires 1 to 100 rows")
        clean_rows, seen = [], set()
        for item in rows:
            if not isinstance(item, dict) or set(item) != {"row_id", "expected_target_version"}:
                raise ValueError("row icon batch entry fields do not match the contract")
            row_id = _identifier(item["row_id"], field="row_id")
            version = item["expected_target_version"]
            if isinstance(version, bool) or not isinstance(version, int) or version < 1:
                raise ValueError("row icon batch requires a positive version for every row")
            if row_id in seen:
                raise ValueError("row icon batch targets must be unique")
            seen.add(row_id)
            clean_rows.append({"row_id": row_id, "expected_target_version": version})
        for name, value in (("document sequence", expected_document_sequence),
                            ("workspace epoch", expected_workspace_epoch),
                            ("document epoch", expected_document_epoch)):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"positive {name} required")
        operation = _operation_id(operation_id)
        request = {"document_id": document, "database_id": database,
                   "icon": icon, "rows": clean_rows,
                   "expected_document_sequence": expected_document_sequence,
                   "expected_workspace_epoch": expected_workspace_epoch,
                   "expected_document_epoch": expected_document_epoch}
        action = "collaboration.database.row_icon_batch"
        request_hash = _request_sha256(actor, action, request)
        image = database_row_appearance.prepare(self, actor, document, {"icon": icon})
        with self._transaction() as con:
            if not database_row_trash.installed(con):
                raise AuthorityUnavailable("database-row icon batch requires the 2423 authority")
            def authorize() -> None:
                self._exact_access(con, actor, document, minimum_role="edit_content",
                    expected_workspace_epoch=expected_workspace_epoch,
                    expected_document_epoch=expected_document_epoch)
                self._database_for_document(con, document, database)

            replay = self._replay(con, actor=actor, action=action,
                                  operation_id=operation, request_sha256=request_hash,
                                  authorize=authorize)
            if replay is not None:
                return replay
            workspace, document_row, role = self._exact_access(
                con, actor, document, minimum_role="edit_content",
                expected_workspace_epoch=expected_workspace_epoch,
                expected_document_epoch=expected_document_epoch)
            _require_unlocked_or_temporary(con, document_row, actor)
            self._database_for_document(con, document, database)
            sequence = int(document_row["document_sequence"])
            if sequence != expected_document_sequence:
                raise DocumentSequenceConflict(sequence)
            for item in clean_rows:
                row = con.execute("SELECT * FROM workspace_collaboration_database_rows "
                    "WHERE database_id=? AND id=?", (database, item["row_id"])).fetchone()
                if row is None:
                    raise NotFoundOrDenied("not found or permission denied")
                database_row_trash.require_active(con, database, item["row_id"])
                self._check_target_version(row, item["expected_target_version"])
            next_sequence, now = sequence + 1, int(time.time())
            changed = []
            for item in clean_rows:
                result = database_row_appearance.set_appearance(
                    con, item["row_id"], {"icon": icon}, document_id=document,
                    expected_version=item["expected_target_version"], now=now,
                    image=image)
                changed.append({"row_id": item["row_id"], **result})
            content_hash = _sha256(_canonical_json(
                self._editing_snapshot(con, self._document(con, document))))
            con.execute("UPDATE workspace_collaboration_documents SET document_sequence=?,"
                "content_sha256=?,row_version=row_version+1,updated_at=? WHERE id=?",
                (next_sequence, content_hash, now, document))
            result = {"contract": "code_assist.notion.collaborative_database_row_icon_batch.v1",
                "workspace_id": str(workspace["id"]), "document_id": document,
                "database_id": database, "target_id": database,
                "command_kind": "database.rows.icon.set",
                "document_sequence": next_sequence, "effective_role": role,
                "content_sha256": content_hash, "rows": changed}
            return self._finish(con, actor=actor, action=action,
                operation_id=operation, request_sha256=request_hash,
                workspace_id=str(workspace["id"]), document_id=document,
                workspace_epoch=expected_workspace_epoch,
                document_epoch=expected_document_epoch,
                document_sequence=next_sequence, event_type="database.surface.committed",
                event_payload=result, result=result, fault=fault)

    def execute_row_duplicate_batch(
        self, actor_subject_id: str, document_id: str, *, database_id: str,
        target_id: str, payload: Mapping[str, Any],
        expected_document_sequence: int, expected_workspace_epoch: int,
        expected_document_epoch: int, operation_id: str,
        expected_target_version: int | None = None, fault: str | None = None,
    ) -> dict[str, Any]:
        """Duplicate selected active items immediately after their sources."""
        actor = _identifier(actor_subject_id, field="actor_subject_id")
        document = _identifier(document_id, field="document_id")
        database = _identifier(database_id, field="database_id")
        if target_id != database or expected_target_version is not None:
            raise ValueError("row duplication must target its database with per-row versions")
        clean_payload = _clean_object(payload, field="row duplication payload")
        if set(clean_payload) != {"rows"}:
            raise ValueError("row duplication requires only rows")
        rows = clean_payload["rows"]
        if not isinstance(rows, list) or not 1 <= len(rows) <= 100:
            raise ValueError("row duplication requires 1 to 100 rows")
        clean_rows, seen = [], set()
        for item in rows:
            if not isinstance(item, dict) or set(item) != {"row_id", "expected_target_version"}:
                raise ValueError("row duplication entry fields do not match the contract")
            row_id = _identifier(item["row_id"], field="row_id")
            version = item["expected_target_version"]
            if isinstance(version, bool) or not isinstance(version, int) or version < 1:
                raise ValueError("row duplication requires a positive version for every row")
            if row_id in seen:
                raise ValueError("row duplication targets must be unique")
            seen.add(row_id)
            clean_rows.append({"row_id": row_id, "expected_target_version": version})
        for name, value in (("document sequence", expected_document_sequence),
                            ("workspace epoch", expected_workspace_epoch),
                            ("document epoch", expected_document_epoch)):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"positive {name} required")
        operation = _operation_id(operation_id)
        action = "collaboration.database.row_duplicate_batch"
        request_hash = _request_sha256(actor, action, {
            "document_id": document, "database_id": database, "rows": clean_rows,
            "expected_document_sequence": expected_document_sequence,
            "expected_workspace_epoch": expected_workspace_epoch,
            "expected_document_epoch": expected_document_epoch,
        })
        with self._transaction() as con:
            if not database_row_trash.installed(con):
                raise AuthorityUnavailable("database-row duplication requires the 2423 authority")

            def authorize() -> None:
                self._exact_access(con, actor, document, minimum_role="edit_content",
                    expected_workspace_epoch=expected_workspace_epoch,
                    expected_document_epoch=expected_document_epoch)
                self._database_for_document(con, document, database)

            replay = self._replay(con, actor=actor, action=action,
                operation_id=operation, request_sha256=request_hash,
                authorize=authorize)
            if replay is not None:
                return replay
            workspace, document_row, role = self._exact_access(
                con, actor, document, minimum_role="edit_content",
                expected_workspace_epoch=expected_workspace_epoch,
                expected_document_epoch=expected_document_epoch)
            _require_unlocked_or_temporary(con, document_row, actor)
            self._database_for_document(con, document, database)
            sequence = int(document_row["document_sequence"])
            if sequence != expected_document_sequence:
                raise DocumentSequenceConflict(sequence)
            copied: dict[str, str] = {}
            for item in clean_rows:
                source = con.execute("SELECT * FROM workspace_collaboration_database_rows "
                    "WHERE database_id=? AND id=?", (database, item["row_id"])).fetchone()
                if source is None:
                    raise NotFoundOrDenied("not found or permission denied")
                database_row_trash.require_active(con, database, item["row_id"])
                self._check_target_version(source, item["expected_target_version"])
                new_id = _stable_id("row_copy", actor, document, database,
                                    operation, item["row_id"])
                if con.execute("SELECT 1 FROM workspace_collaboration_database_rows WHERE id=?",
                               (new_id,)).fetchone() is not None:
                    raise AuthorityUnavailable("copied row identity already exists")
                copied[item["row_id"]] = new_id
            ordered = [str(item["id"]) for item in con.execute(
                "SELECT id FROM workspace_collaboration_database_rows WHERE database_id=? "
                "ORDER BY position,id", (database,)).fetchall()]
            now, next_sequence = int(time.time()), sequence + 1
            next_position = len(ordered)
            for source_id, new_id in copied.items():
                con.execute("INSERT INTO workspace_collaboration_database_rows("
                    "id,database_id,position,row_version,created_at,updated_at) "
                    "VALUES(?,?,?,1,?,?)", (new_id, database, next_position, now, now))
                next_position += 1
                for cell in con.execute("SELECT cell.property_id,cell.value_json,"
                    "property.database_id,property.property_type "
                    "FROM workspace_collaboration_database_cells cell "
                    "JOIN workspace_collaboration_database_properties property "
                    "ON property.id=cell.property_id WHERE cell.row_id=?", (source_id,)).fetchall():
                    if (str(cell["database_id"]) != database
                            and str(cell["property_type"]) != "relation"):
                        raise AuthorityUnavailable(
                            "copied cell outside current database is not a relation")
                    con.execute("INSERT INTO workspace_collaboration_database_cells("
                        "row_id,property_id,value_json,row_version,updated_at) VALUES(?,?,?,1,?)",
                        (new_id, cell["property_id"], cell["value_json"], now))
                body = con.execute("SELECT blocks_json FROM workspace_collaboration_database_row_bodies "
                    "WHERE row_id=?", (source_id,)).fetchone()
                if body is not None:
                    blocks = _duplicate_row_blocks(json.loads(str(body["blocks_json"])),
                        actor=actor, document=document, database=database,
                        operation=operation, source_row=source_id)
                    con.execute("INSERT INTO workspace_collaboration_database_row_bodies(row_id,blocks_json) "
                        "VALUES(?,?)", (new_id, _canonical_json(blocks)))
                appearance = con.execute("SELECT appearance_json FROM "
                    "workspace_collaboration_database_row_appearance WHERE row_id=?",
                    (source_id,)).fetchone()
                if appearance is not None:
                    con.execute("INSERT INTO workspace_collaboration_database_row_appearance("
                        "row_id,appearance_json) VALUES(?,?)", (new_id, appearance["appearance_json"]))
            reordered: list[str] = []
            for source_id in ordered:
                reordered.append(source_id)
                if source_id in copied:
                    reordered.append(copied[source_id])
            for position, row_id in enumerate(reordered):
                con.execute("UPDATE workspace_collaboration_database_rows SET position=? WHERE id=?",
                            (position, row_id))
            content_hash = _sha256(_canonical_json(
                self._editing_snapshot(con, self._document(con, document))))
            con.execute("UPDATE workspace_collaboration_documents SET document_sequence=?,"
                "content_sha256=?,row_version=row_version+1,updated_at=? WHERE id=?",
                (next_sequence, content_hash, now, document))
            database_activity.touch(con, database_ids=(database,), document_id=document,
                actor_subject_id=actor, edited_at=now, document_sequence=next_sequence)
            result = {"contract": "code_assist.notion.collaborative_database_row_duplicate_batch.v1",
                "workspace_id": str(workspace["id"]), "document_id": document,
                "database_id": database, "target_id": database,
                "command_kind": "database.rows.duplicate",
                "document_sequence": next_sequence, "effective_role": role,
                "content_sha256": content_hash,
                "rows": [{"source_row_id": source_id, "row_id": copied[source_id],
                          "position": reordered.index(copied[source_id]), "target_version": 1}
                         for source_id in ordered if source_id in copied]}
            return self._finish(con, actor=actor, action=action,
                operation_id=operation, request_sha256=request_hash,
                workspace_id=str(workspace["id"]), document_id=document,
                workspace_epoch=expected_workspace_epoch,
                document_epoch=expected_document_epoch,
                document_sequence=next_sequence, event_type="database.surface.committed",
                event_payload=result, result=result, fault=fault)

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
        expected_database_epoch: int | None = None,
        _linked_scope=None,
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
        if _linked_scope is not None:
            # A linked view may edit templates with a database grant, but must
            # not gain the source page's general schema authority.
            if (kind not in {"database.template.create", "database.template.update"}
                    or set(clean_payload) != {"name", "body", "default_values"}
                    or expected_database_epoch is None):
                raise ValueError("scoped linked template command requires exact fields")
            if kind == "database.template.create" and (
                    clean_payload["body"] != [] or clean_payload["default_values"] != {}
                    or target_version != 0):
                raise ValueError("scoped linked template creation requires a blank template")
            if kind == "database.template.update" and (
                    not isinstance(clean_payload["body"], list)
                    or not isinstance(clean_payload["default_values"], dict)
                    or target_version is None or target_version < 1):
                raise ValueError("scoped linked template update requires a versioned draft")
            database_epoch = int(expected_database_epoch)
            if database_epoch < 1:
                raise ValueError("positive database access epoch required")
        else:
            database_epoch = None
        action = ("collaboration.database_schema_command_scoped" if _linked_scope is not None
                  else "collaboration.database_schema_command")
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
        if _linked_scope is not None:
            request["expected_database_epoch"] = database_epoch
            request["linked_instance"] = _linked_scope.request_context()
        request_sha256 = _request_sha256(actor, action, request)
        with self._transaction() as con:
            def authorize(*, replay: bool):
                if _linked_scope is not None:
                    from . import database_access
                    access = database_access.require_write_on_connection(
                        self, con, actor, _linked_scope.workspace, database,
                        minimum_role="edit",
                        expected_workspace_epoch=workspace_epoch,
                        expected_document_epoch=document_epoch,
                        expected_database_epoch=database_epoch,
                    )
                    if access.document_id != document:
                        raise NotFoundOrDenied("not found or permission denied")
                    _linked_scope.authorize(self, con, actor, access, replay=replay)
                    if (kind == "database.template.update"
                            and database_access._template_body_has_unscoped_reference(clean_payload["body"])):
                        self._authorized_document(con, actor, document)
                    return _linked_scope.workspace, self._document(con, document), access.role
                workspace, document_row, role = self._exact_access(
                    con, actor, document, minimum_role="edit",
                    expected_workspace_epoch=workspace_epoch,
                    expected_document_epoch=document_epoch,
                )
                return str(workspace["id"]), document_row, role

            replay = self._replay(
                con,
                actor=actor,
                action=action,
                operation_id=operation,
                request_sha256=request_sha256,
                authorize=lambda: authorize(replay=True),
            )
            if replay is not None:
                return replay
            workspace_id, document_row, role = authorize(replay=False)
            if con.execute(
                "SELECT 1 FROM workspace_collaboration_database_schema_commands "
                "WHERE actor_subject_id=? AND document_id=? AND operation_id=?",
                (actor, document, operation),
            ).fetchone() is not None:
                raise OperationConflict("operation_id reused with a different schema request")
            _require_unlocked_or_temporary(con, document_row, actor)
            if _linked_scope is not None and kind == "database.template.update":
                from . import database_access
                from .database_move import _file_ids
                body = database_row_content.clean_blocks(clean_payload["body"])
                defaults = clean_payload["default_values"]
                if database_access._template_body_has_unscoped_reference(defaults):
                    raise NotFoundOrDenied("not found or permission denied")
                current_template = con.execute(
                    "SELECT body_json,default_values_json FROM "
                    "workspace_collaboration_database_templates "
                    "WHERE database_id=? AND id=? AND state='active'",
                    (database, target),
                ).fetchone()
                if current_template is None:
                    raise NotFoundOrDenied("not found or permission denied")
                current_body = database_row_content.clean_blocks(json.loads(str(current_template["body_json"])))
                current_defaults = json.loads(str(current_template["default_values_json"]))
                from .linked_template_body import retained_body
                retained, needs_parent_read = retained_body(body, current_body)
                if not retained or database_access._template_body_has_unscoped_reference(current_defaults):
                    raise NotFoundOrDenied("not found or permission denied")
                if needs_parent_read:
                    self._authorized_document(con, actor, document)
                visible_files = database_access._visible_file_ids_on_connection(
                    con, database, actor_subject_id=actor)
                if not (_file_ids(body) | _file_ids(defaults)
                        | _file_ids(current_body) | _file_ids(current_defaults)) <= visible_files:
                    raise NotFoundOrDenied("not found or permission denied")
                properties = {str(row["id"]): row for row in con.execute(
                    "SELECT id,property_type,config_json FROM "
                    "workspace_collaboration_database_properties WHERE database_id=?",
                    (database,),
                ).fetchall()}
                if not set(defaults) <= set(properties):
                    raise ValueError("template default property is unavailable")
                def require_relation_target(target_id, minimum_role):
                    target_access = database_access.resolve_on_connection(
                        self, con, actor, workspace_id, target_id)
                    if (database_access.PAGE_ROLE_LEVEL[target_access.role]
                            < database_access.PAGE_ROLE_LEVEL[minimum_role]):
                        raise NotFoundOrDenied("not found or permission denied")
                for property_id in set(defaults) | set(current_defaults):
                    property_row = properties.get(property_id)
                    if property_row is None:
                        raise NotFoundOrDenied("not found or permission denied")
                    if property_row["property_type"] == "relation":
                        config = json.loads(str(property_row["config_json"]))
                        target_database = str(config.get("target_database_id") or "")
                        if target_database and target_database != database:
                            target_access = database_access.resolve_on_connection(
                                self, con, actor, workspace_id, target_database)
                            if (config.get("reciprocal_property_id")
                                    and target_access.role not in {
                                        "edit_content", "edit", "full_access"}):
                                raise NotFoundOrDenied("not found or permission denied")
                        if property_id in defaults:
                            defaults[property_id] = self._clean_database_cell_value(
                                con, actor_subject_id=actor,
                                property_row=property_row,
                                value=defaults[property_id],
                                target_database_access=require_relation_target,
                            )
                clean_payload["body"] = body
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
                actor=actor,
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
                "contract": ("code_assist.notion.database_sharing.v1" if _linked_scope is not None
                             else DATABASE_FEATURE_CONTRACT),
                "workspace_id": workspace_id,
                "database_id": database,
                "command_kind": kind,
                "target_id": target,
                "target_version": result_target_version,
                "document_sequence": next_sequence,
                "effective_role": role,
            }
            if _linked_scope is not None:
                result["database_epoch"] = database_epoch
                result["linked_instance"] = {
                    "document_id": _linked_scope.instance_document,
                    "block_id": _linked_scope.block,
                }
            else:
                result["document_id"] = document
                result["content_sha256"] = content_sha256
            command_id = _stable_id(
                "dbcmdlinked" if _linked_scope is not None else "dbcmd",
                actor, document, operation,
            )
            con.execute(
                "INSERT INTO workspace_collaboration_database_schema_commands("
                "id,workspace_id,document_id,database_id,actor_subject_id,operation_id,"
                "command_kind,target_id,expected_document_sequence,expected_target_version,"
                "result_target_version,payload_json,result_json,document_sequence,"
                "workspace_epoch,document_epoch,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    command_id,
                    workspace_id,
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
                workspace_id=workspace_id,
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
        if kind in {"database.row.content.set","database.row.appearance.set",
                    "database.row.template.apply"} and (
            isinstance(expected_target_version, bool)
            or not isinstance(expected_target_version, int)
            or expected_target_version < 1
        ):
            raise ValueError("database item requires a positive expected_target_version")
        if kind in {"database.item_layout.set", "database.template_default.set"} and (
            isinstance(expected_target_version, bool)
            or not isinstance(expected_target_version, int)
            or expected_target_version < 0
        ):
            raise ValueError("database item-page layout requires an expected_target_version")
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
        appearance_file = database_row_appearance.prepare(self,actor,document,clean_payload) if kind=='database.row.appearance.set' else None
        with self._transaction() as con:
            def authorize():
                access = self._exact_access(
                    con,
                    actor,
                    document,
                    minimum_role=SURFACE_COMMAND_MINIMUM_ROLE[kind],
                    expected_workspace_epoch=workspace_epoch,
                    expected_document_epoch=document_epoch,
                )
                if kind == "database.row.appearance.set":
                    try:
                        _require_unlocked_or_temporary(con, access[1], actor)
                    except PermissionError:
                        raise PermissionError("unlock the source page before changing item appearance") from None
                return access

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
            workspace, document_row, role = authorize()
            _require_unlocked_or_temporary(con, document_row, actor)
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
                appearance_file=appearance_file,
                actor=actor,
            )
            reciprocal_changes = domain.pop("_reciprocal_documents", {})
            next_sequence = current_sequence + 1
            refreshed = self._document(con, document)
            content_sha256 = _sha256(_canonical_json(self._editing_snapshot(con, refreshed)))
            con.execute(
                "UPDATE workspace_collaboration_documents SET document_sequence=?,"
                "content_sha256=?,row_version=row_version+1,updated_at=? WHERE id=?",
                (next_sequence, content_sha256, now, document),
            )
            if reciprocal_changes:
                from .database_relation_pair import commit_reciprocal_documents
                reciprocal_effects = commit_reciprocal_documents(
                    self, con, actor=actor, source_document_id=document,
                    operation_id=operation, affected=reciprocal_changes, now=now,
                    effect_kind="template",
                )
                if reciprocal_effects:
                    domain["reciprocal_documents"] = reciprocal_effects
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
        actor: str,
        appearance_file=None,
        target_database_access=None,
    ) -> dict[str, Any]:
        database = self._database_for_document(con, document_id, database_id)
        if command_kind == "database.template_default.set":
            if target_id != database_id or set(payload) != {"template_id"}:
                raise ValueError("database template default target or payload is invalid")
            return {"target_version": database_template_default.set_default(
                con, database_id, payload["template_id"],
                expected_version=expected_target_version, actor=actor, now=created_at,
            )}
        if command_kind == "database.item_layout.set":
            if target_id != database_id:
                raise ValueError("database item-page layout target binding mismatch")
            if set(payload) not in ({"layout"}, {"layout", "new_properties"}):
                raise ValueError("database item-page layout payload is invalid")
            if not database_item_page_layout.installed(con):
                raise AuthorityUnavailable("database item-page layout is not installed")
            current_layout = database_item_page_layout.read(con, database_id)
            layout_version = current_layout["version"] if current_layout else 0
            if expected_target_version != layout_version:
                raise TargetVersionConflict(layout_version)
            new_properties = payload.get("new_properties", [])
            if not isinstance(new_properties, list) or len(new_properties) > 20:
                raise ValueError("database item-page new properties are invalid")
            if new_properties:
                count = con.execute(
                    "SELECT COUNT(*) FROM workspace_collaboration_database_properties "
                    "WHERE database_id=?", (database_id,),
                ).fetchone()[0]
                if count + len(new_properties) > 500:
                    raise ValueError("database property limit exceeded")
            created_property_ids = []
            for item in new_properties:
                if not isinstance(item, Mapping) or set(item) != {"id", "name", "type", "config"}:
                    raise ValueError("database item-page new property is invalid")
                property_id = _identifier(item["id"], field="property_id")
                if item["type"] == "title":
                    raise ValueError("database item-page title property cannot be created")
                self._apply_schema_domain(
                    con, document_id=document_id, database_id=database_id,
                    command_kind="database.property.create", target_id=property_id,
                    payload={"name": item["name"], "type": item["type"], "config": item["config"]},
                    expected_target_version=0, created_at=created_at, actor=actor,
                )
                created_property_ids.append(property_id)
            result = database_item_page_layout.set_layout(
                con, database_id, {"layout": payload["layout"]},
                expected_version=expected_target_version, actor=actor, now=created_at,
            )
            if created_property_ids:
                result["created_property_ids"] = created_property_ids
            return result
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
            if (str(row["property_type"]) == "relation"
                    and json.loads(str(row["config_json"])).get(
                        "reciprocal_property_id")):
                raise ValueError("paired relation must be disabled before deletion")
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
            database_item_page_layout.forget_property(
                con, database_id, target_id, actor=actor, now=created_at,
            )
            return {"target_version": deleted_version, "deleted": True}

        if command_kind == "database.row.create":
            if set(payload) not in (set(), {"template_id"}):
                raise ValueError("database row creation payload is invalid")
            template_id = None
            template_body: list[dict[str, Any]] = []
            template_defaults: dict[str, Any] = {}
            selected_template = (payload.get("template_id") if "template_id" in payload
                                 else database_template_default.read(con, database_id)["template_id"])
            if selected_template is not None:
                template_id = _identifier(selected_template, field="template_id")
                template = con.execute(
                    "SELECT body_json,default_values_json FROM "
                    "workspace_collaboration_database_templates "
                    "WHERE database_id=? AND id=? AND state='active'",
                    (database_id, template_id),
                ).fetchone()
                if template is None:
                    raise NotFoundOrDenied("not found or permission denied")
                template_body = database_row_content.clean_blocks(
                    json.loads(str(template["body_json"])))
                template_defaults = json.loads(str(template["default_values_json"]))
                if not isinstance(template_defaults, dict):
                    raise ValueError("template default values must be an object")
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
            applied_defaults: dict[str, Any] = {}
            reciprocal_documents: dict[str, set[str]] = {}
            for property_id, value in template_defaults.items():
                property_id = _identifier(property_id, field="property_id")
                property_row = con.execute(
                    "SELECT property.*,property.database_id AS bound_database_id "
                    "FROM workspace_collaboration_database_properties property "
                    "WHERE database_id=? AND id=?",
                    (database_id, property_id),
                ).fetchone()
                if property_row is None:
                    raise NotFoundOrDenied("not found or permission denied")
                clean_value = (_clean_template_file_defaults(con, document_id, value)
                    if str(property_row["property_type"]) == "files"
                    else self._clean_database_cell_value(
                        con, actor_subject_id=actor, property_row=property_row,
                        value=value, target_database_access=target_database_access))
                con.execute(
                    "INSERT INTO workspace_collaboration_database_cells("
                    "row_id,property_id,value_json,row_version,updated_at) VALUES(?,?,?,1,?)",
                    (target_id, property_id, _canonical_json(clean_value), created_at),
                )
                applied_defaults[property_id] = clean_value
                if str(property_row["property_type"]) == "relation":
                    from .database_relation_pair import apply_reciprocal_cell_edit
                    reciprocal = apply_reciprocal_cell_edit(
                        self, con, actor=actor, source_document_id=document_id,
                        source_row_id=target_id, source_property=property_row,
                        previous_value=None, new_value=clean_value, now=created_at,
                        target_database_access=target_database_access,
                    )
                    if reciprocal is not None:
                        reciprocal_documents.setdefault(reciprocal[0], set()).add(reciprocal[1])
            if template_body:
                if not database_row_content.installed(con):
                    raise AuthorityUnavailable("database item content is not installed")
                blocks = _duplicate_row_blocks(
                    template_body, actor=actor, document=document_id,
                    database=database_id, operation=target_id, source_row=template_id,
                    template_references=True)
                con.execute(
                    "INSERT INTO workspace_collaboration_database_row_bodies("
                    "row_id,blocks_json) VALUES(?,?)",
                    (target_id, _canonical_json(blocks)),
                )
            return {"target_version": 1, "position": position,
                    **({"template_id": template_id, "applied_defaults": applied_defaults,
                        "body_block_count": len(template_body)} if template_id else {}),
                    **({"_reciprocal_documents": reciprocal_documents} if reciprocal_documents else {})}

        row = con.execute(
            "SELECT * FROM workspace_collaboration_database_rows WHERE database_id=? AND id=?",
            (database_id, target_id),
        ).fetchone()
        if row is None:
            raise NotFoundOrDenied("not found or permission denied")
        database_row_trash.require_active(con, database_id, target_id)
        self._check_target_version(row, expected_target_version)
        if command_kind == "database.row.template.apply":
            if set(payload) != {"template_id"}:
                raise ValueError("template application requires a template_id")
            template_id = _identifier(payload["template_id"], field="template_id")
            template = con.execute(
                "SELECT body_json,default_values_json FROM "
                "workspace_collaboration_database_templates "
                "WHERE database_id=? AND id=? AND state='active'",
                (database_id, template_id),
            ).fetchone()
            if template is None:
                raise NotFoundOrDenied("not found or permission denied")
            template_body = database_row_content.clean_blocks(
                json.loads(str(template["body_json"])))
            defaults = json.loads(str(template["default_values_json"]))
            if not isinstance(defaults, dict):
                raise ValueError("template default values must be an object")
            body_supported = database_row_content.installed(con)
            if template_body and not body_supported:
                raise AuthorityUnavailable("database item content is not installed")
            existing_body = (con.execute(
                "SELECT blocks_json FROM workspace_collaboration_database_row_bodies "
                "WHERE row_id=?", (target_id,),
            ).fetchone() if body_supported else None)
            if existing_body is not None and json.loads(str(existing_body["blocks_json"])):
                raise ValueError("template can only be applied to an item with an empty body")
            applied_defaults: dict[str, Any] = {}
            reciprocal_documents: dict[str, set[str]] = {}
            for property_id, value in defaults.items():
                property_id = _identifier(property_id, field="property_id")
                property_row = con.execute(
                    "SELECT property.*,property.database_id AS bound_database_id "
                    "FROM workspace_collaboration_database_properties property "
                    "WHERE database_id=? AND id=?", (database_id, property_id),
                ).fetchone()
                if property_row is None:
                    raise NotFoundOrDenied("not found or permission denied")
                clean_value = (_clean_template_file_defaults(con, document_id, value)
                    if str(property_row["property_type"]) == "files"
                    else self._clean_database_cell_value(
                        con, actor_subject_id=actor, property_row=property_row, value=value))
                existing_cell = con.execute(
                    "SELECT value_json,row_version FROM workspace_collaboration_database_cells "
                    "WHERE row_id=? AND property_id=?", (target_id, property_id),
                ).fetchone()
                if existing_cell is not None and json.loads(str(existing_cell["value_json"])) not in (None, "", []):
                    continue
                if existing_cell is None:
                    con.execute(
                        "INSERT INTO workspace_collaboration_database_cells("
                        "row_id,property_id,value_json,row_version,updated_at) VALUES(?,?,?,1,?)",
                        (target_id, property_id, _canonical_json(clean_value), created_at),
                    )
                else:
                    con.execute(
                        "UPDATE workspace_collaboration_database_cells SET value_json=?,"
                        "row_version=row_version+1,updated_at=? WHERE row_id=? AND property_id=?",
                        (_canonical_json(clean_value), created_at, target_id, property_id),
                    )
                applied_defaults[property_id] = clean_value
                if str(property_row["property_type"]) == "relation":
                    from .database_relation_pair import apply_reciprocal_cell_edit
                    reciprocal = apply_reciprocal_cell_edit(
                        self, con, actor=actor, source_document_id=document_id,
                        source_row_id=target_id, source_property=property_row,
                        previous_value=(json.loads(str(existing_cell["value_json"]))
                                        if existing_cell is not None else None),
                        new_value=clean_value, now=created_at,
                    )
                    if reciprocal is not None:
                        reciprocal_documents.setdefault(reciprocal[0], set()).add(reciprocal[1])
            if template_body:
                blocks = _duplicate_row_blocks(
                    template_body, actor=actor, document=document_id,
                    database=database_id, operation=f"{target_id}:{template_id}:{int(row['row_version'])}",
                    source_row=template_id, template_references=True)
                con.execute(
                    "INSERT INTO workspace_collaboration_database_row_bodies("
                    "row_id,blocks_json) VALUES(?,?) ON CONFLICT(row_id) DO UPDATE "
                    "SET blocks_json=excluded.blocks_json",
                    (target_id, _canonical_json(blocks)),
                )
            next_version = int(row["row_version"]) + 1
            con.execute(
                "UPDATE workspace_collaboration_database_rows SET row_version=?,updated_at=? "
                "WHERE id=?", (next_version, created_at, target_id),
            )
            return {"target_version": next_version, "template_id": template_id,
                    "applied_defaults": applied_defaults,
                    "body_block_count": len(template_body),
                    **({"_reciprocal_documents": reciprocal_documents} if reciprocal_documents else {})}
        if command_kind == "database.row.content.set":
            return database_row_content.set_body(con, target_id, payload,
                expected_version=int(row["row_version"]), now=created_at)
        if command_kind == "database.row.appearance.set":
            return database_row_appearance.set_appearance(con,target_id,payload,document_id=document_id,
                expected_version=int(row["row_version"]),now=created_at,image=appearance_file)
        deleted_version = int(row["row_version"])
        from .database_row_comments import retire_row_comments
        retire_row_comments(con, document_id, database_id, target_id, now=created_at)
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
        actor: str,
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
                if config.get("reciprocal_property_id"):
                    raise ValueError("paired relation requires an atomic pair command")
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
            previous_config = json.loads(str(row["config_json"]))
            if (config.get("reciprocal_property_id")
                    != previous_config.get("reciprocal_property_id")):
                raise ValueError("paired relation requires an atomic pair command")
            if (previous_config.get("reciprocal_property_id")
                    and (property_type != "relation"
                         or config.get("target_database_id")
                         != previous_config.get("target_database_id"))):
                raise ValueError("paired relation target cannot change")
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
                    _canonical_json(_clean_template_default_values(
                        con, document_id, database_id, defaults)),
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
            database_template_default.clear_archived(
                con, database_id, target_id, actor=actor, now=created_at,
            )
            con.execute(
                f"UPDATE {table} SET state='archived',archived_at=?,row_version=?,updated_at=? WHERE id=?",
                (created_at, next_version, created_at, target_id),
            )
            return next_version
        body = payload.get("body", json.loads(str(row["body_json"])))
        if isinstance(body, (str, bytes, bytearray)) or not isinstance(body, Sequence):
            raise ValueError("template body array required")
        defaults = _clean_template_default_values(
            con, document_id, database_id,
            payload.get("default_values", json.loads(str(row["default_values_json"]))),
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
            database_trash.require_active(con, database)
            role, _sources = CollaborativeIAMAuthority._resolution_on_connection(
                con, subject, str(row["workspace_id"]), str(row["document_id"])
            )
            if role is None:
                raise NotFoundOrDenied("not found or permission denied")
            document = self._document(con, str(row["document_id"]))
            descriptor = self._editing_snapshot(con, document)
            raw = next(item for item in descriptor["databases"] if item["id"] == database)
            raw["effective_role"] = role
            raw["capabilities"] = {"atomic_cell_batch": True, "max_cell_batch": 1000}
            raw["capabilities"]["row_content"] = database_row_content.installed(con)
            raw["capabilities"]["item_page_layout"] = database_item_page_layout.installed(con)
            raw["capabilities"]["row_comments"] = database_row_content.installed(con)
            raw["capabilities"]["row_appearance"] = database_row_appearance.installed(con)
            raw["capabilities"]["row_icon_batch"] = database_row_appearance.installed(con) and database_row_trash.installed(con)
            raw["capabilities"]["row_duplicate_batch"] = database_row_trash.installed(con)
            raw["capabilities"]["row_move_batch"] = database_row_trash.installed(con)
            raw["rows"] = self._materialize_rows(con, subject, raw)
            if database_activity.installed(con):
                activity = con.execute(
                    "SELECT last_editor_subject_id,last_edited_at,document_sequence "
                    "FROM workspace_collaboration_database_activity WHERE database_id=?",
                    (database,),
                ).fetchone()
                raw["last_edit"] = _database_last_edit(activity)
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
            activities: dict[str, dict[str, Any]] = {}
            activity_installed = database_activity.installed(con)
            if activity_installed:
                activities = {
                    str(activity["database_id"]): _database_last_edit(activity)
                    for activity in con.execute(
                        "SELECT database_id,last_editor_subject_id,last_edited_at,document_sequence "
                        "FROM workspace_collaboration_database_activity WHERE document_id=?",
                        (document,),
                    ).fetchall()
                }
            result: list[dict[str, Any]] = []
            for item in descriptor["databases"]:
                value = dict(item)
                value["effective_role"] = role
                value["capabilities"] = {"atomic_cell_batch": True, "max_cell_batch": 1000}
                value["capabilities"]["row_content"] = database_row_content.installed(con)
                value["capabilities"]["item_page_layout"] = database_item_page_layout.installed(con)
                value["capabilities"]["row_comments"] = database_row_content.installed(con)
                value["capabilities"]["row_appearance"] = database_row_appearance.installed(con)
                value["capabilities"]["row_icon_batch"] = database_row_appearance.installed(con) and database_row_trash.installed(con)
                value["capabilities"]["row_duplicate_batch"] = database_row_trash.installed(con)
                value["capabilities"]["row_move_batch"] = database_row_trash.installed(con)
                value["rows"] = self._materialize_rows(con, subject, value)
                if activity_installed:
                    value["last_edit"] = activities.get(str(value["id"]))
                result.append(value)
            return result

    @staticmethod
    def _materialize_rows(
        con: sqlite3.Connection, subject_id: str, database: Mapping[str, Any],
        *, target_access: Callable[[str], bool] | None = None,
    ) -> list[dict[str, Any]]:
        properties = {str(item["id"]): item for item in database["properties"]}
        names = {str(item["name"]): str(item["id"]) for item in database["properties"]}
        raw_rows = {str(item["id"]): dict(item) for item in database["rows"]}
        formula_ids = {key: _formula_property_ids(item["config"].get("copy_property_ids", {}))
            for key, item in properties.items() if item["type"] == "formula"}
        relation_access: dict[str, bool] = {}
        relation_targets: dict[str, frozenset[str]] = {}
        active_targets_by_database: dict[str, frozenset[str]] = {}
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
                and not database_trash.is_trashed(con, target_database_id)
                and (
                    target_access(target_database_id) if target_access is not None
                    else CollaborativeIAMAuthority._resolution_on_connection(
                        con,
                        subject_id,
                        str(target["workspace_id"]),
                        str(target["document_id"]),
                    )[0] is not None
                )
            )
            if relation_access[property_id]:
                if target_database_id not in active_targets_by_database:
                    target_ids = {str(item[0]) for item in con.execute(
                        "SELECT id FROM workspace_collaboration_database_rows "
                        "WHERE database_id=?", (target_database_id,),
                    ).fetchall()}
                    hidden_ids = (
                        database_row_trash.archived_row_ids(con, target_database_id)
                        | database_row_page_placement.page_row_ids(
                            con, target_database_id)
                    )
                    active_targets_by_database[target_database_id] = frozenset(
                        target_ids - hidden_ids)
                relation_targets[property_id] = active_targets_by_database[
                    target_database_id]

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
                elif property_id in cells:
                    cells[property_id] = [row_id for row_id in cells[property_id]
                        if row_id in relation_targets[property_id]]

            def resolve(key: str) -> Any:
                property_id = key if key in properties else names.get(key)
                return None if property_id is None else cells.get(property_id)

            computed: dict[str, Any] = {}
            for property_id, property_item in properties.items():
                if property_item["type"] == "formula":
                    computed[property_id] = _evaluate_formula(
                        str(property_item["config"]["expression"]),
                        lambda key: resolve(formula_ids[property_id].get(key, key))
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
