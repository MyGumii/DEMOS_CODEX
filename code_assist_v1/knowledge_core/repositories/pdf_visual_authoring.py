"""Opt-in visual author ledger; no worker, transport or publication activation.

Preparation/replay is outside transactions. Writes use a current source capability
and exact stored archive; callers own transaction, audit/outbox and execution
fences. A reservation replay explicitly cannot authorize another model call.
"""
from dataclasses import asdict, dataclass, field, replace
import hmac
import json
import secrets
from uuid import UUID, uuid5

from ..db import db_now_ms
from ..pdf_visual_authoring import PdfVisualReceipt, VERSION_V12, VERSION_V13, VERSION_V14, VERSION_V15, VERSION_V16, VERSION_V17, VERSION_V18, VERSION_V19, VERSION_V20, VERSION_V21, VERSION_V22, VERSION_V23, VERSION_V24, VERSION_V25, VERSION_V26, VERSION_V27, VERSION_V28, VERSION_V29, VERSION_V30, VERSION_V31, VERSION_V32, VERSION_V34, VERSION_V35, VERSION_V36, _request as _visual_request, _request_images as _visual_request_images, _inventory_batch_rows, _replay, assemble_pdf_visual_wiki, prompt_for_policy
from ..wiki_authoring import _hash, _json, AuthoringWorkLimitError
from ..services.pdf_visual_authoring_source import PreparedCanonicalPdfVisualSource, _seal as _source_seal
from ..migrations.v043_pdf_visual_authoring_storage import (SOURCE_POLICY, SLOT_POLICY,
    TABLE_POLICY, METADATA_TYPES, FAILURE_CODES)
from ..migrations.v095_pdf_numeric_append import POLICY_IDENTITIES
from .artifacts import _connection
from .pdf_visual_archive import PdfVisualArchiveRepository
from .pdf_authoring import _row, _insert, _uuid

_KEY = secrets.token_bytes(32)
_NS = UUID("91487427-5643-410e-827c-17173698302a")
_RUN = ("id", "source_archive_id", "preview_id", "parent_job_id", "actor_user_id", "artifact_id",
    "extraction_snapshot_id", "source_fence_sha256", "source_plan_sha256", "metadata_json",
    "metadata_sha256", "batch_count", "max_executions", "attempt_units")
_BATCH = ("ordinal", "batch_id", "members_json", "request_sha256", "input_size_bytes", "ordinals_json",
    "page", "visual", "wire_bytes", "image_bytes")
_ATTEMPT = ("id", "run_id", "ordinal", "sequence", "operation_id", "request_sha256", "attempt_units", "started_at")
_OUTCOME = ("attempt_id", "run_id", "ordinal", "status", "failure_code", "response_sha256", "response_size_bytes",
    "model_sha256", "model_size_bytes", "transport_sha256", "transport_size_bytes", "evidence_sha256", "evidence_size_bytes", "evidence_binding")
_BODY = ("response", "model_json", "transport_json", "attempt_json")


class PdfVisualStorageConflict(ValueError): pass


def _require(ok, message="Visual authoring identity, state or retention differs."):
    if not ok: raise PdfVisualStorageConflict(message)


def _mac(kind, value): return hmac.digest(_KEY, (kind+"\0"+_json(value)).encode(), "sha256").hex()


def _verify(kind, value, seal):
    try: ok = type(seal) is str and hmac.compare_digest(seal, _mac(kind, value))
    except (TypeError, ValueError, UnicodeError, RecursionError): ok = False
    _require(ok, "Visual storage preparation changed or belongs to another process.")


def _values(prepared):
    # Only small prepared records; never recursively copy the source/PDF images.
    values = asdict(prepared)
    values.pop("seal")
    return values


@dataclass(frozen=True, slots=True)
class PreparedVisualRun:
    source: PreparedCanonicalPdfVisualSource = field(repr=False)
    source_archive_id: str
    run_id: str
    metadata_json: str
    batches: tuple[tuple,...]
    seal: str = field(repr=False)

    def identity(self):
        s, m = self.source, json.loads(self.metadata_json)
        return (self.run_id, self.source_archive_id, s.preview_id, s.parent_job_id, s.actor_user_id,
            s.artifact_id, s.plan.source.geometry.snapshot_id, s.fence_sha256, s.plan.plan_sha256,
            self.metadata_json, _hash(self.metadata_json), len(self.batches), m["max_executions"], 1)


