"""Unactivated, source-preserving upload-to-chunk durable workflow v1.

One job executes one ordinal, not an entire document. Completed chunks and job
success commit together. Provider calls are at-least-once after an interrupted
attempt; only canonical effects are at-most-once. No public route, runtime
registry, migration, model fallback or final-publication selection is enabled.
The model-free plan/final reduced-synthesis successors remain separate work.
"""
from __future__ import annotations

import json
from uuid import UUID, uuid5

from ..db import db_now_ms, transaction, TransactionMode
from ..repositories.jobs import ClaimFence, JobEnqueueDraft, JobRepository
from ..repositories.audit import AuditEventDraft, AuditRepository
from ..repositories.outbox import OutboxEventDraft, OutboxRepository
from ..repositories.users import UserRepository
from ..repositories.wiki_authoring import (
    AuthoringStorageConflict, PreparedAuthoringRun, PreparedAuthoredChunk,
    WikiAuthoringRepository,
)
from ..scope import Capability
from ..wiki_authoring import (
    AUTHORING_POLICY_VERSION, AuthoringModelError, AuthoringModelPolicy,
    AuthoringWorkPolicy, AuthoringWorkLimitError, ExtractionSource, WikiChunkAuthor,
    plan_authoring,
)
from ..worker import (
    JobExpectedStateConflictError, PermanentJobExecutionError, PreparedJobEffect,
    RegisteredJobHandler, RetryableJobExecutionError,
)


_NAMESPACE = UUID("2d5a3f64-c8a8-4bb4-ad72-b8bca7ee1baf")
_JOB_TYPE = "publication_wiki.author_chunk"
_VERSION = "v1"
# This retained handler policy is frozen, not a deployment-time retry knob.
# Runner attempts are independently frozen in the source plan. Admission below
# accounts for their product, including a lost response followed by recovery.
_DURABLE_ATTEMPTS = 2


