"""Canonical, database-wide item-page layout distinct from view configuration."""
from __future__ import annotations

import json
import sqlite3
from typing import Any, Mapping

from code_assist_v1 import notion_workspace_database_item_page_layout_migration as migration
from code_assist_v1 import notion_workspace_database_template_default_migration as template_default_migration
from code_assist_v1 import notion_workspace_database_sharing_migration as database_sharing_migration
from code_assist_v1 import notion_workspace_database_upload_scope_migration as upload_scope_migration
from code_assist_v1 import notion_workspace_direct_database_favorite_migration as direct_favorite_migration
from .canonical_authority import AuthorityUnavailable, _canonical_json, _identifier
from .editing_authority import TargetVersionConflict


BOOLEAN_OPTIONS = {
    "show_page": True,
    "property_icons": True,
    "full_width": False,
    "show_templates": True,
}
OPTION_DEFAULTS = {**BOOLEAN_OPTIONS, "inline_comments": "default"}
MODULES = frozenset({"properties", "comments", "content"})


def installed(con: sqlite3.Connection) -> bool:
    marker = con.execute(
        "SELECT version,name,checksum,actual_schema_sha256 FROM workspace_schema_migrations "
        "ORDER BY version DESC LIMIT 1"
    ).fetchone()
    return marker is not None and tuple(marker) in {
        (item.TARGET_VERSION, item.TARGET_NAME,
         item.TARGET_CHECKSUM, item.TARGET_ACTUAL_SCHEMA_SHA256)
        for item in (migration, template_default_migration, database_sharing_migration, upload_scope_migration, direct_favorite_migration)
    }


def clean_layout(value: Any, *, property_ids: set[str]) -> dict[str, Any]:
    required_fields = {
        "structure", "modules", "page_properties", "panel_properties",
        "hidden_properties", "options",
    }
    optional_fields = {"empty_hidden_properties", "property_group_placement", "sections",
                       "standalone_properties"}
    if (not isinstance(value, Mapping) or not required_fields <= set(value)
            or set(value) - required_fields - optional_fields):
        raise ValueError("database item-page layout fields are invalid")
    structure = value["structure"]
    if structure not in {"compact", "tabs"}:
        raise ValueError("database item-page layout structure is invalid")
    modules = value["modules"]
    if (not isinstance(modules, list) or len(modules) != 3
            or not all(isinstance(item, str) for item in modules)
            or set(modules) != MODULES):
        raise ValueError("database item-page modules are invalid")
    options = value["options"]
    if not isinstance(options, Mapping) or set(options) != set(OPTION_DEFAULTS):
        raise ValueError("database item-page options are invalid")
    if any(type(options[key]) is not bool for key in BOOLEAN_OPTIONS):
        raise ValueError("database item-page options require booleans")
    if options["inline_comments"] not in {"default", "minimized"}:
        raise ValueError("database item-page inline comments option is invalid")

    groups: dict[str, list[str]] = {}
    placed: set[str] = set()
    for key in ("page_properties", "panel_properties", "hidden_properties"):
        raw = value[key]
        if not isinstance(raw, list) or len(raw) > 500:
            raise ValueError("database item-page property placement is invalid")
        clean = [_identifier(item, field="property_id") for item in raw]
        if any(item not in property_ids or item in placed for item in clean):
            raise ValueError("database item-page property placement is invalid")
        placed.update(clean)
        groups[key] = clean
    empty_hidden = value.get("empty_hidden_properties")
    if "empty_hidden_properties" in value:
        if not isinstance(empty_hidden, list) or len(empty_hidden) > 500:
            raise ValueError("database item-page empty visibility is invalid")
        empty_hidden = [_identifier(item, field="property_id") for item in empty_hidden]
        if (len(set(empty_hidden)) != len(empty_hidden)
                or any(item not in property_ids or item in groups["hidden_properties"]
                       for item in empty_hidden)):
            raise ValueError("database item-page empty visibility is invalid")
    placement = value.get("property_group_placement")
    if "property_group_placement" in value and placement not in {"page", "panel"}:
        raise ValueError("database item-page property group placement is invalid")
    standalone = value.get("standalone_properties")
    if "standalone_properties" in value:
        if not isinstance(standalone, list) or len(standalone) > 500:
            raise ValueError("database item-page standalone properties are invalid")
        standalone = [_identifier(item, field="property_id") for item in standalone]
        visible = set(groups["page_properties"] + groups["panel_properties"])
        if len(set(standalone)) != len(standalone) or any(item not in visible for item in standalone):
            raise ValueError("database item-page standalone properties are invalid")
    sections = value.get("sections")
    clean_sections: list[dict[str, Any]] = []
    if "sections" in value:
        if not isinstance(sections, list) or len(sections) > 100:
            raise ValueError("database item-page sections are invalid")
        section_ids: set[str] = set()
        section_properties: set[str] = set()
        for section in sections:
            if not isinstance(section, Mapping) or set(section) != {"id", "name", "property_ids"}:
                raise ValueError("database item-page sections are invalid")
            section_id = _identifier(section["id"], field="section_id")
            name = section["name"]
            raw_properties = section["property_ids"]
            if (section_id in section_ids or not isinstance(name, str)
                    or not name.strip() or len(name) > 120 or "\n" in name or "\r" in name
                    or not isinstance(raw_properties, list) or len(raw_properties) > 500):
                raise ValueError("database item-page sections are invalid")
            section_ids.add(section_id)
            section_property_ids = [_identifier(item, field="property_id")
                                    for item in raw_properties]
            if (len(set(section_property_ids)) != len(section_property_ids)
                    or any(item not in property_ids or item in section_properties
                           for item in section_property_ids)):
                raise ValueError("database item-page sections are invalid")
            section_properties.update(section_property_ids)
            clean_sections.append({"id": section_id, "name": name.strip(),
                                   "property_ids": section_property_ids})
    # Newly added properties are not in older saved layouts. They appear in
    # the page group by default until an editor explicitly places them.
    result = {
        "structure": structure,
        "modules": list(modules),
        **groups,
        "options": {key: options[key] for key in OPTION_DEFAULTS},
    }
    if "empty_hidden_properties" in value:
        result["empty_hidden_properties"] = empty_hidden
    if "property_group_placement" in value:
        result["property_group_placement"] = placement
    if "standalone_properties" in value:
        result["standalone_properties"] = standalone
    if "sections" in value:
        result["sections"] = clean_sections
    if len(_canonical_json(result).encode("utf-8")) > 65536:
        raise ValueError("database item-page layout is too large")
    return result