def prepare_visual_run(source, *, source_archive_id, max_executions=1):
    """Reserve all executions up front; default is one, never retry-until-good."""
    return _prepare_visual_run(source, source_archive_id=source_archive_id, max_executions=max_executions)


def _prepare_visual_run_from_reuse(source, *, source_archive_id, max_executions, reuse, key):
    """Private V31/V32/V35 warm path; rows come from the same bounded sealed entry."""
    from ..services.pdf_visual_plan_reuse import _PdfVisualPlanReuse
    _require(type(reuse) is _PdfVisualPlanReuse and reuse.owner is source._leases.owner)
    rows = reuse._checked_inventory(reuse.owner, key, source.plan)
    if rows is None:
        return prepare_visual_run(source, source_archive_id=source_archive_id, max_executions=max_executions), False
    run = _prepare_visual_run(source, source_archive_id=source_archive_id,
        max_executions=max_executions, _inventory=rows)
    _require(reuse._checked_inventory(reuse.owner, key, source.plan) is rows,
        "PDF inventory changed during private synchronous run preparation.")
    return run, True


def _prepare_visual_run(source, *, source_archive_id, max_executions, _inventory=None):
    _uuid(source_archive_id)
    _require(type(source) is PreparedCanonicalPdfVisualSource and type(source.seal) is str
        and source._leases.token in source._leases.owner._active
        and hmac.compare_digest(source.seal, _source_seal(source)))
    policy_version = source.plan.policy_version
    prompt_sha256 = _hash(prompt_for_policy(policy_version))
    _require((policy_version, prompt_sha256) in POLICY_IDENTITIES
        and source.source_preparation_version == SOURCE_POLICY)
    _require(type(max_executions) is int and 1 <= max_executions <= 2)
    p, batches = source.plan, []
    if _inventory is not None:
        _require(policy_version in (VERSION_V31, VERSION_V32, VERSION_V35, VERSION_V36))
        batches = _inventory
    elif policy_version in (VERSION_V24, VERSION_V25, VERSION_V26, VERSION_V27, VERSION_V28, VERSION_V29, VERSION_V30, VERSION_V31, VERSION_V32, VERSION_V34, VERSION_V35, VERSION_V36):
        batches = _inventory_batch_rows(p)
    else:
        for b in p.batches:
            # Historical selected-request admission remains byte-for-byte.
            _, _, digest, reserved = _visual_request(p, b)
            _require(0 < reserved["wire_bytes"] <= p.model_policy.request_budget_bytes)
            batches.append((b.ordinal, b.batch_id, _json([p.chunks[i-1].chunk_id for i in b.chunk_ordinals]),
                digest, reserved["wire_bytes"]+reserved["image_bytes"], _json(list(b.chunk_ordinals)),
                b.page, int(b.visual), reserved["wire_bytes"], reserved["image_bytes"]))
    reserved = (len(batches)*max_executions, sum(b[4] for b in batches)*max_executions,
        len(batches)*max_executions*65536)
    budgets = (p.work_policy.model_attempts, p.work_policy.logical_input_bytes, p.work_policy.response_bytes)
    if any(a>b for a,b in zip(reserved,budgets)):
        raise AuthoringWorkLimitError("All durable visual executions exceed the document work budget.")
    m = dict(schema_version=1, policy_version=policy_version, source_preparation_version=SOURCE_POLICY,
        source_slot_version=SLOT_POLICY, assembly_version=policy_version, table_rendering_version=TABLE_POLICY,
        prompt_sha256=prompt_sha256, preview_id=source.preview_id, source_plan_sha256=p.plan_sha256,
        source_fence_sha256=source.fence_sha256, configuration_sha256=_hash(source.configuration_json),
        configuration_size_bytes=len(source.configuration_json.encode()), batch_count=len(batches),
        max_executions=max_executions, attempt_units=1, reserved_model_attempts=reserved[0],
        reserved_input_bytes=reserved[1], reserved_response_bytes=reserved[2], budget_model_attempts=budgets[0],
        budget_input_bytes=budgets[1], budget_response_bytes=budgets[2],
        budget_markdown_bytes=p.work_policy.markdown_bytes, budget_manifest_bytes=p.work_policy.manifest_bytes)
    _require(set(m)==set(METADATA_TYPES) and 1<=m["configuration_size_bytes"]<=65536
        and all(type(m[k]) is int and 0<=m[k]<2**63 for k,v in METADATA_TYPES.items() if v=="integer"))
    metadata = _json(m)
    run_id = str(uuid5(_NS, source_archive_id+":"+_hash(metadata)))
    run = PreparedVisualRun(source, source_archive_id, run_id, metadata, tuple(batches), "")
    return replace(run, seal=_mac("run", (run.identity(),run.batches)))


