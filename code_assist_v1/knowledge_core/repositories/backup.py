"""Durable backup-run and immutable-blob hold state machine.

This repository owns only short canonical SQLite transactions.  Snapshot and
blob filesystem I/O belongs to the P7.4 backup service and must complete
outside these calls.  A ``preparing`` run is the global GC fence; the atomic
transition to ``copying`` installs the exact per-blob hold set before that
global fence can ever become terminal.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
import uuid
from typing import Iterable

from ..db import ManagedConnection
from ..errors import KnowledgeCoreDatabaseError, KnowledgeCoreTransactionError
from ..runtime import APPROVED_SQLITE_SOURCE_ID


_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_ERROR_CODE_RE = re.compile(r"[A-Z][A-Z0-9_]{0,79}\Z", re.ASCII)
_SNAPSHOT_PATH = "database/knowledge-core.sqlite"


class BackupRepositoryError(KnowledgeCoreDatabaseError):
    """Base class for durable backup-ledger failures."""

    code = "KNOWLEDGE_CORE_BACKUP_LEDGER_ERROR"


class BackupIntegrityError(BackupRepositoryError):
    """Stored backup state conflicts with the requested or derived state."""

    code = "KNOWLEDGE_CORE_BACKUP_INTEGRITY_ERROR"


class BackupConflictError(BackupRepositoryError):
    """A run identifier or transition was reused with different facts."""

    code = "KNOWLEDGE_CORE_BACKUP_CONFLICT"


@dataclass(frozen=True, slots=True, order=True)
class BackupBlobSpec:
    sha256: str
    size_bytes: int

    def __post_init__(self) -> None:
        _sha256(self.sha256, field="sha256")
        _nonnegative_integer(self.size_bytes, field="size_bytes")


@dataclass(frozen=True, slots=True)
class BackupBlobRecord:
    backup_run_id: str
    blob_sha256: str
    size_bytes: int
    status: str
    copied_at_ms: int | None
    verified_at_ms: int | None
    error_code: str | None


@dataclass(frozen=True, slots=True)
class BackupRunRecord:
    id: str
    state: str
    snapshot_db_relative_path: str | None
    snapshot_db_sha256: str | None
    manifest_sha256: str | None
    snapshot_audit_high_water: int
    schema_version: int
    sqlite_source_id: str
    error_code: str | None
    created_at_ms: int
    completed_at_ms: int | None
    row_version: int


def _write_connection(value: object) -> ManagedConnection:
    if not isinstance(value, ManagedConnection):
        raise TypeError("BackupRepository requires a ManagedConnection.")
    if not value.in_transaction:
        raise KnowledgeCoreTransactionError(
            "Backup mutation requires an existing managed transaction."
        )
    return value


def _canonical_uuid(value: object, *, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a canonical UUID string.")
    try:
        canonical = str(uuid.UUID(value))
    except (ValueError, AttributeError) as exc:
        raise ValueError(f"{field} must be a canonical UUID string.") from exc
    if canonical != value:
        raise ValueError(f"{field} must be a canonical UUID string.")
    return value


def _sha256(value: object, *, field: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"{field} must be a lowercase SHA-256 digest.")
    return value


def _nonnegative_integer(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer.")
    return value


def _error_json(code: str) -> str:
    if not isinstance(code, str) or _ERROR_CODE_RE.fullmatch(code) is None:
        raise ValueError("error_code must be a stable uppercase identifier.")
    return json.dumps(
        {"code": code, "schema_version": 1},
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def _decode_error(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise BackupIntegrityError("Stored backup error evidence is invalid.")
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError) as exc:
        raise BackupIntegrityError("Stored backup error evidence is invalid.") from exc
    if (
        not isinstance(parsed, dict)
        or set(parsed) != {"code", "schema_version"}
        or parsed.get("schema_version") != 1
        or not isinstance(parsed.get("code"), str)
        or _ERROR_CODE_RE.fullmatch(parsed["code"]) is None
        or _error_json(parsed["code"]) != value
    ):
        raise BackupIntegrityError("Stored backup error evidence is invalid.")
    return parsed["code"]


def _run_record(row: object) -> BackupRunRecord:
    if row is None:
        raise BackupIntegrityError("The backup run is unavailable.")
    values = tuple(row)  # type: ignore[arg-type]
    if len(values) != 12:
        raise BackupIntegrityError("Stored backup-run shape is invalid.")
    record = BackupRunRecord(
        id=str(values[0]),
        state=str(values[1]),
        snapshot_db_relative_path=(
            None if values[2] is None else str(values[2])
        ),
        snapshot_db_sha256=None if values[3] is None else str(values[3]),
        manifest_sha256=None if values[4] is None else str(values[4]),
        snapshot_audit_high_water=int(values[5]),
        schema_version=int(values[6]),
        sqlite_source_id=str(values[7]),
        error_code=_decode_error(values[8]),
        created_at_ms=int(values[9]),
        completed_at_ms=None if values[10] is None else int(values[10]),
        row_version=int(values[11]),
    )
    try:
        _canonical_uuid(record.id, field="backup_run_id")
        _nonnegative_integer(
            record.snapshot_audit_high_water, field="snapshot_audit_high_water"
        )
        _nonnegative_integer(record.created_at_ms, field="created_at_ms")
        if record.completed_at_ms is not None:
            _nonnegative_integer(record.completed_at_ms, field="completed_at_ms")
        if record.snapshot_db_sha256 is not None:
            _sha256(record.snapshot_db_sha256, field="snapshot_db_sha256")
        if record.manifest_sha256 is not None:
            _sha256(record.manifest_sha256, field="manifest_sha256")
    except ValueError as exc:
        raise BackupIntegrityError("Stored backup-run fields are invalid.") from exc
    if (
        record.state not in {"preparing", "copying", "verified", "failed"}
        or record.schema_version != 1
        or record.sqlite_source_id != APPROVED_SQLITE_SOURCE_ID
        or record.row_version < 1
    ):
        raise BackupIntegrityError("Stored backup-run contract is invalid.")
    return record


def _blob_record(row: object) -> BackupBlobRecord:
    values = tuple(row)  # type: ignore[arg-type]
    if len(values) != 7:
        raise BackupIntegrityError("Stored backup-blob shape is invalid.")
    record = BackupBlobRecord(
        backup_run_id=str(values[0]),
        blob_sha256=str(values[1]),
        size_bytes=int(values[2]),
        status=str(values[3]),
        copied_at_ms=None if values[4] is None else int(values[4]),
        verified_at_ms=None if values[5] is None else int(values[5]),
        error_code=_decode_error(values[6]),
    )
    try:
        _canonical_uuid(record.backup_run_id, field="backup_run_id")
        _sha256(record.blob_sha256, field="blob_sha256")
        _nonnegative_integer(record.size_bytes, field="size_bytes")
        if record.copied_at_ms is not None:
            _nonnegative_integer(record.copied_at_ms, field="copied_at_ms")
        if record.verified_at_ms is not None:
            _nonnegative_integer(record.verified_at_ms, field="verified_at_ms")
    except ValueError as exc:
        raise BackupIntegrityError("Stored backup-blob fields are invalid.") from exc
    if record.status not in {"pending", "verified", "failed"}:
        raise BackupIntegrityError("Stored backup-blob status is invalid.")
    return record


def _specs(values: Iterable[BackupBlobSpec]) -> tuple[BackupBlobSpec, ...]:
    try:
        result = tuple(values)
    except TypeError as exc:
        raise TypeError("blobs must be an iterable of BackupBlobSpec values.") from exc
    if any(not isinstance(value, BackupBlobSpec) for value in result):
        raise TypeError("blobs must contain only BackupBlobSpec values.")
    ordered = tuple(sorted(result))
    if len({value.sha256 for value in ordered}) != len(ordered):
        raise ValueError("A backup blob digest may appear only once.")
    return ordered


_RUN_SELECT = """SELECT id,state,snapshot_db_relative_path,snapshot_db_sha256,
                        manifest_sha256,snapshot_audit_high_water,schema_version,
                        sqlite_source_id,error_json,created_at,completed_at,row_version
                   FROM backup_run WHERE id=?"""

_BLOB_SELECT = """SELECT backup_run_id,blob_sha256,size_bytes,status,copied_at,
                         verified_at,error_json
                    FROM backup_blob WHERE backup_run_id=?
                   ORDER BY blob_sha256"""


class BackupRepository:
    """Connection-scoped P7.4 backup ledger with exact replay semantics."""

    def get(self, connection: ManagedConnection, backup_run_id: str) -> BackupRunRecord | None:
        run_id = _canonical_uuid(backup_run_id, field="backup_run_id")
        row = connection.execute(_RUN_SELECT, (run_id,)).fetchone()
        return None if row is None else _run_record(row)

    def list_blobs(
        self, connection: ManagedConnection, backup_run_id: str
    ) -> tuple[BackupBlobRecord, ...]:
        run_id = _canonical_uuid(backup_run_id, field="backup_run_id")
        return tuple(
            _blob_record(row)
            for row in connection.execute(_BLOB_SELECT, (run_id,)).fetchall()
        )

    def begin(
        self,
        connection: ManagedConnection,
        *,
        backup_run_id: str,
        created_at_ms: int,
    ) -> BackupRunRecord:
        connection = _write_connection(connection)
        run_id = _canonical_uuid(backup_run_id, field="backup_run_id")
        created_at = _nonnegative_integer(created_at_ms, field="created_at_ms")
        existing = self.get(connection, run_id)
        if existing is not None:
            if existing.created_at_ms != created_at:
                raise BackupConflictError(
                    "The backup run identifier is bound to another creation time."
                )
            return existing
        active = connection.execute(
            """SELECT id FROM backup_run
                 WHERE state IN ('preparing','copying')
                 ORDER BY id
                 LIMIT 1"""
        ).fetchone()
        if active is not None:
            raise BackupConflictError(
                "Another backup run already owns the global GC hold."
            )
        connection.execute(
            """INSERT INTO backup_run(
                   id,state,snapshot_audit_high_water,schema_version,sqlite_source_id,
                   error_json,created_at,completed_at,row_version
               ) VALUES (?,'preparing',0,1,?,NULL,?,NULL,1)""",
            (run_id, APPROVED_SQLITE_SOURCE_ID, created_at),
        )
        return _run_record(connection.execute(_RUN_SELECT, (run_id,)).fetchone())

    def begin_copying(
        self,
        connection: ManagedConnection,
        *,
        backup_run_id: str,
        snapshot_db_sha256: str,
        snapshot_audit_high_water: int,
        blobs: Iterable[BackupBlobSpec],
    ) -> BackupRunRecord:
        connection = _write_connection(connection)
        run_id = _canonical_uuid(backup_run_id, field="backup_run_id")
        snapshot_sha = _sha256(
            snapshot_db_sha256, field="snapshot_db_sha256"
        )
        audit_high_water = _nonnegative_integer(
            snapshot_audit_high_water, field="snapshot_audit_high_water"
        )
        expected_blobs = _specs(blobs)
        run = self.get(connection, run_id)
        if run is None:
            raise BackupConflictError("The backup run has not acquired its GC hold.")
        if run.state == "copying":
            observed = self.list_blobs(connection, run_id)
            if (
                run.snapshot_db_relative_path != _SNAPSHOT_PATH
                or run.snapshot_db_sha256 != snapshot_sha
                or run.snapshot_audit_high_water != audit_high_water
                or tuple(
                    BackupBlobSpec(value.blob_sha256, value.size_bytes)
                    for value in observed
                )
                != expected_blobs
            ):
                raise BackupConflictError(
                    "The copying backup run differs from the observed snapshot."
                )
            return run
        if run.state != "preparing":
            raise BackupConflictError("Only a preparing backup may begin copying.")
        if self.list_blobs(connection, run_id):
            raise BackupIntegrityError(
                "A preparing backup already contains an incomplete blob hold set."
            )
        connection.executemany(
            """INSERT INTO backup_blob(
                   backup_run_id,blob_sha256,size_bytes,status,copied_at,
                   verified_at,error_json
               ) VALUES (?, ?, ?, 'pending', NULL, NULL, NULL)""",
            ((run_id, value.sha256, value.size_bytes) for value in expected_blobs),
        )
        cursor = connection.execute(
            """UPDATE backup_run
                  SET state='copying',snapshot_db_relative_path=?,
                      snapshot_db_sha256=?,snapshot_audit_high_water=?,
                      row_version=row_version+1
                WHERE id=? AND state='preparing' AND row_version=?""",
            (_SNAPSHOT_PATH, snapshot_sha, audit_high_water, run_id, run.row_version),
        )
        if cursor.rowcount != 1:
            raise BackupConflictError("The backup hold changed before copy transition.")
        return _run_record(connection.execute(_RUN_SELECT, (run_id,)).fetchone())

    def mark_blob_verified(
        self,
        connection: ManagedConnection,
        *,
        backup_run_id: str,
        blob_sha256: str,
        copied_at_ms: int,
        verified_at_ms: int,
    ) -> BackupBlobRecord:
        connection = _write_connection(connection)
        run_id = _canonical_uuid(backup_run_id, field="backup_run_id")
        digest = _sha256(blob_sha256, field="blob_sha256")
        copied_at = _nonnegative_integer(copied_at_ms, field="copied_at_ms")
        verified_at = _nonnegative_integer(verified_at_ms, field="verified_at_ms")
        if verified_at < copied_at:
            raise ValueError("verified_at_ms cannot precede copied_at_ms.")
        run = self.get(connection, run_id)
        if run is None or run.state != "copying":
            raise BackupConflictError("Blob verification requires a copying backup.")
        row = connection.execute(
            _BLOB_SELECT.replace(
                "WHERE backup_run_id=?", "WHERE backup_run_id=? AND blob_sha256=?"
            ).replace("ORDER BY blob_sha256", ""),
            (run_id, digest),
        ).fetchone()
        if row is None:
            raise BackupConflictError("The blob is not part of this backup snapshot.")
        existing = _blob_record(row)
        if existing.status == "verified":
            if (
                existing.copied_at_ms != copied_at
                or existing.verified_at_ms != verified_at
            ):
                raise BackupConflictError(
                    "The verified backup blob has different completion evidence."
                )
            return existing
        if existing.status != "pending":
            raise BackupConflictError("A failed backup blob cannot be verified.")
        cursor = connection.execute(
            """UPDATE backup_blob
                  SET status='verified',copied_at=?,verified_at=?,error_json=NULL
                WHERE backup_run_id=? AND blob_sha256=? AND status='pending'""",
            (copied_at, verified_at, run_id, digest),
        )
        if cursor.rowcount != 1:
            raise BackupConflictError("The backup blob changed before verification.")
        return _blob_record(
            connection.execute(
                _BLOB_SELECT.replace(
                    "WHERE backup_run_id=?", "WHERE backup_run_id=? AND blob_sha256=?"
                ).replace("ORDER BY blob_sha256", ""),
                (run_id, digest),
            ).fetchone()
        )

    def mark_verified(
        self,
        connection: ManagedConnection,
        *,
        backup_run_id: str,
        manifest_sha256: str,
        completed_at_ms: int,
    ) -> BackupRunRecord:
        connection = _write_connection(connection)
        run_id = _canonical_uuid(backup_run_id, field="backup_run_id")
        manifest_sha = _sha256(manifest_sha256, field="manifest_sha256")
        completed_at = _nonnegative_integer(completed_at_ms, field="completed_at_ms")
        run = self.get(connection, run_id)
        if run is None:
            raise BackupConflictError("The backup run is unavailable.")
        if run.state == "verified":
            if (
                run.manifest_sha256 != manifest_sha
                or run.completed_at_ms != completed_at
            ):
                raise BackupConflictError(
                    "The verified backup run has different completion evidence."
                )
            return run
        if run.state != "copying":
            raise BackupConflictError("Only a copying backup may be verified.")
        if completed_at < run.created_at_ms:
            raise ValueError("completed_at_ms cannot precede created_at_ms.")
        incomplete = connection.execute(
            """SELECT count(*) FROM backup_blob
                 WHERE backup_run_id=? AND status<>'verified'""",
            (run_id,),
        ).fetchone()
        if incomplete is None or int(incomplete[0]) != 0:
            raise BackupIntegrityError(
                "Every referenced backup blob must be verified before finalization."
            )
        cursor = connection.execute(
            """UPDATE backup_run
                  SET state='verified',manifest_sha256=?,completed_at=?,
                      error_json=NULL,row_version=row_version+1
                WHERE id=? AND state='copying' AND row_version=?""",
            (manifest_sha, completed_at, run_id, run.row_version),
        )
        if cursor.rowcount != 1:
            raise BackupConflictError("The backup run changed before finalization.")
        return _run_record(connection.execute(_RUN_SELECT, (run_id,)).fetchone())

    def mark_failed(
        self,
        connection: ManagedConnection,
        *,
        backup_run_id: str,
        error_code: str,
        completed_at_ms: int,
    ) -> BackupRunRecord:
        connection = _write_connection(connection)
        run_id = _canonical_uuid(backup_run_id, field="backup_run_id")
        error = _error_json(error_code)
        completed_at = _nonnegative_integer(completed_at_ms, field="completed_at_ms")
        run = self.get(connection, run_id)
        if run is None:
            raise BackupConflictError("The backup run is unavailable.")
        if run.state == "failed":
            if (
                run.error_code != error_code
                or run.completed_at_ms != completed_at
            ):
                raise BackupConflictError(
                    "The failed backup run has different failure evidence."
                )
            return run
        if run.state == "verified":
            raise BackupConflictError("A verified backup cannot become failed.")
        if completed_at < run.created_at_ms:
            raise ValueError("completed_at_ms cannot precede created_at_ms.")
        connection.execute(
            """UPDATE backup_blob
                  SET status='failed',error_json=?
                WHERE backup_run_id=? AND status='pending'""",
            (error, run_id),
        )
        cursor = connection.execute(
            """UPDATE backup_run
                  SET state='failed',error_json=?,completed_at=?,
                      row_version=row_version+1
                WHERE id=? AND state IN ('preparing','copying') AND row_version=?""",
            (error, completed_at, run_id, run.row_version),
        )
        if cursor.rowcount != 1:
            raise BackupConflictError("The backup run changed before failure recording.")
        return _run_record(connection.execute(_RUN_SELECT, (run_id,)).fetchone())

    def latest_verified(self, connection: ManagedConnection) -> BackupRunRecord | None:
        row = connection.execute(
            """SELECT id,state,snapshot_db_relative_path,snapshot_db_sha256,
                      manifest_sha256,snapshot_audit_high_water,schema_version,
                      sqlite_source_id,error_json,created_at,completed_at,row_version
                 FROM backup_run WHERE state='verified'
                ORDER BY completed_at DESC,id DESC LIMIT 1"""
        ).fetchone()
        return None if row is None else _run_record(row)

    def list_verified(
        self,
        connection: ManagedConnection,
    ) -> tuple[BackupRunRecord, ...]:
        rows = connection.execute(
            """SELECT id,state,snapshot_db_relative_path,snapshot_db_sha256,
                      manifest_sha256,snapshot_audit_high_water,schema_version,
                      sqlite_source_id,error_json,created_at,completed_at,row_version
                 FROM backup_run WHERE state='verified'
                ORDER BY completed_at DESC,id DESC"""
        ).fetchall()
        return tuple(_run_record(row) for row in rows)


__all__ = [
    "BackupBlobRecord",
    "BackupBlobSpec",
    "BackupConflictError",
    "BackupIntegrityError",
    "BackupRepository",
    "BackupRepositoryError",
    "BackupRunRecord",
]
