"""Shared fail-closed successor ownership check for the F0-P1 packet."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from code_assist_v1.tools.adopted_surface_successor import (
    owns_current_artifact as adopted_surface_owns_current_artifact,
)
from code_assist_v1.tools.search_authority_successor import (
    owns_current_artifact as search_owns_current_artifact,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = PROJECT_ROOT.parent
CONTRACT_PATH = (
    PROJECT_ROOT
    / "docs"
    / "notion_parity"
    / "core_free_web_ui"
    / "internal_collaborative_database_features.v1.json"
)
FILE_AUTHORITY_CONTRACT_PATH = (
    PROJECT_ROOT
    / "docs"
    / "notion_parity"
    / "core_free_web_ui"
    / "internal_collaborative_file_authority.v1.json"
)
HISTORY_AUTHORITY_CONTRACT_PATH = (
    PROJECT_ROOT
    / "docs"
    / "notion_parity"
    / "core_free_web_ui"
    / "internal_collaborative_history_authority.v1.json"
)


def _sha256_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _contract_owns_current_artifact(
    contract_path: Path,
    relative: str,
    target: Path,
    *,
    packet_id: str,
    gate_claim: str,
) -> bool:
    if not contract_path.is_file() or not target.is_file():
        return False
    try:
        value = json.loads(contract_path.read_text(encoding="utf-8"))
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
        value.get("packet_id") == packet_id
        and value.get("status") == "implemented_and_validated"
        and value.get("claims", {}).get(gate_claim) is True
        and isinstance(item, dict)
        and item.get("sha256") == _sha256_file(target)
    )


def owns_current_artifact(relative: str, target: Path) -> bool:
    return adopted_surface_owns_current_artifact(
        relative, target
    ) or search_owns_current_artifact(relative, target) or _contract_owns_current_artifact(
        HISTORY_AUTHORITY_CONTRACT_PATH,
        relative,
        target,
        packet_id="F0-P3",
        gate_claim="F0_P3_gate_passed",
    ) or _contract_owns_current_artifact(
        FILE_AUTHORITY_CONTRACT_PATH,
        relative,
        target,
        packet_id="F0-P2",
        gate_claim="F0_P2_gate_passed",
    ) or _contract_owns_current_artifact(
        CONTRACT_PATH,
        relative,
        target,
        packet_id="F0-P1",
        gate_claim="F0_P1_gate_passed",
    )


__all__ = ["owns_current_artifact"]
