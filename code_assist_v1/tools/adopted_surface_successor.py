"""Fail-closed successor ownership for the F0-P5 adopted surface packet."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = (
    PROJECT_ROOT
    / "docs"
    / "notion_parity"
    / "core_free_web_ui"
    / "internal_collaborative_adopted_surface.v1.json"
)


def _sha256_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def owns_current_artifact(relative: str, target: Path) -> bool:
    """Return true only when the sealed F0-P5 manifest owns this exact file hash."""

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
    item = next(
        (
            entry
            for entry in manifest
            if isinstance(entry, dict) and entry.get("path") == relative
        ),
        None,
    )
    return bool(
        value.get("packet_id") == "F0-P5"
        and value.get("status") == "implemented_and_validated"
        and value.get("claims", {}).get("F0_P5_gate_passed") is True
        and isinstance(item, dict)
        and item.get("sha256") == _sha256_file(target)
    )


__all__ = ["owns_current_artifact"]
