"""Opt-in continuation publication through the ordinary atomic publisher."""
from dataclasses import dataclass, replace

from ..db import db_now_ms, transaction, TransactionMode
from ..migrations.pdf_context_resume_catalog import PDF_CONTEXT_RESUME_MIGRATION_CATALOG
from ..repositories.publication_body_request import (
    has_pdf_continuation_work, requested_publication_body,
    RequestedBodyPendingError, RequestedBodyUnavailableError,
)
from ..repositories.publication_commit import PublicationCommitIntegrityError, PublicationCommitConflictError, publication_operation_id
from ..repositories.cross_kind_publication import selection_row, selected_result_fields
from ..repositories.pdf_visual_continuation_publication_commit import (
    FrozenPdfContinuationPublicationSource, PdfContinuationPublicationCommitRepository,
    PdfContinuationOfficeHybridPublicationCommitRepository,
)
from ..scope import Capability, ScopeResolutionError, SessionPrincipal
from .pdf_visual_publication import PdfVisualPublicationService
from .office_hybrid_publication import OfficeHybridPublicationService
from .published_upload_body import CanonicalPublishedUploadBodyService
from .wiki_publication import WikiPublicationSelection, WikiPublicationSelector
from .publication import (
    PublicationApiService, PublicationPreviewServiceError,
    PublicationPreviewAuthorizationError, GetPublicationPreviewQuery,
    _preview_uuid, _preview_user, _preview_epoch,
)
from ..repositories.users import UserRepository
from ..repositories.publication import PublicationPreviewRepository
from .pdf_visual_continuation import PdfContinuationWorkflow
from .pdf_visual_authoring_restore import _restore_pdf_visual_run


def _candidate_current(connection, preview, now_ms):
    rows = connection.execute("""SELECT * FROM pdf_continuation_publication_candidate
        WHERE preview_id=? AND actor_user_id=?""", (preview.preview_id,preview.actor_user_id)).fetchall()
    return (len(rows)==1 and preview.status=="ready" and rows[0]["deadline_at"]>now_ms
        and (rows[0]["artifact_id"],rows[0]["extraction_snapshot_id"],rows[0]["source_snapshot_sha256"],
             rows[0]["captured_scope_epoch"]) ==
            (preview.artifact_id,preview.extraction_snapshot_id,preview.source_snapshot_sha256,preview.captured_scope_epoch))


@dataclass(frozen=True, slots=True)
class PdfContinuationPublicationSelection(WikiPublicationSelection):
    pdf_continuation_snapshot_id: str | None = None
    progress: dict | None = None
    failure: dict | None = None

    def overlay(self, preview):
        result = {"wiki_generation":{"schema_version":9,"kind":"pdf_source_continuation",
            "status":self.status,"semantic_review_performed":False,"semantic_completeness_proven":False},
            "publication_ready":self.publication_ready}
        if self.progress is not None:
            result["generation_progress"] = self.progress
        if self.failure is not None:
            result["generation_error"] = self.failure
        return result

    def require_publishable(self):
        super().require_publishable()
        return None  # Never put a continuation into the legacy authored slot.


