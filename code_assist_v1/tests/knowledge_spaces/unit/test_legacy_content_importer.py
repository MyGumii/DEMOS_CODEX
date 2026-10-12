from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

import pytest

from code_assist_v1.knowledge_core.db import ConnectionFactory
from code_assist_v1.knowledge_core.legacy.content_importer import (
    LegacyContentBatchLimitError,
    LegacyContentPreparationError,
    LegacyPersonalContentImporter,
    StagedBlobObservation,
)
from code_assist_v1.knowledge_core.legacy.blob_stager import (
    FilesystemLegacyBlobStager,
    LegacyBlobStagingError,
)
from code_assist_v1.knowledge_core.legacy.importer import LegacyIdentityImporter
from code_assist_v1.knowledge_core.legacy.source_reader import (
    LegacySourceBindingError,
    ReviewedLegacySourceReader,
)
from code_assist_v1.knowledge_core.legacy.plan_contract import canonical_json_bytes
from code_assist_v1.knowledge_core.legacy.planner import (
    build_plan_draft,
    confirm_migration_plan,
)
from code_assist_v1.knowledge_core.migrations import (
    CANONICAL_MIGRATION_CATALOG,
    MigrationStorageLayout,
    SingleMigrator,
)
from code_assist_v1.knowledge_core.repositories.artifacts import StagedVerifiedBlob
from code_assist_v1.knowledge_core.repositories.migration import VerifiedBlobEvidence
from code_assist_v1.tests.knowledge_spaces.unit.test_legacy_migration_planner import (
    _clock,
    _coverage,
    _csv_bytes,
    _mapping_row,
    _mkdirs,
    _probe,
)
from code_assist_v1.tools.legacy_inventory import inventory_snapshot
from code_assist_v1.tools.legacy_inventory_contract import (
    canonical_json_bytes as canonical_inventory_json_bytes,
)


pytestmark = pytest.mark.unit

_ATTESTATION = "7" * 64


@dataclass(frozen=True)
class _ReviewedFixture:
    plan_bytes: bytes
    plan_sha256: str
    users_bytes: bytes
    source_record_id: str
    asset_record_id: str
    relationship_id: str
    source_bytes: bytes
    asset_bytes: bytes
    extra_record_ids: tuple[str, ...] = ()


