"""Offline-only legacy-v3 structural page mutation adapter.

This module is intentionally absent from application startup, routes, workers,
and CLIs.  It accepts one caller-owned exact SQLite connection, requires an
explicit quiescence assertion for every new effect, and stores an immutable
response-loss receipt in the same source transaction as create/move/reorder.
It performs no path discovery and never opens or migrates a database.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
import sqlite3
from typing import Any, Callable
import uuid

from code_assist_v1.notion_legacy_change_consumer_contract import (
    CONSUMER_NOW_UDF,
    CONSUMER_WRITE_CONTEXT_UDF,
    MAX_TIMESTAMP,
    inspect_legacy_change_consumer_contract,
)
from code_assist_v1.notion_legacy_page_lifecycle import (
    LegacyPageRenameContractError,
    LegacyPageRenameManualAuditRequired,
    LegacyPageRenameOperationConflict,
    LegacyPageRenameSourceAdapter,
    LegacyPageRenameSourceConflict,
)


STRUCTURAL_CONSUMER = "np2c.lifecycle"
STRUCTURAL_RESULT_CONTRACT = "np2c_legacy_page_structural_receipt_v1"
STRUCTURAL_REQUEST_DOMAIN = (
    "code_assist.notion.legacy_page_lifecycle.structural.request.v1"
)
STRUCTURAL_RECEIPT_DOMAIN = (
    "code_assist.notion.legacy_page_lifecycle.structural.receipt.v1"
)
STRUCTURAL_MANIFEST_DOMAIN = (
    "code_assist.notion.legacy_page_lifecycle.structural.manifest.v1"
)
STRUCTURAL_PRECONDITION_DOMAIN = (
    "code_assist.notion.legacy_page_lifecycle.structural.precondition.v1"
)
MAX_STRUCTURAL_EFFECTS = 4_096
MAX_STRUCTURAL_SUBTREE = 1_000
MAX_STRUCTURAL_DEPTH = 256

EFFECT_TARGET = 1
EFFECT_OLD_SCOPE = 2
EFFECT_NEW_SCOPE = 4
EFFECT_SUBTREE = 8
EFFECT_OLD_PARENT = 16
EFFECT_NEW_PARENT = 32

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{1,63}$")
_OPERATION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}$")
_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError, UnicodeError) as exc:
        raise LegacyPageRenameContractError(
            "legacy structural evidence is invalid"
        ) from exc


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(
        _canonical_json(value).encode("utf-8")
    ).hexdigest()


def _name(value: object, *, field: str) -> str:
    if not isinstance(value, str) or _NAME_RE.fullmatch(value) is None:
        raise ValueError(f"valid {field} required")
    return value


def _operation_id(value: object, *, field: str) -> str:
    if not isinstance(value, str) or _OPERATION_RE.fullmatch(value) is None:
        raise ValueError(f"valid {field} required")
    return value


def _page_id(value: object, *, allow_empty: bool = False) -> str:
    if allow_empty and value in {None, ""}:
        return ""
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > 160
    ):
        raise ValueError("valid source page identity required")
    return value


def _sha256(value: object, *, field: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"valid {field} required")
    return value


def _title(value: object) -> str:
    try:
        normalized = " ".join(str(value or "Untitled").strip().split())[:160]
        result = normalized or "Untitled"
        result.encode("utf-8")
        return result
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ValueError("valid structural create title required") from exc


@dataclass(frozen=True)
class LegacyPageStructuralEffect:
    source_page_id: str
    effect_roles: int
    old_parent_source_page_id: str
    new_parent_source_page_id: str
    old_sibling_ordinal: int | None
    new_sibling_ordinal: int
    old_depth: int | None
    new_depth: int
    source_mutated: bool

    def descriptor(self) -> dict[str, Any]:
        return {
            "source_page_id": self.source_page_id,
            "effect_roles": self.effect_roles,
            "old_parent_source_page_id": self.old_parent_source_page_id,
            "new_parent_source_page_id": self.new_parent_source_page_id,
            "old_sibling_ordinal": self.old_sibling_ordinal,
            "new_sibling_ordinal": self.new_sibling_ordinal,
            "old_depth": self.old_depth,
            "new_depth": self.new_depth,
            "source_mutated": self.source_mutated,
        }


@dataclass(frozen=True)
class LegacyPageStructuralReceipt:
    action: str
    source_owner_subject_id: str
    target_source_page_id: str
    source_request_sha256: str
    source_operation_receipt_sha256: str
    post_manifest_sha256: str
    source_device: int
    source_inode: int
    source_link_count: int
    source_epoch: int
    full_sweep_generation: int
    subtree_count: int
    old_scope_count: int
    new_scope_count: int
    effects: tuple[LegacyPageStructuralEffect, ...]

    def descriptor(self, *, include_receipt: bool = True) -> dict[str, Any]:
        result = {
            "contract": STRUCTURAL_RESULT_CONTRACT,
            "action": self.action,
            "source_owner_subject_id": self.source_owner_subject_id,
            "target_source_page_id": self.target_source_page_id,
            "source_request_sha256": self.source_request_sha256,
            "post_manifest_sha256": self.post_manifest_sha256,
            "source_device": self.source_device,
            "source_inode": self.source_inode,
            "source_link_count": self.source_link_count,
            "source_epoch": self.source_epoch,
            "full_sweep_generation": self.full_sweep_generation,
            "subtree_count": self.subtree_count,
            "old_scope_count": self.old_scope_count,
            "new_scope_count": self.new_scope_count,
            "effects": [effect.descriptor() for effect in self.effects],
        }
        if include_receipt:
            result["source_operation_receipt_sha256"] = (
                self.source_operation_receipt_sha256
            )
        return result


def derive_structural_source_operation_id(
    *, action: object, catalog_command_id: object, caller_operation_id: object
) -> str:
    exact_action = str(action or "")
    if exact_action not in {"create", "move", "reorder"}:
        raise ValueError("valid structural action required")
    command = _operation_id(catalog_command_id, field="catalog_command_id")
    caller = _operation_id(caller_operation_id, field="caller_operation_id")
    return "np2c-structural:" + hashlib.sha256(
        _canonical_json(
            {
                "domain": "code_assist.notion.legacy_page_lifecycle.structural.operation.v1",
                "action": exact_action,
                "catalog_command_id": command,
                "caller_operation_id": caller,
            }
        ).encode("utf-8")
    ).hexdigest()


def structural_request_sha256(
    *,
    action: object,
    mutation_actor_subject_id: object,
    source_owner_subject_id: object,
    target_source_page_id: object,
    new_parent_source_page_id: object,
    relative_source_page_id: object,
    relative_position: object,
    requested_title: object,
    catalog_command_id: object,
    caller_operation_id: object,
    expected_precondition_sha256: object,
) -> str:
    exact_action = str(action or "")
    if exact_action not in {"create", "move", "reorder"}:
        raise ValueError("valid structural action required")
    actor = _name(mutation_actor_subject_id, field="mutation_actor_subject_id")
    owner = _name(source_owner_subject_id, field="source_owner_subject_id")
    target = _page_id(target_source_page_id)
    parent = _page_id(new_parent_source_page_id, allow_empty=True)
    relative = _page_id(relative_source_page_id, allow_empty=True)
    position = str(relative_position or "")
    if bool(relative) != bool(position) or position not in {"", "before", "after"}:
        raise ValueError("valid structural relative target required")
    title = _title(requested_title) if exact_action == "create" else ""
    command = _operation_id(catalog_command_id, field="catalog_command_id")
    caller = _operation_id(caller_operation_id, field="caller_operation_id")
    expected = _sha256(
        expected_precondition_sha256, field="expected_precondition_sha256"
    )
    return _digest(
        {
            "domain": STRUCTURAL_REQUEST_DOMAIN,
            "consumer": STRUCTURAL_CONSUMER,
            "action": exact_action,
            "source_operation_id": derive_structural_source_operation_id(
                action=exact_action,
                catalog_command_id=command,
                caller_operation_id=caller,
            ),
            "catalog_command_id": command,
            "caller_operation_id": caller,
            "mutation_actor_subject_id": actor,
            "source_owner_subject_id": owner,
            "target_source_page_id": target,
            "new_parent_source_page_id": parent,
            "relative_source_page_id": relative,
            "relative_position": position,
            "requested_title": title,
            "expected_precondition_sha256": expected,
        }
    )


def _effect_from_descriptor(raw: Any) -> LegacyPageStructuralEffect:
    keys = {
        "source_page_id", "effect_roles", "old_parent_source_page_id",
        "new_parent_source_page_id", "old_sibling_ordinal",
        "new_sibling_ordinal", "old_depth", "new_depth", "source_mutated",
    }
    if not isinstance(raw, dict) or set(raw) != keys:
        raise LegacyPageRenameContractError("legacy structural receipt is invalid")
    roles = raw["effect_roles"]
    old_ordinal = raw["old_sibling_ordinal"]
    old_depth = raw["old_depth"]
    if (
        isinstance(roles, bool)
        or not isinstance(roles, int)
        or not 1 <= roles <= 63
        or type(raw["source_mutated"]) is not bool
        or (old_ordinal is None) != (old_depth is None)
    ):
        raise LegacyPageRenameContractError("legacy structural receipt is invalid")
    for value, minimum, maximum in (
        (raw["new_sibling_ordinal"], 1, MAX_STRUCTURAL_EFFECTS),
        (raw["new_depth"], 0, MAX_STRUCTURAL_DEPTH),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
            raise LegacyPageRenameContractError("legacy structural receipt is invalid")
    if old_ordinal is not None and (
        isinstance(old_ordinal, bool)
        or not isinstance(old_ordinal, int)
        or not 1 <= old_ordinal <= MAX_STRUCTURAL_EFFECTS
        or isinstance(old_depth, bool)
        or not isinstance(old_depth, int)
        or not 0 <= old_depth <= MAX_STRUCTURAL_DEPTH
    ):
        raise LegacyPageRenameContractError("legacy structural receipt is invalid")
    return LegacyPageStructuralEffect(
        source_page_id=_page_id(raw["source_page_id"]),
        effect_roles=roles,
        old_parent_source_page_id=_page_id(
            raw["old_parent_source_page_id"], allow_empty=True
        ),
        new_parent_source_page_id=_page_id(
            raw["new_parent_source_page_id"], allow_empty=True
        ),
        old_sibling_ordinal=old_ordinal,
        new_sibling_ordinal=raw["new_sibling_ordinal"],
        old_depth=old_depth,
        new_depth=raw["new_depth"],
        source_mutated=raw["source_mutated"],
    )


def validate_structural_receipt(
    value: Any,
    *,
    expected_action: str,
    expected_request_sha256: str,
) -> LegacyPageStructuralReceipt:
    raw = value.descriptor() if isinstance(value, LegacyPageStructuralReceipt) else value
    keys = {
        "contract", "action", "source_owner_subject_id", "target_source_page_id",
        "source_request_sha256", "source_operation_receipt_sha256",
        "post_manifest_sha256", "source_device", "source_inode",
        "source_link_count", "source_epoch", "full_sweep_generation",
        "subtree_count", "old_scope_count", "new_scope_count", "effects",
    }
    if not isinstance(raw, dict) or set(raw) != keys:
        raise LegacyPageRenameContractError("legacy structural receipt is invalid")
    if raw["contract"] != STRUCTURAL_RESULT_CONTRACT or raw["action"] != expected_action:
        raise LegacyPageRenameContractError("legacy structural receipt is invalid")
    effects_raw = raw["effects"]
    if not isinstance(effects_raw, list) or not 1 <= len(effects_raw) <= MAX_STRUCTURAL_EFFECTS:
        raise LegacyPageRenameContractError("legacy structural receipt is invalid")
    effects = tuple(_effect_from_descriptor(item) for item in effects_raw)
    if tuple(effect.source_page_id for effect in effects) != tuple(
        sorted(effect.source_page_id for effect in effects)
    ) or len({effect.source_page_id for effect in effects}) != len(effects):
        raise LegacyPageRenameContractError("legacy structural receipt is invalid")
    integers = {}
    for field, minimum, maximum in (
        ("source_link_count", 1, 65_535),
        ("source_epoch", 1, 9_007_199_254_740_991),
        ("full_sweep_generation", 1, 9_007_199_254_740_991),
        ("subtree_count", 1, MAX_STRUCTURAL_SUBTREE),
        ("old_scope_count", 0, MAX_STRUCTURAL_EFFECTS),
        ("new_scope_count", 1, MAX_STRUCTURAL_EFFECTS),
    ):
        item = raw[field]
        if isinstance(item, bool) or not isinstance(item, int) or not minimum <= item <= maximum:
            raise LegacyPageRenameContractError("legacy structural receipt is invalid")
        integers[field] = item
    for field in ("source_device", "source_inode"):
        item = raw[field]
        if isinstance(item, bool) or not isinstance(item, int) or not -(2**63) <= item < 2**63:
            raise LegacyPageRenameContractError("legacy structural receipt is invalid")
        integers[field] = item
    receipt = LegacyPageStructuralReceipt(
        action=expected_action,
        source_owner_subject_id=_name(
            raw["source_owner_subject_id"], field="source_owner_subject_id"
        ),
        target_source_page_id=_page_id(raw["target_source_page_id"]),
        source_request_sha256=_sha256(
            raw["source_request_sha256"], field="source_request_sha256"
        ),
        source_operation_receipt_sha256=_sha256(
            raw["source_operation_receipt_sha256"],
            field="source_operation_receipt_sha256",
        ),
        post_manifest_sha256=_sha256(
            raw["post_manifest_sha256"], field="post_manifest_sha256"
        ),
        source_device=integers["source_device"],
        source_inode=integers["source_inode"],
        source_link_count=integers["source_link_count"],
        source_epoch=integers["source_epoch"],
        full_sweep_generation=integers["full_sweep_generation"],
        subtree_count=integers["subtree_count"],
        old_scope_count=integers["old_scope_count"],
        new_scope_count=integers["new_scope_count"],
        effects=effects,
    )
    if receipt.source_request_sha256 != _sha256(
        expected_request_sha256, field="expected_request_sha256"
    ):
        raise LegacyPageRenameContractError("legacy structural receipt is invalid")
    body = receipt.descriptor(include_receipt=False)
    expected_receipt = _digest({"domain": STRUCTURAL_RECEIPT_DOMAIN, "result": body})
    if receipt.source_operation_receipt_sha256 != expected_receipt:
        raise LegacyPageRenameContractError("legacy structural receipt is invalid")
    expected_manifest = _digest(
        {
            "domain": STRUCTURAL_MANIFEST_DOMAIN,
            "action": receipt.action,
            "source_owner_subject_id": receipt.source_owner_subject_id,
            "target_source_page_id": receipt.target_source_page_id,
            "source_device": receipt.source_device,
            "source_inode": receipt.source_inode,
            "source_link_count": receipt.source_link_count,
            "source_epoch": receipt.source_epoch,
            "full_sweep_generation": receipt.full_sweep_generation,
            "effects": [effect.descriptor() for effect in receipt.effects],
        }
    )
    if receipt.post_manifest_sha256 != expected_manifest:
        raise LegacyPageRenameContractError("legacy structural receipt is invalid")
    return receipt


class LegacyPageStructuralSourceAdapter(LegacyPageRenameSourceAdapter):
    """Exact owner-shard structural mutation over one authenticated connection."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._incarnation_write_context = 0
        self._con.create_function(
            "notion_incarnation_write_context",
            0,
            lambda: self._incarnation_write_context,
            deterministic=False,
        )
        self._con.create_function(
            "notion_new_incarnation_id",
            0,
            lambda: "inc_" + uuid.uuid4().hex,
            deterministic=False,
        )

    def _active_page(self, page_id: str) -> sqlite3.Row | None:
        return self._con.execute(
            "SELECT * FROM pages WHERE id=? AND owner_user_id=? AND archived=0",
            (page_id, self._owner),
        ).fetchone()

    @staticmethod
    def _settings_locked(row: sqlite3.Row | None) -> bool:
        if row is None:
            return False
        try:
            value = json.loads(str(row["settings_json"] or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise LegacyPageRenameContractError(
                "legacy structural page settings are invalid"
            ) from exc
        if not isinstance(value, dict):
            raise LegacyPageRenameContractError(
                "legacy structural page settings are invalid"
            )
        return bool(value.get("locked"))

    def _scope(self, parent_page_id: str) -> tuple[str, ...]:
        if parent_page_id:
            rows = self._con.execute(
                "SELECT id FROM pages WHERE owner_user_id=? AND archived=0 "
                "AND parent_id=? ORDER BY position,updated_at DESC,id LIMIT ?",
                (self._owner, parent_page_id, MAX_STRUCTURAL_EFFECTS + 1),
            ).fetchall()
        else:
            rows = self._con.execute(
                "SELECT id FROM pages WHERE owner_user_id=? AND archived=0 "
                "AND parent_id IS NULL ORDER BY position,updated_at DESC,id LIMIT ?",
                (self._owner, MAX_STRUCTURAL_EFFECTS + 1),
            ).fetchall()
        if len(rows) > MAX_STRUCTURAL_EFFECTS:
            raise LegacyPageRenameContractError(
                "legacy structural sibling scope exceeds the adapter bound"
            )
        return tuple(str(row[0]) for row in rows)

    def _subtree(self, page_id: str) -> tuple[str, ...]:
        rows = self._con.execute(
            "WITH RECURSIVE tree(id,depth) AS ("
            "SELECT id,0 FROM pages WHERE id=? AND owner_user_id=? AND archived=0 "
            "UNION ALL SELECT p.id,tree.depth+1 FROM pages p JOIN tree "
            "ON p.parent_id=tree.id WHERE p.owner_user_id=? AND p.archived=0 "
            "AND tree.depth<?) SELECT id,depth FROM tree ORDER BY depth,id LIMIT ?",
            (
                page_id,
                self._owner,
                self._owner,
                MAX_STRUCTURAL_DEPTH,
                MAX_STRUCTURAL_SUBTREE + 1,
            ),
        ).fetchall()
        if not rows or len(rows) > MAX_STRUCTURAL_SUBTREE:
            raise LegacyPageRenameContractError(
                "legacy structural subtree exceeds the adapter bound"
            )
        if any(int(row[1]) >= MAX_STRUCTURAL_DEPTH for row in rows):
            raise LegacyPageRenameContractError(
                "legacy structural depth exceeds the adapter bound"
            )
        return tuple(str(row[0]) for row in rows)

    def _structure(self, page_id: str) -> tuple[str, int, int]:
        row = self._active_page(page_id)
        if row is None:
            raise LegacyPageRenameSourceConflict(
                "legacy structural source page changed"
            )
        parent = str(row["parent_id"] or "")
        scope = self._scope(parent)
        try:
            ordinal = scope.index(page_id) + 1
        except ValueError as exc:
            raise LegacyPageRenameSourceConflict(
                "legacy structural sibling scope changed"
            ) from exc
        depth = 0
        current = parent
        visited = {page_id}
        while current:
            if current in visited or depth >= MAX_STRUCTURAL_DEPTH:
                raise LegacyPageRenameContractError(
                    "legacy structural ancestry exceeds the adapter bound"
                )
            visited.add(current)
            parent_row = self._active_page(current)
            if parent_row is None:
                raise LegacyPageRenameSourceConflict(
                    "legacy structural ancestry changed"
                )
            current = str(parent_row["parent_id"] or "")
            depth += 1
        return parent, ordinal, depth

    def _generation(self, page_id: str) -> tuple[int, int, int, str, int]:
        page = self._active_page(page_id)
        watermark = self._con.execute(
            "SELECT change_generation,change_mask FROM notion_page_change_watermarks "
            "WHERE page_id=?", (page_id,)
        ).fetchone()
        incarnation = self._con.execute(
            "SELECT incarnation_id,incarnation_generation,retired_at "
            "FROM notion_page_incarnation_history WHERE page_id=? "
            "ORDER BY incarnation_generation DESC LIMIT 1", (page_id,)
        ).fetchone()
        if page is None or watermark is None or incarnation is None or incarnation["retired_at"] is not None:
            raise LegacyPageRenameSourceConflict(
                "legacy structural source generation changed"
            )
        return (
            int(page["revision"]),
            int(page["updated_at"]),
            int(watermark["change_generation"]),
            str(incarnation["incarnation_id"]),
            int(incarnation["incarnation_generation"]),
        )

    def _resolve_destination(
        self,
        *,
        action: str,
        target_page_id: str,
        requested_parent_page_id: str,
        relative_page_id: str,
    ) -> tuple[str, str]:
        target = self._active_page(target_page_id)
        old_parent = str(target["parent_id"] or "") if target is not None else ""
        destination = requested_parent_page_id
        if relative_page_id:
            relative = self._active_page(relative_page_id)
            if relative is None:
                raise LegacyPageRenameSourceConflict(
                    "legacy structural relative page changed"
                )
            resolved = str(relative["parent_id"] or "")
            if destination != resolved:
                raise LegacyPageRenameSourceConflict(
                    "legacy structural destination changed"
                )
            destination = resolved
        if action == "create":
            prior_incarnation = self._con.execute(
                "SELECT 1 FROM notion_page_incarnation_history WHERE page_id=? LIMIT 1",
                (target_page_id,),
            ).fetchone()
            if target is not None or prior_incarnation is not None:
                raise LegacyPageRenameSourceConflict(
                    "legacy structural create target already exists"
                )
        elif target is None:
            raise LegacyPageRenameSourceConflict(
                "legacy structural target changed"
            )
        elif action == "move" and destination == old_parent:
            raise LegacyPageRenameSourceConflict(
                "legacy structural move must change parent"
            )
        elif action == "reorder" and destination != old_parent:
            raise LegacyPageRenameSourceConflict(
                "legacy structural reorder must preserve parent"
            )
        if destination:
            parent = self._active_page(destination)
            if parent is None or self._settings_locked(parent):
                raise PermissionError("editable destination parent required")
        return old_parent, destination

    def _precondition_payload(
        self,
        *,
        action: str,
        target_page_id: str,
        new_parent_page_id: str,
        relative_page_id: str,
    ) -> dict[str, Any]:
        old_parent, destination = self._resolve_destination(
            action=action,
            target_page_id=target_page_id,
            requested_parent_page_id=new_parent_page_id,
            relative_page_id=relative_page_id,
        )
        old_scope = self._scope(old_parent) if action != "create" else ()
        destination_scope = self._scope(destination)
        subtree = self._subtree(target_page_id) if action != "create" else ()
        pages = set(old_scope) | set(destination_scope) | set(subtree)
        pages.update(page for page in (old_parent, destination) if page)
        rows: list[dict[str, Any]] = []
        for page_id in sorted(pages):
            parent, ordinal, depth = self._structure(page_id)
            generation = self._generation(page_id)
            rows.append(
                {
                    "source_page_id": page_id,
                    "parent_source_page_id": parent,
                    "sibling_ordinal": ordinal,
                    "depth": depth,
                    "revision": generation[0],
                    "updated_at": generation[1],
                    "change_generation": generation[2],
                    "incarnation_id": generation[3],
                    "incarnation_generation": generation[4],
                }
            )
        state = self._con.execute(
            "SELECT source_epoch,full_sweep_generation "
            "FROM notion_legacy_change_state WHERE singleton=1"
        ).fetchone()
        if state is None:
            raise LegacyPageRenameContractError(
                "legacy structural tracking state is unavailable"
            )
        return {
            "domain": STRUCTURAL_PRECONDITION_DOMAIN,
            "action": action,
            "source_owner_subject_id": self._owner,
            "target_source_page_id": target_page_id,
            "old_parent_source_page_id": old_parent,
            "new_parent_source_page_id": destination,
            "old_scope": list(old_scope),
            "destination_scope": list(destination_scope),
            "subtree": list(subtree),
            "source_epoch": int(state["source_epoch"]),
            "full_sweep_generation": int(state["full_sweep_generation"]),
            "pages": rows,
        }

    def capture_precondition(
        self,
        *,
        action: str,
        target_source_page_id: str,
        new_parent_source_page_id: str = "",
        relative_source_page_id: str = "",
        source_quiesced_confirmed: bool,
    ) -> str:
        if source_quiesced_confirmed is not True:
            raise ValueError("legacy structural mutation requires source quiescence")
        if self._con.in_transaction:
            raise ValueError("idle SQLite connection required")
        exact_action = str(action or "")
        if exact_action not in {"create", "move", "reorder"}:
            raise ValueError("valid structural action required")
        target = _page_id(target_source_page_id)
        parent = _page_id(new_parent_source_page_id, allow_empty=True)
        relative = _page_id(relative_source_page_id, allow_empty=True)
        inspect_legacy_change_consumer_contract(self._con, verify_integrity=False)
        self._con.execute("BEGIN")
        try:
            payload = self._precondition_payload(
                action=exact_action,
                target_page_id=target,
                new_parent_page_id=parent,
                relative_page_id=relative,
            )
            self._con.rollback()
            return _digest(payload)
        except Exception:
            self._con.rollback()
            raise

    def _replay_structural(
        self,
        *,
        source_operation_id: str,
        action: str,
        source_request_sha256: str,
    ) -> LegacyPageStructuralReceipt | None:
        row = self._con.execute(
            "SELECT action,request_sha256,result_json "
            "FROM notion_legacy_change_consumer_operations "
            "WHERE consumer=? AND operation_id=?",
            (STRUCTURAL_CONSUMER, source_operation_id),
        ).fetchone()
        if row is None:
            return None
        expected_action = f"legacy.page_lifecycle.{action}"
        if str(row["action"]) != expected_action or str(row["request_sha256"]) != source_request_sha256:
            raise LegacyPageRenameOperationConflict(source_operation_id)
        try:
            parsed = json.loads(str(row["result_json"]))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise LegacyPageRenameContractError(
                "legacy structural source ledger is invalid"
            ) from exc
        if str(row["result_json"]) != _canonical_json(parsed):
            raise LegacyPageRenameContractError(
                "legacy structural source ledger is invalid"
            )
        return validate_structural_receipt(
            parsed,
            expected_action=action,
            expected_request_sha256=source_request_sha256,
        )

    def _assert_no_child_reference(self, parent_page_id: str, target_page_id: str) -> None:
        if not parent_page_id:
            return
        row = self._con.execute(
            "SELECT 1 FROM blocks WHERE page_id=? AND block_type='child_page' "
            "AND json_valid(props_json) AND json_extract(props_json,'$.page_id')=? "
            "LIMIT 1", (parent_page_id, target_page_id)
        ).fetchone()
        if row is not None:
            raise LegacyPageRenameContractError(
                "materialized child-page references are not supported by this adapter"
            )

    def _mutate_source(
        self,
        *,
        action: str,
        target_page_id: str,
        destination: str,
        relative_page_id: str,
        relative_position: str,
        requested_title: str,
        now: int,
    ) -> None:
        if action == "create":
            position = len(self._scope(destination)) + 1
            self._incarnation_write_context = 1
            try:
                self._con.execute(
                    "INSERT INTO pages(id,owner_user_id,parent_id,title,icon,cover,"
                    "settings_json,position,archived,archived_at,revision,created_at,"
                    "updated_at) VALUES(?,?,?,?,'','',?, ?,0,NULL,1,?,?)",
                    (
                        target_page_id,
                        self._owner,
                        destination or None,
                        requested_title,
                        "{}",
                        position,
                        now,
                        now,
                    ),
                )
            finally:
                self._incarnation_write_context = 0
            block_id = "b_" + uuid.uuid4().hex
            self._con.execute(
                "INSERT INTO blocks(id,page_id,owner_user_id,position,block_type,"
                "content,props_json,created_at,updated_at) "
                "VALUES(?,?,?,1,'paragraph','','{}',?,?)",
                (block_id, target_page_id, self._owner, now, now),
            )
            self._con.execute(
                "INSERT INTO page_versions(id,page_id,owner_user_id,title,icon,cover,"
                "settings_json,blocks_json,created_at) VALUES(?,?,?,?,'','',?,'[]',?)",
                (
                    "v_" + uuid.uuid4().hex,
                    target_page_id,
                    self._owner,
                    requested_title,
                    "{}",
                    now,
                ),
            )
            return

        target = self._active_page(target_page_id)
        if target is None or self._settings_locked(target):
            raise PermissionError("owner source page is unavailable")
        subtree = set(self._subtree(target_page_id))
        if destination in subtree:
            raise LegacyPageRenameSourceConflict(
                "legacy structural target cannot move under its subtree"
            )
        old_parent = str(target["parent_id"] or "")
        self._assert_no_child_reference(old_parent, target_page_id)
        self._assert_no_child_reference(destination, target_page_id)
        siblings = [
            page for page in self._scope(destination) if page != target_page_id
        ]
        insert_at = len(siblings)
        if relative_page_id:
            if relative_page_id not in siblings:
                raise LegacyPageRenameSourceConflict(
                    "legacy structural relative page changed"
                )
            insert_at = siblings.index(relative_page_id)
            if relative_position == "after":
                insert_at += 1
        siblings.insert(insert_at, target_page_id)
        self._con.execute(
            "UPDATE pages SET parent_id=?,updated_at=? WHERE id=? AND revision=?",
            (destination or None, now, target_page_id, int(target["revision"])),
        )
        for ordinal, page_id in enumerate(siblings, start=1):
            self._con.execute(
                "UPDATE pages SET position=? WHERE id=? AND archived=0",
                (ordinal, page_id),
            )
        for page_id in sorted(set(siblings)):
            self._con.execute(
                "UPDATE pages SET revision=revision+1 WHERE id=? AND archived=0",
                (page_id,),
            )

    def apply_structural(
        self,
        *,
        action: str,
        mutation_actor_subject_id: str,
        target_source_page_id: str,
        new_parent_source_page_id: str,
        relative_source_page_id: str,
        relative_position: str,
        requested_title: object,
        catalog_command_id: str,
        caller_operation_id: str,
        expected_precondition_sha256: str,
        source_quiesced_confirmed: bool,
        fault_hook: Callable[[str], None] | None = None,
    ) -> LegacyPageStructuralReceipt:
        exact_action = str(action or "")
        if exact_action not in {"create", "move", "reorder"}:
            raise ValueError("valid structural action required")
        actor = _name(mutation_actor_subject_id, field="mutation_actor_subject_id")
        if actor != self._owner:
            raise PermissionError("owner permission required")
        target = _page_id(target_source_page_id)
        parent = _page_id(new_parent_source_page_id, allow_empty=True)
        relative = _page_id(relative_source_page_id, allow_empty=True)
        position = str(relative_position or "")
        if bool(relative) != bool(position) or position not in {"", "before", "after"}:
            raise ValueError("valid structural relative target required")
        if exact_action == "create" and relative:
            raise ValueError("structural create does not accept a relative target")
        title = _title(requested_title) if exact_action == "create" else ""
        expected_precondition = _sha256(
            expected_precondition_sha256, field="expected_precondition_sha256"
        )
        source_operation_id = derive_structural_source_operation_id(
            action=exact_action,
            catalog_command_id=catalog_command_id,
            caller_operation_id=caller_operation_id,
        )
        request_sha256 = structural_request_sha256(
            action=exact_action,
            mutation_actor_subject_id=actor,
            source_owner_subject_id=self._owner,
            target_source_page_id=target,
            new_parent_source_page_id=parent,
            relative_source_page_id=relative,
            relative_position=position,
            requested_title=title,
            catalog_command_id=catalog_command_id,
            caller_operation_id=caller_operation_id,
            expected_precondition_sha256=expected_precondition,
        )
        if self._con.in_transaction:
            raise ValueError("idle SQLite connection required")

        replay = self._replay_structural(
            source_operation_id=source_operation_id,
            action=exact_action,
            source_request_sha256=request_sha256,
        )
        if replay is not None:
            return replay
        if source_quiesced_confirmed is not True:
            raise ValueError("legacy structural mutation requires source quiescence")
        inspect_legacy_change_consumer_contract(self._con, verify_integrity=False)
        now = self._now()
        self._now_value = now
        self._con.execute("BEGIN IMMEDIATE")
        try:
            replay = self._replay_structural(
                source_operation_id=source_operation_id,
                action=exact_action,
                source_request_sha256=request_sha256,
            )
            if replay is not None:
                self._con.rollback()
                return replay
            before_payload = self._precondition_payload(
                action=exact_action,
                target_page_id=target,
                new_parent_page_id=parent,
                relative_page_id=relative,
            )
            if _digest(before_payload) != expected_precondition:
                raise LegacyPageRenameSourceConflict(
                    "legacy structural source precondition changed"
                )
            old_parent = str(before_payload["old_parent_source_page_id"] or "")
            destination = str(before_payload["new_parent_source_page_id"] or "")
            old_scope = tuple(before_payload["old_scope"])
            subtree_before = tuple(before_payload["subtree"])
            before_generations = {
                str(row["source_page_id"]): (
                    int(row["revision"]),
                    int(row["updated_at"]),
                    int(row["change_generation"]),
                )
                for row in before_payload["pages"]
            }
            if fault_hook is not None:
                fault_hook("before_source_mutation")
            self._mutate_source(
                action=exact_action,
                target_page_id=target,
                destination=destination,
                relative_page_id=relative,
                relative_position=position,
                requested_title=title,
                now=now,
            )
            if fault_hook is not None:
                fault_hook("after_source_mutation")

            new_scope = self._scope(destination)
            subtree = self._subtree(target)
            old_scope_after = self._scope(old_parent)
            pages = set(old_scope) | set(new_scope) | set(subtree)
            pages.update(page for page in (old_parent, destination) if page)
            if len(pages) > MAX_STRUCTURAL_EFFECTS:
                raise LegacyPageRenameContractError(
                    "legacy structural effect set exceeds the adapter bound"
                )
            # A single structural command can touch the same page through
            # several domain statements (for example page INSERT followed by
            # its initial block INSERT, or position plus revision updates).
            # The watermark is a coalescing queue, and no intermediate value
            # is externally visible inside this transaction.  Canonicalize
            # every affected page to one logical generation so the immutable
            # catalog effect records the command boundary rather than the
            # adapter's internal statement count.
            for page_id in sorted(pages):
                watermark = self._con.execute(
                    "SELECT change_generation FROM notion_page_change_watermarks "
                    "WHERE page_id=?",
                    (page_id,),
                ).fetchone()
                if watermark is None:
                    raise LegacyPageRenameContractError(
                        "legacy structural watermark is unavailable"
                    )
                before_generation = before_generations.get(page_id)
                old_generation = (
                    int(before_generation[2])
                    if before_generation is not None
                    else 0
                )
                observed_generation = int(watermark["change_generation"])
                if observed_generation > old_generation:
                    changed = self._con.execute(
                        "UPDATE notion_page_change_watermarks "
                        "SET change_generation=? "
                        "WHERE page_id=? AND change_generation=?",
                        (old_generation + 1, page_id, observed_generation),
                    )
                    if changed.rowcount != 1:
                        raise LegacyPageRenameSourceConflict(
                            "legacy structural watermark changed"
                        )
            old_scope_ordinals = {
                page_id: index for index, page_id in enumerate(old_scope, start=1)
            }
            new_scope_ordinals = {
                page_id: index for index, page_id in enumerate(new_scope, start=1)
            }
            before_rows = {
                str(row["source_page_id"]): row for row in before_payload["pages"]
            }
            effects: list[LegacyPageStructuralEffect] = []
            for page_id in sorted(pages):
                new_parent_value, new_ordinal_value, new_depth_value = self._structure(
                    page_id
                )
                before_row = before_rows.get(page_id)
                old_parent_value = (
                    str(before_row["parent_source_page_id"])
                    if before_row is not None else ""
                )
                old_ordinal_value = (
                    int(before_row["sibling_ordinal"])
                    if before_row is not None else None
                )
                old_depth_value = (
                    int(before_row["depth"]) if before_row is not None else None
                )
                roles = 0
                if page_id == target:
                    roles |= EFFECT_TARGET
                if page_id in old_scope:
                    roles |= EFFECT_OLD_SCOPE
                    old_ordinal_value = old_scope_ordinals[page_id]
                if page_id in new_scope:
                    roles |= EFFECT_NEW_SCOPE
                    new_ordinal_value = new_scope_ordinals[page_id]
                if page_id in subtree:
                    roles |= EFFECT_SUBTREE
                if old_parent and page_id == old_parent:
                    roles |= EFFECT_OLD_PARENT
                if destination and page_id == destination:
                    roles |= EFFECT_NEW_PARENT
                generation = self._generation(page_id)
                previous_generation = before_generations.get(page_id)
                source_mutated = previous_generation is None or (
                    generation[0], generation[1], generation[2]
                ) != previous_generation
                effects.append(
                    LegacyPageStructuralEffect(
                        source_page_id=page_id,
                        effect_roles=roles,
                        old_parent_source_page_id=old_parent_value,
                        new_parent_source_page_id=new_parent_value,
                        old_sibling_ordinal=old_ordinal_value,
                        new_sibling_ordinal=new_ordinal_value,
                        old_depth=old_depth_value,
                        new_depth=new_depth_value,
                        source_mutated=source_mutated,
                    )
                )
            if not any(effect.source_mutated for effect in effects):
                raise LegacyPageRenameSourceConflict(
                    "legacy structural source mutation was a no-op"
                )
            state = self._con.execute(
                "SELECT source_epoch,full_sweep_generation "
                "FROM notion_legacy_change_state WHERE singleton=1"
            ).fetchone()
            if state is None:
                raise LegacyPageRenameContractError(
                    "legacy structural tracking state is unavailable"
                )
            device, inode, link_count = self._assert_identity()
            manifest_body = {
                "domain": STRUCTURAL_MANIFEST_DOMAIN,
                "action": exact_action,
                "source_owner_subject_id": self._owner,
                "target_source_page_id": target,
                "source_device": device,
                "source_inode": inode,
                "source_link_count": link_count,
                "source_epoch": int(state["source_epoch"]),
                "full_sweep_generation": int(state["full_sweep_generation"]),
                "effects": [effect.descriptor() for effect in effects],
            }
            provisional = LegacyPageStructuralReceipt(
                action=exact_action,
                source_owner_subject_id=self._owner,
                target_source_page_id=target,
                source_request_sha256=request_sha256,
                source_operation_receipt_sha256="sha256:" + "0" * 64,
                post_manifest_sha256=_digest(manifest_body),
                source_device=device,
                source_inode=inode,
                source_link_count=link_count,
                source_epoch=int(state["source_epoch"]),
                full_sweep_generation=int(state["full_sweep_generation"]),
                subtree_count=len(subtree),
                old_scope_count=len(old_scope),
                new_scope_count=len(new_scope),
                effects=tuple(effects),
            )
            receipt = LegacyPageStructuralReceipt(
                **{
                    **provisional.__dict__,
                    "source_operation_receipt_sha256": _digest(
                        {
                            "domain": STRUCTURAL_RECEIPT_DOMAIN,
                            "result": provisional.descriptor(
                                include_receipt=False
                            ),
                        }
                    ),
                }
            )
            receipt = validate_structural_receipt(
                receipt,
                expected_action=exact_action,
                expected_request_sha256=request_sha256,
            )
            if fault_hook is not None:
                fault_hook("before_operation_insert")
            self._write_context = 1
            try:
                self._con.execute(
                    "INSERT INTO notion_legacy_change_consumer_operations("
                    "consumer,operation_id,action,request_sha256,result_json,created_at"
                    ") VALUES(?,?,?,?,?,?)",
                    (
                        STRUCTURAL_CONSUMER,
                        source_operation_id,
                        f"legacy.page_lifecycle.{exact_action}",
                        request_sha256,
                        _canonical_json(receipt.descriptor()),
                        now,
                    ),
                )
            finally:
                self._write_context = 0
            if fault_hook is not None:
                fault_hook("after_operation_insert")
                fault_hook("before_commit")
            try:
                self._con.commit()
            except BaseException as exc:
                if self._con.in_transaction:
                    self._con.rollback()
                    raise
                recovered = self._replay_structural(
                    source_operation_id=source_operation_id,
                    action=exact_action,
                    source_request_sha256=request_sha256,
                )
                if recovered is not None:
                    return recovered
                raise LegacyPageRenameManualAuditRequired(
                    "legacy structural commit outcome is indeterminate"
                ) from exc
            self._assert_identity()
            if fault_hook is not None:
                fault_hook("after_commit")
            return receipt
        except BaseException:
            self._write_context = 0
            self._incarnation_write_context = 0
            if self._con.in_transaction:
                self._con.rollback()
            raise


__all__ = [
    "EFFECT_NEW_PARENT",
    "EFFECT_NEW_SCOPE",
    "EFFECT_OLD_PARENT",
    "EFFECT_OLD_SCOPE",
    "EFFECT_SUBTREE",
    "EFFECT_TARGET",
    "LegacyPageStructuralEffect",
    "LegacyPageStructuralReceipt",
    "LegacyPageStructuralSourceAdapter",
    "STRUCTURAL_RESULT_CONTRACT",
    "derive_structural_source_operation_id",
    "structural_request_sha256",
    "validate_structural_receipt",
]
