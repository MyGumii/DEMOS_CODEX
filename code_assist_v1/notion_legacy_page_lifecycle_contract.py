"""Side-effect-free NP-2C legacy page-lifecycle source contract.

The first source adapter is deliberately limited to a title rename on an
already authenticated legacy-v3 owner shard.  This module defines the exact
request and response-loss receipt envelopes without importing application,
route, authentication, configuration, or filesystem-location modules.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any

from code_assist_v1.notion_legacy_change_contract import CHANGE_ALL, CHANGE_PAGE, MAX_GENERATION
from code_assist_v1.notion_legacy_changes import (
    legacy_incarnation_receipt_sha256,
    sqlite_source_identity,
)


SOURCE_SCHEMA_VERSION = 2026081303
SOURCE_CONSUMER = "np2c.lifecycle"
RENAME_ACTION = "legacy.page_lifecycle.rename"
RENAME_RESULT_CONTRACT = "np2c_legacy_page_rename_receipt_v1"
RENAME_REQUEST_DOMAIN = "code_assist.notion.legacy_page_lifecycle.rename.request.v1"
RENAME_RECEIPT_DOMAIN = "code_assist.notion.legacy_page_lifecycle.rename.receipt.v1"
MAX_TIMESTAMP = 9_007_199_254_740_991

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{1,63}$")
_OPERATION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}$")
_INCARNATION_RE = re.compile(r"^inc_[0-9a-f]{32}$")
_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")

_STATE_KEYS = frozenset(
    {
        "source_device",
        "source_inode",
        "source_link_count",
        "source_epoch",
        "full_sweep_generation",
        "source_owner_subject_id",
        "source_page_id",
        "incarnation_id",
        "incarnation_generation",
        "revision",
        "updated_at",
        "change_generation",
        "change_mask",
        "source_exists",
        "source_active",
        "incarnation_receipt_sha256",
    }
)
_RESULT_KEYS = _STATE_KEYS | {
    "contract",
    "changed",
    "source_request_sha256",
    "source_operation_receipt_sha256",
}


class LegacyPageRenameContractError(Exception):
    """A persisted or supplied lifecycle envelope is not exact."""


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
        raise LegacyPageRenameContractError("legacy page rename evidence is invalid") from exc


def _sha256_payload(value: Any) -> str:
    try:
        encoded = _canonical_json(value).encode("utf-8")
    except UnicodeError as exc:
        raise LegacyPageRenameContractError("legacy page rename evidence is invalid") from exc
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _name(value: object, *, field: str) -> str:
    if not isinstance(value, str) or _NAME_RE.fullmatch(value) is None:
        raise LegacyPageRenameContractError(f"valid {field} required")
    return value


def _operation_id(value: object, *, field: str) -> str:
    if not isinstance(value, str) or _OPERATION_RE.fullmatch(value) is None:
        raise LegacyPageRenameContractError(f"valid {field} required")
    return value


def _page_id(value: object) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or len(value) > 160:
        raise LegacyPageRenameContractError("valid page_id required")
    return value


def _integer(
    value: object,
    *,
    field: str,
    minimum: int = 1,
    maximum: int = MAX_GENERATION,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise LegacyPageRenameContractError(f"valid {field} required")
    return value


def _sha256(value: object, *, field: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise LegacyPageRenameContractError(f"valid {field} required")
    return value


def normalize_legacy_page_title(value: object) -> str:
    """Apply the existing legacy title normalization without persisting input."""
    try:
        normalized = " ".join(str(value or "Untitled").strip().split())[:160]
        result = normalized or "Untitled"
        result.encode("utf-8")
        return result
    except (TypeError, ValueError, UnicodeError) as exc:
        raise LegacyPageRenameContractError("valid page title required") from exc


@dataclass(frozen=True)
class LegacyPageRenamePrestate:
    source_device: int
    source_inode: int
    source_link_count: int
    source_epoch: int
    full_sweep_generation: int
    source_owner_subject_id: str
    source_page_id: str
    incarnation_id: str
    incarnation_generation: int
    revision: int
    updated_at: int
    change_generation: int
    change_mask: int
    source_exists: bool
    source_active: bool
    incarnation_receipt_sha256: str

    def descriptor(self) -> dict[str, Any]:
        return {
            "source_device": self.source_device,
            "source_inode": self.source_inode,
            "source_link_count": self.source_link_count,
            "source_epoch": self.source_epoch,
            "full_sweep_generation": self.full_sweep_generation,
            "source_owner_subject_id": self.source_owner_subject_id,
            "source_page_id": self.source_page_id,
            "incarnation_id": self.incarnation_id,
            "incarnation_generation": self.incarnation_generation,
            "revision": self.revision,
            "updated_at": self.updated_at,
            "change_generation": self.change_generation,
            "change_mask": self.change_mask,
            "source_exists": self.source_exists,
            "source_active": self.source_active,
            "incarnation_receipt_sha256": self.incarnation_receipt_sha256,
        }


@dataclass(frozen=True)
class LegacyPageRenameReceipt:
    changed: bool
    source_request_sha256: str
    source_device: int
    source_inode: int
    source_link_count: int
    source_epoch: int
    full_sweep_generation: int
    source_owner_subject_id: str
    source_page_id: str
    incarnation_id: str
    incarnation_generation: int
    revision: int
    updated_at: int
    change_generation: int
    change_mask: int
    source_exists: bool
    source_active: bool
    incarnation_receipt_sha256: str
    source_operation_receipt_sha256: str

    def descriptor(self) -> dict[str, Any]:
        return {
            "contract": RENAME_RESULT_CONTRACT,
            "changed": self.changed,
            "source_request_sha256": self.source_request_sha256,
            "source_device": self.source_device,
            "source_inode": self.source_inode,
            "source_link_count": self.source_link_count,
            "source_epoch": self.source_epoch,
            "full_sweep_generation": self.full_sweep_generation,
            "source_owner_subject_id": self.source_owner_subject_id,
            "source_page_id": self.source_page_id,
            "incarnation_id": self.incarnation_id,
            "incarnation_generation": self.incarnation_generation,
            "revision": self.revision,
            "updated_at": self.updated_at,
            "change_generation": self.change_generation,
            "change_mask": self.change_mask,
            "source_exists": self.source_exists,
            "source_active": self.source_active,
            "incarnation_receipt_sha256": self.incarnation_receipt_sha256,
            "source_operation_receipt_sha256": self.source_operation_receipt_sha256,
        }


def validate_rename_prestate(value: Any) -> LegacyPageRenamePrestate:
    if isinstance(value, LegacyPageRenamePrestate):
        raw = value.descriptor()
    elif isinstance(value, dict) and set(value) == _STATE_KEYS:
        raw = dict(value)
    else:
        raise LegacyPageRenameContractError("legacy page rename prestate is invalid")
    try:
        device = sqlite_source_identity(raw["source_device"])
        inode = sqlite_source_identity(raw["source_inode"])
    except (TypeError, ValueError) as exc:
        raise LegacyPageRenameContractError("legacy page rename prestate is invalid") from exc
    exists = raw["source_exists"]
    active = raw["source_active"]
    if type(exists) is not bool or type(active) is not bool or not exists or not active:
        raise LegacyPageRenameContractError("legacy page rename requires an active source page")
    incarnation_id = raw["incarnation_id"]
    if not isinstance(incarnation_id, str) or _INCARNATION_RE.fullmatch(incarnation_id) is None:
        raise LegacyPageRenameContractError("legacy page rename prestate is invalid")
    state = LegacyPageRenamePrestate(
        source_device=device,
        source_inode=inode,
        source_link_count=_integer(
            raw["source_link_count"], field="source_link_count", maximum=65_535
        ),
        source_epoch=_integer(raw["source_epoch"], field="source_epoch"),
        full_sweep_generation=_integer(
            raw["full_sweep_generation"], field="full_sweep_generation"
        ),
        source_owner_subject_id=_name(
            raw["source_owner_subject_id"], field="source_owner_subject_id"
        ),
        source_page_id=_page_id(raw["source_page_id"]),
        incarnation_id=incarnation_id,
        incarnation_generation=_integer(
            raw["incarnation_generation"], field="incarnation_generation"
        ),
        revision=_integer(raw["revision"], field="revision"),
        updated_at=_integer(
            raw["updated_at"], field="updated_at", minimum=0, maximum=MAX_TIMESTAMP
        ),
        change_generation=_integer(
            raw["change_generation"], field="change_generation"
        ),
        change_mask=_integer(
            raw["change_mask"], field="change_mask", maximum=CHANGE_ALL
        ),
        source_exists=True,
        source_active=True,
        incarnation_receipt_sha256=_sha256(
            raw["incarnation_receipt_sha256"], field="incarnation_receipt_sha256"
        ),
    )
    expected_receipt = legacy_incarnation_receipt_sha256(
        source_owner_id=state.source_owner_subject_id,
        page_id=state.source_page_id,
        incarnation_id=state.incarnation_id,
        incarnation_generation=state.incarnation_generation,
        incarnation_retired=False,
        change_generation=state.change_generation,
        change_mask=state.change_mask,
        source_exists=True,
        source_active=True,
        source_epoch=state.source_epoch,
        full_sweep_generation=state.full_sweep_generation,
        source_device=state.source_device,
        source_inode=state.source_inode,
        source_link_count=state.source_link_count,
    )
    if state.incarnation_receipt_sha256 != expected_receipt:
        raise LegacyPageRenameContractError("legacy page rename prestate is invalid")
    return state


def derive_rename_source_operation_id(
    *, catalog_command_id: object, caller_operation_id: object
) -> str:
    command = _operation_id(catalog_command_id, field="catalog_command_id")
    caller = _operation_id(caller_operation_id, field="caller_operation_id")
    digest = hashlib.sha256(
        _canonical_json(
            {
                "domain": "code_assist.notion.legacy_page_lifecycle.rename.operation.v1",
                "catalog_command_id": command,
                "caller_operation_id": caller,
            }
        ).encode("utf-8")
    ).hexdigest()
    return "np2c-rename:" + digest


def legacy_page_rename_request_sha256(
    *,
    mutation_actor_subject_id: object,
    normalized_title: object,
    catalog_command_id: object,
    caller_operation_id: object,
    expected_prestate: LegacyPageRenamePrestate,
) -> str:
    actor = _name(mutation_actor_subject_id, field="mutation_actor_subject_id")
    title = normalize_legacy_page_title(normalized_title)
    command = _operation_id(catalog_command_id, field="catalog_command_id")
    caller = _operation_id(caller_operation_id, field="caller_operation_id")
    source_operation_id = derive_rename_source_operation_id(
        catalog_command_id=command, caller_operation_id=caller
    )
    expected = validate_rename_prestate(expected_prestate)
    return _sha256_payload(
        {
            "domain": RENAME_REQUEST_DOMAIN,
            "consumer": SOURCE_CONSUMER,
            "action": RENAME_ACTION,
            "source_operation_id": source_operation_id,
            "catalog_command_id": command,
            "caller_operation_id": caller,
            "mutation_actor_subject_id": actor,
            "normalized_title": title,
            "expected_prestate": expected.descriptor(),
        }
    )


def build_rename_receipt(
    *,
    changed: bool,
    source_request_sha256: str,
    poststate: LegacyPageRenamePrestate,
) -> LegacyPageRenameReceipt:
    if type(changed) is not bool:
        raise LegacyPageRenameContractError("legacy page rename receipt is invalid")
    request_sha256 = _sha256(source_request_sha256, field="source_request_sha256")
    state = validate_rename_prestate(poststate)
    body = {
        "contract": RENAME_RESULT_CONTRACT,
        "changed": changed,
        "source_request_sha256": request_sha256,
        **state.descriptor(),
    }
    receipt_sha256 = _sha256_payload(
        {"domain": RENAME_RECEIPT_DOMAIN, "result": body}
    )
    return LegacyPageRenameReceipt(
        changed=changed,
        source_request_sha256=request_sha256,
        **state.__dict__,
        source_operation_receipt_sha256=receipt_sha256,
    )


def validate_rename_receipt(
    value: Any,
    *,
    expected_request_sha256: str,
    expected_prestate: LegacyPageRenamePrestate,
) -> LegacyPageRenameReceipt:
    raw = value.descriptor() if isinstance(value, LegacyPageRenameReceipt) else value
    if not isinstance(raw, dict) or set(raw) != _RESULT_KEYS:
        raise LegacyPageRenameContractError("legacy page rename receipt is invalid")
    if raw.get("contract") != RENAME_RESULT_CONTRACT or type(raw.get("changed")) is not bool:
        raise LegacyPageRenameContractError("legacy page rename receipt is invalid")
    state = validate_rename_prestate({key: raw[key] for key in _STATE_KEYS})
    receipt = build_rename_receipt(
        changed=raw["changed"],
        source_request_sha256=raw["source_request_sha256"],
        poststate=state,
    )
    if receipt.source_request_sha256 != _sha256(
        expected_request_sha256, field="expected_request_sha256"
    ) or receipt.source_operation_receipt_sha256 != raw["source_operation_receipt_sha256"]:
        raise LegacyPageRenameContractError("legacy page rename receipt is invalid")
    expected = validate_rename_prestate(expected_prestate)
    common = (
        receipt.source_device == expected.source_device
        and receipt.source_inode == expected.source_inode
        and receipt.source_link_count == expected.source_link_count
        and receipt.source_owner_subject_id == expected.source_owner_subject_id
        and receipt.source_page_id == expected.source_page_id
        and receipt.incarnation_id == expected.incarnation_id
        and receipt.incarnation_generation == expected.incarnation_generation
        and receipt.source_exists
        and receipt.source_active
    )
    if not common:
        raise LegacyPageRenameContractError("legacy page rename receipt is invalid")
    if receipt.changed:
        if (
            expected.revision >= MAX_GENERATION
            or expected.change_generation >= MAX_GENERATION
            or expected.source_epoch >= MAX_GENERATION
            or expected.full_sweep_generation >= MAX_GENERATION
            or receipt.revision != expected.revision + 1
            or receipt.change_generation != expected.change_generation + 1
            or receipt.source_epoch != expected.source_epoch + 1
            or receipt.full_sweep_generation != expected.full_sweep_generation + 1
            or receipt.change_mask != (expected.change_mask | CHANGE_PAGE)
            or receipt.updated_at < expected.updated_at
        ):
            raise LegacyPageRenameContractError("legacy page rename receipt is invalid")
    elif state.descriptor() != expected.descriptor():
        raise LegacyPageRenameContractError("legacy page rename receipt is invalid")
    return receipt


__all__ = [
    "LegacyPageRenameContractError",
    "LegacyPageRenamePrestate",
    "LegacyPageRenameReceipt",
    "RENAME_ACTION",
    "RENAME_RESULT_CONTRACT",
    "SOURCE_CONSUMER",
    "SOURCE_SCHEMA_VERSION",
    "build_rename_receipt",
    "derive_rename_source_operation_id",
    "legacy_page_rename_request_sha256",
    "normalize_legacy_page_title",
    "validate_rename_prestate",
    "validate_rename_receipt",
]
