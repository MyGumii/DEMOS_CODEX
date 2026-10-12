from __future__ import annotations

from dataclasses import replace
import hashlib
from uuid import UUID, uuid5

import pytest

from code_assist_v1.knowledge_core.db import (
    ConnectionFactory,
    db_now_ms,
    transaction,
)
from code_assist_v1.knowledge_core.errors import KnowledgeCoreTransactionError
from code_assist_v1.knowledge_core.migrations import (
    CANONICAL_MIGRATION_CATALOG,
    MigrationStorageLayout,
    SingleMigrator,
)
from code_assist_v1.knowledge_core.repositories.artifacts import (
    ArtifactIds,
    ArtifactRepository,
    StagedVerifiedBlob,
)
from code_assist_v1.knowledge_core.repositories.jobs import (
    HandlerKey,
    JobEnqueueDraft,
    JobRepository,
    canonical_job_input_sha256,
)
from code_assist_v1.knowledge_core.repositories.publication import (
    PREVIEW_TTL_MS,
    PreviewFinding,
    PublicationPreviewConflictError,
    PublicationPreviewNotFoundError,
    PublicationPreviewRepository,
    QueuedPreviewDraft,
    ReadyPreviewDraft,
)
from code_assist_v1.knowledge_core.repositories.users import (
    LegacyUserRecord,
    UserRepository,
)
from code_assist_v1.knowledge_core.services.publication import (
    build_source_artifact_bundle,
    prepare_publication_artifact,
)


pytestmark = pytest.mark.unit

_NAMESPACE = UUID("74597720-3aa9-46e6-a0e8-44e3464eb06d")
_ACTOR = "actor"
_RULE = "p6.1-deterministic-ingest-v1"


def _uuid(label: str) -> str:
    return str(uuid5(_NAMESPACE, label))


def _factory(layout) -> ConnectionFactory:
    factory = ConnectionFactory._for_isolated_tests(
        layout.core_db_path,
        CANONICAL_MIGRATION_CATALOG,
    )
    SingleMigrator(
        factory,
        app_version="p4.2-preview-repository-test",
        storage_layout=MigrationStorageLayout(
            data_root=layout.root,
            database=layout.core_db_path,
            blob_root=layout.blobs_dir,
            projection_root=layout.projections_dir,
        ),
    ).run()
    return factory


def _seed_actor(connection) -> None:
    UserRepository().import_legacy(
        connection,
        LegacyUserRecord(
            id=_ACTOR,
            display_name="Actor",
            password_hash="scrypt:p4.2:actor",
            system_role="user",
            account_status="active",
            created_at_ms=100,
            updated_at_ms=100,
        ),
    )


def _seed_artifact(connection, label: str = "source"):
    raw = b"# Source\r\n\r\nBody\r\n"
    prepared = prepare_publication_artifact(
        raw,
        extracted_markdown=raw.decode("utf-8"),
        extraction_failure_code=None,
        extractor_name="manual-markdown",
        extractor_version="1",
        created_at_ms=200,
    )
    sha = hashlib.sha256(raw).hexdigest()
    blob = StagedVerifiedBlob(
        sha256=sha,
        size_bytes=len(raw),
        media_type="text/markdown",
        relative_path=f"{sha[:2]}/{sha[2:4]}/{sha}",
        created_at_ms=190,
        verified_at_ms=191,
    )
    bundle = build_source_artifact_bundle(
        prepared,
        ids=ArtifactIds(
            _uuid(f"artifact:{label}"),
            _uuid(f"raw:{label}"),
            _uuid(f"extraction:{label}"),
        ),
        origin_type="manual",
        origin_key=f"manual:{label}",
        origin_revision_key=None,
        raw_blob=blob,
        logical_name=f"{label}.md",
        original_filename=None,
        publisher_user_id=_ACTOR,
        created_at_ms=200,
    )
    ArtifactRepository().put_source_bundle(connection, bundle)
    assert prepared.extraction.extracted_sha256 is not None
    return bundle, prepared


