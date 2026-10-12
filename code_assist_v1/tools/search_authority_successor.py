"""Fail-closed F0-P4 successor ownership for shared Notion artifacts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from code_assist_v1.tools.adopted_surface_successor import (
    owns_current_artifact as adopted_surface_owns_current_artifact,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = (
    PROJECT_ROOT
    / "docs"
    / "notion_parity"
    / "core_free_web_ui"
    / "internal_collaborative_search_authority.v1.json"
)


def owns_current_artifact(relative: str, target: Path) -> bool:
    if adopted_surface_owns_current_artifact(relative, target):
        return True
    if not CONTRACT_PATH.is_file() or not target.is_file():
        return False
    try:
        value = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        return False
    if not isinstance(value, dict):
        return False
    manifest = [
        *value.get("source_manifest", []),
        *value.get("test_manifest", []),
    ]
    binding = next(
        (
            item
            for item in manifest
            if isinstance(item, dict) and item.get("path") == relative
        ),
        None,
    )
    return bool(
        value.get("packet_id") == "F0-P4"
        and value.get("status") == "implemented_and_validated"
        and value.get("claims", {}).get("F0_P4_gate_passed") is True
        and isinstance(binding, dict)
        and binding.get("sha256")
        == "sha256:" + hashlib.sha256(target.read_bytes()).hexdigest()
    )


__all__ = ["owns_current_artifact"]
