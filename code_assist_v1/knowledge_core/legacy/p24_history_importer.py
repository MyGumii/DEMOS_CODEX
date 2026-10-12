"""Bounded P2.4 import of non-authoritative legacy audit/history evidence.

Personal wiki logs and tombstone files are retained as content-addressed raw
source artifacts without creating current knowledge items.  Parseable JSONL
audit/tombstone rows become sanitized, hash-bound audit events.  Physical
parents are checkpointed only after their exact imported child set exists.
All source reads and blob staging happen before the service-owned transaction.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
from pathlib import PurePosixPath
from typing import Literal
from uuid import UUID

from ..db import ConnectionFactory, ManagedConnection, transaction
from ..errors import KnowledgeCoreError
from ..repositories.artifacts import (
    ArtifactRepository,
    LegacyArtifactBundle,
    StagedVerifiedBlob,
    legacy_artifact_ids_for_record,
)
from ..repositories.audit import AuditEventDraft, AuditEventRecord, AuditRepository
from ..repositories.migration import (
    migration_mapping_id_for_record,
    migration_run_id_for_plan,
    migration_source_locator,
)
from ..repositories.migration_p24 import (
    LegacyP24HistoryDetails,
    LegacyP24HistoryTarget,
    LegacyP24MappingRepository,
)
from ..repositories.outbox import OutboxEventDraft, OutboxRepository
from ..services.spaces import space_id_for_owner
from .content_importer import (
    LegacyBlobStager,
    LegacyContentPreparationError,
    StagedBlobObservation,
    _assert_blob_root_outside_sources,
)
from .importer import _derived_uuid, _mapping, _reviewed_plan, _utc_ms
from .plan_contract import sha256_canonical


MAX_P24_HISTORY_EFFECTS_PER_TRANSACTION = 100
MAX_P24_HISTORY_SOURCE_BYTES = 256 * 1024 * 1024

_REQUEST_NAMESPACE = UUID("24644036-ad35-4d4d-9951-769c305f5f28")
_OPERATION_NAMESPACE = UUID("52d497ca-407f-46b5-85fc-f0901308c5c3")
_HISTORY_EVENT_NAMESPACE = UUID("a532220f-b04b-40c4-98eb-b7a4fbcc7608")
_MIGRATION_EVENT_NAMESPACE = UUID("c2585201-9521-4cd3-bc40-b2982a939a1c")

_SAFE_ENUM_VALUES = frozenset(
    {
        "active", "admin", "administrator", "applied", "archived",
        "cancelled", "candidate", "child", "contradicts", "conversation",
        "decision", "deleted", "depends_on", "derived_from", "done",
        "duplicate", "example_of", "extends", "failed", "graph", "import",
        "lesson", "member", "memory", "merge", "note", "parent", "part_of",
        "pending", "personal", "preference", "project", "queued", "reference",
        "refine", "rejected", "related", "replace", "replaces", "restore",
        "references", "requires", "running", "similar", "skipped", "source",
        "source-of", "supports", "supersedes", "system", "thought", "unknown",
        "user",
    }
)

HistoryMode = Literal["legacy_history", "source_container"]


class LegacyP24HistoryImportError(KnowledgeCoreError):
    code = "LEGACY_P24_HISTORY_IMPORT_FAILED"


class LegacyP24HistoryPreparationError(LegacyP24HistoryImportError):
    code = "LEGACY_P24_HISTORY_PLAN_INVALID"


class LegacyP24HistoryStateConflictError(LegacyP24HistoryImportError):
    code = "LEGACY_P24_HISTORY_STATE_CONFLICT"


@dataclass(frozen=True, slots=True)
class _OwnerAssertion:
    user_id: str
    system_role: str
    account_status: str
    part_id: str | None
    target_space_id: str


@dataclass(frozen=True, slots=True)
class _PreparedHistory:
    plan_row: Mapping[str, object]
    source: Mapping[str, object]
    record_id: str
    mode: HistoryMode
    owner: _OwnerAssertion | None
    path_key: str
    source_record_sha256: str
    parent_record_id: str | None
    child_record_ids: tuple[str, ...]
    child_bindings_sha256: str
    evidence_json: str
    observation: StagedBlobObservation | None
    artifact_bundle: LegacyArtifactBundle | None


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
    records: tuple[_PreparedHistory, ...]


@dataclass(frozen=True, slots=True)
class LegacyP24HistoryOutcome:
    record_id: str
    mapping_id: str
    import_mode: HistoryMode
    effect_kind: Literal["artifact", "audit_event"]
    effect_id: str
    mapping_inserted: bool


@dataclass(frozen=True, slots=True)
class LegacyP24HistoryImportResult:
    plan_sha256: str
    migration_run_id: str
    records: tuple[LegacyP24HistoryOutcome, ...]

    @property
    def inserted_mapping_count(self) -> int:
        return sum(row.mapping_inserted for row in self.records)


def _canonical_object(value: Mapping[str, object]) -> str:
    try:
        encoded = json.dumps(
            dict(value),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError, RecursionError):
        raise LegacyP24HistoryPreparationError(
            "Legacy-history evidence is not canonical JSON."
        ) from None
    if not isinstance(json.loads(encoded), dict):
        raise LegacyP24HistoryPreparationError(
            "Legacy-history evidence must be an object."
        )
    return encoded


def _record_id(value: object, field: str = "record_id") -> str:
    if (
        not isinstance(value, str)
        or len(value) != 28
        or not value.startswith("rec_")
        or any(character not in "0123456789abcdef" for character in value[4:])
    ):
        raise LegacyP24HistoryPreparationError(f"{field} is invalid.")
    return value


def _source(row: Mapping[str, object]) -> Mapping[str, object]:
    value = row.get("source_record")
    if not isinstance(value, Mapping):
        raise LegacyP24HistoryPreparationError("History source record is invalid.")
    return value


def _safe_enum(value: object) -> str | None:
    text = str(value or "").strip().casefold()
    if not text:
        return None
    return text if text in _SAFE_ENUM_VALUES else "unknown"


def _reject_constant(_value: str) -> None:
    raise ValueError("non_finite_json_number")


def _reject_duplicate_members(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_member")
        result[key] = value
    return result


def _jsonl_evidence(
    raw: bytes,
    *,
    parent_sha256: str,
    child: Mapping[str, object],
) -> dict[str, object]:
    ordinal = child.get("source_ordinal")
    if isinstance(ordinal, bool) or not isinstance(ordinal, int) or ordinal < 1:
        raise LegacyP24HistoryPreparationError(
            "Logical history source ordinal is invalid."
        )
    try:
        lines = raw.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        raise LegacyP24HistoryPreparationError(
            "Imported legacy history is not valid UTF-8."
        ) from None
    if ordinal > len(lines) or not lines[ordinal - 1].strip():
        raise LegacyP24HistoryPreparationError(
            "Imported history ordinal is absent from its parent."
        )
    line = lines[ordinal - 1]
    try:
        value = json.loads(
            line,
            object_pairs_hook=_reject_duplicate_members,
            parse_constant=_reject_constant,
        )
    except (json.JSONDecodeError, ValueError):
        raise LegacyP24HistoryPreparationError(
            "Imported history row is not a valid JSON object."
        ) from None
    if not isinstance(value, dict):
        raise LegacyP24HistoryPreparationError(
            "Imported history row is not a JSON object."
        )
    category = str(child.get("category"))
    raw_status = str(value.get("status") or "").strip().casefold()
    if category == "identity.auth_audit" and raw_status and raw_status not in {
        "denied", "error", "failed", "ok", "success"
    }:
        raise LegacyP24HistoryPreparationError(
            "Imported legacy audit status is outside the frozen vocabulary."
        )
    shape = {
        "kind": _safe_enum(value.get("type") or value.get("kind")),
        "status": _safe_enum(value.get("status")),
        "payload_sha256": sha256_canonical(value),
    }
    expected_child_sha = sha256_canonical(
        {
            "ordinal": ordinal,
            "shape": shape,
            "source_sha256": parent_sha256,
        }
    )
    if child.get("sha256") != expected_child_sha:
        raise LegacyP24HistoryPreparationError(
            "Imported history row differs from its reviewed logical record."
        )
    return {
        "authority": "historical_only",
        "category": category,
        "inventory_record_id": child.get("record_id"),
        "kind": shape["kind"] or "unknown",
        "parent_inventory_record_id": child.get("parent_record_id"),
        "payload_sha256": shape["payload_sha256"],
        "schema_version": 1,
        "source_ordinal": ordinal,
        "status": shape["status"] or "unknown",
    }


def _media_type(source: Mapping[str, object]) -> str:
    suffix = PurePosixPath(str(source.get("relative_path"))).suffix.casefold()
    return {
        ".json": "application/json",
        ".jsonl": "application/x-ndjson",
        ".md": "text/markdown",
        ".markdown": "text/markdown",
        ".txt": "text/plain",
    }.get(suffix, "application/octet-stream")


def _logical_name(source: Mapping[str, object]) -> str:
    name = PurePosixPath(str(source.get("relative_path"))).name
    if not name or len(name) > 500 or "\x00" in name:
        raise LegacyP24HistoryPreparationError(
            "Historical source filename is invalid."
        )
    return name


def _validate_observation(
    observation: object,
    *,
    expected_sha256: str,
    expected_size: int,
    media_type: str,
) -> StagedBlobObservation:
    if not isinstance(observation, StagedBlobObservation):
        raise LegacyP24HistoryPreparationError(
            "The history blob stager returned invalid evidence."
        )
    blob = observation.blob
    evidence = observation.evidence
    if (
        not isinstance(blob, StagedVerifiedBlob)
        or blob.sha256 != expected_sha256
        or blob.size_bytes != expected_size
        or blob.media_type != media_type
        or blob.relative_path
        != f"{expected_sha256[:2]}/{expected_sha256[2:4]}/{expected_sha256}"
        or evidence.raw_sha256 != expected_sha256
        or evidence.size_bytes != expected_size
        or evidence.media_type != media_type
        or evidence.normalized_content_sha256 is not None
        or evidence.normalizer_version is not None
        or not callable(getattr(observation.lease, "close", None))
    ):
        raise LegacyP24HistoryPreparationError(
            "Staged history evidence differs from the reviewed bytes."
        )
    return observation


def _owner_assertion(
    row: Mapping[str, object], mappings: Mapping[str, Mapping[str, object]]
) -> _OwnerAssertion | None:
    user_id = row.get("resolved_owner_user_id")
    if user_id is None:
        if row.get("target_space_kind") is not None or row.get("owner_mapping_id") is not None:
            raise LegacyP24HistoryPreparationError(
                "Global history has an unexpected personal mapping."
            )
        return None
    if not isinstance(user_id, str) or row.get("target_space_kind") != "personal":
        raise LegacyP24HistoryPreparationError(
            "Personal history owner binding is invalid."
        )
    mapping = mappings.get(str(row.get("owner_mapping_id")))
    if (
        mapping is None
        or mapping.get("decision") != "personal"
        or mapping.get("user_id") != user_id
        or mapping.get("system_role") not in {"admin", "user"}
        or mapping.get("account_status") not in {"active", "disabled"}
    ):
        raise LegacyP24HistoryPreparationError(
            "Personal history lacks an exact reviewed owner mapping."
        )
    part_id = mapping.get("part_id")
    if part_id is not None and not isinstance(part_id, str):
        raise LegacyP24HistoryPreparationError(
            "Reviewed history part assertion is invalid."
        )
    return _OwnerAssertion(
        user_id=user_id,
        system_role=str(mapping["system_role"]),
        account_status=str(mapping["account_status"]),
        part_id=part_id,
        target_space_id=space_id_for_owner("personal", user_id),
    )


def _child_digest(
    plan_sha256: str, children: Sequence[Mapping[str, object]]
) -> str:
    bindings = [
        [
            migration_mapping_id_for_record(plan_sha256, str(child["record_id"])),
            str(child["record_id"]),
            str(_source(child)["sha256"]),
        ]
        for child in children
    ]
    return hashlib.sha256(
        json.dumps(
            bindings,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _prepare_batch(
    reviewed_plan_bytes: bytes,
    source_payloads: Mapping[str, bytes],
    *,
    selected_record_ids: Sequence[str],
    expected_plan_sha256: str,
    stager: LegacyBlobStager,
) -> _PreparedBatch:
    if not selected_record_ids:
        raise LegacyP24HistoryPreparationError("A history batch cannot be empty.")
    if len(selected_record_ids) > MAX_P24_HISTORY_EFFECTS_PER_TRANSACTION:
        raise LegacyP24HistoryPreparationError(
            "A history transaction is limited to 100 effects."
        )
    canonical_ids = tuple(_record_id(value) for value in selected_record_ids)
    if canonical_ids != tuple(sorted(set(canonical_ids))):
        raise LegacyP24HistoryPreparationError(
            "Selected history record IDs must be sorted and unique."
        )
    if not isinstance(source_payloads, Mapping):
        raise TypeError("source_payloads must be a mapping of record IDs to bytes.")
    plan = _reviewed_plan(
        reviewed_plan_bytes, expected_plan_sha256=expected_plan_sha256
    )
    draft = _mapping(plan.get("draft"), field="plan draft")
    try:
        _assert_blob_root_outside_sources(stager, draft)
    except LegacyContentPreparationError:
        raise LegacyP24HistoryPreparationError(
            "The history blob root overlaps a reviewed source root."
        ) from None
    rows_value = draft.get("records")
    mappings_value = draft.get("owner_mappings")
    if not isinstance(rows_value, list) or not isinstance(mappings_value, list):
        raise LegacyP24HistoryPreparationError("Reviewed history ledgers are invalid.")
    rows = {
        str(row.get("record_id")): row
        for row in rows_value
        if isinstance(row, Mapping)
    }
    mappings = {
        str(row.get("mapping_id")): row
        for row in mappings_value
        if isinstance(row, Mapping)
    }
    selected = []
    try:
        selected = [rows[value] for value in canonical_ids]
    except KeyError:
        raise LegacyP24HistoryPreparationError(
            "A selected history record is absent from the reviewed plan."
        ) from None
    expected_payload_ids = {
        str(_source(row).get("parent_record_id"))
        if _source(row).get("record_kind") == "logical_record"
        else str(row.get("record_id"))
        for row in selected
    }
    if set(source_payloads) != expected_payload_ids or any(
        not isinstance(value, bytes) for value in source_payloads.values()
    ):
        raise LegacyP24HistoryPreparationError(
            "History source payloads do not exactly match the selected batch."
        )
    if sum(len(value) for value in source_payloads.values()) > MAX_P24_HISTORY_SOURCE_BYTES:
        raise LegacyP24HistoryPreparationError(
            "A history batch exceeds the 256 MiB source limit."
        )
    plan_sha = str(plan["plan_sha256"])
    source_inventory = _mapping(draft.get("source_inventory"), field="source inventory")
    metadata = _mapping(source_inventory.get("metadata"), field="source metadata")
    run = _mapping(metadata.get("run"), field="inventory run")
    inputs = _mapping(draft.get("inputs"), field="plan inputs")
    inventory = _mapping(inputs.get("inventory"), field="inventory binding")
    review = _mapping(plan.get("review"), field="review")
    effective_at = _utc_ms(run.get("started_at_utc"))
    prepared: list[_PreparedHistory] = []
    opened: list[StagedBlobObservation] = []
    try:
        for row in selected:
            record_id = _record_id(row.get("record_id"))
            source = _source(row)
            mode = row.get("import_mode")
            category = source.get("category")
            record_kind = source.get("record_kind")
            if (
                row.get("action") != "import"
                or row.get("execution_phase") != "P2.4"
                or mode not in {"legacy_history", "source_container"}
                or category not in {
                    "identity.auth_audit",
                    "knowledge.wiki_log",
                    "knowledge.tombstones",
                }
                or record_kind not in {"physical_file", "logical_record"}
                or row.get("inventory_record_sha256") != sha256_canonical(source)
            ):
                raise LegacyP24HistoryPreparationError(
                    "A selected record is not supported reviewed P2.4 history."
                )
            if category == "identity.auth_audit" and record_kind == "physical_file":
                if mode != "source_container":
                    raise LegacyP24HistoryPreparationError(
                        "Legacy audit parent must be a source container."
                    )
            elif mode != "legacy_history":
                raise LegacyP24HistoryPreparationError(
                    "Non-audit history must use legacy_history mode."
                )
            owner = _owner_assertion(row, mappings)
            if category == "identity.auth_audit" and owner is not None:
                raise LegacyP24HistoryPreparationError(
                    "Global legacy audit cannot be assigned a personal owner."
                )
            if category != "identity.auth_audit" and owner is None:
                raise LegacyP24HistoryPreparationError(
                    "Personal history requires an exact owner mapping."
                )
            path_key = source.get("path_key")
            source_sha = source.get("sha256")
            if not isinstance(path_key, str) or not isinstance(source_sha, str):
                raise LegacyP24HistoryPreparationError(
                    "History source identity is invalid."
                )
            parent_id = (
                _record_id(source.get("parent_record_id"), "parent_record_id")
                if record_kind == "logical_record"
                else None
            )
            children = tuple(
                sorted(
                    (
                        child
                        for child in rows.values()
                        if child.get("action") == "import"
                        and child.get("execution_phase") == "P2.4"
                        and child.get("import_mode") == "legacy_history"
                        and isinstance(child.get("source_record"), Mapping)
                        and child["source_record"].get("parent_record_id") == record_id
                    ),
                    key=lambda value: str(value.get("record_id")),
                )
            )
            child_ids = tuple(_record_id(child.get("record_id")) for child in children)
            observation = None
            artifact_bundle = None
            if record_kind == "logical_record":
                assert parent_id is not None
                parent = rows.get(parent_id)
                raw = source_payloads.get(parent_id)
                if parent is None or not isinstance(raw, bytes):
                    raise LegacyP24HistoryPreparationError(
                        "Logical history requires its exact physical parent bytes."
                    )
                parent_source = _source(parent)
                if (
                    parent_source.get("record_kind") != "physical_file"
                    or parent_source.get("category") != category
                    or parent_source.get("root_id") != source.get("root_id")
                    or parent_source.get("path_key") != path_key
                    or type(parent_source.get("size_bytes")) is not int
                    or parent_source.get("size_bytes") != len(raw)
                    or hashlib.sha256(raw).hexdigest()
                    != parent_source.get("sha256")
                ):
                    raise LegacyP24HistoryPreparationError(
                        "History parent bytes differ from the reviewed source."
                    )
                evidence = _jsonl_evidence(
                    raw,
                    parent_sha256=str(parent_source["sha256"]),
                    child=source,
                )
            else:
                raw = source_payloads.get(record_id)
                expected_size = source.get("size_bytes")
                if (
                    not isinstance(raw, bytes)
                    or hashlib.sha256(raw).hexdigest() != source_sha
                    or type(expected_size) is not int
                    or expected_size != len(raw)
                ):
                    raise LegacyP24HistoryPreparationError(
                        "Physical history bytes differ from the reviewed source."
                    )
                if category == "identity.auth_audit":
                    evidence = {
                        "authority": "historical_only",
                        "category": category,
                        "inventory_record_id": record_id,
                        "planned_import_child_count": len(child_ids),
                        "raw_sha256": source_sha,
                        "schema_version": 1,
                    }
                else:
                    media_type = _media_type(source)
                    observation = _validate_observation(
                        stager.stage_and_verify(
                            raw,
                            expected_sha256=source_sha,
                            media_type=media_type,
                            effective_at_ms=effective_at,
                            normalized_content_sha256=None,
                            normalizer_version=None,
                        ),
                        expected_sha256=source_sha,
                        expected_size=len(raw),
                        media_type=media_type,
                    )
                    opened.append(observation)
                    evidence = {
                        "authority": "historical_only",
                        "category": category,
                        "inventory_record_id": record_id,
                        "media_type": media_type,
                        "raw_sha256": source_sha,
                        "schema_version": 1,
                        "size_bytes": len(raw),
                    }
                    assert owner is not None
                    ids = legacy_artifact_ids_for_record(plan_sha, record_id)
                    locator = migration_source_locator(
                        root_id=str(source["root_id"]),
                        path_key=path_key,
                        record_kind="physical_file",
                        source_ordinal=0,
                    )
                    name = _logical_name(source)
                    artifact_bundle = LegacyArtifactBundle(
                        plan_sha256=plan_sha,
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
                                "category": category,
                                "inventory_record_id": record_id,
                                "schema_version": 1,
                            }
                        ),
                        created_at_ms=effective_at,
                        extraction=None,
                    )
            prepared.append(
                _PreparedHistory(
                    plan_row=row,
                    source=source,
                    record_id=record_id,
                    mode=mode,  # type: ignore[arg-type]
                    owner=owner,
                    path_key=path_key,
                    source_record_sha256=source_sha,
                    parent_record_id=parent_id,
                    child_record_ids=child_ids,
                    child_bindings_sha256=_child_digest(plan_sha, children),
                    evidence_json=_canonical_object(evidence),
                    observation=observation,
                    artifact_bundle=artifact_bundle,
                )
            )
    except Exception:
        for observation in reversed(opened):
            observation.lease.close()
        raise
    return _PreparedBatch(
        plan_sha256=plan_sha,
        migration_run_id=migration_run_id_for_plan(plan_sha),
        inventory_manifest_sha256=str(inventory["manifest_sha256"]),
        root_binding_sha256=str(inventory["root_binding_sha256"]),
        reviewer_id=str(review["reviewer_id"]),
        effective_at_ms=effective_at,
        request_id=_derived_uuid(_REQUEST_NAMESPACE, plan_sha),
        operation_id=_derived_uuid(_OPERATION_NAMESPACE, plan_sha),
        records=tuple(prepared),
    )


class LegacyP24HistoryImporter:
    """Apply one exact history batch in a service-owned transaction."""

    __slots__ = (
        "_artifacts",
        "_audit",
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
            raise TypeError("LegacyP24HistoryImporter requires a ConnectionFactory.")
        if not callable(getattr(stager, "stage_and_verify", None)):
            raise TypeError("A trusted legacy blob stager is required.")
        self._factory = factory
        self._stager = stager
        self._artifacts = ArtifactRepository()
        self._audit = AuditRepository()
        self._mapping = LegacyP24MappingRepository()
        self._outbox = OutboxRepository()
        self._failpoint = failpoint

    def _hit(self, name: str) -> None:
        if self._failpoint is not None:
            self._failpoint(name)

    def apply_batch(
        self,
        reviewed_plan_bytes: bytes,
        source_payloads: Mapping[str, bytes],
        *,
        selected_record_ids: Sequence[str],
        expected_plan_sha256: str,
    ) -> LegacyP24HistoryImportResult:
        batch = _prepare_batch(
            reviewed_plan_bytes,
            source_payloads,
            selected_record_ids=selected_record_ids,
            expected_plan_sha256=expected_plan_sha256,
            stager=self._stager,
        )
        try:
            connection = self._factory.open()
            try:
                with transaction(connection):
                    return self._apply(connection, batch)
            finally:
                connection.close()
        finally:
            for prepared in batch.records:
                if prepared.observation is not None:
                    prepared.observation.lease.close()

    @staticmethod
    def _assert_owner(
        connection: ManagedConnection, owner: _OwnerAssertion | None
    ) -> None:
        if owner is None:
            return
        row = connection.execute(
            """SELECT system_role,account_status,current_part_id
               FROM app_user WHERE id=?""",
            (owner.user_id,),
        ).fetchone()
        if row is None or tuple(row) != (
            owner.system_role,
            owner.account_status,
            owner.part_id,
        ):
            raise LegacyP24HistoryStateConflictError(
                "The reviewed history owner assertion drifted."
            )
        space = connection.execute(
            """SELECT space_kind,owner_user_id,status
               FROM knowledge_space WHERE id=?""",
            (owner.target_space_id,),
        ).fetchone()
        if space is None or tuple(space) != ("personal", owner.user_id, "active"):
            raise LegacyP24HistoryStateConflictError(
                "The reviewed history personal-space assertion drifted."
            )

    def _history_event(
        self,
        connection: ManagedConnection,
        batch: _PreparedBatch,
        prepared: _PreparedHistory,
    ) -> AuditEventRecord:
        event_uuid = _derived_uuid(
            _HISTORY_EVENT_NAMESPACE,
            migration_mapping_id_for_record(batch.plan_sha256, prepared.record_id),
        )
        return self._audit.append(
            connection,
            AuditEventDraft(
                event_uuid=event_uuid,
                occurred_at_ms=batch.effective_at_ms,
                request_id=batch.request_id,
                operation_id=batch.operation_id,
                action=f"legacy_history.{prepared.source['category']}",
                outcome="historical",
                actor_kind="system",
                actor_user_id=None,
                actor_display_name="Legacy history (non-authoritative)",
                actor_role_snapshot="system",
                space_id=(
                    None if prepared.owner is None else prepared.owner.target_space_id
                ),
                item_id=None,
                reason="reviewed P2.4 labelled legacy history",
                details=json.loads(prepared.evidence_json),
                request_meta={"reviewer_id": batch.reviewer_id},
            ),
        )

    def _migration_event(
        self,
        connection: ManagedConnection,
        batch: _PreparedBatch,
        prepared: _PreparedHistory,
        *,
        mapping_id: str,
        artifact_id: str,
    ) -> None:
        details = {
            "artifact_id": artifact_id,
            "authority": "historical_only",
            "inventory_record_id": prepared.record_id,
            "mapping_id": mapping_id,
            "plan_sha256": batch.plan_sha256,
            "schema_version": 1,
        }
        assert prepared.owner is not None
        self._audit.append(
            connection,
            AuditEventDraft(
                event_uuid=_derived_uuid(_MIGRATION_EVENT_NAMESPACE, mapping_id),
                occurred_at_ms=batch.effective_at_ms,
                request_id=batch.request_id,
                operation_id=batch.operation_id,
                action="legacy_migration.legacy_history",
                outcome="artifact_retained",
                actor_kind="system",
                actor_user_id=None,
                actor_display_name="P2.4 legacy migration",
                actor_role_snapshot="system",
                space_id=prepared.owner.target_space_id,
                item_id=None,
                reason="reviewed P2.4 non-authoritative history artifact",
                details=details,
                request_meta={"reviewer_id": batch.reviewer_id},
            ),
        )
        self._outbox.append(
            connection,
            OutboxEventDraft(
                logical_key=f"legacy-migration-p24-history:{mapping_id}:imported:v1",
                space_id=prepared.owner.target_space_id,
                aggregate_type="legacy_migration_map",
                aggregate_id=mapping_id,
                aggregate_version=1,
                event_type="legacy_migration.legacy_history.imported",
                payload={**details, "operation_id": batch.operation_id},
                available_at_ms=batch.effective_at_ms,
            ),
        )

    def _apply_one(
        self,
        connection: ManagedConnection,
        batch: _PreparedBatch,
        prepared: _PreparedHistory,
    ) -> LegacyP24HistoryOutcome:
        self._assert_owner(connection, prepared.owner)
        artifact_id = None
        event = None
        if prepared.artifact_bundle is not None:
            artifact = self._artifacts.put_legacy_bundle(
                connection, prepared.artifact_bundle
            )
            artifact_id = artifact.ids.artifact_id
            effect_kind: Literal["artifact", "audit_event"] = "artifact"
            effect_id = artifact_id
            disposition = "artifact_retained"
        else:
            event = self._history_event(connection, batch, prepared)
            effect_kind = "audit_event"
            effect_id = str(event.id)
            disposition = (
                "dependency_closed"
                if prepared.source.get("record_kind") == "physical_file"
                else "historical_event"
            )
        self._hit("p24_history.after_effect")
        source = prepared.source
        evidence_hash = hashlib.sha256(
            prepared.evidence_json.encode("utf-8")
        ).hexdigest()
        details = LegacyP24HistoryDetails(
            plan_sha256=batch.plan_sha256,
            inventory_manifest_sha256=batch.inventory_manifest_sha256,
            root_binding_sha256=batch.root_binding_sha256,
            inventory_record_id=prepared.record_id,
            inventory_record_sha256=str(
                prepared.plan_row["inventory_record_sha256"]
            ),
            parent_inventory_record_id=prepared.parent_record_id,
            root_id=str(source["root_id"]),
            path_key_sha256=hashlib.sha256(
                prepared.path_key.encode("utf-8")
            ).hexdigest(),
            category=str(source["category"]),
            record_kind=str(source["record_kind"]),
            source_ordinal=int(source["source_ordinal"]),
            import_mode=prepared.mode,
            result_disposition=disposition,
            target_space_id=(
                None if prepared.owner is None else prepared.owner.target_space_id
            ),
            source_record_sha256=prepared.source_record_sha256,
            history_evidence_sha256=evidence_hash,
            planned_import_child_record_ids=prepared.child_record_ids,
            imported_child_bindings_sha256=prepared.child_bindings_sha256,
        )
        self._hit("migration.before_checkpoint")
        mapped, inserted = self._mapping.put_history_imported(
            connection,
            target=LegacyP24HistoryTarget(
                resolved_owner_user_id=(
                    None if prepared.owner is None else prepared.owner.user_id
                ),
                artifact_id=artifact_id,
                audit_event_id=None if event is None else event.id,
                audit_event_uuid=None if event is None else event.event_uuid,
                details=details,
                source_path_key=prepared.path_key,
                history_evidence_json=prepared.evidence_json,
            ),
            now_ms=batch.effective_at_ms,
        )
        self._hit("p24_history.after_mapping")
        if artifact_id is not None:
            self._migration_event(
                connection,
                batch,
                prepared,
                mapping_id=mapped.id,
                artifact_id=artifact_id,
            )
        else:
            assert event is not None
            self._outbox.append(
                connection,
                OutboxEventDraft(
                    logical_key=f"legacy-history:{mapped.id}:imported:v1",
                    space_id=(
                        None
                        if prepared.owner is None
                        else prepared.owner.target_space_id
                    ),
                    aggregate_type="audit_event",
                    aggregate_id=event.event_uuid,
                    aggregate_version=1,
                    event_type="legacy_history.imported",
                    payload={
                        "audit_event_id": event.id,
                        "authority": "historical_only",
                        "inventory_record_id": prepared.record_id,
                        "mapping_id": mapped.id,
                        "operation_id": batch.operation_id,
                        "plan_sha256": batch.plan_sha256,
                        "schema_version": 1,
                    },
                    available_at_ms=batch.effective_at_ms,
                ),
            )
        return LegacyP24HistoryOutcome(
            prepared.record_id,
            mapped.id,
            prepared.mode,
            effect_kind,
            effect_id,
            inserted,
        )

    def _apply(
        self, connection: ManagedConnection, batch: _PreparedBatch
    ) -> LegacyP24HistoryImportResult:
        ordered = tuple(
            sorted(
                batch.records,
                key=lambda value: (
                    value.source.get("record_kind") == "physical_file",
                    value.record_id,
                ),
            )
        )
        outcomes = tuple(
            self._apply_one(connection, batch, prepared) for prepared in ordered
        )
        return LegacyP24HistoryImportResult(
            batch.plan_sha256, batch.migration_run_id, outcomes
        )


__all__ = [
    "LegacyP24HistoryImportError",
    "LegacyP24HistoryImportResult",
    "LegacyP24HistoryImporter",
    "LegacyP24HistoryOutcome",
    "LegacyP24HistoryPreparationError",
    "LegacyP24HistoryStateConflictError",
    "MAX_P24_HISTORY_EFFECTS_PER_TRANSACTION",
    "MAX_P24_HISTORY_SOURCE_BYTES",
]
