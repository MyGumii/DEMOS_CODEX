from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import io
import json
import zipfile
from uuid import UUID, uuid5

import pytest

from code_assist_v1.knowledge_core.db import ConnectionFactory, db_now_ms, transaction
from code_assist_v1.knowledge_core.domain import FeatureFlags
from code_assist_v1.knowledge_core.migrations import (
    CANONICAL_MIGRATION_CATALOG,
    MigrationStorageLayout,
    SingleMigrator,
)
from code_assist_v1.knowledge_core.repositories.artifacts import (
    ArtifactBundleConflictError,
    ArtifactRepository,
    StagedVerifiedBlob,
)
from code_assist_v1.knowledge_core.repositories.publication import (
    PREVIEW_TTL_MS,
    PublicationPreviewRepository,
    ReadyPreviewDraft,
)
from code_assist_v1.knowledge_core.repositories.jobs import JobRepository
from code_assist_v1.knowledge_core.repositories.organization import (
    OrganizationRepository,
    RESPONSIBILITY_ROOT_ID,
)
from code_assist_v1.knowledge_core.repositories.spaces import SpaceRepository
from code_assist_v1.knowledge_core.repositories.users import (
    LegacyUserRecord,
    UserRepository,
)
from code_assist_v1.knowledge_core.scope import ScopeResolver
from code_assist_v1.knowledge_core.services.publication import (
    AuthorizedNotionPageSnapshot,
    CancelPublicationPreviewCommand,
    CanonicalPublicationSourceLoader,
    CreateChatPreviewCommand,
    GetPublicationPreviewQuery,
    LoadedPublicationSource,
    PublicationPreviewExtractionHandler,
    CreateManualPreviewCommand,
    CreateNotionPreviewCommand,
    CreateUploadPreviewCommand,
    PublicationPreviewAuthorizationError,
    PublicationPreviewIdempotencyError,
    PublicationPreviewService,
    PublicationPreviewStagingError,
    PublicationPreviewValidationError,
    PublicationService,
    PublishPreviewCommand,
    Utf8PublicationExtractor,
    build_source_artifact_bundle,
    prepare_publication_artifact,
    publication_artifact_ids,
)
from code_assist_v1.knowledge_core.services.spaces import space_id_for_owner
from code_assist_v1.knowledge_core.services.rich_publication import (
    CanonicalRichPublicationExtractor,
)
from code_assist_v1.knowledge_core.worker import (
    DurableWorkKernel,
    HandlerRegistry,
    PermanentJobExecutionError,
    RetryableJobExecutionError,
)


pytestmark = pytest.mark.unit

_NAMESPACE = UUID("417804a7-6f24-4c96-a75d-f4f2cd798a9f")
_ACTOR = "actor"


def _uuid(label: str) -> str:
    return str(uuid5(_NAMESPACE, label))


def _factory(layout) -> ConnectionFactory:
    factory = ConnectionFactory._for_isolated_tests(
        layout.core_db_path,
        CANONICAL_MIGRATION_CATALOG,
    )
    SingleMigrator(
        factory,
        app_version="p4.2-preview-service-test",
        storage_layout=MigrationStorageLayout(
            data_root=layout.root,
            database=layout.core_db_path,
            blob_root=layout.blobs_dir,
            projection_root=layout.projections_dir,
        ),
    ).run()
    return factory


def _seed_actor(factory: ConnectionFactory) -> str:
    personal_id = space_id_for_owner("personal", _ACTOR)
    connection = factory.open()
    try:
        with transaction(connection):
            UserRepository().import_legacy(
                connection,
                LegacyUserRecord(
                    id=_ACTOR,
                    display_name="Preview Actor",
                    password_hash="scrypt:p4.2:actor",
                    system_role="user",
                    account_status="active",
                    created_at_ms=100,
                    updated_at_ms=100,
                ),
            )
            spaces = SpaceRepository()
            created = spaces.ensure_personal(
                connection,
                space_id=personal_id,
                user_id=_ACTOR,
                created_at_ms=100,
            )
            spaces.update_state(
                connection,
                space_id=created.space.id,
                expected_row_version=created.space.row_version,
                status="active",
                write_enabled=True,
                updated_at_ms=101,
            )
    finally:
        connection.close()
    return personal_id


def _assign_actor_to_part(factory: ConnectionFactory) -> tuple[str, str, int]:
    team_id = _uuid("preview-team")
    part_id = _uuid("preview-part")
    part_space_id = _uuid("preview-part-space")
    organization = OrganizationRepository()
    users = UserRepository()
    spaces = SpaceRepository()
    connection = factory.open()
    try:
        with transaction(connection):
            organization.create_unit(
                connection,
                unit_id=team_id,
                kind="team",
                parent_id=RESPONSIBILITY_ROOT_ID,
                name="Preview Team",
                actor_user_id=_ACTOR,
                now_ms=110,
            )
            organization.create_unit(
                connection,
                unit_id=part_id,
                kind="part",
                parent_id=team_id,
                name="Preview Part",
                actor_user_id=_ACTOR,
                now_ms=111,
            )
            organization.open_membership(
                connection,
                membership_id=_uuid("preview-membership"),
                user_id=_ACTOR,
                part_id=part_id,
                assigned_by=_ACTOR,
                reason="P4.2 target-mode fixture",
                valid_from_ms=112,
            )
            actor = users.require(connection, _ACTOR)
            moved = organization.compare_and_swap_user_part(
                connection,
                user_id=_ACTOR,
                expected_current_part_id=None,
                target_part_id=part_id,
                expected_scope_epoch=actor.scope_epoch,
                expected_row_version=actor.row_version,
                updated_at_ms=113,
            )
            organization.open_part_leader(
                connection,
                assignment_id=_uuid("preview-leader-assignment"),
                user_id=_ACTOR,
                part_id=part_id,
                assigned_by=_ACTOR,
                reason="P4.2 writable-part fixture",
                valid_from_ms=114,
            )
            ensured = spaces.ensure_part(
                connection,
                space_id=part_space_id,
                part_id=part_id,
                created_at_ms=115,
            )
            spaces.update_state(
                connection,
                space_id=part_space_id,
                expected_row_version=ensured.space.row_version,
                status="active",
                write_enabled=True,
                updated_at_ms=116,
            )
        return part_id, part_space_id, moved.scope_epoch
    finally:
        connection.close()


class _Lease:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


@dataclass(frozen=True)
class _Observation:
    blob: StagedVerifiedBlob
    lease: _Lease


class _Stager:
    def __init__(self) -> None:
        self.calls = 0
        self.leases: list[_Lease] = []

    def stage_and_verify(
        self,
        content: bytes,
        *,
        expected_sha256: str,
        media_type: str,
        effective_at_ms: int,
        normalized_content_sha256: str | None,
        normalizer_version: str | None,
    ) -> _Observation:
        self.calls += 1
        assert hashlib.sha256(content).hexdigest() == expected_sha256
        if normalizer_version is None:
            assert normalized_content_sha256 is None
        else:
            assert normalized_content_sha256 == hashlib.sha256(
                content.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
            ).hexdigest()
            assert normalizer_version == "markdown_lf_v1"
        lease = _Lease()
        self.leases.append(lease)
        return _Observation(
            blob=StagedVerifiedBlob(
                sha256=expected_sha256,
                size_bytes=len(content),
                media_type=media_type,
                relative_path=(
                    f"{expected_sha256[:2]}/{expected_sha256[2:4]}/"
                    f"{expected_sha256}"
                ),
                created_at_ms=effective_at_ms,
                verified_at_ms=effective_at_ms,
            ),
            lease=lease,
        )


class _CallbackStager(_Stager):
    def __init__(self, callback) -> None:
        super().__init__()
        self._callback = callback

    def stage_and_verify(self, content: bytes, **kwargs):
        result = super().stage_and_verify(content, **kwargs)
        self._callback()
        return result


