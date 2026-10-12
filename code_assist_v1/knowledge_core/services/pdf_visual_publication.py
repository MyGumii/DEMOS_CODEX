"""Explicit PDF successor: normal atomic publication, no new LLM invocation."""
from dataclasses import dataclass, replace
import json

from ..db import db_now_ms, transaction, TransactionMode
from ..pdf_visual_authoring import VERSION_V35, VERSION_V36
from ..wiki_authoring import AuthoringError
from ..migrations.pdf_visual_publication_catalog import PDF_VISUAL_PUBLICATION_MIGRATION_CATALOG
from ..repositories.publication_body_request import has_pdf_visual_work
from ..repositories.publication_commit import PublicationCommitIntegrityError
from ..repositories.pdf_visual_publication_commit import PdfVisualPublicationCommitRepository, FrozenPdfVisualPublicationSource
from ..repositories.pdf_visual_authoring import (
    PdfVisualAuthoringRepository, PdfVisualStorageConflict, prepare_visual_run, prepare_visual_snapshot,
)
from .pdf_visual_authoring_source import CanonicalPdfVisualSourceService
from .pdf_authoring_source import PdfSourceConflict
from .pdf_publication import PdfPublicationSelector, PdfPublicationService
from ..repositories.pdf_visual_archive import PdfVisualArchiveRepository
from .pdf_visual_authoring_restore import restore_pdf_visual_source, _restore_pdf_visual_run
from .wiki_publication import WikiPublicationSelection
from .publication import PublicationPreviewServiceError


@dataclass(frozen=True, slots=True)
class PdfVisualPublicationSelection(WikiPublicationSelection):
    pdf_visual_snapshot_id: str | None = None
    progress: dict | None = None
    failure: dict | None = None

    def overlay(self, preview):
        result = {"wiki_generation": {"schema_version": 7, "kind": "pdf_visual_information_authoring",
            "status": self.status, "semantic_review_performed": False, "semantic_completeness_proven": False},
            "publication_ready": self.publication_ready}
        # Optional metadata is outside the immutable readiness schema. Never
        # send model responses, private paths or arbitrary exception messages.
        if self.progress is not None:
            result["generation_progress"] = self.progress
        if self.failure is not None:
            result["generation_error"] = self.failure
        return result


