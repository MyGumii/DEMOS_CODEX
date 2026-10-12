"""PDF upload -> durable jobs -> complete retained Wiki snapshot.

Separate PDF identities/tables preserve existing native/display/PPTX workers.
The publication successor owns item creation; this module never publishes.
Model/CAS/parsing remain outside writer transactions. Current authority and
exact claims are revalidated before every persistent effect.
"""
from dataclasses import asdict, dataclass, field
from contextlib import contextmanager
from pathlib import PurePath
import json
import re
import time
from uuid import UUID, uuid5

from ..db import db_now_ms, transaction, TransactionMode
from ..preparation_reads import PreparationReadError
from ..migrations.pdf_authoring_workflow_catalog import PDF_AUTHORING_WORKFLOW_MIGRATION_CATALOG
from ..migrations.v040_pdf_authoring_workflow import VERSION, HANDLER_VERSION, PLAN, BATCH, COMPLETE
from ..migrations.v039_pdf_authoring_storage import (
    POLICY, SOURCE_POLICY, GEOMETRY_VERSION, ENGINE_VERSION, ASSEMBLY, TABLE_POLICY, PROMPT_SHA256,
)
from ..pdf_geometry_authoring import PdfAuthor, PdfAuthoringStopped
from ...document_pdf_geometry import PdfGeometryLimits
from ...document_pdf_process import PdfProcessPolicy
from ..repositories.audit import AuditEventDraft, AuditRepository
from ..repositories.jobs import ClaimFence, JobRepository, JobEnqueueDraft, JobClaimLostError, JobCancellationRequestedError
from ..repositories.pdf_authoring import (
    PdfAuthoringRepository, PreparedPdfRun, PreparedPdfOutcome, PreparedPdfSnapshot,
    prepare_pdf_run, prepare_pdf_outcome, prepare_pdf_snapshot, restore_pdf_run,
    PdfStorageConflict,
)
from ..repositories.outbox import OutboxEventDraft, OutboxRepository
from ..repositories.users import UserRepository
from ..repositories.wiki_preservation import PreservationStorageConflict
from ..scope import Capability
from ..wiki_authoring import AuthoringModelPolicy, AuthoringWorkPolicy, AuthoringError, AuthoringOutputError, AuthoringWorkLimitError, _json, _hash
from ..wiki_preservation import PreservationExecutionPolicy
from ..worker import (
    JobAuthorizationRejectedError, JobExpectedStateConflictError, JobPreparationDeadlineError,
    PermanentJobExecutionError,
    PreparedJobEffect, RegisteredJobHandler, RetryableJobExecutionError,
)
from .pdf_authoring_source import CanonicalPdfAuthoringSourceService
from .publication import PublicationPreviewService

_NS = UUID("751de6e3-5e95-4a70-823b-96d7e8431b42")
_ACTIVE = ("queued", "retry", "claimed", "running")
_GUARD_ERRORS = (JobAuthorizationRejectedError, JobExpectedStateConflictError,
    JobClaimLostError, JobCancellationRequestedError, PreparationReadError)


class _ParserCancellation:
    """Event-compatible fresh claim/authority checks on the parser-owning thread.

    A failed check asks the process runner to kill/reap its child first. The
    original guard error is then propagated to the canonical job kernel.
    The short throttle caches no successful authorization for committing data.
    """
    def __init__(self, check):
        self.check, self.error, self.next_check = check, None, 0.0

    def is_set(self):
        if self.error is not None:
            return True
        if time.monotonic() >= self.next_check:
            try:
                self.check()
            except Exception as exc:
                self.error = exc
                return True
            self.next_check = time.monotonic() + 0.25
        return False


def _require(value, message="PDF workflow identity or state changed."):
    if not value:
        raise JobExpectedStateConflictError(message)


def _require_catalog(factory):
    expected = PDF_AUTHORING_WORKFLOW_MIGRATION_CATALOG.migrations
    if factory.migration_catalog.migrations[:len(expected)] != expected:
        raise ValueError("PDF workflow requires its verified successor catalog.")


@dataclass(frozen=True, slots=True)
class PdfUploadPolicy:
    chunk_bytes: int = 8192
    model: AuthoringModelPolicy = field(default_factory=AuthoringModelPolicy)
    work: AuthoringWorkPolicy = field(default_factory=AuthoringWorkPolicy)
    execution: PreservationExecutionPolicy = field(default_factory=PreservationExecutionPolicy)
    geometry_limits: PdfGeometryLimits = field(default_factory=PdfGeometryLimits)
    process_policy: PdfProcessPolicy = field(default_factory=PdfProcessPolicy)

    def __post_init__(self):
        if (type(self.chunk_bytes) is not int or not 4 <= self.chunk_bytes <= 8192
                or type(self.model) is not AuthoringModelPolicy or type(self.work) is not AuthoringWorkPolicy
                or type(self.execution) is not PreservationExecutionPolicy
                or type(self.geometry_limits) is not PdfGeometryLimits or type(self.process_policy) is not PdfProcessPolicy):
            raise ValueError("Invalid server-owned PDF upload policy.")
        if len(self.encoded().encode()) > 65536:
            raise ValueError("PDF upload policy exceeds its storage budget.")

    @property
    def source_preparation_version(self):
        return SOURCE_POLICY

    @property
    def workflow_version(self):
        return VERSION

    @property
    def handler_version(self):
        return HANDLER_VERSION

    def source_options(self):
        return dict(chunk_bytes=self.chunk_bytes,
            model_policy=self.model, work_policy=self.work, execution_policy=self.execution)

    def source_configuration(self):
        return _json(dict(version=SOURCE_POLICY, policy_version=POLICY, prompt_sha256=PROMPT_SHA256,
            chunk_bytes=self.chunk_bytes, model_policy=asdict(self.model),
            work_policy=asdict(self.work), execution_policy=asdict(self.execution),
            geometry_limits=asdict(self.geometry_limits), process_policy=asdict(self.process_policy)))

    def encoded(self):
        return _json(dict(version=VERSION, durable_attempts=2,
            configuration=json.loads(self.source_configuration())))

    @classmethod
    def restore(cls, encoded):
        try:
            config = json.loads(encoded)["configuration"]
            model = dict(config["model_policy"])
            for key in ("provider_order", "model_ids"):
                if model.get(key) is not None:
                    model[key] = tuple(model[key])
            if model.get("wire_models") is not None:
                model["wire_models"] = tuple(tuple(pair) for pair in model["wire_models"])
            policy = cls(chunk_bytes=config["chunk_bytes"], model=AuthoringModelPolicy(**model),
                work=AuthoringWorkPolicy(**config["work_policy"]), execution=PreservationExecutionPolicy(**config["execution_policy"]),
                geometry_limits=PdfGeometryLimits(**config["geometry_limits"]), process_policy=PdfProcessPolicy(**config["process_policy"]))
            if policy.encoded() != encoded:
                raise ValueError
            return policy
        except (KeyError, TypeError, ValueError) as exc:
            raise JobExpectedStateConflictError("Retained PDF policy is invalid.") from exc