class _FilesystemTestStager:
    """Test-only physical stager; production code receives only its evidence."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.calls: list[str] = []

    @property
    def resolved_root(self) -> str:
        return str(self.root.resolve(strict=True))

    def stage_and_verify(
        self,
        content: bytes,
        *,
        expected_sha256: str,
        media_type: str,
        effective_at_ms: int,
        normalized_content_sha256: str | None,
        normalizer_version: str | None,
    ) -> StagedBlobObservation:
        assert hashlib.sha256(content).hexdigest() == expected_sha256
        relative = f"{expected_sha256[:2]}/{expected_sha256[2:4]}/{expected_sha256}"
        target = self.root / Path(relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            assert target.read_bytes() == content
        else:
            target.write_bytes(content)
        observed = target.read_bytes()
        assert observed == content
        self.calls.append(expected_sha256)
        class _Lease:
            def close(self) -> None:
                return None

        return StagedBlobObservation(
            blob=StagedVerifiedBlob(
                sha256=expected_sha256,
                size_bytes=len(content),
                media_type=media_type,
                relative_path=relative,
                created_at_ms=effective_at_ms,
                verified_at_ms=effective_at_ms,
            ),
            evidence=VerifiedBlobEvidence(
                raw_sha256=expected_sha256,
                size_bytes=len(content),
                media_type=media_type,
                normalized_content_sha256=normalized_content_sha256,
                normalizer_version=normalizer_version,
            ),
            lease=_Lease(),
        )


def _factory(layout) -> ConnectionFactory:
    factory = ConnectionFactory._for_isolated_tests(
        layout.core_db_path, CANONICAL_MIGRATION_CATALOG
    )
    SingleMigrator(
        factory,
        app_version="p2.3-legacy-content-importer-test",
        storage_layout=MigrationStorageLayout(
            data_root=layout.root,
            database=layout.core_db_path,
            blob_root=layout.blobs_dir,
            projection_root=layout.projections_dir,
        ),
    ).run()
    return factory


def _storage_layout(layout) -> MigrationStorageLayout:
    return MigrationStorageLayout(
        data_root=layout.root,
        database=layout.core_db_path,
        blob_root=layout.blobs_dir,
        projection_root=layout.projections_dir,
    )


def _reviewed_fixture(
    tmp_path: Path,
    *,
    extra_personal_files: Mapping[str, bytes] | None = None,
    extra_runtime_files: Mapping[str, bytes] | None = None,
) -> _ReviewedFixture:
    fixture_root = tmp_path / "source-fixture"
    fixture_root.mkdir()
    roots = _mkdirs(fixture_root)
    users_bytes = (
        b'{"users":{"admin":{"disabled":false,"password_hash":'
        b'"SANITIZED_TEST_HASH","role":"admin"}}}'
    )
    (roots["runtime"] / "users.json").write_bytes(users_bytes)
    for relative_path, content in sorted((extra_runtime_files or {}).items()):
        target = roots["runtime"] / Path(relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    wiki = roots["knowledge"][0] / "admin" / "wiki"
    source_path = wiki / "sources" / "source.md"
    asset_path = wiki / "assets" / "asset.png"
    source_path.parent.mkdir(parents=True)
    asset_path.parent.mkdir(parents=True)
    source_bytes = b"# imported\r\n![asset](../assets/asset.png)\r\n"
    asset_bytes = b"sanitized-png-bytes"
    source_path.write_bytes(source_bytes)
    asset_path.write_bytes(asset_bytes)
    for relative_path, content in sorted((extra_personal_files or {}).items()):
        target = roots["knowledge"][0] / "admin" / Path(relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    report, exit_code = inventory_snapshot(
        roots["legacy"],
        roots["knowledge"],
        "p2.3-content-snapshot",
        runtime_root=roots["runtime"],
        user_data_root=roots["user_data"],
        sessions_root=roots["sessions"],
        workspace_root=roots["workspace"],
        snapshot_kind="frozen_copy",
        snapshot_created_at_utc="2026-08-13T00:00:00.000000Z",
        snapshot_attestation_sha256=_ATTESTATION,
        scratch_root=roots["scratch"],
        allow_partial=True,
        clock=_clock,
        code_coverage_provider=_coverage,
    )
    assert exit_code == 0, report["errors"]
    inventory_bytes = canonical_inventory_json_bytes(report) + b"\n"
    mapping_bytes = _csv_bytes([_mapping_row(claim="admin")])
    draft = build_plan_draft(
        inventory_bytes,
        mapping_bytes,
        expected_inventory_manifest_sha256=report["manifest_sha256"],
        expected_snapshot_attestation_sha256=_ATTESTATION,
        expected_root_binding_sha256=report["source_snapshot"][
            "root_binding_sha256"
        ],
        expected_organization_mapping_document_sha256=hashlib.sha256(
            mapping_bytes
        ).hexdigest(),
        allow_data_quality_errors=True,
    )
    draft_bytes = canonical_json_bytes(draft) + b"\n"
    reviewed = confirm_migration_plan(
        draft_bytes,
        expected_draft_sha256=hashlib.sha256(draft_bytes).hexdigest(),
        review_attestation_sha256="8" * 64,
        reviewed_at_utc="2026-08-13T02:00:00.000000Z",
        reviewer_id="p2.3-content-reviewer",
    )
    records = {
        row["category"]: row
        for row in draft["records"]
        if row["action"] == "import"
        and row["execution_phase"] == "P2.3"
        and row["import_mode"] in {"personal_item", "personal_asset"}
    }
    relationship = next(
        row
        for row in draft["relationships"]
        if row["action"] == "import"
        and row["execution_phase"] == "P2.3"
        and row["source_relationship"]["relationship_type"] == "asset_reference"
    )
    extra_ids = tuple(
        sorted(
            str(row["record_id"])
            for row in draft["records"]
            if row["action"] == "import"
            and row["execution_phase"] == "P2.3"
            and row["import_mode"] == "personal_item"
            and row["category"] not in {"knowledge.wiki_sources"}
        )
    )
    return _ReviewedFixture(
        plan_bytes=canonical_json_bytes(reviewed) + b"\n",
        plan_sha256=str(reviewed["plan_sha256"]),
        users_bytes=users_bytes,
        source_record_id=str(records["knowledge.wiki_sources"]["record_id"]),
        asset_record_id=str(records["knowledge.wiki_assets"]["record_id"]),
        relationship_id=str(relationship["relationship_id"]),
        source_bytes=source_bytes,
        asset_bytes=asset_bytes,
        extra_record_ids=extra_ids,
    )


def _apply_identity(factory: ConnectionFactory, fixture: _ReviewedFixture) -> None:
    LegacyIdentityImporter(factory).apply(
        fixture.plan_bytes,
        fixture.users_bytes,
        expected_plan_sha256=fixture.plan_sha256,
        verified_blob_evidence=VerifiedBlobEvidence(
            raw_sha256=hashlib.sha256(fixture.users_bytes).hexdigest(),
            size_bytes=len(fixture.users_bytes),
            media_type="application/json",
        ),
    )


def _counts(factory: ConnectionFactory) -> dict[str, int]:
    tables = (
        "source_artifact",
        "knowledge_item",
        "knowledge_revision",
        "item_source",
        "legacy_migration_map",
        "legacy_migration_relationship_map",
        "audit_event",
        "outbox_event",
    )
    connection = factory.open()
    try:
        return {
            table: int(
                connection.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()[
                    "n"
                ]
            )
            for table in tables
        }
    finally:
        connection.close()


def test_reviewed_content_asset_and_reference_apply_and_exact_replay(
    tmp_path: Path, isolated_knowledge_layout
) -> None:
    fixture = _reviewed_fixture(tmp_path)
    factory = _factory(isolated_knowledge_layout)
    _apply_identity(factory, fixture)
    stager = FilesystemLegacyBlobStager(_storage_layout(isolated_knowledge_layout))
    importer = LegacyPersonalContentImporter(factory, stager)
    record_ids = tuple(sorted((fixture.source_record_id, fixture.asset_record_id)))
    payloads = {
        fixture.source_record_id: fixture.source_bytes,
        fixture.asset_record_id: fixture.asset_bytes,
    }

    first = importer.apply_batch(
        fixture.plan_bytes,
        payloads,
        selected_record_ids=record_ids,
        selected_relationship_ids=(fixture.relationship_id,),
        expected_plan_sha256=fixture.plan_sha256,
    )
    assert len(first.records) == 2
    assert len(first.relationships) == 1
    assert first.inserted_mapping_count == 3
    assert {row.import_mode for row in first.records} == {
        "personal_item",
        "personal_asset",
    }
    after_first = _counts(factory)
    assert after_first["knowledge_item"] == 1
    assert after_first["knowledge_revision"] == 1
    assert after_first["legacy_migration_relationship_map"] == 1

    second = importer.apply_batch(
        fixture.plan_bytes,
        payloads,
        selected_record_ids=record_ids,
        selected_relationship_ids=(fixture.relationship_id,),
        expected_plan_sha256=fixture.plan_sha256,
    )
    assert second.inserted_mapping_count == 0
    assert _counts(factory) == after_first


def test_relationship_only_resume_closes_after_endpoint_batch(
    tmp_path: Path, isolated_knowledge_layout
) -> None:
    fixture = _reviewed_fixture(tmp_path)
    factory = _factory(isolated_knowledge_layout)
    _apply_identity(factory, fixture)
    importer = LegacyPersonalContentImporter(
        factory, _FilesystemTestStager(isolated_knowledge_layout.blobs_dir)
    )
    payloads = {
        fixture.source_record_id: fixture.source_bytes,
        fixture.asset_record_id: fixture.asset_bytes,
    }
    records = importer.apply_batch(
        fixture.plan_bytes,
        payloads,
        selected_record_ids=tuple(
            sorted((fixture.source_record_id, fixture.asset_record_id))
        ),
        expected_plan_sha256=fixture.plan_sha256,
    )
    assert records.inserted_mapping_count == 2
    relationship = importer.apply_batch(
        fixture.plan_bytes,
        payloads,
        selected_record_ids=(),
        selected_relationship_ids=(fixture.relationship_id,),
        expected_plan_sha256=fixture.plan_sha256,
    )
    assert relationship.inserted_mapping_count == 1
    assert len(relationship.relationships) == 1


def test_failpoint_rolls_back_every_canonical_content_effect(
    tmp_path: Path, isolated_knowledge_layout
) -> None:
    fixture = _reviewed_fixture(tmp_path)
    factory = _factory(isolated_knowledge_layout)
    _apply_identity(factory, fixture)
    before = _counts(factory)

    def failpoint(name: str) -> None:
        if name == "legacy_content.after_item":
            raise RuntimeError("synthetic content failure")

    importer = LegacyPersonalContentImporter(
        factory,
        _FilesystemTestStager(isolated_knowledge_layout.blobs_dir),
        failpoint=failpoint,
    )
    with pytest.raises(RuntimeError, match="synthetic content failure"):
        importer.apply_batch(
            fixture.plan_bytes,
            {fixture.source_record_id: fixture.source_bytes},
            selected_record_ids=(fixture.source_record_id,),
            expected_plan_sha256=fixture.plan_sha256,
        )
    assert _counts(factory) == before


@pytest.mark.parametrize("drift", ["bytes", "payload_set", "unsorted"])
def test_public_boundary_rejects_plan_source_and_batch_drift_before_db_write(
    tmp_path: Path, isolated_knowledge_layout, drift: str
) -> None:
    fixture = _reviewed_fixture(tmp_path)
    factory = _factory(isolated_knowledge_layout)
    _apply_identity(factory, fixture)
    before = _counts(factory)
    stager = _FilesystemTestStager(isolated_knowledge_layout.blobs_dir)
    importer = LegacyPersonalContentImporter(factory, stager)
    plan = fixture.plan_bytes
    payloads: Mapping[str, bytes] = {
        fixture.source_record_id: fixture.source_bytes,
        fixture.asset_record_id: fixture.asset_bytes,
    }
    record_ids = tuple(sorted((fixture.source_record_id, fixture.asset_record_id)))
    if drift == "bytes":
        payloads = {**payloads, fixture.source_record_id: fixture.source_bytes + b"x"}
    elif drift == "payload_set":
        payloads = {fixture.source_record_id: fixture.source_bytes}
    else:
        record_ids = tuple(reversed(record_ids))
    with pytest.raises(LegacyContentPreparationError):
        importer.apply_batch(
            plan,
            payloads,
            selected_record_ids=record_ids,
            expected_plan_sha256=fixture.plan_sha256,
        )
    assert _counts(factory) == before


def test_filesystem_blob_stager_retains_verified_handle_until_release(
    isolated_knowledge_layout,
) -> None:
    _factory(isolated_knowledge_layout)
    stager = FilesystemLegacyBlobStager(
        _storage_layout(isolated_knowledge_layout)
    )
    content = b"# staged\r\n"
    digest = hashlib.sha256(content).hexdigest()
    normalized = hashlib.sha256(content.replace(b"\r\n", b"\n")).hexdigest()
    observation = stager.stage_and_verify(
        content,
        expected_sha256=digest,
        media_type="text/markdown",
        effective_at_ms=100,
        normalized_content_sha256=normalized,
        normalizer_version="markdown_lf_v1",
    )
    target = isolated_knowledge_layout.blobs_dir / observation.blob.relative_path
    try:
        assert target.read_bytes() == content
        with pytest.raises(OSError):
            with target.open("r+b") as stream:
                stream.write(b"x")
        replay = stager.stage_and_verify(
            content,
            expected_sha256=digest,
            media_type="text/markdown",
            effective_at_ms=100,
            normalized_content_sha256=normalized,
            normalizer_version="markdown_lf_v1",
        )
        replay.lease.close()
    finally:
        observation.lease.close()
    assert target.read_bytes() == content

    corrupt_content = b"different"
    corrupt_digest = hashlib.sha256(corrupt_content).hexdigest()
    corrupt_target = (
        isolated_knowledge_layout.blobs_dir
        / corrupt_digest[:2]
        / corrupt_digest[2:4]
        / corrupt_digest
    )
    corrupt_target.parent.mkdir(parents=True, exist_ok=True)
    corrupt_target.write_bytes(b"x" * len(corrupt_content))
    with pytest.raises(LegacyBlobStagingError):
        stager.stage_and_verify(
            corrupt_content,
            expected_sha256=corrupt_digest,
            media_type="application/octet-stream",
            effective_at_ms=100,
            normalized_content_sha256=None,
            normalizer_version=None,
        )


def test_content_boundary_rejects_blob_root_overlapping_frozen_source(
    tmp_path: Path, isolated_knowledge_layout
) -> None:
    fixture = _reviewed_fixture(tmp_path)
    factory = _factory(isolated_knowledge_layout)
    _apply_identity(factory, fixture)
    source_root = Path(
        next(
            row["resolved_path"]
            for row in json.loads(fixture.plan_bytes)["draft"]["source_snapshot"][
                "roots"
            ]
            if row["root_id"] == "knowledge_000"
        )
    )
    importer = LegacyPersonalContentImporter(
        factory, _FilesystemTestStager(source_root)
    )
    with pytest.raises(LegacyContentPreparationError, match="overlaps"):
        importer.apply_batch(
            fixture.plan_bytes,
            {fixture.source_record_id: fixture.source_bytes},
            selected_record_ids=(fixture.source_record_id,),
            expected_plan_sha256=fixture.plan_sha256,
        )


@pytest.mark.parametrize(
    ("boundary", "include_relationship"),
    [
        ("legacy_content.after_artifact", False),
        ("legacy_content.after_item", False),
        ("legacy_content.after_record_mapping", False),
        ("legacy_content.after_embedded_asset", True),
        ("legacy_content.after_relationship_mapping", True),
    ],
)
def test_every_content_composition_failpoint_rolls_back_the_complete_batch(
    tmp_path: Path,
    isolated_knowledge_layout,
    boundary: str,
    include_relationship: bool,
) -> None:
    fixture = _reviewed_fixture(tmp_path)
    factory = _factory(isolated_knowledge_layout)
    _apply_identity(factory, fixture)
    before = _counts(factory)

    def failpoint(name: str) -> None:
        if name == boundary:
            raise RuntimeError(boundary)

    importer = LegacyPersonalContentImporter(
        factory,
        _FilesystemTestStager(isolated_knowledge_layout.blobs_dir),
        failpoint=failpoint,
    )
    if include_relationship:
        record_ids = tuple(
            sorted((fixture.source_record_id, fixture.asset_record_id))
        )
        relationship_ids = (fixture.relationship_id,)
        payloads = {
            fixture.source_record_id: fixture.source_bytes,
            fixture.asset_record_id: fixture.asset_bytes,
        }
    else:
        record_ids = (fixture.source_record_id,)
        relationship_ids = ()
        payloads = {fixture.source_record_id: fixture.source_bytes}
    with pytest.raises(RuntimeError, match=boundary):
        importer.apply_batch(
            fixture.plan_bytes,
            payloads,
            selected_record_ids=record_ids,
            selected_relationship_ids=relationship_ids,
            expected_plan_sha256=fixture.plan_sha256,
        )
    assert _counts(factory) == before


def test_content_batch_limit_and_missing_current_owner_fail_closed(
    tmp_path: Path, isolated_knowledge_layout
) -> None:
    fixture = _reviewed_fixture(tmp_path)
    factory = _factory(isolated_knowledge_layout)
    stager = _FilesystemTestStager(isolated_knowledge_layout.blobs_dir)
    importer = LegacyPersonalContentImporter(factory, stager)
    selected = tuple(f"rec_{index:024x}" for index in range(1, 102))
    with pytest.raises(LegacyContentBatchLimitError):
        importer.apply_batch(
            fixture.plan_bytes,
            {},
            selected_record_ids=selected,
            expected_plan_sha256=fixture.plan_sha256,
        )

    before = _counts(factory)
    with pytest.raises(Exception) as caught:
        importer.apply_batch(
            fixture.plan_bytes,
            {fixture.source_record_id: fixture.source_bytes},
            selected_record_ids=(fixture.source_record_id,),
            expected_plan_sha256=fixture.plan_sha256,
        )
    assert getattr(caught.value, "code", None) == "LEGACY_CONTENT_STATE_CONFLICT"
    assert _counts(factory) == before


def test_reviewed_source_reader_binds_root_bytes_and_retains_file_handle(
    tmp_path: Path,
) -> None:
    fixture = _reviewed_fixture(tmp_path)
    reader = ReviewedLegacySourceReader(
        fixture.plan_bytes, expected_plan_sha256=fixture.plan_sha256
    )
    observations = reader.read_records(
        tuple(sorted((fixture.source_record_id, fixture.asset_record_id)))
    )
    source = observations[fixture.source_record_id]
    asset = observations[fixture.asset_record_id]
    plan = json.loads(fixture.plan_bytes)["draft"]
    records = {row["record_id"]: row for row in plan["records"]}
    source_record = records[fixture.source_record_id]["source_record"]
    root = next(
        row
        for row in plan["source_snapshot"]["roots"]
        if row["root_id"] == source_record["root_id"]
    )
    source_path = Path(root["resolved_path"]) / Path(
        source_record["relative_path"]
    )
    try:
        assert source.content == fixture.source_bytes
        assert asset.content == fixture.asset_bytes
        assert source.raw_sha256 == hashlib.sha256(fixture.source_bytes).hexdigest()
        with pytest.raises(OSError):
            with source_path.open("r+b") as stream:
                stream.write(b"x")
    finally:
        for observation in observations.values():
            observation.close()
    assert source_path.read_bytes() == fixture.source_bytes


def test_reviewed_source_reader_rejects_source_byte_drift(
    tmp_path: Path,
) -> None:
    fixture = _reviewed_fixture(tmp_path)
    plan = json.loads(fixture.plan_bytes)["draft"]
    record = next(
        row
        for row in plan["records"]
        if row["record_id"] == fixture.source_record_id
    )["source_record"]
    root = next(
        row
        for row in plan["source_snapshot"]["roots"]
        if row["root_id"] == record["root_id"]
    )
    path = Path(root["resolved_path"]) / Path(record["relative_path"])
    path.write_bytes(fixture.source_bytes + b"drift")
    reader = ReviewedLegacySourceReader(
        fixture.plan_bytes, expected_plan_sha256=fixture.plan_sha256
    )
    with pytest.raises(LegacySourceBindingError):
        reader.read_record(fixture.source_record_id)


def test_reviewed_source_reader_recomputes_frozen_tree_high_water(
    tmp_path: Path,
) -> None:
    fixture = _reviewed_fixture(tmp_path)
    reader = ReviewedLegacySourceReader(
        fixture.plan_bytes, expected_plan_sha256=fixture.plan_sha256
    )
    reader.verify_snapshot_high_water()
    plan = json.loads(fixture.plan_bytes)["draft"]
    root = next(
        row
        for row in plan["source_snapshot"]["roots"]
        if row["root_id"] == "workspace"
    )
    (Path(root["resolved_path"]) / "late-file.txt").write_text(
        "drift", encoding="utf-8"
    )
    with pytest.raises(LegacySourceBindingError):
        reader.verify_snapshot_high_water()