def _finding(label: str = "clear") -> PreviewFinding:
    return PreviewFinding(
        kind=label,
        severity="info",
        risk_tier="T0",
        visibility_effect="none",
        evidence_sha256=hashlib.sha256(label.encode()).hexdigest(),
    )


def _ready_draft(
    bundle,
    prepared,
    *,
    preview_id: str | None = None,
    job_id: str | None = None,
    created_at_ms: int = 500,
    completed_at_ms: int | None = None,
) -> ReadyPreviewDraft:
    assert prepared.extraction.extracted_sha256 is not None
    return ReadyPreviewDraft(
        preview_id=preview_id or _uuid("preview:ready"),
        request_id=_uuid("request:ready"),
        operation_id=_uuid("operation:ready"),
        actor_user_id=_ACTOR,
        artifact_id=bundle.ids.artifact_id,
        extraction_snapshot_id=bundle.ids.extraction_snapshot_id,
        durable_job_id=job_id,
        source_snapshot_sha256=prepared.extraction.extracted_sha256,
        captured_part_id=None,
        captured_scope_epoch=1,
        available_target_modes=("personal",),
        rule_version=_RULE,
        title="Source",
        size_bytes=prepared.extraction.extracted_size_bytes,
        warnings=("fixture_warning",),
        deterministic_findings=(_finding(),),
        expires_at_ms=created_at_ms + PREVIEW_TTL_MS,
        created_at_ms=created_at_ms,
        completed_at_ms=(created_at_ms if completed_at_ms is None else completed_at_ms),
    )


def _enqueue_job(connection, *, preview_id: str, artifact_id: str):
    jobs = JobRepository()
    input_ref = {
        "artifact_id": artifact_id,
        "preview_id": preview_id,
        "rule_version": _RULE,
    }
    operation_id = _uuid(f"job-operation:{preview_id}")
    dedup_id = _uuid(f"job-dedup:{preview_id}")
    now_ms = db_now_ms(connection)
    UserRepository().begin_operation(
        connection,
        actor_user_id=_ACTOR,
        operation_type="job:publication_preview.extract",
        operation_id=operation_id,
        request_sha256=canonical_job_input_sha256(input_ref),
        dedup_id=dedup_id,
        now_ms=now_ms,
    )
    result = jobs.enqueue(
        connection,
        JobEnqueueDraft(
            job_id=_uuid(f"job:{preview_id}"),
            dedup_id=dedup_id,
            operation_id=operation_id,
            actor_kind="user",
            actor_user_id=_ACTOR,
            space_id=None,
            job_type="publication_preview.extract",
            handler_version="v1",
            effect_key=f"publication-preview:{preview_id}",
            input_ref=input_ref,
            expected_state={"preview_id": preview_id, "schema_version": 1},
            authorization_snapshot={
                "account_status": "active",
                "actor_kind": "user",
                "actor_user_id": _ACTOR,
                "capabilities": [],
                "current_part_id": None,
                "schema_version": 1,
                "scope_epoch": 1,
                "space_id": None,
                "system_role": "user",
            },
            authorization_scope_epoch=1,
            authorization_part_id=None,
            authorization_capabilities=(),
            max_attempts=3,
            cancellable=True,
            available_at_ms=now_ms,
        ),
    )
    return result.job


def _seed_queued(connection, bundle, *, label: str = "queued"):
    preview_id = _uuid(f"preview:{label}")
    job = _enqueue_job(
        connection, preview_id=preview_id, artifact_id=bundle.ids.artifact_id
    )
    draft = QueuedPreviewDraft(
        preview_id=preview_id,
        actor_user_id=_ACTOR,
        artifact_id=bundle.ids.artifact_id,
        durable_job_id=job.id,
        captured_part_id=None,
        captured_scope_epoch=1,
        available_target_modes=("personal",),
        rule_version=_RULE,
        expires_at_ms=job.created_at_ms + PREVIEW_TTL_MS,
        created_at_ms=job.created_at_ms,
    )
    record = PublicationPreviewRepository().insert_queued(connection, draft)
    return record, draft, job


