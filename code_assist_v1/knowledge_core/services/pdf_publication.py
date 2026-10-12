"""Explicit PDF successor: normal atomic publication, no new LLM invocation."""
from dataclasses import dataclass, replace

from ..db import db_now_ms, transaction, TransactionMode
from ..wiki_authoring import AuthoringError
from ..migrations.pdf_publication_catalog import PDF_PUBLICATION_MIGRATION_CATALOG
from ..repositories.publication_body_request import has_pdf_work
from ..repositories.publication_commit import PublicationCommitIntegrityError
from ..repositories.pdf_publication_commit import PdfPublicationCommitRepository, FrozenPdfPublicationSource
from ..repositories.pdf_authoring import (
    PdfAuthoringRepository, PdfStorageConflict, restore_pdf_run, prepare_pdf_snapshot,
)
from .pdf_authoring_source import CanonicalPdfAuthoringSourceService, PdfSourceConflict
from .pptx_role_publication import PptxRolePublicationSelector, PptxRolePublicationService
from .wiki_publication import WikiPublicationSelection
from .publication import PublicationPreviewServiceError


@dataclass(frozen=True, slots=True)
class PdfPublicationSelection(WikiPublicationSelection):
    pdf_snapshot_id: str | None = None

    def overlay(self, preview):
        return {"wiki_generation": {"schema_version": 6, "kind": "pdf_information_authoring",
            "status": self.status, "semantic_review_performed": False, "semantic_completeness_proven": False},
            "publication_ready": self.publication_ready}


class PdfPublicationSelector(PptxRolePublicationSelector):
    @staticmethod
    def has_request(connection, preview_id):
        return has_pdf_work(connection, preview_id) or PptxRolePublicationSelector.has_request(connection, preview_id)

    def select(self, connection, preview):
        if not has_pdf_work(connection, preview.preview_id):
            return super().select(connection, preview)
        request = connection.execute("""SELECT q.*,j.actor_user_id,j.job_type,j.handler_version AS parent_handler_version,j.state,j.cancel_requested
            FROM pdf_workflow_request q JOIN durable_job j ON j.id=q.parent_job_id WHERE q.preview_id=?""",
            (preview.preview_id,)).fetchone()
        if (request is None or request["parent_job_id"] != preview.durable_job_id
                or request["actor_user_id"] != preview.actor_user_id or request["job_type"] != "publication_preview.extract"
                or request["parent_handler_version"] != "v1"):
            raise PublicationCommitIntegrityError("The PDF upload lacks its exact registered parent.")
        if (db_now_ms(connection) >= min(request["deadline_at"], preview.expires_at_ms)
                or request["cancel_requested"] or request["state"] in {"failed", "dead", "cancelled"}
                or preview.status not in {"queued", "extracting", "ready", "consumed"}
                or connection.execute("SELECT 1 FROM pdf_workflow_stop WHERE preview_id=?", (preview.preview_id,)).fetchone()):
            return PdfPublicationSelection("unavailable")
        if preview.status in {"queued", "extracting"}:
            return PdfPublicationSelection("pending")
        if request["state"] != "succeeded":
            raise PublicationCommitIntegrityError("A ready PDF extraction lacks durable success.")
        row = connection.execute("""SELECT r.id,state.status,planned.run_id AS planned_run_id,s.id AS snapshot_id
            FROM pdf_authoring_run r JOIN pdf_authoring_state state ON state.run_id=r.id
            LEFT JOIN pdf_workflow_planned planned ON planned.preview_id=r.preview_id
            LEFT JOIN pdf_wiki_snapshot s ON s.run_id=r.id WHERE r.preview_id=?""", (preview.preview_id,)).fetchone()
        if row is None:
            return PdfPublicationSelection("pending")
        if row["id"] != row["planned_run_id"]:
            raise PublicationCommitIntegrityError("The PDF run lacks its committed planner.")
        if row["status"] == "failed":
            return PdfPublicationSelection("unavailable")
        if row["status"] == "pending" and row["snapshot_id"] is None:
            return PdfPublicationSelection("pending")
        if row["status"] != "ready" or row["snapshot_id"] is None:
            raise PublicationCommitIntegrityError("The PDF completion lacks its exact snapshot.")
        eligible = connection.execute("SELECT snapshot_id FROM pdf_publication_candidate WHERE preview_id=?",
            (preview.preview_id,)).fetchone()
        return PdfPublicationSelection("ready" if eligible or preview.status == "consumed" else "unavailable",
            publication_ready=eligible is not None, pdf_snapshot_id=row["snapshot_id"])


class PdfPublicationService(PptxRolePublicationService):
    def __init__(self, factory, scope_resolver, stager, *, python_executable,
                 geometry_limits=None, process_policy=None, **kwargs):
        expected = PDF_PUBLICATION_MIGRATION_CATALOG.migrations
        if factory.migration_catalog.migrations[:len(expected)] != expected:
            raise ValueError("PDF publication requires its exact successor catalog.")
        super().__init__(factory, scope_resolver, stager, **kwargs)
        options = {key: value for key, value in (("geometry_limits", geometry_limits), ("process_policy", process_policy)) if value is not None}
        self._pdf_source = CanonicalPdfAuthoringSourceService(factory, scope_resolver, stager,
            python_executable=python_executable, **options)
        self._commits = PdfPublicationCommitRepository(self._commits.native_source, self._public_bodies, self._pdf_source)
        self._wiki_publication = PdfPublicationSelector(factory)

    def _freeze_new_publication_source(self, **fields):
        composed = super()._freeze_new_publication_source(**fields)
        own = getattr(composed, "own_candidate", None)
        if type(own) is not FrozenPdfPublicationSource:
            return composed  # Committed replay returns None and never reparses.
        try:
            repo = PdfAuthoringRepository(self._pdf_source)
            connection = self._factory.open(read_only=True)
            try:
                with transaction(connection, TransactionMode.DEFERRED):
                    configuration = repo.load_configuration(connection, preview_id=fields["preview_id"], actor_user_id=fields["actor_id"])
            finally:
                connection.close()
            stack = self._body_context.get()
            if stack is None:
                raise PublicationCommitIntegrityError("PDF publication requires its owning lease scope.")
            # File/parser work and deterministic receipt/assembly replay are
            # outside all DB transactions. This invokes no model or semantic QA.
            run = stack.enter_context(restore_pdf_run(self._pdf_source, configuration))
            connection = self._factory.open(read_only=True)
            try:
                with transaction(connection, TransactionMode.DEFERRED):
                    receipts = repo.load_receipts(connection, run)
            finally:
                connection.close()
            snapshot = prepare_pdf_snapshot(run, receipts)
            verified = self._commits.bind_replayed_snapshot(own, run, snapshot)
            composed = replace(composed, own_candidate=verified, preparation_seal="")
            return replace(composed, preparation_seal=self._commits._seal(composed))
        except (PdfStorageConflict, PdfSourceConflict, PublicationCommitIntegrityError) as exc:
            raise PublicationPreviewServiceError(str(exc)) from exc
        except (AuthoringError, ValueError, TypeError, KeyError, UnicodeError, RecursionError) as exc:
            # Restart JSON and stored writer receipts are private input. Keep
            # malformed records inside the publication API's typed boundary;
            # never include their values or model text in a client message.
            raise PublicationPreviewServiceError(
                "The stored PDF writing result could not be verified."
            ) from exc
