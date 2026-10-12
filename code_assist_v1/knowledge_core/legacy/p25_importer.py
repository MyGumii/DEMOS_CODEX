"""Bounded P2.5 legacy session and durable-job-history import.

The public service accepts an exact reviewed plan plus the exact physical
source bytes required for one batch.  Strict JSON parsing, session child
reconciliation, job policy classification, and content-addressed blob staging
all happen before SQLite is opened.  The service then owns one transaction for
canonical conversation/history effects, immutable migration checkpoints,
audit, and outbox rows.

Legacy jobs are never inserted into ``durable_job``.  Terminal jobs and
queued/running/unknown jobs are retained as historical raw artifacts; the
latter are explicitly labelled abandoned and can never execute stale code.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
from pathlib import PurePosixPath
from typing import Any, Literal
from uuid import UUID

from ..db import ConnectionFactory, ManagedConnection, transaction
from ..errors import KnowledgeCoreError
from ..repositories.artifacts import (
    ArtifactRepository,
    LegacyArtifactBundle,
    legacy_artifact_ids_for_record,
)
from ..repositories.audit import AuditEventDraft, AuditRepository
from ..repositories.conversations import (
    ConversationRepository,
    LegacyConversation,
    LegacyConversationMessage,
    LegacyConversationSummary,
    legacy_conversation_id,
    legacy_conversation_message_id,
    legacy_conversation_summary_id,
)
from ..repositories.migration import (
    migration_mapping_id_for_record,
    migration_run_id_for_plan,
    migration_source_locator,
)
from ..repositories.migration_p25 import (
    LegacyP25JobDetails,
    LegacyP25JobTarget,
    LegacyP25MappingRepository,
    LegacyP25Record,
    LegacyP25SessionDetails,
    LegacyP25SessionTarget,
    p25_child_bindings_sha256,
)
from ..repositories.outbox import OutboxEventDraft, OutboxRepository
from ..services.spaces import space_id_for_owner
from .content_importer import (
    LegacyBlobStager,
    StagedBlobObservation,
    _assert_blob_root_outside_sources,
    _validate_observation,
)
from .importer import _derived_uuid, _mapping, _reviewed_plan, _utc_ms
from .plan_contract import sha256_canonical


MAX_P25_EFFECTS_PER_TRANSACTION = 100
MAX_P25_SOURCE_BYTES = 256 * 1024 * 1024

_REQUEST_NAMESPACE = UUID("45ffecae-b453-4dc6-8a3f-d047709f76c2")
_OPERATION_NAMESPACE = UUID("f9766239-24a6-4973-ae0b-5406984018f7")
_RECORD_AUDIT_NAMESPACE = UUID("03062e00-d5fb-4b68-b08b-c8f2eed17f14")
_JOB_AUDIT_NAMESPACE = UUID("9b18846f-b539-48a1-97fc-14e254c64b15")

_SESSION_CATEGORIES = frozenset(
    {"conversation.user_sessions", "conversation.global_sessions"}
)
_CHILD_KIND_ORDER = (
    "session_core_metadata",
    "messages",
    "metadata",
    "summaries",
    "uploads",
    "bindings",
)
_JOB_STATUS = frozenset(
    {"cancelled", "failed", "queued", "running", "succeeded", "unknown"}
)


class LegacyP25ImportError(KnowledgeCoreError):
    code = "LEGACY_P25_IMPORT_FAILED"


class LegacyP25PreparationError(LegacyP25ImportError):
    code = "LEGACY_P25_PLAN_INVALID"


class LegacyP25BatchLimitError(LegacyP25PreparationError):
    code = "LEGACY_P25_BATCH_LIMIT"


class LegacyP25StateConflictError(LegacyP25ImportError):
    code = "LEGACY_P25_STATE_CONFLICT"


@dataclass(frozen=True, slots=True)
class _OwnerAssertion:
    user_id: str
    system_role: str
    account_status: str
    part_id: str | None
    target_space_id: str


@dataclass(frozen=True, slots=True)
class _PreparedSessionChild:
    plan_row: Mapping[str, object]
    source: Mapping[str, object]
    payload: object
    child_kind: str
    payload_sha256: str
    source_ordinal: int

    @property
    def record_id(self) -> str:
        return str(self.plan_row["record_id"])


@dataclass(frozen=True, slots=True)
class _PreparedSession:
    parent_row: Mapping[str, object]
    parent_source: Mapping[str, object]
    raw: bytes
    owner: _OwnerAssertion
    path_key: str
    conversation: LegacyConversation
    session_id_sha256: str
    all_children: tuple[_PreparedSessionChild, ...]
    selected_children: tuple[_PreparedSessionChild, ...]
    close_parent: bool

    @property
    def parent_record_id(self) -> str:
        return str(self.parent_row["record_id"])


@dataclass(frozen=True, slots=True)
class _PreparedJob:
    plan_row: Mapping[str, object]
    source: Mapping[str, object]
    raw: bytes
    owner: _OwnerAssertion
    path_key: str
    source_status: str
    import_mode: Literal["job_history", "abandoned_legacy_job"]
    result_disposition: Literal[
        "terminal_history_recorded", "abandoned_historical"
    ]
    job_identity_sha256: str
    evidence_json: str
    observation: StagedBlobObservation
    artifact_bundle: LegacyArtifactBundle

    @property
    def record_id(self) -> str:
        return str(self.plan_row["record_id"])


@dataclass(frozen=True, slots=True)
class _PreparedBatch:
    plan_sha256: str
    migration_run_id: str
    inventory_manifest_sha256: str
    root_binding_sha256: str
    reviewer_id: str
    effective_at_ms: int
    request_id: str
    operation_id: str
    sessions: tuple[_PreparedSession, ...]
    jobs: tuple[_PreparedJob, ...]


@dataclass(frozen=True, slots=True)
class LegacyP25RecordOutcome:
    record_id: str
    mapping_id: str
    import_mode: str
    result_disposition: str
    conversation_id: str | None
    artifact_id: str | None
    mapping_inserted: bool


@dataclass(frozen=True, slots=True)
class LegacyP25ImportResult:
    plan_sha256: str
    migration_run_id: str
    request_id: str
    operation_id: str
    records: tuple[LegacyP25RecordOutcome, ...]

    @property
    def inserted_mapping_count(self) -> int:
        return sum(record.mapping_inserted for record in self.records)


def _canonical_object(value: Mapping[str, object]) -> str:
    try:
        encoded = json.dumps(
            dict(value),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (RecursionError, TypeError, ValueError):
        raise LegacyP25PreparationError("P2.5 evidence is not canonical JSON.") from None
    if not isinstance(json.loads(encoded), dict):
        raise LegacyP25PreparationError("P2.5 evidence must be a JSON object.")
    return encoded


def _record_id(value: object, field: str = "record_id") -> str:
    if (
        not isinstance(value, str)
        or len(value) != 28
        or not value.startswith("rec_")
        or any(character not in "0123456789abcdef" for character in value[4:])
    ):
        raise LegacyP25PreparationError(f"{field} is invalid.")
    return value


def _selected(values: Sequence[str]) -> tuple[str, ...]:
    if isinstance(values, (str, bytes, bytearray)):
        raise TypeError("selected_record_ids must be a sequence.")
    prepared = tuple(_record_id(value) for value in values)
    if prepared != tuple(sorted(set(prepared))):
        raise LegacyP25PreparationError(
            "Selected P2.5 record IDs must be sorted and unique."
        )
    if not 1 <= len(prepared) <= MAX_P25_EFFECTS_PER_TRANSACTION:
        raise LegacyP25BatchLimitError(
            "A P2.5 batch must contain 1..100 reviewed effects."
        )
    return prepared


def _source(row: Mapping[str, object]) -> Mapping[str, object]:
    value = row.get("source_record")
    if not isinstance(value, Mapping):
        raise LegacyP25PreparationError("Reviewed P2.5 source record is invalid.")
    return value


def _reject_duplicate_members(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_member")
        result[key] = value
    return result


def _reject_nonfinite(value: str) -> None:
    raise ValueError(f"non_finite_json_number:{value}")


def _strict_json_object(raw: bytes, *, label: str) -> dict[str, object]:
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_members,
            parse_constant=_reject_nonfinite,
        )
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        RecursionError,
        ValueError,
    ):
        raise LegacyP25PreparationError(f"{label} is not strict UTF-8 JSON.") from None
    if not isinstance(value, dict):
        raise LegacyP25PreparationError(f"{label} must be a JSON object.")
    return value


def _physical_bytes(
    source: Mapping[str, object],
    raw: bytes,
    *,
    label: str,
) -> None:
    expected_sha = source.get("sha256")
    expected_size = source.get("size_bytes")
    if (
        source.get("record_kind") != "physical_file"
        or source.get("source_ordinal") != 0
        or not isinstance(expected_sha, str)
        or hashlib.sha256(raw).hexdigest() != expected_sha
        or isinstance(expected_size, bool)
        or not isinstance(expected_size, int)
        or expected_size != len(raw)
    ):
        raise LegacyP25PreparationError(
            f"{label} bytes differ from the reviewed physical source."
        )


def _owner_assertion(
    row: Mapping[str, object],
    owner_mappings: Mapping[str, Mapping[str, object]],
) -> _OwnerAssertion:
    owner_id = row.get("resolved_owner_user_id")
    mapping_id = row.get("owner_mapping_id")
    owner_mapping = owner_mappings.get(str(mapping_id))
    if (
        row.get("target_space_kind") != "personal"
        or not isinstance(owner_id, str)
        or not isinstance(owner_mapping, Mapping)
        or owner_mapping.get("mapping_id") != mapping_id
        or owner_mapping.get("decision") != "personal"
        or owner_mapping.get("user_id") != owner_id
        or owner_mapping.get("system_role") not in {"admin", "user"}
        or owner_mapping.get("account_status") not in {"active", "disabled"}
    ):
        raise LegacyP25PreparationError(
            "A P2.5 effect lacks an exact reviewed personal owner mapping."
        )
    part_id = owner_mapping.get("part_id")
    if part_id is not None and not isinstance(part_id, str):
        raise LegacyP25PreparationError("Reviewed owner part binding is invalid.")
    return _OwnerAssertion(
        user_id=owner_id,
        system_role=str(owner_mapping["system_role"]),
        account_status=str(owner_mapping["account_status"]),
        part_id=part_id,
        target_space_id=space_id_for_owner("personal", owner_id),
    )


def _same_owner(left: _OwnerAssertion, right: _OwnerAssertion) -> bool:
    return left == right


def _session_payloads(data: Mapping[str, object]) -> tuple[
    str, dict[str, list[object]], dict[str, object]
]:
    messages = data.get("messages", [])
    uploaded_files = data.get("uploaded_files", data.get("uploads", []))
    skills_used = data.get("skills_used", [])
    metadata = data.get("metadata", {})
    if (
        not isinstance(messages, list)
        or not isinstance(uploaded_files, list)
        or not isinstance(skills_used, list)
        or not isinstance(metadata, dict)
        or any(
            not isinstance(message, dict)
            or message.get("role") not in {"system", "user", "assistant", "tool"}
            for message in messages
        )
        or any(not isinstance(upload, dict) for upload in uploaded_files)
        or any(not isinstance(skill, (str, dict)) for skill in skills_used)
    ):
        raise LegacyP25PreparationError("Legacy session collections are invalid.")
    session_id = str(data.get("session_id") or data.get("id") or "").strip()
    aliases = [
        str(data[key]).strip()
        for key in ("session_id", "id")
        if data.get(key) not in (None, "")
    ]
    if not session_id or len({value.casefold() for value in aliases}) > 1:
        raise LegacyP25PreparationError("Legacy session identity aliases conflict.")
    if "uploaded_files" in data and "uploads" in data and (
        data.get("uploaded_files") != data.get("uploads")
    ):
        raise LegacyP25PreparationError("Legacy session upload aliases conflict.")
    summary_aliases = [
        value
        for value in (
            metadata.get("compact_summary"),
            data.get("compact_summary"),
            data.get("summary"),
        )
        if value not in (None, "")
    ]
    if len({sha256_canonical(value) for value in summary_aliases}) > 1:
        raise LegacyP25PreparationError("Legacy session summary aliases conflict.")
    declared = data.get("message_count")
    if declared is not None and (
        isinstance(declared, bool)
        or not isinstance(declared, int)
        or declared != len(messages)
    ):
        raise LegacyP25PreparationError("Legacy session message count differs.")
    binding_keys = (
        "knowledge_files",
        "knowledge_space_id",
        "memory_ids",
        "notion_page_ids",
        "notion_pages",
        "pinned_thoughts",
        "pinned_wiki_pages",
        "project_context_ids",
        "project_id",
        "space_id",
    )
    top_bindings = data.get("bindings", [])
    if top_bindings is not None and not isinstance(top_bindings, list):
        raise LegacyP25PreparationError("Legacy session bindings are invalid.")
    bindings: list[object] = list(top_bindings or [])
    for key in binding_keys:
        metadata_value = metadata.get(key)
        top_value = data.get(key)
        if (
            metadata_value not in (None, "", [])
            and top_value not in (None, "", [])
            and metadata_value != top_value
        ):
            raise LegacyP25PreparationError(
                "Legacy session binding aliases conflict."
            )
        value = metadata_value if metadata_value is not None else top_value
        if isinstance(value, list):
            bindings.extend(value)
        elif isinstance(value, (str, int)) and str(value).strip():
            bindings.append(value)
    summary = (
        metadata.get("compact_summary")
        or data.get("compact_summary")
        or data.get("summary")
    )
    collection_keys = {
        "bindings",
        "compact_summary",
        "messages",
        "metadata",
        "skills_used",
        "summary",
        "uploaded_files",
        "uploads",
        *binding_keys,
    }
    core = {key: data[key] for key in sorted(data) if key not in collection_keys}
    payloads: dict[str, list[object]] = {
        "session_core_metadata": [core],
        "messages": list(messages),
        "metadata": (
            [{"metadata": metadata, "skills_used": skills_used}]
            if metadata or skills_used
            else []
        ),
        "summaries": [summary] if summary else [],
        "uploads": list(uploaded_files),
        "bindings": bindings,
    }
    return session_id, payloads, core


def _timestamp_ms(value: object, fallback: int, offset: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        return fallback + offset
    observed = int(value * 1000) if value < 10_000_000_000 else int(value)
    if not 0 <= observed <= 9_223_372_036_854_775_807:
        return fallback + offset
    return observed


def _prepare_session(
    *,
    parent_row: Mapping[str, object],
    all_records: Sequence[Mapping[str, object]],
    selected_ids: set[str],
    raw: bytes,
    owner_mappings: Mapping[str, Mapping[str, object]],
    plan_sha256: str,
    effective_at_ms: int,
) -> _PreparedSession:
    parent_id = _record_id(parent_row.get("record_id"), "parent_record_id")
    parent_source = _source(parent_row)
    category = parent_source.get("category")
    if (
        parent_row.get("action") != "import"
        or parent_row.get("execution_phase") != "P2.5"
        or parent_row.get("import_mode") != "source_container"
        or category not in _SESSION_CATEGORIES
        or parent_source.get("record_kind") != "physical_file"
        or parent_row.get("inventory_record_sha256")
        != sha256_canonical(parent_source)
    ):
        raise LegacyP25PreparationError(
            "The reviewed session parent is not an imported P2.5 container."
        )
    _physical_bytes(parent_source, raw, label="Legacy session")
    data = _strict_json_object(raw, label="Legacy session")
    session_id, payloads, core = _session_payloads(data)
    relative = parent_source.get("relative_path")
    if not isinstance(relative, str) or PurePosixPath(relative).stem != session_id:
        raise LegacyP25PreparationError(
            "Legacy session ID differs from its reviewed filename."
        )
    parse = parent_source.get("parse")
    session_id_sha = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
    if (
        not isinstance(parse, Mapping)
        or parse.get("status") != "valid"
        or parse.get("session_id_sha256") != session_id_sha
    ):
        raise LegacyP25PreparationError(
            "Reviewed session parse evidence is invalid."
        )
    owner = _owner_assertion(parent_row, owner_mappings)
    plan_children = tuple(
        sorted(
            (
                row
                for row in all_records
                if row.get("action") == "import"
                and row.get("execution_phase") == "P2.5"
                and row.get("import_mode") == "conversation_child"
                and _source(row).get("parent_record_id") == parent_id
            ),
            key=lambda row: int(_source(row).get("source_ordinal", 0)),
        )
    )
    flattened: list[tuple[int, str, object, str]] = []
    ordinal = 0
    aggregates: dict[str, str] = {}
    counts: dict[str, int] = {}
    for kind in _CHILD_KIND_ORDER:
        hashes: list[str] = []
        counts[kind] = len(payloads[kind])
        for payload in payloads[kind]:
            ordinal += 1
            payload_sha = sha256_canonical(payload)
            hashes.append(payload_sha)
            flattened.append((ordinal, kind, payload, payload_sha))
        aggregates[kind] = sha256_canonical(hashes)
    if (
        parse.get("child_count") != len(flattened)
        or parse.get("record_count") != len(flattened)
        or parse.get("child_counts") != counts
        or parse.get("child_aggregate_sha256") != aggregates
        or parse.get("logical_sha256") != sha256_canonical(aggregates)
        or len(plan_children) != len(flattened)
    ):
        raise LegacyP25PreparationError(
            "Legacy session child aggregates differ from the reviewed plan."
        )
    prepared_children: list[_PreparedSessionChild] = []
    evidence: list[dict[str, object]] = []
    for row, (child_ordinal, kind, payload, payload_sha) in zip(
        plan_children, flattened, strict=True
    ):
        source = _source(row)
        record_id = _record_id(row.get("record_id"))
        child_owner = _owner_assertion(row, owner_mappings)
        if (
            not _same_owner(owner, child_owner)
            or row.get("inventory_record_sha256") != sha256_canonical(source)
            or source.get("record_kind") != "logical_record"
            or source.get("category") != category
            or source.get("source_ordinal") != child_ordinal
            or source.get("child_kind") != kind
            or source.get("payload_sha256") != payload_sha
            or source.get("path_key") != parent_source.get("path_key")
            or source.get("root_id") != parent_source.get("root_id")
            or not isinstance(source.get("sha256"), str)
        ):
            raise LegacyP25PreparationError(
                "A legacy session child differs from its reviewed record."
            )
        prepared_children.append(
            _PreparedSessionChild(
                plan_row=row,
                source=source,
                payload=payload,
                child_kind=kind,
                payload_sha256=payload_sha,
                source_ordinal=child_ordinal,
            )
        )
        evidence.append(
            {
                "child_kind": kind,
                "inventory_record_id": record_id,
                "payload_sha256": payload_sha,
                "source_ordinal": child_ordinal,
            }
        )
    metadata_json = _canonical_object(
        {
            "legacy_p2_5": {
                "bindings": payloads["bindings"],
                "child_evidence": evidence,
                "inventory_record_id": parent_id,
                "metadata": payloads["metadata"],
                "plan_sha256": plan_sha256,
                "schema_version": 1,
                "session_core_metadata": payloads["session_core_metadata"],
                "session_id_sha256": session_id_sha,
                "summaries": payloads["summaries"],
                "uploads": payloads["uploads"],
            },
            "schema_version": 1,
        }
    )
    raw_title = core.get("title", "")
    title = raw_title[:200] if isinstance(raw_title, str) else ""
    created = _timestamp_ms(core.get("timestamp"), effective_at_ms)
    conversation_id = legacy_conversation_id(plan_sha256, parent_id)
    conversation = LegacyConversation(
        plan_sha256=plan_sha256,
        inventory_record_id=parent_id,
        conversation_id=conversation_id,
        owner_user_id=owner.user_id,
        personal_space_id=owner.target_space_id,
        captured_scope_epoch=1,
        title=title,
        metadata_json=metadata_json,
        created_at_ms=created,
    )
    selected_children = tuple(
        child for child in prepared_children if child.record_id in selected_ids
    )
    return _PreparedSession(
        parent_row=parent_row,
        parent_source=parent_source,
        raw=raw,
        owner=owner,
        path_key=str(parent_source["path_key"]),
        conversation=conversation,
        session_id_sha256=session_id_sha,
        all_children=tuple(prepared_children),
        selected_children=selected_children,
        close_parent=parent_id in selected_ids,
    )


def _prepare_job(
    *,
    row: Mapping[str, object],
    raw: bytes,
    owner_mappings: Mapping[str, Mapping[str, object]],
    plan_sha256: str,
    effective_at_ms: int,
    stager: LegacyBlobStager,
) -> _PreparedJob:
    record_id = _record_id(row.get("record_id"))
    source = _source(row)
    if (
        row.get("action") != "import"
        or row.get("execution_phase") != "P2.5"
        or row.get("import_mode") not in {"job_history", "abandoned_legacy_job"}
        or source.get("category") != "operations.jobs"
        or source.get("record_kind") != "physical_file"
        or row.get("inventory_record_sha256") != sha256_canonical(source)
    ):
        raise LegacyP25PreparationError(
            "The reviewed job is not a supported P2.5 historical effect."
        )
    _physical_bytes(source, raw, label="Legacy job")
    data = _strict_json_object(raw, label="Legacy job")
    parse = source.get("parse")
    identity = parse.get("job_identity") if isinstance(parse, Mapping) else None
    source_status = parse.get("source_status") if isinstance(parse, Mapping) else None
    if (
        not isinstance(identity, Mapping)
        or source_status not in _JOB_STATUS
        or identity.get("payload_sha256") != sha256_canonical(data)
    ):
        raise LegacyP25PreparationError("Reviewed legacy job identity is invalid.")
    mode = (
        "abandoned_legacy_job"
        if source_status in {"queued", "running", "unknown"}
        else "job_history"
    )
    if row.get("import_mode") != mode:
        raise LegacyP25PreparationError("Reviewed legacy job policy differs.")
    disposition = (
        "abandoned_historical"
        if mode == "abandoned_legacy_job"
        else "terminal_history_recorded"
    )
    owner = _owner_assertion(row, owner_mappings)
    path_key = source.get("path_key")
    source_sha = source.get("sha256")
    if not isinstance(path_key, str) or not isinstance(source_sha, str):
        raise LegacyP25PreparationError("Reviewed legacy job source is invalid.")
    observation = _validate_observation(
        stager.stage_and_verify(
            raw,
            expected_sha256=source_sha,
            media_type="application/json",
            effective_at_ms=effective_at_ms,
            normalized_content_sha256=None,
            normalizer_version=None,
        ),
        expected_sha256=source_sha,
        expected_size=len(raw),
        media_type="application/json",
        normalized_sha256=None,
        normalizer_version=None,
    )
    identity_sha = sha256_canonical(identity)
    evidence = {
        "authority": "historical_only",
        "category": "operations.jobs",
        "execution_policy": "never_replay",
        "inventory_record_id": record_id,
        "job_identity_sha256": identity_sha,
        "media_type": "application/json",
        "raw_sha256": source_sha,
        "schema_version": 1,
        "size_bytes": len(raw),
        "source_status": source_status,
    }
    locator = migration_source_locator(
        root_id=str(source["root_id"]),
        path_key=path_key,
        record_kind="physical_file",
        source_ordinal=0,
    )
    ids = legacy_artifact_ids_for_record(plan_sha256, record_id)
    name = PurePosixPath(str(source.get("relative_path"))).name
    artifact = LegacyArtifactBundle(
        plan_sha256=plan_sha256,
        inventory_record_id=record_id,
        ids=ids,
        owner_user_id=owner.user_id,
        origin_key=locator,
        raw_blob=observation.blob,
        logical_name=name,
        original_filename=name,
        source_owner_snapshot_json=_canonical_object(
            {"owner": source.get("owner"), "schema_version": 1}
        ),
        metadata_json=_canonical_object(
            {
                "authority": "historical_only",
                "category": "operations.jobs",
                "execution_policy": "never_replay",
                "inventory_record_id": record_id,
                "schema_version": 1,
            }
        ),
        created_at_ms=effective_at_ms,
        extraction=None,
    )
    return _PreparedJob(
        plan_row=row,
        source=source,
        raw=raw,
        owner=owner,
        path_key=path_key,
        source_status=str(source_status),
        import_mode=mode,
        result_disposition=disposition,
        job_identity_sha256=identity_sha,
        evidence_json=_canonical_object(evidence),
        observation=observation,
        artifact_bundle=artifact,
    )


def _prepare_batch(
    reviewed_plan: bytes | Mapping[str, object],
    source_payloads: Mapping[str, bytes],
    *,
    selected_record_ids: Sequence[str],
    expected_plan_sha256: str,
    stager: LegacyBlobStager,
) -> _PreparedBatch:
    selected_ids = _selected(selected_record_ids)
    plan = _reviewed_plan(reviewed_plan, expected_plan_sha256=expected_plan_sha256)
    draft = _mapping(plan.get("draft"), field="plan draft")
    _assert_blob_root_outside_sources(stager, draft)
    records_value = draft.get("records")
    mappings_value = draft.get("owner_mappings")
    if not isinstance(records_value, list) or not all(
        isinstance(row, Mapping) for row in records_value
    ):
        raise LegacyP25PreparationError("Reviewed plan records are invalid.")
    if not isinstance(mappings_value, list) or not all(
        isinstance(row, Mapping) for row in mappings_value
    ):
        raise LegacyP25PreparationError("Reviewed owner mappings are invalid.")
    records: tuple[Mapping[str, object], ...] = tuple(records_value)  # type: ignore[assignment]
    records_by_id = {str(row.get("record_id")): row for row in records}
    if len(records_by_id) != len(records) or any(value not in records_by_id for value in selected_ids):
        raise LegacyP25PreparationError("Selected P2.5 records are absent or duplicated.")
    owner_mappings = {
        str(row.get("mapping_id")): row for row in mappings_value  # type: ignore[union-attr]
    }
    selected_rows = tuple(records_by_id[value] for value in selected_ids)
    required_physical_ids: set[str] = set()
    session_parent_ids: set[str] = set()
    for row in selected_rows:
        source = _source(row)
        category = source.get("category")
        if category in _SESSION_CATEGORIES:
            parent_id = (
                str(row["record_id"])
                if source.get("record_kind") == "physical_file"
                else _record_id(source.get("parent_record_id"), "parent_record_id")
            )
            session_parent_ids.add(parent_id)
            required_physical_ids.add(parent_id)
        elif category == "operations.jobs" and source.get("record_kind") == "physical_file":
            required_physical_ids.add(str(row["record_id"]))
        else:
            raise LegacyP25PreparationError(
                "A selected record is not a supported session/job P2.5 effect."
            )
    if not isinstance(source_payloads, Mapping) or set(source_payloads) != required_physical_ids:
        raise LegacyP25PreparationError(
            "P2.5 source payloads must cover exact physical dependencies."
        )
    total = 0
    for record_id, raw in source_payloads.items():
        if not isinstance(raw, bytes):
            raise TypeError(f"Source payload {record_id} must be bytes.")
        total += len(raw)
    if total > MAX_P25_SOURCE_BYTES:
        raise LegacyP25BatchLimitError("A P2.5 batch exceeds the source-byte limit.")
    source_inventory = _mapping(draft.get("source_inventory"), field="source inventory")
    metadata = _mapping(source_inventory.get("metadata"), field="source metadata")
    run = _mapping(metadata.get("run"), field="inventory run")
    effective_at = _utc_ms(run.get("started_at_utc"))
    inputs = _mapping(draft.get("inputs"), field="plan inputs")
    inventory = _mapping(inputs.get("inventory"), field="inventory binding")
    review = _mapping(plan.get("review"), field="review")
    sessions: list[_PreparedSession] = []
    for parent_id in sorted(session_parent_ids):
        parent_row = records_by_id.get(parent_id)
        if parent_row is None:
            raise LegacyP25PreparationError("A session physical parent is absent.")
        sessions.append(
            _prepare_session(
                parent_row=parent_row,
                all_records=records,
                selected_ids=set(selected_ids),
                raw=source_payloads[parent_id],
                owner_mappings=owner_mappings,
                plan_sha256=expected_plan_sha256,
                effective_at_ms=effective_at,
            )
        )
    jobs: list[_PreparedJob] = []
    opened: list[StagedBlobObservation] = []
    try:
        for row in selected_rows:
            source = _source(row)
            if source.get("category") != "operations.jobs":
                continue
            prepared = _prepare_job(
                row=row,
                raw=source_payloads[str(row["record_id"])],
                owner_mappings=owner_mappings,
                plan_sha256=expected_plan_sha256,
                effective_at_ms=effective_at,
                stager=stager,
            )
            jobs.append(prepared)
            opened.append(prepared.observation)
    except Exception:
        for observation in reversed(opened):
            observation.lease.close()
        raise
    return _PreparedBatch(
        plan_sha256=expected_plan_sha256,
        migration_run_id=migration_run_id_for_plan(expected_plan_sha256),
        inventory_manifest_sha256=str(inventory["manifest_sha256"]),
        root_binding_sha256=str(inventory["root_binding_sha256"]),
        reviewer_id=str(review["reviewer_id"]),
        effective_at_ms=effective_at,
        request_id=_derived_uuid(_REQUEST_NAMESPACE, expected_plan_sha256),
        operation_id=_derived_uuid(_OPERATION_NAMESPACE, expected_plan_sha256),
        sessions=tuple(sessions),
        jobs=tuple(jobs),
    )


class LegacyP25Importer:
    """Apply one exact session/job batch in a service-owned transaction."""

    __slots__ = (
        "_artifacts",
        "_audit",
        "_conversations",
        "_factory",
        "_failpoint",
        "_mapping",
        "_outbox",
        "_stager",
    )

    def __init__(
        self,
        factory: ConnectionFactory,
        stager: LegacyBlobStager,
        *,
        failpoint: Callable[[str], None] | None = None,
    ) -> None:
        if not isinstance(factory, ConnectionFactory):
            raise TypeError("LegacyP25Importer requires a ConnectionFactory.")
        if not callable(getattr(stager, "stage_and_verify", None)):
            raise TypeError("A trusted legacy blob stager is required.")
        self._factory = factory
        self._stager = stager
        self._artifacts = ArtifactRepository()
        self._audit = AuditRepository()
        self._conversations = ConversationRepository()
        self._mapping = LegacyP25MappingRepository()
        self._outbox = OutboxRepository()
        self._failpoint = failpoint

    def _hit(self, name: str) -> None:
        if self._failpoint is not None:
            self._failpoint(name)

    @staticmethod
    def _assert_owner(
        connection: ManagedConnection,
        owner: _OwnerAssertion,
    ) -> int:
        user = connection.execute(
            """SELECT system_role,account_status,current_part_id,scope_epoch
               FROM app_user WHERE id=?""",
            (owner.user_id,),
        ).fetchone()
        space = connection.execute(
            """SELECT space_kind,owner_user_id,status
               FROM knowledge_space WHERE id=?""",
            (owner.target_space_id,),
        ).fetchone()
        memberships = connection.execute(
            """SELECT part_id FROM org_membership
               WHERE user_id=? AND valid_to IS NULL""",
            (owner.user_id,),
        ).fetchall()
        if (
            user is None
            or space is None
            or user["system_role"] != owner.system_role
            or user["account_status"] != owner.account_status
            or user["current_part_id"] != owner.part_id
            or space["space_kind"] != "personal"
            or space["owner_user_id"] != owner.user_id
            or space["status"] != "active"
            or (
                owner.part_id is None
                and memberships
            )
            or (
                owner.part_id is not None
                and (
                    len(memberships) != 1
                    or memberships[0]["part_id"] != owner.part_id
                )
            )
        ):
            raise LegacyP25StateConflictError(
                "The reviewed P2.5 owner, membership, or personal space drifted."
            )
        return int(user["scope_epoch"])

    def _record_events(
        self,
        connection: ManagedConnection,
        *,
        batch: _PreparedBatch,
        record: LegacyP25Record,
        owner: _OwnerAssertion,
        import_mode: str,
        result_disposition: str,
    ) -> None:
        details = {
            "import_mode": import_mode,
            "inventory_record_id": json.loads(record.details_json)[
                "inventory_record_id"
            ],
            "mapping_id": record.id,
            "migration_run_id": record.migration_run_id,
            "plan_sha256": batch.plan_sha256,
            "result_disposition": result_disposition,
            "schema_version": 1,
        }
        self._audit.append(
            connection,
            AuditEventDraft(
                event_uuid=_derived_uuid(_RECORD_AUDIT_NAMESPACE, record.id),
                occurred_at_ms=batch.effective_at_ms,
                request_id=batch.request_id,
                operation_id=batch.operation_id,
                action=f"legacy_migration.{import_mode}",
                outcome=result_disposition,
                actor_kind="system",
                actor_user_id=None,
                actor_display_name="P2.5 legacy migration",
                actor_role_snapshot="system",
                space_id=owner.target_space_id,
                reason="reviewed P2.5 historical import",
                details=details,
                request_meta={"reviewer_id": batch.reviewer_id},
            ),
        )
        self._outbox.append(
            connection,
            OutboxEventDraft(
                logical_key=f"legacy-migration-p25:{record.id}:imported:v1",
                space_id=owner.target_space_id,
                aggregate_type="legacy_migration_map",
                aggregate_id=record.id,
                aggregate_version=1,
                event_type=f"legacy_migration.{import_mode}.imported",
                payload={
                    **details,
                    "operation_id": batch.operation_id,
                    "resolved_owner_user_id": owner.user_id,
                },
                available_at_ms=batch.effective_at_ms,
            ),
        )

    def apply_batch(
        self,
        reviewed_plan: bytes | Mapping[str, object],
        source_payloads: Mapping[str, bytes],
        *,
        selected_record_ids: Sequence[str],
        expected_plan_sha256: str,
    ) -> LegacyP25ImportResult:
        batch = _prepare_batch(
            reviewed_plan,
            source_payloads,
            selected_record_ids=selected_record_ids,
            expected_plan_sha256=expected_plan_sha256,
            stager=self._stager,
        )
        connection: ManagedConnection | None = None
        try:
            connection = self._factory.open()
            with transaction(connection):
                return self._apply(connection, batch)
        finally:
            if connection is not None:
                connection.close()
            for job in reversed(batch.jobs):
                job.observation.lease.close()

    def _apply_session_child(
        self,
        connection: ManagedConnection,
        batch: _PreparedBatch,
        session: _PreparedSession,
        child: _PreparedSessionChild,
    ) -> LegacyP25RecordOutcome:
        source = child.source
        message_id = None
        summary_id = None
        if child.child_kind == "messages":
            if not isinstance(child.payload, Mapping):
                raise LegacyP25StateConflictError(
                    "Prepared legacy message is not an object."
                )
            message_id = legacy_conversation_message_id(
                batch.plan_sha256, child.record_id
            )
            self._conversations.put_legacy_message(
                connection,
                LegacyConversationMessage(
                    plan_sha256=batch.plan_sha256,
                    inventory_record_id=child.record_id,
                    message_id=message_id,
                    conversation_id=session.conversation.conversation_id,
                    owner_user_id=session.owner.user_id,
                    personal_space_id=session.owner.target_space_id,
                    sequence_no=sum(
                        1
                        for row in session.all_children
                        if row.child_kind == "messages"
                        and row.source_ordinal <= child.source_ordinal
                    ),
                    role=str(child.payload["role"]),  # type: ignore[arg-type]
                    content_json=_canonical_object(child.payload),
                    created_at_ms=_timestamp_ms(
                        child.payload.get("timestamp")
                        or child.payload.get("created_at"),
                        batch.effective_at_ms,
                        child.source_ordinal,
                    ),
                ),
            )
            disposition = "message_created"
            sequence_no = sum(
                1
                for row in session.all_children
                if row.child_kind == "messages"
                and row.source_ordinal <= child.source_ordinal
            )
        elif child.child_kind == "summaries":
            message_count = sum(
                row.child_kind == "messages" for row in session.all_children
            )
            sequence_no = None
            if message_count:
                summary_id = legacy_conversation_summary_id(
                    batch.plan_sha256, child.record_id
                )
                summary_md = (
                    child.payload
                    if isinstance(child.payload, str)
                    else json.dumps(
                        child.payload,
                        ensure_ascii=False,
                        allow_nan=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                )
                self._conversations.put_legacy_summary(
                    connection,
                    LegacyConversationSummary(
                        plan_sha256=batch.plan_sha256,
                        inventory_record_id=child.record_id,
                        summary_id=summary_id,
                        conversation_id=session.conversation.conversation_id,
                        owner_user_id=session.owner.user_id,
                        personal_space_id=session.owner.target_space_id,
                        covered_through_sequence_no=message_count,
                        summary_md=summary_md,
                        summary_sha256=hashlib.sha256(
                            summary_md.encode("utf-8")
                        ).hexdigest(),
                        created_at_ms=batch.effective_at_ms + child.source_ordinal,
                    ),
                )
                disposition = "summary_created"
            else:
                disposition = "preserved_in_metadata"
        else:
            disposition = "preserved_in_metadata"
            sequence_no = None
        details = LegacyP25SessionDetails(
            plan_sha256=batch.plan_sha256,
            inventory_manifest_sha256=batch.inventory_manifest_sha256,
            root_binding_sha256=batch.root_binding_sha256,
            inventory_record_id=child.record_id,
            inventory_record_sha256=str(child.plan_row["inventory_record_sha256"]),
            parent_inventory_record_id=session.parent_record_id,
            root_id=str(source["root_id"]),
            path_key_sha256=hashlib.sha256(
                session.path_key.encode("utf-8")
            ).hexdigest(),
            category=str(source["category"]),
            record_kind="logical_record",
            source_ordinal=child.source_ordinal,
            import_mode="conversation_child",
            result_disposition=disposition,
            target_space_id=session.owner.target_space_id,
            source_record_sha256=str(source["sha256"]),
            conversation_id=session.conversation.conversation_id,
            session_id_sha256=session.session_id_sha256,
            child_kind=child.child_kind,  # type: ignore[arg-type]
            payload_sha256=child.payload_sha256,
            message_sequence_no=sequence_no,
            planned_import_child_record_ids=(),
            imported_child_bindings_sha256=p25_child_bindings_sha256(()),
        )
        self._hit("migration.before_checkpoint")
        mapped, inserted = self._mapping.put_session_imported(
            connection,
            target=LegacyP25SessionTarget(
                resolved_owner_user_id=session.owner.user_id,
                conversation_id=session.conversation.conversation_id,
                message_id=message_id,
                summary_id=summary_id,
                details=details,
                source_path_key=session.path_key,
            ),
            now_ms=batch.effective_at_ms,
        )
        self._record_events(
            connection,
            batch=batch,
            record=mapped,
            owner=session.owner,
            import_mode="conversation_child",
            result_disposition=disposition,
        )
        return LegacyP25RecordOutcome(
            child.record_id,
            mapped.id,
            "conversation_child",
            disposition,
            session.conversation.conversation_id,
            None,
            inserted,
        )

    def _apply_session_parent(
        self,
        connection: ManagedConnection,
        batch: _PreparedBatch,
        session: _PreparedSession,
    ) -> LegacyP25RecordOutcome:
        source = session.parent_source
        bindings = [
            (
                migration_mapping_id_for_record(batch.plan_sha256, child.record_id),
                child.record_id,
                str(child.source["sha256"]),
            )
            for child in session.all_children
        ]
        details = LegacyP25SessionDetails(
            plan_sha256=batch.plan_sha256,
            inventory_manifest_sha256=batch.inventory_manifest_sha256,
            root_binding_sha256=batch.root_binding_sha256,
            inventory_record_id=session.parent_record_id,
            inventory_record_sha256=str(
                session.parent_row["inventory_record_sha256"]
            ),
            parent_inventory_record_id=None,
            root_id=str(source["root_id"]),
            path_key_sha256=hashlib.sha256(
                session.path_key.encode("utf-8")
            ).hexdigest(),
            category=str(source["category"]),
            record_kind="physical_file",
            source_ordinal=0,
            import_mode="source_container",
            result_disposition="dependency_closed",
            target_space_id=session.owner.target_space_id,
            source_record_sha256=str(source["sha256"]),
            conversation_id=session.conversation.conversation_id,
            session_id_sha256=session.session_id_sha256,
            child_kind=None,
            payload_sha256=None,
            message_sequence_no=None,
            planned_import_child_record_ids=tuple(
                sorted(child.record_id for child in session.all_children)
            ),
            imported_child_bindings_sha256=p25_child_bindings_sha256(bindings),
        )
        self._hit("migration.before_checkpoint")
        mapped, inserted = self._mapping.put_session_imported(
            connection,
            target=LegacyP25SessionTarget(
                resolved_owner_user_id=session.owner.user_id,
                conversation_id=session.conversation.conversation_id,
                message_id=None,
                summary_id=None,
                details=details,
                source_path_key=session.path_key,
            ),
            now_ms=batch.effective_at_ms,
        )
        self._record_events(
            connection,
            batch=batch,
            record=mapped,
            owner=session.owner,
            import_mode="source_container",
            result_disposition="dependency_closed",
        )
        return LegacyP25RecordOutcome(
            session.parent_record_id,
            mapped.id,
            "source_container",
            "dependency_closed",
            session.conversation.conversation_id,
            None,
            inserted,
        )

    def _apply_job(
        self,
        connection: ManagedConnection,
        batch: _PreparedBatch,
        job: _PreparedJob,
    ) -> LegacyP25RecordOutcome:
        artifact = self._artifacts.put_legacy_bundle(
            connection, job.artifact_bundle
        )
        self._hit("p25.after_job_artifact")
        audit_uuid = _derived_uuid(_JOB_AUDIT_NAMESPACE, batch.plan_sha256, job.record_id)
        event = self._audit.append(
            connection,
            AuditEventDraft(
                event_uuid=audit_uuid,
                occurred_at_ms=batch.effective_at_ms,
                request_id=batch.request_id,
                operation_id=batch.operation_id,
                action=(
                    "legacy_job.abandoned_history"
                    if job.import_mode == "abandoned_legacy_job"
                    else "legacy_job.terminal_history"
                ),
                outcome=job.result_disposition,
                actor_kind="system",
                actor_user_id=None,
                actor_display_name="P2.5 legacy migration",
                actor_role_snapshot="system",
                space_id=job.owner.target_space_id,
                reason="reviewed P2.5 historical job; never replay",
                details=json.loads(job.evidence_json),
                request_meta={"reviewer_id": batch.reviewer_id},
            ),
        )
        self._hit("p25.after_job_audit")
        source = job.source
        details = LegacyP25JobDetails(
            plan_sha256=batch.plan_sha256,
            inventory_manifest_sha256=batch.inventory_manifest_sha256,
            root_binding_sha256=batch.root_binding_sha256,
            inventory_record_id=job.record_id,
            inventory_record_sha256=str(job.plan_row["inventory_record_sha256"]),
            root_id=str(source["root_id"]),
            path_key_sha256=hashlib.sha256(
                job.path_key.encode("utf-8")
            ).hexdigest(),
            source_record_sha256=str(source["sha256"]),
            target_space_id=job.owner.target_space_id,
            import_mode=job.import_mode,
            result_disposition=job.result_disposition,
            source_status=job.source_status,  # type: ignore[arg-type]
            job_identity_sha256=job.job_identity_sha256,
            job_evidence_sha256=hashlib.sha256(
                job.evidence_json.encode("utf-8")
            ).hexdigest(),
        )
        self._hit("migration.before_checkpoint")
        mapped, inserted = self._mapping.put_job_imported(
            connection,
            target=LegacyP25JobTarget(
                resolved_owner_user_id=job.owner.user_id,
                artifact_id=artifact.ids.artifact_id,
                audit_event_id=event.id,
                audit_event_uuid=event.event_uuid,
                details=details,
                source_path_key=job.path_key,
                job_evidence_json=job.evidence_json,
            ),
            now_ms=batch.effective_at_ms,
        )
        self._outbox.append(
            connection,
            OutboxEventDraft(
                logical_key=f"legacy-migration-p25-job:{mapped.id}:imported:v1",
                space_id=job.owner.target_space_id,
                aggregate_type="legacy_migration_map",
                aggregate_id=mapped.id,
                aggregate_version=1,
                event_type=f"legacy_migration.{job.import_mode}.imported",
                payload={
                    "artifact_id": artifact.ids.artifact_id,
                    "audit_event_id": event.id,
                    "execution_policy": "never_replay",
                    "inventory_record_id": job.record_id,
                    "mapping_id": mapped.id,
                    "operation_id": batch.operation_id,
                    "plan_sha256": batch.plan_sha256,
                    "schema_version": 1,
                },
                available_at_ms=batch.effective_at_ms,
            ),
        )
        return LegacyP25RecordOutcome(
            job.record_id,
            mapped.id,
            job.import_mode,
            job.result_disposition,
            None,
            artifact.ids.artifact_id,
            inserted,
        )

    def _apply(
        self,
        connection: ManagedConnection,
        batch: _PreparedBatch,
    ) -> LegacyP25ImportResult:
        outcomes: list[LegacyP25RecordOutcome] = []
        for session in batch.sessions:
            epoch = self._assert_owner(connection, session.owner)
            conversation = session.conversation
            if conversation.captured_scope_epoch != epoch:
                conversation = LegacyConversation(
                    plan_sha256=conversation.plan_sha256,
                    inventory_record_id=conversation.inventory_record_id,
                    conversation_id=conversation.conversation_id,
                    owner_user_id=conversation.owner_user_id,
                    personal_space_id=conversation.personal_space_id,
                    captured_scope_epoch=epoch,
                    title=conversation.title,
                    metadata_json=conversation.metadata_json,
                    created_at_ms=conversation.created_at_ms,
                )
                session = _PreparedSession(
                    session.parent_row,
                    session.parent_source,
                    session.raw,
                    session.owner,
                    session.path_key,
                    conversation,
                    session.session_id_sha256,
                    session.all_children,
                    session.selected_children,
                    session.close_parent,
                )
            self._conversations.put_legacy_conversation(connection, conversation)
            self._hit("p25.after_conversation")
            for child in session.selected_children:
                outcomes.append(
                    self._apply_session_child(connection, batch, session, child)
                )
                self._hit("p25.after_session_child")
            if session.close_parent:
                outcomes.append(
                    self._apply_session_parent(connection, batch, session)
                )
                self._hit("p25.after_session_parent")
        for job in batch.jobs:
            self._assert_owner(connection, job.owner)
            outcomes.append(self._apply_job(connection, batch, job))
            self._hit("p25.after_job_mapping")
        return LegacyP25ImportResult(
            batch.plan_sha256,
            batch.migration_run_id,
            batch.request_id,
            batch.operation_id,
            tuple(outcomes),
        )


__all__ = [
    "LegacyP25BatchLimitError",
    "LegacyP25ImportError",
    "LegacyP25Importer",
    "LegacyP25ImportResult",
    "LegacyP25PreparationError",
    "LegacyP25RecordOutcome",
    "LegacyP25StateConflictError",
    "MAX_P25_EFFECTS_PER_TRANSACTION",
    "MAX_P25_SOURCE_BYTES",
]
