"""Office hybrid publication selector and opt-in atomic publisher.

Only registered v3 requests enter this branch. Historical upload selectors are
delegated unchanged. Readiness is not authority to publish: the commit
repository must still replay receipts and recheck Office export permission.
"""

from dataclasses import dataclass
from dataclasses import replace
import json

from ..db import db_now_ms
from ..migrations.office_hybrid_publication_body_catalog import (
    OFFICE_HYBRID_PUBLICATION_BODY_MIGRATION_CATALOG,
)
from ..repositories.publication_commit import PublicationCommitIntegrityError
from ..repositories.publication_body_request import (
    has_pdf_visual_work, has_pdf_work, has_pptx_role_work,
)
from ..repositories.native_publication_commit import has_native_request
from ..repositories.office_hybrid_publication_commit import (
    FrozenOfficeHybridPublicationSource, OfficeHybridPublicationCommitRepository,
)
from .office_hybrid_publication_source import (
    OfficeHybridPublicationSourceConflict, OfficeHybridPublicationSourceService,
)
from .pdf_visual_publication import PdfVisualPublicationSelector, PdfVisualPublicationService
from .preserved_publication import has_preservation_request
from .publication import PublicationPreviewServiceError
from .wiki_publication import WikiPublicationSelection, WikiPublicationSelector


@dataclass(frozen=True, slots=True)
class OfficeHybridPublicationSelection(WikiPublicationSelection):
    office_hybrid_snapshot_id: str | None = None
    progress: dict | None = None
    failure: dict | None = None

    def overlay(self, preview):
        result = {"wiki_generation": {"schema_version": 8,
            "kind": "office_hybrid_native_page_information_authoring",
            "status": self.status, "semantic_review_performed": False,
            "semantic_completeness_proven": False},
            "publication_ready": self.publication_ready}
        # Optional UI metadata never changes the immutable readiness schema.
        if self.progress is not None:
            result["generation_progress"] = self.progress
        if self.failure is not None:
            result["generation_error"] = self.failure
        return result

    def require_publishable(self):
        super().require_publishable()
        # The ID is a server-owned Office candidate, not a client-authored
        # Wiki snapshot. The common publisher's authored slot must stay empty.
        return None


def _office_failure_code(encoded, fallback):
    """Use only a bounded machine code; never interpret private error prose."""
    if type(encoded) is not str or len(encoded.encode()) > 16384:
        return fallback
    try:
        error = json.loads(encoded)
    except (TypeError, ValueError):
        return fallback
    if type(error) is not dict or type(error.get("code")) is not str:
        return fallback
    codes = {"MODEL_FAILURE", "INVALID_OUTPUT", "TIMEOUT", "WORK_LIMIT",
        "SOURCE_UNAVAILABLE", "AUTHORITY_UNAVAILABLE", "CANCELLED", "LEASE_LOST",
        "EXPIRED", "EXTRACTION_FAILED", "PROCESSING_FAILED"}
    code = error["code"]
    if code in codes:
        return code
    return {"JOB_AUTHORIZATION_REJECTED": "AUTHORITY_UNAVAILABLE",
        "JOB_CANCEL_REQUESTED": "CANCELLED", "JOB_CLAIM_EXPIRED": "LEASE_LOST",
        "JOB_CLAIM_LOST": "LEASE_LOST"}.get(code, fallback)


