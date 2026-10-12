"""Connection-scoped claim/finalize primitives for quarantined blob GC."""

from __future__ import annotations

from dataclasses import dataclass
import json
import re

from ..db import ManagedConnection
from ..errors import KnowledgeCoreDatabaseError, KnowledgeCoreTransactionError
from ..retained_blob_references import blob_is_referenced


_SHA_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_WORKER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$", re.ASCII)
_CLAIM_MS = 300_000


class BlobGcRepositoryError(KnowledgeCoreDatabaseError):
    pass


class BlobGcIntegrityError(BlobGcRepositoryError):
    pass


@dataclass(frozen=True, slots=True)
class BlobGcClaim:
    blob_sha256: str
    size_bytes: int
    media_type: str
    relative_path: str
    claim_owner: str
    claim_expires_at_ms: int
    attempt_count: int


def _write_connection(value: object) -> ManagedConnection:
    if not isinstance(value, ManagedConnection):
        raise TypeError("BlobGcRepository requires a ManagedConnection.")
    if not value.in_transaction:
        raise KnowledgeCoreTransactionError(
            "Blob GC mutation requires an existing managed transaction."
        )
    return value


def _worker(value: object) -> str:
    if not isinstance(value, str) or _WORKER_RE.fullmatch(value) is None:
        raise ValueError("claim_owner must be a stable worker identifier.")
    return value