class PdfContinuationPublicationSelector(WikiPublicationSelector):
    def __init__(self, factory, predecessor):
        super().__init__(factory)
        if not isinstance(predecessor,WikiPublicationSelector) or predecessor.database_path!=self.database_path:
            raise TypeError("Continuation selection needs the same-database predecessor.")
        self.predecessor = predecessor

    def has_request(self, connection, preview_id):
        return has_pdf_continuation_work(connection,preview_id) or self.predecessor.has_request(connection,preview_id)

    @staticmethod
    def _progress(connection, preview):
        from ..migrations.v097_pdf_context_resume import _source_current
        requests = connection.execute("""SELECT q.*,s.status,s.failure_code,j.state AS job_state,
            j.claim_expires_at,j.cancel_requested FROM pdf_continuation_request q
            JOIN pdf_continuation_state s ON s.request_id=q.id JOIN durable_job j ON j.id=q.root_job_id
            WHERE q.parent_preview_id=? AND q.actor_user_id=?""",
            (preview.preview_id,preview.actor_user_id)).fetchall()
        now = db_now_ms(connection)
        active = [request for request in requests if request["status"] in ("pending","ready")]
        if active:
            requests = active
        if len(requests)!=1:
            return dict(phase="failed",completed_batches=0,total_batches=0,queued_batches=0,
                running_batches=0,recovery_pending=False),dict(code="PROCESSING_FAILED",retryable=False)
        request = requests[0]
        from ..repositories.pdf_continuation_retained import is_retained_request
        if is_retained_request(connection, request["id"]):
            from ..migrations.v098_pdf_terminal_result_reuse import _active, _reference_ready
            from ..pdf_visual_continuation import ContinuationPolicy
            import json
            policy = ContinuationPolicy(**json.loads(request["policy_json"]))
            policy.encoded()
            rows = connection.execute("""SELECT l.kind,j.state,j.claim_expires_at,
                (EXISTS(SELECT 1 FROM pdf_continuation_reuse reuse WHERE reuse.leaf_id=l.id)
                 OR (j.state='succeeded' AND EXISTS(SELECT 1 FROM pdf_continuation_attempt attempt
                     JOIN pdf_continuation_outcome outcome ON outcome.attempt_id=attempt.id
                     WHERE attempt.leaf_id=l.id AND outcome.status='validated'))
                 OR (""" + _reference_ready() + """)) AS completed
                FROM pdf_continuation_request q JOIN pdf_continuation_leaf l ON l.request_id=q.id
                LEFT JOIN durable_job j ON j.id=l.job_id
                WHERE q.id=? AND (""" + _active() + ") LIMIT ?",
                (request["id"], policy.max_leaves + 1)).fetchall()
            if len(rows) > policy.max_leaves:
                raise PublicationCommitIntegrityError("The retained progress exceeds its finite leaf contract.")
        else:
            rows = connection.execute("""SELECT l.kind,j.state,j.claim_expires_at,
            (EXISTS(SELECT 1 FROM pdf_continuation_reuse reuse WHERE reuse.leaf_id=l.id)
             OR (j.state='succeeded' AND EXISTS(SELECT 1 FROM pdf_continuation_attempt attempt
                 JOIN pdf_continuation_outcome outcome ON outcome.attempt_id=attempt.id
                 WHERE attempt.leaf_id=l.id AND outcome.status='validated'))) AS completed
            FROM pdf_continuation_leaf l LEFT JOIN durable_job j ON j.id=l.job_id
            WHERE l.request_id=? AND NOT EXISTS(SELECT 1 FROM pdf_continuation_split split WHERE split.parent_leaf_id=l.id)
              AND (l.parent_leaf_id IS NULL OR EXISTS(SELECT 1 FROM pdf_continuation_split split,json_each(split.children_json) child
                  WHERE split.parent_leaf_id=l.parent_leaf_id AND child.value=l.id)) LIMIT 100001""",(request["id"],)).fetchall()
        if len(rows)>100000:
            raise PublicationCommitIntegrityError("The continuation progress exceeds its finite ownership contract.")
        completed = sum(bool(row["completed"]) for row in rows)
        queued = sum(not row["completed"] and row["state"] in ("queued","retry") for row in rows)
        running = sum(not row["completed"] and row["state"] in ("claimed","running") for row in rows)
        recovering = any(not row["completed"] and row["state"] in ("claimed","running")
            and row["claim_expires_at"] is not None and row["claim_expires_at"]<=now for row in rows)
        if not rows:
            recovering = bool(request["job_state"] in ("claimed","running")
                and request["claim_expires_at"] is not None and request["claim_expires_at"]<=now)
        phase = ("planning" if request["job_state"] in ("claimed","running") else "planning_queued") if not rows else (
            "writing" if running else "writing_queued")
        if rows and completed==len(rows):
            finish = connection.execute("""SELECT j.state,j.claim_expires_at FROM pdf_continuation_completion c
                JOIN durable_job j ON j.id=c.job_id WHERE c.request_id=?""",(request["id"],)).fetchone()
            phase = "assembling" if finish is not None and finish["state"] in ("claimed","running") else "assembling_queued"
            recovering = bool(finish is not None and finish["state"] in ("claimed","running")
                and finish["claim_expires_at"] is not None and finish["claim_expires_at"]<=now)
        if request["status"]=="ready":
            phase,recovering = "ready",False
        stop = connection.execute("SELECT failure_code FROM pdf_continuation_stop WHERE request_id=?",(request["id"],)).fetchone()
        code = stop[0] if stop is not None else request["failure_code"]
        if request["cancel_requested"] or request["job_state"]=="cancelled" or preview.status=="cancelled":
            code = "CANCELLED"
        elif code is None and preview.status!="consumed" and now>=request["deadline_at"]:
            code = "EXPIRED"
        elif code is None and preview.status!="consumed" and not connection.execute(
                "SELECT 1 FROM pdf_continuation_request q WHERE q.id=? AND ("+_source_current('q')+")",(request["id"],)).fetchone():
            code = "AUTHORITY_UNAVAILABLE"
        elif request["job_state"] in ("failed","dead") and code is None:
            code = "PROCESSING_FAILED"
        failure = None
        if code is not None:
            allowed = {"MODEL_FAILURE","INVALID_OUTPUT","TIMEOUT","WORK_LIMIT","SOURCE_UNAVAILABLE",
                "AUTHORITY_UNAVAILABLE","CANCELLED","LEASE_LOST","EXPIRED"}
            code = code if code in allowed else "PROCESSING_FAILED"
            failure = dict(code=code,retryable=code in {"MODEL_FAILURE","TIMEOUT","LEASE_LOST"})
            phase,recovering = "failed",False
        elif recovering:
            phase = "recovery_pending"
        return dict(phase=phase,completed_batches=completed,total_batches=len(rows),queued_batches=queued,
            running_batches=running,recovery_pending=recovering),failure

    def select(self, connection, preview):
        if not has_pdf_continuation_work(connection,preview.preview_id):
            return self.predecessor.select(connection,preview)
        progress,failure = self._progress(connection,preview)
        def selection(status, **kwargs):
            return PdfContinuationPublicationSelection(status,progress=progress,failure=failure,**kwargs)
        if failure is not None:
            return selection("unavailable")
        try:
            requested = requested_publication_body(connection,actor_id=preview.actor_user_id,preview_id=preview.preview_id)
        except RequestedBodyPendingError:
            return selection("pending")
        except RequestedBodyUnavailableError:
            return selection("unavailable")
        ready = _candidate_current(connection,preview,db_now_ms(connection))
        return selection("ready" if ready or preview.status=="consumed" else "unavailable",
            publication_ready=ready,pdf_continuation_snapshot_id=requested.snapshot_id)

    def replay_options(self, connection, *, actor_id, operation_id, stored, requested_authored_snapshot_id):
        row = selection_row(connection,publication_operation_id(actor_id,operation_id))
        if row is None or row["requested_kind"]!="pdf_continuation_wiki":
            return self.predecessor.replay_options(connection,actor_id=actor_id,operation_id=operation_id,
                stored=stored,requested_authored_snapshot_id=requested_authored_snapshot_id)
        fields = selected_result_fields(row)
        if (stored.publication_operation_id!=row["publication_operation_id"] or stored.preview_id!=row["preview_id"]
                or requested_authored_snapshot_id is not None or stored.body_source!=row["selected_kind"]
                or any(getattr(stored,key)!=value for key,value in fields.items())):
            raise PublicationCommitIntegrityError("The continuation replay result differs from its immutable receipt.")
        return {"body_selection":True}