def _office_progress(connection, preview, row):
    """Retained work units for one Office writer, not an LLM percentage/ETA.

    A claimed writer processes one native batch or visual page at a time.
    Receipts are counted without reading response bodies. An expired claim
    means recovery is pending, not proof that the writer process has died.
    This metadata is not publication authority; select() keeps its old checks.
    """
    now = db_now_ms(connection)
    total = completed = queued = running = 0
    phase = "extraction_queued" if preview.status == "queued" else "extracting"
    active = "parent"
    if preview.status in {"ready", "consumed"}:
        active = "dispatch"
        phase = "planning" if row["dispatch_state"] in {"claimed", "running"} else "planning_queued"
    if row["job_id"] is not None:
        active = "writer"
        native, visual = row["native_batch_count"], row["visual_page_count"]
        if (type(native) is not int or not 0 <= native <= 4096
                or type(visual) is not int or not 1 <= visual <= 4096):
            raise PublicationCommitIntegrityError("Office progress has an invalid work plan.")
        receipts = connection.execute("""SELECT task_kind,count(*) AS completed
            FROM office_hybrid_wiki_receipt WHERE preview_id=?
            GROUP BY task_kind""", (preview.preview_id,)).fetchall()
        counts = {"native_batch": 0, "visual_page": 0}
        for receipt in receipts:
            if (receipt["task_kind"] not in counts or type(receipt["completed"]) is not int
                    or receipt["completed"] < 0):
                raise PublicationCommitIntegrityError("Office progress has invalid retained work.")
            counts[receipt["task_kind"]] = receipt["completed"]
        if counts["native_batch"] > native or counts["visual_page"] > visual:
            raise PublicationCommitIntegrityError("Office progress exceeds the retained work plan.")
        total, completed = native + visual, sum(counts.values())
        remaining = total - completed
        current = row["writer_state"] in {"claimed", "running"}
        claim = row["writer_claim_expires_at"]
        expired_claim = current and (type(claim) is not int or claim <= now)
        if remaining:
            running = int(current and not expired_claim)
            if row["writer_state"] in {"queued", "retry", "claimed", "running"}:
                queued = remaining - running
            phase = "writing" if running else "writing_queued"
        else:
            phase = "assembling" if current and not expired_claim else "assembling_queued"
        if row["writer_state"] == "succeeded":
            phase = "ready"
    state = row[active + "_state"]
    claim = row[active + "_claim_expires_at"]
    recovering = bool(state in {"claimed", "running"}
        and (type(claim) is not int or claim <= now))
    code = None
    if (preview.status == "cancelled" or row["parent_cancel_requested"]
            or state == "cancelled" or row[active + "_cancel_requested"]):
        code = "CANCELLED"
    elif now >= preview.expires_at_ms:
        code = "EXPIRED"
    elif row["parent_state"] in {"failed", "dead"}:
        code = _office_failure_code(row["parent_error_json"], "EXTRACTION_FAILED")
    elif state in {"failed", "dead"}:
        code = _office_failure_code(row[active + "_error_json"], "PROCESSING_FAILED")
    elif preview.status not in {"queued", "extracting", "ready", "consumed"}:
        code = "EXTRACTION_FAILED" if preview.status == "failed" else "PROCESSING_FAILED"
    failure = None
    if code is not None:
        failure = {"code": code, "retryable": code in {"MODEL_FAILURE", "TIMEOUT", "LEASE_LOST"}}
        phase, recovering = "failed", False
        running = queued = 0
    elif recovering:
        phase = "recovery_pending"
    return {"phase": phase, "completed_batches": completed, "total_batches": total,
        "queued_batches": queued, "running_batches": running,
        "recovery_pending": recovering}, failure


class OfficeHybridPublicationSelector(PdfVisualPublicationSelector):
    def __init__(self, factory):
        expected = OFFICE_HYBRID_PUBLICATION_BODY_MIGRATION_CATALOG.migrations
        if factory.migration_catalog.migrations[:len(expected)] != expected:
            raise ValueError("Office publication selector requires verified v58 schema.")
        super().__init__(factory)

    @staticmethod
    def has_request(connection, preview_id):
        return (connection.execute("""SELECT 1 FROM office_hybrid_upload_request
            WHERE preview_id=?""", (preview_id,)).fetchone() is not None
            or PdfVisualPublicationSelector.has_request(connection, preview_id))

    def select(self, connection, preview):
        row = connection.execute("""SELECT q.preview_id,q.parent_job_id,
                q.actor_user_id,q.artifact_id,parent.state AS parent_state,
                parent.job_type AS parent_type,
                parent.handler_version AS parent_version,
                parent.cancel_requested AS parent_cancel_requested,
                parent.claim_expires_at AS parent_claim_expires_at,
                parent.error_json AS parent_error_json,
                r.job_id,r.native_batch_count,r.visual_page_count,
                writer.state AS writer_state,
                writer.cancel_requested AS writer_cancel_requested,
                writer.claim_expires_at AS writer_claim_expires_at,
                writer.error_json AS writer_error_json,
                dispatch.state AS dispatch_state,
                dispatch.cancel_requested AS dispatch_cancel_requested,
                dispatch.claim_expires_at AS dispatch_claim_expires_at,
                dispatch.error_json AS dispatch_error_json
            FROM office_hybrid_upload_request q
            JOIN durable_job parent ON parent.id=q.parent_job_id
            LEFT JOIN office_hybrid_wiki_run r ON r.preview_id=q.preview_id
            LEFT JOIN durable_job writer ON writer.id=r.job_id
            LEFT JOIN durable_job dispatch ON dispatch.effect_key=
                'office-hybrid-wiki-dispatch:' || q.preview_id
            WHERE q.preview_id=?""", (preview.preview_id,)).fetchone()
        if row is None:
            return super().select(connection, preview)
        # The inherited selector also counts the common publication selection
        # after commit. That is this Office request's own history, not a
        # conflicting generator. Check only concrete competing workflows.
        if (has_pdf_visual_work(connection, preview.preview_id)
                or has_pdf_work(connection, preview.preview_id)
                or has_pptx_role_work(connection, preview.preview_id)
                or has_native_request(connection, preview.preview_id)
                or has_preservation_request(connection, preview.preview_id)
                or WikiPublicationSelector.has_request(connection, preview.preview_id)):
            raise PublicationCommitIntegrityError(
                "Two generation policies own one Office upload.")
        if (row["parent_job_id"] != preview.durable_job_id
                or row["actor_user_id"] != preview.actor_user_id
                or row["artifact_id"] != preview.artifact_id
                or row["parent_type"] != "publication_preview.extract"
                or row["parent_version"] != "v3"):
            raise PublicationCommitIntegrityError(
                "Office upload extraction lineage is inconsistent.")
        progress, failure = _office_progress(connection, preview, row)
        def selection(status, **kwargs):
            # A pending cancellation still follows the predecessor's readiness
            # order. Public error metadata is legal only once unavailable.
            error = failure if status == "unavailable" else None
            if status == "unavailable" and error is None:
                error = {"code": "PROCESSING_FAILED", "retryable": False}
                displayed = dict(progress, phase="failed", recovery_pending=False,
                    running_batches=0, queued_batches=0)
            else:
                displayed = progress
            return OfficeHybridPublicationSelection(status, progress=displayed, failure=error, **kwargs)
        unavailable = selection("unavailable")
        if (db_now_ms(connection) >= preview.expires_at_ms
                or row["parent_cancel_requested"]
                or row["parent_state"] in {"failed", "dead", "cancelled"}
                or preview.status not in {"queued", "extracting", "ready", "consumed"}):
            return unavailable
        if preview.status in {"queued", "extracting"}:
            return selection("pending")
        if row["parent_state"] != "succeeded":
            raise PublicationCommitIntegrityError(
                "Ready Office extraction lacks durable success.")
        if row["job_id"] is None:
            if row["dispatch_state"] in {"failed", "dead", "cancelled"}:
                return unavailable
            if row["dispatch_state"] == "succeeded":
                raise PublicationCommitIntegrityError(
                    "Office dispatch succeeded without a writer run.")
            return selection("pending")
        if row["writer_state"] in {"queued", "claimed", "running", "retry"}:
            return selection("pending")
        if row["writer_cancel_requested"] or row["writer_state"] in {
                "failed", "dead", "cancelled"}:
            return unavailable
        if row["writer_state"] != "succeeded":
            raise PublicationCommitIntegrityError(
                "Office writer state is inconsistent.")
        candidate = connection.execute("""SELECT snapshot_id,job_id
            FROM office_hybrid_publication_candidate WHERE preview_id=?""",
            (preview.preview_id,)).fetchone()
        if candidate is not None and candidate["job_id"] != row["job_id"]:
            raise PublicationCommitIntegrityError(
                "Office candidate belongs to a different writer.")
        return selection(
            "ready" if candidate is not None or preview.status == "consumed"
                else "unavailable",
            publication_ready=candidate is not None,
            office_hybrid_snapshot_id=row["preview_id"])


