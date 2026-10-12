from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
import json

import pytest

from code_assist_v1.knowledge_core.db import transaction
from code_assist_v1.knowledge_core.legacy.blob_stager import (
    FilesystemLegacyBlobStager,
)
from code_assist_v1.knowledge_core.projection.temporal import (
    ProjectionManifestUntrustedError,
    TemporalFooterMetadata,
    TemporalProjectionMaterializer,
    TrustedTemporalProjectionImporter,
    render_temporal_page,
)
from code_assist_v1.knowledge_core.migrations import MigrationStorageLayout
from code_assist_v1.knowledge_core.scope import ScopeResolver
from code_assist_v1.knowledge_core.repositories.spaces import SpaceRepository
from code_assist_v1.knowledge_core.services.temporal import (
    TemporalAssertionRevokeCommand,
    TemporalReadRequest,
    TemporalService,
    VerificationAttestCommand,
    VerificationRevokeCommand,
)
from code_assist_v1.knowledge_core.services.retrieval import (
    CanonicalRetrievalService,
    SpaceRetrievalRequest,
)
from code_assist_v1.tests.knowledge_spaces.unit.test_item_lifecycle_service import (
    _flags,
    _seed,
    _seed_blob_backed_published_item,
)
from code_assist_v1.tests.knowledge_spaces.unit.test_temporal_service import (
    _assertion,
    _uuid,
)


pytestmark = pytest.mark.unit


def _metadata(**changes) -> TemporalFooterMetadata:
    base = TemporalFooterMetadata(
        registered_at_ms=0,
        content_modified_at_ms=1_000,
        revision_no=3,
    )
    return replace(base, **changes)


def _layout(isolated_knowledge_layout) -> MigrationStorageLayout:
    return MigrationStorageLayout(
        data_root=isolated_knowledge_layout.root,
        database=isolated_knowledge_layout.core_db_path,
        blob_root=isolated_knowledge_layout.blobs_dir,
        projection_root=isolated_knowledge_layout.projections_dir,
    )


def _ensure_team_space(fixture, *, label: str) -> None:
    connection = fixture.factory.open()
    try:
        with transaction(connection):
            team = connection.execute(
                """SELECT team.id FROM knowledge_space AS part_space
                     JOIN org_unit AS part ON part.id=part_space.owner_org_unit_id
                     JOIN org_unit AS team ON team.id=part.parent_id
                    WHERE part_space.id=?""",
                (fixture.space_id,),
            ).fetchone()
            assert team is not None
            SpaceRepository().ensure_higher(
                connection,
                space_id=_uuid(label),
                org_unit_id=str(team["id"]),
                kind="team",
                created_at_ms=400,
            )
    finally:
        connection.close()


@pytest.mark.scenario("TIME-007", "TIME-012", "TIME-013", "TIME-016")
def test_footer_v2_golden_bytes_and_lf_body_boundary() -> None:
    rendered = render_temporal_page(
        "한글\r\nbody\r",
        _metadata(
            source_modified_at={
                "precision": "instant",
                "value": "2026-08-24T01:02:03.004Z",
            },
            information_effective_at={
                "precision": "date",
                "timezone": "Asia/Seoul",
                "value": "2026-08-25",
            },
            last_verified_at_ms=2_000,
        ),
    )
    assert rendered == (
        "한글\nbody\n\n\n<!-- knowledge-footer:v2 -->\n"
        "---\n"
        "> **문서 정보**  \n"
        "> 생성: 1970-01-01T09:00:00.000+09:00  \n"
        "> 마지막 내용 수정: 1970-01-01T09:00:01.000+09:00  \n"
        "> Revision: 3  \n"
        "> 원문 수정: 2026-08-24T10:02:03.004+09:00  \n"
        "> 정보 기준: 2026-08-25  \n"
        "> 마지막 검증: 1970-01-01T09:00:02.000+09:00  \n"
        "<!-- /knowledge-footer:v2 -->\n"
    ).encode("utf-8")
    assert b"\r" not in rendered


@pytest.mark.parametrize("source", [False, True])
@pytest.mark.parametrize("effective", [False, True])
@pytest.mark.parametrize("verified", [False, True])
@pytest.mark.scenario("TIME-014", "TIME-016")
def test_footer_v2_all_eight_optional_row_combinations(
    source: bool,
    effective: bool,
    verified: bool,
) -> None:
    rendered = render_temporal_page(
        b"",
        _metadata(
            source_modified_at=(
                {"precision": "instant", "value": "2026-01-01T00:00:00.000Z"}
                if source
                else None
            ),
            information_effective_at=(
                {
                    "precision": "date",
                    "timezone": "Asia/Seoul",
                    "value": "2026-01-02",
                }
                if effective
                else None
            ),
            last_verified_at_ms=3_000 if verified else None,
        ),
    ).decode("utf-8")
    assert rendered.startswith("\n\n<!-- knowledge-footer:v2 -->\n")
    assert rendered.endswith("<!-- /knowledge-footer:v2 -->\n")
    assert ("> 원문 수정:" in rendered) is source
    assert ("> 정보 기준:" in rendered) is effective
    assert ("> 마지막 검증:" in rendered) is verified
    positions = [
        rendered.find(label)
        for label in ("> 원문 수정:", "> 정보 기준:", "> 마지막 검증:")
        if label in rendered
    ]
    assert positions == sorted(positions)


