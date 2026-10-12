"""Isolated synthesis ledger; caller owns authority, transactions, jobs/audit.

Full source/response replay is outside any transaction. The writer accepts only
process-local sealed preparation and rechecks source/actor/receipt metadata.
This repository does not register handlers, activate uploads or call a model.
"""
from dataclasses import asdict, dataclass, replace
from contextlib import nullcontext
import hmac
import json
import secrets
from uuid import UUID, uuid5

from ..db import db_now_ms, transaction, TransactionMode
from ..wiki_authoring import AuthoredWiki, AuthoringIncompleteError, AuthoringWorkLimitError
from ..wiki_synthesis import (
    SYNTHESIS_VERSION, ReductionReceipt, ReducedBatch, SynthesisPolicy,
    reduction_receipt, resume_synthesis, _restore_reduction,
)
from .wiki_authoring import (
    AuthoringStorageConflict, WikiAuthoringRepository, _json, _sha, _prepare, _write, _time,
)
from .jobs import ClaimFence, JobRepository, JobRepositoryError


_KEY = secrets.token_bytes(32)
_NAMESPACE = UUID("aa229151-c69e-4794-b1d1-33c092bf3d90")


@dataclass(frozen=True, slots=True)
class PreparedSynthesisProgress:
    run_id: str
    actor_user_id: str
    config: tuple[str, str, str, int, int]
    wave_json: str | None
    wiki: AuthoredWiki | None
    completion: tuple[str, int, str] | None
    fence_json: str
    preparation_seal: str


@dataclass(frozen=True, slots=True)
class PreparedReductionReceipt:
    run_id: str
    actor_user_id: str
    ordinal: int
    claim: ClaimFence
    receipt: ReductionReceipt
    fence_json: str
    preparation_seal: str


def _seal(domain, *values):
    return hmac.digest(_KEY, _json((domain, values)).encode(), "sha256").hex()


def _wiki_identity(wiki):
    if wiki is None:
        return None
    if not isinstance(wiki, AuthoredWiki):
        raise AuthoringStorageConflict("Invalid synthesized Wiki preparation.")
    return (_sha(wiki.markdown), len(wiki.markdown.encode()),
            _sha(wiki.manifest_json), len(wiki.manifest_json.encode()))


def _progress_seal(value):
    return _seal("synthesis-progress-v1", value.run_id, value.actor_user_id,
        value.config, value.wave_json, _wiki_identity(value.wiki), value.completion, value.fence_json)


def _receipt_seal(value):
    return _seal("synthesis-receipt-v1", value.run_id, value.actor_user_id, value.ordinal,
        asdict(value.claim), asdict(value.receipt), value.fence_json)


def reduction_job_result(*, run_id: str, ordinal: int, receipt: ReductionReceipt) -> dict:
    """Versioned canonical result shared by the worker and retained replay."""
    return dict(schema_version=1, run_id=run_id, round_number=receipt.round_number,
        ordinal=ordinal, batch_id=receipt.batch_id, request_sha256=receipt.request_sha256,
        response_sha256=receipt.response_sha256)


def authored_snapshot_id(run_id: str, content_sha256: str) -> str:
    """Stable canonical identity shared with the durable finalizer's result."""
    return str(uuid5(_NAMESPACE, run_id + ":" + content_sha256))


def _wave_json(stage):
    value = _json({
        "version": SYNTHESIS_VERSION, "round_number": stage.wave.round_number,
        "wave_id": stage.wave.wave_id, "map_completion_id": stage.wave.map_completion_id,
        "predecessor_id": stage.wave.predecessor_id, "card_count": len(stage.wave),
        "reserved_work": stage.reserved_work, "model_calls": stage.model_calls,
        "batches": [{"batch_id": row.batch_id, "request_sha256": row.request_sha256}
                    for row in stage.batches],
    })
    if len(value.encode()) > 262_144:
        raise AuthoringWorkLimitError("The frozen synthesis stage metadata exceeds storage v1.")
    return value


