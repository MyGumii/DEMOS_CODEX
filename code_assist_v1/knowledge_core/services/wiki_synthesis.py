"""Opt-in durable map -> wave -> reduced Wiki workflow.

Planner claims do no model work. Reducer claims execute exactly one batch.
The kernel's ordinary pre-claim reconciliation schedules the next complete
input round, including after a crash between a commit and acknowledgement.
This is not public-upload or production-runtime activation.
"""
from dataclasses import asdict
import json
from uuid import UUID, uuid5

from ..db import db_now_ms
from ..repositories.audit import AuditEventDraft, AuditRepository
from ..repositories.outbox import OutboxEventDraft, OutboxRepository
from ..repositories.users import UserRepository
from ..repositories.jobs import ClaimFence, JobEnqueueDraft, JobRepository
from ..repositories.wiki_authoring import AuthoringStorageConflict, _json, _sha
from ..repositories.wiki_synthesis import (
    PreparedSynthesisProgress, PreparedReductionReceipt, WikiSynthesisRepository,
    reduction_job_result, authored_snapshot_id,
)
from ..scope import Capability
from ..wiki_authoring import AuthoringError, AuthoringModelError
from ..wiki_synthesis import SYNTHESIS_VERSION, SynthesisPolicy
from ..worker import (
    JobAuthorizationRejectedError, JobExpectedStateConflictError, PermanentJobExecutionError,
    PreparedJobEffect, RegisteredJobHandler, RetryableJobExecutionError,
)


_NAMESPACE = UUID("2753ae3d-3b98-4230-99a1-af73d20d4207")
_PLAN = "publication_wiki.plan_synthesis"
_REDUCE = "publication_wiki.reduce_batch"
_ATTEMPTS = 2  # v1 frozen map/reducer/planner durable policy
_ACTIVE = ("queued", "retry", "claimed", "running")