class _ContinuationPublicBodies(CanonicalPublishedUploadBodyService):
    def _authority(self, connection, binding, targets):
        if not has_pdf_continuation_work(connection,binding.preview_id):
            return super()._authority(connection,binding,targets)
        preview = self._previews.require_for_actor(connection,preview_id=binding.preview_id,actor_user_id=binding.actor_user_id)
        if (not _candidate_current(connection,preview,db_now_ms(connection))
                or (preview.artifact_id,preview.extraction_snapshot_id,preview.source_snapshot_sha256)!=
                    (binding.artifact_id,binding.extraction_snapshot_id,binding.source_snapshot_sha256)):
            raise PublicationCommitConflictError("The continuation no longer authorizes body preparation.")
        principal = SessionPrincipal(user_id=binding.actor_user_id,captured_scope_epoch=preview.captured_scope_epoch)
        fence = []
        try:
            for target in sorted(targets,key=lambda t:t.space_id):
                resolved = self._scope.resolve_in_transaction(connection,principal,target.space_id,Capability.PUBLISH)
                plan = resolved.access_plan
                if (resolved.space.kind!=target.kind or plan is None or plan.native_mutation_space_id!=target.space_id
                        or target.kind=="part" and resolved.current_part_id!=preview.captured_part_id):
                    raise PublicationCommitConflictError("The public reuse target is not currently authorized.")
                fence.append((target.space_id,resolved.scope_epoch,resolved.actor_row_version,resolved.current_part_id,
                    plan.role_assignment_id,plan.role_assignment_generation,plan.corpus_closure.organization_snapshot_version))
        except ScopeResolutionError as exc:
            raise PublicationCommitConflictError("Current scope does not authorize public body reuse.") from exc
        return (preview.row_version,tuple(fence))


