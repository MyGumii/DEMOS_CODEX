"""Connection-scoped immutable authoring records; no model calls or transactions.

The service must reauthorize preview/target/role/epoch, own the write transaction
and bind chunk jobs. This repository also fences actor, ready preview, expiry,
and immutable extraction. v14 is opt-in until service/retention integration.
"""
from dataclasses import asdict, dataclass
from hashlib import sha256
import hmac
import json
import secrets
from uuid import UUID, uuid5

from ..db import ManagedConnection, db_now_ms
from ..wiki_authoring import (
    AUTHORING_POLICY_VERSION, AuthoringModelPolicy, AuthoringWorkPolicy, ExtractionSource,
    AuthoredChunk, AuthoredWiki, AuthoringChunk, AuthoringPlan, AuthoringIncompleteError,
    plan_authoring, authoring_request_descriptor, assemble_wiki, validate_chunk_output,
    _model_receipt, _transport_receipt, _plan_work,
)


_NAMESPACE = UUID("5d3569c7-a9d0-4d5c-81a1-f6f87331ad4d")
# Ephemeral, never persisted or sent to a client. A crashed worker re-prepares
# from durable chunks. This seals trusted in-process preparation, not user auth.
_PREPARATION_KEY = secrets.token_bytes(32)


@dataclass(frozen=True, slots=True)
class PreparedAuthoredWiki:
    wiki: AuthoredWiki
    fence_json: str
    preparation_seal: str


@dataclass(frozen=True, slots=True)
class PreparedAuthoredChunk:
    run_id: str
    actor_user_id: str
    ordinal: int
    values: tuple[str, str, str, str]
    fence_json: str
    preparation_seal: str


@dataclass(frozen=True, slots=True)
class PreparedAuthoringRun:
    """Process-local plan metadata; never carries extraction or chunk text."""
    run_id: str
    preview_id: str
    actor_user_id: str
    plan_json: str
    plan_sha256: str
    chunks: tuple[tuple[int, str, int, int, str, str], ...]
    reserved_work: tuple[int, int, int]
    fence_json: str
    preparation_seal: str


class AuthoringStorageConflict(ValueError):
    pass


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _sha(value):
    return sha256(value.encode("utf-8")).hexdigest()


def _seal(wiki, fence_json):
    payload = _json({"fence": fence_json, "body_sha256": _sha(wiki.markdown),
                     "manifest_sha256": _sha(wiki.manifest_json)})
    return hmac.digest(_PREPARATION_KEY, payload.encode(), "sha256").hex()


def _chunk_seal(run_id, actor_user_id, ordinal, values, fence_json):
    payload = _json({"domain": "authored-chunk-preparation-v1", "run_id": run_id,
        "actor_user_id": actor_user_id, "ordinal": ordinal,
        "values_sha256": [_sha(value) for value in values], "fence": fence_json})
    return hmac.digest(_PREPARATION_KEY, payload.encode(), "sha256").hex()


def _run_seal(run_id, preview_id, actor_user_id, plan_json, chunks, reserved_work, fence_json):
    payload = _json({"domain": "authoring-run-preparation-v1", "run_id": run_id,
        "preview_id": preview_id, "actor_user_id": actor_user_id,
        "plan_sha256": _sha(plan_json), "chunks_sha256": _sha(_json(chunks)),
        "reserved_work": reserved_work, "fence": fence_json})
    return hmac.digest(_PREPARATION_KEY, payload.encode(), "sha256").hex()


def _write(connection):
    if not isinstance(connection, ManagedConnection) or not connection.in_transaction:
        raise AuthoringStorageConflict("An explicit service transaction is required.")


def _prepare(connection):
    if not isinstance(connection, ManagedConnection) or connection.in_transaction:
        raise AuthoringStorageConflict("Authoring preparation must run outside a transaction.")


def _time(value):
    if type(value) is not int or value < 0:
        raise AuthoringStorageConflict("Invalid authoring timestamp.")


def _plan_json(plan):
    return _json({"policy_version": AUTHORING_POLICY_VERSION,
                  "source_snapshot_id": plan.source.snapshot_id, "source_sha256": plan.source.content_sha256,
                  "chunk_bytes": plan.chunk_bytes, "model_policy": asdict(plan.model_policy),
                  "work_policy": asdict(plan.work_policy),
                  "chunks": [{"ordinal": c.ordinal, "chunk_id": c.chunk_id, "start": c.start, "end": c.end,
                              "source_sha256": c.content_sha256,
                              "request_sha256": _sha(_json(authoring_request_descriptor(plan, c)))} for c in plan.chunks]})