class _DriftingEvidenceStager(_Stager):
    def __init__(self, field_name: str) -> None:
        super().__init__()
        self._field_name = field_name

    def stage_and_verify(self, content: bytes, **kwargs):
        result = super().stage_and_verify(content, **kwargs)
        values = {
            "sha256": "0" * 64,
            "size_bytes": len(content) + 1,
            "media_type": "application/x-drift",
            "relative_path": "00/00/" + "0" * 64,
            "created_at_ms": result.blob.created_at_ms + 1,
            "verified_at_ms": result.blob.verified_at_ms + 1,
        }
        return replace(
            result,
            blob=replace(result.blob, **{self._field_name: values[self._field_name]}),
        )


class _ExistingVerifier:
    def __init__(self, content: bytes, field_name: str | None = None) -> None:
        self._content = content
        self._field_name = field_name
        self.lease = _Lease()

    def verify_existing(
        self,
        *,
        expected_sha256: str,
        expected_size: int,
        media_type: str,
        normalized_content_sha256: str | None,
        normalizer_version: str | None,
    ):
        assert expected_size == len(self._content)
        assert normalized_content_sha256 is None
        assert normalizer_version is None
        blob = StagedVerifiedBlob(
            sha256=expected_sha256,
            size_bytes=expected_size,
            media_type=media_type,
            relative_path=(
                f"{expected_sha256[:2]}/{expected_sha256[2:4]}/"
                f"{expected_sha256}"
            ),
            created_at_ms=0,
            verified_at_ms=0,
        )
        if self._field_name is not None:
            values = {
                "sha256": "0" * 64,
                "size_bytes": expected_size + 1,
                "media_type": "application/x-drift",
                "relative_path": "00/00/" + "0" * 64,
                "created_at_ms": 1,
                "verified_at_ms": 1,
            }
            blob = replace(blob, **{self._field_name: values[self._field_name]})
        return _Observation(blob=blob, lease=self.lease), self._content


class _SourceLoader:
    def __init__(
        self,
        content: bytes,
        *,
        original_filename: str = "source.md",
    ) -> None:
        self.content = content
        self.original_filename = original_filename
        self.calls = 0
        self.leases: list[_Lease] = []

    def load(
        self,
        *,
        artifact_id: str,
        expected_sha256: str,
        expected_size: int,
        expected_media_type: str,
    ) -> LoadedPublicationSource:
        del artifact_id
        self.calls += 1
        assert hashlib.sha256(self.content).hexdigest() == expected_sha256
        assert len(self.content) == expected_size
        lease = _Lease()
        self.leases.append(lease)
        return LoadedPublicationSource(
            content=self.content,
            media_type=expected_media_type,
            original_filename=self.original_filename,
            lease=lease,
        )


class _RetryingSourceLoader:
    def load(self, **kwargs):
        del kwargs
        raise RetryableJobExecutionError("transient source verification failure")


class _BrokenExtractor:
    def extract(self, content, *, media_type, original_filename):
        del content, media_type, original_filename
        return object()


class _CancelDuringExtractor:
    def __init__(self, service: PublicationPreviewService, command) -> None:
        self._service = service
        self._command = command

    def extract(self, content, *, media_type, original_filename):
        self._service.cancel_preview(self._command)
        return Utf8PublicationExtractor().extract(
            content,
            media_type=media_type,
            original_filename=original_filename,
        )