class OfficeHybridPublicationService(PdfVisualPublicationService):
    """Publish a replayed v56 Office Wiki using the existing common commit."""

    def __init__(self, factory, scope_resolver, stager, *, python_executable,
            office_source: OfficeHybridPublicationSourceService, **kwargs):
        expected = OFFICE_HYBRID_PUBLICATION_BODY_MIGRATION_CATALOG.migrations
        if factory.migration_catalog.migrations[:len(expected)] != expected:
            raise ValueError("Office publication requires the verified v58 catalog.")
        if (type(office_source) is not OfficeHybridPublicationSourceService
                or office_source.factory.database_path != factory.database_path):
            raise TypeError("Office publication requires its same-database source.")
        super().__init__(factory, scope_resolver, stager,
            python_executable=python_executable, **kwargs)
        self._office_source = office_source
        self._commits = OfficeHybridPublicationCommitRepository(
            self._commits.native_source, self._public_bodies, self._pdf_source,
            self._pdf_visual_source, self._visual_archive, office_source)
        self._wiki_publication = OfficeHybridPublicationSelector(factory)

    def _freeze_new_publication_source(self, **fields):
        composed = super()._freeze_new_publication_source(**fields)
        own = getattr(composed, "own_candidate", None)
        if type(own) is not FrozenOfficeHybridPublicationSource:
            return composed
        stack = self._body_context.get()
        if stack is None:
            raise PublicationPreviewServiceError("Office publication requires its owning lease scope.")
        try:
            # This can read the retained source, replay model receipts and
            # assemble Markdown. It must stay outside the writer transaction.
            prepared = stack.enter_context(self._office_source.prepare(
                preview_id=fields["preview_id"], actor_user_id=fields["actor_id"]))
            verified = self._commits.bind_replayed_source(own, prepared)
            composed = replace(composed, own_candidate=verified, preparation_seal="")
            return replace(composed, preparation_seal=self._commits._seal(composed))
        except (OfficeHybridPublicationSourceConflict, PermissionError,
                PublicationCommitIntegrityError) as exc:
            raise PublicationPreviewServiceError(
                "The stored Office writing result or current export permission could not be verified.") from exc


__all__ = ["OfficeHybridPublicationSelector",
    "OfficeHybridPublicationSelection", "OfficeHybridPublicationService"]
