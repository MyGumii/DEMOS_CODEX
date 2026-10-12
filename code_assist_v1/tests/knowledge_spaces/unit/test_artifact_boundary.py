from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from uuid import UUID, uuid5

import pytest

from code_assist_v1.knowledge_core.db import ConnectionFactory, transaction
from code_assist_v1.knowledge_core.errors import KnowledgeCoreTransactionError
from code_assist_v1.knowledge_core.migrations import (
    CANONICAL_MIGRATION_CATALOG,
    MigrationStorageLayout,
    SingleMigrator,
)
from code_assist_v1.knowledge_core.repositories.artifacts import (
    ArtifactBundleConflictError,
    ArtifactExtractionSpec,
    ArtifactIds,
    ArtifactRepository,
    CanonicalEmbeddedAssetSpec,
    PreparedMarkdownBody,
    SourceArtifactBundle,
    StagedVerifiedBlob,
)
from code_assist_v1.knowledge_core.repositories.users import (
    LegacyUserRecord,
    UserRepository,
)
from code_assist_v1.knowledge_core.services.publication import (
    AuthorizedNotionPageSnapshot,
    PublicationSourcePreparationError,
    build_source_artifact_bundle,
    lint_markdown_structure,
    normalize_markdown_lf,
    prepare_publication_artifact,
)


pytestmark = pytest.mark.unit

_NAMESPACE = UUID("df596cf9-5a52-429b-b088-196b54f59f2d")
_OWNER = "owner"


def _uuid(label: str) -> str:
    return str(uuid5(_NAMESPACE, label))


def _factory(layout) -> ConnectionFactory:
    factory = ConnectionFactory._for_isolated_tests(
        layout.core_db_path,
        CANONICAL_MIGRATION_CATALOG,
    )
    SingleMigrator(
        factory,
        app_version="p4.1-artifact-boundary-test",
        storage_layout=MigrationStorageLayout(
            data_root=layout.root,
            database=layout.core_db_path,
            blob_root=layout.blobs_dir,
            projection_root=layout.projections_dir,
        ),
    ).run()
    return factory


def _seed_owner(connection, owner: str = _OWNER) -> None:
    UserRepository().import_legacy(
        connection,
        LegacyUserRecord(
            id=owner,
            display_name=owner.title(),
            password_hash=f"scrypt:p4.1:{owner}",
            system_role="user",
            account_status="active",
            created_at_ms=100,
            updated_at_ms=100,
        ),
    )


def _blob(content: bytes, media_type: str, *, clock: int = 150) -> StagedVerifiedBlob:
    sha256 = hashlib.sha256(content).hexdigest()
    return StagedVerifiedBlob(
        sha256=sha256,
        size_bytes=len(content),
        media_type=media_type,
        relative_path=f"{sha256[:2]}/{sha256[2:4]}/{sha256}",
        created_at_ms=clock,
        verified_at_ms=clock + 1,
    )