def _preview(connection, preview_id, actor, now_ms, *, pending=True):
    _time(now_ms)
    row = connection.execute("""SELECT p.actor_user_id,p.artifact_id,p.extraction_snapshot_id,
        p.source_snapshot_sha256,p.status,p.expires_at,e.body_retention_status,
        b.content_md,b.content_sha256,b.content_size_bytes
        FROM publication_preview p
        JOIN extraction_snapshot e ON e.id=p.extraction_snapshot_id AND e.artifact_id=p.artifact_id
        JOIN extraction_snapshot_body b ON b.snapshot_id=e.id AND b.artifact_id=e.artifact_id
        WHERE p.id=? AND e.status='ready'""", (preview_id,)).fetchone()
    if (row is None or row[0] != actor or row[6] != "present"
            or row[3] != row[8] or not isinstance(row[7], str)
            or (pending and (row[4] != "ready" or row[5] <= now_ms))):
        raise AuthoringStorageConflict("The immutable authoring source is unavailable.")
    return row


class WikiAuthoringRepository:
    def _run_source_fence(self, connection, *, preview_id, actor_user_id, now_ms):
        """Fixed-shape metadata, without selecting any source or plan text."""
        _time(now_ms)
        row = connection.execute("""SELECT p.id AS preview_id,p.actor_user_id,
            p.artifact_id,p.extraction_snapshot_id,p.source_snapshot_sha256,
            p.row_version AS preview_version,p.expires_at,u.scope_epoch,u.row_version AS actor_version,
            e.extracted_size_bytes,e.normalized_content_sha256,e.normalizer_version,
            e.extractor_name,e.extractor_version,e.warnings_json,
            a.raw_sha256,a.raw_size_bytes,a.media_type,a.original_filename
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
            WHERE p.id=? AND p.actor_user_id=? AND p.status='ready' AND p.expires_at>?""",
            (preview_id, actor_user_id, max(now_ms, db_now_ms(connection)))).fetchone()
        if row is None:
            raise AuthoringStorageConflict("The initial authoring source or actor is unavailable or expired.")
        return _json(dict(row))

    def prepare_run(self, connection, *, preview_id, actor_user_id, plan: AuthoringPlan, now_ms):
        """Validate the entire immutable source and plan before the writer."""
        _prepare(connection)
        if not isinstance(plan, AuthoringPlan):
            raise AuthoringStorageConflict("A verified authoring plan is required.")
        before = self._run_source_fence(connection, preview_id=preview_id,
            actor_user_id=actor_user_id, now_ms=now_ms)
        row = _preview(connection, preview_id, actor_user_id, max(now_ms, db_now_ms(connection)))
        if (row[2] != plan.source.snapshot_id or row[3] != plan.source.content_sha256
                or row[7] != plan.source.markdown or row[9] != len(plan.source.markdown.encode())):
            raise AuthoringStorageConflict("The plan differs from its exact canonical extraction.")
        # Reconstruct the validated value, not a process-local object supplied
        # across a future job/restart boundary. No plan text grants authority.
        AuthoringPlan(ExtractionSource(row[2], row[3], row[7]), plan.chunk_bytes,
                      plan.chunks, plan.model_policy, plan.work_policy)
        encoded = _plan_json(plan)
        digest = _sha(encoded)
        run_id = str(uuid5(_NAMESPACE, preview_id + ":" + digest))
        chunks = tuple((part["ordinal"], part["chunk_id"], part["start"], part["end"],
                        part["source_sha256"], part["request_sha256"])
                       for part in json.loads(encoded)["chunks"])
        work = _plan_work(plan)
        reserved = tuple(work[key] for key in ("reserved_model_attempts",
            "reserved_logical_input_bytes", "reserved_response_bytes"))
        after = self._run_source_fence(connection, preview_id=preview_id,
            actor_user_id=actor_user_id, now_ms=now_ms)
        if before != after:
            raise AuthoringStorageConflict("Initial source or authority changed during authoring preparation.")
        return PreparedAuthoringRun(run_id, preview_id, actor_user_id, encoded, digest, chunks, reserved, after,
            _run_seal(run_id, preview_id, actor_user_id, encoded, chunks, reserved, after))

    def _chunk_fence(self, connection, *, run_id, actor_user_id, ordinal, now_ms):
        """No original text, model response or full plan crosses this query."""
        _time(now_ms)
        now_ms = max(now_ms, db_now_ms(connection))
        if type(ordinal) is not int or ordinal < 1:
            raise AuthoringStorageConflict("Unknown authoring chunk ordinal.")
        row = connection.execute("""SELECT r.id,r.actor_user_id,r.row_version,r.plan_sha256,
            r.chunk_count,r.preview_id,r.artifact_id,r.extraction_snapshot_id,r.source_sha256,
            e.body_retention_status,b.content_sha256,b.content_size_bytes,p.row_version,
            p.expires_at,u.scope_epoch,b.content_blob_hash
            FROM publication_authoring_run r JOIN publication_preview p ON p.id=r.preview_id
            JOIN app_user u ON u.id=r.actor_user_id AND u.account_status='active'
              AND u.scope_epoch=p.captured_scope_epoch
            JOIN extraction_snapshot e ON e.id=r.extraction_snapshot_id AND e.artifact_id=r.artifact_id
            JOIN extraction_snapshot_body b ON b.snapshot_id=e.id AND b.artifact_id=e.artifact_id
            WHERE r.id=? AND r.actor_user_id=? AND r.status='pending'
              AND p.actor_user_id=r.actor_user_id AND p.status='ready' AND p.expires_at>?
              AND p.artifact_id=r.artifact_id AND p.extraction_snapshot_id=r.extraction_snapshot_id
              AND p.source_snapshot_sha256=r.source_sha256 AND e.status='ready'
              AND e.extracted_sha256=r.source_sha256""", (run_id, actor_user_id, now_ms)).fetchone()
        if row is None or row[9] != "present" or row[8] != row[10] or row[15] is not None:
            raise AuthoringStorageConflict("The pending authoring source or actor is unavailable or expired.")
        chunk = connection.execute("""SELECT ordinal,chunk_id,source_start,source_end,
            source_sha256,request_sha256,durable_job_id FROM publication_authoring_chunk
            WHERE run_id=? AND ordinal=?""", (run_id, ordinal)).fetchone()
        if chunk is None:
            raise AuthoringStorageConflict("The frozen authoring chunk is unavailable.")
        return _json({"run": tuple(row), "chunk": tuple(chunk)})

    def _completion_fence(self, connection, *, run_id, actor_user_id, now_ms):
        """Read immutable metadata only, never extraction or response bodies."""
        _time(now_ms)
        now_ms = max(now_ms, db_now_ms(connection))
        row = connection.execute("""SELECT r.id,r.actor_user_id,r.row_version,r.plan_sha256,
            r.chunk_count,r.preview_id,r.artifact_id,r.extraction_snapshot_id,r.source_sha256,
            e.body_retention_status,b.content_sha256,b.content_size_bytes,p.row_version,u.scope_epoch
            FROM publication_authoring_run r JOIN publication_preview p ON p.id=r.preview_id
            JOIN app_user u ON u.id=r.actor_user_id AND u.account_status='active'
              AND u.scope_epoch=p.captured_scope_epoch
            JOIN extraction_snapshot e ON e.id=r.extraction_snapshot_id AND e.artifact_id=r.artifact_id
            JOIN extraction_snapshot_body b ON b.snapshot_id=e.id AND b.artifact_id=e.artifact_id
            WHERE r.id=? AND r.actor_user_id=? AND r.status='pending'
              AND p.actor_user_id=r.actor_user_id AND p.status='ready' AND p.expires_at>?
              AND p.artifact_id=r.artifact_id AND p.extraction_snapshot_id=r.extraction_snapshot_id
              AND p.source_snapshot_sha256=r.source_sha256 AND e.status='ready'
              AND e.extracted_sha256=r.source_sha256""", (run_id, actor_user_id, now_ms)).fetchone()
        if row is None or row[9] != "present" or row[8] != row[10]:
            raise AuthoringStorageConflict("The pending authoring source is unavailable or expired.")
        chunks = connection.execute("""SELECT c.ordinal,c.chunk_id,c.source_start,c.source_end,
            c.source_sha256,c.request_sha256,b.response_sha256,b.model_json,b.transport_json
            FROM publication_authoring_chunk c JOIN publication_authoring_chunk_body b
            ON b.run_id=c.run_id AND b.ordinal=c.ordinal WHERE c.run_id=? ORDER BY c.ordinal""",
            (run_id,)).fetchall()
        if len(chunks) != row[4] or [part[0] for part in chunks] != list(range(1, row[4] + 1)):
            raise AuthoringIncompleteError("Every immutable chunk receipt is required for completion.")
        return _json({"run": tuple(row), "chunks": [tuple(part) for part in chunks]})

    def create_run(self, connection, *, preview_id, actor_user_id, prepared: PreparedAuthoringRun, now_ms):
        """Persist prepared plan/chunk metadata with a fresh source/actor fence."""
        _write(connection)
        _time(now_ms)
        if (not isinstance(prepared, PreparedAuthoringRun) or prepared.preview_id != preview_id
                or prepared.actor_user_id != actor_user_id or not isinstance(prepared.chunks, tuple)
                or not prepared.chunks or not isinstance(prepared.plan_json, str)
                or not isinstance(prepared.preparation_seal, str)):
            raise AuthoringStorageConflict("A verified prepared authoring run is required.")
        try:
            valid = (prepared.plan_sha256 == _sha(prepared.plan_json)
                and hmac.compare_digest(prepared.preparation_seal, _run_seal(prepared.run_id,
                    preview_id, actor_user_id, prepared.plan_json, prepared.chunks,
                    prepared.reserved_work, prepared.fence_json)))
        except (UnicodeError, ValueError, TypeError) as exc:
            raise AuthoringStorageConflict("The prepared authoring run is invalid.") from exc
        if (not valid or prepared.fence_json != self._run_source_fence(connection,
                preview_id=preview_id, actor_user_id=actor_user_id, now_ms=now_ms)):
            raise AuthoringStorageConflict("The prepared authoring run or its source/authority changed.")
        encoded, digest, run_id = prepared.plan_json, prepared.plan_sha256, prepared.run_id
        source = json.loads(prepared.fence_json)
        existing = connection.execute("SELECT id,plan_sha256,chunk_count FROM publication_authoring_run WHERE preview_id=?",
                                      (preview_id,)).fetchone()
        if existing is not None:
            chunks = connection.execute("""SELECT ordinal,chunk_id,source_start,source_end,source_sha256,request_sha256
                FROM publication_authoring_chunk WHERE run_id=? ORDER BY ordinal""", (run_id,)).fetchall()
            if (tuple(existing) != (run_id, digest, len(prepared.chunks))
                    or tuple(tuple(chunk) for chunk in chunks) != prepared.chunks):
                raise AuthoringStorageConflict("This preview already has a different frozen authoring plan.")
            return run_id
        connection.execute("""INSERT INTO publication_authoring_run
            (id,preview_id,actor_user_id,artifact_id,extraction_snapshot_id,source_sha256,
             policy_version,plan_json,plan_sha256,chunk_count,status,created_at,row_version)
            VALUES(?,?,?,?,?,?,?,?,?,?,'pending',?,1)""",
            (run_id, preview_id, actor_user_id, source["artifact_id"], source["extraction_snapshot_id"],
             source["source_snapshot_sha256"], AUTHORING_POLICY_VERSION,
             encoded, digest, len(prepared.chunks), max(now_ms, db_now_ms(connection))))
        for chunk in prepared.chunks:
            connection.execute("""INSERT INTO publication_authoring_chunk
                (run_id,ordinal,chunk_id,source_start,source_end,source_sha256,request_sha256)
                VALUES(?,?,?,?,?,?,?)""", (run_id, *chunk))
        return run_id

    def bind_chunk_job(self, connection, *, run_id, actor_user_id, ordinal, job_id, now_ms):
        """Bind one retained versioned chunk job once, alongside its enqueue."""
        _write(connection)
        frozen = json.loads(self._chunk_fence(connection, run_id=run_id,
            actor_user_id=actor_user_id, ordinal=ordinal, now_ms=now_ms))["chunk"]
        row = connection.execute("""SELECT actor_user_id,job_type,handler_version,input_ref_json
            FROM durable_job WHERE id=?""", (job_id,)).fetchone()
        if row is None or tuple(row[:3]) != (actor_user_id, "publication_wiki.author_chunk", "v1"):
            raise AuthoringStorageConflict("The authoring job identity is invalid.")
        value = json.loads(row[3])
        if (value.get("run_id"), value.get("ordinal"), value.get("chunk_id"), value.get("request_sha256")) != (
                run_id, ordinal, frozen[1], frozen[5]):
            raise AuthoringStorageConflict("The job does not describe this exact chunk.")
        if frozen[6] is not None:
            if frozen[6] != job_id:
                raise AuthoringStorageConflict("This chunk is already bound to another job.")
            return
        connection.execute("UPDATE publication_authoring_chunk SET durable_job_id=? WHERE run_id=? AND ordinal=?",
            (job_id, run_id, ordinal))

    def load_plan(self, connection, *, run_id, actor_user_id, now_ms, pending=True):
        run = connection.execute("SELECT preview_id,plan_json,plan_sha256,status FROM publication_authoring_run WHERE id=? AND actor_user_id=?",
                                 (run_id, actor_user_id)).fetchone()
        if run is None or (pending and run[3] != "pending") or _sha(run[1]) != run[2]:
            raise AuthoringStorageConflict("The authoring run is unavailable or changed.")
        row = _preview(connection, run[0], actor_user_id, now_ms, pending=pending)
        try:
            value = json.loads(run[1])
            policy = value["model_policy"]
            policy["provider_order"] = tuple(policy["provider_order"])
            if policy["model_ids"] is not None:
                policy["model_ids"] = tuple(policy["model_ids"])
            if policy.get("wire_models") is not None:
                policy["wire_models"] = tuple(tuple(pair) for pair in policy["wire_models"])
            source = ExtractionSource(row[2], row[3], row[7])
            plan = plan_authoring(source, chunk_bytes=value["chunk_bytes"],
                                  model_policy=AuthoringModelPolicy(**policy),
                                  work_policy=AuthoringWorkPolicy(**value["work_policy"]))
            if _plan_json(plan) != run[1]:
                raise ValueError
        except (ValueError, TypeError, KeyError) as exc:
            raise AuthoringStorageConflict("The stored plan no longer reproduces its source contract.") from exc
        return plan

    def prepare_chunk(self, connection, *, run_id, actor_user_id, result: AuthoredChunk, now_ms):
        """Validate original/response outside the writer; never calls a model."""
        _prepare(connection)
        if not isinstance(result, AuthoredChunk) or not isinstance(result.chunk, AuthoringChunk):
            raise AuthoringStorageConflict("Unknown authoring result.")
        before = self._chunk_fence(connection, run_id=run_id, actor_user_id=actor_user_id,
                                   ordinal=result.chunk.ordinal, now_ms=now_ms)
        plan = self.load_plan(connection, run_id=run_id, actor_user_id=actor_user_id, now_ms=now_ms)
        if (not isinstance(result, AuthoredChunk) or not isinstance(result.chunk, AuthoringChunk)
                or type(result.chunk.ordinal) is not int or not 1 <= result.chunk.ordinal <= len(plan.chunks)):
            raise AuthoringStorageConflict("Unknown authoring result.")
        expected = plan.chunks[result.chunk.ordinal - 1]
        if (result.policy_version != AUTHORING_POLICY_VERSION or result.chunk != expected
                or result.request_sha256 != _sha(_json(authoring_request_descriptor(plan, expected)))):
            raise AuthoringStorageConflict("The result is not bound to the frozen request.")
        if expected.text.strip():
            if validate_chunk_output(result.response, expected) != (result.sections, result.warnings):
                raise AuthoringStorageConflict("The saved result differs from its source-validated response.")
            model = _model_receipt(result.model, plan.model_policy)
        else:
            if result.response or result.sections or result.warnings or result.model or result.request_transport_json != "{}":
                raise AuthoringStorageConflict("Whitespace cannot carry generated knowledge.")
            model = {}
        _transport_receipt(result.request_transport_json, plan.model_policy,
                           messages=authoring_request_descriptor(plan, expected)["messages"], model=model)
        values = (result.response, _sha(result.response), _json(model), result.request_transport_json)
        after = self._chunk_fence(connection, run_id=run_id, actor_user_id=actor_user_id,
                                  ordinal=expected.ordinal, now_ms=now_ms)
        if before != after:
            raise AuthoringStorageConflict("Authoring source or authority changed during preparation.")
        return PreparedAuthoredChunk(run_id, actor_user_id, expected.ordinal, values, after,
            _chunk_seal(run_id, actor_user_id, expected.ordinal, values, after))

    def save_chunk(self, connection, *, run_id, actor_user_id, prepared: PreparedAuthoredChunk, now_ms):
        """Commit only prepared bounded text under a fresh metadata-only fence."""
        _write(connection)
        _time(now_ms)
        if (not isinstance(prepared, PreparedAuthoredChunk)
                or prepared.run_id != run_id or prepared.actor_user_id != actor_user_id
                or type(prepared.ordinal) is not int or prepared.ordinal < 1
                or not isinstance(prepared.values, tuple) or len(prepared.values) != 4
                or any(not isinstance(value, str) or len(value) > 65_536 for value in prepared.values)
                or not isinstance(prepared.fence_json, str) or len(prepared.fence_json) > 65_536
                or not isinstance(prepared.preparation_seal, str)):
            raise AuthoringStorageConflict("A valid bounded prepared authoring chunk is required.")
        values = prepared.values
        try:
            invalid = (any(len(value.encode("utf-8")) > 65_536 for value in values)
                or values[1] != _sha(values[0])
                or not hmac.compare_digest(prepared.preparation_seal,
                    _chunk_seal(run_id, actor_user_id, prepared.ordinal, values, prepared.fence_json)))
        except (UnicodeError, TypeError, ValueError) as exc:
            raise AuthoringStorageConflict("Invalid prepared chunk encoding or seal.") from exc
        if (invalid or prepared.fence_json != self._chunk_fence(connection, run_id=run_id,
                actor_user_id=actor_user_id, ordinal=prepared.ordinal, now_ms=now_ms)):
            raise AuthoringStorageConflict("The prepared chunk or its immutable source/authority changed.")
        old = connection.execute("""SELECT response,response_sha256,model_json,transport_json
            FROM publication_authoring_chunk_body WHERE run_id=? AND ordinal=?""", (run_id, prepared.ordinal)).fetchone()
        if old is not None:
            if tuple(old) != values:
                raise AuthoringStorageConflict("A completed chunk cannot be replaced by a retry.")
            return
        connection.execute("""INSERT INTO publication_authoring_chunk_body
            (run_id,ordinal,response,response_sha256,model_json,transport_json,completed_at)
            VALUES(?,?,?,?,?,?,?)""", (run_id, prepared.ordinal, *values, now_ms))

    def load_completed_maps(self, connection, *, run_id, actor_user_id, now_ms):
        """Reconstruct maps outside a transaction; caller fences before/after."""
        _prepare(connection)
        plan = self.load_plan(connection, run_id=run_id, actor_user_id=actor_user_id, now_ms=now_ms)
        rows = connection.execute("""SELECT ordinal,response,response_sha256,model_json,transport_json
            FROM publication_authoring_chunk_body WHERE run_id=? ORDER BY ordinal""", (run_id,)).fetchall()
        if len(rows) != len(plan.chunks):
            raise AuthoringIncompleteError("Every durable chunk must finish before assembly.")
        results = []
        for chunk, row in zip(plan.chunks, rows, strict=True):
            if chunk.ordinal != row[0] or _sha(row[1]) != row[2]:
                raise AuthoringStorageConflict("A retained authoring response is inconsistent.")
            sections, warnings = validate_chunk_output(row[1], chunk) if chunk.text.strip() else ((), ())
            results.append(AuthoredChunk(chunk, _sha(_json(authoring_request_descriptor(plan, chunk))),
                                        row[1], sections, warnings, tuple(json.loads(row[3]).items()),
                                        request_transport_json=row[4]))
        return plan, tuple(results)

    def assemble(self, connection, *, run_id, actor_user_id, now_ms):
        """Pure preparation from retained chunks; caller runs outside write txn."""
        _prepare(connection)
        before = self._completion_fence(connection, run_id=run_id, actor_user_id=actor_user_id, now_ms=now_ms)
        plan, results = self.load_completed_maps(connection, run_id=run_id, actor_user_id=actor_user_id, now_ms=now_ms)
        wiki = assemble_wiki(plan, results)
        fence = self._completion_fence(connection, run_id=run_id, actor_user_id=actor_user_id, now_ms=now_ms)
        if before != fence:
            raise AuthoringStorageConflict("Authoring inputs or authority changed during Wiki preparation.")
        return PreparedAuthoredWiki(wiki, fence, _seal(wiki, fence))

    def complete(self, connection, *, run_id, actor_user_id, prepared: PreparedAuthoredWiki, now_ms):
        _write(connection)
        _time(now_ms)
        if not isinstance(prepared, PreparedAuthoredWiki) or not isinstance(prepared.wiki, AuthoredWiki):
            raise AuthoringStorageConflict("A completely assembled Wiki is required.")
        wiki = prepared.wiki
        run = connection.execute("SELECT status FROM publication_authoring_run WHERE id=? AND actor_user_id=?",
                                 (run_id, actor_user_id)).fetchone()
        if run is None:
            raise AuthoringStorageConflict("The authoring run is unavailable.")
        # A commit whose acknowledgement was lost must be replayable, including
        # after preview expiry. This returns the original immutable result only;
        # it does not revive the preview or authorize a new publication.
        if run[0] == "ready":
            old = connection.execute("""SELECT s.id,s.content_sha256,s.content_size_bytes,
                s.manifest_sha256,s.manifest_size_bytes FROM authored_wiki_snapshot s
                WHERE s.run_id=? AND EXISTS(SELECT 1 FROM authored_wiki_snapshot_body b WHERE b.snapshot_id=s.id)""",
                (run_id,)).fetchone()
            expected = (_sha(wiki.markdown), len(wiki.markdown.encode()),
                        _sha(wiki.manifest_json), len(wiki.manifest_json.encode()))
            if old is None or tuple(old)[1:] != expected:
                raise AuthoringStorageConflict("A completed Wiki cannot be replaced by a retry.")
            return old[0]
        # The expensive exact-source quote scan and rendering already happened
        # outside the write transaction. Fence only immutable metadata here.
        if (not isinstance(prepared.preparation_seal, str)
                or not hmac.compare_digest(prepared.preparation_seal, _seal(wiki, prepared.fence_json))
                or prepared.fence_json != self._completion_fence(connection, run_id=run_id,
                    actor_user_id=actor_user_id, now_ms=now_ms)):
            raise AuthoringStorageConflict("The prepared Wiki or its immutable source receipts changed.")
        snapshot_id = str(uuid5(_NAMESPACE, run_id + ":snapshot:" + _sha(wiki.markdown)))
        connection.execute("""INSERT INTO authored_wiki_snapshot
            (id,run_id,content_sha256,content_size_bytes,manifest_sha256,manifest_size_bytes,created_at)
            VALUES(?,?,?,?,?,?,?)""", (snapshot_id, run_id, _sha(wiki.markdown), len(wiki.markdown.encode()),
                                       _sha(wiki.manifest_json), len(wiki.manifest_json.encode()), now_ms))
        connection.execute("INSERT INTO authored_wiki_snapshot_body(snapshot_id,content_md,manifest_json) VALUES(?,?,?)",
                           (snapshot_id, wiki.markdown, wiki.manifest_json))
        connection.execute("""UPDATE publication_authoring_run SET status='ready',completed_at=?,row_version=row_version+1
            WHERE id=? AND status='pending'""", (now_ms, run_id))
        return snapshot_id

    def extraction_only(self, connection, *, run_id, actor_user_id, now_ms, failure_code):
        _write(connection)
        _time(now_ms)
        if failure_code not in {"model_unavailable", "work_limit", "invalid_output", "expired", "source_unavailable", "extraction_selected", "authoring_incomplete"}:
            raise AuthoringStorageConflict("A sanitized authoring failure code is required.")
        row = connection.execute("SELECT status,failure_code FROM publication_authoring_run WHERE id=? AND actor_user_id=?",
                                 (run_id, actor_user_id)).fetchone()
        if row is None or (row[0] != "pending" and tuple(row) != ("extraction_only", failure_code)):
            raise AuthoringStorageConflict("Cannot change the completed authoring result.")
        if row[0] == "pending":
            connection.execute("""UPDATE publication_authoring_run SET status='extraction_only',failure_code=?,
                completed_at=?,row_version=row_version+1 WHERE id=?""", (failure_code, now_ms, run_id))