@pytest.mark.scenario("PUB-012")
def test_synchronous_ready_preview_is_immutable_restart_safe_and_body_free(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    previews = PublicationPreviewRepository()
    connection = factory.open()
    try:
        with transaction(connection):
            _seed_actor(connection)
            bundle, prepared = _seed_artifact(connection)
            draft = _ready_draft(bundle, prepared)
            record = previews.insert_ready(connection, draft)
            assert record.status == "ready"
            assert record.durable_job_id is None
            assert record.source_snapshot_sha256 == prepared.extraction.extracted_sha256
            result = record.ready_result
            assert result is not None
            assert result["status"] == "ready"
            assert result["source_snapshot_sha256"] == record.source_snapshot_sha256
            assert "raw" not in result and "markdown" not in result
            assert connection.execute("SELECT count(*) FROM knowledge_item").fetchone()[0] == 0
        reopened = factory.open(read_only=True)
        try:
            replay = previews.require_for_actor(
                reopened, preview_id=draft.preview_id, actor_user_id=_ACTOR
            )
            assert replay.ready_result_json == record.ready_result_json
            assert replay.deterministic_findings_json == record.deterministic_findings_json
        finally:
            reopened.close()
    finally:
        connection.close()


def test_foreign_actor_preview_lookup_is_indistinguishable_from_missing(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    connection = factory.open()
    try:
        with transaction(connection):
            _seed_actor(connection)
            bundle, prepared = _seed_artifact(connection)
            record = PublicationPreviewRepository().insert_ready(
                connection, _ready_draft(bundle, prepared)
            )
        with pytest.raises(PublicationPreviewNotFoundError):
            PublicationPreviewRepository().require_for_actor(
                connection, preview_id=record.preview_id, actor_user_id="foreign"
            )
    finally:
        connection.close()


def test_queued_preview_claim_start_and_ready_completion_bind_one_running_job(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    previews = PublicationPreviewRepository()
    jobs = JobRepository()
    connection = factory.open()
    try:
        with transaction(connection):
            _seed_actor(connection)
            bundle, prepared = _seed_artifact(connection)
            queued, queued_draft, job = _seed_queued(connection, bundle)
        worker_now = job.created_at_ms + 1
        with transaction(connection):
            claimed = jobs.claim_next(
                connection,
                handler_keys=(HandlerKey("publication_preview.extract", "v1"),),
                claim_owner="worker-1",
                lease_ms=10_000,
                now_ms=worker_now,
            )
            assert claimed is not None
            jobs.start(connection, claimed.fence, now_ms=worker_now)
            extracting = previews.mark_extracting(
                connection, preview_id=queued.preview_id, updated_at_ms=worker_now
            )
            assert extracting.status == "extracting"
        completed = worker_now + 1
        with transaction(connection):
            jobs.assert_effect_fence(connection, fence=claimed.fence, now_ms=completed)
            ready_draft = _ready_draft(
                bundle,
                prepared,
                preview_id=queued.preview_id,
                job_id=job.id,
                created_at_ms=queued_draft.created_at_ms,
                completed_at_ms=completed,
            )
            ready = previews.complete_ready(connection, ready_draft)
            jobs.succeed(
                connection,
                claimed.fence,
                result={"preview_id": ready.preview_id, "status": "ready"},
                now_ms=completed,
            )
            assert ready.status == "ready"
            assert ready.ready_result["job_id"] == job.id
        reopened = factory.open()
        try:
            with transaction(reopened):
                assert previews.get(reopened, queued.preview_id).status == "ready"
                assert jobs.require(reopened, job.id).state == "succeeded"
        finally:
            reopened.close()
    finally:
        connection.close()


def test_queued_preview_exact_replay_and_drift_rejection(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    previews = PublicationPreviewRepository()
    connection = factory.open()
    try:
        with transaction(connection):
            _seed_actor(connection)
            bundle, _ = _seed_artifact(connection)
            original, draft, _ = _seed_queued(connection, bundle)
            assert previews.insert_queued(connection, draft) == original
            with pytest.raises(PublicationPreviewConflictError):
                previews.insert_queued(
                    connection,
                    replace(draft, rule_version="changed"),
                )
    finally:
        connection.close()


@pytest.mark.parametrize("terminal", ["failed", "cancelled"])
@pytest.mark.scenario("PUB-012")
def test_unready_preview_terminal_transitions_preserve_zero_knowledge(
    isolated_knowledge_layout,
    terminal: str,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    previews = PublicationPreviewRepository()
    connection = factory.open()
    try:
        with transaction(connection):
            _seed_actor(connection)
            bundle, _ = _seed_artifact(connection)
            queued, _, job = _seed_queued(connection, bundle, label=terminal)
            completed = job.created_at_ms + 1
            result = (
                previews.fail(
                    connection,
                    preview_id=queued.preview_id,
                    error={"code": "EXTRACTION_FAILED", "message": "Extraction failed."},
                    completed_at_ms=completed,
                )
                if terminal == "failed"
                else previews.cancel(
                    connection,
                    preview_id=queued.preview_id,
                    completed_at_ms=completed,
                )
            )
            assert result.status == terminal
            assert result.completed_at_ms == completed
            assert connection.execute("SELECT count(*) FROM knowledge_item").fetchone()[0] == 0
    finally:
        connection.close()


@pytest.mark.parametrize("terminal", ["expired", "cancelled"])
def test_ready_preview_terminal_transition_preserves_immutable_result(
    isolated_knowledge_layout,
    terminal: str,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    previews = PublicationPreviewRepository()
    connection = factory.open()
    try:
        with transaction(connection):
            _seed_actor(connection)
            bundle, prepared = _seed_artifact(connection)
            ready = previews.insert_ready(connection, _ready_draft(bundle, prepared))
            frozen = ready.ready_result_json
        with transaction(connection):
            transitioned = (
                previews.expire_ready(
                    connection,
                    preview_id=ready.preview_id,
                    now_ms=ready.expires_at_ms,
                )
                if terminal == "expired"
                else previews.cancel(
                    connection,
                    preview_id=ready.preview_id,
                    completed_at_ms=ready.created_at_ms + 1,
                )
            )
            assert transitioned.status == terminal
            assert transitioned.ready_result_json == frozen
    finally:
        connection.close()


@pytest.mark.parametrize(
    "mutation",
    [
        "ttl",
        "target",
        "source_hash",
        "warning",
        "finding",
        "job_on_sync",
    ],
)
def test_ready_preview_shape_fails_before_insert(
    isolated_knowledge_layout,
    mutation: str,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    connection = factory.open()
    try:
        with transaction(connection):
            _seed_actor(connection)
            bundle, prepared = _seed_artifact(connection)
            draft = _ready_draft(bundle, prepared)
            if mutation == "ttl":
                draft = replace(draft, expires_at_ms=draft.expires_at_ms + 1)
            elif mutation == "target":
                draft = replace(draft, available_target_modes=("part",))
            elif mutation == "source_hash":
                draft = replace(draft, source_snapshot_sha256="0" * 64)
            elif mutation == "warning":
                draft = replace(draft, warnings=("raw warning text",))
            elif mutation == "finding":
                draft = replace(
                    draft,
                    deterministic_findings=(
                        PreviewFinding("bad", "info", "T0", "none", "bad"),
                    ),
                )
            else:
                draft = replace(draft, durable_job_id=_uuid("unexpected-job"))
            with pytest.raises((ValueError, PublicationPreviewConflictError)):
                PublicationPreviewRepository().insert_ready(connection, draft)
        check = factory.open(read_only=True)
        try:
            assert check.execute("SELECT count(*) FROM publication_preview").fetchone()[0] == 0
        finally:
            check.close()
    finally:
        connection.close()


def test_preview_writes_require_managed_transaction(isolated_knowledge_layout) -> None:
    factory = _factory(isolated_knowledge_layout)
    connection = factory.open()
    try:
        with transaction(connection):
            _seed_actor(connection)
            bundle, prepared = _seed_artifact(connection)
        with pytest.raises(KnowledgeCoreTransactionError):
            PublicationPreviewRepository().insert_ready(
                connection, _ready_draft(bundle, prepared)
            )
    finally:
        connection.close()