class WikiAuthoringChunkWorkflow:
    def __init__(self, factory, *, author: WikiChunkAuthor):
        self._factory = factory
        self._author = author
        self._repository = WikiAuthoringRepository()
        self._jobs = JobRepository()
        self._kernel = None
        self._synthesis = None

    def bind_synthesis(self, synthesis):
        """Opt-in successor; existing v14 chunk-only callers remain unchanged."""
        if self._synthesis is not None or synthesis.database_path != self._factory.database_path:
            raise ValueError("Authoring requires one same-database synthesis successor.")
        self._synthesis = synthesis

    def bind_kernel(self, kernel):
        if self._kernel is not None or kernel.database_path != self._factory.database_path:
            raise ValueError("Authoring requires its one canonical worker kernel.")
        kernel.registry.require(self.registered_handler().key)
        self._kernel = kernel

    @property
    def durable_attempts(self):
        return _DURABLE_ATTEMPTS

    @property
    def kernel(self):
        if self._kernel is None:
            raise RuntimeError("The authoring workflow has no canonical kernel.")
        return self._kernel

    def registered_handler(self):
        return RegisteredJobHandler.build(job_type=_JOB_TYPE, handler_version=_VERSION,
            actor_kind="user", required_capability=Capability.PUBLISH,
            validate_input=self.validate_input, prepare=self.prepare,
            revalidate_expected_state=self.revalidate_expected_state,
            commit_effect=self.commit_effect, prepare_terminal=self.prepare_terminal,
            converge_terminal=self.converge_terminal, reconcile_pending=self.reconcile_pending)

    def _parent(self, connection, preview_id, actor):
        row = connection.execute("""SELECT durable_job_id,artifact_id,captured_scope_epoch
            FROM publication_preview WHERE id=? AND actor_user_id=? AND status='ready'""",
            (preview_id, actor)).fetchone()
        if row is None or row[0] is None:
            raise AuthoringStorageConflict("A completed canonical upload extraction is required.")
        parent = self.kernel.revalidate_job_authority(connection, row[0])
        if (parent.job_type != "publication_preview.extract" or parent.handler_version != "v1"
                or parent.state != "succeeded" or parent.actor_kind != "user"
                or parent.actor_user_id != actor or parent.authorization_scope_epoch != row[2]
                or parent.input_ref.get("preview_id") != preview_id
                or parent.input_ref.get("artifact_id") != row[1]):
            raise AuthoringStorageConflict("The upload extraction binding is unavailable.")
        return parent

    def prepare_upload(self, *, preview_id, actor_user_id, chunk_bytes=24_576,
                       model_policy=None, work_policy=None):
        """Expensive source/plan validation is outside any DB transaction."""
        connection = self._factory.open(read_only=True)
        try:
            with transaction(connection, TransactionMode.DEFERRED):
                self._parent(connection, preview_id, actor_user_id)
            row = connection.execute("""SELECT b.snapshot_id,b.content_sha256,b.content_md
                FROM publication_preview p JOIN extraction_snapshot_body b
                ON b.snapshot_id=p.extraction_snapshot_id
                WHERE p.id=? AND p.actor_user_id=?""", (preview_id, actor_user_id)).fetchone()
            if row is None:
                raise AuthoringStorageConflict("The source extraction is unavailable.")
            plan = plan_authoring(ExtractionSource(*row), chunk_bytes=chunk_bytes,
                model_policy=model_policy or AuthoringModelPolicy(),
                work_policy=work_policy or AuthoringWorkPolicy())
            prepared = self._repository.prepare_run(connection, preview_id=preview_id,
                actor_user_id=actor_user_id, plan=plan, now_ms=db_now_ms(connection))
            self._admit(prepared)
            with transaction(connection, TransactionMode.DEFERRED):
                self._parent(connection, preview_id, actor_user_id)
            return prepared
        finally:
            connection.close()

    @staticmethod
    def _admit(prepared):
        policy = json.loads(prepared.plan_json)["work_policy"]
        for reserved, key in zip(prepared.reserved_work,
                ("model_attempts", "logical_input_bytes", "response_bytes"), strict=True):
            if _DURABLE_ATTEMPTS * reserved > policy[key]:
                raise AuthoringWorkLimitError("Durable retries exceed the frozen whole-document work budget.")

    def enqueue_upload(self, prepared: PreparedAuthoringRun):
        """Server-owned successor IDs; never accepts client-selected job space."""
        connection = self._factory.open()
        try:
            with transaction(connection):
                return self.enqueue_prepared(connection, prepared)
        finally:
            connection.close()

    def enqueue_prepared(self, connection, prepared: PreparedAuthoringRun, *, synthesis_policy=None):
        """Compose run/map effects in the caller's one service-layer writer."""
        if not connection.in_transaction or not isinstance(prepared, PreparedAuthoringRun):
            raise AuthoringStorageConflict("A writer and prepared source plan are required.")
        if synthesis_policy is not None and self._synthesis is None:
            raise AuthoringStorageConflict("The frozen synthesis successor is unavailable.")
        parent = self._parent(connection, prepared.preview_id, prepared.actor_user_id)
        now = db_now_ms(connection)
        run_id = self._repository.create_run(connection, preview_id=prepared.preview_id,
            actor_user_id=prepared.actor_user_id, prepared=prepared, now_ms=now)
        # The repository validates the seal before these metadata are
        # trusted. Any admission/enqueue/bind failure rolls back the run.
        self._admit(prepared)
        if self._synthesis is not None:
            self._synthesis.register_run(connection, prepared=prepared, run_id=run_id, parent=parent, policy=synthesis_policy)
        job_ids = []
        inserted_count = 0
        for ordinal, chunk_id, start, end, source_sha, request_sha in prepared.chunks:
            effect = f"wiki-author-chunk:v1:{run_id}:{ordinal}:{request_sha}"
            job_id = str(uuid5(_NAMESPACE, effect))
            input_ref = dict(run_id=run_id, ordinal=ordinal, chunk_id=chunk_id,
                request_sha256=request_sha, policy_version=AUTHORING_POLICY_VERSION)
            expected = dict(schema_version=1, plan_sha256=prepared.plan_sha256,
                parent_job_id=parent.id, source_fence=prepared.fence_json,
                durable_attempts=_DURABLE_ATTEMPTS,
                reserved_work=list(prepared.reserved_work),
                _authorization_fence=parent.expected_state.get("_authorization_fence"))
            self.validate_input(input_ref, expected)
            # Inherit only the canonical extraction job's server-bound
            # mutation authority. Other authors' new documents must not
            # invalidate this immutable extraction's content generation.
            queued = self._jobs.enqueue(connection, JobEnqueueDraft(job_id=job_id,
                dedup_id=None, operation_id=None, actor_kind="user",
                actor_user_id=parent.actor_user_id, space_id=parent.space_id,
                job_type=_JOB_TYPE, handler_version=_VERSION, effect_key=effect,
                input_ref=input_ref, expected_state=expected,
                authorization_snapshot=parent.authorization_snapshot,
                authorization_scope_epoch=parent.authorization_scope_epoch,
                authorization_part_id=parent.authorization_part_id,
                authorization_capabilities=parent.authorization_capabilities,
                max_attempts=_DURABLE_ATTEMPTS, cancellable=True, available_at_ms=now))
            self._repository.bind_chunk_job(connection, run_id=run_id,
                actor_user_id=prepared.actor_user_id, ordinal=ordinal, job_id=job_id, now_ms=now)
            inserted_count += int(queued.inserted)
            job_ids.append(job_id)
        if inserted_count:
            self._event(connection, parent, key=f"{run_id}:queued", action="wiki.authoring.queued",
                run_id=run_id, details={"chunk_count": len(job_ids),
                    "durable_attempts": _DURABLE_ATTEMPTS,
                    "reserved_model_attempts": _DURABLE_ATTEMPTS * prepared.reserved_work[0]})
        return run_id, tuple(job_ids)

    @staticmethod
    def validate_input(value, expected):
        if (set(value) != {"run_id", "ordinal", "chunk_id", "request_sha256", "policy_version"}
                or value["policy_version"] != AUTHORING_POLICY_VERSION
                or type(value["ordinal"]) is not int or value["ordinal"] < 1
                or set(expected) != {"schema_version", "plan_sha256", "parent_job_id", "source_fence",
                    "durable_attempts", "reserved_work", "_authorization_fence"}
                or expected["schema_version"] != 1 or expected["durable_attempts"] != _DURABLE_ATTEMPTS):
            raise JobExpectedStateConflictError("Invalid versioned authoring input.")

    def revalidate_expected_state(self, connection, job):
        self.validate_input(job.input_ref, job.expected_state)
        value, expected = job.input_ref, job.expected_state
        if job.max_attempts != _DURABLE_ATTEMPTS:
            raise JobExpectedStateConflictError("The durable attempt policy changed.")
        try:
            frozen = json.loads(self._repository._chunk_fence(connection, run_id=value["run_id"],
                actor_user_id=job.actor_user_id, ordinal=value["ordinal"], now_ms=db_now_ms(connection)))
            run, chunk = frozen["run"], frozen["chunk"]
            if ((chunk[1], chunk[5], chunk[6]) != (value["chunk_id"], value["request_sha256"], job.id)
                    or run[3] != expected["plan_sha256"]
                    or self._repository._run_source_fence(connection, preview_id=run[5],
                        actor_user_id=job.actor_user_id, now_ms=db_now_ms(connection)) != expected["source_fence"]):
                raise AuthoringStorageConflict("The chunk job binding changed.")
            parent = self._parent(connection, run[5], job.actor_user_id)
            if parent.id != expected["parent_job_id"] or parent.space_id != job.space_id:
                raise AuthoringStorageConflict("The chunk upload authority changed.")
        except AuthoringStorageConflict as exc:
            raise JobExpectedStateConflictError("The authoring source or chunk is unavailable.") from exc

    def prepare(self, job):
        fence = ClaimFence(job.id, job.claim_owner, job.attempt_count)
        self.kernel.preflight_preparation(fence)
        connection = self._factory.open(read_only=True)
        try:
            plan = self._repository.load_plan(connection, run_id=job.input_ref["run_id"],
                actor_user_id=job.actor_user_id, now_ms=db_now_ms(connection))
        finally:
            connection.close()
        # Planning may take time; repeat the cheap current-authority/lease check
        # immediately before one ordinal's provider call. No DB handle is open.
        self.kernel.preflight_preparation(fence)
        try:
            result = self._author.author(plan, job.input_ref["ordinal"])
        except AuthoringModelError as exc:
            raise RetryableJobExecutionError("Authoring provider attempt did not complete.") from exc
        connection = self._factory.open(read_only=True)
        try:
            prepared = self._repository.prepare_chunk(connection, run_id=job.input_ref["run_id"],
                actor_user_id=job.actor_user_id, result=result, now_ms=db_now_ms(connection))
        finally:
            connection.close()
        return PreparedJobEffect(result=self._result(prepared), payload=prepared)

    @staticmethod
    def _result(prepared):
        return dict(schema_version=1, run_id=prepared.run_id, ordinal=prepared.ordinal,
            response_sha256=prepared.values[1])

    def commit_effect(self, connection, job, prepared):
        value = prepared.payload
        if (not isinstance(value, PreparedAuthoredChunk)
                or value.run_id != job.input_ref["run_id"] or value.ordinal != job.input_ref["ordinal"]
                or dict(prepared.result) != self._result(value)):
            raise PermanentJobExecutionError("Invalid prepared chunk effect.")
        try:
            self._repository.save_chunk(connection, run_id=value.run_id, actor_user_id=job.actor_user_id,
                prepared=value, now_ms=db_now_ms(connection))
        except AuthoringStorageConflict as exc:
            raise JobExpectedStateConflictError("The prepared chunk can no longer commit.") from exc
        self._event(connection, job, key=f"{value.run_id}:chunk:{value.ordinal}",
            action="wiki.authoring.chunk_completed", run_id=value.run_id,
            details={"ordinal": value.ordinal, "response_sha256": value.values[1]})

    @staticmethod
    def _event(connection, job, *, key, action, run_id, details):
        now = db_now_ms(connection)
        actor = UserRepository().require(connection, job.actor_user_id)
        event_id = str(uuid5(_NAMESPACE, "event:" + key))
        payload = {"schema_version": 1, "run_id": run_id, "job_id": job.id, **details}
        AuditRepository().append(connection, AuditEventDraft(event_uuid=event_id,
            occurred_at_ms=now, request_id=job.id, operation_id=job.operation_id,
            action=action, outcome="success", actor_kind="user", actor_user_id=actor.id,
            actor_display_name=actor.display_name, actor_role_snapshot=actor.system_role,
            space_id=job.space_id, details=payload))
        OutboxRepository().append(connection, OutboxEventDraft(logical_key="wiki-authoring:" + key,
            space_id=job.space_id, aggregate_type="publication_authoring_run", aggregate_id=run_id,
            aggregate_version=1, event_type=action, payload=payload, available_at_ms=now))

    def prepare_terminal(self, connection, job, terminal_state):
        # Queued cancellation only invokes this hook; it must be sufficient.
        self.converge_terminal(connection, job)

    def reconcile_pending(self, connection):
        """Converge one run per poll when an external service cancelled jobs.

        Role/account changes can terminalize queued jobs without invoking this
        handler. Source preview cancellation/expiry can also happen while no
        chunk is running. Read only identities here; never extraction bodies.
        This is a work-batch bound, not a document acceptance/count limit.
        """
        row = connection.execute("""SELECT r.id,r.status,p.status,p.expires_at FROM publication_authoring_run r
            JOIN publication_preview p ON p.id=r.preview_id WHERE r.status IN ('pending','extraction_only')
            AND ((r.status='pending' AND (p.status<>'ready' OR p.expires_at<=? OR EXISTS(
                SELECT 1 FROM publication_authoring_chunk c JOIN durable_job j ON j.id=c.durable_job_id
                WHERE c.run_id=r.id AND j.state IN ('failed','cancelled','dead'))))
              OR (r.status='extraction_only' AND EXISTS(SELECT 1 FROM publication_authoring_chunk c
                JOIN durable_job j ON j.id=c.durable_job_id WHERE c.run_id=r.id
                AND j.state IN ('queued','retry','claimed','running') AND j.cancel_requested=0)))
            AND EXISTS(SELECT 1 FROM publication_authoring_chunk c WHERE c.run_id=r.id AND c.durable_job_id IS NOT NULL)
            ORDER BY r.created_at,r.id LIMIT 1""", (db_now_ms(connection),)).fetchone()
        if row is None:
            return
        selected = connection.execute("""SELECT durable_job_id FROM publication_authoring_chunk
            WHERE run_id=? AND durable_job_id IS NOT NULL ORDER BY ordinal LIMIT 1""", (row[0],)).fetchone()
        job = self._jobs.require(connection, selected[0])
        reason = ("run_terminal" if row[1] == "extraction_only" else
                  "preview_terminal" if row[2] != "ready" else
                  "preview_expired" if row[3] <= db_now_ms(connection) else "chunk_terminal")
        self.converge_terminal(connection, job, reason=reason)
        if job.state in {"queued", "retry", "claimed", "running"}:
            self._jobs.request_cancel(connection, job_id=job.id, now_ms=db_now_ms(connection))

    def converge_terminal(self, connection, job, *, reason="chunk_terminal"):
        run = connection.execute("""SELECT r.id,r.status FROM publication_authoring_run r
            JOIN publication_authoring_chunk c ON c.run_id=r.id
            WHERE c.durable_job_id=? AND r.actor_user_id=?""", (job.id, job.actor_user_id)).fetchone()
        if run is None or run[1] == "ready":
            return
        if run[1] == "pending":
            # First terminal wins. Subsequent sibling failures must not rewrite
            # its cause or prevent durable cancellation from becoming terminal.
            self._repository.extraction_only(connection, run_id=run[0], actor_user_id=job.actor_user_id,
                now_ms=db_now_ms(connection), failure_code="authoring_incomplete")
            self._event(connection, job, key=f"{run[0]}:extraction_only", action="wiki.authoring.extraction_only",
                run_id=run[0], details={"cause": reason, "anchor_job_id": job.id,
                    "canonical_extraction_preserved": True})
        siblings = connection.execute("""SELECT j.id FROM publication_authoring_chunk c
            JOIN durable_job j ON j.id=c.durable_job_id WHERE c.run_id=? AND j.id<>?
            AND j.state IN ('queued','retry','claimed','running')""", (run[0], job.id)).fetchall()
        for sibling in siblings:
            self._jobs.request_cancel(connection, job_id=sibling[0], now_ms=db_now_ms(connection))
        if self._synthesis is not None:
            self._synthesis.cancel_siblings(connection, run_id=run[0], except_job_id=job.id)