@dataclass(frozen=True, slots=True)
class VisualAttempt:
    id: str
    run_id: str
    ordinal: int
    sequence: int
    operation_id: str
    request_sha256: str
    attempt_units: int
    started_at: int


@dataclass(frozen=True, slots=True)
class AttemptReservation:
    attempt: VisualAttempt
    created: bool  # False means replay only, never permission to invoke.


@dataclass(frozen=True, slots=True)
class PreparedVisualOutcome:
    attempt: VisualAttempt
    metadata: tuple
    body: tuple[str,...] = field(repr=False)
    run_metadata_sha256: str
    seal: str = field(repr=False)


def _canonical_object(value, maximum):
    _require(type(value) is str and 2<=len(value.encode())<=maximum)
    try: obj = json.loads(value)
    except (ValueError,TypeError,RecursionError) as exc: raise PdfVisualStorageConflict("Invalid evidence JSON.") from exc
    _require(type(obj) is dict and _json(obj)==value, "Evidence must be an exact canonical object.")
    return obj


def _evidence_binding(transport_json, attempt_json):
    """Raw-record agreement only; malformed failures must remain recordable."""
    transport,local=json.loads(transport_json),json.loads(attempt_json)
    if "image_input_attempt" not in transport: return "absent"
    other=transport["image_input_attempt"]
    if type(other) is dict and not other: return "absent"
    return "matched" if type(other) is dict and _json(other)==_json(local) else "conflicting"


def prepare_visual_outcome(run, attempt, *, receipt=None, status="validated", failure_code=None, attempt_json=None):
    _require(type(run) is PreparedVisualRun and type(attempt) is VisualAttempt)
    _verify("run", (run.identity(),run.batches), run.seal)
    _uuid(attempt.id); _uuid(attempt.operation_id)
    _require(all(type(getattr(attempt,k)) is int for k in ("ordinal","sequence","attempt_units","started_at"))
        and attempt.run_id==run.run_id and 1<=attempt.ordinal<=len(run.batches)
        and 1<=attempt.sequence<=json.loads(run.metadata_json)["max_executions"]
        and attempt.attempt_units==1 and 0<=attempt.started_at<2**63
        and attempt.request_sha256==run.batches[attempt.ordinal-1][3])
    _require(status in ("validated","failed","abandoned"))
    if status=="validated":
        _require(failure_code is None)
        _replay(run.source.plan, attempt.ordinal, receipt)
        expected = _json(json.loads(receipt.transport_json).get("image_input_attempt",{}))
        _require(attempt_json is None or attempt_json==expected)
        attempt_json = expected
    else:
        _require(failure_code in FAILURE_CODES)
        if receipt is not None:
            _require(type(receipt) is PdfVisualReceipt and receipt.validation_state=="attempt_only"
                and receipt.batch_id==run.batches[attempt.ordinal-1][1] and receipt.policy_version==run.source.plan.policy_version
                and receipt.request_sha256==attempt.request_sha256 and receipt.response_sha256==_hash(receipt.response))
    if attempt_json is None: attempt_json="{}"  # Explicit absence is not successful transport evidence.
    body = ("", "{}", "{}", attempt_json) if receipt is None else (
        receipt.response, _json(dict(receipt.model)), receipt.transport_json, attempt_json)
    _require(type(body[0]) is str and len(body[0].encode())<=65536)
    for value,limit in zip(body[1:],(4096,65536,65536)): _canonical_object(value,limit)
    if receipt is not None:
        _require(len(dict(receipt.model))==len(receipt.model)
            and all(type(k) is str and type(v) is str for k,v in receipt.model))
    metadata = (attempt.id,run.run_id,attempt.ordinal,status,failure_code,
        *(v for text in body for v in (_hash(text),len(text.encode()))),_evidence_binding(body[2],body[3]))
    outcome = PreparedVisualOutcome(attempt,metadata,body,_hash(run.metadata_json),"")
    return replace(outcome,seal=_mac("outcome",_values(outcome)))


