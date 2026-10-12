"""Opt-in upload entry: frozen request -> extraction -> durable source planner.

No HTTP/runtime activation. Planning does not call a model. The source planner
commits all map jobs and the synthesis intent in its own canonical job effect.
"""
from dataclasses import asdict, dataclass, field
import json
from uuid import UUID, uuid5

from ..db import db_now_ms
from ..repositories.jobs import ClaimFence, JobEnqueueDraft, JobRepository
from ..repositories.wiki_authoring import AuthoringStorageConflict, PreparedAuthoringRun, _json, _sha
from ..scope import Capability
from ..wiki_authoring import AUTHORING_POLICY_VERSION, AuthoringError, AuthoringModelPolicy, AuthoringWorkPolicy
from ..wiki_synthesis import SYNTHESIS_VERSION, SynthesisPolicy
from ..worker import (JobAuthorizationRejectedError, JobExpectedStateConflictError,
    PermanentJobExecutionError, PreparedJobEffect, RegisteredJobHandler)
from .wiki_synthesis import WikiSynthesisWorkflow


_VERSION = "upload-source-v1"
_KIND = "publication_wiki.plan_upload"
_ATTEMPTS = 2
_NAMESPACE = UUID("0f30dc7c-79cf-4099-a913-d649b3dd45b2")


@dataclass(frozen=True, slots=True)
class WikiUploadPolicy:
    chunk_bytes: int = 24_576
    model: AuthoringModelPolicy = field(default_factory=AuthoringModelPolicy)
    work: AuthoringWorkPolicy = field(default_factory=AuthoringWorkPolicy)
    synthesis: SynthesisPolicy = field(default_factory=SynthesisPolicy)

    def __post_init__(self):
        if (type(self.chunk_bytes) is not int or not 4 <= self.chunk_bytes <= 24_576
                or not isinstance(self.model, AuthoringModelPolicy)
                or not isinstance(self.work, AuthoringWorkPolicy)
                or not isinstance(self.synthesis, SynthesisPolicy)):
            raise AuthoringStorageConflict("Invalid server-owned Wiki upload policy.")
        if len(self.encoded().encode()) > 16_384:
            raise AuthoringStorageConflict("The frozen policy metadata is too large.")

    def encoded(self):
        return _json(dict(version=_VERSION, authoring_version=AUTHORING_POLICY_VERSION,
            synthesis_version=SYNTHESIS_VERSION, durable_attempts=_ATTEMPTS,
            map_durable_attempts=2, reduction_durable_attempts=2, synthesis_planner_durable_attempts=2,
            chunk_bytes=self.chunk_bytes, model_policy=asdict(self.model),
            work_policy=asdict(self.work), synthesis_policy=asdict(self.synthesis)))

    @classmethod
    def restore(cls, encoded):
        try:
            value = json.loads(encoded)
            model = dict(value["model_policy"])
            for key in ("provider_order", "model_ids"):
                if model[key] is not None:
                    model[key] = tuple(model[key])
            if model["wire_models"] is not None:
                model["wire_models"] = tuple(tuple(pair) for pair in model["wire_models"])
            policy = cls(value["chunk_bytes"], AuthoringModelPolicy(**model),
                AuthoringWorkPolicy(**value["work_policy"]), SynthesisPolicy(**value["synthesis_policy"]))
            if policy.encoded() != encoded:
                raise ValueError("Policy schema/version is not canonical.")
            return policy
        except (TypeError, ValueError, KeyError) as exc:
            raise AuthoringStorageConflict("The retained Wiki upload policy is invalid.") from exc