class _NotionSnapshots:
    def __init__(self, *, markdown: str = "# Notion page\n\nBody\n") -> None:
        self.markdown = markdown
        self.calls = 0

    def capture(self, *, actor_user_id: str, page_id: str):
        self.calls += 1
        content_sha = hashlib.sha256(self.markdown.encode("utf-8")).hexdigest()
        asset_sha = hashlib.sha256(b"[]").hexdigest()
        identity = {
            "asset_manifest_sha256": asset_sha,
            "content_sha256": content_sha,
            "owner_user_id": _ACTOR,
            "page_id": page_id,
            "revision": 7,
            "schema_version": 1,
            "updated_at": 1234,
        }
        source_sha = hashlib.sha256(
            json.dumps(
                identity,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        return AuthorizedNotionPageSnapshot(
            actor_user_id=actor_user_id,
            page_id=page_id,
            owner_user_id=_ACTOR,
            title="Notion page",
            revision=7,
            updated_at=1234,
            markdown=self.markdown,
            content_sha256=content_sha,
            asset_manifest_sha256=asset_sha,
            source_snapshot_sha256=source_sha,
        )


def _seed_conversation(factory: ConnectionFactory) -> str:
    conversation_id = _uuid("chat-conversation")
    personal_id = space_id_for_owner("personal", _ACTOR)
    connection = factory.open()
    try:
        with transaction(connection):
            connection.execute(
                """INSERT INTO conversation(
                     id,owner_user_id,space_id,captured_scope_epoch,title,status,
                     metadata_json,created_at,updated_at,row_version
                   ) VALUES (?,?,?,1,'Promotion source','active','{}',200,200,1)""",
                (conversation_id, _ACTOR, personal_id),
            )
            for sequence, role, content in (
                (1, "user", "Question"),
                (2, "assistant", "Answer"),
            ):
                connection.execute(
                    """INSERT INTO conversation_message(
                         id,conversation_id,sequence_no,role,content_json,created_at
                       ) VALUES (?,?,?,?,?,?)""",
                    (
                        _uuid(f"chat-message-{sequence}"),
                        conversation_id,
                        sequence,
                        role,
                        json.dumps(
                            {"content": content, "role": role},
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                        200 + sequence,
                    ),
                )
    finally:
        connection.close()
    return conversation_id


def _service(factory: ConnectionFactory, stager: _Stager, **kwargs):
    return PublicationPreviewService(
        factory,
        ScopeResolver(
            FeatureFlags(
                core_enabled=True,
                personal_reads=True,
                personal_writes=True,
            )
        ),
        stager,
        **kwargs,
    )


def _command(*, markdown: str = "# Ready\r\n\r\nBody\r\n"):
    return CreateManualPreviewCommand(
        request_id=_uuid("request"),
        operation_id=_uuid("operation"),
        actor_user_id=_ACTOR,
        actor_scope_epoch=1,
        markdown=markdown,
        title=None,
        request_meta={"correlation_id": "p4.2-test", "ignored": "value"},
    )


def _upload_command(*, content: bytes = b"uploaded source bytes"):
    return CreateUploadPreviewCommand(
        request_id=_uuid("upload-request"),
        operation_id=_uuid("upload-operation"),
        actor_user_id=_ACTOR,
        actor_scope_epoch=1,
        content=content,
        original_filename="source.pdf",
        media_type="application/pdf",
        title="Uploaded Source",
        request_meta={"correlation_id": "p4.2-upload"},
    )


def _rich_docx_fixture() -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(
            "word/document.xml",
            """<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body><w:p><w:r><w:t>Durable rich extraction</w:t></w:r></w:p></w:body>
</w:document>""",
        )
        archive.writestr("word/media/canonical.png", b"\x89PNG\r\n\x1a\nworker-asset")
    return output.getvalue()


def _chat_command(conversation_id: str, **changes):
    command = CreateChatPreviewCommand(
        request_id=_uuid("chat-preview-request"),
        operation_id=_uuid("chat-preview-operation"),
        actor_user_id=_ACTOR,
        actor_scope_epoch=1,
        conversation_id=conversation_id,
        first_sequence_no=1,
        last_sequence_no=2,
        request_meta={"correlation_id": "p4.2-chat"},
    )
    return replace(command, **changes)


def _notion_command(**changes):
    command = CreateNotionPreviewCommand(
        request_id=_uuid("notion-preview-request"),
        operation_id=_uuid("notion-preview-operation"),
        actor_user_id=_ACTOR,
        actor_scope_epoch=1,
        page_id="page-1",
        request_meta={"correlation_id": "p4.2-notion"},
    )
    return replace(command, **changes)


def _seed_old_ready_preview(factory: ConnectionFactory) -> tuple[str, str]:
    created_at = 1_000
    operation_id = _uuid("old-ready-operation")
    request_id = _uuid("old-ready-request")
    preview_id = _uuid("old-ready-preview")
    content = b"# Old preview\n\nBody\n"
    raw_sha256 = hashlib.sha256(content).hexdigest()
    prepared = prepare_publication_artifact(
        content,
        extracted_markdown=content.decode("utf-8"),
        extraction_failure_code=None,
        extractor_name="test_utf8",
        extractor_version="v1",
        created_at_ms=created_at,
    )
    ids = publication_artifact_ids(_ACTOR, operation_id)
    bundle = build_source_artifact_bundle(
        prepared,
        ids=ids,
        origin_type="manual",
        origin_key=None,
        origin_revision_key=None,
        raw_blob=StagedVerifiedBlob(
            sha256=raw_sha256,
            size_bytes=len(content),
            media_type="text/markdown",
            relative_path=f"{raw_sha256[:2]}/{raw_sha256[2:4]}/{raw_sha256}",
            created_at_ms=created_at,
            verified_at_ms=created_at,
        ),
        logical_name="old-preview.md",
        original_filename=None,
        publisher_user_id=_ACTOR,
        created_at_ms=created_at,
    )
    connection = factory.open()
    try:
        with transaction(connection):
            ArtifactRepository().put_source_bundle(connection, bundle)
            PublicationPreviewRepository().insert_ready(
                connection,
                ReadyPreviewDraft(
                    preview_id=preview_id,
                    request_id=request_id,
                    operation_id=operation_id,
                    actor_user_id=_ACTOR,
                    artifact_id=ids.artifact_id,
                    extraction_snapshot_id=ids.extraction_snapshot_id,
                    durable_job_id=None,
                    source_snapshot_sha256=prepared.extraction.extracted_sha256,
                    captured_part_id=None,
                    captured_scope_epoch=1,
                    available_target_modes=("personal",),
                    rule_version="p6.1-deterministic-ingest-v1",
                    title="Old preview",
                    size_bytes=prepared.extraction.extracted_size_bytes,
                    warnings=(),
                    deterministic_findings=(),
                    expires_at_ms=created_at + PREVIEW_TTL_MS,
                    created_at_ms=created_at,
                    completed_at_ms=created_at,
                ),
            )
    finally:
        connection.close()
    return preview_id, ids.artifact_id


def test_manual_preview_commits_artifact_preview_dedup_audit_and_outbox_atomically(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    personal_id = _seed_actor(factory)
    stager = _Stager()
    result = _service(factory, stager).create_manual_preview(_command())

    assert result.status == "ready"
    assert result.ready_result is not None
    assert result.ready_result["title"] == "Ready"
    assert result.ready_result["available_target_modes"] == ["personal"]
    assert result.captured_part_id is None
    assert stager.calls == 1 and stager.leases[0].closed

    connection = factory.open(read_only=True)
    try:
        assert connection.execute("SELECT count(*) FROM source_artifact").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM extraction_snapshot").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM publication_preview").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM operation_dedup").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM audit_event").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM outbox_event").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM knowledge_item").fetchone()[0] == 0
        audit = connection.execute(
            "SELECT space_id,request_meta_json FROM audit_event"
        ).fetchone()
        assert audit["space_id"] == personal_id
        assert audit["request_meta_json"] == '{"correlation_id":"p4.2-test"}'
    finally:
        connection.close()


def test_manual_preview_exact_retry_returns_frozen_result_without_restaging(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _seed_actor(factory)
    stager = _Stager()
    service = _service(factory, stager)
    first = service.create_manual_preview(_command())
    retry = service.create_manual_preview(
        replace(_command(), request_id=_uuid("different-request"))
    )
    assert retry.preview_id == first.preview_id
    assert retry.ready_result_json == first.ready_result_json
    assert retry.ready_result["request_id"] == _uuid("request")
    assert stager.calls == 1


def test_manual_preview_operation_drift_fails_before_restaging(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _seed_actor(factory)
    stager = _Stager()
    service = _service(factory, stager)
    service.create_manual_preview(_command())
    with pytest.raises(PublicationPreviewIdempotencyError):
        service.create_manual_preview(_command(markdown="# Changed\n"))
    assert stager.calls == 1


def test_preview_freezes_exact_current_part_target_modes_and_membership_epoch(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _seed_actor(factory)
    part_id, _, epoch = _assign_actor_to_part(factory)
    enabled = PublicationPreviewService(
        factory,
        ScopeResolver(
            FeatureFlags(
                core_enabled=True,
                personal_reads=True,
                personal_writes=True,
                part_reads=True,
                part_writes=True,
            )
        ),
        _Stager(),
    ).create_manual_preview(replace(_command(), actor_scope_epoch=epoch))
    assert enabled.captured_part_id == part_id
    assert enabled.captured_scope_epoch == epoch
    assert enabled.ready_result["available_target_modes"] == [
        "personal",
        "part",
        "both",
    ]

    disabled = PublicationPreviewService(
        factory,
        ScopeResolver(
            FeatureFlags(
                core_enabled=True,
                personal_reads=True,
                personal_writes=True,
                part_reads=True,
                part_writes=False,
            )
        ),
        _Stager(),
    ).create_manual_preview(
        replace(
            _command(),
            request_id=_uuid("part-disabled-request"),
            operation_id=_uuid("part-disabled-operation"),
            actor_scope_epoch=epoch,
        )
    )
    assert disabled.captured_part_id == part_id
    assert disabled.captured_scope_epoch == epoch
    assert disabled.ready_result["available_target_modes"] == ["personal"]


def test_chat_preview_reads_canonical_message_range_and_replays_immutable_snapshot(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _seed_actor(factory)
    conversation_id = _seed_conversation(factory)
    stager = _Stager()
    service = _service(factory, stager)
    first = service.create_chat_preview(_chat_command(conversation_id))
    assert first.status == "ready"
    assert first.ready_result["title"] == "Promotion source"

    connection = factory.open()
    try:
        with transaction(connection):
            connection.execute(
                """INSERT INTO conversation_message(
                     id,conversation_id,sequence_no,role,content_json,created_at
                   ) VALUES (?,?,3,'user','{"content":"Later","role":"user"}',203)""",
                (_uuid("chat-message-3"), conversation_id),
            )
    finally:
        connection.close()
    replay = service.create_chat_preview(
        _chat_command(conversation_id, request_id=_uuid("chat-replay-request"))
    )
    assert replay.ready_result_json == first.ready_result_json
    assert stager.calls == 1

    connection = factory.open(read_only=True)
    try:
        artifact = connection.execute(
            """SELECT origin_type,origin_key,origin_revision_key,raw_sha256
                 FROM source_artifact"""
        ).fetchone()
        assert artifact["origin_type"] == "chat"
        assert artifact["origin_key"] == f"chat:{conversation_id}:1-2"
        assert len(artifact["origin_revision_key"]) == 64
        body = connection.execute(
            "SELECT content_md FROM extraction_snapshot_body"
        ).fetchone()[0]
        assert "## User\n\nQuestion" in body
        assert "## Assistant\n\nAnswer" in body
        assert "Later" not in body
        audit = json.loads(
            connection.execute("SELECT details_json FROM audit_event").fetchone()[0]
        )
        assert audit["source_type"] == "chat"
        assert audit["first_sequence_no"] == 1
        assert audit["last_sequence_no"] == 2
    finally:
        connection.close()


@pytest.mark.scenario("PUB-007")
def test_chat_snapshot_drift_between_stage_and_commit_fails_without_effect(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _seed_actor(factory)
    conversation_id = _seed_conversation(factory)

    def archive() -> None:
        connection = factory.open()
        try:
            with transaction(connection):
                connection.execute(
                    """UPDATE conversation SET status='archived',updated_at=300,
                         row_version=row_version+1 WHERE id=?""",
                    (conversation_id,),
                )
        finally:
            connection.close()

    stager = _CallbackStager(archive)
    service = _service(factory, stager)
    with pytest.raises(PublicationPreviewAuthorizationError):
        service.create_chat_preview(_chat_command(conversation_id))
    assert stager.leases[0].closed
    connection = factory.open(read_only=True)
    try:
        assert connection.execute("SELECT count(*) FROM source_artifact").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM publication_preview").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM operation_dedup").fetchone()[0] == 0
    finally:
        connection.close()


def test_chat_preview_rejects_missing_or_oversized_range_before_staging(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _seed_actor(factory)
    conversation_id = _seed_conversation(factory)
    stager = _Stager()
    service = _service(factory, stager)
    with pytest.raises(PublicationPreviewValidationError):
        service.create_chat_preview(
            _chat_command(conversation_id, last_sequence_no=101)
        )
    with pytest.raises(PublicationPreviewValidationError):
        service.create_chat_preview(
            _chat_command(conversation_id, last_sequence_no=3)
        )
    assert stager.calls == 0


def test_chat_preview_conceals_missing_conversation_as_authorization_failure(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _seed_actor(factory)
    stager = _Stager()
    with pytest.raises(PublicationPreviewAuthorizationError):
        _service(factory, stager).create_chat_preview(
            _chat_command(_uuid("foreign-or-missing-conversation"))
        )
    assert stager.calls == 0


def test_notion_preview_binds_authorized_page_snapshot_and_exact_replay(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _seed_actor(factory)
    provider = _NotionSnapshots()
    stager = _Stager()
    service = _service(factory, stager, notion_snapshots=provider)
    first = service.create_notion_preview(_notion_command())
    replay = service.create_notion_preview(
        _notion_command(request_id=_uuid("notion-replay-request"))
    )
    assert first.status == "ready"
    assert replay.ready_result_json == first.ready_result_json
    assert provider.calls == 1 and stager.calls == 1

    connection = factory.open(read_only=True)
    try:
        artifact = connection.execute(
            """SELECT origin_type,origin_key,origin_revision_key,raw_sha256
                 FROM source_artifact"""
        ).fetchone()
        assert tuple(artifact)[:2] == ("notion", "notion:actor:page-1")
        assert len(artifact["origin_revision_key"]) == 64
        audit = json.loads(
            connection.execute("SELECT details_json FROM audit_event").fetchone()[0]
        )
        assert audit["source_type"] == "notion"
        assert audit["notion_page_id"] == "page-1"
        assert audit["notion_owner_user_id"] == _ACTOR
        assert audit["notion_revision"] == 7
    finally:
        connection.close()


@pytest.mark.scenario("PUB-007")
def test_notion_snapshot_expected_hash_and_adapter_evidence_fail_closed(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _seed_actor(factory)
    provider = _NotionSnapshots()
    stager = _Stager()
    service = _service(factory, stager, notion_snapshots=provider)
    with pytest.raises(PublicationPreviewIdempotencyError):
        service.create_notion_preview(
            _notion_command(expected_source_snapshot_sha256="f" * 64)
        )
    assert stager.calls == 0

    class _TamperedNotion(_NotionSnapshots):
        def capture(self, **kwargs):
            return replace(super().capture(**kwargs), content_sha256="0" * 64)

    with pytest.raises(PublicationPreviewStagingError):
        _service(
            factory, _Stager(), notion_snapshots=_TamperedNotion()
        ).create_notion_preview(
            _notion_command(operation_id=_uuid("tampered-notion-operation"))
        )
    connection = factory.open(read_only=True)
    try:
        assert connection.execute("SELECT count(*) FROM source_artifact").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM publication_preview").fetchone()[0] == 0
    finally:
        connection.close()


def test_upload_preview_atomically_stores_raw_source_job_and_queued_result(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    personal_id = _seed_actor(factory)
    stager = _Stager()
    result = _service(factory, stager).create_upload_preview(_upload_command())
    assert result.status == "queued"
    assert result.status_url.endswith(result.preview_id)
    assert stager.calls == 1 and stager.leases[0].closed

    connection = factory.open(read_only=True)
    try:
        preview = connection.execute(
            "SELECT * FROM publication_preview WHERE id=?", (result.preview_id,)
        ).fetchone()
        assert preview["status"] == "queued"
        assert preview["durable_job_id"] == result.job_id
        assert preview["extraction_snapshot_id"] is None
        job = connection.execute(
            "SELECT * FROM durable_job WHERE id=?", (result.job_id,)
        ).fetchone()
        assert job["state"] == "queued"
        assert job["space_id"] == personal_id
        assert connection.execute("SELECT count(*) FROM extraction_snapshot").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM operation_dedup").fetchone()[0] == 2
        assert connection.execute("SELECT count(*) FROM audit_event").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM outbox_event").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM knowledge_item").fetchone()[0] == 0
    finally:
        connection.close()


@pytest.mark.scenario("UPLOAD-004")
@pytest.mark.parametrize(
    "field_name",
    (
        "sha256",
        "size_bytes",
        "media_type",
        "relative_path",
        "created_at_ms",
        "verified_at_ms",
    ),
)
def test_upload_preview_freezes_exact_stager_evidence_before_transaction(
    isolated_knowledge_layout,
    field_name: str,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _seed_actor(factory)
    stager = _DriftingEvidenceStager(field_name)

    with pytest.raises(
        PublicationPreviewStagingError,
        match="invalid physical evidence",
    ):
        _service(factory, stager).create_upload_preview(_upload_command())

    assert stager.leases[0].closed is True
    connection = factory.open(read_only=True)
    try:
        for table in (
            "source_artifact",
            "publication_preview",
            "durable_job",
            "operation_dedup",
            "audit_event",
            "outbox_event",
        ):
            assert connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
    finally:
        connection.close()


@pytest.mark.scenario("UPLOAD-004")
@pytest.mark.parametrize(
    "field_name",
    (
        "sha256",
        "size_bytes",
        "media_type",
        "relative_path",
        "created_at_ms",
        "verified_at_ms",
    ),
)
def test_canonical_source_loader_rejects_drifted_no_follow_evidence(
    isolated_knowledge_layout,
    field_name: str,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _seed_actor(factory)
    command = _upload_command(content=b"loader evidence\n")
    _service(factory, _Stager()).create_upload_preview(command)
    connection = factory.open(read_only=True)
    try:
        row = connection.execute(
            """SELECT id,raw_sha256,raw_size_bytes,media_type
                 FROM source_artifact"""
        ).fetchone()
        before = tuple(row)
        artifact_id = str(row["id"])
        raw_sha256 = str(row["raw_sha256"])
        raw_size_bytes = int(row["raw_size_bytes"])
        media_type = str(row["media_type"])
    finally:
        connection.close()
    verifier = _ExistingVerifier(command.content, field_name)

    with pytest.raises(
        PermanentJobExecutionError,
        match="inconsistent evidence",
    ):
        CanonicalPublicationSourceLoader(factory, verifier).load(
            artifact_id=artifact_id,
            expected_sha256=raw_sha256,
            expected_size=raw_size_bytes,
            expected_media_type=media_type,
        )

    assert verifier.lease.closed is True
    connection = factory.open(read_only=True)
    try:
        assert tuple(
            connection.execute(
                """SELECT id,raw_sha256,raw_size_bytes,media_type
                     FROM source_artifact"""
            ).fetchone()
        ) == before
    finally:
        connection.close()


@pytest.mark.scenario("UPLOAD-004")
def test_canonical_source_loader_returns_exact_frozen_source(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _seed_actor(factory)
    command = _upload_command(content=b"valid loader evidence\n")
    _service(factory, _Stager()).create_upload_preview(command)
    connection = factory.open(read_only=True)
    try:
        row = connection.execute(
            """SELECT id,raw_sha256,raw_size_bytes,media_type
                 FROM source_artifact"""
        ).fetchone()
        values = tuple(row)
    finally:
        connection.close()
    verifier = _ExistingVerifier(command.content)

    loaded = CanonicalPublicationSourceLoader(factory, verifier).load(
        artifact_id=str(values[0]),
        expected_sha256=str(values[1]),
        expected_size=int(values[2]),
        expected_media_type=str(values[3]),
    )
    try:
        assert loaded.content == command.content
        assert loaded.media_type == command.media_type
        assert loaded.original_filename == command.original_filename
    finally:
        loaded.lease.close()
    assert verifier.lease.closed is True


def test_upload_preview_retry_returns_original_queued_response_without_restaging(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _seed_actor(factory)
    stager = _Stager()
    service = _service(factory, stager)
    first = service.create_upload_preview(_upload_command())
    replay = service.create_upload_preview(
        replace(_upload_command(), request_id=_uuid("new-upload-request"))
    )
    assert replay == first
    assert replay.request_id == _uuid("upload-request")
    assert stager.calls == 1


def test_upload_preview_operation_drift_fails_before_restaging(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _seed_actor(factory)
    stager = _Stager()
    service = _service(factory, stager)
    service.create_upload_preview(_upload_command())
    with pytest.raises(PublicationPreviewIdempotencyError):
        service.create_upload_preview(_upload_command(content=b"changed"))
    assert stager.calls == 1


@pytest.mark.scenario("PUB-012")
def test_durable_upload_extraction_survives_process_boundary_and_commits_ready(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _seed_actor(factory)
    flags = FeatureFlags(
        core_enabled=True,
        personal_reads=True,
        personal_writes=True,
    )
    scope = ScopeResolver(flags)
    stager = _Stager()
    content = b"# Uploaded\r\n\r\nBody\r\n"
    queued = PublicationPreviewService(factory, scope, stager).create_upload_preview(
        replace(
            _upload_command(content=content),
            original_filename="source.md",
            media_type="text/markdown",
        )
    )
    query = GetPublicationPreviewQuery(
        actor_user_id=_ACTOR,
        actor_scope_epoch=1,
        preview_id=queued.preview_id,
    )
    polled_queued = PublicationPreviewService(
        factory, ScopeResolver(FeatureFlags(core_enabled=True)), _Stager()
    ).get_preview(query)
    assert polled_queued == queued.to_mapping()
    with pytest.raises(PublicationPreviewAuthorizationError):
        PublicationPreviewService(
            factory, ScopeResolver(FeatureFlags(core_enabled=True)), _Stager()
        ).get_preview(replace(query, actor_scope_epoch=2))
    loader = _SourceLoader(content)
    handler = PublicationPreviewExtractionHandler(
        factory,
        loader,
        Utf8PublicationExtractor(),
    )
    kernel = DurableWorkKernel(
        factory,
        HandlerRegistry((handler.registered_handler(),)),
        scope_resolver=scope,
    )
    recovery, claim = kernel.recover_and_claim(
        claim_owner="preview-worker-1", lease_ms=30_000
    )
    assert recovery.retried_ids == () and claim is not None
    run = kernel.run_claim(claim)
    assert run.job.state == "succeeded"
    assert run.job.result == {
        "preview_id": queued.preview_id,
        "schema_version": 1,
        "source_snapshot_sha256": hashlib.sha256(content).hexdigest(),
        "status": "ready",
    }
    assert loader.calls == 1 and loader.leases[0].closed

    restarted_handler = PublicationPreviewExtractionHandler(
        factory,
        _SourceLoader(content),
        Utf8PublicationExtractor(),
    )
    restarted = DurableWorkKernel(
        factory,
        HandlerRegistry((restarted_handler.registered_handler(),)),
        scope_resolver=ScopeResolver(flags),
    )
    restarted.verify_registry_coverage()
    _, next_claim = restarted.recover_and_claim(
        claim_owner="preview-worker-after-restart", lease_ms=30_000
    )
    assert next_claim is None
    polled_ready = PublicationPreviewService(
        factory, ScopeResolver(flags), _Stager()
    ).get_preview(query)
    assert polled_ready["status"] == "ready"
    assert polled_ready["job_id"] == queued.job_id
    assert polled_ready["source_snapshot_sha256"] == hashlib.sha256(content).hexdigest()
    assert polled_ready["ready_result_schema_version"] == 1
    connection = factory.open(read_only=True)
    try:
        preview = connection.execute(
            "SELECT * FROM publication_preview WHERE id=?", (queued.preview_id,)
        ).fetchone()
        assert preview["status"] == "ready"
        assert preview["source_snapshot_sha256"] == hashlib.sha256(content).hexdigest()
        assert connection.execute("SELECT count(*) FROM extraction_snapshot_body").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM knowledge_item").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM audit_event").fetchone()[0] == 3
        assert connection.execute("SELECT count(*) FROM outbox_event").fetchone()[0] == 3
    finally:
        connection.close()


@pytest.mark.scenario("UPLOAD-005")
@pytest.mark.scenario("UPLOAD-006")
def test_rich_worker_atomically_commits_markdown_and_embedded_asset_child(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _seed_actor(factory)
    flags = FeatureFlags(
        core_enabled=True,
        personal_reads=True,
        personal_writes=True,
    )
    scope = ScopeResolver(flags)
    content = _rich_docx_fixture()
    queued = PublicationPreviewService(factory, scope, _Stager()).create_upload_preview(
        replace(
            _upload_command(content=content),
            original_filename="canonical.docx",
            media_type=(
                "application/vnd.openxmlformats-officedocument."
                "wordprocessingml.document"
            ),
        )
    )
    asset_stager = _Stager()
    handler = PublicationPreviewExtractionHandler(
        factory,
        _SourceLoader(content, original_filename="canonical.docx"),
        CanonicalRichPublicationExtractor(),
        asset_stager=asset_stager,
    )
    kernel = DurableWorkKernel(
        factory,
        HandlerRegistry((handler.registered_handler(),)),
        scope_resolver=scope,
    )
    _recovery, claim = kernel.recover_and_claim(
        claim_owner="rich-preview-worker",
        lease_ms=30_000,
    )
    assert claim is not None
    completed = kernel.run_claim(claim)
    assert completed.job.state == "succeeded"
    assert completed.job.result["status"] == "ready"
    assert asset_stager.calls == 1
    assert all(lease.closed for lease in asset_stager.leases)

    connection = factory.open(read_only=True)
    try:
        preview = connection.execute(
            "SELECT artifact_id,extraction_snapshot_id,status FROM publication_preview WHERE id=?",
            (queued.preview_id,),
        ).fetchone()
        assert preview is not None and preview["status"] == "ready"
        body = connection.execute(
            """SELECT content_md FROM extraction_snapshot_body
                 WHERE snapshot_id=?""",
            (preview["extraction_snapshot_id"],),
        ).fetchone()[0]
        asset = connection.execute(
            """SELECT b.artifact_id,b.content_sha256,b.size_bytes,b.role,
                      b.logical_name,b.media_type,b.ordinal,
                      bb.content_sha256 AS body_sha256
                 FROM artifact_blob b JOIN artifact_blob_body bb
                   ON bb.artifact_blob_id=b.id
                WHERE b.artifact_id=? AND b.role='embedded_asset'""",
            (preview["artifact_id"],),
        ).fetchone()
        assert asset is not None
        assert asset["artifact_id"] == preview["artifact_id"]
        assert asset["role"] == "embedded_asset"
        assert asset["logical_name"] == "asset-001-docx_image_001.png"
        assert asset["media_type"] == "image/png"
        assert asset["ordinal"] == 1
        embedded_bytes = b"\x89PNG\r\n\x1a\nworker-asset"
        assert asset["size_bytes"] == len(embedded_bytes)
        assert hashlib.sha256(embedded_bytes).hexdigest() == asset["content_sha256"]
        assert asset["body_sha256"] == asset["content_sha256"]
        assert "Durable rich extraction" in body
        assert f"(asset://sha256/{asset['content_sha256']})" in body
    finally:
        connection.close()


@pytest.mark.scenario("UPLOAD-009")
@pytest.mark.scenario("UPLOAD-010")
def test_upload_reupload_matches_exact_filename_but_changed_filename_is_new_item(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _seed_actor(factory)
    flags = FeatureFlags(
        core_enabled=True,
        personal_reads=True,
        personal_writes=True,
    )
    scope = ScopeResolver(flags)
    content = b"# Ordinary upload\n\nExact body with filename policy.\n"
    preview_service = PublicationPreviewService(factory, scope, _Stager())
    queued = []
    for label, filename in (
        ("first", "policy.md"),
        ("same-name", "policy.md"),
        ("changed-name", "renamed-policy.md"),
    ):
        queued.append(
            preview_service.create_upload_preview(
                replace(
                    _upload_command(content=content),
                    request_id=_uuid(f"filename-policy:{label}:preview-request"),
                    operation_id=_uuid(f"filename-policy:{label}:preview-operation"),
                    original_filename=filename,
                    media_type="text/markdown",
                    title="Filename policy",
                )
            )
        )

    class _FilenameLoader:
        def load(
            self,
            *,
            artifact_id: str,
            expected_sha256: str,
            expected_size: int,
            expected_media_type: str,
        ) -> LoadedPublicationSource:
            assert expected_sha256 == hashlib.sha256(content).hexdigest()
            assert expected_size == len(content)
            assert expected_media_type == "text/markdown"
            connection = factory.open(read_only=True)
            try:
                filename = connection.execute(
                    "SELECT original_filename FROM source_artifact WHERE id=?",
                    (artifact_id,),
                ).fetchone()[0]
            finally:
                connection.close()
            return LoadedPublicationSource(
                content=content,
                media_type=expected_media_type,
                original_filename=str(filename),
                lease=_Lease(),
            )

    extraction_kernel = DurableWorkKernel(
        factory,
        HandlerRegistry(
            (
                PublicationPreviewExtractionHandler(
                    factory,
                    _FilenameLoader(),
                    Utf8PublicationExtractor(),
                ).registered_handler(),
            )
        ),
        scope_resolver=scope,
    )
    for ordinal in (1, 2, 3):
        _recovery, claim = extraction_kernel.recover_and_claim(
            claim_owner=f"filename-policy-extraction-{ordinal}",
            lease_ms=30_000,
        )
        assert claim is not None
        assert extraction_kernel.run_claim(claim).job.state == "succeeded"

    publisher = PublicationService(factory, scope)
    results = []
    for label, preview in zip(
        ("first", "same-name", "changed-name"),
        queued,
        strict=True,
    ):
        results.append(
            publisher.publish(
                PublishPreviewCommand(
                    request_id=_uuid(f"filename-policy:{label}:publish-request"),
                    operation_id=_uuid(f"filename-policy:{label}:publish-operation"),
                    actor_user_id=_ACTOR,
                    actor_scope_epoch=1,
                    preview_id=preview.preview_id,
                    source_snapshot_sha256=hashlib.sha256(content).hexdigest(),
                    target_mode="personal",
                )
            )
        )
    assert [result.targets[0]["outcome"] for result in results] == [
        "created",
        "provenance_added",
        "created",
    ]
    assert results[0].targets[0]["item_id"] == results[1].targets[0]["item_id"]
    assert results[2].targets[0]["item_id"] != results[0].targets[0]["item_id"]
    connection = factory.open(read_only=True)
    try:
        assert connection.execute("SELECT count(*) FROM knowledge_item").fetchone()[0] == 2
        assert connection.execute("SELECT count(*) FROM item_source").fetchone()[0] == 3
        assert tuple(
            row[0]
            for row in connection.execute(
                "SELECT original_filename FROM source_artifact ORDER BY created_at,id"
            ).fetchall()
        ) == ("policy.md", "policy.md", "renamed-policy.md")
    finally:
        connection.close()


def test_unsupported_upload_finishes_as_durable_failed_preview_without_knowledge(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _seed_actor(factory)
    flags = FeatureFlags(
        core_enabled=True,
        personal_reads=True,
        personal_writes=True,
    )
    scope = ScopeResolver(flags)
    content = b"%PDF-fixture"
    queued = PublicationPreviewService(factory, scope, _Stager()).create_upload_preview(
        _upload_command(content=content)
    )
    loader = _SourceLoader(content)
    handler = PublicationPreviewExtractionHandler(
        factory, loader, Utf8PublicationExtractor()
    )
    kernel = DurableWorkKernel(
        factory,
        HandlerRegistry((handler.registered_handler(),)),
        scope_resolver=scope,
    )
    _, claim = kernel.recover_and_claim(
        claim_owner="preview-worker-failed", lease_ms=30_000
    )
    assert claim is not None
    run = kernel.run_claim(claim)
    assert run.job.state == "succeeded"
    assert run.job.result["status"] == "failed"
    polled = PublicationPreviewService(
        factory, ScopeResolver(flags), _Stager()
    ).get_preview(
        GetPublicationPreviewQuery(
            actor_user_id=_ACTOR,
            actor_scope_epoch=1,
            preview_id=queued.preview_id,
        )
    )
    assert polled["status"] == "failed"
    assert polled["job_id"] == queued.job_id
    assert polled["error"] == {
        "code": "UNSUPPORTED_MEDIA_TYPE",
        "message": "The source could not be extracted safely.",
    }
    assert "status_url" not in polled
    connection = factory.open(read_only=True)
    try:
        preview = connection.execute(
            "SELECT * FROM publication_preview WHERE id=?", (queued.preview_id,)
        ).fetchone()
        assert preview["status"] == "failed"
        assert preview["error_json"] == (
            '{"code":"UNSUPPORTED_MEDIA_TYPE",'
            '"message":"The source could not be extracted safely."}'
        )
        extraction = connection.execute(
            "SELECT status,body_retention_status FROM extraction_snapshot"
        ).fetchone()
        assert tuple(extraction) == ("failed", "none")
        assert connection.execute("SELECT count(*) FROM knowledge_item").fetchone()[0] == 0
    finally:
        connection.close()


@pytest.mark.scenario("PUB-012")
def test_queued_preview_cancellation_atomically_cancels_job_and_is_replayable(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _seed_actor(factory)
    scope = ScopeResolver(
        FeatureFlags(
            core_enabled=True,
            personal_reads=True,
            personal_writes=True,
        )
    )
    service = PublicationPreviewService(factory, scope, _Stager())
    queued = service.create_upload_preview(_upload_command())
    command = CancelPublicationPreviewCommand(
        request_id=_uuid("cancel-request"),
        operation_id=_uuid("cancel-operation"),
        actor_user_id=_ACTOR,
        actor_scope_epoch=1,
        preview_id=queued.preview_id,
        request_meta={"correlation_id": "cancel"},
    )
    cancelled = service.cancel_preview(command)
    replay = service.cancel_preview(
        replace(command, request_id=_uuid("cancel-retry-request"))
    )
    assert cancelled.status == replay.status == "cancelled"
    assert cancelled.row_version == replay.row_version == 2
    polled = PublicationPreviewService(factory, scope, _Stager()).get_preview(
        GetPublicationPreviewQuery(
            actor_user_id=_ACTOR,
            actor_scope_epoch=1,
            preview_id=queued.preview_id,
        )
    )
    assert polled["status"] == "cancelled"
    assert polled["job_id"] == queued.job_id
    assert "status_url" not in polled and "error" not in polled
    connection = factory.open(read_only=True)
    try:
        job = connection.execute(
            "SELECT state,cancel_requested FROM durable_job WHERE id=?",
            (queued.job_id,),
        ).fetchone()
        assert tuple(job) == ("cancelled", 1)
        assert connection.execute("SELECT count(*) FROM extraction_snapshot").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM knowledge_item").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM operation_dedup").fetchone()[0] == 3
        assert connection.execute("SELECT count(*) FROM audit_event").fetchone()[0] == 2
        assert connection.execute("SELECT count(*) FROM outbox_event").fetchone()[0] == 2
    finally:
        connection.close()


def test_running_preview_cancellation_wins_before_prepared_effect_commit(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _seed_actor(factory)
    scope = ScopeResolver(
        FeatureFlags(core_enabled=True, personal_reads=True, personal_writes=True)
    )
    service = PublicationPreviewService(factory, scope, _Stager())
    content = b"# Cancel race\n"
    queued = service.create_upload_preview(
        replace(
            _upload_command(content=content),
            original_filename="cancel.md",
            media_type="text/markdown",
        )
    )
    cancel = CancelPublicationPreviewCommand(
        request_id=_uuid("running-cancel-request"),
        operation_id=_uuid("running-cancel-operation"),
        actor_user_id=_ACTOR,
        actor_scope_epoch=1,
        preview_id=queued.preview_id,
    )
    loader = _SourceLoader(content)
    handler = PublicationPreviewExtractionHandler(
        factory, loader, _CancelDuringExtractor(service, cancel)
    )
    kernel = DurableWorkKernel(
        factory,
        HandlerRegistry((handler.registered_handler(),)),
        scope_resolver=scope,
    )
    _, claim = kernel.recover_and_claim(
        claim_owner="preview-worker-cancel-race", lease_ms=30_000
    )
    assert claim is not None
    run = kernel.run_claim(claim)
    assert run.job.state == "cancelled"
    assert loader.leases[0].closed

    connection = factory.open(read_only=True)
    try:
        assert connection.execute(
            "SELECT status FROM publication_preview WHERE id=?", (queued.preview_id,)
        ).fetchone()[0] == "cancelled"
        assert connection.execute("SELECT count(*) FROM extraction_snapshot").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM knowledge_item").fetchone()[0] == 0
    finally:
        connection.close()


def test_permanent_extractor_failure_atomically_closes_job_and_preview(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _seed_actor(factory)
    scope = ScopeResolver(
        FeatureFlags(core_enabled=True, personal_reads=True, personal_writes=True)
    )
    content = b"# Broken extractor\n"
    queued = PublicationPreviewService(factory, scope, _Stager()).create_upload_preview(
        replace(
            _upload_command(content=content),
            original_filename="broken.md",
            media_type="text/markdown",
        )
    )
    handler = PublicationPreviewExtractionHandler(
        factory, _SourceLoader(content), _BrokenExtractor()
    )
    kernel = DurableWorkKernel(
        factory,
        HandlerRegistry((handler.registered_handler(),)),
        scope_resolver=scope,
    )
    _, claim = kernel.recover_and_claim(
        claim_owner="preview-worker-permanent-failure", lease_ms=30_000
    )
    assert claim is not None
    run = kernel.run_claim(claim)
    assert run.job.state == "failed"

    connection = factory.open(read_only=True)
    try:
        preview = connection.execute(
            "SELECT status,error_json FROM publication_preview WHERE id=?",
            (queued.preview_id,),
        ).fetchone()
        assert preview["status"] == "failed"
        assert "JOB_EXECUTION_FAILED" in preview["error_json"]
        assert connection.execute("SELECT count(*) FROM extraction_snapshot").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM knowledge_item").fetchone()[0] == 0
    finally:
        connection.close()


def test_retry_exhaustion_closes_preview_when_job_becomes_dead(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _seed_actor(factory)
    scope = ScopeResolver(
        FeatureFlags(core_enabled=True, personal_reads=True, personal_writes=True)
    )
    queued = PublicationPreviewService(factory, scope, _Stager()).create_upload_preview(
        _upload_command()
    )
    handler = PublicationPreviewExtractionHandler(
        factory, _RetryingSourceLoader(), Utf8PublicationExtractor()
    )
    kernel = DurableWorkKernel(
        factory,
        HandlerRegistry((handler.registered_handler(),)),
        scope_resolver=scope,
    )
    states = []
    for attempt in range(3):
        _, claim = kernel.recover_and_claim(
            claim_owner=f"preview-worker-retry-{attempt}", lease_ms=30_000
        )
        assert claim is not None
        states.append(kernel.run_claim(claim).job.state)
    assert states == ["retry", "retry", "dead"]

    connection = factory.open(read_only=True)
    try:
        preview = connection.execute(
            "SELECT status,error_json FROM publication_preview WHERE id=?",
            (queued.preview_id,),
        ).fetchone()
        assert preview["status"] == "failed"
        assert "JOB_EXECUTION_RETRYABLE" in preview["error_json"]
        assert connection.execute("SELECT count(*) FROM extraction_snapshot").fetchone()[0] == 0
    finally:
        connection.close()


def test_expired_claim_recovery_converges_dead_job_and_preview(
    isolated_knowledge_layout,
    monkeypatch,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _seed_actor(factory)
    scope = ScopeResolver(
        FeatureFlags(core_enabled=True, personal_reads=True, personal_writes=True)
    )
    content = b"# Expired claim\n"
    queued = PublicationPreviewService(factory, scope, _Stager()).create_upload_preview(
        replace(
            _upload_command(content=content),
            original_filename="expired.md",
            media_type="text/markdown",
        )
    )
    handler = PublicationPreviewExtractionHandler(
        factory, _SourceLoader(content), Utf8PublicationExtractor()
    )
    kernel = DurableWorkKernel(
        factory,
        HandlerRegistry((handler.registered_handler(),)),
        scope_resolver=scope,
    )
    connection = factory.open(read_only=True)
    try:
        created_at = connection.execute(
            "SELECT created_at FROM durable_job WHERE id=?", (queued.job_id,)
        ).fetchone()[0]
    finally:
        connection.close()
    clock = [created_at + 1]
    monkeypatch.setattr(
        "code_assist_v1.knowledge_core.worker.db_now_ms",
        lambda connection: clock[0],
    )
    recovery, claim = kernel.recover_and_claim(
        claim_owner="preview-expired-worker-1", lease_ms=1
    )
    assert recovery.retried_ids == recovery.dead_ids == ()
    assert claim is not None
    for attempt in range(3):
        connection = factory.open()
        try:
            with transaction(connection):
                JobRepository().start(
                    connection, claim.fence, now_ms=clock[0]
                )
        finally:
            connection.close()
        clock[0] += 2
        recovery, next_claim = kernel.recover_and_claim(
            claim_owner=f"preview-expired-worker-{attempt + 2}", lease_ms=1
        )
        if attempt < 2:
            assert recovery.retried_ids == (queued.job_id,)
            assert recovery.dead_ids == ()
            assert next_claim is not None
            claim = next_claim
        else:
            assert recovery.dead_ids == (queued.job_id,)
            assert recovery.retried_ids == ()
            assert next_claim is None

    connection = factory.open(read_only=True)
    try:
        preview = PublicationPreviewRepository().get(connection, queued.preview_id)
        assert connection.execute(
            "SELECT state FROM durable_job WHERE id=?", (queued.job_id,)
        ).fetchone()[0] == "dead"
        assert preview is not None and preview.status == "failed"
        assert preview.error == {
            "code": "JOB_CLAIM_EXPIRED",
            "message": "The source extraction job ended without a result.",
        }
        assert connection.execute("SELECT count(*) FROM extraction_snapshot").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM knowledge_item").fetchone()[0] == 0
    finally:
        connection.close()


@pytest.mark.scenario("PUB-013")
def test_authorization_change_after_prepare_closes_preview_without_effect(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _seed_actor(factory)
    scope = ScopeResolver(
        FeatureFlags(core_enabled=True, personal_reads=True, personal_writes=True)
    )
    content = b"# Authorization changed\n"
    queued = PublicationPreviewService(factory, scope, _Stager()).create_upload_preview(
        replace(
            _upload_command(content=content),
            original_filename="authorization.md",
            media_type="text/markdown",
        )
    )
    handler = PublicationPreviewExtractionHandler(
        factory, _SourceLoader(content), Utf8PublicationExtractor()
    )
    kernel = DurableWorkKernel(
        factory,
        HandlerRegistry((handler.registered_handler(),)),
        scope_resolver=scope,
    )
    _, claim = kernel.recover_and_claim(
        claim_owner="preview-worker-auth-change", lease_ms=30_000
    )
    assert claim is not None
    connection = factory.open()
    try:
        with transaction(connection):
            started = JobRepository().start(
                connection, claim.fence, now_ms=db_now_ms(connection)
            )
    finally:
        connection.close()
    prepared = handler.prepare(started)
    connection = factory.open()
    try:
        with transaction(connection):
            actor = UserRepository().require(connection, _ACTOR)
            UserRepository().transition_account_status(
                connection,
                user_id=_ACTOR,
                target_status="disabled",
                expected_row_version=actor.row_version,
                changed_at_ms=db_now_ms(connection),
            )
    finally:
        connection.close()

    run = kernel.commit_prepared(claim.fence, prepared)
    assert run.job.state == "cancelled"
    connection = factory.open(read_only=True)
    try:
        assert connection.execute(
            "SELECT status FROM publication_preview WHERE id=?", (queued.preview_id,)
        ).fetchone()[0] == "cancelled"
        assert connection.execute("SELECT count(*) FROM extraction_snapshot").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM knowledge_item").fetchone()[0] == 0
    finally:
        connection.close()


def test_disabled_publication_fails_before_staging_or_database_effect(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _seed_actor(factory)
    stager = _Stager()
    service = PublicationPreviewService(
        factory,
        ScopeResolver(FeatureFlags(core_enabled=True, personal_reads=True)),
        stager,
    )
    with pytest.raises(PublicationPreviewAuthorizationError):
        service.create_manual_preview(_command())
    assert stager.calls == 0
    connection = factory.open(read_only=True)
    try:
        assert connection.execute("SELECT count(*) FROM publication_preview").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM operation_dedup").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM audit_event").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM outbox_event").fetchone()[0] == 0
    finally:
        connection.close()


class _FailAfterArtifactRepository(ArtifactRepository):
    def put_source_bundle(self, connection, bundle):
        super().put_source_bundle(connection, bundle)
        raise RuntimeError("failpoint")


def test_service_owned_transaction_rolls_back_every_database_effect_on_failure(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _seed_actor(factory)
    stager = _Stager()
    service = _service(
        factory,
        stager,
        artifacts=_FailAfterArtifactRepository(),
    )
    with pytest.raises(RuntimeError, match="failpoint"):
        service.create_manual_preview(_command())
    assert stager.calls == 1 and stager.leases[0].closed
    connection = factory.open(read_only=True)
    try:
        for table in (
            "blob_object",
            "source_artifact",
            "artifact_blob",
            "extraction_snapshot",
            "publication_preview",
            "operation_dedup",
            "audit_event",
            "outbox_event",
        ):
            assert connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
    finally:
        connection.close()


@pytest.mark.scenario("PUB-013")
def test_expiry_and_thirty_day_orphan_cleanup_are_durable_exact_replays(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    personal_id = _seed_actor(factory)
    preview_id, artifact_id = _seed_old_ready_preview(factory)
    service = _service(factory, _Stager())

    expired = service.expire_preview(preview_id)
    assert expired.status == "expired" and expired.row_version == 2
    assert service.expire_preview(preview_id) == expired
    polled_expired = service.get_preview(
        GetPublicationPreviewQuery(
            actor_user_id=_ACTOR,
            actor_scope_epoch=1,
            preview_id=preview_id,
        )
    )
    assert polled_expired["status"] == "expired"
    assert polled_expired["source_snapshot_sha256"] == expired.source_snapshot_sha256
    assert polled_expired["ready_result_schema_version"] == 1

    cleaned = service.cleanup_orphan_preview_bodies(artifact_id)
    assert cleaned.changed
    assert cleaned.deleted_artifact_body_count == 1
    assert cleaned.deleted_extraction_body_count == 1
    replay = service.cleanup_orphan_preview_bodies(artifact_id)
    assert not replay.changed
    assert replay.deleted_artifact_body_count == 0
    assert replay.deleted_extraction_body_count == 0

    connection = factory.open(read_only=True)
    try:
        artifact = connection.execute(
            """SELECT raw_sha256,raw_size_bytes,body_retention_status,
                      body_retention_reason,body_purged_at,metadata_json
                 FROM source_artifact WHERE id=?""",
            (artifact_id,),
        ).fetchone()
        assert artifact["body_retention_status"] == "purged"
        assert artifact["body_retention_reason"] == "orphan_ingestion_cleanup"
        assert artifact["body_purged_at"] is not None
        assert len(artifact["raw_sha256"]) == 64
        assert artifact["raw_size_bytes"] > 0
        assert '"preparation_sha256"' in artifact["metadata_json"]
        assert connection.execute(
            "SELECT count(*) FROM artifact_blob_body"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT count(*) FROM extraction_snapshot_body"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT body_retention_status FROM artifact_blob"
        ).fetchone()[0] == "purged"
        assert connection.execute(
            "SELECT status,body_retention_status FROM extraction_snapshot"
        ).fetchone()[:] == ("ready", "purged")
        assert connection.execute("SELECT count(*) FROM blob_object").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM publication_preview").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM audit_event").fetchone()[0] == 2
        assert connection.execute("SELECT count(*) FROM outbox_event").fetchone()[0] == 2
        assert connection.execute(
            "SELECT count(*) FROM audit_event WHERE actor_kind='system'"
        ).fetchone()[0] == 2
        assert connection.execute(
            "SELECT count(*) FROM outbox_event WHERE space_id=?", (personal_id,)
        ).fetchone()[0] == 2
        assert connection.execute("SELECT count(*) FROM knowledge_item").fetchone()[0] == 0
    finally:
        connection.close()


def test_orphan_cleanup_rejects_active_preview_and_unelapsed_grace(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _seed_actor(factory)
    old_preview_id, old_artifact_id = _seed_old_ready_preview(factory)
    service = _service(factory, _Stager())

    with pytest.raises(ArtifactBundleConflictError):
        service.cleanup_orphan_preview_bodies(old_artifact_id)
    service.expire_preview(old_preview_id)

    current = service.create_manual_preview(_command())
    cancelled = service.cancel_preview(
        CancelPublicationPreviewCommand(
            request_id=_uuid("current-cancel-request"),
            operation_id=_uuid("current-cancel-operation"),
            actor_user_id=_ACTOR,
            actor_scope_epoch=1,
            preview_id=current.preview_id,
        )
    )
    assert cancelled.status == "cancelled"
    with pytest.raises(ArtifactBundleConflictError):
        service.cleanup_orphan_preview_bodies(current.artifact_id)

    connection = factory.open(read_only=True)
    try:
        assert connection.execute(
            "SELECT count(*) FROM artifact_blob_body"
        ).fetchone()[0] == 2
        assert connection.execute(
            "SELECT count(*) FROM extraction_snapshot_body"
        ).fetchone()[0] == 2
        assert connection.execute(
            "SELECT count(*) FROM source_artifact WHERE body_retention_status='purged'"
        ).fetchone()[0] == 0
    finally:
        connection.close()


class _FailAfterCleanupRepository(ArtifactRepository):
    def cleanup_orphan_bodies(self, connection, *, artifact_id, purged_at_ms):
        super().cleanup_orphan_bodies(
            connection, artifact_id=artifact_id, purged_at_ms=purged_at_ms
        )
        raise RuntimeError("cleanup-failpoint")


def test_cleanup_service_rolls_back_body_deletions_on_unexpected_failure(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _seed_actor(factory)
    preview_id, artifact_id = _seed_old_ready_preview(factory)
    service = _service(factory, _Stager())
    service.expire_preview(preview_id)
    failing = _service(
        factory, _Stager(), artifacts=_FailAfterCleanupRepository()
    )

    with pytest.raises(RuntimeError, match="cleanup-failpoint"):
        failing.cleanup_orphan_preview_bodies(artifact_id)

    connection = factory.open(read_only=True)
    try:
        assert connection.execute(
            "SELECT body_retention_status FROM source_artifact WHERE id=?",
            (artifact_id,),
        ).fetchone()[0] == "present"
        assert connection.execute(
            "SELECT count(*) FROM artifact_blob_body"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT count(*) FROM extraction_snapshot_body"
        ).fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM audit_event").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM outbox_event").fetchone()[0] == 1
    finally:
        connection.close()
