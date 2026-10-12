"""Explicit v31 selector/composition. Public callers never select a snapshot."""
from dataclasses import dataclass

from ..db import db_now_ms, transaction, TransactionMode
from ..migrations.native_publication_catalog import NATIVE_PUBLICATION_MIGRATION_CATALOG
from ..repositories.native_publication_commit import NativePublicationCommitRepository, has_native_request
from ..repositories.publication_commit import PublicationCommitIntegrityError, publication_operation_id
from .preserved_publication import PreservedWikiPublicationSelector, has_preservation_request
from .wiki_publication import WikiPublicationSelection, WikiPublicationSelector
from .publication import (PublicationService, PublicationPreviewAuthorizationError, PublicationPreviewServiceError,
    PublicationPreviewIdempotencyError)
from .native_authoring_source import CanonicalNativeAuthoringSourceService


@dataclass(frozen=True, slots=True)
class NativeWikiPublicationSelection(WikiPublicationSelection):
    native_snapshot_id: str | None = None

    def overlay(self, preview):
        return {"wiki_generation": {"schema_version": 4, "kind": "native_information_authoring",
            "status": self.status, "semantic_review_performed": False, "semantic_completeness_proven": False},
            "publication_ready": self.publication_ready}


class NativeWikiPublicationSelector(PreservedWikiPublicationSelector):
    @staticmethod
    def has_request(connection, preview_id):
        return has_native_request(connection, preview_id) or PreservedWikiPublicationSelector.has_request(connection, preview_id)

    def select(self, connection, preview):
        if not has_native_request(connection, preview.preview_id):
            return super().select(connection, preview)
        if (has_preservation_request(connection, preview.preview_id)
                or WikiPublicationSelector.has_request(connection, preview.preview_id)):
            raise PublicationCommitIntegrityError("Two generation policies own one upload.")
        request = connection.execute("""SELECT q.*,j.actor_user_id,j.job_type,j.handler_version,j.state,j.cancel_requested
            FROM native_workflow_request q JOIN durable_job j ON j.id=q.parent_job_id WHERE q.preview_id=?""",
            (preview.preview_id,)).fetchone()
        if (request is None or request["parent_job_id"] != preview.durable_job_id
                or request["actor_user_id"] != preview.actor_user_id or request["job_type"] != "publication_preview.extract"
                or request["handler_version"] != "v2"):
            raise PublicationCommitIntegrityError("Native extraction lineage is inconsistent.")
        unavailable = NativeWikiPublicationSelection("unavailable")
        if (db_now_ms(connection) >= min(request["deadline_at"], preview.expires_at_ms)
                or request["cancel_requested"] or request["state"] in {"failed", "dead", "cancelled"}
                or preview.status not in {"queued", "extracting", "ready", "consumed"}
                or connection.execute("SELECT 1 FROM native_workflow_stop WHERE preview_id=?", (preview.preview_id,)).fetchone()):
            return unavailable
        if preview.status in {"queued", "extracting"}:
            return NativeWikiPublicationSelection("pending")
        if request["state"] != "succeeded":
            raise PublicationCommitIntegrityError("Native ready extraction lacks durable success.")
        row = connection.execute("""SELECT r.id,state.status,planned.run_id AS planned_run_id,s.id AS snapshot_id
            FROM native_authoring_run r JOIN native_authoring_state state ON state.run_id=r.id
            LEFT JOIN native_workflow_planned planned ON planned.preview_id=r.preview_id
            LEFT JOIN native_wiki_snapshot s ON s.run_id=r.id WHERE r.preview_id=?""", (preview.preview_id,)).fetchone()
        if row is None:
            return NativeWikiPublicationSelection("pending")
        if row["id"] != row["planned_run_id"]:
            raise PublicationCommitIntegrityError("Native run lacks its committed planner result.")
        if row["status"] == "failed":
            return unavailable
        if row["status"] == "pending":
            if row["snapshot_id"] is not None:
                raise PublicationCommitIntegrityError("Unfinished native work has a completed snapshot.")
            return NativeWikiPublicationSelection("pending")
        if row["status"] != "ready" or row["snapshot_id"] is None:
            raise PublicationCommitIntegrityError("Native completion lacks its exact snapshot.")
        eligible = connection.execute("SELECT snapshot_id FROM native_publication_candidate WHERE preview_id=?",
            (preview.preview_id,)).fetchone()
        return NativeWikiPublicationSelection("ready" if eligible or preview.status == "consumed" else "unavailable",
            publication_ready=eligible is not None, native_snapshot_id=row["snapshot_id"])

    def replay_options(self, connection, *, actor_id, operation_id, stored, requested_authored_snapshot_id):
        if not has_native_request(connection, stored.preview_id):
            return super().replay_options(connection, actor_id=actor_id, operation_id=operation_id,
                stored=stored, requested_authored_snapshot_id=requested_authored_snapshot_id)
        operation = publication_operation_id(actor_id, operation_id)
        if (stored.publication_operation_id != operation or requested_authored_snapshot_id is not None
                or stored.authored_snapshot_id is not None or stored.preserved_snapshot_id is not None
                or stored.reused_extraction_snapshot_id is not None or stored.body_source != "native_wiki"
                or stored.native_snapshot_id is None):
            raise PublicationCommitIntegrityError("Native replay is not an exact, exclusive body selection.")
        selected = connection.execute("SELECT native_snapshot_id FROM publication_native_selection WHERE publication_operation_id=?",
            (operation,)).fetchone()
        if selected is None or selected[0] != stored.native_snapshot_id:
            raise PublicationCommitIntegrityError("Native publication selection receipt is missing.")
        return {"native_snapshot_id": selected[0]}


