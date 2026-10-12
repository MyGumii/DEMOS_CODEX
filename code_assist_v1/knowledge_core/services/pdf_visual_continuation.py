"""Opt-in retained-output continuation with ordinary durable job fencing.

The caller explicitly requests a fresh finite continuation. Historical jobs,
deadlines, plans, raw responses and outcomes remain unchanged. New child inputs
are created only after captured typed provider context refusal. New v98-marked
requests can directly reference verified actual writes and inherit their real
partitions; existing unmarked v97 requests keep their original execution path.
"""
from contextlib import contextmanager
from dataclasses import replace
import json
from uuid import UUID
import logging
from functools import wraps

from ..db import db_now_ms, transaction, TransactionMode
from ..pdf_visual_continuation import (
    VERSION,TASK,PLAN,BATCH,COMPLETE,HANDLER_VERSION,ContinuationPolicy,
    checked_leaf,make_leaf,parent_inventory,subdivide,decode_response,assemble,
    identity,require,_hash,_json,
    _continuation_transport_permit,leaf_images,validate_output_receipt,validate_refusal_receipt,
    _prepare_inventory_and_leaves,
)
from ..repositories.pdf_visual_continuation import PdfContinuationRepository,ContinuationSourceAuthorityError
from ..repositories.pdf_continuation_retained import (
    is_retained_request,mark_retained_request,load_direct_evidence,
    put_result_reference,put_inherited_partition,retained_execution,
    assert_retained_execution,retained_ready,
)
from ..pdf_continuation_retained_plan import plan_retained_roots,RetainedPlanNode
from ..repositories.jobs import JobRepository,JobEnqueueDraft,ClaimFence
from ..scope import Capability
from ..worker import RegisteredJobHandler,PreparedJobEffect,PermanentJobExecutionError,RetryableJobExecutionError,JobExpectedStateConflictError
from ..wiki_authoring import AuthoringOutputError,AuthoringWorkLimitError,_model_receipt,_transport_receipt
from ..pdf_batch_markdown import UnboundMarkdownImageOutputError
from ..pdf_visual_authoring import (_api_policy,_codex_policy,_codex_prompt,
    PDF_VISUAL_TRANSIENT_FAILURES,PDF_VISUAL_INVALID_PROVIDER_FAILURES)
from ...llm_images import prepare_image_messages,image_input_receipt,bind_image_receipt
from ...llm_response import validate_chat_response_receipt,validate_chat_failure_diagnostics
from ...codex_response import validate_codex_response_receipt
from .pdf_visual_authoring_restore import _restore_pdf_visual_run

_LOG = logging.getLogger(__name__)


def _source_scoped(method):
    @wraps(method)
    def scoped(self,connection,job,*args,**kwargs):
        with self.source_authority(job.input_ref["request_id"]):
            return method(self,connection,job,*args,**kwargs)
    return scoped