class WikiSynthesisRepository:
    def __init__(self):
        self.maps = WikiAuthoringRepository()

    def _fence(self, connection, *, run_id, actor_user_id, now_ms):
        _time(now_ms)
        args = dict(run_id=run_id, actor_user_id=actor_user_id, now_ms=now_ms)
        maps = self.maps._completion_fence(connection, **args)
        preview = json.loads(maps)["run"][5]
        source = self.maps._run_source_fence(connection, preview_id=preview,
            actor_user_id=actor_user_id, now_ms=now_ms)
        config = connection.execute("""SELECT version,policy_sha256,map_durable_attempts,
            reduction_durable_attempts,created_at FROM publication_synthesis_config WHERE run_id=?""",
            (run_id,)).fetchone()
        waves = connection.execute("""SELECT round_number,wave_id,plan_sha256,batch_count,created_at
            FROM publication_synthesis_wave WHERE run_id=? ORDER BY round_number""", (run_id,)).fetchall()
        batches = connection.execute("""SELECT b.round_number,b.ordinal,b.batch_id,b.request_sha256,
            b.durable_job_id,r.response_sha256,r.response_bytes,r.model_sha256,r.model_bytes,
            r.transport_sha256,r.transport_bytes,r.retention_status,r.completed_at,
            CASE WHEN x.run_id IS NULL THEN 0 ELSE 1 END AS body_present
            FROM publication_synthesis_batch b LEFT JOIN publication_synthesis_receipt r
              ON r.run_id=b.run_id AND r.round_number=b.round_number AND r.ordinal=b.ordinal
            LEFT JOIN publication_synthesis_batch_body x
              ON x.run_id=b.run_id AND x.round_number=b.round_number AND x.ordinal=b.ordinal
            WHERE b.run_id=? ORDER BY b.round_number,b.ordinal""", (run_id,)).fetchall()
        return _json({"maps": maps, "source": source, "config": None if config is None else tuple(config),
                      "waves": [tuple(row) for row in waves], "batches": [tuple(row) for row in batches]})

    def _config(self, connection, run_id, *, policy=None, map_durable_attempts=None,
                reduction_durable_attempts=None):
        old = connection.execute("""SELECT version,policy_json,policy_sha256,
            map_durable_attempts,reduction_durable_attempts FROM publication_synthesis_config WHERE run_id=?""",
            (run_id,)).fetchone()
        try:
            if ((policy is not None and not isinstance(policy, SynthesisPolicy))
                    or any(value is not None and (type(value) is not int or value < 1)
                           for value in (map_durable_attempts, reduction_durable_attempts))):
                raise ValueError
            if old is not None:
                value = tuple(old)
                restored = SynthesisPolicy(**json.loads(value[1]))
                if (value[0] != SYNTHESIS_VERSION or _sha(value[1]) != value[2]
                        or _json(asdict(restored)) != value[1]
                        or (policy is not None and restored != policy)
                        or (map_durable_attempts is not None and map_durable_attempts != value[3])
                        or (reduction_durable_attempts is not None and reduction_durable_attempts != value[4])):
                    raise ValueError
                return value
            policy = policy or SynthesisPolicy()
            encoded = _json(asdict(policy))
            attempts = (2 if map_durable_attempts is None else map_durable_attempts,
                        2 if reduction_durable_attempts is None else reduction_durable_attempts)
            if (not isinstance(policy, SynthesisPolicy) or len(encoded.encode()) > 4096
                    or any(type(count) is not int or count < 1 for count in attempts)):
                raise ValueError
            return (SYNTHESIS_VERSION, encoded, _sha(encoded), *attempts)
        except (ValueError, TypeError, RecursionError) as exc:
            raise AuthoringStorageConflict("The frozen synthesis configuration changed or is invalid.") from exc

    def _receipts(self, connection, run_id):
        rows = connection.execute("""SELECT b.round_number,b.batch_id,b.request_sha256,
            r.response_sha256,r.response_bytes,r.model_sha256,r.model_bytes,r.transport_sha256,
            r.transport_bytes,r.retention_status,x.response,x.model_json,x.transport_json,j.state,j.result_json,b.ordinal
            FROM publication_synthesis_receipt r JOIN publication_synthesis_batch b
              ON b.run_id=r.run_id AND b.round_number=r.round_number AND b.ordinal=r.ordinal
            LEFT JOIN publication_synthesis_batch_body x
              ON x.run_id=r.run_id AND x.round_number=r.round_number AND x.ordinal=r.ordinal
            LEFT JOIN durable_job j ON j.id=b.durable_job_id
            WHERE r.run_id=? ORDER BY r.round_number,r.ordinal""", (run_id,)).fetchall()
        receipts = []
        for row in rows:
            if row[13] != "succeeded":
                raise AuthoringStorageConflict("A synthesis receipt requires its committed successful job.")
            if row[9] != "present":
                raise AuthoringStorageConflict("A required synthesis response was purged.")
            for text, digest, size in ((row[10], row[3], row[4]), (row[11], row[5], row[6]),
                                       (row[12], row[7], row[8])):
                if not isinstance(text, str) or _sha(text) != digest or len(text.encode()) != size:
                    raise AuthoringStorageConflict("A retained synthesis response is missing or inconsistent.")
            receipt = ReductionReceipt(row[0], row[1], row[2], row[10], row[3], row[11], row[12])
            try:
                if _json(json.loads(row[14])) != _json(reduction_job_result(run_id=run_id, ordinal=row[15], receipt=receipt)):
                    raise ValueError
            except (ValueError, TypeError) as exc:
                raise AuthoringStorageConflict("A completed reduction job disagrees with its exact receipt.") from exc
            receipts.append(receipt)
        return tuple(receipts)

    def _replay(self, connection, *, run_id, actor_user_id, now_ms, config, target_round=None):
        plan, maps = self.maps.load_completed_maps(connection, run_id=run_id,
            actor_user_id=actor_user_id, now_ms=now_ms)
        observed, target = {}, None
        def inspect(stage):
            nonlocal target
            observed[stage.wave.round_number] = _wave_json(stage)
            if stage.wave.round_number == target_round:
                target = stage
        progress = resume_synthesis(plan, maps, self._receipts(connection, run_id),
            policy=SynthesisPolicy(**json.loads(config[1])),
            map_durable_attempts=config[3], reduction_durable_attempts=config[4], on_stage=inspect)
        stored = {}
        for row in connection.execute("""SELECT round_number,plan_json,plan_sha256
                FROM publication_synthesis_wave WHERE run_id=? ORDER BY round_number""", (run_id,)):
            if _sha(row[1]) != row[2] or observed.get(row[0]) != row[1]:
                raise AuthoringStorageConflict("A stored wave is not the exact replayed complete plan.")
            stored[row[0]] = row[1]
            batches = connection.execute("""SELECT batch_id,request_sha256 FROM publication_synthesis_batch
                WHERE run_id=? AND round_number=? ORDER BY ordinal""", (run_id, row[0])).fetchall()
            if [dict(zip(("batch_id", "request_sha256"), part, strict=True)) for part in batches] != json.loads(row[1])["batches"]:
                raise AuthoringStorageConflict("A stored wave has missing or changed batch identities.")
        missing = set(observed) - set(stored)
        allowed = set() if progress.stage is None else {progress.stage.wave.round_number}
        if missing - allowed:
            raise AuthoringStorageConflict("A completed predecessor has no durable wave plan.")
        return plan, progress, observed, target

    def _batch_fence(self, connection, *, round_number, ordinal, **args):
        """Parallel valid sibling results do not invalidate this batch's work."""
        if type(round_number) is not int or round_number < 1 or type(ordinal) is not int or ordinal < 1:
            raise AuthoringStorageConflict("Invalid synthesis batch position.")
        value = json.loads(self._fence(connection, **args))
        rows = value["batches"]
        # New valid sibling receipts are harmless. A required receipt becoming
        # purged/missing still invalidates any in-flight provider preparation.
        value["unavailable_siblings"] = [
            row[:2] for row in rows if row[0] == round_number and row[1] != ordinal
            and row[5] is not None and (row[11] != "present" or row[13] != 1)]
        value["batches"] = [row for row in rows
            if row[0] < round_number or row[:2] == [round_number, ordinal]]
        value["waves"] = [row for row in value["waves"] if row[0] <= round_number]
        return _json(value)

    def prepare_progress(self, connection, *, run_id, actor_user_id, now_ms, policy=None,
                         map_durable_attempts=None, reduction_durable_attempts=None):
        _prepare(connection)
        args = dict(run_id=run_id, actor_user_id=actor_user_id, now_ms=now_ms)
        before = self._fence(connection, **args)
        config = self._config(connection, run_id, policy=policy, map_durable_attempts=map_durable_attempts,
                              reduction_durable_attempts=reduction_durable_attempts)
        _, progress, observed, _ = self._replay(connection, **args, config=config)
        wave_json = None if progress.stage is None else observed[progress.stage.wave.round_number]
        completion = None
        if progress.wiki is not None:
            summary = json.loads(progress.wiki.manifest_json)["synthesis"]
            last = max((row["round_number"] for row in summary["rounds"]), default=0)
            completion = ("reduced_complete_wave" if last else "single_map_no_reduction",
                          last, summary["final_wave_id"])
        after = self._fence(connection, **args)
        if before != after:
            raise AuthoringStorageConflict("Synthesis source, authority or receipts changed during preparation.")
        value = PreparedSynthesisProgress(run_id, actor_user_id, config, wave_json,
                                          progress.wiki, completion, after, "")
        return replace(value, preparation_seal=_progress_seal(value))

    def persist_progress(self, connection, *, prepared: PreparedSynthesisProgress, now_ms):
        _write(connection)
        _time(now_ms)
        if not isinstance(prepared, PreparedSynthesisProgress):
            raise AuthoringStorageConflict("A prepared synthesis stage is required.")
        identity = _wiki_identity(prepared.wiki)
        # Lost-ack replay returns only the already committed immutable snapshot.
        old = connection.execute("""SELECT s.id,s.content_sha256,s.content_size_bytes,s.manifest_sha256,
            s.manifest_size_bytes,x.mode,x.last_round,x.final_wave_id
            FROM publication_authoring_run r JOIN publication_synthesis_completion x ON x.run_id=r.id
            JOIN authored_wiki_snapshot s ON s.id=x.snapshot_id
            JOIN authored_wiki_snapshot_body b ON b.snapshot_id=s.id
            WHERE r.id=? AND r.actor_user_id=? AND r.status='ready'""",
            (prepared.run_id, prepared.actor_user_id)).fetchone()
        if old is not None:
            if identity is None or tuple(old)[1:5] != identity or tuple(old)[5:] != prepared.completion:
                raise AuthoringStorageConflict("A completed synthesis cannot be replaced by replay.")
            return old[0]
        if (not isinstance(prepared.preparation_seal, str)
                or not hmac.compare_digest(prepared.preparation_seal, _progress_seal(prepared))
                or prepared.fence_json != self._fence(connection, run_id=prepared.run_id,
                    actor_user_id=prepared.actor_user_id, now_ms=now_ms)):
            raise AuthoringStorageConflict("Synthesis preparation or its current metadata changed.")
        now_ms = max(now_ms, db_now_ms(connection))
        connection.execute("""INSERT INTO publication_synthesis_config
            (run_id,version,policy_json,policy_sha256,map_durable_attempts,reduction_durable_attempts,created_at)
            VALUES(?,?,?,?,?,?,?) ON CONFLICT(run_id) DO NOTHING""", (prepared.run_id, *prepared.config, now_ms))
        if prepared.wave_json is not None:
            value = json.loads(prepared.wave_json)
            connection.execute("""INSERT INTO publication_synthesis_wave
                (run_id,round_number,wave_id,plan_json,plan_sha256,batch_count,created_at)
                VALUES(?,?,?,?,?,?,?) ON CONFLICT(run_id,round_number) DO NOTHING""",
                (prepared.run_id, value["round_number"], value["wave_id"], prepared.wave_json,
                 _sha(prepared.wave_json), len(value["batches"]), now_ms))
            for ordinal, batch in enumerate(value["batches"], 1):
                connection.execute("""INSERT INTO publication_synthesis_batch
                    (run_id,round_number,ordinal,batch_id,request_sha256) VALUES(?,?,?,?,?)
                    ON CONFLICT(run_id,round_number,ordinal) DO NOTHING""",
                    (prepared.run_id, value["round_number"], ordinal, batch["batch_id"], batch["request_sha256"]))
            return None
        if prepared.wiki is None or prepared.completion is None:
            raise AuthoringStorageConflict("A terminal synthesis needs its complete final Wiki.")
        snapshot_id = authored_snapshot_id(prepared.run_id, identity[0])
        connection.execute("""INSERT INTO authored_wiki_snapshot
            (id,run_id,content_sha256,content_size_bytes,manifest_sha256,manifest_size_bytes,created_at)
            VALUES(?,?,?,?,?,?,?)""", (snapshot_id, prepared.run_id, *identity, now_ms))
        connection.execute("INSERT INTO authored_wiki_snapshot_body(snapshot_id,content_md,manifest_json) VALUES(?,?,?)",
            (snapshot_id, prepared.wiki.markdown, prepared.wiki.manifest_json))
        connection.execute("""INSERT INTO publication_synthesis_completion
            (run_id,snapshot_id,mode,last_round,final_wave_id) VALUES(?,?,?,?,?)""",
            (prepared.run_id, snapshot_id, *prepared.completion))
        connection.execute("""UPDATE publication_authoring_run SET status='ready',completed_at=?,
            row_version=row_version+1 WHERE id=? AND status='pending'""", (now_ms, prepared.run_id))
        return snapshot_id

    def _require_claim(self, connection, *, run_id, actor_user_id, round_number, ordinal, claim, now_ms):
        """Read-only attempt fence; the worker also owns current scope authority."""
        if not isinstance(claim, ClaimFence):
            raise AuthoringStorageConflict("An exact running reduction claim is required.")
        row = connection.execute("""SELECT b.batch_id,b.request_sha256,b.durable_job_id,c.reduction_durable_attempts
            FROM publication_synthesis_batch b JOIN publication_synthesis_config c ON c.run_id=b.run_id
            WHERE b.run_id=? AND b.round_number=? AND b.ordinal=?""", (run_id, round_number, ordinal)).fetchone()
        if row is None or row[2] != claim.job_id:
            raise AuthoringStorageConflict("The reduction job is not bound to this exact batch.")
        try:
            with nullcontext() if connection.in_transaction else transaction(connection, TransactionMode.DEFERRED):
                job = JobRepository().assert_effect_fence(connection, fence=claim,
                    now_ms=max(now_ms, db_now_ms(connection)))
        except JobRepositoryError as exc:
            raise AuthoringStorageConflict("The exact reduction claim is no longer executable.") from exc
        value = job.input_ref
        if (job.actor_kind != "user" or job.actor_user_id != actor_user_id
                or job.job_type != "publication_wiki.reduce_batch" or job.handler_version != "v1"
                or job.state != "running" or job.cancel_requested
                or job.claim_owner != claim.claim_owner or job.attempt_count != claim.attempt_count
                or job.claim_expires_at_ms is None or job.claim_expires_at_ms <= max(now_ms, db_now_ms(connection))
                or job.max_attempts != row[3]
                or any(value.get(key) != expected for key, expected in (
                    ("run_id", run_id), ("round_number", round_number), ("ordinal", ordinal),
                    ("batch_id", row[0]), ("request_sha256", row[1])))):
            raise AuthoringStorageConflict("The exact reduction claim is no longer executable.")
        return job

    def load_batch(self, connection, *, run_id, actor_user_id, round_number, ordinal, claim, now_ms):
        _prepare(connection)
        args = dict(run_id=run_id, actor_user_id=actor_user_id, now_ms=now_ms)
        self._require_claim(connection, **args, round_number=round_number, ordinal=ordinal, claim=claim)
        before = self._batch_fence(connection, **args, round_number=round_number, ordinal=ordinal)
        config = self._config(connection, run_id)
        plan, progress, _, stage = self._replay(connection, **args, config=config, target_round=round_number)
        row = connection.execute("""SELECT batch_id,request_sha256 FROM publication_synthesis_batch
            WHERE run_id=? AND round_number=? AND ordinal=?""", (run_id, round_number, ordinal)).fetchone()
        if (stage is None or type(ordinal) is not int or not 1 <= ordinal <= len(stage.batches)
                or row is None or tuple(row) != (stage.batches[ordinal-1].batch_id, stage.batches[ordinal-1].request_sha256)
                or stage.batches[ordinal-1] not in progress.pending_batches):
            raise AuthoringStorageConflict("This exact durable reduction batch is not pending.")
        if before != self._batch_fence(connection, **args, round_number=round_number, ordinal=ordinal):
            raise AuthoringStorageConflict("Synthesis changed while loading model input.")
        self._require_claim(connection, **args, round_number=round_number, ordinal=ordinal, claim=claim)
        return plan, stage.wave, stage.batches[ordinal-1], SynthesisPolicy(**json.loads(config[1]))

    def prepare_receipt(self, connection, *, run_id, actor_user_id, round_number, ordinal,
                        result: ReducedBatch, claim, now_ms):
        _prepare(connection)
        args = dict(run_id=run_id, actor_user_id=actor_user_id, now_ms=now_ms)
        self._require_claim(connection, **args, round_number=round_number, ordinal=ordinal, claim=claim)
        before = self._batch_fence(connection, **args, round_number=round_number, ordinal=ordinal)
        config = self._config(connection, run_id)
        plan, _, _, stage = self._replay(connection, **args, config=config, target_round=round_number)
        row = connection.execute("""SELECT batch_id,request_sha256 FROM publication_synthesis_batch
            WHERE run_id=? AND round_number=? AND ordinal=?""", (run_id, round_number, ordinal)).fetchone()
        if (stage is None or type(ordinal) is not int or not 1 <= ordinal <= len(stage.batches)
                or row is None or tuple(row) != (stage.batches[ordinal-1].batch_id, stage.batches[ordinal-1].request_sha256)):
            raise AuthoringStorageConflict("The durable reduction plan is unavailable.")
        receipt = reduction_receipt(result, round_number=round_number)
        rebuilt = _restore_reduction(plan, stage.batches[ordinal-1], receipt, SynthesisPolicy(**json.loads(config[1])))
        if rebuilt != result:
            raise AuthoringIncompleteError("Prepared cards differ from the exact retained provider response.")
        after = self._batch_fence(connection, **args, round_number=round_number, ordinal=ordinal)
        if before != after:
            raise AuthoringStorageConflict("Synthesis changed while verifying a model response.")
        self._require_claim(connection, **args, round_number=round_number, ordinal=ordinal, claim=claim)
        value = PreparedReductionReceipt(run_id, actor_user_id, ordinal, claim, receipt, after, "")
        return replace(value, preparation_seal=_receipt_seal(value))

    def save_receipt(self, connection, *, prepared: PreparedReductionReceipt, now_ms):
        _write(connection)
        _time(now_ms)
        if (not isinstance(prepared, PreparedReductionReceipt)
                or not isinstance(prepared.claim, ClaimFence)
                or not isinstance(prepared.receipt, ReductionReceipt)
                or not isinstance(prepared.preparation_seal, str)
                or not hmac.compare_digest(prepared.preparation_seal, _receipt_seal(prepared))
                or prepared.fence_json != self._batch_fence(connection, run_id=prepared.run_id,
                    actor_user_id=prepared.actor_user_id, round_number=prepared.receipt.round_number,
                    ordinal=prepared.ordinal, now_ms=now_ms)):
            raise AuthoringStorageConflict("Prepared reduction or its current metadata changed.")
        row = prepared.receipt
        self._require_claim(connection, run_id=prepared.run_id, actor_user_id=prepared.actor_user_id,
            round_number=row.round_number, ordinal=prepared.ordinal, claim=prepared.claim, now_ms=now_ms)
        key = (prepared.run_id, row.round_number, prepared.ordinal)
        values = (row.response, row.model_json, row.transport_json)
        old = connection.execute("""SELECT response,model_json,transport_json FROM publication_synthesis_batch_body
            WHERE run_id=? AND round_number=? AND ordinal=?""", key).fetchone()
        if old is not None:
            if tuple(old) != values:
                raise AuthoringStorageConflict("An immutable reduction response cannot be replaced.")
            return
        metadata = tuple(part for text in values for part in (_sha(text), len(text.encode())))
        connection.execute("""INSERT INTO publication_synthesis_receipt
            (run_id,round_number,ordinal,response_sha256,response_bytes,model_sha256,model_bytes,
             transport_sha256,transport_bytes,retention_status,completed_at)
            VALUES(?,?,?,?,?,?,?,?,?,'present',?)""", (*key, *metadata, max(now_ms, db_now_ms(connection))))
        connection.execute("""INSERT INTO publication_synthesis_batch_body
            (run_id,round_number,ordinal,response,model_json,transport_json) VALUES(?,?,?,?,?,?)""",
            (*key, *values))

    def bind_batch_job(self, connection, *, run_id, actor_user_id, round_number, ordinal, job_id, now_ms):
        _write(connection)
        self._fence(connection, run_id=run_id, actor_user_id=actor_user_id, now_ms=now_ms)
        row = connection.execute("""SELECT durable_job_id FROM publication_synthesis_batch
            WHERE run_id=? AND round_number=? AND ordinal=?""", (run_id, round_number, ordinal)).fetchone()
        if row is None or (row[0] is not None and row[0] != job_id):
            raise AuthoringStorageConflict("The reduction batch is absent or already bound.")
        if row[0] == job_id:
            return
        connection.execute("""UPDATE publication_synthesis_batch SET durable_job_id=?
            WHERE run_id=? AND round_number=? AND ordinal=?""", (job_id, run_id, round_number, ordinal))