def _bundle(
    label: str,
    *,
    origin_type: str = "upload",
    failed: bool = False,
    raw: bytes = b"%PDF-raw-source\r\n",
) -> SourceArtifactBundle:
    extracted_markdown = "# Extracted\r\n\r\nBody\r\n"
    if origin_type == "notion":
        raw = extracted_markdown.encode("utf-8")
    prepared = prepare_publication_artifact(
        raw,
        extracted_markdown=None if failed else extracted_markdown,
        extraction_failure_code="unsupported_source" if failed else None,
        extractor_name="fixture-extractor",
        extractor_version="1.0.0",
        warnings=("fixture_warning",),
        created_at_ms=201,
    )
    blob = _blob(raw, "application/pdf")
    notion_snapshot = None
    origin_key = f"source:{label}"
    origin_revision_key = f"revision:{label}"
    if origin_type == "notion":
        content_sha = hashlib.sha256(extracted_markdown.encode("utf-8")).hexdigest()
        asset_sha = hashlib.sha256(b"[]").hexdigest()
        notion_identity = {
            "asset_manifest_sha256": asset_sha,
            "content_sha256": content_sha,
            "owner_user_id": _OWNER,
            "page_id": f"page-{label}",
            "revision": 1,
            "schema_version": 1,
            "updated_at": 2,
        }
        notion_snapshot_sha = hashlib.sha256(
            json.dumps(
                notion_identity,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        notion_snapshot = AuthorizedNotionPageSnapshot(
            actor_user_id=_OWNER,
            page_id=f"page-{label}",
            owner_user_id=_OWNER,
            title="Notion fixture",
            revision=1,
            updated_at=2,
            markdown=extracted_markdown,
            content_sha256=content_sha,
            asset_manifest_sha256=asset_sha,
            source_snapshot_sha256=notion_snapshot_sha,
        )
        origin_key = f"notion:{_OWNER}:page-{label}"
        origin_revision_key = notion_snapshot_sha
    return build_source_artifact_bundle(
        prepared,
        ids=ArtifactIds(
            artifact_id=_uuid(f"artifact:{label}"),
            raw_manifest_id=_uuid(f"raw:{label}"),
            extraction_snapshot_id=_uuid(f"extraction:{label}"),
        ),
        origin_type=origin_type,  # type: ignore[arg-type]
        origin_key=origin_key,
        origin_revision_key=origin_revision_key,
        raw_blob=blob,
        logical_name=f"{label}.pdf",
        original_filename=f"{label}.pdf",
        publisher_user_id=_OWNER,
        created_at_ms=200,
        notion_snapshot=notion_snapshot,
    )


def _count(connection, table: str) -> int:
    row = connection.execute(f"SELECT count(*) FROM {table}").fetchone()
    assert row is not None
    return int(row[0])


def test_preparation_binds_raw_exact_and_normalized_hashes_without_io() -> None:
    raw = b"binary source that is not markdown"
    markdown = "# Title\r\n\r\nBody\r\n"
    first = prepare_publication_artifact(
        raw,
        extracted_markdown=markdown,
        extraction_failure_code=None,
        extractor_name="deterministic-markdown",
        extractor_version="1",
        warnings=("z_warning", "a_warning"),
        created_at_ms=10,
    )
    second = prepare_publication_artifact(
        raw,
        extracted_markdown=markdown,
        extraction_failure_code=None,
        extractor_name="deterministic-markdown",
        extractor_version="1",
        warnings=("a_warning", "z_warning"),
        created_at_ms=10,
    )
    exact = markdown.encode("utf-8")
    assert first == second
    assert first.raw_sha256 == hashlib.sha256(raw).hexdigest()
    assert first.extraction.extracted_sha256 == hashlib.sha256(exact).hexdigest()
    assert first.extraction.normalized_content_sha256 == hashlib.sha256(
        normalize_markdown_lf(markdown)
    ).hexdigest()
    assert first.raw_sha256 != first.extraction.extracted_sha256
    assert first.extraction.warnings_json == '["a_warning","z_warning"]'
    assert first.publishable
    assert first.lint_findings == ()


def test_failed_extraction_preserves_raw_evidence_without_claiming_a_body() -> None:
    prepared = prepare_publication_artifact(
        b"\x00\x01unsupported",
        extracted_markdown=None,
        extraction_failure_code="unsupported_source",
        extractor_name="safe-extractor",
        extractor_version="2",
        created_at_ms=11,
    )
    assert prepared.extraction.status == "failed"
    assert prepared.extraction.body is None
    assert prepared.extraction.extracted_sha256 is None
    assert prepared.extraction.normalized_content_sha256 is None
    assert prepared.extraction.warnings_json == '["unsupported_source"]'
    assert [finding.code for finding in prepared.lint_findings] == [
        "extraction_failed"
    ]
    assert not prepared.publishable


def test_bundle_builder_rejects_a_different_staged_raw_blob() -> None:
    prepared = prepare_publication_artifact(
        b"approved raw",
        extracted_markdown="# Extracted\n",
        extraction_failure_code=None,
        extractor_name="extractor",
        extractor_version="1",
        created_at_ms=20,
    )
    with pytest.raises(PublicationSourcePreparationError):
        build_source_artifact_bundle(
            prepared,
            ids=ArtifactIds(_uuid("a"), _uuid("b"), _uuid("c")),
            origin_type="upload",
            origin_key="upload:1",
            origin_revision_key=None,
            raw_blob=_blob(b"different raw", "application/octet-stream"),
            logical_name="source.bin",
            original_filename="source.bin",
            publisher_user_id=_OWNER,
            created_at_ms=21,
        )


@pytest.mark.parametrize(
    ("markdown", "code", "severity"),
    [
        (" \r\n\t", "empty_markdown", "blocker"),
        ("safe\x01unsafe", "unsafe_control_character", "error"),
        ("---\ntitle: open", "unterminated_front_matter", "blocker"),
        ("# Title\n```python\nprint(1)", "unterminated_code_fence", "error"),
    ],
)
def test_minimal_structural_lint_is_deterministic_and_body_free(
    markdown: str,
    code: str,
    severity: str,
) -> None:
    findings = lint_markdown_structure(markdown)
    assert any(
        finding.code == code and finding.severity == severity for finding in findings
    )
    assert all(not hasattr(finding, "body") for finding in findings)
    assert findings == lint_markdown_structure(markdown)


@pytest.mark.parametrize("origin_type", ["upload", "notion", "chat", "manual"])
def test_generic_artifact_bundle_inserts_and_exactly_replays_without_knowledge(
    isolated_knowledge_layout,
    origin_type: str,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    repository = ArtifactRepository()
    bundle = _bundle(origin_type, origin_type=origin_type)
    connection = factory.open()
    try:
        with transaction(connection):
            _seed_owner(connection)
            inserted = repository.put_source_bundle(connection, bundle)
            assert inserted.inserted
        with transaction(connection):
            replay = repository.put_source_bundle(connection, bundle)
            assert not replay.inserted
            artifact = connection.execute(
                """SELECT origin_type,raw_sha256,raw_size_bytes,media_type,
                          metadata_schema_version
                   FROM source_artifact WHERE id=?""",
                (bundle.ids.artifact_id,),
            ).fetchone()
            extraction = connection.execute(
                """SELECT status,extracted_sha256,normalized_content_sha256,
                          normalizer_version,warnings_json
                   FROM extraction_snapshot WHERE id=?""",
                (bundle.ids.extraction_snapshot_id,),
            ).fetchone()
            assert artifact is not None and extraction is not None
            assert artifact["origin_type"] == origin_type
            assert artifact["raw_sha256"] == bundle.raw_blob.sha256
            assert extraction["status"] == "ready"
            if origin_type == "notion":
                assert extraction["extracted_sha256"] == artifact["raw_sha256"]
            else:
                assert extraction["extracted_sha256"] != artifact["raw_sha256"]
            assert extraction["normalizer_version"] == "markdown_lf_v1"
            assert _count(connection, "source_artifact") == 1
            assert _count(connection, "artifact_blob") == 1
            assert _count(connection, "artifact_blob_body") == 1
            assert _count(connection, "extraction_snapshot") == 1
            assert _count(connection, "extraction_snapshot_body") == 1
            assert _count(connection, "knowledge_item") == 0
            assert _count(connection, "knowledge_revision") == 0
    finally:
        connection.close()


def test_notion_artifact_requires_exact_body_free_provenance_binding(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    repository = ArtifactRepository()
    bundle = _bundle("notion-provenance", origin_type="notion")
    metadata = json.loads(bundle.metadata_json)
    provenance = metadata.pop("notion_provenance")
    assert provenance == {
        "asset_manifest_sha256": hashlib.sha256(b"[]").hexdigest(),
        "owner_user_id": _OWNER,
        "page_id": "page-notion-provenance",
        "revision": 1,
        "source_snapshot_sha256": bundle.origin_revision_key,
        "updated_at": 2,
    }
    missing = replace(
        bundle,
        metadata_json=json.dumps(
            metadata,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
    )
    drifted = replace(bundle, origin_revision_key="f" * 64)
    connection = factory.open()
    try:
        with transaction(connection):
            _seed_owner(connection)
            with pytest.raises(ValueError, match="source-evidence shape"):
                repository.put_source_bundle(connection, missing)
            with pytest.raises(ValueError, match="Notion provenance"):
                repository.put_source_bundle(connection, drifted)
        assert _count(connection, "source_artifact") == 0
    finally:
        connection.close()


def test_failed_extraction_bundle_replays_without_an_extraction_body(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    bundle = _bundle("failed", failed=True)
    repository = ArtifactRepository()
    connection = factory.open()
    try:
        with transaction(connection):
            _seed_owner(connection)
            assert repository.put_source_bundle(connection, bundle).inserted
        with transaction(connection):
            assert not repository.put_source_bundle(connection, bundle).inserted
            snapshot = connection.execute(
                """SELECT status,extracted_sha256,normalized_content_sha256,
                          extracted_size_bytes,normalizer_version,
                          body_retention_status
                   FROM extraction_snapshot WHERE id=?""",
                (bundle.ids.extraction_snapshot_id,),
            ).fetchone()
            assert snapshot is not None
            assert tuple(snapshot) == ("failed", None, None, None, None, "none")
            assert _count(connection, "extraction_snapshot_body") == 0
    finally:
        connection.close()


def test_generic_bundle_requires_a_caller_owned_transaction(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    connection = factory.open()
    try:
        with transaction(connection):
            _seed_owner(connection)
        with pytest.raises(KnowledgeCoreTransactionError):
            ArtifactRepository().put_source_bundle(connection, _bundle("no-tx"))
    finally:
        connection.close()


def test_inline_body_hash_or_normalized_hash_forgery_fails_before_writes(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    repository = ArtifactRepository()
    baseline = _bundle("forged")
    assert baseline.extraction.body is not None
    forged_body = replace(baseline.extraction.body, content_sha256="f" * 64)
    forged = replace(
        baseline,
        extraction=replace(
            baseline.extraction,
            extracted_sha256="f" * 64,
            normalized_content_sha256="e" * 64,
            body=forged_body,
        ),
    )
    connection = factory.open()
    try:
        with transaction(connection):
            _seed_owner(connection)
            with pytest.raises(ValueError):
                repository.put_source_bundle(connection, forged)
            UserRepository().import_legacy(
                connection,
                LegacyUserRecord(
                    id="unrelated",
                    display_name="Unrelated",
                    password_hash="scrypt:p4.1:unrelated",
                    system_role="user",
                    account_status="active",
                    created_at_ms=300,
                    updated_at_ms=300,
                ),
            )
        check = factory.open(read_only=True)
        try:
            assert _count(check, "source_artifact") == 0
            assert _count(check, "blob_object") == 0
            assert _count(check, "app_user") == 2
        finally:
            check.close()
    finally:
        connection.close()


def test_failed_extraction_cannot_smuggle_body_or_hash_evidence(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    baseline = _bundle("failed-smuggle", failed=True)
    forged_spec = replace(
        baseline.extraction,
        extracted_sha256="a" * 64,
        body=PreparedMarkdownBody("x", None, 1, hashlib.sha256(b"x").hexdigest()),
    )
    connection = factory.open()
    try:
        with transaction(connection):
            _seed_owner(connection)
            with pytest.raises(ValueError):
                ArtifactRepository().put_source_bundle(
                    connection, replace(baseline, extraction=forged_spec)
                )
        check = factory.open(read_only=True)
        try:
            assert _count(check, "source_artifact") == 0
        finally:
            check.close()
    finally:
        connection.close()


@pytest.mark.parametrize(
    "mutation",
    [
        "snapshot_hash",
        "publishable",
        "extra_secret",
        "owner_extra",
        "unsafe_warning",
    ],
)
def test_repository_independently_recomputes_sanitized_source_evidence(
    isolated_knowledge_layout,
    mutation: str,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    bundle = _bundle(f"metadata-{mutation}")
    if mutation in {"snapshot_hash", "publishable", "extra_secret"}:
        metadata = json.loads(bundle.metadata_json)
        if mutation == "snapshot_hash":
            metadata["preparation_sha256"] = "0" * 64
        elif mutation == "publishable":
            metadata["publishable"] = not metadata["publishable"]
        else:
            metadata["raw_content"] = "must never enter metadata"
        bundle = replace(
            bundle,
            metadata_json=json.dumps(
                metadata,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
        )
    elif mutation == "owner_extra":
        bundle = replace(
            bundle,
            source_owner_snapshot_json=(
                '{"publisher_user_id":"owner","raw_claim":"secret",'
                '"schema_version":1}'
            ),
        )
    else:
        bundle = replace(
            bundle,
            extraction=replace(
                bundle.extraction, warnings_json='["raw private warning text"]'
            ),
        )
    connection = factory.open()
    try:
        with transaction(connection):
            _seed_owner(connection)
            with pytest.raises(ValueError):
                ArtifactRepository().put_source_bundle(connection, bundle)
        check = factory.open(read_only=True)
        try:
            assert _count(check, "source_artifact") == 0
            assert _count(check, "blob_object") == 0
        finally:
            check.close()
    finally:
        connection.close()


def test_exact_replay_rejects_source_or_extraction_drift(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    repository = ArtifactRepository()
    bundle = _bundle("drift")
    connection = factory.open()
    try:
        with transaction(connection):
            _seed_owner(connection)
            repository.put_source_bundle(connection, bundle)
        with transaction(connection):
            with pytest.raises((ValueError, ArtifactBundleConflictError)):
                repository.put_source_bundle(
                    connection,
                    replace(bundle, metadata_json='{"schema_version":2}'),
                )
            with pytest.raises((ValueError, ArtifactBundleConflictError)):
                repository.put_source_bundle(
                    connection,
                    replace(
                        bundle,
                        extraction=replace(
                            bundle.extraction, warnings_json='["changed"]'
                        ),
                    ),
                )
        check = factory.open(read_only=True)
        try:
            assert _count(check, "source_artifact") == 1
            assert _count(check, "extraction_snapshot") == 1
        finally:
            check.close()
    finally:
        connection.close()


def test_durable_extraction_commits_embedded_assets_and_replays_exactly(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    repository = ArtifactRepository()
    complete = _bundle("rich-worker")
    pending_metadata = json.dumps(
        {
            "ingestion_state": "pending_extraction",
            "raw_sha256": complete.raw_blob.sha256,
            "raw_size_bytes": complete.raw_blob.size_bytes,
            "schema_version": 1,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    pending = replace(
        complete,
        extraction=None,
        metadata_json=pending_metadata,
    )
    asset = CanonicalEmbeddedAssetSpec(
        manifest_id=_uuid("rich-worker:asset:1"),
        blob=_blob(b"canonical-image", "image/png", clock=201),
        logical_name="asset-001-diagram.png",
        ordinal=1,
    )
    connection = factory.open()
    try:
        with transaction(connection):
            _seed_owner(connection)
            repository.put_source_bundle(connection, pending)
        with transaction(connection):
            inserted = repository.put_source_extraction(
                connection,
                artifact_id=complete.ids.artifact_id,
                extraction_snapshot_id=complete.ids.extraction_snapshot_id,
                extraction=complete.extraction,
                embedded_assets=(asset,),
            )
            assert inserted.inserted
        with transaction(connection):
            replay = repository.put_source_extraction(
                connection,
                artifact_id=complete.ids.artifact_id,
                extraction_snapshot_id=complete.ids.extraction_snapshot_id,
                extraction=complete.extraction,
                embedded_assets=(asset,),
            )
            assert not replay.inserted
            with pytest.raises(ArtifactBundleConflictError):
                repository.put_source_extraction(
                    connection,
                    artifact_id=complete.ids.artifact_id,
                    extraction_snapshot_id=complete.ids.extraction_snapshot_id,
                    extraction=complete.extraction,
                    embedded_assets=(replace(asset, logical_name="drift.png"),),
                )
        check = factory.open(read_only=True)
        try:
            row = check.execute(
                """SELECT b.logical_name,b.media_type,b.ordinal,bb.content_sha256
                     FROM artifact_blob b
                     JOIN artifact_blob_body bb ON bb.artifact_blob_id=b.id
                    WHERE b.artifact_id=? AND b.role='embedded_asset'""",
                (complete.ids.artifact_id,),
            ).fetchone()
            assert row is not None
            assert tuple(row) == (
                "asset-001-diagram.png",
                "image/png",
                1,
                asset.blob.sha256,
            )
        finally:
            check.close()
    finally:
        connection.close()


@pytest.mark.parametrize(
    "mutation",
    [
        lambda bundle: replace(bundle, publisher_user_id="missing"),
        lambda bundle: replace(bundle, origin_type="legacy"),
        lambda bundle: replace(
            bundle,
            ids=ArtifactIds(
                bundle.ids.artifact_id,
                bundle.ids.artifact_id,
                bundle.ids.extraction_snapshot_id,
            ),
        ),
        lambda bundle: replace(bundle, metadata_schema_version=0),
    ],
)
def test_generic_bundle_shape_and_owner_fail_closed(
    isolated_knowledge_layout,
    mutation,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    connection = factory.open()
    try:
        with transaction(connection):
            _seed_owner(connection)
            with pytest.raises((ValueError, ArtifactBundleConflictError)):
                ArtifactRepository().put_source_bundle(
                    connection, mutation(_bundle("bad-shape"))
                )
        check = factory.open(read_only=True)
        try:
            assert _count(check, "source_artifact") == 0
            assert _count(check, "blob_object") == 0
        finally:
            check.close()
    finally:
        connection.close()


@pytest.mark.parametrize(
    "kwargs",
    [
        {
            "extracted_markdown": "ok",
            "extraction_failure_code": "failed",
        },
        {"extracted_markdown": None, "extraction_failure_code": None},
    ],
)
def test_preparation_requires_exactly_one_extraction_outcome(kwargs) -> None:
    with pytest.raises(PublicationSourcePreparationError):
        prepare_publication_artifact(
            b"raw",
            extractor_name="extractor",
            extractor_version="1",
            created_at_ms=1,
            **kwargs,
        )