class PdfVisualPublicationSelector(PdfPublicationSelector):
    @staticmethod
    def _progress(connection, preview, request):
        """Read the exact authorized workflow's phase, not an inferred LLM ETA.

        An expired lease means recovery is needed; it does not prove that a
        process died. Authority and publication eligibility remain in select().
        """
        now = db_now_ms(connection)
        stopped = connection.execute("""SELECT s.failure_code,j.job_type
            FROM pdf_visual_workflow_stop s JOIN durable_job j ON j.id=s.job_id
            WHERE s.preview_id=?""", (preview.preview_id,)).fetchone()
        run = connection.execute("""SELECT r.id,r.batch_count,s.status,s.failure_code
            FROM pdf_visual_authoring_run r JOIN pdf_visual_authoring_state s ON s.run_id=r.id
            WHERE r.preview_id=?""", (preview.preview_id,)).fetchone()
        plan = connection.execute("""SELECT j.state,j.claim_expires_at
            FROM pdf_visual_workflow_plan p JOIN durable_job j ON j.id=p.job_id
            WHERE p.preview_id=?""", (preview.preview_id,)).fetchone()
        phase = "extraction_queued" if preview.status == "queued" else "extracting"
        total = completed = queued = running = 0
        recovering = bool(request["state"] in {"claimed", "running"}
            and request["claim_expires_at"] is not None and request["claim_expires_at"] <= now)
        if preview.status in {"ready", "consumed"}:
            phase = "planning" if plan is not None and plan["state"] in {"claimed", "running"} else "planning_queued"
            recovering = bool(plan is not None and plan["state"] in {"claimed", "running"}
                and plan["claim_expires_at"] is not None and plan["claim_expires_at"] <= now)
        if run is not None:
            total = run["batch_count"]
            batches = connection.execute("""SELECT j.state,j.claim_expires_at
                FROM pdf_visual_workflow_batch b JOIN durable_job j ON j.id=b.job_id
                WHERE b.run_id=?""", (run["id"],)).fetchall()
            completed = sum(row["state"] == "succeeded" for row in batches)
            queued = sum(row["state"] in {"queued", "retry"} for row in batches)
            running = sum(row["state"] in {"claimed", "running"} for row in batches)
            recovering = any(row["state"] in {"claimed", "running"}
                and row["claim_expires_at"] is not None and row["claim_expires_at"] <= now for row in batches)
            phase = "writing" if running else "writing_queued"
            if completed == total:
                finish = connection.execute("""SELECT j.state,j.claim_expires_at
                    FROM pdf_visual_workflow_completion c JOIN durable_job j ON j.id=c.job_id
                    WHERE c.run_id=?""", (run["id"],)).fetchone()
                phase = "assembling" if finish is not None and finish["state"] in {"claimed", "running"} else "assembling_queued"
                recovering = bool(finish is not None and finish["state"] in {"claimed", "running"}
                    and finish["claim_expires_at"] is not None and finish["claim_expires_at"] <= now)
            if run["status"] == "ready":
                phase, recovering = "ready", False
        code = stopped["failure_code"] if stopped is not None else (run["failure_code"] if run is not None else None)
        if preview.status == "cancelled" or request["cancel_requested"] or request["state"] == "cancelled":
            code = "CANCELLED"
        elif code is None and now >= min(request["deadline_at"], preview.expires_at_ms):
            code = "EXPIRED"
        elif request["state"] in {"failed", "dead"} and code is None:
            code = "EXTRACTION_FAILED"
        allowed = {"MODEL_FAILURE", "INVALID_OUTPUT", "TIMEOUT", "WORK_LIMIT", "SOURCE_UNAVAILABLE",
            "AUTHORITY_UNAVAILABLE", "CANCELLED", "LEASE_LOST", "EXPIRED", "EXTRACTION_FAILED"}
        failure = None
        if code is not None:
            code = code if code in allowed else "PROCESSING_FAILED"
            failure = {"code": code, "retryable": code in {"MODEL_FAILURE", "TIMEOUT", "LEASE_LOST"}}
            phase, recovering = "failed", False
        elif recovering:
            phase = "recovery_pending"
        return {"phase": phase, "completed_batches": completed, "total_batches": total,
            "queued_batches": queued, "running_batches": running, "recovery_pending": recovering}, failure

    @staticmethod
    def has_request(connection, preview_id):
        return has_pdf_visual_work(connection, preview_id) or PdfPublicationSelector.has_request(connection, preview_id)

    def select(self, connection, preview):
        if not has_pdf_visual_work(connection, preview.preview_id):
            return super().select(connection, preview)
        request = connection.execute("""SELECT q.*,j.actor_user_id,j.job_type,j.handler_version AS parent_handler_version,j.state,j.cancel_requested,j.claim_expires_at
            FROM pdf_visual_workflow_request q JOIN durable_job j ON j.id=q.parent_job_id WHERE q.preview_id=?""",
            (preview.preview_id,)).fetchone()
        if (request is None or request["parent_job_id"] != preview.durable_job_id
                or request["actor_user_id"] != preview.actor_user_id or request["job_type"] != "publication_preview.extract"
                or request["parent_handler_version"] != "v1"):
            raise PublicationCommitIntegrityError("The PDF upload lacks its exact registered parent.")
        progress, failure = self._progress(connection, preview, request)
        def selection(status, **kwargs):
            return PdfVisualPublicationSelection(status, progress=progress, failure=failure, **kwargs)
        if (db_now_ms(connection) >= min(request["deadline_at"], preview.expires_at_ms)
                or request["cancel_requested"] or request["state"] in {"failed", "dead", "cancelled"}
                or preview.status not in {"queued", "extracting", "ready", "consumed"}
                or connection.execute("SELECT 1 FROM pdf_visual_workflow_stop WHERE preview_id=?", (preview.preview_id,)).fetchone()):
            return selection("unavailable")
        if preview.status in {"queued", "extracting"}:
            return selection("pending")
        if request["state"] != "succeeded":
            raise PublicationCommitIntegrityError("A ready PDF extraction lacks durable success.")
        row = connection.execute("""SELECT r.id,state.status,planned.run_id AS planned_run_id,s.id AS snapshot_id
            FROM pdf_visual_authoring_run r JOIN pdf_visual_authoring_state state ON state.run_id=r.id
            LEFT JOIN pdf_visual_workflow_planned planned ON planned.preview_id=r.preview_id
            LEFT JOIN pdf_visual_wiki_snapshot s ON s.run_id=r.id WHERE r.preview_id=?""", (preview.preview_id,)).fetchone()
        if row is None:
            return selection("pending")
        if row["id"] != row["planned_run_id"]:
            raise PublicationCommitIntegrityError("The PDF run lacks its committed planner.")
        if row["status"] == "failed":
            return selection("unavailable")
        if row["status"] == "pending" and row["snapshot_id"] is None:
            return selection("pending")
        if row["status"] != "ready" or row["snapshot_id"] is None:
            raise PublicationCommitIntegrityError("The PDF completion lacks its exact snapshot.")
        eligible = connection.execute("SELECT snapshot_id FROM pdf_visual_publication_candidate WHERE preview_id=?",
            (preview.preview_id,)).fetchone()
        return selection("ready" if eligible or preview.status == "consumed" else "unavailable",
            publication_ready=eligible is not None, pdf_visual_snapshot_id=row["snapshot_id"])


