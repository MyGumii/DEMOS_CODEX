"""Role Wiki publication using the normal atomic cross-kind publisher."""
from dataclasses import dataclass

from ..db import db_now_ms
from ..migrations.pptx_role_publication_catalog import PPTX_ROLE_PUBLICATION_MIGRATION_CATALOG
from ..repositories.publication_body_request import has_pptx_role_work
from ..repositories.publication_commit import PublicationCommitIntegrityError
from ..repositories.pptx_role_publication_commit import PptxRolePublicationCommitRepository
from .cross_kind_publication import CrossKindPublicationSelector, CrossKindPublicationService
from .wiki_publication import WikiPublicationSelection


@dataclass(frozen=True, slots=True)
class PptxRolePublicationSelection(WikiPublicationSelection):
    pptx_role_snapshot_id: str | None = None

    def overlay(self, preview):
        return {"wiki_generation": {"schema_version": 5, "kind": "pptx_role_information_authoring",
            "status": self.status, "semantic_review_performed": False, "semantic_completeness_proven": False},
            "publication_ready": self.publication_ready}


class PptxRolePublicationSelector(CrossKindPublicationSelector):
    @staticmethod
    def has_request(connection, preview_id):
        return has_pptx_role_work(connection, preview_id) or CrossKindPublicationSelector.has_request(connection, preview_id)

    def select(self, connection, preview):
        if not has_pptx_role_work(connection, preview.preview_id):
            return super().select(connection, preview)
        request = connection.execute("""SELECT q.*,j.actor_user_id,j.job_type,j.handler_version AS parent_handler_version,j.state,j.cancel_requested
            FROM pptx_role_workflow_request q JOIN durable_job j ON j.id=q.parent_job_id WHERE q.preview_id=?""",
            (preview.preview_id,)).fetchone()
        if (request is None or request["parent_job_id"] != preview.durable_job_id
                or request["actor_user_id"] != preview.actor_user_id or request["job_type"] != "publication_preview.extract"
                or request["parent_handler_version"] != "v2"):
            raise PublicationCommitIntegrityError("The role upload lacks its exact registered parent.")
        if (db_now_ms(connection) >= min(request["deadline_at"], preview.expires_at_ms)
                or request["cancel_requested"] or request["state"] in {"failed", "dead", "cancelled"}
                or preview.status not in {"queued", "extracting", "ready", "consumed"}
                or connection.execute("SELECT 1 FROM pptx_role_workflow_stop WHERE preview_id=?", (preview.preview_id,)).fetchone()):
            return PptxRolePublicationSelection("unavailable")
        if preview.status in {"queued", "extracting"}:
            return PptxRolePublicationSelection("pending")
        if request["state"] != "succeeded":
            raise PublicationCommitIntegrityError("A ready role extraction lacks durable success.")
        row = connection.execute("""SELECT r.id,state.status,planned.run_id AS planned_run_id,s.id AS snapshot_id
            FROM pptx_role_authoring_run r JOIN pptx_role_authoring_state state ON state.run_id=r.id
            LEFT JOIN pptx_role_workflow_planned planned ON planned.preview_id=r.preview_id
            LEFT JOIN pptx_role_wiki_snapshot s ON s.run_id=r.id WHERE r.preview_id=?""", (preview.preview_id,)).fetchone()
        if row is None:
            return PptxRolePublicationSelection("pending")
        if row["id"] != row["planned_run_id"]:
            raise PublicationCommitIntegrityError("The role run lacks its committed planner.")
        if row["status"] == "failed":
            return PptxRolePublicationSelection("unavailable")
        if row["status"] == "pending" and row["snapshot_id"] is None:
            return PptxRolePublicationSelection("pending")
        if row["status"] != "ready" or row["snapshot_id"] is None:
            raise PublicationCommitIntegrityError("The role completion lacks its exact snapshot.")
        eligible = connection.execute("SELECT snapshot_id FROM pptx_role_publication_candidate WHERE preview_id=?",
                                      (preview.preview_id,)).fetchone()
        return PptxRolePublicationSelection("ready" if eligible or preview.status == "consumed" else "unavailable",
            publication_ready=eligible is not None, pptx_role_snapshot_id=row["snapshot_id"])


class PptxRolePublicationService(CrossKindPublicationService):
    def __init__(self, factory, scope_resolver, stager, **kwargs):
        expected = PPTX_ROLE_PUBLICATION_MIGRATION_CATALOG.migrations
        if factory.migration_catalog.migrations[:len(expected)] != expected:
            raise ValueError("Role publication requires its exact successor catalog.")
        super().__init__(factory, scope_resolver, stager, **kwargs)
        self._commits = PptxRolePublicationCommitRepository(self._commits.native_source, self._public_bodies)
        self._wiki_publication = PptxRolePublicationSelector(factory)
