"""Additive continuation storage using normal source, claim and SQL guards."""
from dataclasses import asdict, dataclass
from contextlib import contextmanager
from contextvars import ContextVar
import asyncio
import hmac
import json
import secrets
import threading

from ..db import db_now_ms, _database_file_identity
from ..pdf_visual_continuation import (
    VERSION, identity, require, checked_leaf, checked_coverage, exact_union,
    parent_inventory, ContinuationPolicy, _hash, _json,make_leaf,validate_output_receipt,
)
from ..wiki_authoring import AuthoringWorkLimitError
from .artifacts import _connection
from .pdf_authoring import _insert


def _execution_identity():
    try:
        task = asyncio.current_task()
    except RuntimeError:
        task = None
    return threading.get_ident(), id(task) if task is not None else None


class ContinuationSourceAuthorityError(PermissionError):
    """Current finite continuation authority, not model output, is unavailable."""


@dataclass(frozen=True, slots=True)
class _SourceAuthority:
    repository: object
    source: object
    factory: object
    database_identity: tuple
    kernel: object
    request_id: str | None
    publication: bool
    admission_connection: object | None
    admission_parent_run_id: str | None
    admission_actor_user_id: str | None
    admission_deadline: int | None
    execution_identity: tuple
    nonce: str
    seal: str


def _authority_identity(authority):
    return repr((id(authority.repository), id(authority.source), id(authority.factory),
        authority.database_identity,id(authority.kernel),
        authority.request_id, authority.publication, id(authority.admission_connection),
        authority.admission_parent_run_id, authority.admission_actor_user_id,
        authority.admission_deadline, authority.execution_identity, authority.nonce)).encode()


def _current_source_authority(source):
    context = getattr(source, "_pdf_continuation_source_authority", None)
    value = None if context is None else context.get()
    if value is not None and (type(value) is not _SourceAuthority or value.source is not source):
        raise ContinuationSourceAuthorityError("Continuation authority belongs to another source instance")
    return value


def _check_source_authority(connection, authority, *, preview_id, actor_user_id, now_ms):
    if type(authority) is not _SourceAuthority:
        raise ContinuationSourceAuthorityError("A sealed continuation source authority is required")
    authority.repository._validate_source_authority(connection, authority,
        preview_id=preview_id, actor_user_id=actor_user_id, now_ms=now_ms)