class PdfContinuationWorkflow:
    def __init__(self, original_workflow):
        from .pdf_visual_authoring_workflow import PdfVisualAuthoringWorkflow
        require(type(original_workflow) is PdfVisualAuthoringWorkflow)
        self.original = original_workflow
        self._factory = original_workflow._factory
        self.repo = PdfContinuationRepository(original_workflow.repo)
        self.jobs = JobRepository()
        self._kernel = None

    @property
    def kernel(self):
        require(self._kernel is not None,"Bind continuation to the normal same-database kernel")
        return self._kernel

    def bind_kernel(self,kernel):
        require(self._kernel is None and kernel.database_path == self._factory.database_path)
        for handler in self.registered_handlers():
            require(kernel.registry.require(handler.key) == handler)
        self._kernel = kernel
        self.repo.bind_source_authority(kernel)

    def source_authority(self,request_id,*,publication=False):
        return self.repo.source_authority(self.kernel,request_id=request_id,publication=publication)

    @staticmethod
    def validate_input(value,expected):
        require(set(value) in ({"request_id"},{"request_id","leaf_id"})
            and set(expected) == {"schema_version","version","parent_run_id","source_archive_id",
                "source_fence_sha256","parent_plan_sha256","policy_sha256","_authorization_fence"}
            and expected["schema_version"] == 1 and expected["version"] == VERSION)
        for name in ("request_id","leaf_id"):
            if name in value: require(type(value[name]) is str and str(UUID(value[name])) == value[name])

    @staticmethod
    def _expected(request,root):
        return dict(schema_version=1,version=VERSION,parent_run_id=request["parent_run_id"],
            source_archive_id=request["source_archive_id"],source_fence_sha256=request["source_fence_sha256"],
            parent_plan_sha256=request["parent_plan_sha256"],policy_sha256=request["policy_sha256"],
            _authorization_fence=root.expected_state["_authorization_fence"])

    def _enqueue(self,connection,kind,value,request,root):
        effect = VERSION+":"+kind+":"+_hash(_json([request["id"],value,request["policy_sha256"]]))
        return self.jobs.enqueue(connection,JobEnqueueDraft(job_id=identity("job",effect),
            dedup_id=None,operation_id=None,actor_kind="user",actor_user_id=root.actor_user_id,
            space_id=root.space_id,job_type=kind,handler_version=HANDLER_VERSION,effect_key=effect,
            input_ref=value,expected_state=self._expected(request,root),
            authorization_snapshot=root.authorization_snapshot,
            authorization_scope_epoch=root.authorization_scope_epoch,
            authorization_part_id=root.authorization_part_id,
            authorization_capabilities=root.authorization_capabilities,max_attempts=2,cancellable=True,
            available_at_ms=db_now_ms(connection))).job

    def request(self,connection,*,parent_run_id,actor_user_id,operation_id,ttl_ms=3600000):
        """Explicit fresh request, in caller normal writer transaction."""
        with self.repo.source_authority(self.kernel,admission_connection=connection,
                parent_run_id=parent_run_id,actor_user_id=actor_user_id,ttl_ms=ttl_ms):
            return self._request_current(connection,parent_run_id=parent_run_id,
                actor_user_id=actor_user_id,operation_id=operation_id,ttl_ms=ttl_ms)

    def _request_current(self,connection,*,parent_run_id,actor_user_id,operation_id,ttl_ms):
        require(connection.in_transaction and connection.database_path == self._factory.database_path
            and type(ttl_ms) is int and 1 <= ttl_ms <= 86400000)
        run = connection.execute("SELECT r.*,s.status,s.failure_code FROM pdf_visual_authoring_run r "
            "JOIN pdf_visual_authoring_state s ON s.run_id=r.id WHERE r.id=?",(parent_run_id,)).fetchone()
        require(run is not None and run["actor_user_id"] == actor_user_id and run["status"] == "failed"
            and run["failure_code"] in ("MODEL_FAILURE","INVALID_OUTPUT","TIMEOUT","WORK_LIMIT"),
            "Only an authorized failed retained source can receive a fresh continuation")
        parent = self.kernel.revalidate_job_authority(connection,run["parent_job_id"])
        source = self.original.source._snapshot(connection,run["preview_id"],actor_user_id,now_ms=0)
        require(source.source_extension is None and parent.state == "succeeded"
            and source.fence_sha256 == run["source_fence_sha256"])
        # The code-owned actual writer prompt SHA is read from the retained
        # source configuration during PLAN restore; its immutable base prompt
        # plus admitted guidance is reconstructed without a model invocation.
        from ..pdf_batch_markdown import PROMPT,LITERAL_BOUNDED_OWNED_WRITER_GUIDANCE
        policy = ContinuationPolicy(_hash(PROMPT+LITERAL_BOUNDED_OWNED_WRITER_GUIDANCE))
        encoded = policy.encoded()
        now = db_now_ms(connection)
        request_id = identity("request",actor_user_id,parent_run_id,operation_id)
        previous = connection.execute("SELECT * FROM pdf_continuation_request WHERE id=?",(request_id,)).fetchone()
        if previous is not None:
            require(previous["operation_id"] == operation_id and previous["parent_run_id"] == parent_run_id
                and previous["actor_user_id"] == actor_user_id and previous["policy_json"] == encoded
                and previous["source_fence_sha256"] == source.fence_sha256
                and previous["deadline_at"]-previous["created_at"] == ttl_ms,
                "Continuation operation replay differs")
            return request_id,previous["root_job_id"]
        values = dict(id=request_id,operation_id=operation_id,parent_run_id=parent_run_id,
            parent_preview_id=run["preview_id"],root_job_id="",actor_user_id=actor_user_id,
            space_id=parent.space_id,source_archive_id=run["source_archive_id"],
            source_fence_sha256=source.fence_sha256,parent_plan_sha256=run["source_plan_sha256"],
            captured_scope_epoch=parent.authorization_scope_epoch,
            authorization_snapshot_json=parent.authorization_snapshot_json,
            authorization_snapshot_sha256=parent.authorization_snapshot_sha256,
            policy_json=encoded,policy_sha256=_hash(encoded),created_at=now,deadline_at=now+ttl_ms)
        root = self._enqueue(connection,PLAN,dict(request_id=request_id),values,parent)
        values["root_job_id"] = root.id
        values["created_at"] = root.created_at_ms
        values["deadline_at"] = root.created_at_ms+ttl_ms
        self.repo.put_request(connection,values)
        # Only a genuinely new post-v98 request is marked. An idempotent replay
        # above returns without upgrading any historical v97 request or job.
        if connection.execute("SELECT 1 FROM schema_migration WHERE version=98").fetchone():
            mark_retained_request(self.repo,connection,request_id)
        return request_id,root.id

    @_source_scoped
    def revalidate_expected_state(self,connection,job):
        try:
            self.validate_input(job.input_ref,job.expected_state)
            request,_ = self.repo.bound(connection,job.input_ref["request_id"],pending=True)
            root = self.jobs.require(connection,request["root_job_id"])
            self.kernel.revalidate_job_authority(connection,root.id)
            parent = self.kernel.revalidate_job_authority(connection,
                connection.execute("SELECT parent_job_id FROM pdf_visual_authoring_run WHERE id=?",
                    (request["parent_run_id"],)).fetchone()[0])
            source = self.original.source._snapshot(connection,request["parent_preview_id"],job.actor_user_id,now_ms=0)
            require(source.source_extension is None and source.fence_sha256 == request["source_fence_sha256"]
                and job.actor_user_id == request["actor_user_id"] and job.space_id == request["space_id"]
                and parent.authorization_scope_epoch == request["captured_scope_epoch"]
                and _json(job.expected_state) == _json(self._expected(request,root)))
            if job.job_type == PLAN: require(job.id == root.id)
            elif job.job_type == BATCH:
                require(root.state == "succeeded")
                leaf = connection.execute("SELECT job_id FROM pdf_continuation_leaf WHERE id=? AND request_id=?",
                    (job.input_ref["leaf_id"],request["id"])).fetchone()
                require(leaf is not None and leaf[0] == job.id)
            else:
                require(job.job_type == COMPLETE and root.state == "succeeded"
                    and connection.execute("SELECT 1 FROM pdf_continuation_completion WHERE request_id=? AND job_id=?",
                        (request["id"],job.id)).fetchone() is not None)
        except Exception as exc:
            raise JobExpectedStateConflictError("Continuation authority, source or finite deadline changed") from exc

    @staticmethod
    def _fence(job): return ClaimFence(job.id,job.claim_owner,job.attempt_count)

    def _current(self,connection,job):
        current = self.jobs.assert_effect_fence(connection,fence=self._fence(job),now_ms=db_now_ms(connection))
        self.kernel.revalidate_job_authority(connection,current.id)
        self.revalidate_expected_state(connection,current)
        return current

    @contextmanager
    def _restore(self,job,*,_preflight=None):
        with self.source_authority(job.input_ref["request_id"]):
            with self._restore_current(job,_preflight=_preflight) as run:
                yield run

    @contextmanager
    def _restore_current(self,job,*,_preflight=None):
        require(_preflight is None or callable(_preflight),"Invalid continuation preparation callback")
        fence = self._fence(job)
        preflight = _preflight if _preflight is not None else lambda:self.kernel.preflight_preparation(fence)
        preflight()
        self.kernel.renew_claim(fence,lease_ms=int((2*self.original.source._process_policy.timeout_seconds+60)*1000))
        connection = self._factory.open(read_only=True)
        try:
            with transaction(connection,TransactionMode.DEFERRED):
                request,_ = self.repo.bound(connection,job.input_ref["request_id"],pending=True)
        finally: connection.close()
        # Reuse the ordinary restore probe: only CPU/parser polling is
        # throttled (0.25s). Entry, restored-source handoff, provider calls and
        # exit still perform fresh authority/claim transactions. No permission,
        # source, result or live transaction is cached between callbacks.
        from .pdf_visual_authoring_workflow import _ParserCancellation
        cancellation = _ParserCancellation(preflight)
        run = None
        try:
            run = _restore_pdf_visual_run(self.original.source,self.original.archive,
                preview_id=request["parent_preview_id"],actor_user_id=job.actor_user_id,
                source_archive_id=request["source_archive_id"],max_executions=2,cancel_event=cancellation)
            if cancellation.error is not None:
                raise cancellation.error
            require(run.run_id == request["parent_run_id"] and "protected_binding" not in json.loads(run.source.configuration_json))
            preflight()
            yield run
            preflight()
        except BaseException as error:
            if cancellation.error is not None and cancellation.error is not error:
                raise cancellation.error from error
            raise
        finally:
            if run is not None: run.source.close()

    def prepare_plan(self,job):
        return self.kernel.prepare_with_read_guard(self._fence(job),
            lambda preflight:self._prepare_plan(job,preflight))

    def _prepare_plan(self,job,preflight):
        with self._restore(job,_preflight=preflight) as run:
            inventory,requests = _prepare_inventory_and_leaves(run.source.plan)
            connection = self._factory.open(read_only=True)
            try:
                with transaction(connection,TransactionMode.DEFERRED):
                    request,policy = self.repo.bound(connection,job.input_ref["request_id"],run,pending=True)
                    receipts = self.original.repo.load_receipts(connection,run)
                    validated = {r.request_sha256 for r in receipts}
                    ids = {row["ordinal"]:row["attempt_id"] for row in connection.execute(
                        "SELECT ordinal,attempt_id FROM pdf_visual_authoring_outcome WHERE run_id=? AND status='validated'",(run.run_id,))}
                    marked = is_retained_request(connection,request["id"])
                    tree = ()
                    if marked:
                        # The original source is restored once by this lexical
                        # preparation. Repository replay admits only direct
                        # actual writes from terminal failed same-source runs.
                        from .pdf_visual_authoring_workflow import _ParserCancellation
                        cancellation = _ParserCancellation(preflight)
                        evidence = load_direct_evidence(self.repo,connection,request["id"],run,
                            cancel_event=cancellation)
                        if cancellation.error is not None:
                            raise cancellation.error
                        remaining = tuple(leaf for leaf in requests if leaf.parent_ordinal not in ids)
                        original_roots = len(requests)-len(remaining)
                        if len(requests)>policy.max_leaves or (remaining and original_roots>=policy.max_leaves):
                            raise AuthoringWorkLimitError("Retained roots exceed the complete finite leaf reservation")
                        if remaining:
                            # Original parent reuse roots also count globally;
                            # inherited ancestors and every child consume the
                            # remaining admission budget in the pure planner.
                            planning_policy = replace(policy,max_leaves=policy.max_leaves-original_roots)
                            def materialize(parent,coverage,exact_parent):
                                preflight()
                                child = make_leaf(run.source.plan,parent.parent_ordinal,coverage,
                                    exact_parent=exact_parent)
                                preflight()
                                return child
                            def lookup(request_sha256):
                                preflight()
                                return evidence.get(request_sha256)
                            tree = plan_retained_roots(remaining,planning_policy,lookup,materialize)
            finally: connection.close()
            require(all(checked_leaf(leaf)["prompt_sha256"] == policy.prompt_sha256 for leaf in requests),
                "Retained liked writer prompt changed")
            return PreparedJobEffect(result=dict(schema_version=1,request_id=job.input_ref["request_id"],
                    parent_inventory_sha256=_hash(_json(inventory)),root_leaf_count=len(requests)),
                payload=(inventory,requests,ids,tree) if marked else (inventory,requests,ids))

    @_source_scoped
    def commit_plan(self,connection,job,prepared):
        self._current(connection,job)
        request,policy = self.repo.bound(connection,job.input_ref["request_id"],pending=True)
        marked = is_retained_request(connection,request["id"])
        if marked:
            inventory,requests,ids,tree = prepared.payload
            require(type(tree) is tuple and all(type(node) is RetainedPlanNode for node in tree),
                "Invalid retained source plan")
        else:
            inventory,requests,ids = prepared.payload
        from ..repositories.pdf_authoring import _insert
        encoded = _json(inventory)
        _insert(connection,"pdf_continuation_plan_body",("request_id","parent_inventory_json","parent_inventory_sha256"),
            (request["id"],encoded,_hash(encoded)))
        require(len(requests) == len(inventory))
        nodes = {node.leaf.parent_ordinal:node for node in tree} if marked else {}
        require(not marked or (len(nodes)==len(tree)
            and set(nodes)=={leaf.parent_ordinal for leaf in requests if leaf.parent_ordinal not in ids}),
            "Retained plan does not preserve every fresh original parent")
        admitted = []
        root = self.jobs.require(connection,request["root_job_id"]) if marked else None
        def enqueue_node(node,parent_leaf_id=None,depth=0):
            require(type(node) is RetainedPlanNode and node.relation in ("write","reference","partition"))
            if parent_leaf_id is None:
                leaf_id = identity("leaf",request["id"],node.leaf.parent_ordinal,node.leaf.coverage_json)
            else:
                leaf_id = identity("leaf",request["id"],parent_leaf_id,node.leaf.coverage_json)
            child = self._enqueue(connection,BATCH,dict(request_id=request["id"],leaf_id=leaf_id),request,root)
            self.repo.put_leaf(connection,request["id"],leaf_id,node.leaf,kind="write",job_id=child.id,
                parent_leaf_id=parent_leaf_id,depth=depth)
            admitted.append((node,leaf_id))
            for child_node in node.children:
                enqueue_node(child_node,leaf_id,depth+1)
        for leaf in requests:
            if not marked:
                root = self.jobs.require(connection,request["root_job_id"])
            leaf_id = identity("leaf",request["id"],leaf.parent_ordinal,leaf.coverage_json)
            if leaf.parent_ordinal in ids:
                # Reuse leaf identity is the old request SHA, not a new model
                # attempt. The SQL reference binds the exact old raw outcome.
                self.repo.put_leaf(connection,request["id"],leaf_id,leaf,kind="reuse",job_id=None)
                self.repo.put_reuse(connection,leaf_id,ids[leaf.parent_ordinal])
            elif marked:
                node = nodes[leaf.parent_ordinal]
                require(node.leaf.identity()==leaf.identity(),"Retained root input changed before admission")
                enqueue_node(node)
            else:
                child = self._enqueue(connection,BATCH,dict(request_id=request["id"],leaf_id=leaf_id),request,root)
                self.repo.put_leaf(connection,request["id"],leaf_id,leaf,kind="write",job_id=child.id)
        # All destination jobs/leaves/bodies are inserted parent-first before
        # any descriptors. SQL then authenticates full historical children,
        # while the normal PLAN claim and source authority are still current.
        for node,leaf_id in admitted:
            self._current(connection,job)
            if node.relation=="reference":
                require(not node.children)
                put_result_reference(self.repo,connection,request["id"],leaf_id,node.source_attempt_id)
            elif node.relation=="partition":
                require(2<=len(node.children)<=16)
                child_ids = [identity("leaf",request["id"],leaf_id,child.leaf.coverage_json)
                    for child in node.children]
                put_inherited_partition(self.repo,connection,request["id"],leaf_id,
                    node.source_attempt_id,child_ids)
            else:
                require(not node.children and node.source_attempt_id is None
                    and node.source_request_id is None and node.source_leaf_id is None)

    def _load_leaf(self,connection,request_id,leaf_id,run):
        self.repo.bound(connection,request_id,run)
        stored_inventory = connection.execute("SELECT * FROM pdf_continuation_plan_body WHERE request_id=?",
            (request_id,)).fetchone()
        row = connection.execute("SELECT l.*,b.payload_json FROM pdf_continuation_leaf l "
            "JOIN pdf_continuation_leaf_body b ON b.leaf_id=l.id WHERE l.id=? AND l.request_id=?",(leaf_id,request_id)).fetchone()
        require(row is not None and row["kind"] == "write")
        stored = json.loads(row["payload_json"])
        inventory,leaves = _prepare_inventory_and_leaves(run.source.plan,
            ((row["parent_ordinal"],json.loads(row["coverage_json"]),stored["exact_parent"]),))
        expected = _json(inventory)
        require(stored_inventory is not None and stored_inventory["parent_inventory_json"] == expected
            and stored_inventory["parent_inventory_sha256"] == _hash(expected), "Restored parent inventory differs")
        leaf = leaves[0]
        require(leaf.payload_json == row["payload_json"] and leaf.request_sha256 == row["request_sha256"]
            and leaf.input_size_bytes == row["input_size_bytes"])
        return dict(row),leaf

    def _author(self,run,leaf,guard):
        payload = checked_leaf(leaf)
        p = run.source.plan
        model = p.model_policy
        images = leaf_images(p,json.loads(leaf.coverage_json))
        options = dict(messages=payload["messages"],output_format="text",provider_order=list(model.provider_order),
            model_ids=None if model.model_ids is None else list(model.model_ids),max_tokens=4096,temperature=.1,
            max_attempts=1,timeout_s=p.execution_policy.timeout_s,api_timeout_s=p.execution_policy.timeout_s,
            request_budget_bytes=4194304,preserve_codex_input=True,validate_output=None,
            capture_request_receipt=True,capture_failure_diagnostics=True,
            context_refusal_version=VERSION,context_request_sha256=leaf.request_sha256,
            attempt_guard=guard)
        if images: options.update(image_inputs=images,image_policy=p.source.render_policy.image_policy(len(images)),image_attempt_receipt={})
        options["_pdf_continuation_transport"] = _continuation_transport_permit(leaf,model)
        options.update(response_policy=_api_policy()) if model.provider_order == ("hynix_api",) else options.update(codex_response_policy=_codex_policy(p))
        response,metadata = self.original.author.runner(TASK,payload,**options)
        require(type(metadata) is dict,"Missing continuation transport")
        transport = {k:metadata[k] for k in ("request_transport","response_transport","codex_response_transport",
            "image_input_transport","image_input_attempt","context_refusal") if k in metadata}
        model_values = {k:metadata[k] for k in ("model_kind","model_id","model_used") if type(metadata.get(k)) is str}
        if response is None and "context_refusal" in transport:
            validate_refusal_receipt(p,leaf,transport)
            return dict(status="context_refused",failure_code="CONTEXT_LIMIT",response="",model_json=_json(model_values),transport_json=_json(transport))
        try:
            require(type(response) is str and 0 < len(response.encode()) <= 65536,"Incomplete continuation response")
        except Exception as error:
            # The runner normally returns (None, content-free metadata) on
            # provider failure. Preserve bounded diagnostic accounting without
            # admitting partial text, provider prose or an unvalidated receipt.
            # Successful receipts and typed context refusal keep their exact
            # existing contracts; this is a failed outcome, never a split or
            # retry permission. Historical records are not changed.
            failure_transport = {}
            codes = metadata.get("failure_codes")
            if type(codes) is list and len(codes) <= 16:
                allowed_codes = frozenset(PDF_VISUAL_TRANSIENT_FAILURES) | PDF_VISUAL_INVALID_PROVIDER_FAILURES
                accepted = [code for code in codes if type(code) is str
                    and code in allowed_codes]
                failure_transport["failure_codes"] = accepted
                if len(accepted) != len(codes):
                    failure_transport["invalid_failure_code_count"] = len(codes)-len(accepted)
            elif codes is not None:
                failure_transport["failure_codes_invalid"] = True
            if "provider_failure_diagnostics" in metadata:
                try:
                    diagnostic = validate_chat_failure_diagnostics(metadata["provider_failure_diagnostics"])
                    require(len(_json(diagnostic).encode()) <= 8192,"Failure diagnostics exceed accounting budget")
                    failure_transport["provider_failure_diagnostics"] = diagnostic
                except Exception:
                    failure_transport["provider_failure_diagnostics_invalid"] = True
            error.continuation_raw_outcome = dict(status="failed",failure_code="MODEL_FAILURE",
                response="",model_json="{}",transport_json=_json(failure_transport))
            raise
        retained = dict(response=response,model_json=_json(model_values),transport_json=_json(transport))
        try:
            projected,projection,decoded = validate_output_receipt(p,leaf,response,
                retained["model_json"],retained["transport_json"],stored=False)
            transport["markdown_decode"] = decoded
            transport["output_projection"] = projection
            retained["transport_json"] = _json(transport)
            guard()
            return dict(status="validated",failure_code=None,**retained)
        except Exception as error:
            error.continuation_raw_outcome = dict(status="failed",failure_code="INVALID_OUTPUT",**retained)
            raise

    def prepare_batch(self,job):
        return self.kernel.prepare_with_read_guard(self._fence(job),
            lambda preflight:self._prepare_batch(job,preflight))

    def _prepare_batch(self,job,preflight):
        with self._restore(job,_preflight=preflight) as run:
            connection = self._factory.open()
            try:
                with transaction(connection):
                    self._current(connection,job)
                    request,policy = self.repo.bound(connection,job.input_ref["request_id"],run,pending=True)
                    row,leaf = self._load_leaf(connection,request["id"],job.input_ref["leaf_id"],run)
                    execution = (retained_execution(self.repo,connection,request["id"],row["id"],run)
                        if is_retained_request(connection,request["id"]) else None)
                    if execution is None:
                        attempt = self.repo.start_attempt(connection,request["id"],row["id"],job)
            finally: connection.close()
            fence = self._fence(job)
            self.kernel.renew_claim(fence,lease_ms=int((2*run.source.plan.execution_policy.timeout_s+60)*1000))
            guard = preflight
            if execution is not None:
                require(type(execution) is dict and set(execution)==
                    {"schema_version","request_id","leaf_id","source_attempt_id","status"}
                    and type(execution["schema_version"]) is int and execution["schema_version"]==2
                    and execution["request_id"]==request["id"] and execution["leaf_id"]==row["id"]
                    and execution["status"] in ("referenced_result","inherited_partition"),
                    "Invalid retained job execution")
                source_attempt_id = execution.get("source_attempt_id")
                require(type(source_attempt_id) is str and str(UUID(source_attempt_id))==source_attempt_id)
                guard()
                # This is fresh guarded job execution, not a model invocation:
                # no own attempt, outcome, provider response or refusal exists.
                result = dict(schema_version=2,request_id=request["id"],leaf_id=row["id"],
                    source_attempt_id=source_attempt_id,status=execution["status"])
                return PreparedJobEffect(result=result,
                    payload=("retained_execution",source_attempt_id,execution["status"]))
            try:
                outcome = self._author(run,leaf,guard)
                children = ()
                if outcome["status"] == "context_refused":
                    if row["depth"] >= policy.max_depth:
                        raise AuthoringWorkLimitError("Continuation finite subdivision depth exhausted")
                    children = subdivide(run.source.plan,leaf,policy)
                    connection = self._factory.open(read_only=True)
                    try:
                        count = connection.execute("SELECT count(*) FROM pdf_continuation_leaf WHERE request_id=?",(request["id"],)).fetchone()[0]
                    finally: connection.close()
                    if count+len(children) > policy.max_leaves:
                        raise AuthoringWorkLimitError("Continuation finite leaf reservation exhausted")
                return PreparedJobEffect(result=dict(schema_version=1,request_id=request["id"],
                    leaf_id=row["id"],attempt_id=attempt["id"],status=outcome["status"]),payload=(attempt,outcome,children,row["depth"]))
            except Exception as error:
                outcome = (outcome if 'outcome' in locals() and outcome["status"] == "context_refused"
                    else getattr(error,"continuation_raw_outcome",dict(status="failed",failure_code="WORK_LIMIT"
                        if isinstance(error,AuthoringWorkLimitError) else "MODEL_FAILURE",response="",model_json="{}",transport_json="{}")))
                connection = self._factory.open()
                try:
                    with transaction(connection):
                        self._current(connection,job)
                        self.repo.put_outcome(connection,attempt,**outcome)
                finally: connection.close()
                if isinstance(error,UnboundMarkdownImageOutputError) and attempt["sequence"] == 1:
                    raise RetryableJobExecutionError("Rejected unbound image markup; one identical writing retry remains") from error
                raise PermanentJobExecutionError("Continuation write failed; exact retained results remain available") from error

    @_source_scoped
    def commit_batch(self,connection,job,prepared):
        self._current(connection,job)
        request,_ = self.repo.bound(connection,job.input_ref["request_id"],pending=True)
        if prepared.result.get("schema_version")==2:
            result = prepared.result
            require(type(result) is dict and set(result)==
                {"schema_version","request_id","leaf_id","source_attempt_id","status"}
                and type(result["schema_version"]) is int and result["schema_version"]==2
                and result["request_id"]==request["id"] and result["leaf_id"]==job.input_ref["leaf_id"]
                and result["status"] in ("referenced_result","inherited_partition")
                and prepared.payload==("retained_execution",result["source_attempt_id"],result["status"])
                and is_retained_request(connection,request["id"]),
                "Invalid no-provider retained job effect")
            assert_retained_execution(self.repo,connection,request["id"],job.input_ref["leaf_id"],
                result["source_attempt_id"],result["status"])
            # The normal durable kernel commits the fresh schema2 job result;
            # it must satisfy the v98 succeeded-job gate for this exact ref.
            return
        attempt,outcome,children,depth = prepared.payload
        self.repo.put_outcome(connection,attempt,**outcome)
        if children:
            root = self.jobs.require(connection,request["root_job_id"])
            child_ids = []
            for leaf in children:
                leaf_id = identity("leaf",request["id"],job.input_ref["leaf_id"],leaf.coverage_json)
                child_job = self._enqueue(connection,BATCH,dict(request_id=request["id"],leaf_id=leaf_id),request,root)
                self.repo.put_leaf(connection,request["id"],leaf_id,leaf,kind="write",job_id=child_job.id,
                    parent_leaf_id=job.input_ref["leaf_id"],depth=depth+1)
                child_ids.append(leaf_id)
            self.repo.put_split(connection,job.input_ref["leaf_id"],attempt["id"],child_ids)

    def reconcile_pending(self,connection):
        for stopped in connection.execute("SELECT q.root_job_id FROM pdf_continuation_request q "
                "JOIN pdf_continuation_state s ON s.request_id=q.id JOIN pdf_continuation_stop stop ON stop.request_id=q.id "
                "WHERE s.status='pending'").fetchall():
            self.converge_terminal(connection,self.jobs.require(connection,stopped[0]))
        # Expired invocations consume their original attempt reservation. They
        # are never rerun invisibly or mistaken for complete stored outputs.
        expired = connection.execute("""SELECT a.* FROM pdf_continuation_attempt a
            JOIN durable_job j ON j.id=a.job_id LEFT JOIN pdf_continuation_outcome o ON o.attempt_id=a.id
            WHERE o.attempt_id IS NULL AND (j.attempt_count>a.claim_attempt OR
              (j.state IN ('claimed','running') AND j.claim_expires_at<=?))""",(db_now_ms(connection),)).fetchall()
        for attempt in expired:
            self._abandon(connection,dict(attempt),"LEASE_LOST")
        # One automatic forward request per fresh ordinary failed parent. This
        # is new authority/TTL, not resurrection or retry-until-good. Historical
        # non-V14 and protected plans remain on their existing paths.
        for row in connection.execute("""SELECT r.id,r.actor_user_id FROM pdf_visual_authoring_run r
                JOIN pdf_visual_authoring_state s ON s.run_id=r.id
                JOIN pdf_visual_authoring_run_body body ON body.run_id=r.id
                JOIN publication_preview preview ON preview.id=r.preview_id
                WHERE s.status='failed' AND s.failure_code IN ('MODEL_FAILURE','INVALID_OUTPUT','TIMEOUT','WORK_LIMIT')
                  AND preview.created_at>=(SELECT applied_at FROM schema_migration WHERE version=97)
                  AND json_extract(body.configuration_json,'$.request_partition_policy')='pdf-original-range-request-partition-v14'
                  AND json_type(body.configuration_json,'$.protected_binding') IS NULL
                  AND NOT EXISTS(SELECT 1 FROM pdf_continuation_request q WHERE q.parent_run_id=r.id)
                  AND NOT EXISTS(SELECT 1 FROM pdf_visual_authoring_attempt a LEFT JOIN pdf_visual_authoring_outcome o
                    ON o.attempt_id=a.id WHERE a.run_id=r.id AND o.attempt_id IS NULL)
                ORDER BY s.completed_at,r.id LIMIT 16"""):
            try:
                # A savepoint keeps ineligible current-authority candidates
                # from leaving queued jobs, without weakening the SQL guard.
                connection.execute("SAVEPOINT continuation_admission")
                self.request(connection,parent_run_id=row["id"],actor_user_id=row["actor_user_id"],
                    operation_id=identity("automatic-continuation",row["id"],VERSION))
                connection.execute("RELEASE continuation_admission")
            except Exception as error:
                connection.execute("ROLLBACK TO continuation_admission")
                connection.execute("RELEASE continuation_admission")
                # Metadata only. Do not log raw response, source contents or
                # provider error prose; schema/programming failures stay visible.
                _LOG.warning("Continuation admission rejected: parent=%s exception=%s",row["id"],type(error).__name__)
        for row in connection.execute("SELECT q.id FROM pdf_continuation_request q JOIN pdf_continuation_state s "
                "ON s.request_id=q.id JOIN durable_job j ON j.id=q.root_job_id WHERE s.status='pending' AND j.state='succeeded'"):
            deadline = connection.execute("SELECT deadline_at,root_job_id FROM pdf_continuation_request WHERE id=?",(row[0],)).fetchone()
            if deadline[0] <= db_now_ms(connection):
                self._stop(connection,self.jobs.require(connection,deadline[1]),"TIMEOUT")
                self.converge_terminal(connection,self.jobs.require(connection,deadline[1]))
                continue
            request,_ = self.repo.bound(connection,row[0],pending=True)
            if connection.execute("SELECT 1 FROM pdf_continuation_completion WHERE request_id=?",(row[0],)).fetchone(): continue
            if is_retained_request(connection,row[0]):
                # Includes every active actual/reference result and successful
                # inherited parent jobs; no own attempt is fabricated for refs.
                ready = retained_ready(self.repo,connection,row[0])
            else:
                leaves = self.repo.active(connection,row[0])
                ready = leaves and all(l["kind"] == "reuse" or connection.execute(
                        "SELECT 1 FROM pdf_continuation_attempt a JOIN pdf_continuation_outcome o ON o.attempt_id=a.id "
                        "JOIN durable_job j ON j.id=a.job_id WHERE a.leaf_id=? AND o.status='validated' AND j.state='succeeded'",(l["id"],)).fetchone() for l in leaves)
            if ready:
                root = self.jobs.require(connection,request["root_job_id"])
                child = self._enqueue(connection,COMPLETE,dict(request_id=request["id"]),request,root)
                connection.execute("INSERT INTO pdf_continuation_completion VALUES(?,?)",(row[0],child.id))

    def prepare_completion(self,job):
        return self.kernel.prepare_with_read_guard(self._fence(job),
            lambda preflight:self._prepare_completion(job,preflight))

    def _prepare_completion(self,job,preflight):
        with self._restore(job,_preflight=preflight) as run:
            connection = self._factory.open(read_only=True)
            try:
                with transaction(connection,TransactionMode.DEFERRED):
                    request,policy = self.repo.bound(connection,job.input_ref["request_id"],run,pending=True)
                    markdown,manifest = self.repo.prepare_snapshot(connection,request["id"],run)
            finally: connection.close()
            snapshot_id = identity("snapshot",request["id"])
            return PreparedJobEffect(result=dict(schema_version=1,request_id=request["id"],snapshot_id=snapshot_id,
                content_sha256=_hash(markdown),manifest_sha256=_hash(manifest)),payload=(markdown,manifest))

    @_source_scoped
    def commit_completion(self,connection,job,prepared):
        self._current(connection,job)
        self.repo.put_snapshot(connection,job.input_ref["request_id"],identity("snapshot",job.input_ref["request_id"]),*prepared.payload)

    def _abandon(self,connection,attempt,code):
        if connection.execute("SELECT 1 FROM pdf_continuation_outcome WHERE attempt_id=?",(attempt["id"],)).fetchone():
            return
        retained = connection.execute("""SELECT 1 FROM pdf_continuation_leaf l
            JOIN pdf_continuation_request q ON q.id=l.request_id JOIN pdf_visual_authoring_run r ON r.id=q.parent_run_id
            JOIN extraction_snapshot_body e ON e.snapshot_id=r.extraction_snapshot_id
            JOIN pdf_visual_source_body s ON s.source_id=q.source_archive_id WHERE l.id=?""",(attempt["leaf_id"],)).fetchone()
        self.repo.put_outcome(connection,attempt,status="abandoned",failure_code=code,retain_body=retained is not None)

    def _stop(self,connection,job,code):
        request_id = job.input_ref["request_id"]
        exists = connection.execute("SELECT 1 FROM pdf_continuation_stop WHERE request_id=?",(request_id,)).fetchone()
        if exists is None:
            connection.execute("INSERT INTO pdf_continuation_stop VALUES(?,?,?,?)",(request_id,job.id,code,db_now_ms(connection)))
        else:
            code = connection.execute("SELECT failure_code FROM pdf_continuation_stop WHERE request_id=?",(request_id,)).fetchone()[0]
        for attempt in connection.execute("""SELECT a.* FROM pdf_continuation_attempt a
            JOIN pdf_continuation_leaf l ON l.id=a.leaf_id LEFT JOIN pdf_continuation_outcome o ON o.attempt_id=a.id
            WHERE l.request_id=? AND o.attempt_id IS NULL""",(request_id,)).fetchall():
            self._abandon(connection,dict(attempt),code)

    def prepare_terminal(self,connection,job,terminal_state):
        state = connection.execute("SELECT status FROM pdf_continuation_state WHERE request_id=?",(job.input_ref["request_id"],)).fetchone()
        if state is None or state[0] != "pending": return
        failure = connection.execute("SELECT o.status,o.failure_code FROM pdf_continuation_attempt a "
            "JOIN pdf_continuation_outcome o ON o.attempt_id=a.id WHERE a.job_id=? ORDER BY a.sequence DESC LIMIT 1",(job.id,)).fetchone()
        code = "CANCELLED" if terminal_state == "cancelled" else (
            "WORK_LIMIT" if failure is not None and failure[0] == "context_refused" else
            failure[1] if failure is not None and failure[1] else "MODEL_FAILURE")
        deadline = connection.execute("SELECT deadline_at FROM pdf_continuation_request WHERE id=?",(job.input_ref["request_id"],)).fetchone()
        if code == "MODEL_FAILURE" and deadline[0] <= db_now_ms(connection): code = "TIMEOUT"
        if code == "MODEL_FAILURE":
            try:
                self.revalidate_expected_state(connection,job)
            except JobExpectedStateConflictError as exc:
                # A lost source/current authorization is not a model failure.
                # Classify the future terminal record using the actual guard
                # cause; existing outcomes and failed requests are untouched.
                from .pdf_authoring_source import PdfSourceConflict
                causes = []
                current = exc
                while current is not None and all(current is not prior for prior in causes):
                    causes.append(current)
                    current = current.__cause__
                code = "AUTHORITY_UNAVAILABLE" if any(isinstance(cause,PermissionError) for cause in causes) else (
                    "SOURCE_UNAVAILABLE" if any(isinstance(cause,PdfSourceConflict) for cause in causes)
                    else "AUTHORITY_UNAVAILABLE")
        if code == "MODEL_FAILURE" and job.job_type == BATCH:
            try:
                self.repo.check_attempt_budget(connection,job.input_ref["request_id"],job.input_ref["leaf_id"])
            except AuthoringWorkLimitError:
                code = "WORK_LIMIT"
        self._stop(connection,job,code)

    def converge_terminal(self,connection,job):
        request_id = job.input_ref["request_id"]
        state = connection.execute("SELECT status FROM pdf_continuation_state WHERE request_id=?",(request_id,)).fetchone()
        if state is not None and state[0] == "pending":
            stop = connection.execute("SELECT failure_code FROM pdf_continuation_stop WHERE request_id=?",(request_id,)).fetchone()
            require(stop is not None)
            connection.execute("UPDATE pdf_continuation_state SET status='failed',failure_code=?,completed_at=?,row_version=row_version+1 WHERE request_id=?",
                (stop[0],db_now_ms(connection),request_id))
            for row in connection.execute("SELECT id FROM durable_job WHERE json_extract(input_ref_json,'$.request_id')=? "
                    "AND state IN ('queued','retry','claimed','running')",(request_id,)):
                self.jobs.request_cancel(connection,job_id=row[0],now_ms=db_now_ms(connection))

    def registered_handlers(self):
        shared = dict(actor_kind="user",required_capability=Capability.PUBLISH,validate_input=self.validate_input,
            revalidate_expected_state=self.revalidate_expected_state,prepare_terminal=self.prepare_terminal,
            converge_terminal=self.converge_terminal)
        return tuple(RegisteredJobHandler.build(job_type=kind,handler_version=HANDLER_VERSION,
            prepare=prepare,commit_effect=commit,reconcile_pending=self.reconcile_pending if kind == PLAN else self._noop_reconcile,**shared)
            for kind,prepare,commit in ((PLAN,self.prepare_plan,self.commit_plan),(BATCH,self.prepare_batch,self.commit_batch),
                (COMPLETE,self.prepare_completion,self.commit_completion)))

    @staticmethod
    def _noop_reconcile(connection):
        return None