class WikiSynthesisWorkflow:
    def __init__(self, factory, *, chunks, author, policy=SynthesisPolicy()):
        if not isinstance(policy, SynthesisPolicy):
            raise ValueError("A server-owned synthesis policy is required.")
        self._factory, self._chunks, self._author, self._policy = factory, chunks, author, policy
        self._repository, self._jobs, self._kernel = WikiSynthesisRepository(), JobRepository(), None
        chunks.bind_synthesis(self)

    @property
    def database_path(self):
        return self._factory.database_path

    @property
    def durable_attempts(self):
        return (_ATTEMPTS, _ATTEMPTS, _ATTEMPTS)

    @property
    def kernel(self):
        if self._kernel is None:
            raise RuntimeError("Synthesis has no canonical worker kernel.")
        return self._kernel

    def bind_kernel(self, kernel):
        if (self._kernel is not None or kernel.database_path != self.database_path
                or self._chunks.kernel is not kernel):
            raise ValueError("Synthesis requires one same-database canonical kernel.")
        for handler in self.registered_handlers():
            kernel.registry.require(handler.key)
        self._kernel = kernel

    def registered_handlers(self):
        shared = dict(handler_version="v1", actor_kind="user", required_capability=Capability.PUBLISH,
            validate_input=self.validate_input, revalidate_expected_state=self.revalidate_expected_state,
            prepare_terminal=self.prepare_terminal, converge_terminal=self.converge_terminal)
        return (
            RegisteredJobHandler.build(job_type=_PLAN, prepare=self.prepare_plan,
                commit_effect=self.commit_plan, reconcile_pending=self.reconcile_pending, **shared),
            RegisteredJobHandler.build(job_type=_REDUCE, prepare=self.prepare_batch,
                commit_effect=self.commit_batch, **shared),
        )

    def register_run(self, connection, *, prepared, run_id, parent, policy=None):
        """Called by chunk enqueue in its writer, before any map can execute."""
        old = connection.execute("SELECT parent_job_id,version,policy_json,policy_sha256,source_fence,"
            "map_durable_attempts,reduction_durable_attempts,planner_durable_attempts "
            "FROM publication_synthesis_intent WHERE run_id=?", (run_id,)).fetchone()
        selected = self._policy if policy is None else policy
        if not isinstance(selected, SynthesisPolicy):
            raise AuthoringStorageConflict("A frozen synthesis policy is required.")
        policy = _json(asdict(selected))
        values = (parent.id, SYNTHESIS_VERSION, policy, _sha(policy), prepared.fence_json,
            _ATTEMPTS, _ATTEMPTS, _ATTEMPTS)
        if old is not None:
            # A retry cannot replace the previously frozen deployment policy.
            if tuple(old) != values:
                raise AuthoringStorageConflict("Synthesis upload intent already has another policy.")
            return
        connection.execute("""INSERT INTO publication_synthesis_intent
            (run_id,parent_job_id,version,policy_json,policy_sha256,source_fence,
             map_durable_attempts,reduction_durable_attempts,planner_durable_attempts,created_at)
            VALUES(?,?,?,?,?,?,?,?,?,?)""", (run_id, *values, db_now_ms(connection)))

    def _intent(self, connection, run_id):
        row = connection.execute("""SELECT i.*,r.actor_user_id,r.preview_id,r.plan_sha256,r.status
            FROM publication_synthesis_intent i JOIN publication_authoring_run r ON r.id=i.run_id
            WHERE i.run_id=?""", (run_id,)).fetchone()
        if (row is None or row["version"] != SYNTHESIS_VERSION
                or any(row[key] != _ATTEMPTS for key in
                    ("map_durable_attempts", "reduction_durable_attempts", "planner_durable_attempts"))):
            raise AuthoringStorageConflict("The retained synthesis intent is unavailable.")
        if (_sha(row["policy_json"]) != row["policy_sha256"]
                or _json(asdict(SynthesisPolicy(**json.loads(row["policy_json"])))) != row["policy_json"]):
            raise AuthoringStorageConflict("The retained synthesis policy is inconsistent.")
        return row

    def _parent(self, connection, intent):
        parent = self._chunks._parent(connection, intent["preview_id"], intent["actor_user_id"])
        if (parent.id != intent["parent_job_id"] or intent["status"] != "pending"
                or self._repository.maps._run_source_fence(connection, preview_id=intent["preview_id"],
                    actor_user_id=intent["actor_user_id"], now_ms=db_now_ms(connection)) != intent["source_fence"]):
            raise AuthoringStorageConflict("The current synthesis upload binding changed.")
        return parent

    @staticmethod
    def validate_input(value, expected):
        planner = set(value) == {"run_id", "after_round"}
        reducer = set(value) == {"run_id", "round_number", "ordinal", "batch_id", "request_sha256"}
        if (not (planner or reducer) or set(expected) != {"schema_version", "version", "policy_sha256",
                "parent_job_id", "plan_sha256", "source_fence", "durable_attempts", "_authorization_fence"}
                or type(expected["schema_version"]) is not int or expected["schema_version"] != 1
                or expected["version"] != SYNTHESIS_VERSION
                or type(expected["durable_attempts"]) is not int or expected["durable_attempts"] != _ATTEMPTS
                or any(type(value[key]) is not int or value[key] < minimum for key, minimum in
                    (("after_round", 0),) if planner)
                or (reducer and any(type(value[key]) is not int or value[key] < 1
                    for key in ("round_number", "ordinal")))):
            raise JobExpectedStateConflictError("Invalid versioned synthesis job input.")

    @staticmethod
    def _identity(kind, value, policy_sha):
        effect = f"wiki-synthesis:v1:{kind}:{_sha(_json((value, policy_sha)))}"
        return str(uuid5(_NAMESPACE, effect)), effect

    def _enqueue(self, connection, *, kind, value, intent, parent):
        job_id, effect = self._identity(kind, value, intent["policy_sha256"])
        expected = dict(schema_version=1, version=SYNTHESIS_VERSION, policy_sha256=intent["policy_sha256"],
            parent_job_id=parent.id, plan_sha256=intent["plan_sha256"], source_fence=intent["source_fence"],
            durable_attempts=_ATTEMPTS, _authorization_fence=parent.expected_state.get("_authorization_fence"))
        self.validate_input(value, expected)
        queued = self._jobs.enqueue(connection, JobEnqueueDraft(job_id=job_id, dedup_id=None,
            operation_id=None, actor_kind="user", actor_user_id=parent.actor_user_id, space_id=parent.space_id,
            job_type=kind, handler_version="v1", effect_key=effect, input_ref=value, expected_state=expected,
            authorization_snapshot=parent.authorization_snapshot,
            authorization_scope_epoch=parent.authorization_scope_epoch,
            authorization_part_id=parent.authorization_part_id,
            authorization_capabilities=parent.authorization_capabilities, max_attempts=_ATTEMPTS,
            cancellable=True, available_at_ms=db_now_ms(connection)))
        return queued

    def _complete_inputs(self, connection, run_id, after_round):
        """Metadata-only proof; full source/result hashes are replayed outside SQL writers."""
        if after_round == 0:
            rows = connection.execute("""SELECT c.ordinal,b.response_sha256,j.state,j.result_json
                FROM publication_authoring_chunk c LEFT JOIN publication_authoring_chunk_body b
                ON b.run_id=c.run_id AND b.ordinal=c.ordinal LEFT JOIN durable_job j ON j.id=c.durable_job_id
                WHERE c.run_id=? ORDER BY c.ordinal""", (run_id,)).fetchall()
            count = connection.execute("SELECT chunk_count FROM publication_authoring_run WHERE id=?", (run_id,)).fetchone()[0]
            if len(rows) != count:
                raise AuthoringStorageConflict("Incomplete map job set.")
            for ordinal, digest, state, result in rows:
                expected = dict(schema_version=1, run_id=run_id, ordinal=ordinal, response_sha256=digest)
                if digest is None or state != "succeeded" or _json(json.loads(result)) != _json(expected):
                    raise AuthoringStorageConflict("Map jobs have not completed their exact effects.")
        else:
            self._completed_planner(connection, run_id, after_round - 1)
            wave = connection.execute("SELECT batch_count FROM publication_synthesis_wave WHERE run_id=? AND round_number=?",
                (run_id, after_round)).fetchone()
            rows = connection.execute("""SELECT b.ordinal,b.batch_id,b.request_sha256,r.response_sha256,
                r.retention_status,x.run_id,j.state,j.result_json FROM publication_synthesis_batch b
                LEFT JOIN publication_synthesis_receipt r USING(run_id,round_number,ordinal)
                LEFT JOIN publication_synthesis_batch_body x USING(run_id,round_number,ordinal)
                LEFT JOIN durable_job j ON j.id=b.durable_job_id WHERE b.run_id=? AND b.round_number=?
                ORDER BY b.ordinal""", (run_id, after_round)).fetchall()
            if wave is None or len(rows) != wave[0]:
                raise AuthoringStorageConflict("Incomplete synthesis batch job set.")
            for ordinal, batch, request, response, retention, body, state, result in rows:
                expected = dict(schema_version=1, run_id=run_id, round_number=after_round, ordinal=ordinal,
                    batch_id=batch, request_sha256=request, response_sha256=response)
                if (response is None or retention != "present" or body is None or state != "succeeded"
                        or _json(json.loads(result)) != _json(expected)):
                    raise AuthoringStorageConflict("Reduction jobs have not completed their exact retained effects.")

    def _completed_planner(self, connection, run_id, after_round):
        row = connection.execute("""SELECT j.state,j.result_json,w.plan_sha256,w.wave_id,w.batch_count
            FROM publication_synthesis_planner p JOIN durable_job j ON j.id=p.durable_job_id
            LEFT JOIN publication_synthesis_wave w ON w.run_id=p.run_id AND w.round_number=p.after_round+1
            WHERE p.run_id=? AND p.after_round=?""", (run_id, after_round)).fetchone()
        if row is None or row[0] != "succeeded" or row[2] is None:
            raise AuthoringStorageConflict("The predecessor planner did not commit its exact wave.")
        expected = dict(schema_version=1, run_id=run_id, after_round=after_round, outcome="wave",
            plan_sha256=row[2], round_number=after_round+1, wave_id=row[3], batch_count=row[4])
        if _json(json.loads(row[1])) != _json(expected):
            raise AuthoringStorageConflict("The predecessor planner did not commit its exact wave.")

    def revalidate_expected_state(self, connection, job):
        try:
            self.validate_input(job.input_ref, job.expected_state)
            value, expected = job.input_ref, job.expected_state
            intent = self._intent(connection, value["run_id"])
            parent = self._parent(connection, intent)
            job_id, effect = self._identity(job.job_type, value, intent["policy_sha256"])
            if (job.id != job_id or job.effect_key != effect or job.max_attempts != _ATTEMPTS
                    or job.actor_user_id != intent["actor_user_id"] or job.space_id != parent.space_id
                    or any(expected[key] != intent[key] for key in
                        ("version", "policy_sha256", "parent_job_id", "plan_sha256", "source_fence"))):
                raise AuthoringStorageConflict("Synthesis job identity or authority changed.")
            self._repository.maps._completion_fence(connection, run_id=value["run_id"],
                actor_user_id=job.actor_user_id, now_ms=db_now_ms(connection))
            if job.job_type == _PLAN:
                bound = connection.execute("SELECT durable_job_id FROM publication_synthesis_planner WHERE run_id=? AND after_round=?",
                    (value["run_id"], value["after_round"])).fetchone()
                latest = connection.execute("SELECT coalesce(max(round_number),0) FROM publication_synthesis_wave WHERE run_id=?",
                    (value["run_id"],)).fetchone()[0]
                if bound is None or bound[0] != job.id or latest != value["after_round"]:
                    raise AuthoringStorageConflict("Planner is not bound to the current completed input round.")
                self._complete_inputs(connection, value["run_id"], value["after_round"])
            elif job.job_type == _REDUCE:
                self._completed_planner(connection, value["run_id"], value["round_number"] - 1)
                bound = connection.execute("""SELECT durable_job_id,batch_id,request_sha256
                    FROM publication_synthesis_batch WHERE run_id=? AND round_number=? AND ordinal=?""",
                    (value["run_id"], value["round_number"], value["ordinal"])).fetchone()
                if bound is None or tuple(bound) != (job.id, value["batch_id"], value["request_sha256"]):
                    raise AuthoringStorageConflict("Reducer is not bound to its exact batch.")
            else:
                raise AuthoringStorageConflict("Unknown synthesis handler kind.")
        except (AuthoringStorageConflict, AuthoringError, ValueError, TypeError, KeyError) as exc:
            raise JobExpectedStateConflictError("Synthesis authority, source or predecessor changed.") from exc

    def prepare_plan(self, job):
        fence = ClaimFence(job.id, job.claim_owner, job.attempt_count)
        self.kernel.preflight_preparation(fence)
        connection = self._factory.open(read_only=True)
        try:
            intent = self._intent(connection, job.input_ref["run_id"])
            value = self._repository.prepare_progress(connection, run_id=job.input_ref["run_id"],
                actor_user_id=job.actor_user_id, now_ms=db_now_ms(connection),
                policy=SynthesisPolicy(**json.loads(intent["policy_json"])),
                map_durable_attempts=_ATTEMPTS, reduction_durable_attempts=_ATTEMPTS)
        finally:
            connection.close()
        self.kernel.preflight_preparation(fence)
        return PreparedJobEffect(result=self._plan_result(job, value), payload=value)

    @staticmethod
    def _plan_result(job, value):
        result = dict(schema_version=1, run_id=value.run_id, after_round=job.input_ref["after_round"])
        if value.wave_json is not None:
            wave = json.loads(value.wave_json)
            return dict(result, outcome="wave", round_number=wave["round_number"], wave_id=wave["wave_id"],
                plan_sha256=_sha(value.wave_json), batch_count=len(wave["batches"]))
        digest = _sha(value.wiki.markdown)
        return dict(result, outcome="ready", snapshot_id=authored_snapshot_id(value.run_id, digest),
            content_sha256=digest, content_bytes=len(value.wiki.markdown.encode()),
            manifest_sha256=_sha(value.wiki.manifest_json), manifest_bytes=len(value.wiki.manifest_json.encode()),
            mode=value.completion[0], last_round=value.completion[1], final_wave_id=value.completion[2])

    def commit_plan(self, connection, job, prepared):
        value = prepared.payload
        if (not isinstance(value, PreparedSynthesisProgress) or value.run_id != job.input_ref["run_id"]
                or value.actor_user_id != job.actor_user_id or dict(prepared.result) != self._plan_result(job, value)):
            raise PermanentJobExecutionError("Invalid prepared synthesis plan effect.")
        try:
            intent = self._intent(connection, value.run_id)
            parent = self._parent(connection, intent)
            self._repository.persist_progress(connection, prepared=value, now_ms=db_now_ms(connection))
            if value.wave_json is not None:
                wave = json.loads(value.wave_json)
                if wave["round_number"] != job.input_ref["after_round"] + 1:
                    raise AuthoringStorageConflict("Planner output skipped a synthesis round.")
                for ordinal, batch in enumerate(wave["batches"], 1):
                    inputs = dict(run_id=value.run_id, round_number=wave["round_number"], ordinal=ordinal, **batch)
                    queued = self._enqueue(connection, kind=_REDUCE, value=inputs, intent=intent, parent=parent)
                    self._repository.bind_batch_job(connection, run_id=value.run_id, actor_user_id=job.actor_user_id,
                        round_number=wave["round_number"], ordinal=ordinal, job_id=queued.job.id, now_ms=db_now_ms(connection))
            self._event(connection, job, action="wiki.synthesis." + prepared.result["outcome"], details=prepared.result)
        except AuthoringStorageConflict as exc:
            raise JobExpectedStateConflictError("The prepared synthesis plan can no longer commit.") from exc

    def prepare_batch(self, job):
        fence = ClaimFence(job.id, job.claim_owner, job.attempt_count)
        self.kernel.preflight_preparation(fence)
        args = dict(run_id=job.input_ref["run_id"], actor_user_id=job.actor_user_id,
            round_number=job.input_ref["round_number"], ordinal=job.input_ref["ordinal"], claim=fence)
        connection = self._factory.open(read_only=True)
        try:
            plan, wave, batch, policy = self._repository.load_batch(connection, **args, now_ms=db_now_ms(connection))
        finally:
            connection.close()
        self.kernel.preflight_preparation(fence)
        try:
            result = self._author.author(plan, batch, wave=wave, policy=policy)
        except AuthoringModelError as exc:
            raise RetryableJobExecutionError("The synthesis provider attempt did not complete.") from exc
        self.kernel.preflight_preparation(fence)
        connection = self._factory.open(read_only=True)
        try:
            value = self._repository.prepare_receipt(connection, **args, result=result, now_ms=db_now_ms(connection))
        finally:
            connection.close()
        return PreparedJobEffect(result=reduction_job_result(run_id=value.run_id,
            ordinal=value.ordinal, receipt=value.receipt), payload=value)

    def commit_batch(self, connection, job, prepared):
        value = prepared.payload
        if (not isinstance(value, PreparedReductionReceipt) or value.claim != ClaimFence(job.id, job.claim_owner, job.attempt_count)
                or value.run_id != job.input_ref["run_id"] or value.actor_user_id != job.actor_user_id
                or value.ordinal != job.input_ref["ordinal"]
                or dict(prepared.result) != reduction_job_result(run_id=value.run_id, ordinal=value.ordinal, receipt=value.receipt)):
            raise PermanentJobExecutionError("Invalid prepared synthesis receipt effect.")
        try:
            self._repository.save_receipt(connection, prepared=value, now_ms=db_now_ms(connection))
            self._event(connection, job, action="wiki.synthesis.batch_completed", details=prepared.result)
        except AuthoringStorageConflict as exc:
            raise JobExpectedStateConflictError("The prepared synthesis receipt can no longer commit.") from exc

    def reconcile_pending(self, connection):
        """At most one failed and one eligible intent per normal kernel poll."""
        failed = connection.execute("""SELECT i.run_id FROM publication_synthesis_intent i
            JOIN publication_authoring_run r ON r.id=i.run_id JOIN publication_preview p ON p.id=r.preview_id
            WHERE r.status IN ('pending','extraction_only') AND
            ((r.status='pending' AND (p.status<>'ready' OR p.expires_at<=? OR EXISTS(
                SELECT 1 FROM publication_synthesis_receipt x WHERE x.run_id=r.id AND (x.retention_status<>'present'
                  OR NOT EXISTS(SELECT 1 FROM publication_synthesis_batch_body b WHERE b.run_id=x.run_id
                    AND b.round_number=x.round_number AND b.ordinal=x.ordinal))) OR EXISTS(
                SELECT 1 FROM (SELECT run_id,durable_job_id FROM publication_synthesis_planner
                  UNION ALL SELECT run_id,durable_job_id FROM publication_synthesis_batch) b
                JOIN durable_job j ON j.id=b.durable_job_id WHERE b.run_id=r.id
                  AND j.state IN ('failed','cancelled','dead')) OR EXISTS(
                SELECT 1 FROM publication_synthesis_planner q JOIN durable_job j ON j.id=q.durable_job_id
                WHERE q.run_id=r.id AND j.state='succeeded' AND NOT EXISTS(
                  SELECT 1 FROM publication_synthesis_wave w WHERE w.run_id=q.run_id AND w.round_number=q.after_round+1))))
             OR (r.status='extraction_only' AND EXISTS(
                SELECT 1 FROM (SELECT run_id,durable_job_id FROM publication_synthesis_planner
                  UNION ALL SELECT run_id,durable_job_id FROM publication_synthesis_batch) b
                JOIN durable_job j ON j.id=b.durable_job_id WHERE b.run_id=r.id
                AND j.state IN ('queued','retry','claimed','running') AND j.cancel_requested=0)))
            ORDER BY r.created_at,r.id LIMIT 1""", (db_now_ms(connection),)).fetchone()
        if failed is not None:
            self._terminal_run(connection, failed[0], reason="synthesis_unavailable")
        eligible = connection.execute("""SELECT i.run_id,coalesce((SELECT max(round_number)
            FROM publication_synthesis_wave w WHERE w.run_id=i.run_id),0) AS completed_round
            FROM publication_synthesis_intent i JOIN publication_authoring_run r ON r.id=i.run_id
            WHERE r.status='pending' AND NOT EXISTS(SELECT 1 FROM publication_authoring_chunk c
                LEFT JOIN durable_job j ON j.id=c.durable_job_id WHERE c.run_id=r.id
                AND (j.id IS NULL OR j.state<>'succeeded'))
            AND NOT EXISTS(SELECT 1 FROM publication_synthesis_batch b LEFT JOIN durable_job j ON j.id=b.durable_job_id
                WHERE b.run_id=r.id AND (j.id IS NULL OR j.state<>'succeeded'))
            AND NOT EXISTS(SELECT 1 FROM publication_synthesis_planner p WHERE p.run_id=r.id
                AND p.after_round=coalesce((SELECT max(round_number) FROM publication_synthesis_wave w WHERE w.run_id=r.id),0))
            ORDER BY r.created_at,r.id LIMIT 1""").fetchone()
        if eligible is None:
            return
        run_id, after_round = eligible
        try:
            intent = self._intent(connection, run_id)
            parent = self._parent(connection, intent)
            self._complete_inputs(connection, run_id, after_round)
        except (AuthoringStorageConflict, AuthoringError, JobAuthorizationRejectedError) as exc:
            self._terminal_run(connection, run_id, reason="predecessor_unavailable")
            return
        queued = self._enqueue(connection, kind=_PLAN, value=dict(run_id=run_id, after_round=after_round),
            intent=intent, parent=parent)
        connection.execute("INSERT INTO publication_synthesis_planner(run_id,after_round,durable_job_id) VALUES(?,?,?)",
            (run_id, after_round, queued.job.id))
        self._event(connection, queued.job, action="wiki.synthesis.planner_queued",
            details={"run_id": run_id, "after_round": after_round})

    def cancel_siblings(self, connection, *, run_id, except_job_id=None):
        jobs = connection.execute("""SELECT durable_job_id FROM publication_synthesis_planner WHERE run_id=?
            UNION SELECT durable_job_id FROM publication_synthesis_batch WHERE run_id=?
            UNION SELECT durable_job_id FROM publication_authoring_chunk WHERE run_id=?""", (run_id, run_id, run_id)).fetchall()
        for row in jobs:
            if row[0] is not None and row[0] != except_job_id:
                job = self._jobs.require(connection, row[0])
                if job.state in _ACTIVE:
                    self._jobs.request_cancel(connection, job_id=job.id, now_ms=db_now_ms(connection))

    def _terminal_run(self, connection, run_id, *, reason, job=None):
        intent = self._intent(connection, run_id)
        if intent["status"] == "ready":
            return
        if intent["status"] == "pending":
            self._repository.maps.extraction_only(connection, run_id=run_id, actor_user_id=intent["actor_user_id"],
                now_ms=db_now_ms(connection), failure_code="authoring_incomplete")
            anchor = job or self._jobs.require(connection, intent["parent_job_id"])
            self._chunks._event(connection, anchor, key=f"{run_id}:extraction_only", action="wiki.authoring.extraction_only",
                run_id=run_id, details={"cause": reason, "anchor_job_id": anchor.id, "canonical_extraction_preserved": True})
        self.cancel_siblings(connection, run_id=run_id, except_job_id=None if job is None else job.id)

    def prepare_terminal(self, connection, job, terminal_state):
        self.converge_terminal(connection, job)

    def converge_terminal(self, connection, job):
        bound = connection.execute("""SELECT run_id FROM publication_synthesis_planner WHERE durable_job_id=?
            UNION SELECT run_id FROM publication_synthesis_batch WHERE durable_job_id=?""", (job.id, job.id)).fetchone()
        if bound is not None:
            self._terminal_run(connection, bound[0], reason="synthesis_job_terminal", job=job)

    @staticmethod
    def _event(connection, job, *, action, details):
        now = db_now_ms(connection)
        identity = str(uuid5(_NAMESPACE, job.id + ":" + action))
        actor = UserRepository().require(connection, job.actor_user_id)
        payload = {"schema_version": 1, "job_id": job.id, **details}
        AuditRepository().append(connection, AuditEventDraft(event_uuid=identity, occurred_at_ms=now,
            request_id=job.id, operation_id=job.operation_id, action=action, outcome="success", actor_kind="user",
            actor_user_id=actor.id, actor_display_name=actor.display_name, actor_role_snapshot=actor.system_role,
            space_id=job.space_id, details=payload))
        # One immutable effect per aggregate, not many changing run events all at version 1.
        OutboxRepository().append(connection, OutboxEventDraft(logical_key="wiki-synthesis:" + identity,
            space_id=job.space_id, aggregate_type="wiki_synthesis_effect", aggregate_id=identity,
            aggregate_version=1, event_type=action, payload=payload, available_at_ms=now))