class _ContinuationPublisher:
    def __init__(self, factory, scope_resolver, stager, *, continuation, **kwargs):
        expected = PDF_CONTEXT_RESUME_MIGRATION_CATALOG.migrations
        if factory.migration_catalog.migrations[:len(expected)]!=expected:
            raise ValueError("Continuation publication requires the exact v97 successor catalog.")
        if type(continuation) is not PdfContinuationWorkflow or continuation._factory.database_path!=factory.database_path:
            raise TypeError("Continuation publication needs its same-database normal workflow.")
        super().__init__(factory,scope_resolver,stager,**kwargs)
        self._continuation = continuation
        predecessor = self._wiki_publication
        self._public_bodies = _ContinuationPublicBodies(factory,scope_resolver,stager)
        args = (self._commits.native_source,self._public_bodies,self._pdf_source,self._pdf_visual_source,self._visual_archive)
        if isinstance(self,OfficeHybridPublicationService):
            self._commits = PdfContinuationOfficeHybridPublicationCommitRepository(
                *args,self._office_source,continuation_repository=continuation.repo)
        else:
            self._commits = PdfContinuationPublicationCommitRepository(*args,continuation_repository=continuation.repo)
        self._wiki_publication = PdfContinuationPublicationSelector(factory,predecessor)

    def _publication_preview_current(self, connection, preview, now_ms):
        if has_pdf_continuation_work(connection,preview.preview_id):
            return _candidate_current(connection,preview,now_ms)
        return super()._publication_preview_current(connection,preview,now_ms)

    def _freeze_new_publication_source(self, **fields):
        composed = super()._freeze_new_publication_source(**fields)
        own = getattr(composed,"own_candidate",None)
        if type(own) is not FrozenPdfContinuationPublicationSource:
            return composed
        with self._continuation.source_authority(own.request_id,publication=True):
            return self._replay_continuation_source(composed,own,fields)

    def _replay_continuation_source(self,composed,own,fields):
        stack = self._body_context.get()
        if stack is None:
            raise PublicationPreviewServiceError("Continuation publication needs its owning lease scope.")
        try:
            connection = self._factory.open(read_only=True)
            try:
                with transaction(connection,TransactionMode.DEFERRED):
                    row = connection.execute("""SELECT q.source_archive_id,r.max_executions
                        FROM pdf_continuation_request q JOIN pdf_visual_authoring_run r ON r.id=q.parent_run_id
                        WHERE q.id=? AND q.actor_user_id=? AND q.parent_preview_id=?""",
                        (own.request_id,fields["actor_id"],fields["preview_id"])).fetchone()
                    if row is None or row["max_executions"] not in (1,2):
                        raise PublicationCommitIntegrityError("The continuation retained run is unavailable.")
            finally:
                connection.close()
            original = self._continuation.original
            run = _restore_pdf_visual_run(original.source,original.archive,preview_id=fields["preview_id"],
                actor_user_id=fields["actor_id"],source_archive_id=row["source_archive_id"],max_executions=row["max_executions"])
            try:
                stack.enter_context(run.source)
            except BaseException:
                run.source.close()
                raise
            connection = self._factory.open(read_only=True)
            try:
                with transaction(connection,TransactionMode.DEFERRED):
                    markdown,manifest = self._continuation.repo.prepare_snapshot(connection,own.request_id,run)
            finally:
                connection.close()
            verified = self._commits.bind_replayed_continuation(own,run,markdown,manifest)
            composed = replace(composed,own_candidate=verified,preparation_seal="")
            return replace(composed,preparation_seal=self._commits._seal(composed))
        except Exception as exc:
            # Stored private inputs are never reflected in the client message.
            raise PublicationPreviewServiceError("The retained continuation source and writing receipts could not be verified.") from exc


class PdfContinuationPublicationService(_ContinuationPublisher, PdfVisualPublicationService):
    pass


class PdfContinuationOfficeHybridPublicationService(_ContinuationPublisher, OfficeHybridPublicationService):
    pass