@pytest.mark.scenario(
    "TIME-008", "TIME-009", "TIME-010", "TIME-011", "TIME-014", "TIME-016"
)
def test_temporal_projection_manifest_is_stable_and_reimport_is_ledger_bound(
    isolated_knowledge_layout,
) -> None:
    fixture = _seed(isolated_knowledge_layout)
    service = TemporalService(fixture.factory, ScopeResolver(_flags()))
    asserted = service.assert_temporal(_assertion(fixture, label="projection"))
    attested = service.attest(
        VerificationAttestCommand(
            principal=fixture.principal("editor"),
            space_id=fixture.space_id,
            item_id=fixture.item_id,
            expected_revision_id=fixture.revision_id,
            expected_temporal_summary_sha256=asserted["temporal_summary_sha256"],
            expected_head_event_id=None,
            expected_head_generation=0,
            method="reviewed source",
            evidence_sha256=sha256(b"projection verification").hexdigest(),
            evidence_reference="ticket:PROJECTION-1",
            note=None,
            subject_verified_at=None,
            operation_id=_uuid("projection-attest-operation"),
            request_id=_uuid("projection-attest-request"),
        )
    )
    assert attested["last_verified_at"] is not None

    _ensure_team_space(fixture, label="projection-team-space")
    layout = _layout(isolated_knowledge_layout)
    materializer = TemporalProjectionMaterializer(
        fixture.factory, layout
    )
    first = materializer.rebuild_space(fixture.space_id)
    second = materializer.rebuild_space(fixture.space_id)
    assert second == first

    generation = (
        isolated_knowledge_layout.projections_dir
        / fixture.space_id
        / "temporal-generations"
        / f"{first.content_generation}-0-{first.temporal_generation}"
    )
    source = (generation / "sources" / f"{fixture.item_id}.md").read_bytes()
    page = (generation / "pages" / f"{fixture.item_id}.md").read_bytes()
    manifest_bytes = (generation / "manifest-v2.json").read_bytes()
    manifest = json.loads(manifest_bytes)
    assert b"knowledge-footer:v2" not in source
    assert b"knowledge-footer:v2" in page
    assert page.startswith(source + b"\n\n")
    assert "built_at" not in manifest
    assert manifest["snapshot_as_of_event_id"] == first.canonical_high_water
    assert manifest["content_generation"] == first.content_generation
    assert manifest["temporal_generation"] == first.temporal_generation
    assert manifest["graph_generation"] == 0
    assert manifest["items"][0]["projected_body_sha256"] == sha256(source).hexdigest()
    assert manifest["items"][0]["rendered_sha256"] == sha256(page).hexdigest()

    importer = TrustedTemporalProjectionImporter(
        fixture.factory, layout
    )
    assert importer.extract_body(
        rendered=page,
        manifest_bytes=manifest_bytes,
        item_id=fixture.item_id,
    ) == source
    with pytest.raises(ProjectionManifestUntrustedError):
        importer.extract_body(
            rendered=page + b"x",
            manifest_bytes=manifest_bytes,
            item_id=fixture.item_id,
        )

    self_declared = dict(manifest)
    self_declared["snapshot_as_of_event_id"] += 1
    forged_manifest = (
        json.dumps(
            self_declared,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
    )
    with pytest.raises(ProjectionManifestUntrustedError):
        importer.extract_body(
            rendered=page,
            manifest_bytes=forged_manifest,
            item_id=fixture.item_id,
        )

    current = service.read(
        TemporalReadRequest(
            principal=fixture.principal("editor"),
            space_id=fixture.space_id,
            item_id=fixture.item_id,
        )
    )
    assert current["last_verified_at"] == attested["last_verified_at"]

    retrieval = CanonicalRetrievalService(
        fixture.factory,
        scope_resolver=ScopeResolver(_flags()),
    ).retrieve_for_space(
        SpaceRetrievalRequest(
            actor_user_id="editor",
            actor_scope_epoch=fixture.epochs["editor"],
            space_id=fixture.space_id,
            query="Lifecycle",
        )
    )
    assert retrieval.hits
    assert all(
        "knowledge-footer:v2" not in hit.excerpt_md for hit in retrieval.hits
    )


@pytest.mark.scenario("TIME-007", "TIME-009", "TIME-012", "TIME-016")
def test_temporal_projection_reads_verified_blob_backed_body_without_byte_drift(
    isolated_knowledge_layout,
) -> None:
    fixture = _seed(isolated_knowledge_layout)
    item_id, _revision_id, body = _seed_blob_backed_published_item(fixture)
    digest = sha256(body).hexdigest()
    layout = _layout(isolated_knowledge_layout)
    observation = FilesystemLegacyBlobStager(layout).stage_and_verify(
        body,
        expected_sha256=digest,
        media_type="text/markdown",
        effective_at_ms=500,
        normalized_content_sha256=digest,
        normalizer_version="markdown_lf_v1",
    )
    observation.lease.close()
    _ensure_team_space(fixture, label="blob-projection-team-space")

    result = TemporalProjectionMaterializer(
        fixture.factory, layout
    ).rebuild_space(fixture.space_id)
    generation = (
        isolated_knowledge_layout.projections_dir
        / fixture.space_id
        / "temporal-generations"
        / f"{result.content_generation}-0-{result.temporal_generation}"
    )
    source = (generation / "sources" / f"{item_id}.md").read_bytes()
    page = (generation / "pages" / f"{item_id}.md").read_bytes()
    manifest = json.loads((generation / "manifest-v2.json").read_bytes())
    entry = next(row for row in manifest["items"] if row["item_id"] == item_id)

    assert source == body
    assert page.startswith(body + b"\n\n<!-- knowledge-footer:v2 -->\n")
    assert entry["projected_body_size_bytes"] == len(body)
    assert entry["projected_body_sha256"] == digest
    assert entry["rendered_size_bytes"] == len(page)
    assert entry["rendered_sha256"] == sha256(page).hexdigest()


@pytest.mark.scenario("TIME-006", "TIME-014", "TIME-019")
def test_temporal_projection_log_has_each_head_transition_exactly_once(
    isolated_knowledge_layout,
) -> None:
    fixture = _seed(isolated_knowledge_layout)
    service = TemporalService(fixture.factory, ScopeResolver(_flags()))
    first = service.assert_temporal(_assertion(fixture, label="log-first"))
    attested = service.attest(
        VerificationAttestCommand(
            principal=fixture.principal("editor"),
            space_id=fixture.space_id,
            item_id=fixture.item_id,
            expected_revision_id=fixture.revision_id,
            expected_temporal_summary_sha256=first["temporal_summary_sha256"],
            expected_head_event_id=None,
            expected_head_generation=0,
            method="log fixture review",
            evidence_sha256=sha256(b"log verification").hexdigest(),
            evidence_reference="ticket:LOG-1",
            note=None,
            subject_verified_at=None,
            operation_id=_uuid("log-attest-operation"),
            request_id=_uuid("log-attest-request"),
        )
    )
    second = service.assert_temporal(
        replace(
            _assertion(fixture, label="log-second"),
            value={"precision": "instant", "value": "2026-08-25T01:02:03.004Z"},
            expected_head_event_id=first["assertion_id"],
            expected_head_generation=1,
        )
    )
    service.revoke_assertion(
        TemporalAssertionRevokeCommand(
            principal=fixture.principal("editor"),
            space_id=fixture.space_id,
            item_id=fixture.item_id,
            expected_revision_id=fixture.revision_id,
            assertion_id=second["assertion_id"],
            expected_head_generation=2,
            reason="log assertion withdrawn",
            operation_id=_uuid("log-assertion-revoke-operation"),
            request_id=_uuid("log-assertion-revoke-request"),
        )
    )
    service.revoke_verification(
        VerificationRevokeCommand(
            principal=fixture.principal("editor"),
            space_id=fixture.space_id,
            item_id=fixture.item_id,
            expected_revision_id=fixture.revision_id,
            attestation_id=attested["attestation_id"],
            expected_head_generation=1,
            reason="log verification withdrawn",
            operation_id=_uuid("log-verification-revoke-operation"),
            request_id=_uuid("log-verification-revoke-request"),
        )
    )
    _ensure_team_space(fixture, label="log-projection-team-space")
    layout = _layout(isolated_knowledge_layout)
    result = TemporalProjectionMaterializer(
        fixture.factory, layout
    ).rebuild_space(fixture.space_id)
    generation = (
        isolated_knowledge_layout.projections_dir
        / fixture.space_id
        / "temporal-generations"
        / f"{result.content_generation}-0-{result.temporal_generation}"
    )
    records = [
        json.loads(line.strip())
        for line in (generation / "log.md").read_text(encoding="utf-8").splitlines()
        if line.startswith("    {")
    ]
    temporal_records = [
        (record["action"], record["outcome"])
        for record in records
        if record["action"].startswith("temporal.")
        or record["action"].startswith("verification.")
    ]
    assert temporal_records == [
        ("temporal.assert", "asserted"),
        ("verification.attest", "attested"),
        ("temporal.assert", "superseded"),
        ("temporal.revoke", "revoked"),
        ("verification.revoke", "revoked"),
    ]
