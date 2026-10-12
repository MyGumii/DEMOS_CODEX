"""Durable v56 Office Wiki draft writer with per-task retained model receipts.

No model call occurs inside a writer transaction. A completed native batch or
visual page is appended before the next task and replay-validated after a
process restart. The final draft snapshot and durable-job success commit in
one transaction. Publication is a separate opt-in boundary.
"""

from __future__ import annotations

import json
from uuid import UUID

from ..db import TransactionMode, db_now_ms, transaction
from ..migrations.office_hybrid_wiki_catalog import OFFICE_HYBRID_WIKI_MIGRATION_CATALOG
from ..migrations.v056_office_hybrid_wiki import VERSION, WRITE
from ..native_wiki_authoring import NativeAuthoringStopped
from ..native_wiki_batches import (
    NativeBatchReceipt, NativeWikiBatchAuthor, replay_native_batch,
)
from ..office_hybrid_visual_authoring import (
    OfficeHybridVisualAuthor, OfficeVisualObservationReceipt,
    assemble_office_hybrid_batches, replay_office_visual_observation,
)
from ..repositories.jobs import ClaimFence, JobClaimLostError, JobRepository
from ..scope import Capability
from ..wiki_authoring import AuthoredWiki, _hash, _json
from ..worker import (
    JobAuthorizationRejectedError, JobCancellationRequestedError,
    JobExpectedStateConflictError, PermanentJobExecutionError,
    PreparedJobEffect, RegisteredJobHandler, RetryableJobExecutionError,
)
from .office_hybrid_authoring_source import (
    CanonicalOfficeHybridAuthoringSourceService,
    OfficeHybridAuthoringSourceConflict,
)
from .office_hybrid_wiki_start import _native_plan_sha256


