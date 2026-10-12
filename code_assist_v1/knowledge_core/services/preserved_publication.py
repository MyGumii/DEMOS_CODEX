"""Opt-in preservation source selector; never treat a failed review as raw success."""
from dataclasses import dataclass
from functools import partial
from hashlib import sha256

from ..db import db_now_ms
from ..repositories.publication_commit import PublicationCommitIntegrityError, publication_operation_id
from .wiki_publication import WikiPublicationSelector, WikiPublicationSelection
from .wiki_preservation import WikiPreservationUploadPolicy
from ..wiki_preservation import AUTHORING_ONLY_VERSIONS


def has_preservation_request(connection, preview_id):
    exists = connection.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name='publication_preservation_request'").fetchone()
    return exists is not None and connection.execute(
        "SELECT 1 FROM publication_preservation_request WHERE preview_id=?", (preview_id,)).fetchone() is not None


@dataclass(frozen=True, slots=True)
class PreservedPublicationSelection(WikiPublicationSelection):
    preserved_snapshot_id: str | None = None
    authoring_only: bool = False

    def overlay(self, preview):
        value = super(PreservedPublicationSelection, self).overlay(preview)
        value["wiki_generation"].update(schema_version=2, kind="information_preservation",
            semantic_completeness_proven=False)
        if self.authoring_only:
            value["wiki_generation"].update(schema_version=3, kind="information_authoring",
                semantic_review_performed=False)
        return value


class PreservedWikiPublicationSelector(WikiPublicationSelector):
    @staticmethod
    def has_request(connection, preview_id):
        return (has_preservation_request(connection, preview_id)
                or WikiPublicationSelector.has_request(connection, preview_id))

    def select(self, connection, preview):
        if not has_preservation_request(connection, preview.preview_id):
            return super().select(connection, preview)
        if WikiPublicationSelector.has_request(connection, preview.preview_id):
            raise PublicationCommitIntegrityError("Two upload policies own one preview.")
        request = connection.execute("""SELECT r.parent_job_id,r.policy_json,r.policy_sha256,j.state,j.cancel_requested,
            j.actor_user_id,j.job_type,j.handler_version FROM publication_preservation_request r
            LEFT JOIN durable_job j ON j.id=r.parent_job_id WHERE r.preview_id=?""",
            (preview.preview_id,)).fetchone()
        if (request is None or request["parent_job_id"] != preview.durable_job_id
                or request["actor_user_id"] != preview.actor_user_id
                or request["job_type"] != "publication_preview.extract" or request["handler_version"] != "v1"):
            raise PublicationCommitIntegrityError("Preservation extraction binding changed.")
        if sha256(request["policy_json"].encode()).hexdigest() != request["policy_sha256"]:
            raise PublicationCommitIntegrityError("Preservation request policy binding changed.")
        policy = WikiPreservationUploadPolicy.restore(request["policy_json"])
        selection = partial(PreservedPublicationSelection,
            authoring_only=policy.preservation_version in AUTHORING_ONLY_VERSIONS)
        unavailable = selection("unavailable")
        if (db_now_ms(connection) >= preview.expires_at_ms or request["cancel_requested"]
                or request["state"] in {"failed", "dead", "cancelled"}
                or preview.status not in {"queued", "extracting", "ready", "consumed"}):
            return unavailable
        if preview.status in {"queued", "extracting"}:
            return selection("pending")
        if request["state"] != "succeeded":
            raise PublicationCommitIntegrityError("Ready extraction has no durable success.")
        outcome = connection.execute("SELECT outcome,run_id FROM publication_preservation_source_result WHERE preview_id=?",
            (preview.preview_id,)).fetchone()
        run = connection.execute("SELECT * FROM publication_preservation_run WHERE preview_id=?",
            (preview.preview_id,)).fetchone()
        if outcome is None:
            if run is not None:
                raise PublicationCommitIntegrityError("Preservation plan lacks its committed outcome.")
            return selection("pending")
        if outcome["outcome"] == "failed":
            return unavailable
        if (outcome["outcome"] != "planned" or run is None or run["id"] != outcome["run_id"]
                or run["actor_user_id"] != preview.actor_user_id or run["artifact_id"] != preview.artifact_id
                or run["extraction_snapshot_id"] != preview.extraction_snapshot_id
                or run["source_sha256"] != preview.source_snapshot_sha256
                or run["policy_version"] != policy.preservation_version):
            raise PublicationCommitIntegrityError("Preservation source identity is inconsistent.")
        retained = connection.execute("""SELECT 1 FROM extraction_snapshot_body b
            JOIN extraction_snapshot x ON x.id=b.snapshot_id AND x.body_retention_status='present'
            JOIN source_artifact a ON a.id=x.artifact_id AND a.body_retention_status='present'
            JOIN app_user u ON u.id=? AND u.account_status='active' AND u.scope_epoch=?
            WHERE b.snapshot_id=? AND b.artifact_id=? AND b.content_sha256=?""",
            (preview.actor_user_id, preview.captured_scope_epoch, preview.extraction_snapshot_id,
             preview.artifact_id, preview.source_snapshot_sha256)).fetchone()
        if run["status"] == "failed" or retained is None:
            return unavailable
        if run["status"] == "pending":
            return selection("pending")
        snapshot = connection.execute("""SELECT s.id,
            EXISTS(SELECT 1 FROM preserved_wiki_snapshot_body b WHERE b.snapshot_id=s.id) AS retained
            FROM preserved_wiki_snapshot s WHERE s.run_id=?""", (run["id"],)).fetchone()
        if run["status"] != "ready" or snapshot is None:
            raise PublicationCommitIntegrityError("Preservation readiness lacks its retained snapshot.")
        return selection("ready", publication_ready=preview.status == "ready" and bool(snapshot[1]),
            preserved_snapshot_id=snapshot[0])

    def replay_options(self, connection, *, actor_id, operation_id, stored, requested_authored_snapshot_id):
        if not has_preservation_request(connection, stored.preview_id):
            return super().replay_options(connection, actor_id=actor_id, operation_id=operation_id,
                stored=stored, requested_authored_snapshot_id=requested_authored_snapshot_id)
        operation = publication_operation_id(actor_id, operation_id)
        if (stored.publication_operation_id != operation or requested_authored_snapshot_id is not None
                or stored.authored_snapshot_id is not None or stored.reused_extraction_snapshot_id is not None
                or stored.body_source != "preserved_wiki" or stored.preserved_snapshot_id is None):
            raise PublicationCommitIntegrityError("Preserved publication replay is inconsistent.")
        row = connection.execute("SELECT preserved_snapshot_id FROM publication_preserved_selection WHERE publication_operation_id=?",
            (operation,)).fetchone()
        if row is None or row[0] != stored.preserved_snapshot_id:
            raise PublicationCommitIntegrityError("Preserved publication selection receipt is missing.")
        return {"preserved_snapshot_id": row[0]}