class NativeWikiPublicationService(PublicationService):
    def __init__(self, factory, scope_resolver, stager, **kwargs):
        expected = NATIVE_PUBLICATION_MIGRATION_CATALOG.migrations
        if factory.migration_catalog.migrations[:len(expected)] != expected:
            raise ValueError("Native publication requires its verified successor catalog.")
        if "commits" in kwargs or "wiki_publication" in kwargs:
            raise TypeError("Native publication owns its exact repository and selector composition.")
        source = CanonicalNativeAuthoringSourceService(factory, scope_resolver, stager)
        super().__init__(factory, scope_resolver, commits=NativePublicationCommitRepository(source),
            wiki_publication=NativeWikiPublicationSelector(factory), **kwargs)

    def _request_sha256(self, **fields):
        """Bind the requested server candidate, never a prior reused snapshot.

        Exact already-committed v1 operations retain their historical hash. New
        opt-in upload operations use v2; default/legacy publishers are unchanged.
        Only immutable metadata is needed after private-body retention loss.
        """
        from ..repositories.publication_body_request import (
            requested_publication_body, RequestedBodyPendingError, RequestedBodyUnavailableError,
        )
        from .wiki_publication import WikiPublicationPendingError, WikiPublicationUnavailableError
        intent = super()._request_sha256(**fields)
        connection = self._factory.open(read_only=True)
        try:
            with transaction(connection,TransactionMode.DEFERRED):
                actor = self._users.require(connection,fields['actor_id'])
                if actor.account_status!='active' or actor.scope_epoch!=fields['epoch']:
                    raise PublicationPreviewAuthorizationError("Current actor state does not authorize publication.")
                old = connection.execute("""SELECT p.request_sha256,json_extract(d.result_json,'$.preview_id')
                    FROM publication_operation p
                    JOIN operation_dedup d ON d.id=p.dedup_id AND d.status='committed'
                      AND d.operation_type='publication' AND d.actor_user_id=p.actor_user_id
                      AND d.operation_id=p.operation_id AND d.request_sha256=p.request_sha256
                    WHERE p.id=? AND p.actor_user_id=? AND p.operation_id=?""",
                    (publication_operation_id(fields['actor_id'],fields['operation_id']),
                     fields['actor_id'],fields['operation_id'])).fetchone()
                if old is not None and old[0]==intent:
                    return intent
                if old is not None:
                    # Compare the *committed* request before inspecting an
                    # incoming (possibly pending/failed) replacement preview.
                    # Its original ready run is immutable even if bodies expire.
                    internal = connection.execute('''SELECT s.id FROM publication_authoring_run r
                        JOIN authored_wiki_snapshot s ON s.run_id=r.id WHERE r.preview_id=?''',(old[1],)).fetchone()
                    try:
                        original = requested_publication_body(connection,actor_id=fields['actor_id'],
                            preview_id=old[1],authored_snapshot_id=internal[0] if internal else None)
                    except PublicationCommitIntegrityError as exc:
                        raise PublicationPreviewServiceError('Committed publication request identity is inconsistent.') from exc
                    if original is None or original.request_sha256(intent)!=old[0]:
                        raise PublicationPreviewIdempotencyError('The publication operation ID is already bound to a different request.')
                    return old[0]
                # Only fresh operations consult live readiness. Identity lookup
                # alone cannot distinguish a not-yet-created run from a stopped
                # or expired upload, and must not turn terminal failure into a
                # retryable pending response. Committed replay above deliberately
                # remains independent of retention, expiry and current item state.
                preview = self._previews.require_for_actor(connection,
                    preview_id=fields['preview_id'],actor_user_id=fields['actor_id'])
                selection = self._wiki_publication.select(connection,preview)
                if selection is not None:
                    selection.require_publishable()
                elif preview.status!='ready' or db_now_ms(connection)>=preview.expires_at_ms:
                    raise PublicationPreviewAuthorizationError(
                        'The preview is not currently eligible for this publication.')
                requested = requested_publication_body(connection,actor_id=fields['actor_id'],
                    preview_id=fields['preview_id'],authored_snapshot_id=fields.get('authored_snapshot_id'))
                return intent if requested is None else requested.request_sha256(intent)
        except RequestedBodyPendingError as exc:
            raise WikiPublicationPendingError(str(exc)) from exc
        except RequestedBodyUnavailableError as exc:
            raise WikiPublicationUnavailableError(str(exc)) from exc
        except PublicationCommitIntegrityError as exc:
            raise PublicationPreviewServiceError(str(exc)) from exc
        finally:
            connection.close()