class WikiUploadWorkflow:
    def __init__(self, factory, *, chunks, synthesis, policy=WikiUploadPolicy()):
        if (not isinstance(policy, WikiUploadPolicy) or synthesis.database_path != factory.database_path
                or chunks._synthesis is not synthesis or synthesis._chunks is not chunks):
            raise ValueError("Upload entry requires one same-database chunk/synthesis workflow.")
        self._factory, self._chunks, self._synthesis, self._policy = factory, chunks, synthesis, policy
        self._jobs, self._kernel = JobRepository(), None

    @property
    def database_path(self):
        return self._factory.database_path

    @property
    def kernel(self):
        if self._kernel is None:
            raise RuntimeError("Upload entry has no canonical worker kernel.")
        return self._kernel

    def bind_kernel(self, kernel):
        if (self._kernel is not None or kernel.database_path != self.database_path
                or self._chunks.kernel is not kernel or self._synthesis.kernel is not kernel):
            raise ValueError("Upload entry requires the shared canonical worker kernel.")
        kernel.registry.require(self.registered_handler().key)
        self._kernel = kernel

    def registered_handler(self):
        return RegisteredJobHandler.build(job_type=_KIND, handler_version="v1", actor_kind="user",
            required_capability=Capability.PUBLISH, validate_input=self.validate_input,
            revalidate_expected_state=self.revalidate_expected_state, prepare=self.prepare,
            commit_effect=self.commit_effect, prepare_terminal=self.prepare_terminal,
            converge_terminal=self.converge_terminal, reconcile_pending=self.reconcile_pending)

    def register_upload(self, connection, *, preview_id, parent):
        """New-preview writer callback. Replay must never call this registrar."""
        if not connection.in_transaction:
            raise AuthoringStorageConflict("Upload registration requires the preview writer.")
        policy = self._policy.encoded()
        connection.execute("""INSERT INTO publication_wiki_request
            (preview_id,parent_job_id,version,policy_json,policy_sha256,created_at)
            VALUES(?,?,?,?,?,?)""", (preview_id, parent.id, _VERSION, policy, _sha(policy), db_now_ms(connection)))
        self._event(connection, parent, action="wiki.upload.requested", preview_id=preview_id,
            policy_sha256=_sha(policy))

    def _request(self, connection, preview_id):
        row = connection.execute("""SELECT r.*,p.actor_user_id FROM publication_wiki_request r
            JOIN publication_preview p ON p.id=r.preview_id WHERE r.preview_id=?""", (preview_id,)).fetchone()
        if (row is None or row["version"] != _VERSION
                or _sha(row["policy_json"]) != row["policy_sha256"]):
            raise AuthoringStorageConflict("The frozen upload request is unavailable.")
        policy = WikiUploadPolicy.restore(row["policy_json"])
        # These are exact-version v1 work ceilings, not mutable deployment knobs.
        # A new implementation needs a new handler version; refuse rather than
        # silently broadening attempts on an already-accepted request.
        if self._chunks.durable_attempts != 2 or self._synthesis.durable_attempts != (2, 2, 2):
            raise AuthoringStorageConflict("The frozen v1 durable attempt implementation changed.")
        return row, policy

    def _source(self, connection, request):
        parent = self._chunks._parent(connection, request["preview_id"], request["actor_user_id"])
        if parent.id != request["parent_job_id"]:
            raise AuthoringStorageConflict("The initial extraction job changed.")
        fence = self._chunks._repository._run_source_fence(connection, preview_id=request["preview_id"],
            actor_user_id=request["actor_user_id"], now_ms=db_now_ms(connection))
        return parent, fence

    @staticmethod
    def _identity(preview_id, policy_sha):
        effect = f"wiki-upload:v1:{preview_id}:{policy_sha}"
        return str(uuid5(_NAMESPACE, effect)), effect

    @staticmethod
    def validate_input(value, expected):
        if (set(value) != {"preview_id"} or not isinstance(value["preview_id"], str)
                or set(expected) != {"schema_version", "version", "policy_sha256", "parent_job_id",
                    "source_fence", "durable_attempts", "_authorization_fence"}
                or type(expected["schema_version"]) is not int or expected["schema_version"] != 1
                or expected["version"] != _VERSION or type(expected["durable_attempts"]) is not int
                or expected["durable_attempts"] != _ATTEMPTS):
            raise JobExpectedStateConflictError("Invalid initial Wiki planner input.")

    def revalidate_expected_state(self, connection, job):
        self.validate_input(job.input_ref, job.expected_state)
        preview = job.input_ref["preview_id"]
        try:
            request, _ = self._request(connection, preview)
            parent, fence = self._source(connection, request)
            binding = connection.execute("SELECT durable_job_id FROM publication_wiki_source_plan WHERE preview_id=?",
                (preview,)).fetchone()
            expected = dict(schema_version=1, version=_VERSION, policy_sha256=request["policy_sha256"],
                parent_job_id=parent.id, source_fence=fence, durable_attempts=_ATTEMPTS,
                _authorization_fence=parent.expected_state.get("_authorization_fence"))
            if (binding is None or binding[0] != job.id
                    or (job.id, job.effect_key) != self._identity(preview, request["policy_sha256"])
                    or job.actor_user_id != request["actor_user_id"] or job.space_id != parent.space_id
                    or job.max_attempts != _ATTEMPTS or _json(job.expected_state) != _json(expected)
                    or connection.execute("SELECT 1 FROM publication_wiki_source_result WHERE preview_id=?", (preview,)).fetchone()
                    or connection.execute("SELECT 1 FROM publication_authoring_run WHERE preview_id=?", (preview,)).fetchone()):
                raise AuthoringStorageConflict("The initial source planner binding changed.")
        except (AuthoringStorageConflict, AuthoringError, TypeError, ValueError) as exc:
            raise JobExpectedStateConflictError("The initial authoring source or request is unavailable.") from exc

    @staticmethod
    def _result(prepared, request):
        return dict(schema_version=1, preview_id=prepared.preview_id, parent_job_id=request["parent_job_id"],
            policy_sha256=request["policy_sha256"], run_id=prepared.run_id,
            plan_sha256=prepared.plan_sha256, chunk_count=len(prepared.chunks))

    def prepare(self, job):
        fence = ClaimFence(job.id, job.claim_owner, job.attempt_count)
        self.kernel.preflight_preparation(fence)
        connection = self._factory.open(read_only=True)
        try:
            request, policy = self._request(connection, job.input_ref["preview_id"])
        finally:
            connection.close()
        try:
            prepared = self._chunks.prepare_upload(preview_id=request["preview_id"],
                actor_user_id=job.actor_user_id, chunk_bytes=policy.chunk_bytes,
                model_policy=policy.model, work_policy=policy.work)
        except (AuthoringStorageConflict, AuthoringError) as exc:
            raise PermanentJobExecutionError("The immutable upload cannot be planned completely.") from exc
        self.kernel.preflight_preparation(fence)
        return PreparedJobEffect(self._result(prepared, request), prepared)

    def commit_effect(self, connection, job, prepared):
        try:
            self._commit_effect(connection, job, prepared)
        except (AuthoringStorageConflict, AuthoringError) as exc:
            raise JobExpectedStateConflictError("The initial Wiki plan can no longer commit.") from exc

    def _commit_effect(self, connection, job, prepared):
        value = prepared.payload
        request, policy = self._request(connection, job.input_ref["preview_id"])
        if (not isinstance(value, PreparedAuthoringRun) or value.preview_id != request["preview_id"]
                or value.actor_user_id != job.actor_user_id
                or _json(prepared.result) != _json(self._result(value, request))):
            raise AuthoringStorageConflict("The initial planner effect is inconsistent.")
        plan = json.loads(value.plan_json)
        if (plan["chunk_bytes"] != policy.chunk_bytes or plan["policy_version"] != AUTHORING_POLICY_VERSION
                or _json(plan["model_policy"]) != _json(asdict(policy.model))
                or _json(plan["work_policy"]) != _json(asdict(policy.work))):
            raise AuthoringStorageConflict("The source plan differs from the frozen upload policy.")
        run_id, jobs = self._chunks.enqueue_prepared(connection, value, synthesis_policy=policy.synthesis)
        if run_id != value.run_id or len(jobs) != len(value.chunks):
            raise AuthoringStorageConflict("The complete map set was not registered.")
        connection.execute("""INSERT INTO publication_wiki_source_result
            (preview_id,outcome,run_id,reason,parent_state,completed_at)
            VALUES(?,'planned',?,NULL,'succeeded',?)""", (value.preview_id, run_id, db_now_ms(connection)))
        self._event(connection, job, action="wiki.upload.planned", preview_id=value.preview_id,
            run_id=run_id, plan_sha256=value.plan_sha256, chunk_count=len(jobs))

    def reconcile_pending(self, connection):
        """One failure and one eligible request per poll; metadata only."""
        failed = connection.execute("""SELECT r.preview_id FROM publication_wiki_request r
            JOIN publication_preview p ON p.id=r.preview_id JOIN durable_job e ON e.id=r.parent_job_id
            JOIN app_user u ON u.id=p.actor_user_id
            LEFT JOIN publication_wiki_source_plan q ON q.preview_id=r.preview_id
            LEFT JOIN durable_job j ON j.id=q.durable_job_id
            WHERE NOT EXISTS(SELECT 1 FROM publication_wiki_source_result x WHERE x.preview_id=r.preview_id)
            AND (p.status NOT IN ('queued','extracting','ready') OR p.expires_at<=?
              OR e.state IN ('failed','cancelled','dead') OR e.cancel_requested=1
              OR u.account_status<>'active' OR u.scope_epoch<>p.captured_scope_epoch
              OR j.state IN ('failed','cancelled','dead') OR j.cancel_requested=1)
            ORDER BY r.created_at,r.preview_id LIMIT 1""", (db_now_ms(connection),)).fetchone()
        if failed:
            self._terminal(connection, failed[0], reason="source_or_planner_unavailable")
        row = connection.execute("""SELECT r.preview_id FROM publication_wiki_request r
            JOIN publication_preview p ON p.id=r.preview_id JOIN durable_job j ON j.id=r.parent_job_id
            WHERE p.status='ready' AND j.state='succeeded'
            AND NOT EXISTS(SELECT 1 FROM publication_wiki_source_plan q WHERE q.preview_id=r.preview_id)
            AND NOT EXISTS(SELECT 1 FROM publication_wiki_source_result x WHERE x.preview_id=r.preview_id)
            ORDER BY r.created_at,r.preview_id LIMIT 1""").fetchone()
        if row is None:
            return
        try:
            request, _ = self._request(connection, row[0])
            parent, source_fence = self._source(connection, request)
        except (AuthoringStorageConflict, AuthoringError, JobAuthorizationRejectedError):
            self._terminal(connection, row[0], reason="source_authority_unavailable")
            return
        job_id, effect = self._identity(row[0], request["policy_sha256"])
        expected = dict(schema_version=1, version=_VERSION, policy_sha256=request["policy_sha256"],
            parent_job_id=parent.id, source_fence=source_fence, durable_attempts=_ATTEMPTS,
            _authorization_fence=parent.expected_state.get("_authorization_fence"))
        job = self._jobs.enqueue(connection, JobEnqueueDraft(job_id=job_id, dedup_id=None, operation_id=None,
            actor_kind="user", actor_user_id=parent.actor_user_id, space_id=parent.space_id,
            job_type=_KIND, handler_version="v1", effect_key=effect, input_ref=dict(preview_id=row[0]),
            expected_state=expected, authorization_snapshot=parent.authorization_snapshot,
            authorization_scope_epoch=parent.authorization_scope_epoch,
            authorization_part_id=parent.authorization_part_id,
            authorization_capabilities=parent.authorization_capabilities,
            max_attempts=_ATTEMPTS, cancellable=True, available_at_ms=db_now_ms(connection))).job
        connection.execute("INSERT INTO publication_wiki_source_plan(preview_id,durable_job_id) VALUES(?,?)", (row[0], job.id))
        self._event(connection, job, action="wiki.upload.planner_queued", preview_id=row[0])

    def _terminal(self, connection, preview_id, *, reason, except_job_id=None):
        existing = connection.execute("SELECT outcome FROM publication_wiki_source_result WHERE preview_id=?", (preview_id,)).fetchone()
        if existing is not None:
            return
        row = connection.execute("SELECT parent_job_id FROM publication_wiki_request WHERE preview_id=?", (preview_id,)).fetchone()
        parent = self._jobs.require(connection, row[0])
        connection.execute("""INSERT INTO publication_wiki_source_result
            (preview_id,outcome,run_id,reason,parent_state,completed_at)
            VALUES(?,'extraction_only',NULL,?,?,?)""", (preview_id, reason, parent.state, db_now_ms(connection)))
        bound = connection.execute("SELECT durable_job_id FROM publication_wiki_source_plan WHERE preview_id=?", (preview_id,)).fetchone()
        if bound and bound[0] != except_job_id:
            job = self._jobs.require(connection, bound[0])
            if job.state in {"queued", "retry", "claimed", "running"} and not job.cancel_requested:
                self._jobs.request_cancel(connection, job_id=job.id, now_ms=db_now_ms(connection))
        self._event(connection, parent, action="wiki.upload.extraction_only", preview_id=preview_id,
            reason=reason, parent_state=parent.state, llm_wiki_generated=False)

    def prepare_terminal(self, connection, job, terminal_state):
        self.converge_terminal(connection, job)

    def converge_terminal(self, connection, job):
        row = connection.execute("SELECT preview_id FROM publication_wiki_source_plan WHERE durable_job_id=?", (job.id,)).fetchone()
        if row:
            self._terminal(connection, row[0], reason="planner_terminal", except_job_id=job.id)

    @staticmethod
    def _event(connection, job, *, action, preview_id, **details):
        WikiSynthesisWorkflow._event(connection, job, action=action,
            details=dict(preview_id=preview_id, **details))
