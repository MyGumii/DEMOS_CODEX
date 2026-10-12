"""Durable, bounded handoff from a ready Office preview to Wiki writing.

The reconciliation hook only enqueues a metadata-only dispatch job. Source
preparation happens when that job runs; its writer enqueue and dispatch
success share the kernel's writer transaction. A restart can therefore neither
lose the handoff nor publish a draft outside the current authority check.
"""

from __future__ import annotations

from uuid import UUID, uuid5

from ..db import db_now_ms
from ..migrations.office_hybrid_publication_body_catalog import (
    OFFICE_HYBRID_PUBLICATION_BODY_MIGRATION_CATALOG,
)
from ..repositories.jobs import ClaimFence, JobEnqueueDraft, JobRepository
from ..scope import Capability
from ..worker import (
    JobExpectedStateConflictError, PermanentJobExecutionError,
    PreparedJobEffect, RegisteredJobHandler,
)
from .office_hybrid_wiki_start import OfficeHybridWikiWriteService


DISPATCH = "office_hybrid_wiki.dispatch"
_NAMESPACE = UUID("349ccbe1-dc48-4a1b-998c-935eae169206")


def _effect_key(preview_id: str) -> str:
    return f"office-hybrid-wiki-dispatch:{preview_id}"


class OfficeHybridWikiDispatchHandler:
    def __init__(self, factory, *, starter: OfficeHybridWikiWriteService):
        expected = OFFICE_HYBRID_PUBLICATION_BODY_MIGRATION_CATALOG.migrations
        if factory.migration_catalog.migrations[:len(expected)] != expected:
            raise ValueError("Office dispatch requires its verified v58 catalog.")
        if (type(starter) is not OfficeHybridWikiWriteService
                or starter.factory.database_path != factory.database_path):
            raise TypeError("Office dispatch requires its same-database writer starter.")
        self.factory, self.starter, self.jobs = factory, starter, JobRepository()
        self.kernel = None

    def registered_handler(self):
        return RegisteredJobHandler.build(job_type=DISPATCH, handler_version="v1",
            actor_kind="user", required_capability=Capability.PUBLISH,
            validate_input=self.validate_input, prepare=self.prepare,
            revalidate_expected_state=self.revalidate_expected_state,
            commit_effect=self.commit_effect,
            reconcile_pending=self.reconcile_pending)

    def bind_kernel(self, kernel):
        if (self.kernel is not None or kernel.database_path != self.factory.database_path
                or kernel.registry.require(self.registered_handler().key)
                    != self.registered_handler()):
            raise ValueError("Office dispatch requires one bound kernel.")
        self.kernel = kernel

    @staticmethod
    def validate_input(value, expected):
        if (type(value) is not dict or set(value) != {"preview_id"}
                or type(expected) is not dict
                or set(expected) != {"schema_version", "parent_job_id",
                    "_authorization_fence"}
                or type(expected["schema_version"]) is not int
                or expected["schema_version"] != 1):
            raise JobExpectedStateConflictError("Office dispatch input is invalid.")
        try:
            if (str(UUID(value["preview_id"])) != value["preview_id"]
                    or str(UUID(expected["parent_job_id"])) != expected["parent_job_id"]):
                raise ValueError
        except (TypeError, ValueError, AttributeError) as exc:
            raise JobExpectedStateConflictError("Office dispatch identity is invalid.") from exc

    def reconcile_pending(self, connection):
        """Create at most eight exact intents; never read original bytes here."""
        now = db_now_ms(connection)
        rows = connection.execute("""SELECT p.id AS preview_id,p.actor_user_id,
                parent.id AS parent_job_id
            FROM publication_preview p
            JOIN office_hybrid_upload_request q ON q.preview_id=p.id
            JOIN durable_job parent ON parent.id=q.parent_job_id
            LEFT JOIN office_hybrid_wiki_run r ON r.preview_id=p.id
            LEFT JOIN durable_job dispatch ON dispatch.effect_key=
                'office-hybrid-wiki-dispatch:' || p.id
            WHERE p.status='ready' AND p.expires_at>?
              AND parent.state='succeeded' AND parent.cancel_requested=0
              AND r.preview_id IS NULL AND dispatch.id IS NULL
            ORDER BY p.created_at,p.id LIMIT 8""", (now,)).fetchall()
        for row in rows:
            parent = self.jobs.require(connection, row["parent_job_id"])
            preview_id = row["preview_id"]
            if (parent.job_type != "publication_preview.extract"
                    or parent.handler_version != "v3"
                    or parent.actor_user_id != row["actor_user_id"]
                    or Capability.PUBLISH.value not in parent.authorization_capabilities):
                raise JobExpectedStateConflictError(
                    "Ready Office preview has no publish-capable extraction authority.")
            self.jobs.enqueue(connection, JobEnqueueDraft(
                job_id=str(uuid5(_NAMESPACE, preview_id)),
                dedup_id=None, operation_id=None,
                actor_kind="user", actor_user_id=row["actor_user_id"],
                space_id=parent.space_id, job_type=DISPATCH,
                handler_version="v1", effect_key=_effect_key(preview_id),
                input_ref={"preview_id": preview_id},
                expected_state={"schema_version": 1,
                    "parent_job_id": parent.id,
                    "_authorization_fence": parent.expected_state.get("_authorization_fence")},
                authorization_snapshot=parent.authorization_snapshot,
                authorization_scope_epoch=parent.authorization_scope_epoch,
                authorization_part_id=parent.authorization_part_id,
                authorization_capabilities=parent.authorization_capabilities,
                max_attempts=2, cancellable=True, available_at_ms=now))

    def revalidate_expected_state(self, connection, job):
        self.validate_input(dict(job.input_ref), dict(job.expected_state))
        row = connection.execute("""SELECT p.actor_user_id,p.durable_job_id,
                p.status,p.expires_at,q.parent_job_id,parent.state,
                parent.cancel_requested
            FROM publication_preview p
            JOIN office_hybrid_upload_request q ON q.preview_id=p.id
            JOIN durable_job parent ON parent.id=q.parent_job_id
            WHERE p.id=?""", (job.input_ref["preview_id"],)).fetchone()
        if (row is None or job.job_type != DISPATCH or job.handler_version != "v1"
                or job.effect_key != _effect_key(job.input_ref["preview_id"])
                or job.id != str(uuid5(_NAMESPACE, job.input_ref["preview_id"]))
                or row["actor_user_id"] != job.actor_user_id
                or row["durable_job_id"] != row["parent_job_id"]
                or row["parent_job_id"] != job.expected_state["parent_job_id"]
                or row["status"] != "ready" or row["expires_at"] <= db_now_ms(connection)
                or row["state"] != "succeeded" or row["cancel_requested"]):
            raise JobExpectedStateConflictError("Office dispatch source is no longer ready.")

    def prepare(self, job):
        if self.kernel is None:
            raise PermanentJobExecutionError("Office dispatch kernel is not bound.")
        self.kernel.preflight_preparation(
            ClaimFence(job.id, job.claim_owner, job.attempt_count))
        plan = self.starter.prepare(preview_id=job.input_ref["preview_id"],
            actor_user_id=job.actor_user_id)
        try:
            if plan.source.parent_job_id != job.expected_state["parent_job_id"]:
                raise JobExpectedStateConflictError("Office dispatch parent changed.")
            return PreparedJobEffect({"schema_version": 1,
                "preview_id": job.input_ref["preview_id"],
                "writer_job_id": plan.job_id}, plan, plan.close)
        except BaseException:
            plan.close()
            raise

    def commit_effect(self, connection, job, effect):
        plan = effect.payload
        if (dict(effect.result) != {"schema_version": 1,
                "preview_id": job.input_ref["preview_id"],
                "writer_job_id": plan.job_id}
                or plan.source.actor_user_id != job.actor_user_id
                or plan.source.parent_job_id != job.expected_state["parent_job_id"]):
            raise PermanentJobExecutionError("Office dispatch result changed.")
        started = self.starter.commit_prepared(connection, plan)
        if started.job_id != plan.job_id:
            raise PermanentJobExecutionError("Office dispatch writer identity changed.")


__all__ = ["DISPATCH", "OfficeHybridWikiDispatchHandler"]