def _time(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a non-negative SQLite timestamp.")
    return value


def _references(connection: ManagedConnection, digest: str) -> int:
    return int(blob_is_referenced(connection,digest))


class BlobGcRepository:
    """Short-transaction GC candidate state machine."""

    def claim_next(
        self,
        connection: ManagedConnection,
        *,
        claim_owner: str,
        now_ms: int,
    ) -> BlobGcClaim | None:
        connection = _write_connection(connection)
        owner = _worker(claim_owner)
        now = _time(now_ms, field="now_ms")
        connection.execute(
            """UPDATE blob_gc_candidate
                  SET state='retry',claim_owner=NULL,claim_expires_at=NULL,
                      last_error_json=?,updated_at=?
                WHERE state='claimed' AND claim_expires_at<=?""",
            (
                json.dumps(
                    {"code": "CLAIM_EXPIRED", "schema_version": 1},
                    ensure_ascii=True,
                    separators=(",", ":"),
                    sort_keys=True,
                ),
                now,
                now,
            ),
        )
        if connection.execute(
            "SELECT 1 FROM backup_run WHERE state IN ('preparing','copying') LIMIT 1"
        ).fetchone() is not None:
            return None
        # Cancel newly referenced quarantined candidates before looking for a
        # claim.  Retry/dead rows are insertion-fenced by schema v7.
        for _ in range(101):
            row = connection.execute(
                """SELECT c.blob_sha256,c.state,b.size_bytes,b.media_type,b.relative_path,
                          b.verification_status,c.attempt_count
                     FROM blob_gc_candidate c JOIN blob_object b ON b.sha256=c.blob_sha256
                    WHERE c.state IN ('quarantined','retry') AND c.delete_after<=?
                    ORDER BY c.delete_after,c.blob_sha256 LIMIT 1""",
                (now,),
            ).fetchone()
            if row is None:
                return None
            digest = str(row[0])
            if _SHA_RE.fullmatch(digest) is None:
                raise BlobGcIntegrityError("A GC candidate digest is invalid.")
            if _references(connection, digest):
                if row[1] != "quarantined":
                    raise BlobGcIntegrityError(
                        "A fenced retry candidate gained a canonical reference."
                    )
                connection.execute(
                    "DELETE FROM blob_gc_candidate WHERE blob_sha256=? AND state='quarantined'",
                    (digest,),
                )
                continue
            if row[5] != "verified":
                raise BlobGcIntegrityError("Only a verified blob may be GC claimed.")
            expires = now + _CLAIM_MS
            cursor = connection.execute(
                """UPDATE blob_gc_candidate
                      SET state='claimed',claim_owner=?,claim_expires_at=?,
                          attempt_count=attempt_count+1,last_error_json=NULL,updated_at=?
                    WHERE blob_sha256=? AND state=? AND delete_after<=?""",
                (owner, expires, now, digest, row[1], now),
            )
            if cursor.rowcount != 1:
                raise BlobGcIntegrityError("The blob GC claim compare-and-swap failed.")
            return BlobGcClaim(
                blob_sha256=digest,
                size_bytes=int(row[2]),
                media_type=str(row[3]),
                relative_path=str(row[4]),
                claim_owner=owner,
                claim_expires_at_ms=expires,
                attempt_count=int(row[6]) + 1,
            )
        raise BlobGcIntegrityError("Too many referenced GC candidates were cancelled.")

    def finalize_deleted(
        self,
        connection: ManagedConnection,
        claim: BlobGcClaim,
        *,
        now_ms: int,
    ) -> None:
        connection = _write_connection(connection)
        if not isinstance(claim, BlobGcClaim):
            raise TypeError("finalize_deleted requires a BlobGcClaim.")
        now = _time(now_ms, field="now_ms")
        row = connection.execute(
            """SELECT state,claim_owner,claim_expires_at,attempt_count
                 FROM blob_gc_candidate WHERE blob_sha256=?""",
            (claim.blob_sha256,),
        ).fetchone()
        if row is None or tuple(row) != (
            "claimed",
            claim.claim_owner,
            claim.claim_expires_at_ms,
            claim.attempt_count,
        ):
            raise BlobGcIntegrityError("The blob GC claim changed before finalize.")
        if now > claim.claim_expires_at_ms:
            raise BlobGcIntegrityError("The blob GC claim expired before finalize.")
        if _references(connection, claim.blob_sha256):
            raise BlobGcIntegrityError("The claimed blob gained a canonical reference.")
        if connection.execute(
            "SELECT 1 FROM backup_run WHERE state IN ('preparing','copying') LIMIT 1"
        ).fetchone() is not None:
            raise BlobGcIntegrityError("An active backup hold blocks blob GC finalize.")
        deleted = connection.execute(
            "DELETE FROM blob_gc_candidate WHERE blob_sha256=? AND state='claimed' AND claim_owner=?",
            (claim.blob_sha256, claim.claim_owner),
        )
        if deleted.rowcount != 1:
            raise BlobGcIntegrityError("The blob GC candidate finalize fence changed.")
        deleted = connection.execute(
            """DELETE FROM blob_object WHERE sha256=? AND size_bytes=?
                 AND media_type=? AND relative_path=? AND verification_status='verified'""",
            (
                claim.blob_sha256,
                claim.size_bytes,
                claim.media_type,
                claim.relative_path,
            ),
        )
        if deleted.rowcount != 1:
            raise BlobGcIntegrityError("The claimed blob metadata changed before finalize.")

    def record_failure(
        self,
        connection: ManagedConnection,
        claim: BlobGcClaim,
        *,
        now_ms: int,
        retryable: bool,
        error_code: str,
    ) -> None:
        connection = _write_connection(connection)
        if not isinstance(claim, BlobGcClaim):
            raise TypeError("record_failure requires a BlobGcClaim.")
        now = _time(now_ms, field="now_ms")
        if not isinstance(error_code, str) or not re.fullmatch(r"[A-Z0-9_]{1,80}", error_code):
            raise ValueError("error_code must be a stable uppercase code.")
        target = "retry" if retryable else "dead"
        error_json = json.dumps(
            {"code": error_code, "schema_version": 1},
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        cursor = connection.execute(
            """UPDATE blob_gc_candidate SET state=?,claim_owner=NULL,
                      claim_expires_at=NULL,last_error_json=?,updated_at=?
                WHERE blob_sha256=? AND state='claimed' AND claim_owner=?
                  AND claim_expires_at=? AND attempt_count=?""",
            (
                target,
                error_json,
                now,
                claim.blob_sha256,
                claim.claim_owner,
                claim.claim_expires_at_ms,
                claim.attempt_count,
            ),
        )
        if cursor.rowcount != 1:
            raise BlobGcIntegrityError("The blob GC failure claim changed.")


__all__ = [
    "BlobGcClaim",
    "BlobGcIntegrityError",
    "BlobGcRepository",
    "BlobGcRepositoryError",
]
