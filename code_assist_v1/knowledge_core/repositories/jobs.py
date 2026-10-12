"""Connection-scoped persistence primitives for canonical durable jobs.

The repository owns SQL, row mapping, database-time validation, and worker
claim compare-and-swap fences.  It deliberately does not own threads, handler
callables, polling, or domain authorization orchestration; those belong to the
durable worker boundary.  A handler commits its domain effect and calls
``succeed`` in the same caller-owned transaction, so the unique effect key and
terminal result cannot diverge after a process crash.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass, replace
from hashlib import sha256
import json
import re
import sqlite3
from typing import Literal

from ..db import ManagedConnection, db_now_ms
from ..errors import KnowledgeCoreDatabaseError, KnowledgeCoreTransactionError


JobState = Literal[
    "queued",
    "claimed",
    "running",
    "retry",
    "succeeded",
    "failed",
    "cancelled",
    "dead",
]
ActorKind = Literal["user", "system"]

_NONTERMINAL_STATES = ("queued", "claimed", "running", "retry")
_TERMINAL_STATES = ("succeeded", "failed", "cancelled", "dead")
_WORKER_STATES = ("claimed", "running")
_IJSON_MAX_INTEGER = (1 << 53) - 1
_SQLITE_MAX_INTEGER = (1 << 63) - 1
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.ASCII,
)
_USER_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$", re.ASCII)

_JOB_COLUMNS = """
id,dedup_id,operation_id,actor_kind,actor_user_id,space_id,job_type,
handler_version,effect_key,input_ref_json,input_sha256,expected_state_json,
authorization_snapshot_json,authorization_snapshot_sha256,
authorization_scope_epoch,authorization_part_id,
authorization_capabilities_json,state,claim_owner,claim_expires_at,
attempt_count,max_attempts,cancellable,cancel_requested,progress_permille,
result_json,error_json,available_at,created_at,updated_at,completed_at
""".strip()


class JobRepositoryError(KnowledgeCoreDatabaseError):
    code = "JOB_REPOSITORY_ERROR"


class JobNotFoundError(JobRepositoryError):
    code = "JOB_NOT_FOUND"


class JobEffectConflictError(JobRepositoryError):
    code = "JOB_EFFECT_CONFLICT"


class JobStateConflictError(JobRepositoryError):
    code = "JOB_STATE_CONFLICT"


class JobClaimLostError(JobRepositoryError):
    code = "JOB_CLAIM_LOST"


class JobCancellationUnsupportedError(JobRepositoryError):
    code = "JOB_NOT_CANCELLABLE"


class JobCancellationRequestedError(JobRepositoryError):
    code = "JOB_CANCEL_REQUESTED"


@dataclass(frozen=True, slots=True, order=True)
class HandlerKey:
    job_type: str
    handler_version: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "job_type",
            _required_text(self.job_type, field="job_type", maximum=100),
        )
        object.__setattr__(
            self,
            "handler_version",
            _required_text(
                self.handler_version, field="handler_version", maximum=80
            ),
        )


@dataclass(frozen=True, slots=True)
class ClaimFence:
    job_id: str
    claim_owner: str
    attempt_count: int

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "job_id",
            _canonical_uuid(self.job_id, field="job_id"),
        )
        object.__setattr__(
            self,
            "claim_owner",
            _required_text(self.claim_owner, field="claim_owner", maximum=200),
        )
        object.__setattr__(
            self,
            "attempt_count",
            _integer(self.attempt_count, field="attempt_count", minimum=1),
        )


@dataclass(frozen=True, slots=True)
class JobEnqueueDraft:
    job_id: str
    dedup_id: str | None
    operation_id: str | None
    actor_kind: ActorKind
    actor_user_id: str | None
    space_id: str | None
    job_type: str
    handler_version: str
    effect_key: str
    input_ref: Mapping[str, object]
    expected_state: Mapping[str, object]
    authorization_snapshot: Mapping[str, object]
    authorization_scope_epoch: int | None
    authorization_part_id: str | None
    authorization_capabilities: tuple[str, ...]
    max_attempts: int
    cancellable: bool
    available_at_ms: int
    created_at_ms: int | None = None


@dataclass(frozen=True, slots=True)
class DurableJobRecord:
    id: str
    dedup_id: str | None
    operation_id: str | None
    actor_kind: ActorKind
    actor_user_id: str | None
    space_id: str | None
    job_type: str
    handler_version: str
    effect_key: str
    input_ref_json: str
    input_sha256: str
    expected_state_json: str
    authorization_snapshot_json: str
    authorization_snapshot_sha256: str
    authorization_scope_epoch: int | None
    authorization_part_id: str | None
    authorization_capabilities_json: str
    state: JobState
    claim_owner: str | None
    claim_expires_at_ms: int | None
    attempt_count: int
    max_attempts: int
    cancellable: bool
    cancel_requested: bool
    progress_permille: int
    result_json: str | None
    error_json: str | None
    available_at_ms: int
    created_at_ms: int
    updated_at_ms: int
    completed_at_ms: int | None

    @property
    def job_id(self) -> str:
        return self.id

    @property
    def handler_key(self) -> HandlerKey:
        return HandlerKey(self.job_type, self.handler_version)

    @property
    def input_ref(self) -> dict[str, object]:
        return _parsed_object(self.input_ref_json, field="input_ref_json")

    @property
    def expected_state(self) -> dict[str, object]:
        return _parsed_object(self.expected_state_json, field="expected_state_json")

    @property
    def authorization_snapshot(self) -> dict[str, object]:
        return _parsed_object(
            self.authorization_snapshot_json,
            field="authorization_snapshot_json",
        )

    @property
    def authorization_capabilities(self) -> tuple[str, ...]:
        value = json.loads(self.authorization_capabilities_json)
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            raise JobRepositoryError(
                "Stored job authorization capabilities are not a string array."
            )
        return tuple(value)

    @property
    def result(self) -> dict[str, object] | None:
        return (
            None
            if self.result_json is None
            else _parsed_object(self.result_json, field="result_json")
        )

    @property
    def error(self) -> dict[str, object] | None:
        return (
            None
            if self.error_json is None
            else _parsed_object(self.error_json, field="error_json")
        )


@dataclass(frozen=True, slots=True)
class EnqueueResult:
    job: DurableJobRecord
    inserted: bool


@dataclass(frozen=True, slots=True)
class ClaimResult:
    job: DurableJobRecord
    fence: ClaimFence


@dataclass(frozen=True, slots=True)
class RecoveryResult:
    retried_ids: tuple[str, ...]
    dead_ids: tuple[str, ...]
    cancelled_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CancelResult:
    job: DurableJobRecord
    changed: bool
    immediate: bool


@dataclass(frozen=True, slots=True)
class _PreparedEnqueue:
    draft: JobEnqueueDraft
    input_ref_json: str
    input_sha256: str
    expected_state_json: str
    authorization_snapshot_json: str
    authorization_snapshot_sha256: str
    authorization_capabilities_json: str


def _require_connection(connection: ManagedConnection) -> ManagedConnection:
    if not isinstance(connection, ManagedConnection):
        raise TypeError("DurableJobRepository requires a ManagedConnection.")
    return connection


def _require_transaction(connection: ManagedConnection) -> ManagedConnection:
    connection = _require_connection(connection)
    if not connection.in_transaction:
        raise KnowledgeCoreTransactionError(
            "Durable job access requires an existing caller-owned IMMEDIATE transaction."
        )
    return connection


def _canonical_uuid(
    value: object,
    *,
    field: str,
    nullable: bool = False,
) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or _UUID_RE.fullmatch(value) is None:
        raise ValueError(f"{field} must be a canonical lowercase UUID.")
    return value


def _user_id(value: object, *, field: str, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or _USER_ID_RE.fullmatch(value) is None:
        raise ValueError(f"{field} must be a canonical user ID.")
    return value


def _required_text(value: object, *, field: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be text.")
    normalized = value.strip()
    if not 1 <= len(normalized) <= maximum:
        raise ValueError(f"{field} must contain 1..{maximum} characters.")
    if _has_surrogate(normalized):
        raise ValueError(f"{field} cannot contain a Unicode surrogate.")
    return normalized


def _integer(
    value: object,
    *,
    field: str,
    minimum: int = 0,
    maximum: int = _SQLITE_MAX_INTEGER,
) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not minimum <= value <= maximum
    ):
        raise ValueError(f"{field} must be an integer from {minimum} through {maximum}.")
    return value


def _has_surrogate(value: str) -> bool:
    return any(0xD800 <= ord(character) <= 0xDFFF for character in value)


def _ijson_value(value: object, *, field: str) -> object:
    """Return a JSON-ready value from the supported RFC 8785/I-JSON subset.

    Arbitrary Python floats are deliberately rejected.  They cannot be emitted
    with RFC 8785 number serialization by the standard library.  Integers are
    restricted to the exactly interoperable IEEE-754 range, and Unicode
    surrogate code points are rejected.
    """

    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        if not -_IJSON_MAX_INTEGER <= value <= _IJSON_MAX_INTEGER:
            raise ValueError(f"{field} contains an integer outside the I-JSON range.")
        return value
    if isinstance(value, float):
        raise ValueError(f"{field} cannot contain floating-point numbers.")
    if isinstance(value, str):
        if _has_surrogate(value):
            raise ValueError(f"{field} cannot contain a Unicode surrogate.")
        return value
    if isinstance(value, Mapping):
        normalized: dict[str, object] = {}
        for key, child in value.items():
            if not isinstance(key, str) or _has_surrogate(key):
                raise ValueError(f"{field} object keys must be valid Unicode strings.")
            normalized[key] = _ijson_value(child, field=field)
        return normalized
    if isinstance(value, (list, tuple)):
        return [_ijson_value(child, field=field) for child in value]
    raise ValueError(f"{field} contains a value outside the supported I-JSON subset.")


def _canonical_json(value: object, *, field: str, require_object: bool) -> str:
    normalized = _ijson_value(value, field=field)
    if require_object and not isinstance(normalized, dict):
        raise ValueError(f"{field} must be a JSON object mapping.")
    try:
        return _serialize_canonical_json(normalized)
    except (TypeError, ValueError, UnicodeError) as exc:  # defensive boundary
        raise ValueError(f"{field} cannot be canonicalized as JSON.") from exc


def _serialize_canonical_json(value: object) -> str:
    """Serialize the supported subset with RFC 8785 UTF-16 key ordering."""

    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False, allow_nan=False)
    if isinstance(value, list):
        return "[" + ",".join(_serialize_canonical_json(item) for item in value) + "]"
    if isinstance(value, dict):
        keys = sorted(value, key=lambda key: key.encode("utf-16-be"))
        return "{" + ",".join(
            _serialize_canonical_json(key)
            + ":"
            + _serialize_canonical_json(value[key])
            for key in keys
        ) + "}"
    raise TypeError("The normalized value is outside the canonical JSON subset.")


def _canonical_object(value: object, *, field: str) -> str:
    return _canonical_json(value, field=field, require_object=True)


def _canonical_array(value: object, *, field: str) -> str:
    encoded = _canonical_json(value, field=field, require_object=False)
    if not isinstance(json.loads(encoded), list):
        raise ValueError(f"{field} must be a JSON array.")
    return encoded


def _json_sha256(encoded: str) -> str:
    return sha256(encoded.encode("utf-8")).hexdigest()


def canonical_job_input_sha256(input_ref: Mapping[str, object]) -> str:
    """Return the exact hash bound to job operation-dedup and durable input."""

    return _json_sha256(_canonical_object(input_ref, field="input_ref"))


def _parsed_object(encoded: str, *, field: str) -> dict[str, object]:
    try:
        value = json.loads(encoded)
    except (TypeError, ValueError) as exc:
        raise JobRepositoryError(f"Stored {field} is invalid JSON.") from exc
    if not isinstance(value, dict):
        raise JobRepositoryError(f"Stored {field} is not a JSON object.")
    return value


def _capabilities(value: object) -> tuple[str, ...]:
    if not isinstance(value, tuple):
        raise ValueError("authorization_capabilities must be a tuple.")
    result = tuple(
        _required_text(item, field="authorization capability", maximum=80)
        for item in value
    )
    if result != tuple(sorted(set(result))):
        raise ValueError(
            "authorization_capabilities must be duplicate-free and lexically sorted."
        )
    return result


def _prepare_enqueue(draft: JobEnqueueDraft) -> _PreparedEnqueue:
    if not isinstance(draft, JobEnqueueDraft):
        raise TypeError("enqueue requires a JobEnqueueDraft.")
    job_id = _canonical_uuid(draft.job_id, field="job_id")
    dedup_id = _canonical_uuid(draft.dedup_id, field="dedup_id", nullable=True)
    operation_id = _canonical_uuid(
        draft.operation_id, field="operation_id", nullable=True
    )
    actor_kind = draft.actor_kind
    if actor_kind not in ("user", "system"):
        raise ValueError("actor_kind must be user or system.")
    actor_user_id = _user_id(
        draft.actor_user_id, field="actor_user_id", nullable=True
    )
    space_id = _canonical_uuid(draft.space_id, field="space_id", nullable=True)
    job_type = _required_text(draft.job_type, field="job_type", maximum=100)
    handler_version = _required_text(
        draft.handler_version, field="handler_version", maximum=80
    )
    effect_key = _required_text(draft.effect_key, field="effect_key", maximum=300)
    authorization_scope_epoch = (
        None
        if draft.authorization_scope_epoch is None
        else _integer(
            draft.authorization_scope_epoch,
            field="authorization_scope_epoch",
            minimum=1,
        )
    )
    authorization_part_id = _canonical_uuid(
        draft.authorization_part_id,
        field="authorization_part_id",
        nullable=True,
    )
    capabilities = _capabilities(draft.authorization_capabilities)
    max_attempts = _integer(draft.max_attempts, field="max_attempts", minimum=1)
    if not isinstance(draft.cancellable, bool):
        raise ValueError("cancellable must be a Boolean.")
    available_at = _integer(draft.available_at_ms, field="available_at_ms")
    created_at = (
        None
        if draft.created_at_ms is None
        else _integer(draft.created_at_ms, field="created_at_ms")
    )

    if actor_kind == "user":
        if actor_user_id is None or authorization_scope_epoch is None:
            raise ValueError("A user job requires an actor and captured scope epoch.")
    elif any(
        value is not None
        for value in (actor_user_id, authorization_scope_epoch, authorization_part_id)
    ) or capabilities:
        raise ValueError("A system job cannot carry user authorization fields.")
    if (dedup_id is None) != (operation_id is None):
        raise ValueError("dedup_id and operation_id must be supplied together.")
    if dedup_id is not None and actor_kind != "user":
        raise ValueError("Only a user job may reference operation idempotency.")

    input_ref_json = _canonical_object(draft.input_ref, field="input_ref")
    expected_state_json = _canonical_object(
        draft.expected_state, field="expected_state"
    )
    authorization_snapshot_json = _canonical_object(
        draft.authorization_snapshot,
        field="authorization_snapshot",
    )
    authorization_capabilities_json = _canonical_array(
        capabilities,
        field="authorization_capabilities",
    )
    normalized = JobEnqueueDraft(
        job_id=job_id,  # type: ignore[arg-type]
        dedup_id=dedup_id,
        operation_id=operation_id,
        actor_kind=actor_kind,
        actor_user_id=actor_user_id,
        space_id=space_id,
        job_type=job_type,
        handler_version=handler_version,
        effect_key=effect_key,
        input_ref=draft.input_ref,
        expected_state=draft.expected_state,
        authorization_snapshot=draft.authorization_snapshot,
        authorization_scope_epoch=authorization_scope_epoch,
        authorization_part_id=authorization_part_id,
        authorization_capabilities=capabilities,
        max_attempts=max_attempts,
        cancellable=draft.cancellable,
        available_at_ms=available_at,
        created_at_ms=created_at,
    )
    return _PreparedEnqueue(
        draft=normalized,
        input_ref_json=input_ref_json,
        input_sha256=_json_sha256(input_ref_json),
        expected_state_json=expected_state_json,
        authorization_snapshot_json=authorization_snapshot_json,
        authorization_snapshot_sha256=_json_sha256(authorization_snapshot_json),
        authorization_capabilities_json=authorization_capabilities_json,
    )


def _row_to_record(row: object) -> DurableJobRecord:
    try:
        record = DurableJobRecord(
            id=str(row["id"]),  # type: ignore[index]
            dedup_id=row["dedup_id"],  # type: ignore[index,arg-type]
            operation_id=row["operation_id"],  # type: ignore[index,arg-type]
            actor_kind=row["actor_kind"],  # type: ignore[index,arg-type]
            actor_user_id=row["actor_user_id"],  # type: ignore[index,arg-type]
            space_id=row["space_id"],  # type: ignore[index,arg-type]
            job_type=str(row["job_type"]),  # type: ignore[index]
            handler_version=str(row["handler_version"]),  # type: ignore[index]
            effect_key=str(row["effect_key"]),  # type: ignore[index]
            input_ref_json=str(row["input_ref_json"]),  # type: ignore[index]
            input_sha256=str(row["input_sha256"]),  # type: ignore[index]
            expected_state_json=str(row["expected_state_json"]),  # type: ignore[index]
            authorization_snapshot_json=str(  # type: ignore[index]
                row["authorization_snapshot_json"]
            ),
            authorization_snapshot_sha256=str(  # type: ignore[index]
                row["authorization_snapshot_sha256"]
            ),
            authorization_scope_epoch=row[  # type: ignore[index,arg-type]
                "authorization_scope_epoch"
            ],
            authorization_part_id=row["authorization_part_id"],  # type: ignore[index,arg-type]
            authorization_capabilities_json=str(  # type: ignore[index]
                row["authorization_capabilities_json"]
            ),
            state=row["state"],  # type: ignore[index,arg-type]
            claim_owner=row["claim_owner"],  # type: ignore[index,arg-type]
            claim_expires_at_ms=row["claim_expires_at"],  # type: ignore[index,arg-type]
            attempt_count=int(row["attempt_count"]),  # type: ignore[index]
            max_attempts=int(row["max_attempts"]),  # type: ignore[index]
            cancellable=bool(row["cancellable"]),  # type: ignore[index]
            cancel_requested=bool(row["cancel_requested"]),  # type: ignore[index]
            progress_permille=int(row["progress_permille"]),  # type: ignore[index]
            result_json=row["result_json"],  # type: ignore[index,arg-type]
            error_json=row["error_json"],  # type: ignore[index,arg-type]
            available_at_ms=int(row["available_at"]),  # type: ignore[index]
            created_at_ms=int(row["created_at"]),  # type: ignore[index]
            updated_at_ms=int(row["updated_at"]),  # type: ignore[index]
            completed_at_ms=row["completed_at"],  # type: ignore[index,arg-type]
        )
    except (KeyError, TypeError, ValueError, IndexError) as exc:
        raise JobRepositoryError("Canonical durable-job row mapping failed.") from exc
    _verify_record_integrity(record)
    return record


def _verify_record_integrity(record: DurableJobRecord) -> None:
    try:
        input_ref = _parsed_object(record.input_ref_json, field="input_ref_json")
        expected_state = _parsed_object(
            record.expected_state_json, field="expected_state_json"
        )
        snapshot = _parsed_object(
            record.authorization_snapshot_json,
            field="authorization_snapshot_json",
        )
        capabilities_value = json.loads(record.authorization_capabilities_json)
        if not isinstance(capabilities_value, list) or any(
            not isinstance(item, str) for item in capabilities_value
        ):
            raise ValueError("authorization capabilities are not a string array")
        capabilities = tuple(capabilities_value)
        if capabilities != tuple(sorted(set(capabilities))):
            raise ValueError("authorization capabilities are not sorted and unique")
        canonical_input = _canonical_object(input_ref, field="input_ref")
        if canonical_input != record.input_ref_json:
            raise ValueError("input reference is not canonical")
        # Hash the exact bytes just validated; do not canonicalize the same
        # parsed input again. Every row is still freshly parsed and verified.
        if _json_sha256(canonical_input) != record.input_sha256:
            raise ValueError("input reference hash differs")
        if (
            _canonical_object(expected_state, field="expected_state")
            != record.expected_state_json
        ):
            raise ValueError("expected state is not canonical")
        if (
            _canonical_object(snapshot, field="authorization_snapshot")
            != record.authorization_snapshot_json
        ):
            raise ValueError("authorization snapshot is not canonical")
        if (
            _json_sha256(record.authorization_snapshot_json)
            != record.authorization_snapshot_sha256
        ):
            raise ValueError("authorization snapshot hash differs")
        if (
            _canonical_array(capabilities, field="authorization_capabilities")
            != record.authorization_capabilities_json
        ):
            raise ValueError("authorization capabilities are not canonical")
        for field, encoded in (
            ("result", record.result_json),
            ("error", record.error_json),
        ):
            if encoded is None:
                continue
            parsed = _parsed_object(encoded, field=f"{field}_json")
            if _canonical_object(parsed, field=field) != encoded:
                raise ValueError(f"{field} is not canonical")
        if record.actor_kind == "system":
            if snapshot != {"schema_version": 1, "actor_kind": "system"}:
                raise ValueError("system authorization snapshot shape differs")
            if any(
                value is not None
                for value in (
                    record.actor_user_id,
                    record.authorization_scope_epoch,
                    record.authorization_part_id,
                )
            ) or capabilities:
                raise ValueError("system authorization columns differ")
        elif record.actor_kind == "user":
            required_keys = {
                "schema_version",
                "actor_kind",
                "actor_user_id",
                "account_status",
                "system_role",
                "current_part_id",
                "scope_epoch",
                "space_id",
                "capabilities",
            }
            if set(snapshot) != required_keys:
                raise ValueError("user authorization snapshot keys differ")
            if (
                snapshot.get("schema_version") != 1
                or snapshot.get("actor_kind") != "user"
                or snapshot.get("actor_user_id") != record.actor_user_id
                or snapshot.get("current_part_id") != record.authorization_part_id
                or snapshot.get("scope_epoch") != record.authorization_scope_epoch
                or snapshot.get("space_id") != record.space_id
                or snapshot.get("capabilities") != list(capabilities)
            ):
                raise ValueError("user authorization snapshot values differ")
        else:
            raise ValueError("actor kind differs")
    except (JobRepositoryError, TypeError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
        if isinstance(exc, JobRepositoryError):
            raise
        raise JobRepositoryError(
            "Canonical durable-job JSON/hash integrity validation failed."
        ) from exc


def _prepared_identity(prepared: _PreparedEnqueue) -> tuple[object, ...]:
    draft = prepared.draft
    return (
        draft.job_id,
        draft.dedup_id,
        draft.operation_id,
        draft.actor_kind,
        draft.actor_user_id,
        draft.space_id,
        draft.job_type,
        draft.handler_version,
        draft.effect_key,
        prepared.input_ref_json,
        prepared.input_sha256,
        prepared.expected_state_json,
        prepared.authorization_snapshot_json,
        prepared.authorization_snapshot_sha256,
        draft.authorization_scope_epoch,
        draft.authorization_part_id,
        prepared.authorization_capabilities_json,
        draft.max_attempts,
        draft.cancellable,
    )


def _record_identity(record: DurableJobRecord) -> tuple[object, ...]:
    return (
        record.id,
        record.dedup_id,
        record.operation_id,
        record.actor_kind,
        record.actor_user_id,
        record.space_id,
        record.job_type,
        record.handler_version,
        record.effect_key,
        record.input_ref_json,
        record.input_sha256,
        record.expected_state_json,
        record.authorization_snapshot_json,
        record.authorization_snapshot_sha256,
        record.authorization_scope_epoch,
        record.authorization_part_id,
        record.authorization_capabilities_json,
        record.max_attempts,
        record.cancellable,
    )


def _fence(value: ClaimFence) -> ClaimFence:
    if not isinstance(value, ClaimFence):
        raise TypeError("A worker mutation requires a ClaimFence.")
    return ClaimFence(
        job_id=_canonical_uuid(value.job_id, field="job_id"),  # type: ignore[arg-type]
        claim_owner=_required_text(
            value.claim_owner, field="claim_owner", maximum=200
        ),
        attempt_count=_integer(
            value.attempt_count, field="attempt_count", minimum=1
        ),
    )


def _handler_keys(values: Collection[HandlerKey]) -> tuple[HandlerKey, ...]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Collection):
        raise TypeError("handler_keys must be a collection of HandlerKey values.")
    normalized = tuple(
        HandlerKey(
            _required_text(value.job_type, field="job_type", maximum=100),
            _required_text(
                value.handler_version, field="handler_version", maximum=80
            ),
        )
        if isinstance(value, HandlerKey)
        else (_raise_handler_type())
        for value in values
    )
    return tuple(sorted(set(normalized)))


def _raise_handler_type() -> HandlerKey:
    raise TypeError("handler_keys must contain only HandlerKey values.")


class JobRepository:
    """SQL/CAS primitives for the frozen ``durable_job`` aggregate."""

    __slots__ = ()

    def get(
        self, connection: ManagedConnection, job_id: str
    ) -> DurableJobRecord | None:
        connection = _require_transaction(connection)
        job_id = _canonical_uuid(job_id, field="job_id")  # type: ignore[assignment]
        row = connection.execute(
            f"SELECT {_JOB_COLUMNS} FROM durable_job WHERE id=?",
            (job_id,),
        ).fetchone()
        return None if row is None else _row_to_record(row)

    def require(
        self, connection: ManagedConnection, job_id: str
    ) -> DurableJobRecord:
        record = self.get(connection, job_id)
        if record is None:
            raise JobNotFoundError("The requested durable job does not exist.")
        return record

    def list_nonterminal_handler_keys(
        self, connection: ManagedConnection
    ) -> tuple[HandlerKey, ...]:
        """Return every exact handler version retained by unfinished work."""

        bindings = self.list_nonterminal_handler_bindings(connection)
        return tuple(sorted({key for key, _actor_kind in bindings}))

    def list_nonterminal_handler_bindings(
        self, connection: ManagedConnection
    ) -> tuple[tuple[HandlerKey, ActorKind], ...]:
        """Return exact handler versions and actor contracts for unfinished work."""

        connection = _require_transaction(connection)
        rows = connection.execute(
            """SELECT DISTINCT job_type,handler_version,actor_kind
                 FROM durable_job
                WHERE state IN ('queued','claimed','running','retry')
                ORDER BY job_type,handler_version,actor_kind"""
        ).fetchall()
        bindings: list[tuple[HandlerKey, ActorKind]] = []
        for row in rows:
            actor_kind = str(row["actor_kind"])
            if actor_kind not in ("user", "system"):
                raise JobRepositoryError(
                    "A non-terminal job has an invalid actor contract."
                )
            bindings.append(
                (
                    HandlerKey(str(row["job_type"]), str(row["handler_version"])),
                    actor_kind,
                )
            )
        return tuple(bindings)

    def verify_integrity(self, connection: ManagedConnection) -> int:
        """Recanonicalize and rehash every durable job in this snapshot."""

        connection = _require_transaction(connection)
        rows = connection.execute(
            f"SELECT {_JOB_COLUMNS} FROM durable_job ORDER BY id"
        ).fetchall()
        for row in rows:
            _row_to_record(row)
        return len(rows)

    def _get_by_effect_key(
        self, connection: ManagedConnection, effect_key: str
    ) -> DurableJobRecord | None:
        row = connection.execute(
            f"SELECT {_JOB_COLUMNS} FROM durable_job WHERE effect_key=?",
            (effect_key,),
        ).fetchone()
        return None if row is None else _row_to_record(row)

    def _resolve_enqueue_identity(
        self,
        connection: ManagedConnection,
        prepared: _PreparedEnqueue,
    ) -> DurableJobRecord | None:
        """Resolve an existing exact enqueue without re-running insert guards."""

        value = prepared.draft
        by_id = self.get(connection, value.job_id)
        by_effect = self._get_by_effect_key(connection, value.effect_key)
        if by_id is not None and by_effect is not None and by_id.id != by_effect.id:
            raise JobEffectConflictError(
                "The job ID and effect key identify different durable jobs."
            )
        record = by_id or by_effect
        if record is not None and _record_identity(record) != _prepared_identity(
            prepared
        ):
            raise JobEffectConflictError(
                "The job ID or effect key is already bound to different immutable input."
            )
        return record

    def enqueue(
        self, connection: ManagedConnection, draft: JobEnqueueDraft
    ) -> EnqueueResult:
        connection = _require_transaction(connection)
        prepared = _prepare_enqueue(draft)
        value = prepared.draft
        observed_now = db_now_ms(connection)
        if value.created_at_ms is not None and value.created_at_ms != observed_now:
            raise ValueError(
                "created_at_ms must equal the database time observed by enqueue."
            )
        value = replace(value, created_at_ms=observed_now)
        prepared = replace(prepared, draft=value)
        existing = self._resolve_enqueue_identity(connection, prepared)
        if existing is not None:
            return EnqueueResult(job=existing, inserted=False)
        if value.dedup_id is not None:
            operation = connection.execute(
                """SELECT status FROM operation_dedup
                     WHERE id=? AND actor_user_id=? AND operation_type=?
                       AND operation_id=? AND request_sha256=?""",
                (
                    value.dedup_id,
                    value.actor_user_id,
                    f"job:{value.job_type}",
                    value.operation_id,
                    prepared.input_sha256,
                ),
            ).fetchone()
            if operation is None:
                raise JobEffectConflictError(
                    "The job operation identity does not match its dedup reservation."
                )
            if str(operation["status"]) != "in_progress":
                raise JobStateConflictError(
                    "A terminal operation cannot enqueue new durable work."
                )
        try:
            cursor = connection.execute(
                """INSERT INTO durable_job(
                     id,dedup_id,operation_id,actor_kind,actor_user_id,space_id,
                     job_type,handler_version,effect_key,input_ref_json,input_sha256,
                     expected_state_json,authorization_snapshot_json,
                     authorization_snapshot_sha256,authorization_scope_epoch,
                     authorization_part_id,authorization_capabilities_json,state,
                     claim_owner,claim_expires_at,attempt_count,max_attempts,
                     cancellable,cancel_requested,progress_permille,result_json,
                     error_json,available_at,created_at,updated_at,completed_at
                   ) VALUES (?,?,?,?,?,?, ?,?,?,?,?, ?,?,?,?,?,?,'queued',
                     NULL,NULL,0,?, ?,0,0,NULL,NULL,?,?,?,NULL)
                   ON CONFLICT DO NOTHING""",
                (
                    value.job_id,
                    value.dedup_id,
                    value.operation_id,
                    value.actor_kind,
                    value.actor_user_id,
                    value.space_id,
                    value.job_type,
                    value.handler_version,
                    value.effect_key,
                    prepared.input_ref_json,
                    prepared.input_sha256,
                    prepared.expected_state_json,
                    prepared.authorization_snapshot_json,
                    prepared.authorization_snapshot_sha256,
                    value.authorization_scope_epoch,
                    value.authorization_part_id,
                    prepared.authorization_capabilities_json,
                    value.max_attempts,
                    1 if value.cancellable else 0,
                    value.available_at_ms,
                    value.created_at_ms,
                    value.created_at_ms,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise JobEffectConflictError(
                "The durable job conflicts with canonical actor, handler, or input state."
            ) from exc

        record = self._resolve_enqueue_identity(connection, prepared)
        if record is None:
            raise JobEffectConflictError(
                "A unique durable-job identity is already bound to another job."
            )
        return EnqueueResult(job=record, inserted=cursor.rowcount == 1)

    def recover_expired(
        self,
        connection: ManagedConnection,
        *,
        now_ms: int,
        error: Mapping[str, object],
    ) -> RecoveryResult:
        connection = _require_transaction(connection)
        now_ms = _integer(now_ms, field="now_ms")
        error_json = _canonical_object(error, field="error")
        queued_cancel_rows = connection.execute(
            """SELECT id FROM durable_job
                WHERE state IN ('queued','retry') AND cancellable=1
                  AND cancel_requested=1 ORDER BY id"""
        ).fetchall()
        cancelled: list[str] = []
        for queued_row in queued_cancel_rows:
            queued_id = str(queued_row["id"])
            cursor = connection.execute(
                """UPDATE durable_job
                      SET state='cancelled',error_json=NULL,updated_at=?,completed_at=?
                    WHERE id=? AND state IN ('queued','retry')
                      AND cancellable=1 AND cancel_requested=1""",
                (now_ms, now_ms, queued_id),
            )
            if cursor.rowcount == 1:
                cancelled.append(queued_id)
        rows = connection.execute(
            """SELECT id,state,claim_owner,claim_expires_at,attempt_count,max_attempts,
                      cancellable,cancel_requested
                 FROM durable_job
                WHERE state IN ('claimed','running') AND claim_expires_at<=?
                ORDER BY claim_expires_at,id""",
            (now_ms,),
        ).fetchall()
        retried: list[str] = []
        dead: list[str] = []
        for row in rows:
            job_id = str(row["id"])
            if bool(row["cancel_requested"]):
                cursor = connection.execute(
                    """UPDATE durable_job
                          SET state='cancelled',claim_owner=NULL,
                              claim_expires_at=NULL,error_json=NULL,
                              updated_at=?,completed_at=?
                        WHERE id=? AND state=? AND claim_owner=?
                          AND claim_expires_at=? AND claim_expires_at<=?
                          AND attempt_count=? AND cancellable=1
                          AND cancel_requested=1""",
                    (
                        now_ms,
                        now_ms,
                        job_id,
                        row["state"],
                        row["claim_owner"],
                        row["claim_expires_at"],
                        now_ms,
                        row["attempt_count"],
                    ),
                )
                if cursor.rowcount == 1:
                    cancelled.append(job_id)
                continue
            cursor = connection.execute(
                """UPDATE durable_job
                      SET state='retry',claim_owner=NULL,claim_expires_at=NULL,
                          error_json=?,available_at=?,updated_at=?
                    WHERE id=? AND state=? AND claim_owner=?
                      AND claim_expires_at=? AND claim_expires_at<=?
                      AND attempt_count=?""",
                (
                    error_json,
                    now_ms,
                    now_ms,
                    job_id,
                    row["state"],
                    row["claim_owner"],
                    row["claim_expires_at"],
                    now_ms,
                    row["attempt_count"],
                ),
            )
            if cursor.rowcount != 1:
                continue
            if int(row["attempt_count"]) == int(row["max_attempts"]):
                final = connection.execute(
                    """UPDATE durable_job
                          SET state='dead',completed_at=?,updated_at=?
                        WHERE id=? AND state='retry'
                          AND attempt_count=max_attempts""",
                    (now_ms, now_ms, job_id),
                )
                if final.rowcount != 1:
                    raise JobRepositoryError(
                        "An exhausted expired job could not become dead."
                    )
                dead.append(job_id)
            else:
                retried.append(job_id)
        return RecoveryResult(tuple(retried), tuple(dead), tuple(cancelled))

    def claim_next(
        self,
        connection: ManagedConnection,
        *,
        handler_keys: Collection[HandlerKey],
        claim_owner: str,
        lease_ms: int,
        now_ms: int,
        avoid_actor: tuple[ActorKind, str | None] | None = None,
        durable_fairness: bool = False,
    ) -> ClaimResult | None:
        connection = _require_transaction(connection)
        keys = _handler_keys(handler_keys)
        if not keys:
            return None
        claim_owner = _required_text(
            claim_owner, field="claim_owner", maximum=200
        )
        lease_ms = _integer(lease_ms, field="lease_ms", minimum=1)
        now_ms = _integer(now_ms, field="now_ms")
        expires_at = _integer(
            now_ms + lease_ms, field="claim_expires_at_ms"
        )
        handler_clause = " OR ".join(
            "(candidate.job_type=? AND candidate.handler_version=?)" for _ in keys
        )
        handler_parameters = tuple(
            part for key in keys for part in (key.job_type, key.handler_version)
        )
        if not isinstance(durable_fairness, bool):
            raise ValueError("durable_fairness must be boolean.")
        fairness_order = ""
        fairness_parameters: tuple[object, ...] = ()
        if durable_fairness:
            # A claimed attempt is durable scheduler history.  Prefer the
            # exact handler and actor lane whose most recent attempt is oldest;
            # attempt_count=0 queued rows do not pretend they were served.
            # This survives worker restarts without a second scheduler table.
            fairness_order += """
                COALESCE((
                    SELECT MAX(handler_history.updated_at)
                      FROM durable_job AS handler_history
                     WHERE handler_history.job_type=candidate.job_type
                       AND handler_history.handler_version=candidate.handler_version
                       AND handler_history.attempt_count>0
                ),-1),
                COALESCE((
                    SELECT MAX(actor_history.updated_at)
                      FROM durable_job AS actor_history
                     WHERE actor_history.job_type=candidate.job_type
                       AND actor_history.handler_version=candidate.handler_version
                       AND actor_history.actor_kind=candidate.actor_kind
                       AND actor_history.actor_user_id IS candidate.actor_user_id
                       AND actor_history.attempt_count>0
                ),-1),
            """
        if avoid_actor is not None:
            if not isinstance(avoid_actor, tuple) or len(avoid_actor) != 2:
                raise ValueError("avoid_actor must be an actor-kind/user-id pair.")
            avoid_kind, avoid_user_id = avoid_actor
            if avoid_kind == "system":
                if avoid_user_id is not None:
                    raise ValueError("A system fairness lane cannot have a user ID.")
            elif avoid_kind == "user":
                if (
                    not isinstance(avoid_user_id, str)
                    or _USER_ID_RE.fullmatch(avoid_user_id) is None
                ):
                    raise ValueError("A user fairness lane requires a canonical user ID.")
            else:
                raise ValueError("The fairness actor kind is invalid.")
            fairness_order += (
                "CASE WHEN candidate.actor_kind=? "
                "AND candidate.actor_user_id IS ? THEN 1 ELSE 0 END,"
            )
            fairness_parameters = (avoid_kind, avoid_user_id)
        row = connection.execute(
            f"""UPDATE durable_job
                   SET state='claimed',claim_owner=?,claim_expires_at=?,
                       attempt_count=attempt_count+1,updated_at=?
                 WHERE id=(
                   SELECT candidate.id FROM durable_job AS candidate
                    WHERE candidate.state IN ('queued','retry')
                      AND candidate.available_at<=?
                      AND candidate.cancel_requested=0
                      AND candidate.attempt_count<candidate.max_attempts
                      AND ({handler_clause})
                    ORDER BY {fairness_order}candidate.available_at,candidate.id LIMIT 1
                 )
                   AND state IN ('queued','retry') AND available_at<=?
                   AND cancel_requested=0 AND attempt_count<max_attempts
                 RETURNING {_JOB_COLUMNS}""",
            (
                claim_owner,
                expires_at,
                now_ms,
                now_ms,
                *handler_parameters,
                *fairness_parameters,
                now_ms,
            ),
        ).fetchone()
        if row is None:
            return None
        job = _row_to_record(row)
        return ClaimResult(
            job=job,
            fence=ClaimFence(job.id, claim_owner, job.attempt_count),
        )

    def _raise_worker_cas(
        self,
        connection: ManagedConnection,
        fence: ClaimFence,
        *,
        now_ms: int,
        expected_states: tuple[str, ...],
        require_cancel: bool | None,
    ) -> None:
        current = self.require(connection, fence.job_id)
        if current.cancel_requested and require_cancel is False:
            raise JobCancellationRequestedError(
                "Cancellation was requested before the worker mutation committed."
            )
        if require_cancel is True and not current.cancel_requested:
            raise JobStateConflictError(
                "The durable job has no cancellation request to acknowledge."
            )
        if current.state not in expected_states:
            raise JobStateConflictError(
                "The durable job is not in a legal state for this transition."
            )
        if (
            current.claim_owner != fence.claim_owner
            or current.attempt_count != fence.attempt_count
            or current.claim_expires_at_ms is None
            or current.claim_expires_at_ms <= now_ms
        ):
            raise JobClaimLostError(
                "The durable job claim owner, attempt fence, or lease is no longer valid."
            )
        raise JobStateConflictError("The durable job compare-and-swap was rejected.")

    def assert_effect_fence(
        self,
        connection: ManagedConnection,
        *,
        fence: ClaimFence,
        now_ms: int,
    ) -> DurableJobRecord:
        """Prove a running attempt may enter its domain-effect callback now.

        This read fence is intentionally called immediately before the domain
        mutation inside the same IMMEDIATE transaction.  ``succeed`` repeats
        the same predicates after the callback so the domain effect and the
        terminal result commit together.
        """

        connection = _require_transaction(connection)
        fence = _fence(fence)
        now_ms = _integer(now_ms, field="now_ms")
        row = connection.execute(
            f"""SELECT {_JOB_COLUMNS} FROM durable_job
                 WHERE id=? AND state='running' AND claim_owner=?
                   AND attempt_count=? AND claim_expires_at>?
                   AND cancel_requested=0""",
            (
                fence.job_id,
                fence.claim_owner,
                fence.attempt_count,
                now_ms,
            ),
        ).fetchone()
        if row is None:
            self._raise_worker_cas(
                connection,
                fence,
                now_ms=now_ms,
                expected_states=("running",),
                require_cancel=False,
            )
        return _row_to_record(row)

    def start(
        self,
        connection: ManagedConnection,
        fence: ClaimFence,
        *,
        now_ms: int,
    ) -> DurableJobRecord:
        connection = _require_transaction(connection)
        fence = _fence(fence)
        now_ms = _integer(now_ms, field="now_ms")
        cursor = connection.execute(
            """UPDATE durable_job SET state='running',updated_at=?
                 WHERE id=? AND state='claimed' AND claim_owner=?
                   AND attempt_count=? AND claim_expires_at>?
                   AND cancel_requested=0 AND updated_at<=?""",
            (
                now_ms,
                fence.job_id,
                fence.claim_owner,
                fence.attempt_count,
                now_ms,
                now_ms,
            ),
        )
        if cursor.rowcount != 1:
            self._raise_worker_cas(
                connection,
                fence,
                now_ms=now_ms,
                expected_states=("claimed",),
                require_cancel=False,
            )
        return self.require(connection, fence.job_id)

    def renew(
        self,
        connection: ManagedConnection,
        fence: ClaimFence,
        *,
        lease_ms: int,
        now_ms: int,
    ) -> DurableJobRecord:
        connection = _require_transaction(connection)
        fence = _fence(fence)
        lease_ms = _integer(lease_ms, field="lease_ms", minimum=1)
        now_ms = _integer(now_ms, field="now_ms")
        proposed = _integer(now_ms + lease_ms, field="claim_expires_at_ms")
        cursor = connection.execute(
            """UPDATE durable_job
                  SET claim_expires_at=max(claim_expires_at,?),updated_at=?
                WHERE id=? AND state IN ('claimed','running') AND claim_owner=?
                  AND attempt_count=? AND claim_expires_at>?
                  AND cancel_requested=0 AND updated_at<=?""",
            (
                proposed,
                now_ms,
                fence.job_id,
                fence.claim_owner,
                fence.attempt_count,
                now_ms,
                now_ms,
            ),
        )
        if cursor.rowcount != 1:
            self._raise_worker_cas(
                connection,
                fence,
                now_ms=now_ms,
                expected_states=_WORKER_STATES,
                require_cancel=False,
            )
        return self.require(connection, fence.job_id)

    def set_progress(
        self,
        connection: ManagedConnection,
        fence: ClaimFence,
        *,
        progress_permille: int,
        now_ms: int,
    ) -> DurableJobRecord:
        connection = _require_transaction(connection)
        fence = _fence(fence)
        progress = _integer(
            progress_permille, field="progress_permille", maximum=1000
        )
        now_ms = _integer(now_ms, field="now_ms")
        cursor = connection.execute(
            """UPDATE durable_job SET progress_permille=?,updated_at=?
                 WHERE id=? AND state='running' AND claim_owner=?
                   AND attempt_count=? AND claim_expires_at>?
                   AND cancel_requested=0 AND progress_permille<=?
                   AND updated_at<=?""",
            (
                progress,
                now_ms,
                fence.job_id,
                fence.claim_owner,
                fence.attempt_count,
                now_ms,
                progress,
                now_ms,
            ),
        )
        if cursor.rowcount != 1:
            current = self.require(connection, fence.job_id)
            if current.progress_permille > progress:
                raise JobStateConflictError("Durable job progress cannot move backward.")
            self._raise_worker_cas(
                connection,
                fence,
                now_ms=now_ms,
                expected_states=("running",),
                require_cancel=False,
            )
        return self.require(connection, fence.job_id)

    def request_cancel(
        self,
        connection: ManagedConnection,
        *,
        job_id: str,
        now_ms: int,
    ) -> CancelResult:
        connection = _require_transaction(connection)
        job_id = _canonical_uuid(job_id, field="job_id")  # type: ignore[assignment]
        now_ms = _integer(now_ms, field="now_ms")
        current = self.require(connection, job_id)
        if current.state in _TERMINAL_STATES:
            return CancelResult(
                job=current,
                changed=False,
                immediate=current.state == "cancelled",
            )
        if not current.cancellable:
            raise JobCancellationUnsupportedError(
                "This durable job does not permit cancellation."
            )
        if current.state in ("queued", "retry"):
            cursor = connection.execute(
                """UPDATE durable_job
                      SET state='cancelled',cancel_requested=1,updated_at=?,
                          completed_at=?,error_json=NULL
                    WHERE id=? AND state=?
                      AND cancellable=1 AND updated_at<=?""",
                (now_ms, now_ms, job_id, current.state, now_ms),
            )
            immediate = True
        else:
            if current.cancel_requested:
                return CancelResult(job=current, changed=False, immediate=False)
            cursor = connection.execute(
                """UPDATE durable_job SET cancel_requested=1,updated_at=?
                     WHERE id=? AND state IN ('claimed','running')
                       AND cancel_requested=0 AND cancellable=1
                       AND updated_at<=?""",
                (now_ms, job_id, now_ms),
            )
            immediate = False
        if cursor.rowcount != 1:
            raise JobStateConflictError(
                "The durable job changed before cancellation could be requested."
            )
        return CancelResult(
            job=self.require(connection, job_id),
            changed=True,
            immediate=immediate,
        )

    def acknowledge_cancel(
        self,
        connection: ManagedConnection,
        fence: ClaimFence,
        *,
        now_ms: int,
    ) -> DurableJobRecord:
        connection = _require_transaction(connection)
        fence = _fence(fence)
        now_ms = _integer(now_ms, field="now_ms")
        cursor = connection.execute(
            """UPDATE durable_job
                  SET state='cancelled',claim_owner=NULL,claim_expires_at=NULL,
                      error_json=NULL,updated_at=?,completed_at=?
                WHERE id=? AND state IN ('claimed','running') AND claim_owner=?
                  AND attempt_count=? AND claim_expires_at>?
                  AND cancel_requested=1 AND updated_at<=?""",
            (
                now_ms,
                now_ms,
                fence.job_id,
                fence.claim_owner,
                fence.attempt_count,
                now_ms,
                now_ms,
            ),
        )
        if cursor.rowcount != 1:
            self._raise_worker_cas(
                connection,
                fence,
                now_ms=now_ms,
                expected_states=_WORKER_STATES,
                require_cancel=True,
            )
        return self.require(connection, fence.job_id)

    def retry_or_dead(
        self,
        connection: ManagedConnection,
        fence: ClaimFence,
        *,
        error: Mapping[str, object],
        delay_ms: int,
        now_ms: int,
    ) -> DurableJobRecord:
        connection = _require_transaction(connection)
        fence = _fence(fence)
        error_json = _canonical_object(error, field="error")
        delay_ms = _integer(delay_ms, field="delay_ms")
        now_ms = _integer(now_ms, field="now_ms")
        available_at = _integer(now_ms + delay_ms, field="available_at_ms")
        cursor = connection.execute(
            """UPDATE durable_job
                  SET state='retry',claim_owner=NULL,claim_expires_at=NULL,
                      error_json=?,available_at=?,updated_at=?
                WHERE id=? AND state IN ('claimed','running') AND claim_owner=?
                  AND attempt_count=? AND claim_expires_at>?
                  AND cancel_requested=0 AND updated_at<=?""",
            (
                error_json,
                available_at,
                now_ms,
                fence.job_id,
                fence.claim_owner,
                fence.attempt_count,
                now_ms,
                now_ms,
            ),
        )
        if cursor.rowcount != 1:
            self._raise_worker_cas(
                connection,
                fence,
                now_ms=now_ms,
                expected_states=_WORKER_STATES,
                require_cancel=False,
            )
        current = self.require(connection, fence.job_id)
        if current.attempt_count == current.max_attempts:
            final = connection.execute(
                """UPDATE durable_job SET state='dead',completed_at=?,updated_at=?
                     WHERE id=? AND state='retry'
                       AND attempt_count=max_attempts""",
                (now_ms, now_ms, fence.job_id),
            )
            if final.rowcount != 1:
                raise JobRepositoryError("The exhausted durable job could not become dead.")
            current = self.require(connection, fence.job_id)
        return current

    def fail(
        self,
        connection: ManagedConnection,
        fence: ClaimFence,
        *,
        error: Mapping[str, object],
        now_ms: int,
    ) -> DurableJobRecord:
        connection = _require_transaction(connection)
        fence = _fence(fence)
        error_json = _canonical_object(error, field="error")
        now_ms = _integer(now_ms, field="now_ms")
        cursor = connection.execute(
            """UPDATE durable_job
                  SET state='failed',claim_owner=NULL,claim_expires_at=NULL,
                      error_json=?,updated_at=?,completed_at=?
                WHERE id=? AND state='running' AND claim_owner=?
                  AND attempt_count=? AND claim_expires_at>?
                  AND cancel_requested=0 AND updated_at<=?""",
            (
                error_json,
                now_ms,
                now_ms,
                fence.job_id,
                fence.claim_owner,
                fence.attempt_count,
                now_ms,
                now_ms,
            ),
        )
        if cursor.rowcount != 1:
            self._raise_worker_cas(
                connection,
                fence,
                now_ms=now_ms,
                expected_states=("running",),
                require_cancel=False,
            )
        return self.require(connection, fence.job_id)

    def succeed(
        self,
        connection: ManagedConnection,
        fence: ClaimFence,
        *,
        result: Mapping[str, object],
        now_ms: int,
    ) -> DurableJobRecord:
        connection = _require_transaction(connection)
        fence = _fence(fence)
        result_json = _canonical_object(result, field="result")
        now_ms = _integer(now_ms, field="now_ms")
        existing = self.require(connection, fence.job_id)
        if existing.state == "succeeded":
            if existing.attempt_count != fence.attempt_count:
                raise JobClaimLostError(
                    "The completed durable job belongs to another attempt fence."
                )
            if existing.result_json != result_json:
                raise JobEffectConflictError(
                    "The completed effect key is bound to a different result."
                )
            return existing
        cursor = connection.execute(
            """UPDATE durable_job
                  SET state='succeeded',claim_owner=NULL,claim_expires_at=NULL,
                      progress_permille=1000,result_json=?,error_json=NULL,
                      updated_at=?,completed_at=?
                WHERE id=? AND state='running' AND claim_owner=?
                  AND attempt_count=? AND claim_expires_at>?
                  AND cancel_requested=0 AND updated_at<=?""",
            (
                result_json,
                now_ms,
                now_ms,
                fence.job_id,
                fence.claim_owner,
                fence.attempt_count,
                now_ms,
                now_ms,
            ),
        )
        if cursor.rowcount != 1:
            current = self.require(connection, fence.job_id)
            if current.state == "succeeded":
                if current.attempt_count != fence.attempt_count:
                    raise JobClaimLostError(
                        "The completed durable job belongs to another attempt fence."
                    )
                if current.result_json != result_json:
                    raise JobEffectConflictError(
                        "The completed effect key is bound to a different result."
                    )
                return current
            self._raise_worker_cas(
                connection,
                fence,
                now_ms=now_ms,
                expected_states=("running",),
                require_cancel=False,
            )
        return self.require(connection, fence.job_id)


DurableJobRepository = JobRepository


__all__ = [
    "ActorKind",
    "CancelResult",
    "ClaimFence",
    "ClaimResult",
    "DurableJobRecord",
    "DurableJobRepository",
    "EnqueueResult",
    "HandlerKey",
    "JobCancellationRequestedError",
    "JobCancellationUnsupportedError",
    "JobClaimLostError",
    "JobEffectConflictError",
    "JobEnqueueDraft",
    "JobNotFoundError",
    "JobRepository",
    "JobRepositoryError",
    "JobState",
    "JobStateConflictError",
    "RecoveryResult",
    "canonical_job_input_sha256",
]