def read(con: sqlite3.Connection, database_id: str) -> dict[str, Any] | None:
    if not installed(con):
        return None
    row = con.execute(
        "SELECT layout_json,row_version FROM workspace_collaboration_database_item_page_layouts "
        "WHERE database_id=?", (database_id,),
    ).fetchone()
    if row is None:
        return None
    return {"value": json.loads(str(row["layout_json"])), "version": int(row["row_version"])}


def set_layout(con: sqlite3.Connection, database_id: str, payload: Mapping[str, Any],
               *, expected_version: int, actor: str, now: int) -> dict[str, Any]:
    if not installed(con):
        raise AuthorityUnavailable("database item-page layout is not installed")
    if set(payload) != {"layout"}:
        raise ValueError("database item-page layout payload is invalid")
    property_ids = {
        str(row["id"]) for row in con.execute(
            "SELECT id FROM workspace_collaboration_database_properties "
            "WHERE database_id=? AND property_type<>'title'", (database_id,),
        ).fetchall()
    }
    layout = clean_layout(payload["layout"], property_ids=property_ids)
    current = read(con, database_id)
    version = current["version"] if current is not None else 0
    if expected_version != version:
        raise TargetVersionConflict(version)
    next_version = version + 1
    con.execute(
        "INSERT INTO workspace_collaboration_database_item_page_layouts("
        "database_id,layout_json,row_version,updated_by_subject_id,created_at,updated_at) "
        "VALUES(?,?,?,?,?,?) ON CONFLICT(database_id) DO UPDATE SET "
        "layout_json=excluded.layout_json,row_version=excluded.row_version,"
        "updated_by_subject_id=excluded.updated_by_subject_id,updated_at=excluded.updated_at",
        (database_id, _canonical_json(layout), next_version, actor, now, now),
    )
    return {"target_version": next_version, "item_page_layout": {"value": layout, "version": next_version}}


def forget_property(con: sqlite3.Connection, database_id: str, property_id: str,
                    *, actor: str, now: int) -> None:
    current = read(con, database_id)
    if current is None:
        return
    layout = current["value"]
    changed = False
    for key in ("page_properties", "panel_properties", "hidden_properties",
                "empty_hidden_properties", "standalone_properties"):
        if property_id in layout.get(key, []):
            layout[key] = [item for item in layout[key] if item != property_id]
            changed = True
    for section in layout.get("sections", []):
        if property_id in section["property_ids"]:
            section["property_ids"] = [item for item in section["property_ids"]
                                       if item != property_id]
            changed = True
    if not changed:
        return
    con.execute(
        "UPDATE workspace_collaboration_database_item_page_layouts SET "
        "layout_json=?,row_version=row_version+1,updated_by_subject_id=?,updated_at=? "
        "WHERE database_id=? AND row_version=?",
        (_canonical_json(layout), actor, now, database_id, current["version"]),
    )