class OfficeHybridWikiWriteHandler:
    def __init__(self, factory, *, source: CanonicalOfficeHybridAuthoringSourceService,
            native_author: NativeWikiBatchAuthor,
            visual_author: OfficeHybridVisualAuthor):
        expected = OFFICE_HYBRID_WIKI_MIGRATION_CATALOG.migrations
        if factory.migration_catalog.migrations[:len(expected)] != expected:
            raise ValueError("Office hybrid writer requires its verified v56 catalog.")
        if (type(source) is not CanonicalOfficeHybridAuthoringSourceService
                or source.factory.database_path != factory.database_path
                or type(native_author) is not NativeWikiBatchAuthor
                or type(visual_author) is not OfficeHybridVisualAuthor):
            raise TypeError("Office hybrid writer requires exact same-database authors.")
        self.factory, self.source = factory, source
        self.native_author, self.visual_author = native_author, visual_author
        self.jobs, self.kernel = JobRepository(), None

    def registered_handler(self):
        return RegisteredJobHandler.build(job_type=WRITE, handler_version="v1",
            actor_kind="user", required_capability=Capability.PUBLISH,
            validate_input=self.validate_input, prepare=self.prepare,
            revalidate_expected_state=self.revalidate_expected_state,
            commit_effect=self.commit_effect)

    def bind_kernel(self, kernel):
        if (self.kernel is not None or kernel.database_path != self.factory.database_path
                or kernel.registry.require(self.registered_handler().key)
                    != self.registered_handler()):
            raise ValueError("Office hybrid writer requires one bound kernel.")
        self.kernel = kernel

    @staticmethod
    def validate_input(value, expected):
        if (type(value) is not dict or set(value) != {"preview_id"}
                or type(expected) is not dict
                or set(expected) != {"schema_version", "workflow_version",
                    "parent_job_id", "policy_sha256", "source_fence_sha256",
                    "native_plan_sha256", "visual_plan_sha256", "native_batch_count",
                    "visual_page_count", "_authorization_fence"}
                or type(expected["schema_version"]) is not int
                or expected["schema_version"] != 1
                or expected["workflow_version"] != VERSION):
            raise JobExpectedStateConflictError("Office hybrid writer input is invalid.")
        try:
            if str(UUID(value["preview_id"])) != value["preview_id"]:
                raise ValueError
            if str(UUID(expected["parent_job_id"])) != expected["parent_job_id"]:
                raise ValueError
            for key in ("policy_sha256", "source_fence_sha256",
                    "native_plan_sha256", "visual_plan_sha256"):
                digest = expected[key]
                if (type(digest) is not str or len(digest) != 64
                        or any(character not in "0123456789abcdef" for character in digest)):
                    raise ValueError
            if (type(expected["native_batch_count"]) is not int
                    or not 0 <= expected["native_batch_count"] <= 4096
                    or type(expected["visual_page_count"]) is not int
                    or not 1 <= expected["visual_page_count"] <= 4096):
                raise ValueError
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise JobExpectedStateConflictError("Office hybrid writer identity is invalid.") from exc

    def revalidate_expected_state(self, connection, job):
        self.validate_input(dict(job.input_ref), dict(job.expected_state))
        row = connection.execute("SELECT * FROM office_hybrid_wiki_run WHERE job_id=?",
            (job.id,)).fetchone()
        if row is None:
            raise JobExpectedStateConflictError("Office hybrid writer has no registered run.")
        try:
            current = self.source._snapshot(connection, row["preview_id"],
                row["actor_user_id"], now_ms=0)
            expected = job.expected_state
            if (job.job_type != WRITE or job.handler_version != "v1"
                    or job.actor_user_id != row["actor_user_id"]
                    or job.input_ref["preview_id"] != row["preview_id"]
                    or current.preview["durable_job_id"] != row["parent_job_id"]
                    or current.fence_sha256 != row["source_fence_sha256"]
                    or row["policy_sha256"] != _hash(self.source.policy.encoded())
                    or expected["parent_job_id"] != row["parent_job_id"]
                    or expected["policy_sha256"] != row["policy_sha256"]
                    or expected["source_fence_sha256"] != row["source_fence_sha256"]
                    or expected["native_plan_sha256"] != row["native_plan_sha256"]
                    or expected["visual_plan_sha256"] != row["visual_plan_sha256"]
                    or expected["native_batch_count"] != row["native_batch_count"]
                    or expected["visual_page_count"] != row["visual_page_count"]):
                raise ValueError
        except (OfficeHybridAuthoringSourceConflict, KeyError, TypeError, ValueError) as exc:
            raise JobExpectedStateConflictError(
                "Office hybrid writer source or policy changed.") from exc

    @staticmethod
    def _fence(job):
        return ClaimFence(job.id, job.claim_owner, job.attempt_count)

    def _guard(self, fence, prepared):
        self.kernel.preflight_preparation(fence)
        connection = self.factory.open(read_only=True)
        try:
            with transaction(connection, TransactionMode.DEFERRED):
                self.source.revalidate_in_transaction(connection, prepared)
        finally:
            connection.close()

    @staticmethod
    def _receipt(row):
        model = tuple(tuple(pair) for pair in json.loads(row["model_json"]))
        if row["task_kind"] == "native_batch":
            return NativeBatchReceipt(row["task_id"], row["request_sha256"],
                row["response_text"], row["response_sha256"], model,
                row["transport_json"])
        return OfficeVisualObservationReceipt(row["ordinal"], row["task_id"],
            row["request_sha256"], row["response_text"],
            row["response_sha256"], model, row["transport_json"])

    def _load_receipts(self, connection, preview_id, prepared):
        native, visual = {}, {}
        for row in connection.execute("""SELECT * FROM office_hybrid_wiki_receipt
                WHERE preview_id=? ORDER BY task_kind,ordinal""", (preview_id,)):
            receipt = self._receipt(row)
            if row["task_kind"] == "native_batch":
                replay_native_batch(prepared.native, row["ordinal"], receipt)
                native[row["ordinal"]] = receipt
            else:
                replay_office_visual_observation(prepared.visual, receipt)
                visual[row["ordinal"]] = receipt
        return native, visual

    def _record_receipt(self, job, prepared, kind, ordinal, receipt):
        fence = self._fence(job)
        connection = self.factory.open()
        try:
            with transaction(connection, TransactionMode.IMMEDIATE):
                self.jobs.assert_effect_fence(connection, fence=fence,
                    now_ms=db_now_ms(connection))
                self.kernel.revalidate_job_authority(connection, job.id)
                self.revalidate_expected_state(connection, job)
                self.source.revalidate_in_transaction(connection, prepared)
                task_id = receipt.batch_id if kind == "native_batch" else receipt.source_id
                prior = connection.execute("""SELECT 1 FROM office_hybrid_wiki_receipt
                    WHERE preview_id=? AND task_kind=? AND ordinal=?""",
                    (prepared.preview_id, kind, ordinal)).fetchone()
                if prior is not None:
                    raise JobExpectedStateConflictError("Office hybrid task was already retained.")
                connection.execute("""INSERT INTO office_hybrid_wiki_receipt(
                    preview_id,task_kind,ordinal,task_id,request_sha256,
                    response_sha256,response_text,model_json,transport_json,created_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (prepared.preview_id, kind, ordinal, task_id,
                        receipt.request_sha256, receipt.response_sha256,
                        receipt.response, _json(list(receipt.model)),
                        receipt.transport_json, db_now_ms(connection)))
        finally:
            connection.close()

    @staticmethod
    def _authority_cause(error):
        current = error
        while current is not None:
            if isinstance(current, (JobAuthorizationRejectedError,
                    JobCancellationRequestedError, JobClaimLostError,
                    JobExpectedStateConflictError,
                    PermissionError, OfficeHybridAuthoringSourceConflict)):
                return current
            current = current.__cause__
        return None

    def prepare(self, job):
        if self.kernel is None:
            raise PermanentJobExecutionError("Office hybrid writer kernel is not bound.")
        fence = self._fence(job)
        self.kernel.preflight_preparation(fence)
        prepared = self.source.prepare(preview_id=job.input_ref["preview_id"],
            actor_user_id=job.actor_user_id)
        try:
            if (prepared.parent_job_id != job.expected_state["parent_job_id"]
                    or prepared.fence_sha256 != job.expected_state["source_fence_sha256"]
                    or _native_plan_sha256(prepared.native) != job.expected_state["native_plan_sha256"]
                    or prepared.visual.plan_sha256 != job.expected_state["visual_plan_sha256"]
                    or len(prepared.native.batches) != job.expected_state["native_batch_count"]
                    or len(prepared.visual.source.pages) != job.expected_state["visual_page_count"]):
                raise JobExpectedStateConflictError("Office hybrid writing plan changed.")
            connection = self.factory.open(read_only=True)
            try:
                with transaction(connection, TransactionMode.DEFERRED):
                    native, visual = self._load_receipts(connection,
                        prepared.preview_id, prepared)
            finally:
                connection.close()
            timeout = max(prepared.native.source_plan.execution_policy.timeout_s,
                prepared.visual.execution_policy.timeout_s)
            for batch in prepared.native.batches:
                if batch.ordinal in native:
                    continue
                self.kernel.renew_claim(fence, lease_ms=(timeout + 30) * 1000)
                try:
                    receipt = self.native_author.author_batch(prepared.native,
                        batch.ordinal, attempt_guard=lambda: self._guard(fence, prepared))
                    self._record_receipt(job, prepared, "native_batch", batch.ordinal, receipt)
                except Exception as exc:
                    cause = self._authority_cause(exc)
                    if cause is not None:
                        raise cause
                    raise RetryableJobExecutionError("Office native batch failed.") from exc
                native[batch.ordinal] = receipt
            for page in range(1, len(prepared.visual.source.pages) + 1):
                if page in visual:
                    continue
                self.kernel.renew_claim(fence, lease_ms=(timeout + 30) * 1000)
                try:
                    receipt, _ = self.visual_author.author_page(prepared.visual,
                        page, attempt_guard=lambda: self._guard(fence, prepared))
                    self._record_receipt(job, prepared, "visual_page", page, receipt)
                except Exception as exc:
                    cause = self._authority_cause(exc)
                    if cause is not None:
                        raise cause
                    raise RetryableJobExecutionError("Office visual page failed.") from exc
                visual[page] = receipt
            wiki = assemble_office_hybrid_batches(prepared.native,
                tuple(native[index] for index in sorted(native)), prepared.visual,
                tuple(visual[index] for index in sorted(visual)))
            self._guard(fence, prepared)
            result = {"schema_version": 1, "preview_id": prepared.preview_id,
                "content_sha256": _hash(wiki.markdown),
                "manifest_sha256": _hash(wiki.manifest_json)}
            return PreparedJobEffect(result, (prepared, wiki), prepared.close)
        except BaseException:
            prepared.close()
            raise

    def commit_effect(self, connection, job, effect):
        if (type(effect.payload) is not tuple or len(effect.payload) != 2
                or type(effect.payload[1]) is not AuthoredWiki):
            raise PermanentJobExecutionError("Office hybrid writer payload is invalid.")
        prepared, wiki = effect.payload
        try:
            self.source.revalidate_in_transaction(connection, prepared)
        except PermissionError as exc:
            raise JobAuthorizationRejectedError(
                "Office source export permission was revoked before draft commit.") from exc
        self.revalidate_expected_state(connection, job)
        native, visual = self._load_receipts(connection, prepared.preview_id, prepared)
        if (len(native) != len(prepared.native.batches)
                or len(visual) != len(prepared.visual.source.pages)):
            raise PermanentJobExecutionError("Office hybrid writer has missing receipts.")
        assembled = assemble_office_hybrid_batches(prepared.native,
            tuple(native[index] for index in sorted(native)), prepared.visual,
            tuple(visual[index] for index in sorted(visual)))
        result = {"schema_version": 1, "preview_id": prepared.preview_id,
            "content_sha256": _hash(wiki.markdown),
            "manifest_sha256": _hash(wiki.manifest_json)}
        if (assembled != wiki or dict(effect.result) != result):
            raise PermanentJobExecutionError("Office hybrid draft changed before commit.")
        connection.execute("""INSERT INTO office_hybrid_wiki_snapshot(
            preview_id,job_id,actor_user_id,content_md,content_sha256,
            manifest_json,manifest_sha256,created_at)
            VALUES(?,?,?,?,?,?,?,?)""",
            (prepared.preview_id, job.id, prepared.actor_user_id,
                wiki.markdown, result["content_sha256"], wiki.manifest_json,
                result["manifest_sha256"], db_now_ms(connection)))


__all__ = ["OfficeHybridWikiWriteHandler"]
