"""Metadata-only v17 source selection; public callers never select snapshot IDs.

Read inside the caller's authorized snapshot. This is neither a worker nor a
body verifier: publication still hashes/freezes the body and fences the writer.
Committed retries use immutable publication receipts, never this live selector.
"""
from dataclasses import dataclass

from ..db import ConnectionFactory, db_now_ms
from ..errors import KnowledgeCoreError
from ..repositories.publication_commit import PublicationCommitIntegrityError, publication_operation_id


class WikiPublicationUnavailableError(KnowledgeCoreError):
    code = "PUBLICATION_PREVIEW_SERVICE_ERROR"
    http_status = 409
    retryable = False


class WikiPublicationPendingError(WikiPublicationUnavailableError):
    retryable = True


@dataclass(frozen=True, slots=True)
class WikiPublicationSelection:
    status: str
    authored_snapshot_id: str | None = None
    publication_ready: bool = False

    def overlay(self, preview):
        return {
            "wiki_generation": {"schema_version": 1, "status": self.status},
            "publication_ready": self.publication_ready,
        }

    def require_publishable(self):
        if self.status == "pending":
            raise WikiPublicationPendingError("Wiki generation is still pending. Retry the same publication operation after readiness.")
        if not self.publication_ready:
            raise WikiPublicationUnavailableError("The upload no longer has a publishable Wiki or retained extraction.")
        return self.authored_snapshot_id


class WikiPublicationSelector:
    def __init__(self, factory: ConnectionFactory):
        if not isinstance(factory, ConnectionFactory):
            raise TypeError("Wiki publication requires a ConnectionFactory.")
        self.database_path = factory.database_path

    @staticmethod
    def has_request(connection, preview_id):
        exists = connection.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name='publication_wiki_request'").fetchone()
        return exists is not None and connection.execute(
            "SELECT 1 FROM publication_wiki_request WHERE preview_id=?", (preview_id,)).fetchone() is not None

    def select(self, connection, preview):
        if not self.has_request(connection, preview.preview_id):
            return None
        request = connection.execute("""SELECT r.parent_job_id,j.state,j.cancel_requested,
            j.actor_user_id,j.job_type,j.handler_version FROM publication_wiki_request r
            LEFT JOIN durable_job j ON j.id=r.parent_job_id WHERE r.preview_id=?""",
            (preview.preview_id,)).fetchone()
        if request is None:
            raise PublicationCommitIntegrityError("The Wiki upload request disappeared during selection.")
        if (request["parent_job_id"] != preview.durable_job_id
                or request["actor_user_id"] != preview.actor_user_id
                or request["job_type"] != "publication_preview.extract" or request["handler_version"] != "v1"):
            raise PublicationCommitIntegrityError("The Wiki upload extraction binding is inconsistent.")
        eligible = preview.status == "ready" and db_now_ms(connection) < preview.expires_at_ms
        if request["cancel_requested"] or request["state"] in {"failed", "dead", "cancelled"}:
            return WikiPublicationSelection("unavailable")
        if preview.status in {"queued", "extracting"}:
            return WikiPublicationSelection("pending" if db_now_ms(connection) < preview.expires_at_ms else "unavailable")
        if request["state"] != "succeeded":
            raise PublicationCommitIntegrityError("A ready upload has no completed extraction job.")
        # Publication accepts both inline and blob-backed extraction storage.
        # Authoring admission is stricter; do not use it to reject a retained
        # extraction fallback. No body, full plan or provider response is read.
        retained = connection.execute("""SELECT 1 FROM publication_preview p
            JOIN app_user u ON u.id=p.actor_user_id AND u.account_status='active'
              AND u.scope_epoch=p.captured_scope_epoch
            JOIN source_artifact a ON a.id=p.artifact_id AND a.publisher_user_id=p.actor_user_id
              AND a.body_retention_status='present'
            JOIN extraction_snapshot x ON x.id=p.extraction_snapshot_id AND x.artifact_id=a.id
              AND x.status='ready' AND x.body_retention_status='present'
              AND x.extracted_sha256=p.source_snapshot_sha256
            JOIN extraction_snapshot_body b ON b.snapshot_id=x.id AND b.artifact_id=a.id
              AND b.content_sha256=x.extracted_sha256 AND b.content_size_bytes=x.extracted_size_bytes
              AND ((b.content_md IS NOT NULL AND b.content_blob_hash IS NULL)
                OR (b.content_md IS NULL AND b.content_blob_hash IS NOT NULL))
            WHERE p.id=? AND p.status='ready' AND p.expires_at>?""",
            (preview.preview_id, db_now_ms(connection))).fetchone()
        eligible = eligible and retained is not None
        outcome = connection.execute("SELECT outcome,run_id FROM publication_wiki_source_result WHERE preview_id=?",
            (preview.preview_id,)).fetchone()
        run = connection.execute("""SELECT id,status,actor_user_id,artifact_id,extraction_snapshot_id,source_sha256
            FROM publication_authoring_run WHERE preview_id=?""", (preview.preview_id,)).fetchone()
        if outcome is None:
            if run is not None:
                raise PublicationCommitIntegrityError("The Wiki run has no committed source-plan receipt.")
            return WikiPublicationSelection("pending" if eligible else "unavailable")
        if outcome["outcome"] == "extraction_only":
            if run is not None or outcome["run_id"] is not None:
                raise PublicationCommitIntegrityError("Extraction fallback has a contradictory Wiki run.")
            return WikiPublicationSelection("extraction_only", publication_ready=eligible)
        if (outcome["outcome"] != "planned" or run is None or run["id"] != outcome["run_id"]
                or run["actor_user_id"] != preview.actor_user_id or run["artifact_id"] != preview.artifact_id
                or run["extraction_snapshot_id"] != preview.extraction_snapshot_id
                or run["source_sha256"] != preview.source_snapshot_sha256):
            raise PublicationCommitIntegrityError("The planned Wiki is not bound to this exact upload.")
        snapshot = connection.execute("""SELECT s.id,c.snapshot_id,
            EXISTS(SELECT 1 FROM authored_wiki_snapshot_body b WHERE b.snapshot_id=s.id) AS retained
            FROM authored_wiki_snapshot s LEFT JOIN publication_synthesis_completion c ON c.run_id=s.run_id
            WHERE s.run_id=?""", (run["id"],)).fetchone()
        if run["status"] in {"pending", "extraction_only"}:
            if snapshot is not None:
                raise PublicationCommitIntegrityError("An unfinished Wiki has a contradictory completed snapshot.")
            if run["status"] == "pending":
                return WikiPublicationSelection("pending" if eligible else "unavailable")
            return WikiPublicationSelection("extraction_only", publication_ready=eligible)
        if run["status"] != "ready" or snapshot is None or snapshot["snapshot_id"] != snapshot["id"]:
            raise PublicationCommitIntegrityError("Wiki readiness has no exact synthesis completion.")
        return WikiPublicationSelection("ready", snapshot["id"], eligible and bool(snapshot["retained"]))

    def replay_options(self, connection, **kwargs):
        # The v17 implementation is unchanged. Successors may own a different
        # immutable selection receipt without reinterpreting old rows.
        return publication_replay_options(connection, **kwargs)