@dataclass(frozen=True, slots=True)
class ResumePdfPreviewCommand:
    request_id: str
    operation_id: str
    actor_user_id: str
    actor_scope_epoch: int
    preview_id: str


class PdfContinuationPublicationApiService(PublicationApiService):
    """Explicit same-preview resume; authority and finite work stay in the workflow."""

    __slots__ = ("_continuation",)

    def __init__(self, previews, commits, *, continuation):
        if (type(continuation) is not PdfContinuationWorkflow
                or not isinstance(commits, (PdfContinuationPublicationService,
                    PdfContinuationOfficeHybridPublicationService))
                or commits._continuation is not continuation
                or commits._factory is not continuation._factory
                or previews._factory is not continuation._factory):
            raise TypeError("PDF resume requires its same-database continuation publisher.")
        # Composition binds the exact handler instance before exposing this API.
        continuation.kernel
        super().__init__(previews, commits)
        self._continuation = continuation

    def resume_pdf_preview(self, command):
        if type(command) is not ResumePdfPreviewCommand:
            raise TypeError("A ResumePdfPreviewCommand is required.")
        _preview_uuid(command.request_id, field_name="request_id")
        operation = _preview_uuid(command.operation_id, field_name="operation_id")
        actor_id = _preview_user(command.actor_user_id)
        epoch = _preview_epoch(command.actor_scope_epoch)
        preview_id = _preview_uuid(command.preview_id, field_name="preview_id")
        workflow = self._continuation
        connection = workflow._factory.open()
        try:
            with transaction(connection):
                actor = UserRepository().require(connection, actor_id)
                if actor.account_status != "active" or actor.scope_epoch != epoch:
                    raise PublicationPreviewAuthorizationError(
                        "Current canonical actor state does not authorize PDF resume.")
                preview = PublicationPreviewRepository().require_for_actor(
                    connection, preview_id=preview_id, actor_user_id=actor_id)
                if preview.status != "ready":
                    raise PublicationPreviewServiceError("The retained PDF preview is not available for resume.")
                parents = connection.execute("""SELECT r.id FROM pdf_visual_authoring_run r
                    JOIN pdf_visual_authoring_state s ON s.run_id=r.id
                    JOIN pdf_visual_authoring_run_body b ON b.run_id=r.id
                    WHERE r.preview_id=? AND r.actor_user_id=? AND s.status='failed'
                      AND s.failure_code IN ('MODEL_FAILURE','INVALID_OUTPUT','TIMEOUT','WORK_LIMIT')
                      AND json_extract(b.configuration_json,'$.request_partition_policy')
                        ='pdf-original-range-request-partition-v14'
                      AND json_type(b.configuration_json,'$.protected_binding') IS NULL
                      AND NOT EXISTS(SELECT 1 FROM pdf_visual_authoring_attempt a
                        LEFT JOIN pdf_visual_authoring_outcome o ON o.attempt_id=a.id
                        WHERE a.run_id=r.id AND o.attempt_id IS NULL)""",
                    (preview_id, actor_id)).fetchall()
                if len(parents) != 1:
                    raise PublicationPreviewServiceError("One eligible retained ordinary PDF run is required.")
                parent_id = parents[0]["id"]
                current = connection.execute("""SELECT q.id,q.deadline_at,s.status
                    FROM pdf_continuation_request q JOIN pdf_continuation_state s ON s.request_id=q.id
                    WHERE q.parent_run_id=? AND q.parent_preview_id=? AND q.actor_user_id=?
                      AND s.status IN ('pending','ready')""",
                    (parent_id, preview_id, actor_id)).fetchall()
                if len(current) > 1:
                    raise PublicationPreviewServiceError("The retained PDF continuation is ambiguous.")
                if current:
                    row = current[0]
                    if row["deadline_at"] <= db_now_ms(connection):
                        raise PublicationPreviewServiceError("The retained PDF continuation deadline has expired.")
                    with workflow.source_authority(row["id"], publication=row["status"] == "ready"):
                        # This rechecks current normal scope, stop/deadline, source
                        # retention and ordinary source identity; text matching is not authority.
                        workflow.original.source._snapshot(connection, preview_id, actor_id, now_ms=0)
                else:
                    workflow.request(connection, parent_run_id=parent_id,
                        actor_user_id=actor_id, operation_id=operation)
        finally:
            connection.close()
        return self.get_preview(GetPublicationPreviewQuery(
            actor_user_id=actor_id, actor_scope_epoch=epoch, preview_id=preview_id))