@dataclass(frozen=True, slots=True)
class PreparedVisualSnapshot:
    run_id: str
    run_metadata_sha256: str
    snapshot_id: str
    markdown: str = field(repr=False)
    manifest_json: str = field(repr=False)
    receipt_sha256s: tuple[str,...]
    seal: str = field(repr=False)


def prepare_visual_snapshot(run, receipts, *, cancel_event=None):
    _require(type(run) is PreparedVisualRun)
    _verify("run",(run.identity(),run.batches),run.seal)
    result = assemble_pdf_visual_wiki(run.source.plan,receipts,cancel_event=cancel_event)
    ordered = sorted(receipts,key=lambda r: next(b[0] for b in run.batches if b[1]==r.batch_id))
    p = PreparedVisualSnapshot(run.run_id,_hash(run.metadata_json),str(uuid5(_NS,"wiki:"+run.run_id)),
        result.markdown,result.manifest_json,tuple(_hash(_json(asdict(r))) for r in ordered),"")
    _require(len(p.markdown.encode())<=4194304 and len(p.manifest_json.encode())<=16777216,
        "Visual Wiki exceeds frozen storage bounds; no partial snapshot is permitted.")
    return replace(p,seal=_mac("snapshot",_values(p)))


class PdfVisualAuthoringRepository:
    def __init__(self, source_service, archive_repository):
        _require(type(archive_repository) is PdfVisualArchiveRepository
            and archive_repository.source_service is source_service)
        self.source_service,self.archive_repository = source_service,archive_repository

    def _check(self,connection,run):
        _connection(connection,write=True)
        _require(type(run) is PreparedVisualRun)
        _verify("run",(run.identity(),run.batches),run.seal)
        self.source_service.revalidate_in_transaction(connection,run.source)
        metadata,bundle,_ = self.archive_repository.load(connection,preview_id=run.source.preview_id,
            actor_user_id=run.source.actor_user_id)
        value = json.loads(bundle)
        _require(metadata[0]==run.source_archive_id and metadata[9]==run.source.plan.plan_sha256
            and value["configuration_json"]==run.source.configuration_json
            and value["retained_assets_json"]==run.source.retained_assets_json
            and _json(value["source"])==_json(run.source.plan.source.identity())
            and value["requests"]==[b[3] for b in run.batches])
        if run.source.plan.policy_version in (VERSION_V34, VERSION_V35, VERSION_V36):
            _require(value['chart_supplement'] == run.source.plan.chart_supplement.identity()
                == json.loads(run.source.configuration_json)['chart_identity'])

    def _bound(self,connection,run):
        self._check(connection,run)
        row=connection.execute("SELECT * FROM pdf_visual_authoring_run WHERE id=?",(run.run_id,)).fetchone()
        _require(_row(row,_RUN)==run.identity())
        batches=connection.execute("SELECT * FROM pdf_visual_authoring_batch WHERE run_id=? ORDER BY ordinal",(run.run_id,)).fetchall()
        _require(tuple(_row(b,_BATCH) for b in batches)==run.batches)
        body=connection.execute("SELECT configuration_json FROM pdf_visual_authoring_run_body WHERE run_id=?",(run.run_id,)).fetchone()
        _require(body is not None and body[0]==run.source.configuration_json)
        state=connection.execute("SELECT * FROM pdf_visual_authoring_state WHERE run_id=?",(run.run_id,)).fetchone()
        _require(state is not None)
        return state

    def put_run(self,connection,run):
        self._check(connection,run)
        prior=connection.execute("SELECT id FROM pdf_visual_authoring_run WHERE preview_id=?",(run.source.preview_id,)).fetchone()
        if prior is not None:
            _require(prior[0]==run.run_id); self._bound(connection,run); return run.run_id
        _insert(connection,"pdf_visual_authoring_run",(*_RUN,"created_at"),(*run.identity(),db_now_ms(connection)))
        _insert(connection,"pdf_visual_authoring_run_body",("run_id","configuration_json"),(run.run_id,run.source.configuration_json))
        _insert(connection,"pdf_visual_authoring_state",("run_id","status","row_version"),(run.run_id,"pending",1))
        for batch in run.batches: _insert(connection,"pdf_visual_authoring_batch",("run_id",*_BATCH),(run.run_id,*batch))
        self._bound(connection,run)
        return run.run_id

    def start_attempt(self,connection,run,*,ordinal,operation_id):
        """created=True is still subject to the worker's separate current claim fence."""
        state=self._bound(connection,run); _uuid(operation_id)
        _require(type(ordinal) is int and 1<=ordinal<=len(run.batches))
        prior=connection.execute("SELECT * FROM pdf_visual_authoring_attempt WHERE operation_id=?",(operation_id,)).fetchone()
        if prior is not None:
            _require(prior["run_id"]==run.run_id and prior["ordinal"]==ordinal and prior["request_sha256"]==run.batches[ordinal-1][3])
            return AttemptReservation(VisualAttempt(*_row(prior,_ATTEMPT)),False)
        _require(state["status"]=="pending")
        n=connection.execute("SELECT count(*) FROM pdf_visual_authoring_attempt WHERE run_id=? AND ordinal=?",(run.run_id,ordinal)).fetchone()[0]
        a=VisualAttempt(str(uuid5(_NS,"attempt:"+operation_id)),run.run_id,ordinal,n+1,operation_id,run.batches[ordinal-1][3],1,db_now_ms(connection))
        _insert(connection,"pdf_visual_authoring_attempt",_ATTEMPT,tuple(asdict(a).values()))
        return AttemptReservation(a,True)

    def put_outcome(self,connection,run,prepared):
        state=self._bound(connection,run)
        _require(type(prepared) is PreparedVisualOutcome); _verify("outcome",_values(prepared),prepared.seal)
        _require(prepared.run_metadata_sha256==_hash(run.metadata_json))
        a=connection.execute("SELECT * FROM pdf_visual_authoring_attempt WHERE id=?",(prepared.attempt.id,)).fetchone()
        _require(_row(a,_ATTEMPT)==tuple(asdict(prepared.attempt).values()) and a["run_id"]==run.run_id)
        prior=connection.execute("SELECT * FROM pdf_visual_authoring_outcome WHERE attempt_id=?",(a["id"],)).fetchone()
        if prior is not None:
            body=connection.execute("SELECT * FROM pdf_visual_authoring_outcome_body WHERE attempt_id=?",(a["id"],)).fetchone()
            _require(_row(prior,_OUTCOME)==prepared.metadata and _row(body,_BODY)==prepared.body); return a["id"]
        _require(state["status"]=="pending")
        _insert(connection,"pdf_visual_authoring_outcome",(*_OUTCOME,"completed_at"),(*prepared.metadata,db_now_ms(connection)))
        _insert(connection,"pdf_visual_authoring_outcome_body",("attempt_id",*_BODY),(a["id"],*prepared.body))
        return a["id"]

    def load_receipts(self,connection,run):
        self._bound(connection,run)
        rows=connection.execute("""SELECT o.*,b.response,b.model_json,b.transport_json,b.attempt_json
            FROM pdf_visual_authoring_outcome o LEFT JOIN pdf_visual_authoring_outcome_body b ON b.attempt_id=o.attempt_id
            WHERE o.run_id=? AND o.status='validated' ORDER BY o.ordinal""",(run.run_id,)).fetchall()
        receipts=[]
        for row in rows:
            b=run.batches[row["ordinal"]-1]
            plan=run.source.plan
            # _bound has freshly verified the source/run seals and exact saved
            # batch reservations. V12 can legitimately own prose without any
            # model image; absence is not acceptable for an image-bearing job.
            text_only=(plan.policy_version in (VERSION_V12,VERSION_V13,VERSION_V14,VERSION_V15, VERSION_V16, VERSION_V17, VERSION_V18, VERSION_V19, VERSION_V20, VERSION_V21, VERSION_V22, VERSION_V23, VERSION_V24, VERSION_V25, VERSION_V26, VERSION_V27, VERSION_V28, VERSION_V29, VERSION_V30, VERSION_V31, VERSION_V32) and b[7]==0 and b[9]==0
                and plan.batches[row["ordinal"]-1].visual is False
                and plan.batches[row["ordinal"]-1].visual_pages==()
                and plan._layout_plan.jobs[row["ordinal"]-1].images==())
            if plan.policy_version == VERSION_V36:
                # V36 uses original V10 batches, not layout-retention jobs.
                # Fresh _bound verification above still owns source and seals.
                batch = plan.batches[row["ordinal"]-1]
                text_only = (b[7]==0 and b[9]==0 and batch.visual is False
                    and batch.visual_pages==()
                    and _visual_request_images(plan, batch)==())
            _require(row["evidence_binding"]=="matched"
                or (text_only and row["evidence_binding"]=="absent"))
            for key,prefix in zip(_BODY,("response","model","transport","evidence")):
                _require(type(row[key]) is str and len(row[key].encode())==row[prefix+"_size_bytes"] and _hash(row[key])==row[prefix+"_sha256"])
            model=_canonical_object(row["model_json"],4096)
            _require(set(model)<={"model_kind","model_id","model_used"} and all(type(v) is str for v in model.values()))
            transport=json.loads(row["transport_json"])
            if text_only and row["evidence_binding"]=="absent":
                _require(row["attempt_json"]=="{}" and transport.get("image_input_attempt",{})=={})
            else:
                _require(row["attempt_json"]==_json(transport["image_input_attempt"]))
            receipts.append(PdfVisualReceipt(b[1],b[3],row["response"],row["response_sha256"],
                tuple((k,model[k]) for k in ("model_kind","model_id","model_used") if k in model),row["transport_json"],
                policy_version=run.source.plan.policy_version))
        return tuple(receipts)

    def put_snapshot(self,connection,run,prepared):
        state=self._bound(connection,run)
        _require(type(prepared) is PreparedVisualSnapshot); _verify("snapshot",_values(prepared),prepared.seal)
        _require(prepared.run_id==run.run_id and prepared.run_metadata_sha256==_hash(run.metadata_json))
        receipts=self.load_receipts(connection,run)
        _require(len(receipts)==len(run.batches) and tuple(_hash(_json(asdict(r))) for r in receipts)==prepared.receipt_sha256s)
        fields=("id","run_id","content_sha256","content_size_bytes","manifest_sha256","manifest_size_bytes")
        values=(prepared.snapshot_id,run.run_id,_hash(prepared.markdown),len(prepared.markdown.encode()),_hash(prepared.manifest_json),len(prepared.manifest_json.encode()))
        prior=connection.execute("SELECT * FROM pdf_visual_wiki_snapshot WHERE run_id=?",(run.run_id,)).fetchone()
        if prior is not None:
            body=connection.execute("SELECT content_md,manifest_json FROM pdf_visual_wiki_snapshot_body WHERE snapshot_id=?",(prior["id"],)).fetchone()
            _require(state["status"]=="ready" and _row(prior,fields)==values and body is not None
                and tuple(body)==(prepared.markdown,prepared.manifest_json)); return prior["id"]
        _require(state["status"]=="pending"); now=db_now_ms(connection)
        _insert(connection,"pdf_visual_wiki_snapshot",(*fields,"created_at"),(*values,now))
        _insert(connection,"pdf_visual_wiki_snapshot_body",("snapshot_id","content_md","manifest_json"),(prepared.snapshot_id,prepared.markdown,prepared.manifest_json))
        connection.execute("UPDATE pdf_visual_authoring_state SET status='ready',completed_at=?,row_version=row_version+1 WHERE run_id=?",(now,run.run_id))
        return prepared.snapshot_id

    def fail_run(self,connection,run,*,failure_code):
        state=self._bound(connection,run); _require(failure_code in FAILURE_CODES)
        if state["status"]=="failed": _require(state["failure_code"]==failure_code); return
        _require(state["status"]=="pending")
        connection.execute("UPDATE pdf_visual_authoring_state SET status='failed',failure_code=?,completed_at=?,row_version=row_version+1 WHERE run_id=?",
            (failure_code,db_now_ms(connection),run.run_id))
