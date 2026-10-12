"""Frozen source-receipt contract for NP2C duplicate, Trash, and restore.

The contract contains no filesystem, SQLite, application, or route imports.  It
is shared by the route-unregistered source adapter and later catalog work.  A
receipt is source-local evidence, not a claim of cross-database exactly-once
delivery or runtime parity.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any


LIFECYCLE_CONSUMER = "np2c.lifecycle"
LIFECYCLE_ACTIONS = frozenset({"duplicate", "trash", "restore"})
LIFECYCLE_RESULT_CONTRACTS = {
    "duplicate": "np2c_legacy_page_duplicate_receipt_v1",
    "trash": "np2c_legacy_page_trash_receipt_v1",
    "restore": "np2c_legacy_page_restore_receipt_v1",
}
LIFECYCLE_REQUEST_DOMAIN = (
    "code_assist.notion.legacy_page_lifecycle.duplicate_trash_restore.request.v1"
)
LIFECYCLE_RECEIPT_DOMAIN = (
    "code_assist.notion.legacy_page_lifecycle.duplicate_trash_restore.receipt.v1"
)
LIFECYCLE_MANIFEST_DOMAIN = (
    "code_assist.notion.legacy_page_lifecycle.duplicate_trash_restore.manifest.v1"
)
LIFECYCLE_FILE_MANIFEST_DOMAIN = (
    "code_assist.notion.legacy_page_lifecycle.duplicate.file_manifest.v1"
)
LIFECYCLE_PRECONDITION_DOMAIN = (
    "code_assist.notion.legacy_page_lifecycle.duplicate_trash_restore.precondition.v1"
)
LIFECYCLE_MAPPING_DOMAIN = (
    "code_assist.notion.legacy_page_lifecycle.duplicate.mapping.v1"
)
LIFECYCLE_OPERATION_DOMAIN = (
    "code_assist.notion.legacy_page_lifecycle.duplicate_trash_restore.operation.v1"
)

MAX_LIFECYCLE_SUBTREE = 1_000
MAX_LIFECYCLE_DEPTH = 256
MAX_LIFECYCLE_SIBLINGS = 10_000
MAX_LIFECYCLE_BLOCKS = 50_000
MAX_LIFECYCLE_FILES = 5_000
MAX_LIFECYCLE_FILE_BYTES = 256 * 1024 * 1024
MAX_LIFECYCLE_METADATA_BYTES = 256 * 1024 * 1024
MAX_LIFECYCLE_PARENT_BLOCKS = 5_000
MAX_LIFECYCLE_REQUEST_BYTES = 64 * 1024
MAX_LIFECYCLE_RESULT_BYTES = 16 * 1024 * 1024
MAX_LIFECYCLE_EFFECTS = MAX_LIFECYCLE_SUBTREE + MAX_LIFECYCLE_SIBLINGS + 4
MAX_TIMESTAMP = 253_402_300_799
MAX_GENERATION = 9_007_199_254_740_991

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{1,63}$")
_OPERATION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}$")
_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_INCARNATION_RE = re.compile(r"^inc_[0-9a-f]{32}$")
_MAPPING_KINDS = frozenset({"page", "block", "file", "parent_reference_block"})
_LIFECYCLE_STATES = frozenset({"absent", "active", "trashed"})


class LegacyPageLifecycleContractError(Exception):
    """Sanitized invalid duplicate/Trash/restore evidence."""


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
        raise LegacyPageLifecycleContractError(
            "legacy page lifecycle evidence is invalid"
        ) from exc


def digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def normalize_action(value: object) -> str:
    action = str(value or "")
    if action not in LIFECYCLE_ACTIONS:
        raise ValueError("valid lifecycle action required")
    return action


def normalize_name(value: object, *, field: str) -> str:
    if not isinstance(value, str) or _NAME_RE.fullmatch(value) is None:
        raise ValueError(f"valid {field} required")
    return value


def normalize_operation_id(value: object, *, field: str) -> str:
    if not isinstance(value, str) or _OPERATION_RE.fullmatch(value) is None:
        raise ValueError(f"valid {field} required")
    return value


def normalize_object_id(
    value: object, *, field: str, allow_empty: bool = False
) -> str:
    if allow_empty and value in {None, ""}:
        return ""
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > 160
        or "\x00" in value
    ):
        raise ValueError(f"valid {field} required")
    return value


def normalize_sha256(value: object, *, field: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"valid {field} required")
    return value


def _integer(
    value: object, *, minimum: int, maximum: int, field: str
) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise LegacyPageLifecycleContractError(
            "legacy page lifecycle receipt is invalid"
        )
    return value


def _optional_integer(
    value: object, *, minimum: int, maximum: int, field: str
) -> int | None:
    if value is None:
        return None
    return _integer(value, minimum=minimum, maximum=maximum, field=field)


@dataclass(frozen=True)
class LegacyPageLifecycleObjectMapping:
    kind: str
    source_object_id: str
    result_object_id: str
    source_page_id: str
    result_page_id: str
    byte_size: int
    content_sha256: str

    def descriptor(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "source_object_id": self.source_object_id,
            "result_object_id": self.result_object_id,
            "source_page_id": self.source_page_id,
            "result_page_id": self.result_page_id,
            "byte_size": self.byte_size,
            "content_sha256": self.content_sha256,
        }


@dataclass(frozen=True)
class LegacyPageLifecycleEffect:
    source_page_id: str
    result_page_id: str
    source_incarnation_id: str
    source_incarnation_generation: int
    source_revision: int
    source_change_generation: int
    result_incarnation_id: str
    result_incarnation_generation: int
    result_revision: int
    result_change_generation: int
    before_lifecycle: str
    after_lifecycle: str
    before_parent_source_page_id: str
    after_parent_source_page_id: str
    before_sibling_ordinal: int | None
    after_sibling_ordinal: int | None
    transitioned: bool

    def descriptor(self) -> dict[str, Any]:
        return {
            "source_page_id": self.source_page_id,
            "result_page_id": self.result_page_id,
            "source_incarnation_id": self.source_incarnation_id,
            "source_incarnation_generation": self.source_incarnation_generation,
            "source_revision": self.source_revision,
            "source_change_generation": self.source_change_generation,
            "result_incarnation_id": self.result_incarnation_id,
            "result_incarnation_generation": self.result_incarnation_generation,
            "result_revision": self.result_revision,
            "result_change_generation": self.result_change_generation,
            "before_lifecycle": self.before_lifecycle,
            "after_lifecycle": self.after_lifecycle,
            "before_parent_source_page_id": self.before_parent_source_page_id,
            "after_parent_source_page_id": self.after_parent_source_page_id,
            "before_sibling_ordinal": self.before_sibling_ordinal,
            "after_sibling_ordinal": self.after_sibling_ordinal,
            "transitioned": self.transitioned,
        }


@dataclass(frozen=True)
class LegacyPageLifecycleReceipt:
    action: str
    source_owner_subject_id: str
    root_source_page_id: str
    result_root_source_page_id: str
    source_request_sha256: str
    source_operation_receipt_sha256: str
    post_manifest_sha256: str
    file_manifest_sha256: str
    source_device: int
    source_inode: int
    source_link_count: int
    source_epoch: int
    full_sweep_generation: int
    trash_source_operation_id: str
    placement_mode: str
    effects: tuple[LegacyPageLifecycleEffect, ...]
    mappings: tuple[LegacyPageLifecycleObjectMapping, ...]

    def descriptor(self, *, include_receipt: bool = True) -> dict[str, Any]:
        result = {
            "contract": LIFECYCLE_RESULT_CONTRACTS[self.action],
            "action": self.action,
            "source_owner_subject_id": self.source_owner_subject_id,
            "root_source_page_id": self.root_source_page_id,
            "result_root_source_page_id": self.result_root_source_page_id,
            "source_request_sha256": self.source_request_sha256,
            "post_manifest_sha256": self.post_manifest_sha256,
            "file_manifest_sha256": self.file_manifest_sha256,
            "source_device": self.source_device,
            "source_inode": self.source_inode,
            "source_link_count": self.source_link_count,
            "source_epoch": self.source_epoch,
            "full_sweep_generation": self.full_sweep_generation,
            "trash_source_operation_id": self.trash_source_operation_id,
            "placement_mode": self.placement_mode,
            "effects": [effect.descriptor() for effect in self.effects],
            "mappings": [mapping.descriptor() for mapping in self.mappings],
        }
        if include_receipt:
            result["source_operation_receipt_sha256"] = (
                self.source_operation_receipt_sha256
            )
        return result


def derive_lifecycle_source_operation_id(
    *, action: object, catalog_command_id: object, caller_operation_id: object
) -> str:
    exact_action = normalize_action(action)
    command = normalize_operation_id(catalog_command_id, field="catalog_command_id")
    caller = normalize_operation_id(caller_operation_id, field="caller_operation_id")
    return "np2c-lifecycle:" + hashlib.sha256(
        canonical_json(
            {
                "domain": LIFECYCLE_OPERATION_DOMAIN,
                "action": exact_action,
                "catalog_command_id": command,
                "caller_operation_id": caller,
            }
        ).encode("utf-8")
    ).hexdigest()


def lifecycle_request_sha256(
    *,
    action: object,
    mutation_actor_subject_id: object,
    source_owner_subject_id: object,
    root_source_page_id: object,
    catalog_command_id: object,
    caller_operation_id: object,
    expected_precondition_sha256: object,
    trash_source_operation_id: object = "",
    placement_mode: object = "",
    fallback_parent_source_page_id: object = "",
    relative_source_page_id: object = "",
    relative_position: object = "",
) -> str:
    exact_action = normalize_action(action)
    actor = normalize_name(
        mutation_actor_subject_id, field="mutation_actor_subject_id"
    )
    owner = normalize_name(source_owner_subject_id, field="source_owner_subject_id")
    root = normalize_object_id(root_source_page_id, field="root_source_page_id")
    command = normalize_operation_id(catalog_command_id, field="catalog_command_id")
    caller = normalize_operation_id(caller_operation_id, field="caller_operation_id")
    expected = normalize_sha256(
        expected_precondition_sha256, field="expected_precondition_sha256"
    )
    trash_operation = ""
    mode = ""
    parent = ""
    relative = ""
    position = ""
    if exact_action == "restore":
        trash_operation = normalize_operation_id(
            trash_source_operation_id, field="trash_source_operation_id"
        )
        mode = str(placement_mode or "")
        if mode not in {"original", "reviewed_fallback"}:
            raise ValueError("valid restore placement_mode required")
        parent = normalize_object_id(
            fallback_parent_source_page_id,
            field="fallback_parent_source_page_id",
            allow_empty=True,
        )
        relative = normalize_object_id(
            relative_source_page_id,
            field="relative_source_page_id",
            allow_empty=True,
        )
        position = str(relative_position or "")
        if bool(relative) != bool(position) or position not in {"", "before", "after"}:
            raise ValueError("valid restore relative target required")
        if mode == "original" and (parent or relative or position):
            raise ValueError("original restore does not accept fallback placement")
    elif any(
        value not in {None, ""}
        for value in (
            trash_source_operation_id,
            placement_mode,
            fallback_parent_source_page_id,
            relative_source_page_id,
            relative_position,
        )
    ):
        raise ValueError("non-restore lifecycle action has restore-only input")
    request = {
        "domain": LIFECYCLE_REQUEST_DOMAIN,
        "consumer": LIFECYCLE_CONSUMER,
        "action": exact_action,
        "source_operation_id": derive_lifecycle_source_operation_id(
            action=exact_action,
            catalog_command_id=command,
            caller_operation_id=caller,
        ),
        "catalog_command_id": command,
        "caller_operation_id": caller,
        "mutation_actor_subject_id": actor,
        "source_owner_subject_id": owner,
        "root_source_page_id": root,
        "expected_precondition_sha256": expected,
        "trash_source_operation_id": trash_operation,
        "placement_mode": mode,
        "fallback_parent_source_page_id": parent,
        "relative_source_page_id": relative,
        "relative_position": position,
    }
    encoded = canonical_json(request).encode("utf-8")
    if len(encoded) > MAX_LIFECYCLE_REQUEST_BYTES:
        raise ValueError("lifecycle request exceeds the adapter bound")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def derive_duplicate_object_id(
    *,
    kind: object,
    prefix: object,
    source_operation_id: object,
    source_owner_subject_id: object,
    mutation_actor_subject_id: object,
    root_source_page_id: object,
    root_incarnation_id: object,
    root_incarnation_generation: object,
    source_object_id: object,
) -> str:
    exact_kind = str(kind or "")
    if exact_kind not in _MAPPING_KINDS | {"version"}:
        raise ValueError("valid duplicate mapping kind required")
    exact_prefix = str(prefix or "")
    if exact_prefix not in {"p_", "b_", "f_", "v_"}:
        raise ValueError("valid duplicate mapping prefix required")
    operation = normalize_operation_id(
        source_operation_id, field="source_operation_id"
    )
    owner = normalize_name(source_owner_subject_id, field="source_owner_subject_id")
    actor = normalize_name(
        mutation_actor_subject_id, field="mutation_actor_subject_id"
    )
    root = normalize_object_id(root_source_page_id, field="root_source_page_id")
    incarnation = str(root_incarnation_id or "")
    if _INCARNATION_RE.fullmatch(incarnation) is None:
        raise ValueError("valid root_incarnation_id required")
    generation = _integer(
        root_incarnation_generation,
        minimum=1,
        maximum=MAX_GENERATION,
        field="root_incarnation_generation",
    )
    source = normalize_object_id(source_object_id, field="source_object_id")
    payload = {
        "domain": LIFECYCLE_MAPPING_DOMAIN,
        "kind": exact_kind,
        "source_operation_id": operation,
        "source_owner_subject_id": owner,
        "mutation_actor_subject_id": actor,
        "root_source_page_id": root,
        "root_incarnation_id": incarnation,
        "root_incarnation_generation": generation,
        "source_object_id": source,
    }
    return exact_prefix + hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()[:32]


def _mapping_from_descriptor(raw: Any) -> LegacyPageLifecycleObjectMapping:
    keys = {
        "kind",
        "source_object_id",
        "result_object_id",
        "source_page_id",
        "result_page_id",
        "byte_size",
        "content_sha256",
    }
    if not isinstance(raw, dict) or set(raw) != keys:
        raise LegacyPageLifecycleContractError(
            "legacy page lifecycle receipt is invalid"
        )
    kind = str(raw["kind"] or "")
    if kind not in _MAPPING_KINDS:
        raise LegacyPageLifecycleContractError(
            "legacy page lifecycle receipt is invalid"
        )
    byte_size = _integer(
        raw["byte_size"],
        minimum=0,
        maximum=MAX_LIFECYCLE_FILE_BYTES,
        field="byte_size",
    )
    content_sha256 = str(raw["content_sha256"] or "")
    if kind == "file":
        normalize_sha256(content_sha256, field="content_sha256")
    elif byte_size != 0 or content_sha256:
        raise LegacyPageLifecycleContractError(
            "legacy page lifecycle receipt is invalid"
        )
    return LegacyPageLifecycleObjectMapping(
        kind=kind,
        source_object_id=normalize_object_id(
            raw["source_object_id"], field="source_object_id"
        ),
        result_object_id=normalize_object_id(
            raw["result_object_id"], field="result_object_id"
        ),
        source_page_id=normalize_object_id(
            raw["source_page_id"], field="source_page_id"
        ),
        result_page_id=normalize_object_id(
            raw["result_page_id"], field="result_page_id"
        ),
        byte_size=byte_size,
        content_sha256=content_sha256,
    )


def _effect_from_descriptor(raw: Any) -> LegacyPageLifecycleEffect:
    keys = {
        "source_page_id",
        "result_page_id",
        "source_incarnation_id",
        "source_incarnation_generation",
        "source_revision",
        "source_change_generation",
        "result_incarnation_id",
        "result_incarnation_generation",
        "result_revision",
        "result_change_generation",
        "before_lifecycle",
        "after_lifecycle",
        "before_parent_source_page_id",
        "after_parent_source_page_id",
        "before_sibling_ordinal",
        "after_sibling_ordinal",
        "transitioned",
    }
    if not isinstance(raw, dict) or set(raw) != keys or type(raw["transitioned"]) is not bool:
        raise LegacyPageLifecycleContractError(
            "legacy page lifecycle receipt is invalid"
        )
    before = str(raw["before_lifecycle"] or "")
    after = str(raw["after_lifecycle"] or "")
    if before not in _LIFECYCLE_STATES or after not in _LIFECYCLE_STATES:
        raise LegacyPageLifecycleContractError(
            "legacy page lifecycle receipt is invalid"
        )
    source_incarnation = str(raw["source_incarnation_id"] or "")
    result_incarnation = str(raw["result_incarnation_id"] or "")
    if (
        _INCARNATION_RE.fullmatch(source_incarnation) is None
        or _INCARNATION_RE.fullmatch(result_incarnation) is None
    ):
        raise LegacyPageLifecycleContractError(
            "legacy page lifecycle receipt is invalid"
        )
    before_ordinal = _optional_integer(
        raw["before_sibling_ordinal"],
        minimum=1,
        maximum=MAX_LIFECYCLE_SIBLINGS + MAX_LIFECYCLE_SUBTREE,
        field="before_sibling_ordinal",
    )
    after_ordinal = _optional_integer(
        raw["after_sibling_ordinal"],
        minimum=1,
        maximum=MAX_LIFECYCLE_SIBLINGS + MAX_LIFECYCLE_SUBTREE,
        field="after_sibling_ordinal",
    )
    if (before == "active") != (before_ordinal is not None) or (
        after == "active"
    ) != (after_ordinal is not None):
        raise LegacyPageLifecycleContractError(
            "legacy page lifecycle receipt is invalid"
        )
    return LegacyPageLifecycleEffect(
        source_page_id=normalize_object_id(
            raw["source_page_id"], field="source_page_id"
        ),
        result_page_id=normalize_object_id(
            raw["result_page_id"], field="result_page_id"
        ),
        source_incarnation_id=source_incarnation,
        source_incarnation_generation=_integer(
            raw["source_incarnation_generation"],
            minimum=1,
            maximum=MAX_GENERATION,
            field="source_incarnation_generation",
        ),
        source_revision=_integer(
            raw["source_revision"], minimum=1, maximum=MAX_GENERATION, field="source_revision"
        ),
        source_change_generation=_integer(
            raw["source_change_generation"],
            minimum=1,
            maximum=MAX_GENERATION,
            field="source_change_generation",
        ),
        result_incarnation_id=result_incarnation,
        result_incarnation_generation=_integer(
            raw["result_incarnation_generation"],
            minimum=1,
            maximum=MAX_GENERATION,
            field="result_incarnation_generation",
        ),
        result_revision=_integer(
            raw["result_revision"], minimum=1, maximum=MAX_GENERATION, field="result_revision"
        ),
        result_change_generation=_integer(
            raw["result_change_generation"],
            minimum=1,
            maximum=MAX_GENERATION,
            field="result_change_generation",
        ),
        before_lifecycle=before,
        after_lifecycle=after,
        before_parent_source_page_id=normalize_object_id(
            raw["before_parent_source_page_id"],
            field="before_parent_source_page_id",
            allow_empty=True,
        ),
        after_parent_source_page_id=normalize_object_id(
            raw["after_parent_source_page_id"],
            field="after_parent_source_page_id",
            allow_empty=True,
        ),
        before_sibling_ordinal=before_ordinal,
        after_sibling_ordinal=after_ordinal,
        transitioned=raw["transitioned"],
    )


def validate_lifecycle_receipt(
    value: Any,
    *,
    expected_action: object,
    expected_request_sha256: object,
) -> LegacyPageLifecycleReceipt:
    action = normalize_action(expected_action)
    request_sha256 = normalize_sha256(
        expected_request_sha256, field="expected_request_sha256"
    )
    raw = value.descriptor() if isinstance(value, LegacyPageLifecycleReceipt) else value
    keys = {
        "contract",
        "action",
        "source_owner_subject_id",
        "root_source_page_id",
        "result_root_source_page_id",
        "source_request_sha256",
        "source_operation_receipt_sha256",
        "post_manifest_sha256",
        "file_manifest_sha256",
        "source_device",
        "source_inode",
        "source_link_count",
        "source_epoch",
        "full_sweep_generation",
        "trash_source_operation_id",
        "placement_mode",
        "effects",
        "mappings",
    }
    if (
        not isinstance(raw, dict)
        or set(raw) != keys
        or raw["action"] != action
        or raw["contract"] != LIFECYCLE_RESULT_CONTRACTS[action]
    ):
        raise LegacyPageLifecycleContractError(
            "legacy page lifecycle receipt is invalid"
        )
    effects_raw = raw["effects"]
    mappings_raw = raw["mappings"]
    if (
        not isinstance(effects_raw, list)
        or not 1 <= len(effects_raw) <= MAX_LIFECYCLE_EFFECTS
        or not isinstance(mappings_raw, list)
        or len(mappings_raw)
        > MAX_LIFECYCLE_SUBTREE + MAX_LIFECYCLE_BLOCKS + MAX_LIFECYCLE_FILES + MAX_LIFECYCLE_PARENT_BLOCKS
    ):
        raise LegacyPageLifecycleContractError(
            "legacy page lifecycle receipt is invalid"
        )
    effects = tuple(_effect_from_descriptor(item) for item in effects_raw)
    mappings = tuple(_mapping_from_descriptor(item) for item in mappings_raw)
    effect_keys = tuple((item.result_page_id, item.source_page_id) for item in effects)
    mapping_keys = tuple(
        (item.kind, item.source_object_id, item.result_object_id) for item in mappings
    )
    if effect_keys != tuple(sorted(effect_keys)) or len(set(effect_keys)) != len(effect_keys):
        raise LegacyPageLifecycleContractError(
            "legacy page lifecycle receipt is invalid"
        )
    if mapping_keys != tuple(sorted(mapping_keys)) or len(set(mapping_keys)) != len(mapping_keys):
        raise LegacyPageLifecycleContractError(
            "legacy page lifecycle receipt is invalid"
        )
    if len({item.result_object_id for item in mappings}) != len(mappings):
        raise LegacyPageLifecycleContractError(
            "legacy page lifecycle receipt is invalid"
        )
    total_file_bytes = sum(item.byte_size for item in mappings if item.kind == "file")
    if (
        sum(1 for item in mappings if item.kind == "file") > MAX_LIFECYCLE_FILES
        or total_file_bytes > MAX_LIFECYCLE_FILE_BYTES
        or sum(1 for item in mappings if item.kind == "block") > MAX_LIFECYCLE_BLOCKS
        or sum(1 for item in effects if item.transitioned) > MAX_LIFECYCLE_SUBTREE
    ):
        raise LegacyPageLifecycleContractError(
            "legacy page lifecycle receipt is invalid"
        )
    trash_operation = str(raw["trash_source_operation_id"] or "")
    placement = str(raw["placement_mode"] or "")
    if action == "restore":
        normalize_operation_id(trash_operation, field="trash_source_operation_id")
        if placement not in {"original", "reviewed_fallback"}:
            raise LegacyPageLifecycleContractError(
                "legacy page lifecycle receipt is invalid"
            )
    elif trash_operation or placement:
        raise LegacyPageLifecycleContractError(
            "legacy page lifecycle receipt is invalid"
        )
    if action == "duplicate":
        if not any(
            item.transitioned
            and item.before_lifecycle == "absent"
            and item.after_lifecycle == "active"
            for item in effects
        ) or any(
            item.transitioned
            and (item.before_lifecycle, item.after_lifecycle) != ("absent", "active")
            for item in effects
        ):
            raise LegacyPageLifecycleContractError(
                "legacy page lifecycle receipt is invalid"
            )
    elif action == "trash" and any(
        item.transitioned
        and (item.before_lifecycle, item.after_lifecycle) != ("active", "trashed")
        for item in effects
    ):
        raise LegacyPageLifecycleContractError(
            "legacy page lifecycle receipt is invalid"
        )
    elif action == "restore" and any(
        item.transitioned
        and (item.before_lifecycle, item.after_lifecycle) != ("trashed", "active")
        for item in effects
    ):
        raise LegacyPageLifecycleContractError(
            "legacy page lifecycle receipt is invalid"
        )
    receipt = LegacyPageLifecycleReceipt(
        action=action,
        source_owner_subject_id=normalize_name(
            raw["source_owner_subject_id"], field="source_owner_subject_id"
        ),
        root_source_page_id=normalize_object_id(
            raw["root_source_page_id"], field="root_source_page_id"
        ),
        result_root_source_page_id=normalize_object_id(
            raw["result_root_source_page_id"], field="result_root_source_page_id"
        ),
        source_request_sha256=normalize_sha256(
            raw["source_request_sha256"], field="source_request_sha256"
        ),
        source_operation_receipt_sha256=normalize_sha256(
            raw["source_operation_receipt_sha256"],
            field="source_operation_receipt_sha256",
        ),
        post_manifest_sha256=normalize_sha256(
            raw["post_manifest_sha256"], field="post_manifest_sha256"
        ),
        file_manifest_sha256=normalize_sha256(
            raw["file_manifest_sha256"], field="file_manifest_sha256"
        ),
        source_device=_integer(
            raw["source_device"], minimum=-(2**63), maximum=2**63 - 1, field="source_device"
        ),
        source_inode=_integer(
            raw["source_inode"], minimum=-(2**63), maximum=2**63 - 1, field="source_inode"
        ),
        source_link_count=_integer(
            raw["source_link_count"], minimum=1, maximum=65_535, field="source_link_count"
        ),
        source_epoch=_integer(
            raw["source_epoch"], minimum=1, maximum=MAX_GENERATION, field="source_epoch"
        ),
        full_sweep_generation=_integer(
            raw["full_sweep_generation"],
            minimum=1,
            maximum=MAX_GENERATION,
            field="full_sweep_generation",
        ),
        trash_source_operation_id=trash_operation,
        placement_mode=placement,
        effects=effects,
        mappings=mappings,
    )
    if receipt.source_request_sha256 != request_sha256:
        raise LegacyPageLifecycleContractError(
            "legacy page lifecycle receipt is invalid"
        )
    root_effect = next(
        (item for item in effects if item.result_page_id == receipt.result_root_source_page_id),
        None,
    )
    if root_effect is None or root_effect.source_page_id != receipt.root_source_page_id:
        raise LegacyPageLifecycleContractError(
            "legacy page lifecycle receipt is invalid"
        )
    file_manifest = digest(
        {
            "domain": LIFECYCLE_FILE_MANIFEST_DOMAIN,
            "action": action,
            "files": [
                item.descriptor() for item in mappings if item.kind == "file"
            ],
        }
    )
    if receipt.file_manifest_sha256 != file_manifest:
        raise LegacyPageLifecycleContractError(
            "legacy page lifecycle receipt is invalid"
        )
    manifest = digest(
        {
            "domain": LIFECYCLE_MANIFEST_DOMAIN,
            "action": action,
            "source_owner_subject_id": receipt.source_owner_subject_id,
            "root_source_page_id": receipt.root_source_page_id,
            "result_root_source_page_id": receipt.result_root_source_page_id,
            "source_device": receipt.source_device,
            "source_inode": receipt.source_inode,
            "source_link_count": receipt.source_link_count,
            "source_epoch": receipt.source_epoch,
            "full_sweep_generation": receipt.full_sweep_generation,
            "trash_source_operation_id": receipt.trash_source_operation_id,
            "placement_mode": receipt.placement_mode,
            "file_manifest_sha256": receipt.file_manifest_sha256,
            "effects": [item.descriptor() for item in receipt.effects],
            "mappings": [item.descriptor() for item in receipt.mappings],
        }
    )
    if receipt.post_manifest_sha256 != manifest:
        raise LegacyPageLifecycleContractError(
            "legacy page lifecycle receipt is invalid"
        )
    body = receipt.descriptor(include_receipt=False)
    if receipt.source_operation_receipt_sha256 != digest(
        {"domain": LIFECYCLE_RECEIPT_DOMAIN, "result": body}
    ):
        raise LegacyPageLifecycleContractError(
            "legacy page lifecycle receipt is invalid"
        )
    if len(canonical_json(receipt.descriptor()).encode("utf-8")) > MAX_LIFECYCLE_RESULT_BYTES:
        raise LegacyPageLifecycleContractError(
            "legacy page lifecycle receipt exceeds the adapter bound"
        )
    return receipt


__all__ = [
    "LIFECYCLE_ACTIONS",
    "LIFECYCLE_CONSUMER",
    "LIFECYCLE_FILE_MANIFEST_DOMAIN",
    "LIFECYCLE_MANIFEST_DOMAIN",
    "LIFECYCLE_PRECONDITION_DOMAIN",
    "LIFECYCLE_RECEIPT_DOMAIN",
    "LIFECYCLE_REQUEST_DOMAIN",
    "LIFECYCLE_RESULT_CONTRACTS",
    "LegacyPageLifecycleContractError",
    "LegacyPageLifecycleEffect",
    "LegacyPageLifecycleObjectMapping",
    "LegacyPageLifecycleReceipt",
    "MAX_LIFECYCLE_BLOCKS",
    "MAX_LIFECYCLE_DEPTH",
    "MAX_LIFECYCLE_EFFECTS",
    "MAX_LIFECYCLE_FILES",
    "MAX_LIFECYCLE_FILE_BYTES",
    "MAX_LIFECYCLE_METADATA_BYTES",
    "MAX_LIFECYCLE_PARENT_BLOCKS",
    "MAX_LIFECYCLE_REQUEST_BYTES",
    "MAX_LIFECYCLE_RESULT_BYTES",
    "MAX_LIFECYCLE_SIBLINGS",
    "MAX_LIFECYCLE_SUBTREE",
    "canonical_json",
    "derive_duplicate_object_id",
    "derive_lifecycle_source_operation_id",
    "digest",
    "lifecycle_request_sha256",
    "normalize_action",
    "normalize_name",
    "normalize_object_id",
    "normalize_operation_id",
    "normalize_sha256",
    "validate_lifecycle_receipt",
]