class PdfContinuationRepository:
    def __init__(self, original_repository):
        from .pdf_visual_authoring import PdfVisualAuthoringRepository
        require(type(original_repository) is PdfVisualAuthoringRepository)
        self.original = original_repository
        self._source_authority_key = secrets.token_bytes(32)
        self._source_authority_active = set()
        self._source_authority_kernel = None

    def bind_source_authority(self,kernel):
        require(self._source_authority_kernel is None
            and kernel.database_path == self.original.source_service._factory.database_path)
        self._source_authority_kernel = kernel
        source = self.original.source_service
        if getattr(source,"_pdf_continuation_source_authority",None) is None:
            source._pdf_continuation_source_authority = ContextVar("pdf_continuation_source_authority",default=None)

    def publication_authority(self,request_id):
        require(self._source_authority_kernel is not None)
        return self.source_authority(self._source_authority_kernel,request_id=request_id,publication=True)

    @contextmanager
    def source_authority(self, kernel, *, request_id=None, publication=False,
            admission_connection=None, parent_run_id=None, actor_user_id=None, ttl_ms=None):
        """Lexical, source-instance-only authority; no historical clock is changed.

        Admission is tied to this exact managed writer transaction and cannot
        authorize an invocation. Existing requests recheck their current source,
        authorization, stop state and finite deadline on EVERY nested source read.
        """
        source = self.original.source_service
        require(type(publication) is bool and kernel is self._source_authority_kernel
            and kernel.database_path == source._factory.database_path)
        admission = admission_connection is not None
        require((admission and request_id is None and not publication
                and type(ttl_ms) is int and 1 <= ttl_ms <= 86400000)
            or (not admission and type(request_id) is str
                and parent_run_id is None and actor_user_id is None and ttl_ms is None))
        if admission:
            _connection(admission_connection,write=True)
            require(admission_connection.in_transaction
                and admission_connection.database_path == kernel.database_path)
        context = getattr(source, "_pdf_continuation_source_authority", None)
        if context is None:
            context = ContextVar("pdf_continuation_source_authority", default=None)
            source._pdf_continuation_source_authority = context
        values = dict(repository=self,source=source,factory=source._factory,
            database_identity=_database_file_identity(source._factory.database_path),
            kernel=kernel,request_id=request_id,
            publication=publication,admission_connection=admission_connection,
            admission_parent_run_id=parent_run_id,admission_actor_user_id=actor_user_id,
            admission_deadline=db_now_ms(admission_connection)+ttl_ms if admission else None,
            execution_identity=_execution_identity(),nonce=secrets.token_hex(24),seal="")
        authority = _SourceAuthority(**values)
        values["seal"] = hmac.digest(self._source_authority_key,_authority_identity(authority),"sha256").hex()
        authority = _SourceAuthority(**values)
        self._source_authority_active.add(authority.nonce)
        token = context.set(authority)
        try:
            yield authority
        finally:
            context.reset(token)
            self._source_authority_active.discard(authority.nonce)

    def _validate_source_authority(self, connection, authority, *, preview_id, actor_user_id, now_ms):
        from ..migrations.v097_pdf_context_resume import _source_current
        try:
            _connection(connection,write=True)
            source = self.original.source_service
            require(authority.repository is self and authority.source is source
                and authority.nonce in self._source_authority_active
                and authority.execution_identity == _execution_identity()
                and _current_source_authority(source) is authority
                and hmac.compare_digest(authority.seal,
                    hmac.digest(self._source_authority_key,_authority_identity(authority),"sha256").hex())
                and connection.in_transaction and connection.database_path == source._factory.database_path
                and authority.kernel.database_path == source._factory.database_path
                and authority.kernel is self._source_authority_kernel
                and authority.factory is source._factory
                and authority.database_identity == connection.database_identity
                and connection.execute("SELECT 1 FROM schema_migration WHERE version=97").fetchone() is not None)
            now = max(now_ms,db_now_ms(connection))
            if authority.admission_connection is not None:
                require(connection is authority.admission_connection and now < authority.admission_deadline
                    and authority.request_id is None and not authority.publication)
                old = connection.execute("SELECT * FROM pdf_visual_authoring_run WHERE id=?",
                    (authority.admission_parent_run_id,)).fetchone()
                require(old is not None and old["actor_user_id"] == authority.admission_actor_user_id == actor_user_id
                    and old["preview_id"] == preview_id)
                parent = authority.kernel.revalidate_job_authority(connection,old["parent_job_id"])
                require(parent.state == "succeeded" and not parent.cancel_requested)
                q = dict(parent_run_id=old["id"],parent_preview_id=old["preview_id"],
                    actor_user_id=old["actor_user_id"],space_id=parent.space_id,
                    source_archive_id=old["source_archive_id"],source_fence_sha256=old["source_fence_sha256"],
                    parent_plan_sha256=old["source_plan_sha256"],captured_scope_epoch=parent.authorization_scope_epoch)
                predicate = "SELECT 1 FROM (SELECT "+",".join("? AS "+key for key in q)+") q WHERE "+_source_current("q")
                require(connection.execute(predicate,tuple(q.values())).fetchone() is not None)
            else:
                q,policy = self.bound(connection,authority.request_id)
                require(q["parent_preview_id"] == preview_id and q["actor_user_id"] == actor_user_id
                    and q["deadline_at"] > now and 0 < q["deadline_at"]-q["created_at"] <= policy.max_deadline_ms
                    and q["status"] == ("ready" if authority.publication else "pending")
                    and connection.execute("SELECT 1 FROM pdf_continuation_stop WHERE request_id=?",(q["id"],)).fetchone() is None
                    and connection.execute("SELECT 1 FROM pdf_continuation_request q WHERE q.id=? AND ("+
                        _source_current("q")+")",(q["id"],)).fetchone() is not None)
                root = authority.kernel.revalidate_job_authority(connection,q["root_job_id"])
                parent_id = connection.execute("SELECT parent_job_id FROM pdf_visual_authoring_run WHERE id=?",
                    (q["parent_run_id"],)).fetchone()[0]
                parent = authority.kernel.revalidate_job_authority(connection,parent_id)
                require(parent.state == "succeeded" and not parent.cancel_requested
                    and root.state in (("succeeded",) if authority.publication else ("queued","claimed","running","succeeded","retry"))
                    and not root.cancel_requested and root.actor_user_id == actor_user_id and root.space_id == q["space_id"]
                    and root.authorization_scope_epoch == q["captured_scope_epoch"]
                    and root.authorization_snapshot_json == q["authorization_snapshot_json"]
                    and root.authorization_snapshot_sha256 == q["authorization_snapshot_sha256"])
                if authority.publication:
                    from .publication_body_request import requested_publication_body
                    selected = requested_publication_body(connection,actor_id=actor_user_id,preview_id=preview_id)
                    require(selected.kind == "pdf_continuation_wiki" and selected.run_id == q["id"]
                        and connection.execute("SELECT 1 FROM pdf_continuation_publication_candidate WHERE request_id=? "
                            "AND preview_id=? AND actor_user_id=? AND snapshot_id=?",(q["id"],preview_id,actor_user_id,selected.snapshot_id)).fetchone() is not None)
        except ContinuationSourceAuthorityError:
            raise
        except Exception as exc:
            raise ContinuationSourceAuthorityError("Current continuation source authority is unavailable") from exc

    def bound(self, connection, request_id, run=None, *, pending=False):
        _connection(connection,write=True)
        row = connection.execute("SELECT q.*,s.status,s.failure_code FROM pdf_continuation_request q "
            "JOIN pdf_continuation_state s ON s.request_id=q.id WHERE q.id=?",(request_id,)).fetchone()
        require(row is not None, "Continuation request is unavailable")
        value = dict(row)
        require(_hash(value["policy_json"]) == value["policy_sha256"]
            and _hash(value["authorization_snapshot_json"]) == value["authorization_snapshot_sha256"])
        policy = ContinuationPolicy(**json.loads(value["policy_json"]))
        require(policy.encoded() == value["policy_json"])
        if run is not None:
            old_state = self.original._bound(connection,run)
            require(old_state["status"] == "failed" and run.run_id == value["parent_run_id"]
                and run.source.preview_id == value["parent_preview_id"]
                and run.source.actor_user_id == value["actor_user_id"]
                and run.source_archive_id == value["source_archive_id"]
                and run.source.fence_sha256 == value["source_fence_sha256"]
                and run.source.plan.plan_sha256 == value["parent_plan_sha256"]
                and "protected_binding" not in json.loads(run.source.configuration_json),
                "Continuation parent source or authority changed")
        if pending:
            require(value["status"] == "pending" and value["deadline_at"] > db_now_ms(connection)
                and connection.execute("SELECT 1 FROM pdf_continuation_stop WHERE request_id=?",(request_id,)).fetchone() is None,
                "Continuation stopped, expired or terminal")
        return value,policy

    def put_request(self, connection, values):
        _connection(connection,write=True)
        fields = ("id","operation_id","parent_run_id","parent_preview_id","root_job_id",
            "actor_user_id","space_id","source_archive_id","source_fence_sha256",
            "parent_plan_sha256","captured_scope_epoch","authorization_snapshot_json",
            "authorization_snapshot_sha256","policy_json","policy_sha256","created_at","deadline_at")
        require(set(values) == set(fields))
        _insert(connection,"pdf_continuation_request",fields,tuple(values[k] for k in fields))
        _insert(connection,"pdf_continuation_state",("request_id","status","row_version"),
            (values["id"],"pending",1))

    def put_inventory(self, connection, request_id, run):
        self.bound(connection,request_id,run,pending=True)
        encoded = _json(parent_inventory(run.source.plan))
        _insert(connection,"pdf_continuation_plan_body",
            ("request_id","parent_inventory_json","parent_inventory_sha256"),
            (request_id,encoded,_hash(encoded)))

    def inventory(self, connection, request_id, run):
        self.bound(connection,request_id,run)
        row = connection.execute("SELECT * FROM pdf_continuation_plan_body WHERE request_id=?",(request_id,)).fetchone()
        expected = _json(parent_inventory(run.source.plan))
        require(row is not None and row["parent_inventory_json"] == expected
            and row["parent_inventory_sha256"] == _hash(expected), "Restored parent inventory differs")
        return json.loads(expected)

    def put_leaf(self, connection, request_id, leaf_id, leaf, *, kind, job_id,
            parent_leaf_id=None, depth=0):
        payload = checked_leaf(leaf)
        self.bound(connection,request_id,pending=True)
        now = db_now_ms(connection)
        _insert(connection,"pdf_continuation_leaf",("id","request_id","parent_ordinal",
            "parent_leaf_id","depth","kind","job_id","coverage_json","coverage_sha256",
            "request_sha256","input_size_bytes","created_at"),
            (leaf_id,request_id,leaf.parent_ordinal,parent_leaf_id,depth,kind,job_id,
                leaf.coverage_json,_hash(leaf.coverage_json),
                payload["parent_request_sha256"] if kind == "reuse" else leaf.request_sha256,
                leaf.input_size_bytes,now))
        if kind == "write":
            _insert(connection,"pdf_continuation_leaf_body",("leaf_id","payload_json"),(leaf_id,leaf.payload_json))

    def put_reuse(self, connection, leaf_id, parent_attempt_id):
        row = connection.execute("SELECT o.* FROM pdf_visual_authoring_outcome o WHERE o.attempt_id=? AND o.status='validated'",
            (parent_attempt_id,)).fetchone()
        require(row is not None,"Reuse requires a retained validated original outcome")
        _insert(connection,"pdf_continuation_reuse",("leaf_id","parent_attempt_id","request_sha256",
            "response_sha256","model_sha256","transport_sha256"),
            (leaf_id,parent_attempt_id,connection.execute("SELECT request_sha256 FROM pdf_visual_authoring_attempt WHERE id=?",
                (parent_attempt_id,)).fetchone()[0],row["response_sha256"],row["model_sha256"],row["transport_sha256"]))

    def start_attempt(self, connection, request_id, leaf_id, job):
        self.bound(connection,request_id,pending=True)
        leaf = connection.execute("SELECT * FROM pdf_continuation_leaf WHERE id=? AND request_id=?",(leaf_id,request_id)).fetchone()
        require(leaf is not None and leaf["kind"] == "write" and leaf["job_id"] == job.id)
        self.check_attempt_budget(connection,request_id,leaf_id)
        operation = identity("execution",job.id,job.attempt_count)
        require(connection.execute("SELECT 1 FROM pdf_continuation_attempt WHERE operation_id=?",(operation,)).fetchone() is None,
            "A reserved continuation claim cannot execute twice")
        sequence = connection.execute("SELECT count(*)+1 FROM pdf_continuation_attempt WHERE leaf_id=?",(leaf_id,)).fetchone()[0]
        attempt = dict(id=identity("attempt",operation),operation_id=operation,leaf_id=leaf_id,
            job_id=job.id,claim_owner=job.claim_owner,claim_attempt=job.attempt_count,
            sequence=sequence,request_sha256=leaf["request_sha256"],started_at=db_now_ms(connection))
        _insert(connection,"pdf_continuation_attempt",tuple(attempt),tuple(attempt.values()))
        return attempt

    def check_attempt_budget(self,connection,request_id,leaf_id):
        _,policy = self.bound(connection,request_id)
        leaf = connection.execute("SELECT input_size_bytes FROM pdf_continuation_leaf WHERE id=? AND request_id=?",(leaf_id,request_id)).fetchone()
        require(leaf is not None)
        totals = connection.execute("SELECT count(*),coalesce(sum(l.input_size_bytes),0) FROM pdf_continuation_attempt a "
            "JOIN pdf_continuation_leaf l ON l.id=a.leaf_id WHERE l.request_id=?",(request_id,)).fetchone()
        per_leaf = connection.execute("SELECT count(*) FROM pdf_continuation_attempt WHERE leaf_id=?",(leaf_id,)).fetchone()[0]
        if (per_leaf >= policy.max_executions or totals[0] >= policy.max_model_attempts
                or totals[1]+leaf[0] > policy.max_input_bytes or 65536*(totals[0]+1) > policy.max_response_bytes):
            raise AuthoringWorkLimitError("Continuation finite invocation/input/response reservation exhausted")

    def put_outcome(self, connection, attempt, *, status, failure_code,
            response="",model_json="{}",transport_json="{}",retain_body=True):
        for encoded in (model_json,transport_json):
            require(type(encoded) is str and _json(json.loads(encoded)) == encoded)
        body = dict(response=response,model_json=model_json,transport_json=transport_json)
        values = dict(attempt_id=attempt["id"],status=status,failure_code=failure_code)
        for field,prefix in (("response","response"),("model_json","model"),("transport_json","transport")):
            values[prefix+"_sha256"] = _hash(body[field])
            values[prefix+"_size_bytes"] = len(body[field].encode())
        values["completed_at"] = db_now_ms(connection)
        _insert(connection,"pdf_continuation_outcome",tuple(values),tuple(values.values()))
        if retain_body:
            _insert(connection,"pdf_continuation_outcome_body",("attempt_id",*body),(attempt["id"],*body.values()))

    def put_split(self, connection, parent_leaf_id, refused_attempt_id, children):
        parent = connection.execute("SELECT coverage_json FROM pdf_continuation_leaf WHERE id=?",(parent_leaf_id,)).fetchone()
        require(parent is not None)
        rows = [connection.execute("SELECT coverage_json FROM pdf_continuation_leaf WHERE id=?",(child,)).fetchone() for child in children]
        require(all(r is not None for r in rows))
        exact_union(json.loads(parent[0]),[json.loads(r[0]) for r in rows])
        encoded = _json(children)
        _insert(connection,"pdf_continuation_split",("parent_leaf_id","refused_attempt_id",
            "children_json","children_sha256","created_at"),
            (parent_leaf_id,refused_attempt_id,encoded,_hash(encoded),db_now_ms(connection)))

    def active(self, connection, request_id):
        from .pdf_continuation_retained import is_retained_request, retained_active
        if is_retained_request(connection, request_id):
            return retained_active(connection, request_id)
        return tuple(dict(row) for row in connection.execute("""SELECT l.* FROM pdf_continuation_leaf l
            WHERE l.request_id=? AND NOT EXISTS(SELECT 1 FROM pdf_continuation_split s WHERE s.parent_leaf_id=l.id)
              AND (l.parent_leaf_id IS NULL OR EXISTS(SELECT 1 FROM pdf_continuation_split s,json_each(s.children_json) c
                WHERE s.parent_leaf_id=l.parent_leaf_id AND c.value=l.id))
            ORDER BY l.parent_ordinal,l.depth,l.coverage_json,l.id""",(request_id,)))

    def results(self, connection, request_id, run):
        from .pdf_continuation_retained import is_retained_request, retained_results
        if is_retained_request(connection, request_id):
            return retained_results(self, connection, request_id, run)
        self.inventory(connection,request_id,run)
        # Normal old repository checks every old source/transport/body binding.
        old = self.original.load_receipts(connection,run)
        by_request = {r.request_sha256:r for r in old}
        result = []
        for leaf in self.active(connection,request_id):
            projection = None
            if leaf["kind"] == "reuse":
                row = connection.execute("SELECT * FROM pdf_continuation_reuse WHERE leaf_id=?",(leaf["id"],)).fetchone()
                require(row is not None and row["request_sha256"] in by_request)
                receipt = by_request[row["request_sha256"]]
                require(receipt.batch_id == run.source.plan.batches[leaf["parent_ordinal"]-1].batch_id
                    and receipt.request_sha256 == row["request_sha256"]
                    and receipt.response_sha256 == row["response_sha256"]
                    and _hash(_json(dict(receipt.model))) == row["model_sha256"]
                    and _hash(receipt.transport_json) == row["transport_sha256"])
                response,sha = receipt.response,receipt.response_sha256
                ref = dict(kind="reuse",parent_attempt_id=row["parent_attempt_id"])
                source_attempt_id,model_sha,transport_sha = row["parent_attempt_id"],row["model_sha256"],row["transport_sha256"]
            else:
                row = connection.execute("""SELECT a.id,o.*,b.* FROM pdf_continuation_attempt a
                    JOIN pdf_continuation_outcome o ON o.attempt_id=a.id
                    LEFT JOIN pdf_continuation_outcome_body b ON b.attempt_id=a.id
                    WHERE a.leaf_id=? AND o.status='validated'""",(leaf["id"],)).fetchone()
                require(row is not None,"Not every final continuation leaf has a validated retained result")
                require(type(row["response"]) is str and _hash(row["response"]) == row["response_sha256"]
                    and _hash(row["model_json"]) == row["model_sha256"]
                    and _hash(row["transport_json"]) == row["transport_sha256"],"Retained continuation output differs")
                response,sha = row["response"],row["response_sha256"]
                body = connection.execute("SELECT payload_json FROM pdf_continuation_leaf_body WHERE leaf_id=?",(leaf["id"],)).fetchone()
                require(body is not None)
                saved = json.loads(body[0])
                restored = make_leaf(run.source.plan,leaf["parent_ordinal"],json.loads(leaf["coverage_json"]),exact_parent=saved["exact_parent"])
                require(restored.payload_json == body[0] and restored.request_sha256 == leaf["request_sha256"]
                    and restored.input_size_bytes == leaf["input_size_bytes"],"Continuation input replay differs")
                projected,projection,decoded = validate_output_receipt(run.source.plan,restored,
                    response,row["model_json"],row["transport_json"])
                ref = dict(kind="write",attempt_id=row["attempt_id"])
                source_attempt_id,model_sha,transport_sha = row["attempt_id"],row["model_sha256"],row["transport_sha256"]
            result.append(dict(parent_ordinal=leaf["parent_ordinal"],coverage_json=leaf["coverage_json"],
                leaf_id=leaf["id"],request_sha256=leaf["request_sha256"],response=response,response_sha256=sha,reference=ref,
                kind=leaf["kind"],source_attempt_id=source_attempt_id,model_sha256=model_sha,transport_sha256=transport_sha))
            if projection is not None: result[-1]["output_projection"] = projection
        def order(row):
            coverage = json.loads(row["coverage_json"])
            ranges = coverage["ranges"]
            return (row["parent_ordinal"],min(((r["chunk_ordinal"],r["start"]) for r in ranges),default=(10**9,0)),row["leaf_id"])
        return tuple(sorted(result,key=order))

    @staticmethod
    def snapshot_manifest(request,markdown,assembly_manifest,results):
        fields = ("leaf_id","kind","parent_ordinal","source_attempt_id","request_sha256",
            "response_sha256","model_sha256","transport_sha256")
        retained = bool(results) and all("execution_source" in row for row in results)
        if retained:
            fields += ("execution_source", "origin_request_id", "origin_leaf_id")
        return _json(dict(schema_version=2 if retained else 1,version=VERSION,request_id=request["id"],
            parent_run_id=request["parent_run_id"],parent_plan_sha256=request["parent_plan_sha256"],
            source_fence_sha256=request["source_fence_sha256"],wiki_sha256=_hash(markdown),wiki_bytes=len(markdown.encode()),
            leaves=[{k:row[k] for k in fields} for row in results],assembly_manifest=json.loads(assembly_manifest),
            all_original_ranges_retained=True,all_detected_table_cells_rendered=True,
            all_visual_ownership_retained=True,semantic_review_performed=False,semantic_completeness_proven=False))

    def prepare_snapshot(self,connection,request_id,run):
        from ..pdf_visual_continuation import assemble
        from .pdf_continuation_retained import is_retained_request, retained_assembly
        request,policy = self.bound(connection,request_id,run)
        results = self.results(connection,request_id,run)
        if is_retained_request(connection, request_id):
            markdown, manifest = retained_assembly(self, connection, request_id, run, results, policy)
        else:
            markdown,manifest = assemble(run.source.plan,results,policy)
        return markdown,self.snapshot_manifest(request,markdown,manifest,results)

    def put_snapshot(self, connection, request_id, snapshot_id, markdown, manifest):
        self.bound(connection,request_id,pending=True)
        now = db_now_ms(connection)
        _insert(connection,"pdf_continuation_snapshot",("id","request_id","content_sha256",
            "content_size_bytes","manifest_sha256","manifest_size_bytes","created_at"),
            (snapshot_id,request_id,_hash(markdown),len(markdown.encode()),_hash(manifest),len(manifest.encode()),now))
        _insert(connection,"pdf_continuation_snapshot_body",("snapshot_id","content_md","manifest_json"),
            (snapshot_id,markdown,manifest))
        connection.execute("UPDATE pdf_continuation_state SET status='ready',completed_at=?,row_version=row_version+1 WHERE request_id=?",
            (now,request_id))