class PdfVisualPublicationService(PdfPublicationService):
    def __init__(self, factory, scope_resolver, stager, *, python_executable,
                 geometry_limits=None, process_policy=None, render_policy=None, asset_policy=None,
                 protected_pdf_opener=None, chart_ocr_language=None, chart_limits=None, **kwargs):
        expected = PDF_VISUAL_PUBLICATION_MIGRATION_CATALOG.migrations
        if factory.migration_catalog.migrations[:len(expected)] != expected:
            raise ValueError("PDF publication requires its exact successor catalog.")
        super().__init__(factory, scope_resolver, stager, python_executable=python_executable,
            geometry_limits=geometry_limits, process_policy=process_policy, **kwargs)
        options = {key: value for key, value in (("geometry_limits", geometry_limits), ("process_policy", process_policy),
            ("render_policy", render_policy), ("asset_policy", asset_policy),
            ('chart_ocr_language', chart_ocr_language), ('chart_limits', chart_limits)) if value is not None}
        if protected_pdf_opener is not None:
            options["protected_pdf_opener"] = protected_pdf_opener
        self._pdf_visual_source = CanonicalPdfVisualSourceService(factory, scope_resolver, stager,
            python_executable=python_executable, **options)
        self._visual_archive = PdfVisualArchiveRepository(self._pdf_visual_source, stager)
        self._commits = PdfVisualPublicationCommitRepository(self._commits.native_source, self._public_bodies,
            self._pdf_source, self._pdf_visual_source, self._visual_archive)
        self._wiki_publication = PdfVisualPublicationSelector(factory)

    def _freeze_new_publication_source(self, **fields):
        composed = super()._freeze_new_publication_source(**fields)
        own = getattr(composed, "own_candidate", None)
        if type(own) is not FrozenPdfVisualPublicationSource:
            return composed  # Committed replay returns None and never reparses.
        try:
            repo = PdfVisualAuthoringRepository(self._pdf_visual_source, self._visual_archive)
            connection = self._factory.open(read_only=True)
            try:
                with transaction(connection, TransactionMode.DEFERRED):
                    record = connection.execute("""SELECT source_archive_id,max_executions,metadata_json FROM pdf_visual_authoring_run
                        WHERE preview_id=? AND actor_user_id=?""", (fields["preview_id"], fields["actor_id"])).fetchone()
                    if record is None:
                        raise PublicationCommitIntegrityError("The visual PDF run is unavailable.")
            finally:
                connection.close()
            stack = self._body_context.get()
            if stack is None:
                raise PublicationCommitIntegrityError("PDF publication requires its owning lease scope.")
            # File/parser work and deterministic receipt/assembly replay are
            # outside all DB transactions. This invokes no model or semantic QA.
            metadata = json.loads(record[2])
            if type(metadata) is dict and metadata.get("policy_version") in (VERSION_V35, VERSION_V36):
                # Only the saved current policy uses same-call inventory sharing.
                # The helper keeps protected sources independent and performs
                # every fresh restore fence before returning the prepared run.
                run = _restore_pdf_visual_run(self._pdf_visual_source, self._visual_archive,
                    preview_id=fields["preview_id"], actor_user_id=fields["actor_id"],
                    source_archive_id=record[0], max_executions=record[1])
                try:
                    stack.enter_context(run.source)
                except BaseException:
                    try:
                        run.source.close()
                    except BaseException:
                        pass
                    raise
            else:
                source = stack.enter_context(restore_pdf_visual_source(self._pdf_visual_source, self._visual_archive,
                    preview_id=fields["preview_id"], actor_user_id=fields["actor_id"]))
                run = prepare_visual_run(source, source_archive_id=record[0], max_executions=record[1])
            if run.metadata_json != record[2]:
                raise PublicationCommitIntegrityError("The visual PDF run configuration changed.")
            connection = self._factory.open(read_only=True)
            try:
                with transaction(connection, TransactionMode.DEFERRED):
                    receipts = repo.load_receipts(connection, run)
            finally:
                connection.close()
            snapshot = prepare_visual_snapshot(run, receipts)
            verified = self._commits.bind_replayed_snapshot(own, run, snapshot)
            composed = replace(composed, own_candidate=verified, preparation_seal="")
            return replace(composed, preparation_seal=self._commits._seal(composed))
        except (PdfVisualStorageConflict, PdfSourceConflict, PublicationCommitIntegrityError) as exc:
            raise PublicationPreviewServiceError(str(exc)) from exc
        except (AuthoringError, ValueError, TypeError, KeyError, UnicodeError, RecursionError) as exc:
            # Restart JSON and stored writer receipts are private input. Keep
            # malformed records inside the publication API's typed boundary;
            # never include their values or model text in a client message.
            raise PublicationPreviewServiceError(
                "The stored PDF writing result could not be verified."
            ) from exc
