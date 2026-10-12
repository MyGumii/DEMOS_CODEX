"""Opt-in v56 writer registration for a verified v3 Office preview.

Preparation retains the verified source without a database effect. A caller
may then enqueue the exact writer in its own transaction, which lets the
automatic dispatch job commit writer intent and its own success atomically.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid5

from ..db import TransactionMode, db_now_ms, transaction
from ..migrations.office_hybrid_wiki_catalog import OFFICE_HYBRID_WIKI_MIGRATION_CATALOG
from ..migrations.v056_office_hybrid_wiki import VERSION, WRITE
from ..repositories.jobs import JobEnqueueDraft, JobRepository
from ..wiki_authoring import _hash, _json
from .office_hybrid_authoring_source import (
    CanonicalOfficeHybridAuthoringSourceService, PreparedOfficeHybridAuthoringSource,
)


_NAMESPACE = UUID("b9e9cb89-3d84-4ab9-9304-b466d4805109")


@dataclass(frozen=True, slots=True)
class OfficeHybridWikiStart:
    preview_id: str
    job_id: str
    replayed: bool
    native_batch_count: int
    visual_page_count: int


@dataclass(frozen=True, slots=True)
class PreparedOfficeHybridWikiStart:
    source: PreparedOfficeHybridAuthoringSource
    job_id: str
    effect_sha256: str
    expected_state: dict
    native_batch_count: int
    visual_page_count: int

    def close(self):
        self.source.close()


def _native_plan_sha256(plan):
    return _hash(_json({"source_plan_sha256": plan.source_plan.plan_sha256,
        "reservation_json": plan.reservation_json,
        "batches": [(batch.ordinal, batch.batch_id, batch.chunk_ordinals)
            for batch in plan.batches]}))


class OfficeHybridWikiWriteService:
    def __init__(self, factory, *, source: CanonicalOfficeHybridAuthoringSourceService):
        expected = OFFICE_HYBRID_WIKI_MIGRATION_CATALOG.migrations
        if factory.migration_catalog.migrations[:len(expected)] != expected:
            raise ValueError("Office hybrid Wiki writing requires its verified v56 catalog.")
        if (type(source) is not CanonicalOfficeHybridAuthoringSourceService
                or source.factory.database_path != factory.database_path):
            raise TypeError("Office hybrid writer needs its same-database source service.")
        self.factory, self.source, self.jobs = factory, source, JobRepository()

    def prepare(self, *, preview_id: str, actor_user_id: str) -> PreparedOfficeHybridWikiStart:
        prepared = self.source.prepare(preview_id=preview_id, actor_user_id=actor_user_id)
        try:
            policy_sha = _hash(self.source.policy.encoded())
            native_sha = _native_plan_sha256(prepared.native)
            visual_sha = prepared.visual.plan_sha256
            native_count, visual_count = len(prepared.native.batches), len(prepared.visual.source.pages)
            effect_sha = _hash(_json([preview_id, policy_sha, prepared.fence_sha256,
                native_sha, visual_sha]))
            job_id = str(uuid5(_NAMESPACE, effect_sha))
            expected = {"schema_version": 1, "workflow_version": VERSION,
                "parent_job_id": prepared.parent_job_id,
                "policy_sha256": policy_sha,
                "source_fence_sha256": prepared.fence_sha256,
                "native_plan_sha256": native_sha,
                "visual_plan_sha256": visual_sha,
                "native_batch_count": native_count,
                "visual_page_count": visual_count}
            return PreparedOfficeHybridWikiStart(prepared, job_id, effect_sha,
                expected, native_count, visual_count)
        except BaseException:
            prepared.close()
            raise

    def commit_prepared(self, connection, plan: PreparedOfficeHybridWikiStart) -> OfficeHybridWikiStart:
        if (type(plan) is not PreparedOfficeHybridWikiStart
                or not connection.in_transaction
                or connection.database_path != self.factory.database_path):
            raise ValueError("Office writer start needs one prepared source and writer transaction.")
        prepared = plan.source
        self.source.revalidate_in_transaction(connection, prepared)
        parent = self.jobs.require(connection, prepared.parent_job_id)
        existing = connection.execute(
            "SELECT * FROM office_hybrid_wiki_run WHERE preview_id=?",
            (prepared.preview_id,)).fetchone()
        if existing is not None:
            if (existing["job_id"] != plan.job_id
                    or existing["source_fence_sha256"] != prepared.fence_sha256
                    or existing["policy_sha256"] != plan.expected_state["policy_sha256"]
                    or existing["native_plan_sha256"] != plan.expected_state["native_plan_sha256"]
                    or existing["visual_plan_sha256"] != plan.expected_state["visual_plan_sha256"]):
                raise ValueError("Office hybrid writer identity changed.")
            return OfficeHybridWikiStart(prepared.preview_id, plan.job_id, True,
                plan.native_batch_count, plan.visual_page_count)
        job = self.jobs.enqueue(connection, JobEnqueueDraft(
            job_id=plan.job_id, dedup_id=None, operation_id=None,
            actor_kind="user", actor_user_id=prepared.actor_user_id,
            space_id=parent.space_id, job_type=WRITE,
            handler_version="v1", effect_key=f"{VERSION}:{plan.effect_sha256}",
            input_ref={"preview_id": prepared.preview_id},
            expected_state={**plan.expected_state,
                "_authorization_fence": parent.expected_state.get("_authorization_fence")},
            authorization_snapshot=parent.authorization_snapshot,
            authorization_scope_epoch=parent.authorization_scope_epoch,
            authorization_part_id=parent.authorization_part_id,
            authorization_capabilities=parent.authorization_capabilities,
            max_attempts=2, cancellable=True,
            available_at_ms=db_now_ms(connection))).job
        connection.execute("""INSERT INTO office_hybrid_wiki_run(
            preview_id,job_id,parent_job_id,actor_user_id,artifact_id,
            extraction_snapshot_id,workflow_version,policy_sha256,
            source_fence_sha256,native_plan_sha256,visual_plan_sha256,
            native_batch_count,visual_page_count,created_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (prepared.preview_id, job.id, prepared.parent_job_id,
                prepared.actor_user_id, prepared.artifact_id,
                prepared.native.source_plan.source.snapshot_id,
                VERSION, plan.expected_state["policy_sha256"], prepared.fence_sha256,
                plan.expected_state["native_plan_sha256"],
                plan.expected_state["visual_plan_sha256"],
                plan.native_batch_count, plan.visual_page_count,
                job.created_at_ms))
        return OfficeHybridWikiStart(prepared.preview_id, job.id, False,
            plan.native_batch_count, plan.visual_page_count)

    def start(self, *, preview_id: str, actor_user_id: str) -> OfficeHybridWikiStart:
        plan = self.prepare(preview_id=preview_id, actor_user_id=actor_user_id)
        try:
            connection = self.factory.open()
            try:
                with transaction(connection, TransactionMode.IMMEDIATE):
                    return self.commit_prepared(connection, plan)
            finally:
                connection.close()
        finally:
            plan.close()


__all__ = ["OfficeHybridWikiWriteService", "OfficeHybridWikiStart",
    "PreparedOfficeHybridWikiStart"]