class PdfAuthoringPreviewService(PublicationPreviewService):
    """Explicit PDF producer over the existing legacy-v1 extraction identity."""

    def __init__(self, factory, scope_resolver, stager, *, wiki_upload, **kwargs):
        _require_catalog(factory)
        if type(wiki_upload) is not PdfAuthoringWorkflow or wiki_upload.database_path != factory.database_path:
            raise TypeError("PDF upload requires its same-database workflow.")
        super().__init__(factory, scope_resolver, stager, wiki_upload=wiki_upload, **kwargs)

    def create_upload_preview(self, command):
        if (not isinstance(command.original_filename, str)
                or PurePath(command.original_filename).suffix.lower() != ".pdf"
                or not isinstance(command.media_type, str)
                or command.media_type.split(";", 1)[0].lower().strip()
                    not in {"application/pdf", "application/octet-stream"}):
            raise ValueError("PDF authoring requires a PDF upload.")
        return super().create_upload_preview(command)

    def _upload_request_sha256(self, **fields):
        original = super()._upload_request_sha256(**fields)
        return _hash(_json(dict(legacy_request_sha256=original, version=self._wiki_upload.policy.workflow_version,
            handler_version=self._upload_handler_version, rule_version=self._upload_rule_version,
            policy_sha256=_hash(self._wiki_upload.policy.encoded()))))


