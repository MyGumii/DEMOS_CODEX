"""Side-effect-free NP-2C create/move/reorder source contracts.

The contracts deliberately reuse the frozen legacy-v3 immutable operation
ledger without changing its DDL.  Raw page content is accepted only while
building a request digest; persisted receipts contain identifiers, bounded
counts and SHA-256 commitments, never title/block/settings bytes.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Iterable

from code_assist_v1.notion_legacy_change_contract import CHANGE_ALL, MAX_GENERATION
from code_assist_v1.notion_legacy_changes import (
    legacy_incarnation_receipt_sha256,
    sqlite_source_identity,
)
from code_assist_v1.notion_legacy_page_lifecycle_contract import (
    SOURCE_CONSUMER,
    SOURCE_SCHEMA_VERSION,
    normalize_legacy_page_title,
)


CREATE_ACTION = "legacy.page_lifecycle.create"
MOVE_ACTION = "legacy.page_lifecycle.move"
REORDER_ACTION = "legacy.page_lifecycle.reorder"
STRUCTURAL_ACTIONS = frozenset({CREATE_ACTION, MOVE_ACTION, REORDER_ACTION})
RESULT_CONTRACTS = {
    CREATE_ACTION: "np2c_legacy_page_create_receipt_v1",
    MOVE_ACTION: "np2c_legacy_page_move_receipt_v1",
    REORDER_ACTION: "np2c_legacy_page_reorder_receipt_v1",
}
REQUEST_DOMAIN = "code_assist.notion.legacy_page_structure.request.v1"
RECEIPT_DOMAIN = "code_assist.notion.legacy_page_structure.receipt.v1"
CREATE_ID_DOMAIN = "code_assist.notion.legacy_page_structure.create_id.v1"
OPERATION_DOMAIN = "code_assist.notion.legacy_page_structure.operation.v1"

MAX_TIMESTAMP = 9_007_199_254_740_991
MAX_SUBTREE_NODES = 1_000
MAX_TREE_DEPTH = 256
MAX_SIBLING_NODES = 10_000
MAX_EFFECT_PAGE_RECEIPTS = 21_002
MAX_AGGREGATE_BYTES = 256 * 1024 * 1024
MAX_RESULT_JSON_BYTES = 16 * 1024 * 1024
MAX_PARENT_BLOCKS = 5_000
MAX_PAGE_BLOCKS = 10_000
MAX_PAGE_FILES = 5_000
MAX_PAGE_BYTES = 16 * 1024 * 1024

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{1,63}$")
_OPERATION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}$")
_PAGE_ID_RE = re.compile(r"^p_[0-9a-f]{32}$")
_INCARNATION_RE = re.compile(r"^inc_[0-9a-f]{32}$")
_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_POSITION_VALUES = frozenset({"", "before", "after"})


class LegacyPageStructureContractError(Exception):
    """A structural request, prestate or persisted receipt is not exact."""


def canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError, UnicodeError) as exc:
        raise LegacyPageStructureContractError(
            "legacy page structure evidence is invalid"
        ) from exc


def sha256_payload(value: Any) -> str:
    try:
        encoded = canonical_json(value).encode("utf-8")
    except UnicodeError as exc:
        raise LegacyPageStructureContractError(
            "legacy page structure evidence is invalid"
        ) from exc
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _utf8_len(value: object) -> int:
    try:
        return len(str(value or "").encode("utf-8"))
    except UnicodeError as exc:
        raise LegacyPageStructureContractError(
            "legacy page structure text is invalid"
        ) from exc


def _name(value: object, *, field: str) -> str:
    if not isinstance(value, str) or _NAME_RE.fullmatch(value) is None:
        raise LegacyPageStructureContractError(f"valid {field} required")
    return value


def _operation_id(value: object, *, field: str) -> str:
    if not isinstance(value, str) or _OPERATION_RE.fullmatch(value) is None:
        raise LegacyPageStructureContractError(f"valid {field} required")
    return value


def _page_id(value: object, *, allow_empty: bool = False) -> str:
    if allow_empty and value == "":
        return ""
    if not isinstance(value, str) or not value or value != value.strip() or len(value) > 160:
        raise LegacyPageStructureContractError("valid page_id required")
    return value


def _integer(
    value: object,
    *,
    field: str,
    minimum: int = 0,
    maximum: int = MAX_GENERATION,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise LegacyPageStructureContractError(f"valid {field} required")
    return value


def _sha256(value: object, *, field: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise LegacyPageStructureContractError(f"valid {field} required")
    return value


def _action(value: object) -> str:
    if value not in STRUCTURAL_ACTIONS:
        raise LegacyPageStructureContractError("valid structural action required")
    return str(value)


def _canonical_props(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise LegacyPageStructureContractError("block props must be an object")
    # JSON round trip both validates the value and detaches caller-owned state.
    try:
        parsed = json.loads(canonical_json(value))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise LegacyPageStructureContractError("block props must be JSON") from exc
    if not isinstance(parsed, dict):
        raise LegacyPageStructureContractError("block props must be an object")
    return parsed


@dataclass(frozen=True)
class LegacyPageCreatePayload:
    title: str
    icon: str
    cover: str
    settings: dict[str, Any]
    blocks: tuple[dict[str, Any], ...]
    add_to_parent_blocks: bool

    def descriptor(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "icon": self.icon,
            "cover": self.cover,
            "settings": self.settings,
            "blocks": list(self.blocks),
            "add_to_parent_blocks": self.add_to_parent_blocks,
        }


def normalize_create_payload(
    *,
    title: object = "Untitled",
    icon: object = "",
    cover: object = "",
    settings: Any = None,
    blocks: Iterable[Any] | None = None,
    add_to_parent_blocks: object = False,
) -> LegacyPageCreatePayload:
    if type(add_to_parent_blocks) is not bool:
        raise LegacyPageStructureContractError("valid add_to_parent_blocks required")
    normalized_settings = {} if settings is None else _canonical_props(settings)
    raw_blocks = list(blocks or ())
    if not raw_blocks:
        raw_blocks = [{"type": "paragraph", "content": "", "props": {}}]
    if len(raw_blocks) > MAX_PAGE_BLOCKS:
        raise LegacyPageStructureContractError("create block bound exceeded")
    prepared: list[dict[str, Any]] = []
    byte_count = 0
    for raw in raw_blocks:
        if not isinstance(raw, dict) or set(raw) - {"type", "content", "props"}:
            raise LegacyPageStructureContractError("create block is invalid")
        block_type = str(raw.get("type") or "paragraph")[:64]
        content = str(raw.get("content") or "")
        props = _canonical_props(raw.get("props") or {})
        block = {"type": block_type, "content": content, "props": props}
        byte_count += _utf8_len(block_type) + _utf8_len(content) + _utf8_len(canonical_json(props))
        if byte_count > MAX_PAGE_BYTES:
            raise LegacyPageStructureContractError("create payload byte bound exceeded")
        prepared.append(block)
    normalized = LegacyPageCreatePayload(
        title=normalize_legacy_page_title(title),
        icon=str(icon or "")[:64],
        cover=str(cover or "")[:1000],
        settings=normalized_settings,
        blocks=tuple(prepared),
        add_to_parent_blocks=add_to_parent_blocks,
    )
    if _utf8_len(canonical_json(normalized.descriptor())) > MAX_PAGE_BYTES:
        raise LegacyPageStructureContractError("create payload byte bound exceeded")
    return normalized


@dataclass(frozen=True)
class LegacyPageStructurePrestate:
    action: str
    source_device: int
    source_inode: int
    source_link_count: int
    source_epoch: int
    full_sweep_generation: int
    source_owner_subject_id: str
    target_page_id: str
    target_exists: bool
    target_incarnation_id: str
    target_incarnation_generation: int
    target_revision: int
    target_updated_at: int
    target_change_generation: int
    target_change_mask: int
    target_incarnation_receipt_sha256: str
    old_parent_page_id: str
    new_parent_page_id: str
    relative_page_id: str
    relative_position: str
    target_dense_ordinal: int
    subtree_count: int
    old_scope_count: int
    new_scope_count: int
    pre_manifest_sha256: str

    def descriptor(self) -> dict[str, Any]:
        return dict(self.__dict__)


_PRESTATE_KEYS = frozenset(LegacyPageStructurePrestate.__dataclass_fields__)


def validate_structure_prestate(value: Any) -> LegacyPageStructurePrestate:
    raw = value.descriptor() if isinstance(value, LegacyPageStructurePrestate) else value
    if not isinstance(raw, dict) or set(raw) != _PRESTATE_KEYS:
        raise LegacyPageStructureContractError("legacy page structure prestate is invalid")
    action = _action(raw["action"])
    try:
        device = sqlite_source_identity(raw["source_device"])
        inode = sqlite_source_identity(raw["source_inode"])
    except (TypeError, ValueError) as exc:
        raise LegacyPageStructureContractError(
            "legacy page structure prestate is invalid"
        ) from exc
    target_exists = raw["target_exists"]
    if type(target_exists) is not bool:
        raise LegacyPageStructureContractError("legacy page structure prestate is invalid")
    relative_position = str(raw["relative_position"])
    if relative_position not in _POSITION_VALUES:
        raise LegacyPageStructureContractError("legacy page structure prestate is invalid")
    state = LegacyPageStructurePrestate(
        action=action,
        source_device=device,
        source_inode=inode,
        source_link_count=_integer(raw["source_link_count"], field="source_link_count", minimum=1, maximum=65_535),
        source_epoch=_integer(raw["source_epoch"], field="source_epoch", minimum=1),
        full_sweep_generation=_integer(raw["full_sweep_generation"], field="full_sweep_generation", minimum=1),
        source_owner_subject_id=_name(raw["source_owner_subject_id"], field="source_owner_subject_id"),
        target_page_id=_page_id(raw["target_page_id"]),
        target_exists=target_exists,
        target_incarnation_id=str(raw["target_incarnation_id"]),
        target_incarnation_generation=_integer(raw["target_incarnation_generation"], field="target_incarnation_generation"),
        target_revision=_integer(raw["target_revision"], field="target_revision"),
        target_updated_at=_integer(raw["target_updated_at"], field="target_updated_at", maximum=MAX_TIMESTAMP),
        target_change_generation=_integer(raw["target_change_generation"], field="target_change_generation"),
        target_change_mask=_integer(raw["target_change_mask"], field="target_change_mask", maximum=CHANGE_ALL),
        target_incarnation_receipt_sha256=str(raw["target_incarnation_receipt_sha256"]),
        old_parent_page_id=_page_id(raw["old_parent_page_id"], allow_empty=True),
        new_parent_page_id=_page_id(raw["new_parent_page_id"], allow_empty=True),
        relative_page_id=_page_id(raw["relative_page_id"], allow_empty=True),
        relative_position=relative_position,
        target_dense_ordinal=_integer(raw["target_dense_ordinal"], field="target_dense_ordinal", maximum=MAX_SIBLING_NODES),
        subtree_count=_integer(raw["subtree_count"], field="subtree_count", maximum=MAX_SUBTREE_NODES),
        old_scope_count=_integer(raw["old_scope_count"], field="old_scope_count", maximum=MAX_SIBLING_NODES),
        new_scope_count=_integer(raw["new_scope_count"], field="new_scope_count", maximum=MAX_SIBLING_NODES),
        pre_manifest_sha256=_sha256(raw["pre_manifest_sha256"], field="pre_manifest_sha256"),
    )
    if action == CREATE_ACTION:
        if (
            state.target_exists
            or _PAGE_ID_RE.fullmatch(state.target_page_id) is None
            or state.target_incarnation_id
            or any((state.target_incarnation_generation, state.target_revision, state.target_updated_at,
                    state.target_change_generation, state.target_change_mask, state.target_dense_ordinal,
                    state.subtree_count, state.old_scope_count))
            or state.target_incarnation_receipt_sha256
            or state.old_parent_page_id
            or state.relative_page_id
            or state.relative_position
        ):
            raise LegacyPageStructureContractError("legacy create prestate is invalid")
    else:
        if (
            not state.target_exists
            or _INCARNATION_RE.fullmatch(state.target_incarnation_id) is None
            or min(
                state.target_incarnation_generation,
                state.target_revision,
                state.target_change_generation,
                state.target_change_mask,
                state.target_dense_ordinal,
                state.subtree_count,
                state.old_scope_count,
            ) < 1
        ):
            raise LegacyPageStructureContractError("legacy move prestate is invalid")
        expected_receipt = legacy_incarnation_receipt_sha256(
            source_owner_id=state.source_owner_subject_id,
            page_id=state.target_page_id,
            incarnation_id=state.target_incarnation_id,
            incarnation_generation=state.target_incarnation_generation,
            incarnation_retired=False,
            change_generation=state.target_change_generation,
            change_mask=state.target_change_mask,
            source_exists=True,
            source_active=True,
            source_epoch=state.source_epoch,
            full_sweep_generation=state.full_sweep_generation,
            source_device=state.source_device,
            source_inode=state.source_inode,
            source_link_count=state.source_link_count,
        )
        if state.target_incarnation_receipt_sha256 != expected_receipt:
            raise LegacyPageStructureContractError("legacy move prestate is invalid")
        if action == MOVE_ACTION and state.old_parent_page_id == state.new_parent_page_id:
            raise LegacyPageStructureContractError("move must change parent")
        if action == REORDER_ACTION and state.old_parent_page_id != state.new_parent_page_id:
            raise LegacyPageStructureContractError("reorder must preserve parent")
        if bool(state.relative_page_id) != bool(state.relative_position):
            raise LegacyPageStructureContractError("relative page and position must match")
    return state


@dataclass(frozen=True)
class LegacyPageStructureReceipt:
    contract: str
    action: str
    source_request_sha256: str
    source_changed: bool
    structure_changed: bool
    source_device: int
    source_inode: int
    source_link_count: int
    source_epoch: int
    full_sweep_generation: int
    source_owner_subject_id: str
    target_page_id: str
    target_incarnation_id: str
    target_incarnation_generation: int
    target_revision: int
    target_updated_at: int
    target_change_generation: int
    target_change_mask: int
    target_incarnation_receipt_sha256: str
    old_parent_page_id: str
    new_parent_page_id: str
    target_dense_ordinal: int
    subtree_count: int
    old_scope_count: int
    new_scope_count: int
    affected_page_count: int
    post_manifest_sha256: str
    source_operation_receipt_sha256: str

    def descriptor(self) -> dict[str, Any]:
        return dict(self.__dict__)


_RECEIPT_KEYS = frozenset(LegacyPageStructureReceipt.__dataclass_fields__)


def derive_create_page_id(
    *, workspace_id: object, source_owner_subject_id: object,
    mutation_actor_subject_id: object, caller_operation_id: object,
) -> str:
    payload = {
        "domain": CREATE_ID_DOMAIN,
        "workspace_id": _name(workspace_id, field="workspace_id"),
        "source_owner_subject_id": _name(source_owner_subject_id, field="source_owner_subject_id"),
        "mutation_actor_subject_id": _name(mutation_actor_subject_id, field="mutation_actor_subject_id"),
        "caller_operation_id": _operation_id(caller_operation_id, field="caller_operation_id"),
    }
    return "p_" + hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()[:32]


def derive_structure_source_operation_id(
    *, action: object, catalog_command_id: object, caller_operation_id: object
) -> str:
    normalized_action = _action(action)
    command = _operation_id(catalog_command_id, field="catalog_command_id")
    caller = _operation_id(caller_operation_id, field="caller_operation_id")
    digest = hashlib.sha256(
        canonical_json(
            {
                "domain": OPERATION_DOMAIN,
                "action": normalized_action,
                "catalog_command_id": command,
                "caller_operation_id": caller,
            }
        ).encode("utf-8")
    ).hexdigest()
    return "np2c-" + normalized_action.rsplit(".", 1)[-1] + ":" + digest


def legacy_page_structure_request_sha256(
    *, mutation_actor_subject_id: object, catalog_command_id: object,
    caller_operation_id: object, expected_prestate: LegacyPageStructurePrestate,
    create_payload: LegacyPageCreatePayload | None = None,
) -> str:
    expected = validate_structure_prestate(expected_prestate)
    actor = _name(mutation_actor_subject_id, field="mutation_actor_subject_id")
    command = _operation_id(catalog_command_id, field="catalog_command_id")
    caller = _operation_id(caller_operation_id, field="caller_operation_id")
    if expected.action == CREATE_ACTION:
        if not isinstance(create_payload, LegacyPageCreatePayload):
            raise LegacyPageStructureContractError("create payload required")
        payload_sha256 = sha256_payload(create_payload.descriptor())
    elif create_payload is not None:
        raise LegacyPageStructureContractError("move request does not accept create payload")
    else:
        payload_sha256 = None
    return sha256_payload(
        {
            "domain": REQUEST_DOMAIN,
            "consumer": SOURCE_CONSUMER,
            "action": expected.action,
            "source_operation_id": derive_structure_source_operation_id(
                action=expected.action,
                catalog_command_id=command,
                caller_operation_id=caller,
            ),
            "catalog_command_id": command,
            "caller_operation_id": caller,
            "mutation_actor_subject_id": actor,
            "create_payload_sha256": payload_sha256,
            "expected_prestate": expected.descriptor(),
        }
    )


def build_structure_receipt(
    *, action: object, source_request_sha256: object, source_changed: object,
    structure_changed: object, poststate: dict[str, Any],
) -> LegacyPageStructureReceipt:
    normalized_action = _action(action)
    if type(source_changed) is not bool or type(structure_changed) is not bool:
        raise LegacyPageStructureContractError("legacy page structure receipt is invalid")
    if not source_changed:
        raise LegacyPageStructureContractError("structural compatibility effect must be receipted as changed")
    body = {
        "contract": RESULT_CONTRACTS[normalized_action],
        "action": normalized_action,
        "source_request_sha256": _sha256(source_request_sha256, field="source_request_sha256"),
        "source_changed": source_changed,
        "structure_changed": structure_changed,
        **poststate,
    }
    required = _RECEIPT_KEYS - {"source_operation_receipt_sha256"}
    if set(body) != required:
        raise LegacyPageStructureContractError("legacy page structure receipt is invalid")
    try:
        device = sqlite_source_identity(body["source_device"])
        inode = sqlite_source_identity(body["source_inode"])
    except (TypeError, ValueError) as exc:
        raise LegacyPageStructureContractError("legacy page structure receipt is invalid") from exc
    incarnation_id = str(body["target_incarnation_id"])
    if _INCARNATION_RE.fullmatch(incarnation_id) is None:
        raise LegacyPageStructureContractError("legacy page structure receipt is invalid")
    normalized = {
        **body,
        "source_device": device,
        "source_inode": inode,
        "source_link_count": _integer(body["source_link_count"], field="source_link_count", minimum=1, maximum=65_535),
        "source_epoch": _integer(body["source_epoch"], field="source_epoch", minimum=1),
        "full_sweep_generation": _integer(body["full_sweep_generation"], field="full_sweep_generation", minimum=1),
        "source_owner_subject_id": _name(body["source_owner_subject_id"], field="source_owner_subject_id"),
        "target_page_id": _page_id(body["target_page_id"]),
        "target_incarnation_id": incarnation_id,
        "target_incarnation_generation": _integer(body["target_incarnation_generation"], field="target_incarnation_generation", minimum=1),
        "target_revision": _integer(body["target_revision"], field="target_revision", minimum=1),
        "target_updated_at": _integer(body["target_updated_at"], field="target_updated_at", maximum=MAX_TIMESTAMP),
        "target_change_generation": _integer(body["target_change_generation"], field="target_change_generation", minimum=1),
        "target_change_mask": _integer(body["target_change_mask"], field="target_change_mask", minimum=1, maximum=CHANGE_ALL),
        "target_incarnation_receipt_sha256": _sha256(body["target_incarnation_receipt_sha256"], field="target_incarnation_receipt_sha256"),
        "old_parent_page_id": _page_id(body["old_parent_page_id"], allow_empty=True),
        "new_parent_page_id": _page_id(body["new_parent_page_id"], allow_empty=True),
        "target_dense_ordinal": _integer(body["target_dense_ordinal"], field="target_dense_ordinal", minimum=1, maximum=MAX_SIBLING_NODES),
        "subtree_count": _integer(body["subtree_count"], field="subtree_count", minimum=1, maximum=MAX_SUBTREE_NODES),
        "old_scope_count": _integer(body["old_scope_count"], field="old_scope_count", maximum=MAX_SIBLING_NODES),
        "new_scope_count": _integer(body["new_scope_count"], field="new_scope_count", minimum=1, maximum=MAX_SIBLING_NODES),
        "affected_page_count": _integer(body["affected_page_count"], field="affected_page_count", minimum=1, maximum=MAX_EFFECT_PAGE_RECEIPTS),
        "post_manifest_sha256": _sha256(body["post_manifest_sha256"], field="post_manifest_sha256"),
    }
    expected_incarnation = legacy_incarnation_receipt_sha256(
        source_owner_id=normalized["source_owner_subject_id"],
        page_id=normalized["target_page_id"],
        incarnation_id=normalized["target_incarnation_id"],
        incarnation_generation=normalized["target_incarnation_generation"],
        incarnation_retired=False,
        change_generation=normalized["target_change_generation"],
        change_mask=normalized["target_change_mask"],
        source_exists=True,
        source_active=True,
        source_epoch=normalized["source_epoch"],
        full_sweep_generation=normalized["full_sweep_generation"],
        source_device=normalized["source_device"],
        source_inode=normalized["source_inode"],
        source_link_count=normalized["source_link_count"],
    )
    if normalized["target_incarnation_receipt_sha256"] != expected_incarnation:
        raise LegacyPageStructureContractError("legacy page structure receipt is invalid")
    if normalized_action == CREATE_ACTION:
        if normalized["old_parent_page_id"] or not normalized["structure_changed"]:
            raise LegacyPageStructureContractError("legacy create receipt is invalid")
    elif normalized_action == MOVE_ACTION:
        if normalized["old_parent_page_id"] == normalized["new_parent_page_id"]:
            raise LegacyPageStructureContractError("legacy move receipt is invalid")
    elif normalized["old_parent_page_id"] != normalized["new_parent_page_id"]:
        raise LegacyPageStructureContractError("legacy reorder receipt is invalid")
    receipt_sha256 = sha256_payload({"domain": RECEIPT_DOMAIN, "result": normalized})
    return LegacyPageStructureReceipt(
        **normalized,
        source_operation_receipt_sha256=receipt_sha256,
    )


def validate_structure_receipt(
    value: Any, *, expected_request_sha256: object,
    expected_prestate: LegacyPageStructurePrestate,
) -> LegacyPageStructureReceipt:
    raw = value.descriptor() if isinstance(value, LegacyPageStructureReceipt) else value
    if not isinstance(raw, dict) or set(raw) != _RECEIPT_KEYS:
        raise LegacyPageStructureContractError("legacy page structure receipt is invalid")
    expected = validate_structure_prestate(expected_prestate)
    receipt = build_structure_receipt(
        action=raw["action"],
        source_request_sha256=raw["source_request_sha256"],
        source_changed=raw["source_changed"],
        structure_changed=raw["structure_changed"],
        poststate={key: raw[key] for key in _RECEIPT_KEYS - {
            "contract", "action", "source_request_sha256", "source_changed",
            "structure_changed", "source_operation_receipt_sha256",
        }},
    )
    if (
        receipt.contract != raw["contract"]
        or receipt.source_operation_receipt_sha256 != raw["source_operation_receipt_sha256"]
        or receipt.source_request_sha256 != _sha256(expected_request_sha256, field="expected_request_sha256")
        or receipt.action != expected.action
        or receipt.source_device != expected.source_device
        or receipt.source_inode != expected.source_inode
        or receipt.source_link_count != expected.source_link_count
        or receipt.source_owner_subject_id != expected.source_owner_subject_id
        or receipt.target_page_id != expected.target_page_id
        or receipt.old_parent_page_id != expected.old_parent_page_id
        or receipt.new_parent_page_id != expected.new_parent_page_id
        or receipt.source_epoch <= expected.source_epoch
        or receipt.full_sweep_generation <= expected.full_sweep_generation
    ):
        raise LegacyPageStructureContractError("legacy page structure receipt is invalid")
    if expected.action != CREATE_ACTION and (
        receipt.target_incarnation_id != expected.target_incarnation_id
        or receipt.target_incarnation_generation != expected.target_incarnation_generation
        or receipt.target_revision < expected.target_revision
        or receipt.target_updated_at < expected.target_updated_at
        or receipt.target_change_generation < expected.target_change_generation
        or (receipt.target_change_mask | expected.target_change_mask)
        != receipt.target_change_mask
    ):
        raise LegacyPageStructureContractError("legacy page structure receipt is invalid")
    return receipt


__all__ = [
    "CREATE_ACTION",
    "LegacyPageCreatePayload",
    "LegacyPageStructureContractError",
    "LegacyPageStructurePrestate",
    "LegacyPageStructureReceipt",
    "MAX_AGGREGATE_BYTES",
    "MAX_EFFECT_PAGE_RECEIPTS",
    "MAX_PAGE_BLOCKS",
    "MAX_PAGE_BYTES",
    "MAX_PAGE_FILES",
    "MAX_PARENT_BLOCKS",
    "MAX_RESULT_JSON_BYTES",
    "MAX_SIBLING_NODES",
    "MAX_SUBTREE_NODES",
    "MAX_TREE_DEPTH",
    "MOVE_ACTION",
    "REORDER_ACTION",
    "RESULT_CONTRACTS",
    "SOURCE_CONSUMER",
    "SOURCE_SCHEMA_VERSION",
    "STRUCTURAL_ACTIONS",
    "build_structure_receipt",
    "canonical_json",
    "derive_create_page_id",
    "derive_structure_source_operation_id",
    "legacy_page_structure_request_sha256",
    "normalize_create_payload",
    "sha256_payload",
    "validate_structure_prestate",
    "validate_structure_receipt",
]
