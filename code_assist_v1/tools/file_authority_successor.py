"""Fail-closed successor ownership check for the F0-P2 file packet."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from code_assist_v1.tools.search_authority_successor import (
    owns_current_artifact as search_owns_current_artifact,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
HISTORY_AUTHORITY_CONTRACT_PATH = (
    PROJECT_ROOT
    / "docs"
    / "notion_parity"
    / "core_free_web_ui"
    / "internal_collaborative_history_authority.v1.json"
)


def owns_current_artifact(relative: str, target: Path) -> bool:
    if search_owns_current_artifact(relative, target):
        return True
    if not HISTORY_AUTHORITY_CONTRACT_PATH.is_file() or not target.is_file():
        return False
    try:
        value = json.loads(HISTORY_AUTHORITY_CONTRACT_PATH.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        return False
    manifest = [*value.get("source_manifest", []), *value.get("test_manifest", [])]
    item = next(
        (
            entry
            for entry in manifest
            if isinstance(entry, dict) and entry.get("path") == relative
        ),
        None,
    )
    return bool(
        value.get("packet_id") == "F0-P3"
        and value.get("status") == "implemented_and_validated"
        and value.get("claims", {}).get("F0_P3_gate_passed") is True
        and isinstance(item, dict)
        and item.get("sha256")
        == "sha256:" + hashlib.sha256(target.read_bytes()).hexdigest()
    )


__all__ = ["owns_current_artifact"]
