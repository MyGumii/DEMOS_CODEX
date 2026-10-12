"""v18 durable preservation records; no model calls or transaction ownership.

Preparation performs source-bound pure replay outside a writer. Commit methods
accept only process-local sealed preparations and recheck current actor, preview,
expiry, extraction retention, plan and committed prefix metadata. Model/source
text lives only in purgeable body tables; immutable metadata keeps hashes.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import hmac
import json
import secrets
from uuid import UUID, uuid5

from ..db import ManagedConnection, db_now_ms
from ..wiki_authoring import (
    AuthoringIncompleteError,
    AuthoringModelPolicy,
    AuthoringWorkPolicy,
    ExtractionSource,
)
from .. import wiki_preservation as preservation
from ..wiki_preservation import (
    AUTHORING_ONLY_VERSIONS,
    PRESERVATION_VERSION,
    PreservedChunk,
    PreservedWiki,
    PreservationExecutionPolicy,
    PreservationPlan,
    PreservationReceipt,
    assemble_preserved_wiki,
    plan_preservation,
    preservation_stages,
    replay_prefix,
    work_reservation,
)


_NAMESPACE = UUID("606f2655-804e-4ddd-a68d-5989ec255119")
_PREPARATION_KEY = secrets.token_bytes(32)
_FAILURE_CODES = frozenset({
    "PRESERVATION_INCOMPLETE",
    "PRESERVATION_PROVIDER_NO_CONTENT",
    "CODEX_EXEC_TIMEOUT",
    "CODEX_OUTPUT_TOO_LARGE",
    "CODEX_EXEC_FAILED",
    "CODEX_OUTPUT_INCOMPLETE",
    "MODEL_EXEC_FAILED",
    "MODEL_RESPONSE_TOO_LARGE",
    "INVALID_OUTPUT",
    "WORK_LIMIT",
    "SOURCE_UNAVAILABLE",
    "AUTHORITY_UNAVAILABLE",
})
_TERMINAL_WITHOUT_ATTEMPT = frozenset({
    "PRESERVATION_INCOMPLETE",
    "WORK_LIMIT",
    "SOURCE_UNAVAILABLE",
    "AUTHORITY_UNAVAILABLE",
})


class PreservationStorageConflict(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class PreparedPreservationRun:
    """Private-text-free plan metadata, sealed for one process."""

    run_id: str
    preview_id: str
    actor_user_id: str
    policy_version: str
    plan_json: str
    plan_sha256: str
    chunks: tuple[tuple[int, str, int, int, str], ...]
    fence_json: str
    preparation_seal: str


@dataclass(frozen=True, slots=True)
class PreparedPreservationReceipt:
    run_id: str
    actor_user_id: str
    ordinal: int
    sequence: int
    receipt: PreservationReceipt
    attempt_units: int
    fence_json: str
    preparation_seal: str


@dataclass(frozen=True, slots=True)
class PreparedPreservationFailure:
    run_id: str
    actor_user_id: str
    ordinal: int
    failure_ordinal: int
    stage_sequence: int
    stage: str
    request_sha256: str
    failure_code: str
    response: str
    model_json: str
    transport_json: str
    attempt_units: int
    fence_json: str
    preparation_seal: str


@dataclass(frozen=True, slots=True)
class PreparedPreservedWiki:
    wiki: PreservedWiki
    fence_json: str
    preparation_seal: str


def _json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _sha(value: str) -> str:
    return sha256(value.encode("utf-8", errors="strict")).hexdigest()


def _bytes(value: str) -> int:
    try:
        return len(value.encode("utf-8", errors="strict"))
    except (UnicodeError, AttributeError) as exc:
        raise PreservationStorageConflict("Preservation text is not valid UTF-8.") from exc


def preserved_snapshot_id(run_id: str, markdown: str) -> str:
    if not isinstance(run_id, str) or not isinstance(markdown, str):
        raise PreservationStorageConflict("Invalid preserved snapshot identity input.")
    try:
        UUID(run_id)
    except (TypeError, ValueError) as exc:
        raise PreservationStorageConflict("Invalid preservation run identity.") from exc
    return str(uuid5(_NAMESPACE, run_id + ":snapshot:" + _sha(markdown)))


def _time(value: int) -> None:
    if type(value) is not int or value < 0:
        raise PreservationStorageConflict("Invalid preservation timestamp.")


def _prepare(connection: ManagedConnection) -> None:
    if not isinstance(connection, ManagedConnection) or connection.in_transaction:
        raise PreservationStorageConflict("Preservation preparation must run outside a transaction.")


def _write(connection: ManagedConnection) -> None:
    if not isinstance(connection, ManagedConnection) or not connection.in_transaction:
        raise PreservationStorageConflict("An explicit service transaction is required.")


def _seal(domain: str, value: object) -> str:
    payload = _json({"domain": domain, "value": value})
    return hmac.digest(_PREPARATION_KEY, payload.encode("utf-8"), "sha256").hex()


def _plan_json(
    preview_id: str,
    plan: PreservationPlan,
    *,
    durable_attempts: int = 1,
) -> str:
    source = plan.source_plan
    if type(durable_attempts) is not int or not 1 <= durable_attempts <= 2:
        raise PreservationStorageConflict("Invalid durable preservation attempt policy.")
    baseline = work_reservation(plan)
    reservation = {
        "durable_attempts": durable_attempts,
        **{f"per_execution_{key.removeprefix('reserved_')}": value for key, value in baseline.items()},
        **{key: durable_attempts * value for key, value in baseline.items()},
    }
    work = source.work_policy
    if (
        reservation["reserved_model_attempts"] > work.model_attempts
        or reservation["reserved_logical_input_bytes"] > work.logical_input_bytes
        or reservation["reserved_response_bytes"] > work.response_bytes
    ):
        raise PreservationStorageConflict(
            "Durable retries exceed the frozen whole-document preservation budget."
        )
    return _json({
        "schema_version": 1,
        "preview_id": preview_id,
        "policy_version": plan.version,
        "source_snapshot_id": source.source.snapshot_id,
        "source_sha256": source.source.content_sha256,
        "source_size_bytes": _bytes(source.source.markdown),
        "chunk_bytes": source.chunk_bytes,
        "model_policy": asdict(source.model_policy),
        "work_policy": asdict(source.work_policy),
        "execution_policy": asdict(plan.execution_policy),
        "reservation": reservation,
        "chunks": [
            {
                "ordinal": chunk.ordinal,
                "chunk_id": chunk.chunk_id,
                "start": chunk.start,
                "end": chunk.end,
                "source_sha256": chunk.content_sha256,
            }
            for chunk in source.chunks
        ],
    })


def _model_policy(value: object) -> AuthoringModelPolicy:
    if not isinstance(value, dict):
        raise ValueError
    row = dict(value)
    row["provider_order"] = tuple(row["provider_order"])
    if row.get("model_ids") is not None:
        row["model_ids"] = tuple(row["model_ids"])
    if row.get("wire_models") is not None:
        row["wire_models"] = tuple(tuple(pair) for pair in row["wire_models"])
    return AuthoringModelPolicy(**row)


def _body_json(value: object, *, limit: int, field: str) -> str:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise PreservationStorageConflict(f"Invalid preservation {field} metadata.")
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=False,
        separators=(",", ":"),
    )
    if _bytes(encoded) > limit:
        raise PreservationStorageConflict(f"Preservation {field} metadata exceeds its bound.")
    return encoded


class WikiPreservationRepository:
    def _source_fence(
        self,
        connection: ManagedConnection,
        *,
        preview_id: str,
        actor_user_id: str,
        now_ms: int,
        continuation_authority=None,
    ) -> str:
        _time(now_ms)
        now = max(now_ms, db_now_ms(connection))
        expiry_clause = " AND p.expires_at>?"
        parameters = (preview_id, actor_user_id, now)
        if continuation_authority is not None:
            from .pdf_visual_continuation import _check_source_authority
            _check_source_authority(connection,continuation_authority,
                preview_id=preview_id,actor_user_id=actor_user_id,now_ms=now)
            # The exact original expires_at still participates in the source
            # fence. Only this sealed, current v97 request supplies a different
            # finite lifetime; legacy callers retain the original clock check.
            expiry_clause = ""
            parameters = (preview_id, actor_user_id)
        row = connection.execute(
            f"""SELECT p.id AS preview_id,p.actor_user_id,p.artifact_id,
                p.extraction_snapshot_id,p.source_snapshot_sha256,p.row_version AS preview_version,
                p.expires_at,p.durable_job_id,u.scope_epoch,u.row_version AS actor_version,
                e.extracted_size_bytes,e.normalized_content_sha256,e.normalizer_version,
                e.extractor_name,e.extractor_version,e.warnings_json,
                a.raw_sha256,a.raw_size_bytes,a.media_type,a.original_filename,j.state AS parent_state
              FROM publication_preview p
              JOIN app_user u ON u.id=p.actor_user_id AND u.account_status='active'
                AND u.scope_epoch=p.captured_scope_epoch
              JOIN source_artifact a ON a.id=p.artifact_id AND a.publisher_user_id=p.actor_user_id
                AND a.body_retention_status='present'
              JOIN extraction_snapshot e ON e.id=p.extraction_snapshot_id AND e.artifact_id=a.id
                AND e.status='ready' AND e.body_retention_status='present'
                AND e.extracted_sha256=p.source_snapshot_sha256
              JOIN extraction_snapshot_body b ON b.snapshot_id=e.id AND b.artifact_id=a.id
                AND b.content_sha256=e.extracted_sha256 AND b.content_size_bytes=e.extracted_size_bytes
                AND b.content_md IS NOT NULL AND b.content_blob_hash IS NULL
              JOIN durable_job j ON j.id=p.durable_job_id AND j.actor_user_id=p.actor_user_id
                AND j.state='succeeded'
              WHERE p.id=? AND p.actor_user_id=? AND p.status='ready'{expiry_clause}""",
            parameters,
        ).fetchone()
        if row is None:
            raise PreservationStorageConflict(
                "The preservation source, actor, authority, or preview lifetime is unavailable."
            )
        return _json(dict(row))

    def _source(
        self,
        connection: ManagedConnection,
        *,
        preview_id: str,
        actor_user_id: str,
        now_ms: int,
    ):
        _time(now_ms)
        now = max(now_ms, db_now_ms(connection))
        row = connection.execute(
            """SELECT p.actor_user_id,p.artifact_id,p.extraction_snapshot_id,
                p.source_snapshot_sha256,p.status,p.expires_at,p.durable_job_id,
                e.body_retention_status,b.content_md,b.content_sha256,b.content_size_bytes
              FROM publication_preview p
              JOIN app_user u ON u.id=p.actor_user_id AND u.account_status='active'
                AND u.scope_epoch=p.captured_scope_epoch
              JOIN source_artifact a ON a.id=p.artifact_id AND a.publisher_user_id=p.actor_user_id
                AND a.body_retention_status='present'
              JOIN extraction_snapshot e ON e.id=p.extraction_snapshot_id AND e.artifact_id=a.id
                AND e.status='ready'
              JOIN extraction_snapshot_body b ON b.snapshot_id=e.id AND b.artifact_id=a.id
              JOIN durable_job j ON j.id=p.durable_job_id AND j.actor_user_id=p.actor_user_id
                AND j.state='succeeded'
              WHERE p.id=?""",
            (preview_id,),
        ).fetchone()
        if (
            row is None
            or row[0] != actor_user_id
            or row[4] != "ready"
            or row[5] <= now
            or row[7] != "present"
            or row[3] != row[9]
            or not isinstance(row[8], str)
            or _bytes(row[8]) != row[10]
        ):
            raise PreservationStorageConflict("The exact retained preservation source is unavailable.")
        return row

    def prepare_run(
        self,
        connection: ManagedConnection,
        *,
        preview_id: str,
        actor_user_id: str,
        plan: PreservationPlan,
        now_ms: int,
        durable_attempts: int = 1,
    ) -> PreparedPreservationRun:
        _prepare(connection)
        if not isinstance(plan, PreservationPlan):
            raise PreservationStorageConflict("A validated preservation plan is required.")
        before = self._source_fence(
            connection, preview_id=preview_id, actor_user_id=actor_user_id, now_ms=now_ms
        )
        source = self._source(
            connection, preview_id=preview_id, actor_user_id=actor_user_id, now_ms=now_ms
        )
        planned = plan.source_plan.source
        if (
            source[2] != planned.snapshot_id
            or source[3] != planned.content_sha256
            or source[8] != planned.markdown
        ):
            raise PreservationStorageConflict("The plan differs from its canonical extraction.")
        PreservationPlan(plan.source_plan, plan.version, plan.execution_policy)
        encoded = _plan_json(preview_id, plan, durable_attempts=durable_attempts)
        digest = _sha(encoded)
        run_id = str(uuid5(_NAMESPACE, preview_id + ":" + digest))
        chunks = tuple(
            (
                chunk.ordinal,
                chunk.chunk_id,
                chunk.start,
                chunk.end,
                chunk.content_sha256,
            )
            for chunk in plan.source_plan.chunks
        )
        after = self._source_fence(
            connection, preview_id=preview_id, actor_user_id=actor_user_id, now_ms=now_ms
        )
        if before != after:
            raise PreservationStorageConflict("Preservation source changed during plan preparation.")
        seal_value = (
            run_id,
            preview_id,
            actor_user_id,
            plan.version,
            digest,
            chunks,
            after,
        )
        return PreparedPreservationRun(
            run_id,
            preview_id,
            actor_user_id,
            plan.version,
            encoded,
            digest,
            chunks,
            after,
            _seal("preservation-run-v1", seal_value),
        )

    def create_run(
        self,
        connection: ManagedConnection,
        *,
        preview_id: str,
        actor_user_id: str,
        prepared: PreparedPreservationRun,
        now_ms: int,
    ) -> str:
        _write(connection)
        _time(now_ms)
        if not isinstance(prepared, PreparedPreservationRun):
            raise PreservationStorageConflict("A prepared preservation run is required.")
        seal_value = (
            prepared.run_id,
            preview_id,
            actor_user_id,
            prepared.policy_version,
            prepared.plan_sha256,
            prepared.chunks,
            prepared.fence_json,
        )
        try:
            valid = (
                prepared.preview_id == preview_id
                and prepared.actor_user_id == actor_user_id
                and prepared.policy_version
                in (PRESERVATION_VERSION, *AUTHORING_ONLY_VERSIONS)
                and prepared.plan_sha256 == _sha(prepared.plan_json)
                and hmac.compare_digest(
                    prepared.preparation_seal,
                    _seal("preservation-run-v1", seal_value),
                )
            )
            value = json.loads(prepared.plan_json)
            valid = valid and value.get("policy_version") == prepared.policy_version
        except (TypeError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
            raise PreservationStorageConflict("Invalid prepared preservation run.") from exc
        if not valid:
            raise PreservationStorageConflict("Prepared preservation plan is invalid.")
        existing = connection.execute(
            """SELECT id,actor_user_id,policy_version,plan_json,plan_sha256,chunk_count
               FROM publication_preservation_run WHERE preview_id=?""",
            (preview_id,),
        ).fetchone()
        if existing is not None:
            stored_chunks = connection.execute(
                """SELECT ordinal,chunk_id,source_start,source_end,source_sha256
                   FROM publication_preservation_chunk WHERE run_id=? ORDER BY ordinal""",
                (prepared.run_id,),
            ).fetchall()
            if (
                tuple(existing) != (
                    prepared.run_id,
                    actor_user_id,
                    prepared.policy_version,
                    prepared.plan_json,
                    prepared.plan_sha256,
                    len(prepared.chunks),
                )
                or tuple(tuple(row) for row in stored_chunks) != prepared.chunks
            ):
                raise PreservationStorageConflict("This preview already has another preservation plan.")
            return prepared.run_id
        current_fence = self._source_fence(
            connection, preview_id=preview_id, actor_user_id=actor_user_id, now_ms=now_ms
        )
        if current_fence != prepared.fence_json:
            raise PreservationStorageConflict("Prepared preservation plan or current authority changed.")
        created = max(now_ms, db_now_ms(connection))
        source_fence = json.loads(prepared.fence_json)
        reservation = value.get("reservation", {})
        connection.execute(
            """INSERT INTO publication_preservation_run(
                id,preview_id,parent_job_id,actor_user_id,artifact_id,extraction_snapshot_id,
                source_sha256,source_size_bytes,policy_version,plan_json,plan_sha256,
                chunk_count,reserved_model_attempts,status,created_at,row_version)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,'pending',?,1)""",
            (
                prepared.run_id,
                preview_id,
                source_fence["durable_job_id"],
                actor_user_id,
                source_fence["artifact_id"],
                source_fence["extraction_snapshot_id"],
                source_fence["source_snapshot_sha256"],
                value["source_size_bytes"],
                prepared.policy_version,
                prepared.plan_json,
                prepared.plan_sha256,
                len(prepared.chunks),
                reservation["reserved_model_attempts"],
                created,
            ),
        )
        for chunk in prepared.chunks:
            connection.execute(
                """INSERT INTO publication_preservation_chunk(
                    run_id,ordinal,chunk_id,source_start,source_end,source_sha256)
                   VALUES(?,?,?,?,?,?)""",
                (prepared.run_id, *chunk),
            )
        return prepared.run_id

    def load_plan(
        self,
        connection: ManagedConnection,
        *,
        run_id: str,
        actor_user_id: str,
        now_ms: int,
    ) -> PreservationPlan:
        _prepare(connection)
        run = connection.execute(
            """SELECT preview_id,plan_json,plan_sha256,status,policy_version
               FROM publication_preservation_run WHERE id=? AND actor_user_id=?""",
            (run_id, actor_user_id),
        ).fetchone()
        if run is None or run[3] != "pending" or _sha(run[1]) != run[2]:
            raise PreservationStorageConflict("The pending preservation plan is unavailable.")
        source = self._source(
            connection, preview_id=run[0], actor_user_id=actor_user_id, now_ms=now_ms
        )
        try:
            value = json.loads(run[1])
            plan = plan_preservation(
                ExtractionSource(source[2], source[3], source[8]),
                chunk_bytes=value["chunk_bytes"],
                model_policy=_model_policy(value["model_policy"]),
                work_policy=AuthoringWorkPolicy(**value["work_policy"]),
                execution_policy=PreservationExecutionPolicy(**value["execution_policy"]),
                version=value["policy_version"],
            )
            if value["policy_version"] != run[4]:
                raise ValueError
            if _plan_json(
                run[0],
                plan,
                durable_attempts=value["reservation"]["durable_attempts"],
            ) != run[1]:
                raise ValueError
        except (KeyError, TypeError, ValueError, UnicodeError) as exc:
            raise PreservationStorageConflict(
                "The stored preservation plan cannot reproduce its source contract."
            ) from exc
        return plan

    def _chunk_fence(
        self,
        connection: ManagedConnection,
        *,
        run_id: str,
        actor_user_id: str,
        ordinal: int,
        now_ms: int,
    ) -> str:
        _time(now_ms)
        if type(ordinal) is not int or ordinal < 1:
            raise PreservationStorageConflict("Unknown preservation chunk ordinal.")
        now = max(now_ms, db_now_ms(connection))
        run = connection.execute(
            """SELECT r.id,r.preview_id,r.actor_user_id,r.row_version,r.plan_sha256,
                r.chunk_count,r.reserved_model_attempts,r.artifact_id,r.extraction_snapshot_id,
                r.source_sha256,p.row_version,p.expires_at,u.scope_epoch,e.body_retention_status,
                b.content_sha256,b.content_size_bytes
              FROM publication_preservation_run r
              JOIN publication_preview p ON p.id=r.preview_id AND p.actor_user_id=r.actor_user_id
                AND p.status='ready' AND p.expires_at>?
              JOIN app_user u ON u.id=r.actor_user_id AND u.account_status='active'
                AND u.scope_epoch=p.captured_scope_epoch
              JOIN source_artifact a ON a.id=r.artifact_id AND a.publisher_user_id=r.actor_user_id
                AND a.body_retention_status='present'
              JOIN extraction_snapshot e ON e.id=r.extraction_snapshot_id AND e.artifact_id=r.artifact_id
                AND e.status='ready' AND e.body_retention_status='present'
              JOIN extraction_snapshot_body b ON b.snapshot_id=e.id AND b.artifact_id=e.artifact_id
                AND b.content_sha256=r.source_sha256 AND b.content_blob_hash IS NULL
              WHERE r.id=? AND r.actor_user_id=? AND r.status='pending'
                AND p.artifact_id=r.artifact_id AND p.extraction_snapshot_id=r.extraction_snapshot_id
                AND p.source_snapshot_sha256=r.source_sha256""",
            (now, run_id, actor_user_id),
        ).fetchone()
        chunk = connection.execute(
            """SELECT ordinal,chunk_id,source_start,source_end,source_sha256
               FROM publication_preservation_chunk WHERE run_id=? AND ordinal=?""",
            (run_id, ordinal),
        ).fetchone()
        if run is None or chunk is None or run[13] != "present" or run[9] != run[14]:
            raise PreservationStorageConflict(
                "The preservation source, actor, authority, or preview lifetime changed."
            )
        receipts = connection.execute(
            """SELECT q.sequence,q.stage,q.request_sha256,q.response_sha256,q.response_size_bytes,
                q.model_sha256,q.model_size_bytes,q.transport_sha256,q.transport_size_bytes,
                q.validation_state,q.attempt_units,q.completed_at,EXISTS(SELECT 1 FROM publication_preservation_receipt_body b
                  WHERE b.run_id=q.run_id AND b.ordinal=q.ordinal AND b.sequence=q.sequence)
              FROM publication_preservation_receipt q
              WHERE q.run_id=? AND q.ordinal=? ORDER BY q.sequence""",
            (run_id, ordinal),
        ).fetchall()
        failures = connection.execute(
            """SELECT f.failure_ordinal,f.stage_sequence,f.stage,f.request_sha256,f.failure_code,
                f.response_sha256,f.response_size_bytes,f.model_sha256,f.model_size_bytes,
                f.transport_sha256,f.transport_size_bytes,f.attempt_units,f.failed_at,
                EXISTS(SELECT 1 FROM publication_preservation_failure_body b
                  WHERE b.run_id=f.run_id AND b.ordinal=f.ordinal AND b.failure_ordinal=f.failure_ordinal)
              FROM publication_preservation_failure f
              WHERE f.run_id=? AND f.ordinal=? ORDER BY f.failure_ordinal""",
            (run_id, ordinal),
        ).fetchall()
        return _json({
            "run": tuple(run),
            "chunk": tuple(chunk),
            "receipts": [tuple(row) for row in receipts],
            "failures": [tuple(row) for row in failures],
        })

    def _load_prefix_with_plan(
        self,
        connection: ManagedConnection,
        *,
        run_id: str,
        actor_user_id: str,
        ordinal: int,
        now_ms: int,
        plan: PreservationPlan,
    ) -> tuple[PreservationReceipt, ...]:
        rows = connection.execute(
            """SELECT q.sequence,q.stage,q.request_sha256,q.response_sha256,
                q.response_size_bytes,q.model_sha256,q.model_size_bytes,
                q.transport_sha256,q.transport_size_bytes,q.validation_state,
                b.response,b.model_json,b.transport_json
              FROM publication_preservation_receipt q
              JOIN publication_preservation_receipt_body b ON b.run_id=q.run_id
                AND b.ordinal=q.ordinal AND b.sequence=q.sequence
              WHERE q.run_id=? AND q.ordinal=? ORDER BY q.sequence""",
            (run_id, ordinal),
        ).fetchall()
        receipts: list[PreservationReceipt] = []
        for expected, row in enumerate(rows, 1):
            if (
                row[0] != expected
                or row[3] != _sha(row[10])
                or row[4] != _bytes(row[10])
                or row[5] != _sha(row[11])
                or row[6] != _bytes(row[11])
                or row[7] != _sha(row[12])
                or row[8] != _bytes(row[12])
                or row[9] != "validated"
            ):
                raise PreservationStorageConflict("A retained preservation receipt body changed.")
            try:
                model_value = json.loads(row[11])
                if not isinstance(model_value, dict):
                    raise ValueError
                model = tuple((key, value) for key, value in model_value.items())
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                raise PreservationStorageConflict("Invalid retained model receipt metadata.") from exc
            receipts.append(
                PreservationReceipt(
                    row[1], row[2], row[10], row[3], model, row[12],
                    validation_state=row[9],
                )
            )
        try:
            replay_prefix(plan, ordinal, tuple(receipts))
        except Exception as exc:
            if isinstance(exc, PreservationStorageConflict):
                raise
            raise PreservationStorageConflict("Retained preservation receipts do not replay.") from exc
        return tuple(receipts)

    def load_prefix(
        self,
        connection: ManagedConnection,
        *,
        run_id: str,
        actor_user_id: str,
        ordinal: int,
        now_ms: int,
    ) -> tuple[PreservationReceipt, ...]:
        _prepare(connection)
        before = self._chunk_fence(
            connection,
            run_id=run_id,
            actor_user_id=actor_user_id,
            ordinal=ordinal,
            now_ms=now_ms,
        )
        plan = self.load_plan(
            connection, run_id=run_id, actor_user_id=actor_user_id, now_ms=now_ms
        )
        receipts = self._load_prefix_with_plan(
            connection,
            run_id=run_id,
            actor_user_id=actor_user_id,
            ordinal=ordinal,
            now_ms=now_ms,
            plan=plan,
        )
        after = self._chunk_fence(
            connection,
            run_id=run_id,
            actor_user_id=actor_user_id,
            ordinal=ordinal,
            now_ms=now_ms,
        )
        if before != after:
            raise PreservationStorageConflict("Preservation prefix changed during restoration.")
        return receipts

    def prepare_receipt(
        self,
        connection: ManagedConnection,
        *,
        run_id: str,
        actor_user_id: str,
        ordinal: int,
        receipt: PreservationReceipt,
        now_ms: int,
        attempt_units: int = 1,
    ) -> PreparedPreservationReceipt:
        _prepare(connection)
        if not isinstance(receipt, PreservationReceipt):
            raise PreservationStorageConflict("A preservation receipt is required.")
        if type(attempt_units) is not int or attempt_units < 1:
            raise PreservationStorageConflict("Invalid preservation attempt consumption.")
        before = self._chunk_fence(
            connection,
            run_id=run_id,
            actor_user_id=actor_user_id,
            ordinal=ordinal,
            now_ms=now_ms,
        )
        plan = self.load_plan(
            connection, run_id=run_id, actor_user_id=actor_user_id, now_ms=now_ms
        )
        prefix = self._load_prefix_with_plan(
            connection,
            run_id=run_id,
            actor_user_id=actor_user_id,
            ordinal=ordinal,
            now_ms=now_ms,
            plan=plan,
        )
        sequence = len(prefix) + 1
        stages = preservation_stages(plan)
        try:
            replay_prefix(plan, ordinal, (*prefix, receipt))
        except Exception as exc:
            raise PreservationStorageConflict(
                "Only a source-validated next preservation receipt can be accepted."
            ) from exc
        if sequence > len(stages) or receipt.stage != stages[sequence - 1]:
            raise PreservationStorageConflict("Invalid next preservation stage.")
        if _bytes(receipt.response) > 32_768:
            raise PreservationStorageConflict("Preservation response exceeds its bound.")
        model_json = _body_json(dict(receipt.model), limit=4_096, field="model")
        try:
            transport = json.loads(receipt.transport_json)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise PreservationStorageConflict("Invalid preservation transport metadata.") from exc
        transport_json = _body_json(transport, limit=65_536, field="transport")
        if transport_json != receipt.transport_json:
            raise PreservationStorageConflict("Preservation transport metadata is not canonical.")
        after = self._chunk_fence(
            connection,
            run_id=run_id,
            actor_user_id=actor_user_id,
            ordinal=ordinal,
            now_ms=now_ms,
        )
        if before != after:
            raise PreservationStorageConflict("Preservation prefix changed during receipt preparation.")
        value = (
            run_id,
            actor_user_id,
            ordinal,
            sequence,
            asdict(receipt),
            attempt_units,
            after,
        )
        return PreparedPreservationReceipt(
            run_id,
            actor_user_id,
            ordinal,
            sequence,
            receipt,
            attempt_units,
            after,
            _seal("preservation-receipt-v1", value),
        )

    @staticmethod
    def _receipt_values(prepared: PreparedPreservationReceipt):
        receipt = prepared.receipt
        model_json = _body_json(dict(receipt.model), limit=4_096, field="model")
        return (
            receipt.stage,
            receipt.request_sha256,
            receipt.response_sha256,
            _bytes(receipt.response),
            _sha(model_json),
            _bytes(model_json),
            _sha(receipt.transport_json),
            _bytes(receipt.transport_json),
            receipt.validation_state,
            prepared.attempt_units,
            receipt.response,
            model_json,
            receipt.transport_json,
        )

    def save_receipt(
        self,
        connection: ManagedConnection,
        *,
        prepared: PreparedPreservationReceipt,
        now_ms: int,
    ) -> None:
        _write(connection)
        _time(now_ms)
        if not isinstance(prepared, PreparedPreservationReceipt):
            raise PreservationStorageConflict("A prepared preservation receipt is required.")
        value = (
            prepared.run_id,
            prepared.actor_user_id,
            prepared.ordinal,
            prepared.sequence,
            asdict(prepared.receipt),
            prepared.attempt_units,
            prepared.fence_json,
        )
        try:
            if not hmac.compare_digest(
                prepared.preparation_seal,
                _seal("preservation-receipt-v1", value),
            ):
                raise PreservationStorageConflict("Prepared preservation receipt was changed.")
            values = self._receipt_values(prepared)
        except (TypeError, ValueError, UnicodeError) as exc:
            raise PreservationStorageConflict("Invalid prepared preservation receipt.") from exc
        old = connection.execute(
            """SELECT q.stage,q.request_sha256,q.response_sha256,q.response_size_bytes,
                q.model_sha256,q.model_size_bytes,q.transport_sha256,q.transport_size_bytes,
                q.validation_state,q.attempt_units,b.response,b.model_json,b.transport_json
              FROM publication_preservation_receipt q
              JOIN publication_preservation_receipt_body b ON b.run_id=q.run_id
                AND b.ordinal=q.ordinal AND b.sequence=q.sequence
              WHERE q.run_id=? AND q.ordinal=? AND q.sequence=?""",
            (prepared.run_id, prepared.ordinal, prepared.sequence),
        ).fetchone()
        if old is not None:
            if tuple(old) != values:
                raise PreservationStorageConflict("An accepted preservation receipt cannot be replaced.")
            return
        current = self._chunk_fence(
            connection,
            run_id=prepared.run_id,
            actor_user_id=prepared.actor_user_id,
            ordinal=prepared.ordinal,
            now_ms=now_ms,
        )
        if current != prepared.fence_json:
            raise PreservationStorageConflict("Prepared preservation receipt or source changed.")
        completed = max(now_ms, db_now_ms(connection))
        connection.execute(
            """INSERT INTO publication_preservation_receipt(
                run_id,ordinal,sequence,stage,request_sha256,response_sha256,response_size_bytes,
                model_sha256,model_size_bytes,transport_sha256,transport_size_bytes,
                validation_state,attempt_units,completed_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                prepared.run_id,
                prepared.ordinal,
                prepared.sequence,
                *values[:10],
                completed,
            ),
        )
        connection.execute(
            """INSERT INTO publication_preservation_receipt_body(
                run_id,ordinal,sequence,response,model_json,transport_json) VALUES(?,?,?,?,?,?)""",
            (prepared.run_id, prepared.ordinal, prepared.sequence, *values[10:]),
        )

    def prepare_failure(
        self,
        connection: ManagedConnection,
        *,
        run_id: str,
        actor_user_id: str,
        ordinal: int,
        failure_code: str,
        response: str = "",
        model: dict | None = None,
        transport: dict | None = None,
        now_ms: int,
        attempt_units: int = 1,
    ) -> PreparedPreservationFailure:
        _prepare(connection)
        if failure_code not in _FAILURE_CODES:
            raise PreservationStorageConflict("A sanitized preservation failure code is required.")
        if type(attempt_units) is not int or attempt_units < 0 or not isinstance(response, str):
            raise PreservationStorageConflict("Invalid failed preservation attempt.")
        if _bytes(response) > 32_768:
            raise PreservationStorageConflict("Failed preservation response exceeds its bound.")
        model_json = _body_json(model or {}, limit=4_096, field="failure model")
        transport_json = _body_json(transport or {}, limit=65_536, field="failure transport")
        before = self._chunk_fence(
            connection,
            run_id=run_id,
            actor_user_id=actor_user_id,
            ordinal=ordinal,
            now_ms=now_ms,
        )
        plan = self.load_plan(
            connection, run_id=run_id, actor_user_id=actor_user_id, now_ms=now_ms
        )
        prefix = self._load_prefix_with_plan(
            connection,
            run_id=run_id,
            actor_user_id=actor_user_id,
            ordinal=ordinal,
            now_ms=now_ms,
            plan=plan,
        )
        stages = preservation_stages(plan)
        complete_rejected_prefix = (
            plan.version == PRESERVATION_VERSION and len(prefix) == len(stages)
        )
        stage_sequence = len(stages) if complete_rejected_prefix else len(prefix) + 1
        if stage_sequence > len(stages):
            raise PreservationStorageConflict(
                "A complete authoring-only prefix cannot record a model failure."
            )
        if complete_rejected_prefix and (
            failure_code != "PRESERVATION_INCOMPLETE" or attempt_units != 0
        ):
            raise PreservationStorageConflict(
                "A complete rejected prefix only permits its zero-cost terminal failure."
            )
        chunk = plan.source_plan.chunks[ordinal - 1]
        sections, warnings, review = replay_prefix(plan, ordinal, prefix)
        if complete_rejected_prefix:
            request_sha256 = prefix[-1].request_sha256
        else:
            _, _, request_sha256 = preservation._request(
                plan,
                chunk,
                stages[stage_sequence - 1],
                sections,
                warnings,
                review,
            )
        count = connection.execute(
            "SELECT count(*) FROM publication_preservation_failure WHERE run_id=? AND ordinal=?",
            (run_id, ordinal),
        ).fetchone()[0]
        failure_ordinal = count + 1
        after = self._chunk_fence(
            connection,
            run_id=run_id,
            actor_user_id=actor_user_id,
            ordinal=ordinal,
            now_ms=now_ms,
        )
        if before != after:
            raise PreservationStorageConflict("Preservation prefix changed during failure preparation.")
        seal_value = (
            run_id,
            actor_user_id,
            ordinal,
            failure_ordinal,
            stage_sequence,
            stages[stage_sequence - 1],
            request_sha256,
            failure_code,
            response,
            model_json,
            transport_json,
            attempt_units,
            after,
        )
        return PreparedPreservationFailure(
            *seal_value[:-1],
            after,
            _seal("preservation-failure-v1", seal_value),
        )

    def save_failure(
        self,
        connection: ManagedConnection,
        *,
        prepared: PreparedPreservationFailure,
        now_ms: int,
    ) -> None:
        _write(connection)
        _time(now_ms)
        if not isinstance(prepared, PreparedPreservationFailure):
            raise PreservationStorageConflict("A prepared preservation failure is required.")
        seal_value = (
            prepared.run_id,
            prepared.actor_user_id,
            prepared.ordinal,
            prepared.failure_ordinal,
            prepared.stage_sequence,
            prepared.stage,
            prepared.request_sha256,
            prepared.failure_code,
            prepared.response,
            prepared.model_json,
            prepared.transport_json,
            prepared.attempt_units,
            prepared.fence_json,
        )
        if not hmac.compare_digest(
            prepared.preparation_seal,
            _seal("preservation-failure-v1", seal_value),
        ):
            raise PreservationStorageConflict("Prepared preservation failure was changed.")
        metadata = (
            prepared.stage_sequence,
            prepared.stage,
            prepared.request_sha256,
            prepared.failure_code,
            _sha(prepared.response),
            _bytes(prepared.response),
            _sha(prepared.model_json),
            _bytes(prepared.model_json),
            _sha(prepared.transport_json),
            _bytes(prepared.transport_json),
            prepared.attempt_units,
        )
        old = connection.execute(
            """SELECT f.stage_sequence,f.stage,f.request_sha256,f.failure_code,
                f.response_sha256,f.response_size_bytes,f.model_sha256,f.model_size_bytes,
                f.transport_sha256,f.transport_size_bytes,f.attempt_units,
                b.response,b.model_json,b.transport_json
              FROM publication_preservation_failure f
              JOIN publication_preservation_failure_body b ON b.run_id=f.run_id
                AND b.ordinal=f.ordinal AND b.failure_ordinal=f.failure_ordinal
              WHERE f.run_id=? AND f.ordinal=? AND f.failure_ordinal=?""",
            (prepared.run_id, prepared.ordinal, prepared.failure_ordinal),
        ).fetchone()
        if old is not None:
            if tuple(old) != (*metadata, prepared.response, prepared.model_json, prepared.transport_json):
                raise PreservationStorageConflict("A failed preservation attempt cannot be replaced.")
            return
        current = self._chunk_fence(
            connection,
            run_id=prepared.run_id,
            actor_user_id=prepared.actor_user_id,
            ordinal=prepared.ordinal,
            now_ms=now_ms,
        )
        if current != prepared.fence_json:
            raise PreservationStorageConflict("Prepared failure or preservation source changed.")
        failed_at = max(now_ms, db_now_ms(connection))
        connection.execute(
            """INSERT INTO publication_preservation_failure(
                run_id,ordinal,failure_ordinal,stage_sequence,stage,request_sha256,failure_code,
                response_sha256,response_size_bytes,model_sha256,model_size_bytes,
                transport_sha256,transport_size_bytes,attempt_units,failed_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                prepared.run_id,
                prepared.ordinal,
                prepared.failure_ordinal,
                *metadata,
                failed_at,
            ),
        )
        connection.execute(
            """INSERT INTO publication_preservation_failure_body(
                run_id,ordinal,failure_ordinal,response,model_json,transport_json)
               VALUES(?,?,?,?,?,?)""",
            (
                prepared.run_id,
                prepared.ordinal,
                prepared.failure_ordinal,
                prepared.response,
                prepared.model_json,
                prepared.transport_json,
            ),
        )

    def _completion_fence(
        self,
        connection: ManagedConnection,
        *,
        run_id: str,
        actor_user_id: str,
        now_ms: int,
    ) -> str:
        row = connection.execute(
            "SELECT chunk_count FROM publication_preservation_run WHERE id=? AND actor_user_id=? AND status='pending'",
            (run_id, actor_user_id),
        ).fetchone()
        if row is None:
            raise PreservationStorageConflict("The pending preservation run is unavailable.")
        chunks = [
            json.loads(
                self._chunk_fence(
                    connection,
                    run_id=run_id,
                    actor_user_id=actor_user_id,
                    ordinal=ordinal,
                    now_ms=now_ms,
                )
            )
            for ordinal in range(1, row[0] + 1)
        ]
        return _json({"run_id": run_id, "actor_user_id": actor_user_id, "chunks": chunks})

    def prepare_completion(
        self,
        connection: ManagedConnection,
        *,
        run_id: str,
        actor_user_id: str,
        now_ms: int,
    ) -> PreparedPreservedWiki:
        _prepare(connection)
        before = self._completion_fence(
            connection, run_id=run_id, actor_user_id=actor_user_id, now_ms=now_ms
        )
        plan = self.load_plan(
            connection, run_id=run_id, actor_user_id=actor_user_id, now_ms=now_ms
        )
        results = []
        for chunk in plan.source_plan.chunks:
            receipts = self._load_prefix_with_plan(
                connection,
                run_id=run_id,
                actor_user_id=actor_user_id,
                ordinal=chunk.ordinal,
                now_ms=now_ms,
                plan=plan,
            )
            results.append(PreservedChunk(chunk.chunk_id, receipts))
        try:
            wiki = assemble_preserved_wiki(plan, results)
        except AuthoringIncompleteError:
            raise
        except Exception as exc:
            raise PreservationStorageConflict("Preservation completion could not be reproduced.") from exc
        after = self._completion_fence(
            connection, run_id=run_id, actor_user_id=actor_user_id, now_ms=now_ms
        )
        if before != after:
            raise PreservationStorageConflict("Preservation receipts changed during completion.")
        seal_value = (run_id, actor_user_id, _sha(wiki.markdown), _sha(wiki.manifest_json), after)
        return PreparedPreservedWiki(
            wiki,
            after,
            _seal("preserved-wiki-v1", seal_value),
        )

    def complete(
        self,
        connection: ManagedConnection,
        *,
        run_id: str,
        actor_user_id: str,
        prepared: PreparedPreservedWiki,
        now_ms: int,
    ) -> str:
        _write(connection)
        _time(now_ms)
        if not isinstance(prepared, PreparedPreservedWiki) or not isinstance(prepared.wiki, PreservedWiki):
            raise PreservationStorageConflict("A completely prepared preserved Wiki is required.")
        wiki = prepared.wiki
        seal_value = (run_id, actor_user_id, _sha(wiki.markdown), _sha(wiki.manifest_json), prepared.fence_json)
        if not hmac.compare_digest(
            prepared.preparation_seal,
            _seal("preserved-wiki-v1", seal_value),
        ):
            raise PreservationStorageConflict("Prepared preserved Wiki was changed.")
        run = connection.execute(
            "SELECT status FROM publication_preservation_run WHERE id=? AND actor_user_id=?",
            (run_id, actor_user_id),
        ).fetchone()
        if run is None:
            raise PreservationStorageConflict("The preservation run is unavailable.")
        snapshot_id = preserved_snapshot_id(run_id, wiki.markdown)
        if run[0] == "ready":
            old = connection.execute(
                """SELECT s.id,s.content_sha256,s.content_size_bytes,s.manifest_sha256,s.manifest_size_bytes
                   FROM preserved_wiki_snapshot s JOIN preserved_wiki_snapshot_body b ON b.snapshot_id=s.id
                   WHERE s.run_id=?""",
                (run_id,),
            ).fetchone()
            expected = (
                snapshot_id,
                _sha(wiki.markdown),
                _bytes(wiki.markdown),
                _sha(wiki.manifest_json),
                _bytes(wiki.manifest_json),
            )
            if old is None or tuple(old) != expected:
                raise PreservationStorageConflict("A completed preserved Wiki cannot be replaced.")
            return snapshot_id
        if run[0] != "pending":
            raise PreservationStorageConflict("A failed preservation run cannot become ready.")
        current = self._completion_fence(
            connection, run_id=run_id, actor_user_id=actor_user_id, now_ms=now_ms
        )
        if current != prepared.fence_json:
            raise PreservationStorageConflict("Prepared Wiki, source, or receipt prefix changed.")
        completed = max(now_ms, db_now_ms(connection))
        connection.execute(
            """INSERT INTO preserved_wiki_snapshot(
                id,run_id,content_sha256,content_size_bytes,manifest_sha256,manifest_size_bytes,created_at)
               VALUES(?,?,?,?,?,?,?)""",
            (
                snapshot_id,
                run_id,
                _sha(wiki.markdown),
                _bytes(wiki.markdown),
                _sha(wiki.manifest_json),
                _bytes(wiki.manifest_json),
                completed,
            ),
        )
        connection.execute(
            "INSERT INTO preserved_wiki_snapshot_body(snapshot_id,content_md,manifest_json) VALUES(?,?,?)",
            (snapshot_id, wiki.markdown, wiki.manifest_json),
        )
        connection.execute(
            """UPDATE publication_preservation_run SET status='ready',completed_at=?,
                 row_version=row_version+1 WHERE id=? AND status='pending'""",
            (completed, run_id),
        )
        return snapshot_id

    def fail_run(
        self,
        connection: ManagedConnection,
        *,
        run_id: str,
        actor_user_id: str,
        failure_code: str,
        now_ms: int,
    ) -> None:
        _write(connection)
        _time(now_ms)
        if failure_code not in _FAILURE_CODES:
            raise PreservationStorageConflict("A sanitized preservation failure code is required.")
        row = connection.execute(
            "SELECT status,failure_code FROM publication_preservation_run WHERE id=? AND actor_user_id=?",
            (run_id, actor_user_id),
        ).fetchone()
        if row is None or (row[0] != "pending" and tuple(row) != ("failed", failure_code)):
            raise PreservationStorageConflict("Cannot change the terminal preservation outcome.")
        if row[0] == "failed":
            return
        failure = connection.execute(
            """SELECT 1 FROM publication_preservation_failure
               WHERE run_id=? AND failure_code=? LIMIT 1""",
            (run_id, failure_code),
        ).fetchone()
        if failure is None and failure_code not in _TERMINAL_WITHOUT_ATTEMPT:
            raise PreservationStorageConflict("Terminal failure requires its durable failed attempt.")
        completed = max(now_ms, db_now_ms(connection))
        connection.execute(
            """UPDATE publication_preservation_run SET status='failed',failure_code=?,completed_at=?,
                 row_version=row_version+1 WHERE id=? AND status='pending'""",
            (failure_code, completed, run_id),
        )


__all__ = [
    "PreparedPreservationFailure",
    "PreparedPreservationReceipt",
    "PreparedPreservationRun",
    "PreparedPreservedWiki",
    "PreservationStorageConflict",
    "WikiPreservationRepository",
    "preserved_snapshot_id",
]