def publication_replay_options(connection, *, actor_id, operation_id, stored, requested_authored_snapshot_id):
    """Infer the actual frozen selection without reading purgeable source bodies."""
    canonical_id = publication_operation_id(actor_id, operation_id)
    if stored.publication_operation_id != canonical_id:
        raise PublicationCommitIntegrityError("The stored publication identity is inconsistent.")
    if WikiPublicationSelector.has_request(connection, stored.preview_id) != (stored.body_source is not None):
        raise PublicationCommitIntegrityError("The stored source-kind presence differs from its immutable upload request.")
    has_tables = connection.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name='publication_authored_selection'").fetchone()
    if has_tables is None:
        if stored.authored_snapshot_id is not None or stored.reused_extraction_snapshot_id is not None or requested_authored_snapshot_id is not None:
            raise PublicationCommitIntegrityError("The stored source selection tables are unavailable.")
        return {}
    authored = connection.execute("SELECT authored_snapshot_id FROM publication_authored_selection WHERE publication_operation_id=?", (canonical_id,)).fetchone()
    reused = connection.execute("SELECT extraction_snapshot_id,requested_authored_snapshot_id FROM publication_extraction_reuse WHERE publication_operation_id=?", (canonical_id,)).fetchone()
    if (authored is not None and reused is not None
            or (None if authored is None else authored[0]) != stored.authored_snapshot_id
            or (None if reused is None else reused[0]) != stored.reused_extraction_snapshot_id):
        raise PublicationCommitIntegrityError("The stored source selection differs from its committed receipt.")
    if authored is not None:
        return {"authored_snapshot_id": authored[0]}
    if reused is not None:
        if requested_authored_snapshot_id is not None and requested_authored_snapshot_id != reused[1]:
            raise PublicationCommitIntegrityError("The extraction reuse request is inconsistent.")
        return {"reused_extraction_snapshot_id": reused[0], "requested_authored_snapshot_id": reused[1]}
    if requested_authored_snapshot_id is not None:
        raise PublicationCommitIntegrityError("The committed authored selection is missing.")
    return {}
