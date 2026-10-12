"""Opt-in v19 upload -> preserved Wiki durable workflow.

Provider calls have no open SQLite transaction. Each validated stage receipt is
checkpointed in its own source-, authority- and claim-fenced transaction, so a
durable retry resumes only a database-acknowledged prefix. This workflow never
publishes a raw extraction or an incomplete preservation result.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from uuid import UUID, uuid5

from ..db import db_now_ms, transaction
from ..repositories.audit import AuditEventDraft, AuditRepository
from ..repositories.jobs import (
    ClaimFence,
    DurableJobRecord,
    JobCancellationRequestedError,
    JobClaimLostError,
    JobEnqueueDraft,
    JobRepository,
)
from ..repositories.outbox import OutboxEventDraft, OutboxRepository
from ..repositories.users import UserRepository
from ..repositories.wiki_authoring import _json, _sha
from ..repositories.wiki_preservation import (
    PreparedPreservationRun,
    PreparedPreservedWiki,
    PreservationStorageConflict,
    WikiPreservationRepository,
    preserved_snapshot_id,
)
from ..scope import Capability
from ..wiki_authoring import (
    AuthoringError,
    AuthoringModelPolicy,
    AuthoringWorkLimitError,
    AuthoringWorkPolicy,
    ExtractionSource,
)
from ..wiki_preservation import (
    AUTHORING_ONLY_VERSIONS,
    PRESERVATION_VERSION,
    PreservationExecutionPolicy,
    PreservationStopped,
    PreservingWikiAuthor,
    plan_preservation,
)
from ..preparation_reads import PreparationReadError
from ..worker import (
    JobAuthorizationRejectedError,
    JobExpectedStateConflictError,
    PermanentJobExecutionError,
    PreparedJobEffect,
    RegisteredJobHandler,
    RetryableJobExecutionError,
)
from .publication import ProtectedPublicationEvidence


_VERSION = "preservation-upload-v1"
_HANDLER_VERSION = "v1"
_PLAN = "publication_preservation.plan_upload"
_CHUNK = "publication_preservation.author_chunk"
_COMPLETE = "publication_preservation.complete"
_DURABLE_ATTEMPTS = 2
_ACTIVE = ("queued", "retry", "claimed", "running")
_NAMESPACE = UUID("e8afd4d1-9254-4fe1-a805-97c224c4dd3d")
_KNOWN_FAILURES = frozenset({
    "PRESERVATION_INCOMPLETE",
    "PRESERVATION_PROVIDER_NO_CONTENT",
    "CODEX_EXEC_TIMEOUT",
    "CODEX_OUTPUT_TOO_LARGE",
    "CODEX_EXEC_FAILED",
    "CODEX_OUTPUT_INCOMPLETE",
    "MODEL_EXEC_FAILED",
    "MODEL_RESPONSE_TOO_LARGE",
    "INVALID_OUTPUT",
    "WORK_LIMIT",
})


@dataclass(frozen=True, slots=True)
class WikiPreservationUploadPolicy:
    chunk_bytes: int = 8_192
    preservation_version: str = PRESERVATION_VERSION
    model: AuthoringModelPolicy = field(default_factory=AuthoringModelPolicy)
    work: AuthoringWorkPolicy = field(default_factory=AuthoringWorkPolicy)
    execution: PreservationExecutionPolicy = field(default_factory=PreservationExecutionPolicy)

    def __post_init__(self) -> None:
        if (
            type(self.chunk_bytes) is not int
            or not 4 <= self.chunk_bytes <= 8_192
            or self.preservation_version
            not in (PRESERVATION_VERSION, *AUTHORING_ONLY_VERSIONS)
            or not isinstance(self.model, AuthoringModelPolicy)
            or not isinstance(self.work, AuthoringWorkPolicy)
            or not isinstance(self.execution, PreservationExecutionPolicy)
        ):
            raise PreservationStorageConflict("Invalid server-owned preservation upload policy.")
        if len(self.encoded().encode("utf-8")) > 16_384:
            raise PreservationStorageConflict("The frozen preservation policy is too large.")

    def encoded(self) -> str:
        return _json({
            "version": _VERSION,
            "preservation_version": self.preservation_version,
            "durable_attempts": _DURABLE_ATTEMPTS,
            "chunk_bytes": self.chunk_bytes,
            "model_policy": asdict(self.model),
            "work_policy": asdict(self.work),
            "execution_policy": asdict(self.execution),
        })

    @classmethod
    def restore(cls, encoded: str) -> "WikiPreservationUploadPolicy":
        try:
            value = json.loads(encoded)
            model = dict(value["model_policy"])
            for key in ("provider_order", "model_ids"):
                if model.get(key) is not None:
                    model[key] = tuple(model[key])
            if model.get("wire_models") is not None:
                model["wire_models"] = tuple(tuple(pair) for pair in model["wire_models"])
            policy = cls(
                chunk_bytes=value["chunk_bytes"],
                preservation_version=value["preservation_version"],
                model=AuthoringModelPolicy(**model),
                work=AuthoringWorkPolicy(**value["work_policy"]),
                execution=PreservationExecutionPolicy(**value["execution_policy"]),
            )
            if (
                value.get("version") != _VERSION
                or value.get("durable_attempts") != _DURABLE_ATTEMPTS
                or policy.encoded() != encoded
            ):
                raise ValueError
            return policy
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise PreservationStorageConflict(
                "The retained preservation upload policy is invalid."
            ) from exc


class WikiPreservationWorkflow:
    def __init__(
        self,
        factory,
        *,
        author: PreservingWikiAuthor,
        policy: WikiPreservationUploadPolicy = WikiPreservationUploadPolicy(),
        protected_authoring_authorizer=None,
        require_protected_authoring_authorization: bool = False,
    ) -> None:
        if not isinstance(author, PreservingWikiAuthor):
            raise ValueError("Preservation workflow requires its pure author.")
        if not isinstance(policy, WikiPreservationUploadPolicy):
            raise ValueError("Preservation workflow requires one frozen server policy.")
        if protected_authoring_authorizer is not None and not callable(protected_authoring_authorizer):
            raise TypeError("Protected authoring authorizer must be callable or None.")
        if type(require_protected_authoring_authorization) is not bool:
            raise TypeError("Protected authoring authorization policy must be boolean.")
        self._factory = factory
        self._author = author
        self._policy = policy
        self._protected_authoring_authorizer = protected_authoring_authorizer
        self._require_protected_authoring_authorization = require_protected_authoring_authorization
        self._repository = WikiPreservationRepository()
        self._jobs = JobRepository()
        self._kernel = None

    @property
    def database_path(self):
        return self._factory.database_path

    @property
    def kernel(self):
        if self._kernel is None:
            raise RuntimeError("Preservation workflow has no canonical worker kernel.")
        return self._kernel

    @property
    def durable_attempts(self) -> int:
        return _DURABLE_ATTEMPTS

    def bind_kernel(self, kernel) -> None:
        if self._kernel is not None or kernel.database_path != self.database_path:
            raise ValueError("Preservation workflow requires one same-database kernel.")
        for handler in self.registered_handlers():
            kernel.registry.require(handler.key)
        self._kernel = kernel

    def _check_protected_rights(self, *, actor_user_id: str, preview_id: str) -> None:
        """Fail closed before source reads/model calls when strict rights are enabled."""
        authorizer = self._protected_authoring_authorizer
        if authorizer is None and not self._require_protected_authoring_authorization:
            return
        connection = self._factory.open(read_only=True)
        try:
            if connection.execute(
                """SELECT 1 FROM sqlite_schema WHERE type='table'
                     AND name='protected_extraction_provenance'"""
            ).fetchone() is None:
                legacy = connection.execute(
                    """SELECT e.extractor_name FROM publication_preview p
                         JOIN extraction_snapshot e ON e.id=p.extraction_snapshot_id
                        WHERE p.id=? AND p.actor_user_id=?""",
                    (preview_id, actor_user_id),
                ).fetchone()
                if legacy is None or legacy[0] in {
                    "canonical-approved-owner-pdf",
                    "canonical-authorized-protected-snapshot",
                }:
                    raise JobAuthorizationRejectedError(
                        "Protected authoring has no origin catalog."
                    )
                return
            row = connection.execute(
                """SELECT p.artifact_id,p.extraction_snapshot_id,a.original_filename,
                          a.raw_sha256,e.extractor_name,
                          r.original_sha256 AS protected_sha256,r.derivative_sha256,
                          r.source_kind,r.opening_method
                     FROM publication_preview p
                     JOIN source_artifact a ON a.id=p.artifact_id
                     JOIN extraction_snapshot e ON e.id=p.extraction_snapshot_id
                     LEFT JOIN protected_extraction_provenance r
                       ON r.extraction_snapshot_id=e.id AND r.artifact_id=a.id
                    WHERE p.id=? AND p.actor_user_id=?""",
                (preview_id, actor_user_id),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            raise JobExpectedStateConflictError("The protected authoring source changed.")
        if row["protected_sha256"] is None:
            if row["extractor_name"] in {
                "canonical-approved-owner-pdf",
                "canonical-authorized-protected-snapshot",
            }:
                raise JobAuthorizationRejectedError("Protected authoring has no origin record.")
            return
        if (row["protected_sha256"] != row["raw_sha256"]
                or row["original_filename"] is None):
            raise JobAuthorizationRejectedError("Protected authoring origin is inconsistent.")
        if authorizer is None:
            raise JobAuthorizationRejectedError("Current protected-source permission is required.")
        evidence = ProtectedPublicationEvidence(
            actor_user_id=actor_user_id, preview_id=preview_id,
            artifact_id=row["artifact_id"],
            extraction_snapshot_id=row["extraction_snapshot_id"],
            original_filename=row["original_filename"],
            original_sha256=row["raw_sha256"],
            derivative_sha256=row["derivative_sha256"],
            source_kind=row["source_kind"],
            opening_method=row["opening_method"],
        )
        try:
            allowed = authorizer(evidence) is True
        except Exception:
            allowed = False
        if not allowed:
            raise JobAuthorizationRejectedError("Current protected-source permission denies authoring.")

    def _check_protected_rights_for_run(self, *, actor_user_id: str, run_id: str) -> None:
        if (self._protected_authoring_authorizer is None
                and not self._require_protected_authoring_authorization):
            return
        connection = self._factory.open(read_only=True)
        try:
            row = connection.execute(
                """SELECT preview_id FROM publication_preservation_run
                    WHERE id=? AND actor_user_id=?""",
                (run_id, actor_user_id),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            raise JobExpectedStateConflictError("The protected authoring run changed.")
        self._check_protected_rights(actor_user_id=actor_user_id, preview_id=row[0])

    def registered_handlers(self):
        shared = dict(
            handler_version=_HANDLER_VERSION,
            actor_kind="user",
            required_capability=Capability.PUBLISH,
            validate_input=self.validate_input,
            revalidate_expected_state=self.revalidate_expected_state,
            prepare_terminal=self.prepare_terminal,
            converge_terminal=self.converge_terminal,
        )
        return (
            RegisteredJobHandler.build(
                job_type=_PLAN,
                prepare=self.prepare_plan,
                commit_effect=self.commit_plan,
                reconcile_pending=self.reconcile_pending,
                **shared,
            ),
            RegisteredJobHandler.build(
                job_type=_CHUNK,
                prepare=self.prepare_chunk,
                commit_effect=self.commit_chunk,
                **shared,
            ),
            RegisteredJobHandler.build(
                job_type=_COMPLETE,
                prepare=self.prepare_completion,
                commit_effect=self.commit_completion,
                **shared,
            ),
        )

    def register_upload(self, connection, *, preview_id, parent) -> None:
        """Register only inside the new preview writer; replay skips this hook."""
        if not connection.in_transaction or not isinstance(parent, DurableJobRecord):
            raise PreservationStorageConflict(
                "Preservation registration requires the canonical preview writer and parent."
            )
        policy = self._policy.encoded()
        connection.execute(
            """INSERT INTO publication_preservation_request(
                preview_id,parent_job_id,version,policy_json,policy_sha256,created_at)
               VALUES(?,?,?,?,?,?)""",
            (preview_id, parent.id, _VERSION, policy, _sha(policy), db_now_ms(connection)),
        )
        self._event(
            connection,
            parent,
            action="wiki.preservation.requested",
            aggregate_id=preview_id,
            details={"preview_id": preview_id, "policy_sha256": _sha(policy)},
        )

    def _request(self, connection, preview_id):
        row = connection.execute(
            """SELECT r.*,p.actor_user_id,p.artifact_id,p.extraction_snapshot_id,
                p.source_snapshot_sha256,p.status AS preview_status,p.expires_at
               FROM publication_preservation_request r
               JOIN publication_preview p ON p.id=r.preview_id WHERE r.preview_id=?""",
            (preview_id,),
        ).fetchone()
        if (
            row is None
            or row["version"] != _VERSION
            or _sha(row["policy_json"]) != row["policy_sha256"]
        ):
            raise PreservationStorageConflict("The frozen preservation request is unavailable.")
        return row, WikiPreservationUploadPolicy.restore(row["policy_json"])

    def _parent(self, connection, request):
        parent = self.kernel.revalidate_job_authority(connection, request["parent_job_id"])
        if (
            parent.job_type != "publication_preview.extract"
            or parent.handler_version != "v1"
            or parent.state != "succeeded"
            or parent.actor_kind != "user"
            or parent.actor_user_id != request["actor_user_id"]
            or parent.input_ref.get("preview_id") != request["preview_id"]
            or parent.input_ref.get("artifact_id") != request["artifact_id"]
            or request["preview_status"] != "ready"
        ):
            raise PreservationStorageConflict("The preservation extraction parent changed.")
        source_fence = self._repository._source_fence(
            connection,
            preview_id=request["preview_id"],
            actor_user_id=request["actor_user_id"],
            now_ms=db_now_ms(connection),
        )
        return parent, source_fence

    @staticmethod
    def _identity(kind: str, value: object, policy_sha256: str):
        effect = f"preservation-workflow:v1:{kind}:{_sha(_json((value, policy_sha256)))}"
        return str(uuid5(_NAMESPACE, effect)), effect

    @staticmethod
    def validate_input(value, expected) -> None:
        plan = set(value) == {"preview_id"}
        chunk = set(value) == {"run_id", "ordinal", "chunk_id", "policy_version"}
        complete = set(value) == {"run_id", "policy_version"}
        if (
            not (plan or chunk or complete)
            or set(expected) != {
                "schema_version",
                "version",
                "policy_sha256",
                "parent_job_id",
                "plan_sha256",
                "source_fence",
                "durable_attempts",
                "_authorization_fence",
            }
            or expected["schema_version"] != 1
            or expected["version"] != _VERSION
            or expected["durable_attempts"] != _DURABLE_ATTEMPTS
            or (chunk and (type(value["ordinal"]) is not int or value["ordinal"] < 1))
            or (
                (chunk or complete)
                and value["policy_version"]
                not in (PRESERVATION_VERSION, *AUTHORING_ONLY_VERSIONS)
            )
        ):
            raise JobExpectedStateConflictError("Invalid versioned preservation job input.")

    def _expected(self, request, parent, source_fence, *, plan_sha256):
        return {
            "schema_version": 1,
            "version": _VERSION,
            "policy_sha256": request["policy_sha256"],
            "parent_job_id": parent.id,
            "plan_sha256": plan_sha256,
            "source_fence": source_fence,
            "durable_attempts": _DURABLE_ATTEMPTS,
            "_authorization_fence": parent.expected_state.get("_authorization_fence"),
        }

    def revalidate_expected_state(self, connection, job) -> None:
        try:
            self.validate_input(job.input_ref, job.expected_state)
            if job.max_attempts != _DURABLE_ATTEMPTS:
                raise PreservationStorageConflict("The durable attempt policy changed.")
            if job.job_type == _PLAN:
                request, _ = self._request(connection, job.input_ref["preview_id"])
                parent, source_fence = self._parent(connection, request)
                binding = connection.execute(
                    "SELECT durable_job_id FROM publication_preservation_source_plan WHERE preview_id=?",
                    (request["preview_id"],),
                ).fetchone()
                plan_sha = None
                value = {"preview_id": request["preview_id"]}
                if connection.execute(
                    "SELECT 1 FROM publication_preservation_source_result WHERE preview_id=?",
                    (request["preview_id"],),
                ).fetchone():
                    raise PreservationStorageConflict("The preservation source is already terminal.")
            else:
                run = connection.execute(
                    """SELECT r.preview_id,r.actor_user_id,r.plan_sha256,r.policy_version,r.status,
                        q.policy_sha256 FROM publication_preservation_run r
                       JOIN publication_preservation_request q ON q.preview_id=r.preview_id
                       WHERE r.id=?""",
                    (job.input_ref["run_id"],),
                ).fetchone()
                if (
                    run is None
                    or run["actor_user_id"] != job.actor_user_id
                    or run["status"] != "pending"
                    or job.input_ref["policy_version"] != run["policy_version"]
                ):
                    raise PreservationStorageConflict("The pending preservation run changed.")
                request, request_policy = self._request(
                    connection, run["preview_id"]
                )
                if request_policy.preservation_version != run["policy_version"]:
                    raise PreservationStorageConflict(
                        "The frozen preservation policy changed."
                    )
                parent, source_fence = self._parent(connection, request)
                plan_sha = run["plan_sha256"]
                value = dict(job.input_ref)
                table = (
                    "publication_preservation_chunk_work"
                    if job.job_type == _CHUNK
                    else "publication_preservation_completion_work"
                )
                where = "run_id=? AND ordinal=?" if job.job_type == _CHUNK else "run_id=?"
                args = (
                    (job.input_ref["run_id"], job.input_ref["ordinal"])
                    if job.job_type == _CHUNK
                    else (job.input_ref["run_id"],)
                )
                binding = connection.execute(
                    f"SELECT durable_job_id FROM {table} WHERE {where}", args
                ).fetchone()
                if job.job_type == _CHUNK:
                    chunk = connection.execute(
                        """SELECT chunk_id FROM publication_preservation_chunk
                           WHERE run_id=? AND ordinal=?""",
                        args,
                    ).fetchone()
                    if chunk is None or chunk[0] != job.input_ref["chunk_id"]:
                        raise PreservationStorageConflict("The preservation chunk identity changed.")
                    self._repository._chunk_fence(
                        connection,
                        run_id=job.input_ref["run_id"],
                        actor_user_id=job.actor_user_id,
                        ordinal=job.input_ref["ordinal"],
                        now_ms=db_now_ms(connection),
                    )
                else:
                    self._repository._completion_fence(
                        connection,
                        run_id=job.input_ref["run_id"],
                        actor_user_id=job.actor_user_id,
                        now_ms=db_now_ms(connection),
                    )
            expected = self._expected(
                request, parent, source_fence, plan_sha256=plan_sha
            )
            job_id, effect = self._identity(job.job_type, value, request["policy_sha256"])
            if (
                binding is None
                or binding[0] != job.id
                or (job.id, job.effect_key) != (job_id, effect)
                or job.actor_user_id != request["actor_user_id"]
                or job.space_id != parent.space_id
                or _json(job.expected_state) != _json(expected)
            ):
                raise PreservationStorageConflict("The preservation work binding changed.")
        except (AuthoringError, PreservationStorageConflict, KeyError, TypeError, ValueError) as exc:
            raise JobExpectedStateConflictError(
                "Preservation authority, source, or job identity changed."
            ) from exc

    def prepare_plan(self, job) -> PreparedJobEffect:
        fence = ClaimFence(job.id, job.claim_owner, job.attempt_count)
        self.kernel.preflight_preparation(fence)
        self._check_protected_rights(
            actor_user_id=job.actor_user_id,
            preview_id=job.input_ref["preview_id"],
        )
        connection = self._factory.open(read_only=True)
        try:
            request, policy = self._request(connection, job.input_ref["preview_id"])
            row = connection.execute(
                """SELECT b.snapshot_id,b.content_sha256,b.content_md
                   FROM publication_preview p JOIN extraction_snapshot_body b
                     ON b.snapshot_id=p.extraction_snapshot_id AND b.artifact_id=p.artifact_id
                   WHERE p.id=? AND p.actor_user_id=?""",
                (request["preview_id"], job.actor_user_id),
            ).fetchone()
            if row is None:
                raise PreservationStorageConflict("The retained extraction is unavailable.")
            plan = plan_preservation(
                ExtractionSource(*row),
                chunk_bytes=policy.chunk_bytes,
                model_policy=policy.model,
                work_policy=policy.work,
                execution_policy=policy.execution,
                version=policy.preservation_version,
            )
            prepared = self._repository.prepare_run(
                connection,
                preview_id=request["preview_id"],
                actor_user_id=job.actor_user_id,
                plan=plan,
                now_ms=db_now_ms(connection),
                durable_attempts=_DURABLE_ATTEMPTS,
            )
        except (AuthoringError, PreservationStorageConflict) as exc:
            raise PermanentJobExecutionError(
                "The immutable extraction cannot be planned for complete preservation."
            ) from exc
        finally:
            connection.close()
        self.kernel.preflight_preparation(fence)
        result = {
            "schema_version": 1,
            "preview_id": prepared.preview_id,
            "run_id": prepared.run_id,
            "plan_sha256": prepared.plan_sha256,
            "chunk_count": len(prepared.chunks),
        }
        return PreparedJobEffect(result=result, payload=prepared)

    def commit_plan(self, connection, job, prepared) -> None:
        value = prepared.payload
        if not isinstance(value, PreparedPreservationRun):
            raise PermanentJobExecutionError("Invalid prepared preservation plan effect.")
        request, request_policy = self._request(connection, value.preview_id)
        parent, source_fence = self._parent(connection, request)
        expected_result = {
            "schema_version": 1,
            "preview_id": value.preview_id,
            "run_id": value.run_id,
            "plan_sha256": value.plan_sha256,
            "chunk_count": len(value.chunks),
        }
        if (
            value.actor_user_id != job.actor_user_id
            or value.policy_version != request_policy.preservation_version
            or dict(prepared.result) != expected_result
            or value.fence_json != source_fence
        ):
            raise PermanentJobExecutionError("Prepared preservation plan changed.")
        try:
            run_id = self._repository.create_run(
                connection,
                preview_id=value.preview_id,
                actor_user_id=job.actor_user_id,
                prepared=value,
                now_ms=db_now_ms(connection),
            )
            for ordinal, chunk_id, *_ in value.chunks:
                input_ref = {
                    "run_id": run_id,
                    "ordinal": ordinal,
                    "chunk_id": chunk_id,
                    "policy_version": value.policy_version,
                }
                queued = self._enqueue(
                    connection,
                    kind=_CHUNK,
                    value=input_ref,
                    request=request,
                    parent=parent,
                    source_fence=source_fence,
                    plan_sha256=value.plan_sha256,
                )
                connection.execute(
                    """INSERT INTO publication_preservation_chunk_work(
                        run_id,ordinal,durable_job_id) VALUES(?,?,?)""",
                    (run_id, ordinal, queued.job.id),
                )
            connection.execute(
                """INSERT INTO publication_preservation_source_result(
                    preview_id,outcome,run_id,failure_code,parent_state,completed_at)
                   VALUES(?,'planned',?,NULL,'succeeded',?)""",
                (value.preview_id, run_id, db_now_ms(connection)),
            )
        except (AuthoringError, PreservationStorageConflict) as exc:
            raise JobExpectedStateConflictError(
                "The preservation plan can no longer commit."
            ) from exc
        self._event(
            connection,
            job,
            action="wiki.preservation.planned",
            aggregate_id=run_id,
            details={"run_id": run_id, "chunk_count": len(value.chunks)},
        )

    def _enqueue(
        self,
        connection,
        *,
        kind,
        value,
        request,
        parent,
        source_fence,
        plan_sha256,
    ):
        job_id, effect = self._identity(kind, value, request["policy_sha256"])
        expected = self._expected(
            request, parent, source_fence, plan_sha256=plan_sha256
        )
        self.validate_input(value, expected)
        return self._jobs.enqueue(
            connection,
            JobEnqueueDraft(
                job_id=job_id,
                dedup_id=None,
                operation_id=None,
                actor_kind="user",
                actor_user_id=parent.actor_user_id,
                space_id=parent.space_id,
                job_type=kind,
                handler_version=_HANDLER_VERSION,
                effect_key=effect,
                input_ref=value,
                expected_state=expected,
                authorization_snapshot=parent.authorization_snapshot,
                authorization_scope_epoch=parent.authorization_scope_epoch,
                authorization_part_id=parent.authorization_part_id,
                authorization_capabilities=parent.authorization_capabilities,
                max_attempts=_DURABLE_ATTEMPTS,
                cancellable=True,
                available_at_ms=db_now_ms(connection),
            ),
        )

    def _checkpoint_receipt(self, fence, job, receipt, *, attempt_units) -> None:
        self.kernel.preflight_preparation(fence)
        self._check_protected_rights_for_run(
            actor_user_id=job.actor_user_id,
            run_id=job.input_ref["run_id"],
        )
        connection = self._factory.open()
        try:
            prepared = self._repository.prepare_receipt(
                connection,
                run_id=job.input_ref["run_id"],
                actor_user_id=job.actor_user_id,
                ordinal=job.input_ref["ordinal"],
                receipt=receipt,
                attempt_units=attempt_units,
                now_ms=db_now_ms(connection),
            )
            with transaction(connection):
                now = db_now_ms(connection)
                current = self._jobs.assert_effect_fence(connection, fence=fence, now_ms=now)
                self.kernel.revalidate_job_authority(connection, current.id)
                self.revalidate_expected_state(connection, current)
                self._repository.save_receipt(connection, prepared=prepared, now_ms=now)
                self._event(
                    connection,
                    current,
                    action="wiki.preservation.stage_checkpointed",
                    aggregate_id=prepared.run_id,
                    details={
                        "run_id": prepared.run_id,
                        "ordinal": prepared.ordinal,
                        "sequence": prepared.sequence,
                        "stage": prepared.receipt.stage,
                        "response_sha256": prepared.receipt.response_sha256,
                    },
                    suffix=f":{prepared.ordinal}:{prepared.sequence}",
                )
        finally:
            connection.close()

    @staticmethod
    def _guard_error(error):
        current = error
        while current is not None:
            if isinstance(
                current,
                (
                    JobCancellationRequestedError,
                    JobClaimLostError,
                    JobAuthorizationRejectedError,
                    JobExpectedStateConflictError,
                    RetryableJobExecutionError,
                    PreparationReadError,
                ),
            ):
                return current
            current = current.__cause__
        return None

    @staticmethod
    def _failure_code(error: PreservationStopped) -> str:
        cause = error.__cause__
        if isinstance(cause, AuthoringWorkLimitError):
            return "WORK_LIMIT"
        return error.failure_code if error.failure_code in _KNOWN_FAILURES else "INVALID_OUTPUT"

    def _checkpoint_failure(self, fence, job, error, plan) -> None:
        attempt = next(
            (receipt for receipt in reversed(error.receipts) if receipt.validation_state == "attempt_only"),
            None,
        )
        connection = self._factory.open()
        try:
            prepared = self._repository.prepare_failure(
                connection,
                run_id=job.input_ref["run_id"],
                actor_user_id=job.actor_user_id,
                ordinal=job.input_ref["ordinal"],
                failure_code=self._failure_code(error),
                response="" if attempt is None else attempt.response,
                model={} if attempt is None else dict(attempt.model),
                transport={} if attempt is None else json.loads(attempt.transport_json),
                attempt_units=(0 if isinstance(error.__cause__, AuthoringWorkLimitError)
                    or (len(error.receipts) == 4 and attempt is None)
                    else plan.source_plan.model_policy.max_attempts),
                now_ms=db_now_ms(connection),
            )
            with transaction(connection):
                now = db_now_ms(connection)
                current = self._jobs.assert_effect_fence(connection, fence=fence, now_ms=now)
                self.kernel.revalidate_job_authority(connection, current.id)
                self.revalidate_expected_state(connection, current)
                self._repository.save_failure(connection, prepared=prepared, now_ms=now)
        finally:
            connection.close()

    def prepare_chunk(self, job) -> PreparedJobEffect:
        fence = ClaimFence(job.id, job.claim_owner, job.attempt_count)
        return self.kernel.prepare_with_read_guard(fence,
            lambda preflight: self._prepare_chunk(job, preflight))

    def _prepare_chunk(self, job, preflight) -> PreparedJobEffect:
        fence = ClaimFence(job.id, job.claim_owner, job.attempt_count)
        preflight()
        self._check_protected_rights_for_run(
            actor_user_id=job.actor_user_id,
            run_id=job.input_ref["run_id"],
        )
        connection = self._factory.open(read_only=True)
        try:
            plan = self._repository.load_plan(
                connection,
                run_id=job.input_ref["run_id"],
                actor_user_id=job.actor_user_id,
                now_ms=db_now_ms(connection),
            )
            prefix = self._repository.load_prefix(
                connection,
                run_id=job.input_ref["run_id"],
                actor_user_id=job.actor_user_id,
                ordinal=job.input_ref["ordinal"],
                now_ms=db_now_ms(connection),
            )
        finally:
            connection.close()

        def guard():
            preflight()
            self._check_protected_rights_for_run(
                actor_user_id=job.actor_user_id,
                run_id=job.input_ref["run_id"],
            )
        try:
            result = self._author.author_chunk(
                plan,
                job.input_ref["ordinal"],
                receipts=prefix,
                attempt_guard=guard,
                on_receipt=lambda receipt: self._checkpoint_receipt(
                    fence,
                    job,
                    receipt,
                    attempt_units=plan.source_plan.model_policy.max_attempts,
                ),
            )
        except PreservationStopped as exc:
            guard_error = self._guard_error(exc)
            if guard_error is not None:
                raise guard_error
            self._checkpoint_failure(fence, job, exc, plan)
            if plan.version == PRESERVATION_VERSION and len(exc.receipts) == 4 and all(
                receipt.validation_state == "validated" for receipt in exc.receipts
            ):
                raise PermanentJobExecutionError(
                    "Preservation review rejected the bounded repair."
                ) from exc
            raise RetryableJobExecutionError(
                "The preservation attempt stopped before a complete validated chunk."
            ) from exc

        connection = self._factory.open(read_only=True)
        try:
            retained = self._repository.load_prefix(
                connection,
                run_id=job.input_ref["run_id"],
                actor_user_id=job.actor_user_id,
                ordinal=job.input_ref["ordinal"],
                now_ms=db_now_ms(connection),
            )
        finally:
            connection.close()
        if retained != result.receipts:
            raise PermanentJobExecutionError(
                "The completed preservation result lacks an acknowledged receipt prefix."
            )
        preflight()
        return PreparedJobEffect(
            result={
                "schema_version": 1,
                "run_id": job.input_ref["run_id"],
                "ordinal": job.input_ref["ordinal"],
                "chunk_id": result.chunk_id,
                "receipt_count": len(retained),
            }
        )

    def commit_chunk(self, connection, job, prepared) -> None:
        rows = connection.execute(
            """SELECT count(*) FROM publication_preservation_receipt q
               JOIN publication_preservation_receipt_body b ON b.run_id=q.run_id
                 AND b.ordinal=q.ordinal AND b.sequence=q.sequence
               WHERE q.run_id=? AND q.ordinal=?""",
            (job.input_ref["run_id"], job.input_ref["ordinal"]),
        ).fetchone()[0]
        expected = {
            "schema_version": 1,
            "run_id": job.input_ref["run_id"],
            "ordinal": job.input_ref["ordinal"],
            "chunk_id": job.input_ref["chunk_id"],
            "receipt_count": rows,
        }
        accepted_counts = (
            (2, 4)
            if job.input_ref["policy_version"] == PRESERVATION_VERSION
            else (1,)
        )
        if rows not in accepted_counts or dict(prepared.result) != expected or prepared.payload is not None:
            raise PermanentJobExecutionError("Invalid completed preservation chunk effect.")
        self._event(
            connection,
            job,
            action="wiki.preservation.chunk_completed",
            aggregate_id=job.input_ref["run_id"],
            details={
                "run_id": job.input_ref["run_id"],
                "ordinal": job.input_ref["ordinal"],
                "receipt_count": rows,
            },
            suffix=f":{job.input_ref['ordinal']}",
        )

    def prepare_completion(self, job) -> PreparedJobEffect:
        fence = ClaimFence(job.id, job.claim_owner, job.attempt_count)
        self.kernel.preflight_preparation(fence)
        self._check_protected_rights_for_run(
            actor_user_id=job.actor_user_id,
            run_id=job.input_ref["run_id"],
        )
        connection = self._factory.open(read_only=True)
        try:
            prepared = self._repository.prepare_completion(
                connection,
                run_id=job.input_ref["run_id"],
                actor_user_id=job.actor_user_id,
                now_ms=db_now_ms(connection),
            )
        finally:
            connection.close()
        self.kernel.preflight_preparation(fence)
        self._check_protected_rights_for_run(
            actor_user_id=job.actor_user_id,
            run_id=job.input_ref["run_id"],
        )
        snapshot_id = preserved_snapshot_id(job.input_ref["run_id"], prepared.wiki.markdown)
        return PreparedJobEffect(
            result={
                "schema_version": 1,
                "run_id": job.input_ref["run_id"],
                "preserved_snapshot_id": snapshot_id,
                "content_sha256": _sha(prepared.wiki.markdown),
                "manifest_sha256": _sha(prepared.wiki.manifest_json),
            },
            payload=prepared,
        )

    def commit_completion(self, connection, job, prepared) -> None:
        value = prepared.payload
        if not isinstance(value, PreparedPreservedWiki):
            raise PermanentJobExecutionError("Invalid prepared preservation completion.")
        expected = {
            "schema_version": 1,
            "run_id": job.input_ref["run_id"],
            "preserved_snapshot_id": preserved_snapshot_id(
                job.input_ref["run_id"], value.wiki.markdown
            ),
            "content_sha256": _sha(value.wiki.markdown),
            "manifest_sha256": _sha(value.wiki.manifest_json),
        }
        if dict(prepared.result) != expected:
            raise PermanentJobExecutionError("Prepared preservation completion changed.")
        try:
            snapshot_id = self._repository.complete(
                connection,
                run_id=job.input_ref["run_id"],
                actor_user_id=job.actor_user_id,
                prepared=value,
                now_ms=db_now_ms(connection),
            )
        except (AuthoringError, PreservationStorageConflict) as exc:
            raise JobExpectedStateConflictError(
                "The complete preserved Wiki can no longer commit."
            ) from exc
        if snapshot_id != expected["preserved_snapshot_id"]:
            raise PermanentJobExecutionError("Preserved snapshot identity changed.")
        self._event(
            connection,
            job,
            action="wiki.preservation.completed",
            aggregate_id=job.input_ref["run_id"],
            details={
                "run_id": job.input_ref["run_id"],
                "preserved_snapshot_id": snapshot_id,
                "content_sha256": expected["content_sha256"],
            },
        )

    def reconcile_pending(self, connection) -> None:
        """Perform at most one action in each bounded workflow category."""
        now = db_now_ms(connection)
        failed = connection.execute(
            """SELECT r.preview_id FROM publication_preservation_request r
               JOIN publication_preview p ON p.id=r.preview_id
               JOIN durable_job e ON e.id=r.parent_job_id
               LEFT JOIN publication_preservation_source_plan q ON q.preview_id=r.preview_id
               LEFT JOIN durable_job j ON j.id=q.durable_job_id
               WHERE NOT EXISTS(SELECT 1 FROM publication_preservation_source_result x
                 WHERE x.preview_id=r.preview_id)
                 AND (p.status NOT IN ('queued','extracting','ready') OR p.expires_at<=?
                   OR e.state IN ('failed','cancelled','dead') OR e.cancel_requested=1
                   OR j.state IN ('failed','cancelled','dead') OR j.cancel_requested=1)
               ORDER BY r.created_at,r.preview_id LIMIT 1""",
            (now,),
        ).fetchone()
        if failed is not None:
            self._terminal_request(connection, failed[0], reason="source_or_planner_unavailable")

        eligible = connection.execute(
            """SELECT r.preview_id FROM publication_preservation_request r
               JOIN publication_preview p ON p.id=r.preview_id
               JOIN durable_job e ON e.id=r.parent_job_id
               WHERE p.status='ready' AND p.expires_at>? AND e.state='succeeded'
                 AND NOT EXISTS(SELECT 1 FROM publication_preservation_source_plan q
                   WHERE q.preview_id=r.preview_id)
                 AND NOT EXISTS(SELECT 1 FROM publication_preservation_source_result x
                   WHERE x.preview_id=r.preview_id)
               ORDER BY r.created_at,r.preview_id LIMIT 1""",
            (now,),
        ).fetchone()
        if eligible is not None:
            try:
                request, _ = self._request(connection, eligible[0])
                parent, source_fence = self._parent(connection, request)
                input_ref = {"preview_id": request["preview_id"]}
                queued = self._enqueue(
                    connection,
                    kind=_PLAN,
                    value=input_ref,
                    request=request,
                    parent=parent,
                    source_fence=source_fence,
                    plan_sha256=None,
                )
                connection.execute(
                    """INSERT INTO publication_preservation_source_plan(
                        preview_id,durable_job_id) VALUES(?,?)""",
                    (request["preview_id"], queued.job.id),
                )
            except (AuthoringError, PreservationStorageConflict, JobAuthorizationRejectedError):
                self._terminal_request(
                    connection, eligible[0], reason="source_authority_unavailable"
                )

        terminal = connection.execute(
            """SELECT w.durable_job_id FROM publication_preservation_chunk_work w
               JOIN publication_preservation_run r ON r.id=w.run_id
               JOIN durable_job j ON j.id=w.durable_job_id
               WHERE r.status='pending' AND j.state IN ('failed','cancelled','dead')
               UNION ALL
               SELECT w.durable_job_id FROM publication_preservation_completion_work w
               JOIN publication_preservation_run r ON r.id=w.run_id
               JOIN durable_job j ON j.id=w.durable_job_id
               WHERE r.status='pending' AND j.state IN ('failed','cancelled','dead')
               ORDER BY durable_job_id LIMIT 1"""
        ).fetchone()
        if terminal is not None:
            self.converge_terminal(connection, self._jobs.require(connection, terminal[0]))

        ready = connection.execute(
            """SELECT r.id,r.preview_id,r.plan_sha256,r.policy_version FROM publication_preservation_run r
               WHERE r.status='pending'
                 AND NOT EXISTS(SELECT 1 FROM publication_preservation_completion_work w
                   WHERE w.run_id=r.id)
                 AND r.chunk_count=(SELECT count(*) FROM publication_preservation_chunk_work w
                   JOIN durable_job j ON j.id=w.durable_job_id
                   WHERE w.run_id=r.id AND j.state='succeeded')
               ORDER BY r.created_at,r.id LIMIT 1"""
        ).fetchone()
        if ready is not None:
            request, _ = self._request(connection, ready[1])
            parent, source_fence = self._parent(connection, request)
            value = {"run_id": ready[0], "policy_version": ready[3]}
            queued = self._enqueue(
                connection,
                kind=_COMPLETE,
                value=value,
                request=request,
                parent=parent,
                source_fence=source_fence,
                plan_sha256=ready[2],
            )
            connection.execute(
                """INSERT INTO publication_preservation_completion_work(
                    run_id,durable_job_id) VALUES(?,?)""",
                (ready[0], queued.job.id),
            )

    def _terminal_request(self, connection, preview_id, *, reason,
            terminalizing_job_id=None) -> None:
        if connection.execute(
            "SELECT 1 FROM publication_preservation_source_result WHERE preview_id=?",
            (preview_id,),
        ).fetchone():
            return
        request, _ = self._request(connection, preview_id)
        parent = self._jobs.require(connection, request["parent_job_id"])
        parent_state = parent.state
        connection.execute(
            """INSERT INTO publication_preservation_source_result(
                preview_id,outcome,run_id,failure_code,parent_state,completed_at)
               VALUES(?,'failed',NULL,?,?,?)""",
            (preview_id, reason, parent_state, db_now_ms(connection)),
        )
        bound = connection.execute(
            "SELECT durable_job_id FROM publication_preservation_source_plan WHERE preview_id=?",
            (preview_id,),
        ).fetchone()
        # The planner currently being failed must retain its live fence until
        # the kernel records that failure; only other active work is cancelled.
        if bound is not None and bound[0] not in {parent.id, terminalizing_job_id}:
            current = self._jobs.require(connection, bound[0])
            if current.state in _ACTIVE and not current.cancel_requested:
                self._jobs.request_cancel(
                    connection, job_id=current.id, now_ms=db_now_ms(connection)
                )

    def prepare_terminal(self, connection, job, terminal_state) -> None:
        del terminal_state
        self.converge_terminal(connection, job)

    def converge_terminal(self, connection, job) -> None:
        if job.job_type == _PLAN:
            row = connection.execute(
                "SELECT preview_id FROM publication_preservation_source_plan WHERE durable_job_id=?",
                (job.id,),
            ).fetchone()
            if row is not None:
                self._terminal_request(connection, row[0], reason="planner_terminal",
                    terminalizing_job_id=job.id)
            return
        table = (
            "publication_preservation_chunk_work"
            if job.job_type == _CHUNK
            else "publication_preservation_completion_work"
        )
        row = connection.execute(
            f"SELECT run_id FROM {table} WHERE durable_job_id=?", (job.id,)
        ).fetchone()
        if row is None:
            return
        run = connection.execute(
            "SELECT actor_user_id,status FROM publication_preservation_run WHERE id=?",
            (row[0],),
        ).fetchone()
        if run is None or run[1] == "ready":
            return
        if run[1] == "pending":
            failure = connection.execute(
                """SELECT failure_code FROM publication_preservation_failure
                   WHERE run_id=? ORDER BY failed_at DESC,failure_ordinal DESC LIMIT 1""",
                (row[0],),
            ).fetchone()
            code = failure[0] if failure is not None else "PRESERVATION_INCOMPLETE"
            self._repository.fail_run(
                connection,
                run_id=row[0],
                actor_user_id=run[0],
                failure_code=code,
                now_ms=db_now_ms(connection),
            )
        siblings = connection.execute(
            """SELECT durable_job_id FROM publication_preservation_chunk_work WHERE run_id=?
               UNION ALL SELECT durable_job_id FROM publication_preservation_completion_work WHERE run_id=?""",
            (row[0], row[0]),
        ).fetchall()
        for sibling in siblings:
            if sibling[0] == job.id:
                continue
            current = self._jobs.require(connection, sibling[0])
            if current.state in _ACTIVE and not current.cancel_requested:
                self._jobs.request_cancel(
                    connection, job_id=current.id, now_ms=db_now_ms(connection)
                )

    @staticmethod
    def _event(
        connection,
        job,
        *,
        action,
        aggregate_id,
        details,
        suffix="",
    ) -> None:
        now = db_now_ms(connection)
        actor = UserRepository().require(connection, job.actor_user_id)
        event_id = str(uuid5(_NAMESPACE, f"event:{job.id}:{action}{suffix}"))
        payload = {"schema_version": 1, "job_id": job.id, **details}
        AuditRepository().append(
            connection,
            AuditEventDraft(
                event_uuid=event_id,
                occurred_at_ms=now,
                request_id=job.id,
                operation_id=job.operation_id,
                action=action,
                outcome="success",
                actor_kind="user",
                actor_user_id=actor.id,
                actor_display_name=actor.display_name,
                actor_role_snapshot=actor.system_role,
                space_id=job.space_id,
                details=payload,
            ),
        )
        OutboxRepository().append(
            connection,
            OutboxEventDraft(
                logical_key=f"preservation:{job.id}:{action}{suffix}",
                space_id=job.space_id,
                aggregate_type="publication_preservation_run",
                aggregate_id=aggregate_id,
                aggregate_version=1,
                event_type=action,
                payload=payload,
                available_at_ms=now,
            ),
        )


__all__ = ["WikiPreservationUploadPolicy", "WikiPreservationWorkflow"]