class PdfAuthoringWorkflow:
    def __init__(self, factory, scope_resolver, stager, *, python_executable, author, policy=None):
        _require_catalog(factory)
        if type(author) is not PdfAuthor:
            raise TypeError("PDF workflow requires its bounded batch author.")
        self._factory, self.author = factory, author
        self.policy = PdfUploadPolicy() if policy is None else policy
        if type(self.policy) is not PdfUploadPolicy:
            raise TypeError("PDF workflow requires its server-owned policy.")
        self.source = CanonicalPdfAuthoringSourceService(factory, scope_resolver, stager,
            python_executable=python_executable, geometry_limits=self.policy.geometry_limits,
            process_policy=self.policy.process_policy)
        self.repo, self.jobs = PdfAuthoringRepository(self.source), JobRepository()
        self._kernel = None

    @property
    def database_path(self):
        return self._factory.database_path

    @property
    def kernel(self):
        if self._kernel is None:
            raise RuntimeError("Bind the PDF workflow to its canonical kernel first.")
        return self._kernel

    def bind_kernel(self, kernel):
        if self._kernel is not None or kernel.database_path != self.database_path:
            raise ValueError("PDF workflow requires one same-database kernel.")
        for handler in self.registered_handlers():
            registered = kernel.registry.require(handler.key)
            if registered != handler:
                raise ValueError("PDF handler belongs to another workflow instance.")
        self._kernel = kernel

    def registered_handlers(self):
        shared = dict(actor_kind="user", required_capability=Capability.PUBLISH,
            validate_input=self.validate_input, revalidate_expected_state=self.revalidate_expected_state,
            prepare_terminal=self.prepare_terminal, converge_terminal=self.converge_terminal)
        return (
            RegisteredJobHandler.build(job_type=PLAN, handler_version=HANDLER_VERSION,
                prepare=self.prepare_plan, commit_effect=self.commit_plan,
                reconcile_pending=self.reconcile_pending, **shared),
            RegisteredJobHandler.build(job_type=BATCH, handler_version=HANDLER_VERSION,
                prepare=self.prepare_batch, commit_effect=self.commit_batch, **shared),
            RegisteredJobHandler.build(job_type=COMPLETE, handler_version=HANDLER_VERSION,
                prepare=self.prepare_completion, commit_effect=self.commit_completion, **shared),
        )

    def register_upload(self, connection, *, preview_id, parent):
        _require(connection.in_transaction and connection.database_path == self.database_path)
        current = self.jobs.require(connection, parent.id)
        _require(current == parent and parent.handler_version == "v1"
            and parent.job_type == "publication_preview.extract" and parent.state == "queued")
        # Registration is part of the original upload transaction. A queued
        # preview alone does not authorize attaching a new producer later.
        _require(connection.execute("""SELECT 1 FROM operation_dedup
            WHERE id=? AND status='in_progress' AND operation_type='job:publication_preview.extract'
              AND actor_user_id=? AND operation_id=? AND request_sha256=?""",
            (parent.dedup_id, parent.actor_user_id, parent.operation_id, parent.input_sha256)).fetchone()
            is not None, "PDF registration requires the original pending upload operation.")
        row = connection.execute("SELECT expires_at FROM publication_preview WHERE id=?", (preview_id,)).fetchone()
        _require(row is not None)
        encoded = self.policy.encoded()
        created_at = db_now_ms(connection)
        connection.execute("""INSERT INTO pdf_workflow_request
            (preview_id,parent_job_id,policy_sha256,policy_size_bytes,created_at,deadline_at,
             workflow_version,handler_version,configuration_sha256,source_preparation_version,
             geometry_version,engine_version,assembly_version,batch_policy_version,prompt_sha256,table_rendering_version)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (preview_id, parent.id, _hash(encoded), len(encoded.encode()), created_at, row[0],
                VERSION, HANDLER_VERSION, _hash(self.policy.source_configuration()), SOURCE_POLICY,
                GEOMETRY_VERSION, ENGINE_VERSION, ASSEMBLY, POLICY, PROMPT_SHA256, TABLE_POLICY))
        connection.execute("INSERT INTO pdf_workflow_policy_body VALUES(?,?)", (preview_id, encoded))
        self._event(connection, parent, "requested", preview_id, dict(preview_id=preview_id))

    def _request(self, connection, preview_id):
        row = connection.execute("""SELECT q.*,p.actor_user_id,p.artifact_id,b.policy_json
            FROM pdf_workflow_request q JOIN publication_preview p ON p.id=q.preview_id
            LEFT JOIN pdf_workflow_policy_body b ON b.preview_id=q.preview_id WHERE q.preview_id=?""",
            (preview_id,)).fetchone()
        _require(row is not None and type(row["policy_json"]) is str
            and _hash(row["policy_json"]) == row["policy_sha256"]
            and len(row["policy_json"].encode()) == row["policy_size_bytes"])
        policy = PdfUploadPolicy.restore(row["policy_json"])
        version, handler, registration = self._request_identity(connection, preview_id)
        _require((policy.workflow_version, policy.handler_version) == (version, handler))
        if registration is not None:
            _require(registration["configuration_sha256"] == _hash(policy.source_configuration()))
        return {**dict(row), "workflow_version": version, "handler_version": handler}, policy

    def _request_identity(self, connection, preview_id):
        """Permanent version resolution, including cancellation after body purge."""
        request = connection.execute("SELECT * FROM pdf_workflow_request WHERE preview_id=?",
            (preview_id,)).fetchone()
        _require(request is not None and tuple(request[k] for k in (
            "workflow_version", "handler_version", "source_preparation_version", "geometry_version",
            "engine_version", "assembly_version", "batch_policy_version", "prompt_sha256", "table_rendering_version"))
            == (VERSION, HANDLER_VERSION, SOURCE_POLICY, GEOMETRY_VERSION, ENGINE_VERSION, ASSEMBLY, POLICY, PROMPT_SHA256, TABLE_POLICY))
        return VERSION, HANDLER_VERSION, request

    def _parent(self, connection, request):
        _require(request["deadline_at"] > db_now_ms(connection)
            and not connection.execute("SELECT 1 FROM pdf_workflow_stop WHERE preview_id=?",
                (request["preview_id"],)).fetchone(), "PDF upload expired or stopped.")
        parent = self.kernel.revalidate_job_authority(connection, request["parent_job_id"])
        source = self.source._snapshot(connection, request["preview_id"], request["actor_user_id"], now_ms=0)
        _require(parent.id == source.preview["durable_job_id"])
        return parent, source.fence_sha256

    @staticmethod
    def _identity(kind, value, policy_sha, version=VERSION):
        effect = f"{version}:{kind}:{_hash(_json((value, policy_sha)))}"
        return str(uuid5(_NS, effect)), effect

    @staticmethod
    def validate_input(value, expected):
        _require(set(value) in ({"preview_id"}, {"preview_id", "run_id", "ordinal", "batch_id"},
            {"preview_id", "run_id"}) and set(expected) == {"schema_version", "version", "policy_sha256",
                "parent_job_id", "source_fence_sha256", "source_plan_sha256", "_authorization_fence"})
        _require(type(expected["schema_version"]) is int and expected["schema_version"] == 1
            and expected["version"] == VERSION)
        for key in ("preview_id", "run_id"):
            if key in value:
                _require(type(value[key]) is str and str(UUID(value[key])) == value[key])
        if "ordinal" in value:
            _require(type(value["ordinal"]) is int and 1 <= value["ordinal"] <= 4096)
            _require(type(value["batch_id"]) is str and re.fullmatch(r"PB_[0-9a-f]{64}", value["batch_id"]) is not None)
        for key in ("policy_sha256", "source_fence_sha256", "source_plan_sha256"):
            if key == "source_plan_sha256" and "run_id" not in value:
                _require(expected[key] is None)
            else:
                _require(type(expected[key]) is str and re.fullmatch(r"[0-9a-f]{64}", expected[key]) is not None)
        _require(type(expected["parent_job_id"]) is str and str(UUID(expected["parent_job_id"])) == expected["parent_job_id"])

    @staticmethod
    def _expected(request, parent, fence, plan_sha):
        return dict(schema_version=1, version=request["workflow_version"], policy_sha256=request["policy_sha256"],
            parent_job_id=parent.id, source_fence_sha256=fence, source_plan_sha256=plan_sha,
            _authorization_fence=parent.expected_state.get("_authorization_fence"))

    def _enqueue(self, connection, kind, value, request, parent, fence, plan_sha=None):
        job_id, effect = self._identity(kind, value, request["policy_sha256"], request["workflow_version"])
        return self.jobs.enqueue(connection, JobEnqueueDraft(job_id=job_id, dedup_id=None, operation_id=None,
            actor_kind="user", actor_user_id=parent.actor_user_id, space_id=parent.space_id,
            job_type=kind, handler_version=request["handler_version"], effect_key=effect, input_ref=value,
            expected_state=self._expected(request, parent, fence, plan_sha),
            authorization_snapshot=parent.authorization_snapshot, authorization_scope_epoch=parent.authorization_scope_epoch,
            authorization_part_id=parent.authorization_part_id, authorization_capabilities=parent.authorization_capabilities,
            max_attempts=2, cancellable=True, available_at_ms=db_now_ms(connection))).job

    def revalidate_expected_state(self, connection, job):
        try:
            self.validate_input(job.input_ref, job.expected_state)
            request, policy = self._request(connection, job.input_ref["preview_id"])
            parent, fence = self._parent(connection, request)
            value, plan_sha = dict(job.input_ref), None
            if job.job_type == PLAN:
                binding = connection.execute("SELECT job_id FROM pdf_workflow_plan WHERE preview_id=?",
                    (request["preview_id"],)).fetchone()
                _require(set(value) == {"preview_id"} and not connection.execute(
                    "SELECT 1 FROM pdf_workflow_planned WHERE preview_id=?", (request["preview_id"],)).fetchone())
            else:
                run = connection.execute("""SELECT r.*,s.status FROM pdf_authoring_run r
                    JOIN pdf_authoring_state s ON s.run_id=r.id WHERE r.id=?""", (value["run_id"],)).fetchone()
                _require(run is not None and run["status"] == "pending"
                    and run["preview_id"] == request["preview_id"] and run["source_fence_sha256"] == fence)
                metadata = json.loads(run["metadata_json"])
                _require(metadata["source_preparation_version"] == policy.source_preparation_version
                    and metadata["configuration_sha256"] == _hash(policy.source_configuration())
                    and metadata["policy_version"] == POLICY)
                _require(connection.execute("""SELECT 1 FROM pdf_workflow_planned p
                    JOIN pdf_workflow_plan w ON w.preview_id=p.preview_id JOIN durable_job j ON j.id=w.job_id
                    WHERE p.run_id=? AND j.state='succeeded'""", (run["id"],)).fetchone() is not None)
                plan_sha = run["source_plan_sha256"]
                if job.job_type == BATCH:
                    _require(set(value) == {"preview_id", "run_id", "ordinal", "batch_id"})
                    binding = connection.execute("""SELECT w.job_id,b.batch_id FROM pdf_workflow_batch w
                        JOIN pdf_authoring_batch b ON b.run_id=w.run_id AND b.ordinal=w.ordinal
                        WHERE w.run_id=? AND w.ordinal=?""", (run["id"], value["ordinal"])).fetchone()
                    _require(binding is not None and binding[1] == value["batch_id"])
                else:
                    _require(job.job_type == COMPLETE and set(value) == {"preview_id", "run_id"})
                    binding = connection.execute("SELECT job_id FROM pdf_workflow_completion WHERE run_id=?",
                        (run["id"],)).fetchone()
                    _require(self._completed_batches(connection, run["id"]) == run["batch_count"])
            _require(job.handler_version == request["handler_version"] and job.max_attempts == 2 and binding is not None
                and binding[0] == job.id and job.actor_user_id == parent.actor_user_id and job.space_id == parent.space_id
                and (job.id, job.effect_key) == self._identity(job.job_type, value, request["policy_sha256"], request["workflow_version"])
                and _json(dict(job.expected_state)) == _json(self._expected(request, parent, fence, plan_sha)))
        except (KeyError, TypeError, ValueError, AuthoringError, PreservationStorageConflict, PdfStorageConflict) as exc:
            raise JobExpectedStateConflictError("PDF worker source, policy or identity changed.") from exc

    @staticmethod
    def _fence(job):
        return ClaimFence(job.id, job.claim_owner, job.attempt_count)

    @contextmanager
    def _parser_guard(self, job):
        fence = self._fence(job)
        self.kernel.preflight_preparation(fence)
        policy = self.source._process_policy
        self.kernel.renew_claim(fence,
            lease_ms=int((policy.timeout_seconds + policy.cleanup_seconds + 30) * 1000))
        cancellation = _ParserCancellation(lambda: self.kernel.preflight_preparation(fence))
        try:
            yield cancellation
        except BaseException as exc:
            if cancellation.error is not None:
                raise cancellation.error from exc
            raise
        if cancellation.error is not None:
            raise cancellation.error
        self.kernel.preflight_preparation(fence)

    def _restore(self, job):
        connection = self._factory.open(read_only=True)
        try:
            with transaction(connection, TransactionMode.DEFERRED):
                _, policy = self._request(connection, job.input_ref["preview_id"])
                _require(job.handler_version == policy.handler_version
                    and job.expected_state["version"] == policy.workflow_version)
                config = self.repo.load_configuration(connection, preview_id=job.input_ref["preview_id"],
                    actor_user_id=job.actor_user_id)
                _require(config.configuration_json == policy.source_configuration())
        finally:
            connection.close()
        run = None
        try:
            with self._parser_guard(job) as cancellation:
                run = restore_pdf_run(self.source, config, cancel_event=cancellation)
            _require(run.run_id == job.input_ref["run_id"], "Restored PDF run differs from its worker.")
            return run
        except BaseException:
            if run is not None:
                run.close()
            raise

    def prepare_plan(self, job):
        fence = self._fence(job)
        self.kernel.preflight_preparation(fence)
        connection = self._factory.open(read_only=True)
        try:
            with transaction(connection, TransactionMode.DEFERRED):
                _, policy = self._request(connection, job.input_ref["preview_id"])
        finally:
            connection.close()
        source = None
        try:
            _require(asdict(policy.geometry_limits) == asdict(self.source._limits)
                and asdict(policy.process_policy) == asdict(self.source._process_policy),
                "Retained PDF parser policy differs from this server.")
            with self._parser_guard(job) as cancellation:
                source = self.source.prepare(preview_id=job.input_ref["preview_id"], actor_user_id=job.actor_user_id,
                    **policy.source_options(), cancel_event=cancellation)
            run = prepare_pdf_run(source, max_executions=2)
            self.kernel.preflight_preparation(fence)
            return PreparedJobEffect(result=dict(schema_version=1, run_id=run.run_id, batch_count=len(run.batches)),
                payload=run, cleanup=run.close)
        except BaseException as exc:
            try:
                if isinstance(exc, AuthoringWorkLimitError):
                    self._record_work_limit(job)
            finally:
                if source is not None:
                    source.close()
            raise

    def commit_plan(self, connection, job, prepared):
        run = prepared.payload
        _require(type(run) is PreparedPdfRun and run.source.preview_id == job.input_ref["preview_id"])
        request, policy = self._request(connection, run.source.preview_id)
        parent, fence = self._parent(connection, request)
        _require(run.source.configuration_json == policy.source_configuration()
            and _json(dict(prepared.result)) == _json(dict(schema_version=1, run_id=run.run_id, batch_count=len(run.batches))))
        self.repo.put_run(connection, run)
        for ordinal, batch_id, *_ in run.batches:
            value = dict(preview_id=request["preview_id"], run_id=run.run_id, ordinal=ordinal, batch_id=batch_id)
            child = self._enqueue(connection, BATCH, value, request, parent, fence, run.source.plan.plan_sha256)
            connection.execute("INSERT INTO pdf_workflow_batch VALUES(?,?,?)", (run.run_id, ordinal, child.id))
        connection.execute("INSERT INTO pdf_workflow_planned VALUES(?,?)", (request["preview_id"], run.run_id))
        self._event(connection, job, "planned", run.run_id, dict(run_id=run.run_id, batch_count=len(run.batches)))

    def _current_effect(self, connection, job):
        current = self.jobs.assert_effect_fence(connection, fence=self._fence(job), now_ms=db_now_ms(connection))
        self.kernel.revalidate_job_authority(connection, current.id)
        self.revalidate_expected_state(connection, current)
        return current

    def _reserve(self, job, run):
        connection = self._factory.open()
        try:
            with transaction(connection):
                self._current_effect(connection, job)
                # A repeated claim invocation cannot use an existing reservation
                # as permission for another remote execution, even after lost ACK.
                prior = connection.execute("SELECT 1 FROM pdf_workflow_claim WHERE job_id=? AND claim_attempt=?",
                    (job.id, job.attempt_count)).fetchone()
                if prior is not None:
                    raise PermanentJobExecutionError("PDF execution was already reserved by this claim.")
                operation = str(uuid5(_NS, f"execution:{job.id}:{job.attempt_count}"))
                attempt = self.repo.start_attempt(connection, run, ordinal=job.input_ref["ordinal"], operation_id=operation)
                connection.execute("INSERT INTO pdf_workflow_claim VALUES(?,?,?,?)",
                    (attempt.id, job.id, job.claim_owner, job.attempt_count))
                self._event(connection, job, "execution_reserved", run.run_id,
                    dict(run_id=run.run_id, attempt_id=attempt.id, attempt_units=attempt.attempt_units), suffix=attempt.id)
                return attempt
        finally:
            connection.close()

    @staticmethod
    def _cause(error, kinds):
        seen = set()
        while error is not None and id(error) not in seen:
            seen.add(id(error))
            if isinstance(error, kinds):
                return error
            error = error.__cause__ or error.__context__
        return None

    def prepare_batch(self, job):
        return self.kernel.prepare_with_read_guard(self._fence(job),
            lambda preflight: self._prepare_batch(job, preflight))

    def _prepare_batch(self, job, preflight):
        fence = self._fence(job)
        preflight()
        run = self._restore(job)
        try:
            timeout = run.source.plan.execution_policy.timeout_s
            # Lease lasts through the bounded call plus commit margin. This is
            # an explicit renewal, unlike preflight, and cannot revive a lost claim.
            self.kernel.renew_claim(fence, lease_ms=(timeout + 30) * 1000)
            attempt = self._reserve(job, run)

            def guard():
                try:
                    preflight(deadline_at_db_ms=attempt.started_at + timeout * 1000)
                except JobPreparationDeadlineError as exc:
                    raise TimeoutError("PDF execution deadline reached.") from exc

            try:
                receipt = self.author.author_batch(run.source.plan, attempt.ordinal, attempt_guard=guard)
                outcome = prepare_pdf_outcome(run, attempt, receipt=receipt)
                guard()
            except Exception as exc:
                guard_error = self._cause(exc, _GUARD_ERRORS)
                if guard_error is not None:
                    raise guard_error
                code = "TIMEOUT" if self._cause(exc, (TimeoutError,)) else (
                    "INVALID_OUTPUT" if self._cause(exc, (AuthoringOutputError,)) else "MODEL_FAILURE")
                retained = exc.receipt if isinstance(exc, PdfAuthoringStopped) else None
                failure = prepare_pdf_outcome(run, attempt, receipt=retained, status="failed", failure_code=code)
                connection = self._factory.open()
                try:
                    with transaction(connection):
                        self._current_effect(connection, job)
                        self.repo.put_outcome(connection, run, failure)
                        self._event(connection, job, "execution_failed", run.run_id,
                            dict(run_id=run.run_id, attempt_id=attempt.id, failure_code=code), suffix=attempt.id)
                finally:
                    connection.close()
                raise RetryableJobExecutionError("PDF batch execution failed within its retry budget.") from exc
            result = dict(schema_version=1, run_id=run.run_id, ordinal=attempt.ordinal, attempt_id=attempt.id)
            return PreparedJobEffect(result=result, payload=(run, outcome), cleanup=run.close)
        except BaseException as exc:
            try:
                if isinstance(exc, AuthoringWorkLimitError):
                    self._record_work_limit(job)
            finally:
                run.close()
            raise

    def commit_batch(self, connection, job, prepared):
        _require(type(prepared.payload) is tuple and len(prepared.payload) == 2)
        run, outcome = prepared.payload
        _require(type(run) is PreparedPdfRun and type(outcome) is PreparedPdfOutcome)
        expected = dict(schema_version=1, run_id=job.input_ref["run_id"], ordinal=job.input_ref["ordinal"],
            attempt_id=outcome.attempt.id)
        binding = connection.execute("SELECT job_id,claim_owner,claim_attempt FROM pdf_workflow_claim WHERE attempt_id=?",
            (outcome.attempt.id,)).fetchone()
        _require(run.run_id == expected["run_id"] and outcome.attempt.ordinal == expected["ordinal"]
            and outcome.metadata[3] == "validated" and binding is not None
            and tuple(binding) == (job.id, job.claim_owner, job.attempt_count)
            and _json(dict(prepared.result)) == _json(expected))
        self.repo.put_outcome(connection, run, outcome)
        self._event(connection, job, "batch_completed", run.run_id, expected)

    def prepare_completion(self, job):
        fence = self._fence(job)
        self.kernel.preflight_preparation(fence)
        run = self._restore(job)
        try:
            connection = self._factory.open(read_only=True)
            try:
                with transaction(connection, TransactionMode.DEFERRED):
                    receipts = self.repo.load_receipts(connection, run)
            finally:
                connection.close()
            snapshot = prepare_pdf_snapshot(run, receipts)
            self.kernel.preflight_preparation(fence)
            return PreparedJobEffect(result=dict(schema_version=1, run_id=run.run_id,
                snapshot_id=snapshot.snapshot_id, content_sha256=_hash(snapshot.markdown),
                manifest_sha256=_hash(snapshot.manifest_json)), payload=(run, snapshot), cleanup=run.close)
        except BaseException as exc:
            try:
                if isinstance(exc, AuthoringWorkLimitError):
                    self._record_work_limit(job)
            finally:
                run.close()
            raise

    def _record_work_limit(self, job):
        connection = self._factory.open()
        try:
            with transaction(connection):
                self._current_effect(connection, job)
                connection.execute("INSERT INTO pdf_workflow_rejection VALUES(?,?,'WORK_LIMIT',?)",
                    (job.id, job.attempt_count, db_now_ms(connection)))
                self._event(connection, job, "work_rejected", job.input_ref["preview_id"],
                    dict(failure_code="WORK_LIMIT"), suffix=str(job.attempt_count))
        finally:
            connection.close()

    def commit_completion(self, connection, job, prepared):
        _require(type(prepared.payload) is tuple and len(prepared.payload) == 2)
        run, snapshot = prepared.payload
        _require(type(run) is PreparedPdfRun and type(snapshot) is PreparedPdfSnapshot)
        expected = dict(schema_version=1, run_id=job.input_ref["run_id"], snapshot_id=snapshot.snapshot_id,
            content_sha256=_hash(snapshot.markdown), manifest_sha256=_hash(snapshot.manifest_json))
        _require(run.run_id == expected["run_id"] and _json(dict(prepared.result)) == _json(expected))
        self.repo.put_snapshot(connection, run, snapshot)
        self._event(connection, job, "completed", run.run_id, expected)

    @staticmethod
    def _completed_batches(connection, run_id):
        return connection.execute("""SELECT count(*) FROM pdf_workflow_batch w
            JOIN durable_job j ON j.id=w.job_id JOIN pdf_authoring_outcome o
              ON o.run_id=w.run_id AND o.ordinal=w.ordinal AND o.status='validated'
            WHERE w.run_id=? AND j.state='succeeded'""", (run_id,)).fetchone()[0]

    def _abandon(self, connection, attempt_id, code):
        """Metadata-only cleanup; SQL requires expired claim or exact run stop.

No source/config/response read and no authorization to create a new effect.
Open attempts cannot be silently forgotten or reused as free executions.
"""
        row = connection.execute("SELECT * FROM pdf_authoring_attempt WHERE id=?", (attempt_id,)).fetchone()
        _require(row is not None)
        if connection.execute("SELECT 1 FROM pdf_authoring_outcome WHERE attempt_id=?", (attempt_id,)).fetchone():
            return
        connection.execute("""INSERT INTO pdf_authoring_outcome
            (attempt_id,run_id,ordinal,status,failure_code,response_sha256,response_size_bytes,
             model_sha256,model_size_bytes,transport_sha256,transport_size_bytes,completed_at)
            VALUES(?,?,?,'abandoned',?,?,?,?,?,?,?,?)""",
            (attempt_id, row["run_id"], row["ordinal"], code, _hash(""), 0,
                _hash("{}"), 2, _hash("{}"), 2, db_now_ms(connection)))
        connection.execute("INSERT INTO pdf_authoring_outcome_body VALUES(?,'','{}','{}')", (attempt_id,))
        bound = connection.execute("SELECT job_id FROM pdf_workflow_claim WHERE attempt_id=?", (attempt_id,)).fetchone()
        self._event(connection, self.jobs.require(connection, bound[0]), "execution_abandoned", row["run_id"],
            dict(run_id=row["run_id"], attempt_id=attempt_id, failure_code=code), suffix=attempt_id)

    def _stop(self, connection, preview_id, job, code):
        """Handler-owned convergence only. It can terminate, never publish/read.

Exact immutable job-to-preview binding is verified by SQL. Kernel invokes this
inside the same transaction as its terminal CAS; a stale claim rolls it back.
"""
        state = connection.execute("""SELECT r.id,s.status FROM pdf_authoring_run r
            JOIN pdf_authoring_state s ON s.run_id=r.id WHERE r.preview_id=?""", (preview_id,)).fetchone()
        if state is not None and state["status"] == "ready":
            return
        stopped = connection.execute("SELECT failure_code FROM pdf_workflow_stop WHERE preview_id=?", (preview_id,)).fetchone()
        if stopped is None:
            connection.execute("INSERT INTO pdf_workflow_stop VALUES(?,?,?,?)", (preview_id, job.id, code, db_now_ms(connection)))
            self._event(connection, job, "stopped", preview_id, dict(preview_id=preview_id, failure_code=code))
        else:
            code = stopped[0]
        if state is not None and state["status"] == "pending":
            rows = connection.execute("""SELECT a.id FROM pdf_authoring_attempt a
                LEFT JOIN pdf_authoring_outcome o ON o.attempt_id=a.id
                WHERE a.run_id=? AND o.attempt_id IS NULL ORDER BY a.ordinal,a.sequence""", (state[0],)).fetchall()
            for row in rows:
                self._abandon(connection, row[0], code)
            connection.execute("""UPDATE pdf_authoring_state SET status='failed',failure_code=?,completed_at=?,
                row_version=row_version+1 WHERE run_id=? AND status='pending'""", (code, db_now_ms(connection), state[0]))
        siblings = connection.execute("""SELECT job_id FROM pdf_workflow_plan WHERE preview_id=?
            UNION ALL SELECT w.job_id FROM pdf_workflow_batch w JOIN pdf_authoring_run r ON r.id=w.run_id WHERE r.preview_id=?
            UNION ALL SELECT w.job_id FROM pdf_workflow_completion w JOIN pdf_authoring_run r ON r.id=w.run_id WHERE r.preview_id=?""",
            (preview_id, preview_id, preview_id)).fetchall()
        for row in siblings:
            current = self.jobs.require(connection, row[0])
            if current.id != job.id and current.state in _ACTIVE and not current.cancel_requested:
                self.jobs.request_cancel(connection, job_id=current.id, now_ms=db_now_ms(connection))
                self._event(connection, current, "sibling_cancel_requested", preview_id,
                    dict(preview_id=preview_id, stopped_by_job_id=job.id, failure_code=code))

    def prepare_terminal(self, connection, job, terminal_state):
        version, handler, _ = self._request_identity(connection, job.input_ref["preview_id"])
        _require(job.job_type in (PLAN, BATCH, COMPLETE) and job.handler_version == handler
            and job.expected_state.get("version") == version)
        code = "SOURCE_UNAVAILABLE"
        rejection = connection.execute("SELECT failure_code FROM pdf_workflow_rejection WHERE job_id=? AND claim_attempt=?",
            (job.id, job.attempt_count)).fetchone()
        failure = connection.execute("""SELECT o.failure_code FROM pdf_workflow_claim c
            JOIN pdf_authoring_outcome o ON o.attempt_id=c.attempt_id
            WHERE c.job_id=? AND c.claim_attempt=? AND o.status<>'validated'""", (job.id, job.attempt_count)).fetchone()
        if terminal_state == "cancelled":
            code = "CANCELLED"
        elif rejection is not None:
            code = rejection[0]
        elif failure is not None:
            code = failure[0]
        elif job.claim_expires_at_ms is not None and job.claim_expires_at_ms <= db_now_ms(connection):
            code = "LEASE_LOST"
        else:
            try:
                self.kernel.revalidate_job_authority(connection, job.id)
            except JobAuthorizationRejectedError:
                code = "AUTHORITY_UNAVAILABLE"
        self._stop(connection, job.input_ref["preview_id"], job, code)

    def converge_terminal(self, connection, job):
        if job.state in ("failed", "cancelled", "dead"):
            self.prepare_terminal(connection, job, job.state)

    def reconcile_pending(self, connection):
        # Match the kernel's recover-all scope exactly: a smaller LIMIT here
        # would leave some expired attempts open after their jobs enter retry.
        expired = connection.execute("""SELECT a.id FROM pdf_workflow_claim c
            JOIN pdf_authoring_attempt a ON a.id=c.attempt_id JOIN durable_job j ON j.id=c.job_id
            LEFT JOIN pdf_authoring_outcome o ON o.attempt_id=a.id
            WHERE o.attempt_id IS NULL AND j.state IN ('claimed','running') AND j.claim_expires_at<=?
            ORDER BY j.claim_expires_at,j.id""", (db_now_ms(connection),)).fetchall()
        for row in expired:
            self._abandon(connection, row[0], "LEASE_LOST")

        cursor = connection.execute("SELECT last_preview_id FROM pdf_workflow_scan WHERE singleton=1").fetchone()[0]
        # A persistent circular cursor prevents the first 16 long-running jobs
        # from starving every later upload. It carries no access authority.
        pending = connection.execute("""SELECT q.preview_id,q.parent_job_id FROM pdf_workflow_request q
            LEFT JOIN pdf_authoring_run r ON r.preview_id=q.preview_id
            LEFT JOIN pdf_authoring_state s ON s.run_id=r.id
            WHERE (s.status IS NULL OR s.status='pending')
              AND NOT EXISTS(SELECT 1 FROM pdf_workflow_stop WHERE preview_id=q.preview_id)
            ORDER BY CASE WHEN q.preview_id>? THEN 0 ELSE 1 END,q.preview_id LIMIT 16""", (cursor,)).fetchall()
        if pending:
            connection.execute("UPDATE pdf_workflow_scan SET last_preview_id=? WHERE singleton=1", (pending[-1][0],))
        for row in pending:
            parent = self.jobs.require(connection, row["parent_job_id"])
            preview = connection.execute("SELECT status,expires_at FROM publication_preview WHERE id=?", (row[0],)).fetchone()
            if parent.state in ("failed", "cancelled", "dead") or parent.cancel_requested or preview[0] not in ("queued", "extracting", "ready") or preview[1] <= db_now_ms(connection):
                self._stop(connection, row[0], parent,
                    "CANCELLED" if parent.cancel_requested or parent.state == "cancelled" else "SOURCE_UNAVAILABLE")
                continue
            try:
                self.kernel.revalidate_job_authority(connection, parent.id)
            except JobAuthorizationRejectedError:
                self._stop(connection, row[0], parent, "AUTHORITY_UNAVAILABLE")
                continue
            terminal = connection.execute("""SELECT job_id FROM pdf_workflow_plan w JOIN durable_job j ON j.id=w.job_id
                WHERE w.preview_id=? AND (j.state IN ('failed','cancelled','dead') OR j.cancel_requested=1)
                UNION ALL SELECT w.job_id FROM pdf_workflow_batch w JOIN pdf_authoring_run r ON r.id=w.run_id
                JOIN durable_job j ON j.id=w.job_id WHERE r.preview_id=? AND (j.state IN ('failed','cancelled','dead') OR j.cancel_requested=1)
                UNION ALL SELECT w.job_id FROM pdf_workflow_completion w JOIN pdf_authoring_run r ON r.id=w.run_id
                JOIN durable_job j ON j.id=w.job_id WHERE r.preview_id=? AND (j.state IN ('failed','cancelled','dead') OR j.cancel_requested=1)
                LIMIT 1""", (row[0], row[0], row[0])).fetchone()
            if terminal is not None:
                current = self.jobs.require(connection, terminal[0])
                self.prepare_terminal(connection, current, "cancelled" if current.cancel_requested else current.state)
                continue
            if parent.state != "succeeded":
                continue
            try:
                request, _ = self._request(connection, row[0])
                parent, fence = self._parent(connection, request)
            except (JobAuthorizationRejectedError, JobExpectedStateConflictError, ValueError, AuthoringError, PreservationStorageConflict, PdfStorageConflict):
                self._stop(connection, row[0], parent, "SOURCE_UNAVAILABLE")
                continue
            if connection.execute("SELECT 1 FROM pdf_workflow_plan WHERE preview_id=?", (row[0],)).fetchone() is None:
                queued = self._enqueue(connection, PLAN, dict(preview_id=row[0]), request, parent, fence)
                connection.execute("INSERT INTO pdf_workflow_plan VALUES(?,?,?)", (row[0], queued.id, fence))
                self._event(connection, queued, "plan_queued", row[0], dict(preview_id=row[0]))
                continue
            run = connection.execute("SELECT * FROM pdf_authoring_run WHERE preview_id=?", (row[0],)).fetchone()
            if run is not None and not connection.execute("SELECT 1 FROM pdf_workflow_completion WHERE run_id=?", (run["id"],)).fetchone():
                if self._completed_batches(connection, run["id"]) == run["batch_count"]:
                    value = dict(preview_id=row[0], run_id=run["id"])
                    queued = self._enqueue(connection, COMPLETE, value, request, parent, fence, run["source_plan_sha256"])
                    connection.execute("INSERT INTO pdf_workflow_completion VALUES(?,?)", (run["id"], queued.id))
                    self._event(connection, queued, "completion_queued", run["id"], dict(run_id=run["id"]))

    def _event(self, connection, job, action, aggregate_id, details, *, suffix=""):
        request = connection.execute("SELECT preview_id FROM pdf_workflow_request WHERE parent_job_id=?",
            (job.id,)).fetchone()
        preview_id = request[0] if request is not None else job.input_ref["preview_id"]
        version, _, _ = self._request_identity(connection, preview_id)
        actor = UserRepository().require(connection, job.actor_user_id)
        name, now = "wiki.pdf." + action, db_now_ms(connection)
        key = f"{version}:{job.id}:{action}:{suffix}"
        payload = {**details, "schema_version": 1, "job_id": job.id}
        AuditRepository().append(connection, AuditEventDraft(event_uuid=str(uuid5(_NS, key)), occurred_at_ms=now,
            request_id=job.id, operation_id=job.operation_id, action=name, outcome="success",
            actor_kind="user", actor_user_id=actor.id, actor_display_name=actor.display_name,
            actor_role_snapshot=actor.system_role, space_id=job.space_id, details=payload))
        OutboxRepository().append(connection, OutboxEventDraft(logical_key=key, space_id=job.space_id,
            aggregate_type="pdf_authoring_run", aggregate_id=aggregate_id, aggregate_version=1,
            event_type=name, payload=payload, available_at_ms=now))
