from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from hashlib import sha256
from threading import Barrier
from uuid import UUID, uuid5

import pytest

from code_assist_v1.knowledge_core.domain import (
    TemporalAssertionKind,
    TemporalAssertionOrigin,
    TemporalPrecision,
)
from code_assist_v1.knowledge_core.repositories.temporal import TemporalAssertionRecord
from code_assist_v1.knowledge_core.scope import ScopeResolver
from code_assist_v1.knowledge_core.services.temporal import (
    TemporalAssertionCommand,
    TemporalAssertionRevokeCommand,
    TemporalHeadConflictError,
    TemporalIdempotencyError,
    TemporalReadRequest,
    TemporalService,
    TemporalValueInvalidError,
    VerificationAttestCommand,
    VerificationNoncurrentError,
    VerificationRevokeCommand,
    normalize_notion_source_timestamp,
    parse_temporal_value,
    temporal_summary_document,
    temporal_summary_sha256,
)
from code_assist_v1.knowledge_core.services.curation_worker import (
    CanonicalCurationRescanHandler,
)
from code_assist_v1.knowledge_core.worker import DurableWorkKernel, HandlerRegistry
from code_assist_v1.tests.knowledge_spaces.unit.test_item_lifecycle_service import (
    _flags,
    _seed,
)
from code_assist_v1.tests.knowledge_spaces.unit.test_item_edit_service import (
    _acquire as _edit_acquire,
    _make_published as _edit_make_published,
    _save_command as _edit_save_command,
    _seed as _edit_seed,
)


pytestmark = pytest.mark.unit

_NAMESPACE = UUID("348c6691-f8e8-4266-966c-e657381d48f4")


def _uuid(label: str) -> str:
    return str(uuid5(_NAMESPACE, label))


def _service(fixture) -> TemporalService:
    return TemporalService(fixture.factory, ScopeResolver(_flags()))


def _assertion(fixture, *, label: str = "source") -> TemporalAssertionCommand:
    return TemporalAssertionCommand(
        principal=fixture.principal("editor"),
        space_id=fixture.space_id,
        item_id=fixture.item_id,
        expected_revision_id=fixture.revision_id,
        assertion_kind=TemporalAssertionKind.SOURCE_MODIFIED,
        value={"precision": "instant", "value": "2026-08-24T01:02:03.004Z"},
        expected_head_event_id=None,
        expected_head_generation=0,
        origin=TemporalAssertionOrigin.USER,
        evidence_sha256=sha256(b"source evidence").hexdigest(),
        evidence_reference="user supplied source metadata",
        operation_id=_uuid(f"assert-operation:{label}"),
        request_id=_uuid(f"assert-request:{label}"),
    )


@pytest.mark.scenario("TIME-004", "TIME-013", "TIME-017")
def test_temporal_value_parser_and_notion_adapter_are_exact() -> None:
    instant = parse_temporal_value(
        {"precision": "instant", "value": "2026-08-24T10:02:03.004+09:00"}
    )
    assert instant.tagged() == {
        "precision": "instant",
        "value": "2026-08-24T01:02:03.004Z",
    }
    assert parse_temporal_value(
        {"precision": "date", "value": "2024-02-29", "timezone": "Asia/Seoul"}
    ).tagged() == {
        "precision": "date",
        "timezone": "Asia/Seoul",
        "value": "2024-02-29",
    }
    assert normalize_notion_source_timestamp(1_725_000_000) == 1_725_000_000_000

    for invalid in (
        {"precision": "date", "value": "2026-02-31", "timezone": "Asia/Seoul"},
        {"precision": "instant", "value": "2026-08-24T01:02:03Z", "timezone": "UTC"},
        {"precision": "instant", "value": "2026-08-24T01:02:03.000"},
    ):
        with pytest.raises(TemporalValueInvalidError):
            parse_temporal_value(invalid)
    for invalid in (True, 999_999_999, 1_725_000_000_000, "1725000000"):
        with pytest.raises(TemporalValueInvalidError):
            normalize_notion_source_timestamp(invalid)


@pytest.mark.scenario("TIME-005")
def test_temporal_summary_golden_hashes_cover_every_head_state() -> None:
    revision_id = "11111111-1111-1111-1111-111111111111"

    def record(
        event_id: str,
        event_kind: str,
        generation: int,
        *,
        precision: TemporalPrecision | None = None,
        instant_ms: int | None = None,
        date_value: str | None = None,
    ) -> TemporalAssertionRecord:
        return TemporalAssertionRecord(
            event_sequence=0,
            id=event_id,
            space_id="22222222-2222-2222-2222-222222222222",
            item_id="33333333-3333-3333-3333-333333333333",
            revision_id=revision_id,
            assertion_kind=TemporalAssertionKind.SOURCE_MODIFIED,
            event_kind=event_kind,
            value_precision=precision,
            instant_value_ms=instant_ms,
            date_value=date_value,
            source_timezone=(
                "UTC"
                if precision is TemporalPrecision.INSTANT
                else "Asia/Seoul" if precision is TemporalPrecision.DATE else None
            ),
            source_utc_offset_minutes=(
                0 if precision is TemporalPrecision.INSTANT else None
            ),
            origin=TemporalAssertionOrigin.USER,
            evidence_sha256="a" * 64,
            evidence_reference="fixture",
            predecessor_event_id=(
                None
                if generation == 1
                else "00000000-0000-0000-0000-000000000000"
            ),
            head_generation=generation,
            actor_user_id="actor",
            operation_id="44444444-4444-4444-4444-444444444444",
            effect_key="effect",
            temporal_summary_sha256="b" * 64,
            audit_event_id=1,
            outbox_event_id=1,
            committed_at_ms=0,
        )

    states = (
        (
            None,
            "5eaf012821a30db115a8fd0948531593681b32fe96e609492747f9147c9b9155",
            None,
        ),
        (
            record(
                "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                "assert",
                1,
                precision=TemporalPrecision.INSTANT,
                instant_ms=1_787_533_323_004,
            ),
            "e6ef0fd766f400101734216287fefc0e5384d11b1daa4cf43f9ba2cabb933627",
            "asserted",
        ),
        (
            record(
                "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
                "assert",
                2,
                precision=TemporalPrecision.DATE,
                date_value="2026-08-25",
            ),
            "c0c3ad7f0023bb85dcce87a245292a2fd92431ff9aedb149482e74ceda623ab8",
            "asserted",
        ),
        (
            record(
                "cccccccc-cccc-cccc-cccc-cccccccccccc",
                "revoke",
                3,
            ),
            "b082c213d444f0e17421effc1ae4f27c13cfa3c5d709c0612f44e0f532b9eaaa",
            "revoked",
        ),
    )
    for source, expected_hash, expected_state in states:
        document = temporal_summary_document(
            revision_id=revision_id,
            source_modified=source,
            information_effective=None,
        )
        assert set(document) == {
            "information_effective_head",
            "revision_id",
            "schema_version",
            "source_modified_head",
        }
        head = document["source_modified_head"]
        assert (None if head is None else head["state"]) == expected_state
        assert temporal_summary_sha256(
            revision_id=revision_id,
            source_modified=source,
            information_effective=None,
        ) == expected_hash


@pytest.mark.scenario("TIME-004", "TIME-018")
def test_reviewed_legacy_time_requires_explicit_source_evidence_and_never_infers_effective(
    isolated_knowledge_layout,
) -> None:
    fixture = _seed(isolated_knowledge_layout)
    service = _service(fixture)
    asserted = service.assert_temporal(
        replace(
            _assertion(fixture, label="reviewed-legacy"),
            origin=TemporalAssertionOrigin.REVIEWED_LEGACY_MANIFEST,
            evidence_sha256=sha256(b"reviewed legacy manifest mtime").hexdigest(),
            evidence_reference="reviewed-manifest:legacy-source-17",
        )
    )
    current = service.read(
        TemporalReadRequest(
            principal=fixture.principal("editor"),
            space_id=fixture.space_id,
            item_id=fixture.item_id,
        )
    )
    assert current["source_modified_at"] == asserted["value"]
    assert current["information_effective_at"] is None

    with pytest.raises(TemporalValueInvalidError, match="source_modified only"):
        service.assert_temporal(
            replace(
                _assertion(fixture, label="invalid-import-effective"),
                assertion_kind=TemporalAssertionKind.INFORMATION_EFFECTIVE,
                origin=TemporalAssertionOrigin.SYSTEM_IMPORT,
                value={
                    "precision": "date",
                    "timezone": "Asia/Seoul",
                    "value": "2026-08-25",
                },
                expected_head_event_id=None,
                expected_head_generation=0,
            )
        )


@pytest.mark.scenario("TIME-002", "TIME-003", "TIME-005", "TIME-006", "TIME-019")
def test_assert_attest_revoke_and_read_preserve_content_identity(
    isolated_knowledge_layout,
) -> None:
    fixture = _seed(isolated_knowledge_layout)
    service = _service(fixture)
    connection = fixture.factory.open(read_only=True)
    try:
        before = connection.execute(
            """SELECT s.generation,s.temporal_generation,r.normalized_content_sha256,
                      r.revision_no,i.current_revision_id
                 FROM knowledge_space AS s
                 JOIN knowledge_item AS i ON i.space_id=s.id
                 JOIN knowledge_revision AS r ON r.id=i.current_revision_id
                WHERE s.id=? AND i.id=?""",
            (fixture.space_id, fixture.item_id),
        ).fetchone()
        before_search = connection.execute(
            "SELECT title,content_md FROM search_document WHERE item_id=?",
            (fixture.item_id,),
        ).fetchone()
    finally:
        connection.close()

    assert before is not None

    command = _assertion(fixture)
    asserted = service.assert_temporal(command)
    assert asserted["head_generation"] == 1
    assert asserted["value"] == {
        "precision": "instant",
        "value": "2026-08-24T01:02:03.004Z",
    }
    assert service.assert_temporal(command) == asserted
    with pytest.raises(TemporalIdempotencyError):
        service.assert_temporal(
            replace(
                command,
                value={
                    "precision": "instant",
                    "value": "2026-08-24T01:02:04.004Z",
                },
            )
        )

    current = service.read(
        TemporalReadRequest(
            principal=fixture.principal("editor"),
            space_id=fixture.space_id,
            item_id=fixture.item_id,
        )
    )
    assert current["source_modified_at"] == asserted["value"]
    assert current["last_verified_at"] is None
    assert current["registered_at"].endswith("Z")
    assert current["content_modified_at"].endswith("Z")

    attested = service.attest(
        VerificationAttestCommand(
            principal=fixture.principal("editor"),
            space_id=fixture.space_id,
            item_id=fixture.item_id,
            expected_revision_id=fixture.revision_id,
            expected_temporal_summary_sha256=asserted["temporal_summary_sha256"],
            expected_head_event_id=None,
            expected_head_generation=0,
            method="human source comparison",
            evidence_sha256=sha256(b"verification evidence").hexdigest(),
            evidence_reference="ticket:TEMP-1",
            note="checked",
            subject_verified_at="2026-08-24T02:00:00.000Z",
            operation_id=_uuid("attest-operation"),
            request_id=_uuid("attest-request"),
        )
    )
    after_attest = service.read(
        TemporalReadRequest(
            principal=fixture.principal("editor"),
            space_id=fixture.space_id,
            item_id=fixture.item_id,
        )
    )
    assert after_attest["last_verified_at"] == attested["last_verified_at"]
    assert after_attest["temporal_summary_sha256"] == asserted["temporal_summary_sha256"]

    with pytest.raises(VerificationNoncurrentError):
        service.attest(
            replace(
                VerificationAttestCommand(
                    principal=fixture.principal("editor"),
                    space_id=fixture.space_id,
                    item_id=fixture.item_id,
                    expected_revision_id=fixture.revision_id,
                    expected_temporal_summary_sha256="0" * 64,
                    expected_head_event_id=attested["attestation_id"],
                    expected_head_generation=1,
                    method="human source comparison",
                    evidence_sha256=sha256(b"other").hexdigest(),
                    evidence_reference="ticket:TEMP-2",
                    note=None,
                    subject_verified_at=None,
                    operation_id=_uuid("stale-attest-operation"),
                    request_id=_uuid("stale-attest-request"),
                )
            )
        )

    revoked_assertion = service.revoke_assertion(
        TemporalAssertionRevokeCommand(
            principal=fixture.principal("editor"),
            space_id=fixture.space_id,
            item_id=fixture.item_id,
            expected_revision_id=fixture.revision_id,
            assertion_id=asserted["assertion_id"],
            expected_head_generation=1,
            reason="source retracted",
            operation_id=_uuid("assert-revoke-operation"),
            request_id=_uuid("assert-revoke-request"),
        )
    )
    after_assertion_revoke = service.read(
        TemporalReadRequest(
            principal=fixture.principal("editor"),
            space_id=fixture.space_id,
            item_id=fixture.item_id,
        )
    )
    assert revoked_assertion["value"] is None
    assert after_assertion_revoke["source_modified_at"] is None
    assert after_assertion_revoke["last_verified_at"] is None

    revoked_verification = service.revoke_verification(
        VerificationRevokeCommand(
            principal=fixture.principal("editor"),
            space_id=fixture.space_id,
            item_id=fixture.item_id,
            expected_revision_id=fixture.revision_id,
            attestation_id=attested["attestation_id"],
            expected_head_generation=1,
            reason="verification superseded",
            operation_id=_uuid("verification-revoke-operation"),
            request_id=_uuid("verification-revoke-request"),
        )
    )
    assert revoked_verification["last_verified_at"] is None

    connection = fixture.factory.open(read_only=True)
    try:
        after = connection.execute(
            """SELECT s.generation,s.temporal_generation,r.normalized_content_sha256,
                      r.revision_no,i.current_revision_id
                 FROM knowledge_space AS s
                 JOIN knowledge_item AS i ON i.space_id=s.id
                 JOIN knowledge_revision AS r ON r.id=i.current_revision_id
                WHERE s.id=? AND i.id=?""",
            (fixture.space_id, fixture.item_id),
        ).fetchone()
        after_search = connection.execute(
            "SELECT title,content_md FROM search_document WHERE item_id=?",
            (fixture.item_id,),
        ).fetchone()
        counts = connection.execute(
            """SELECT
                 (SELECT COUNT(*) FROM revision_temporal_assertion),
                 (SELECT COUNT(*) FROM revision_verification_attestation),
                 (SELECT desired_generation FROM projection_state
                   WHERE projection_name='markdown_v2' AND space_id=?)""",
            (fixture.space_id,),
        ).fetchone()
    finally:
        connection.close()

    superseded = service.assert_temporal(
        replace(
            _assertion(fixture, label="supersede"),
            value={"precision": "instant", "value": "2026-08-25T01:02:03.004Z"},
            expected_head_event_id=revoked_assertion["assertion_id"],
            expected_head_generation=2,
        )
    )
    assert superseded["head_generation"] == 3
    service.revoke_assertion(
        TemporalAssertionRevokeCommand(
            principal=fixture.principal("editor"),
            space_id=fixture.space_id,
            item_id=fixture.item_id,
            expected_revision_id=fixture.revision_id,
            assertion_id=superseded["assertion_id"],
            expected_head_generation=3,
            reason="superseded source withdrawn",
            operation_id=_uuid("supersede-revoke-operation"),
            request_id=_uuid("supersede-revoke-request"),
        )
    )
    current = service.read(
        TemporalReadRequest(
            principal=fixture.principal("editor"),
            space_id=fixture.space_id,
            item_id=fixture.item_id,
        )
    )
    assert current["source_modified_at"] is None
    connection = fixture.factory.open(read_only=True)
    try:
        assert [
            row[0]
            for row in connection.execute(
                """SELECT outcome FROM audit_event
                     WHERE action IN ('temporal.assert','temporal.revoke')
                     ORDER BY id"""
            ).fetchall()
        ] == ["asserted", "revoked", "superseded", "revoked"]
        assert connection.execute(
            "SELECT COUNT(*) FROM revision_temporal_assertion"
        ).fetchone()[0] == 4
    finally:
        connection.close()
    assert tuple(after)[0] == tuple(before)[0]
    assert tuple(after)[1] == tuple(before)[1] + 4
    assert tuple(after)[2:] == tuple(before)[2:]
    assert tuple(after_search) == tuple(before_search)
    assert tuple(counts) == (2, 2, tuple(after)[0] + tuple(after)[1])


@pytest.mark.scenario("TIME-006", "TIME-019")
def test_stale_assertion_head_rolls_back_all_side_effects(
    isolated_knowledge_layout,
) -> None:
    fixture = _seed(isolated_knowledge_layout)
    service = _service(fixture)
    asserted = service.assert_temporal(_assertion(fixture))

    stale = replace(
        _assertion(fixture, label="stale"),
        expected_head_event_id=None,
        expected_head_generation=0,
    )
    with pytest.raises(TemporalHeadConflictError):
        service.assert_temporal(stale)

    connection = fixture.factory.open(read_only=True)
    try:
        assert connection.execute(
            "SELECT COUNT(*) FROM revision_temporal_assertion"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT temporal_generation FROM knowledge_space WHERE id=?",
            (fixture.space_id,),
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT head_event_id FROM revision_temporal_head WHERE revision_id=?",
            (fixture.revision_id,),
        ).fetchone()[0] == asserted["assertion_id"]
    finally:
        connection.close()


@pytest.mark.scenario("TIME-006", "TIME-019")
def test_concurrent_temporal_assertion_cas_has_one_winner_and_no_partial_effects(
    isolated_knowledge_layout,
) -> None:
    fixture = _seed(isolated_knowledge_layout)
    barrier = Barrier(2)

    def attempt(label: str) -> tuple[str, str | None]:
        service = _service(fixture)
        command = replace(
            _assertion(fixture, label=label),
            value={
                "precision": "instant",
                "value": (
                    "2026-08-24T01:02:03.004Z"
                    if label == "left"
                    else "2026-08-24T01:02:04.004Z"
                ),
            },
        )
        barrier.wait(timeout=10)
        try:
            result = service.assert_temporal(command)
        except TemporalHeadConflictError:
            return ("conflict", None)
        return ("committed", str(result["assertion_id"]))

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = tuple(pool.map(attempt, ("left", "right")))

    assert sorted(status for status, _event_id in results) == [
        "committed",
        "conflict",
    ]
    winning_event_id = next(
        event_id for status, event_id in results if status == "committed"
    )
    connection = fixture.factory.open(read_only=True)
    try:
        assert connection.execute(
            "SELECT COUNT(*) FROM revision_temporal_assertion"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM audit_event WHERE action='temporal.assert'"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM outbox_event WHERE event_type='knowledge.temporal.asserted'"
        ).fetchone()[0] == 1
        head = connection.execute(
            """SELECT head_event_id,head_generation
                 FROM revision_temporal_head WHERE revision_id=?""",
            (fixture.revision_id,),
        ).fetchone()
        assert tuple(head) == (winning_event_id, 1)
        assert connection.execute(
            "SELECT temporal_generation FROM knowledge_space WHERE id=?",
            (fixture.space_id,),
        ).fetchone()[0] == 1
    finally:
        connection.close()


@pytest.mark.scenario("TIME-003", "TIME-005", "TIME-006")
def test_new_content_revision_inherits_no_temporal_or_verification_head(
    isolated_knowledge_layout,
) -> None:
    fixture = _edit_seed(isolated_knowledge_layout)
    _edit_make_published(fixture, label="temporal-original")
    temporal = TemporalService(fixture.factory, ScopeResolver(_flags()))
    asserted = temporal.assert_temporal(
        TemporalAssertionCommand(
            principal=fixture.principal("editor"),
            space_id=fixture.space_id,
            item_id=fixture.item_id,
            expected_revision_id=fixture.revision_id,
            assertion_kind=TemporalAssertionKind.INFORMATION_EFFECTIVE,
            value={
                "precision": "date",
                "timezone": "Asia/Seoul",
                "value": "2026-08-25",
            },
            expected_head_event_id=None,
            expected_head_generation=0,
            origin=TemporalAssertionOrigin.USER,
            evidence_sha256=sha256(b"effective original").hexdigest(),
            evidence_reference="approved original",
            operation_id=_uuid("revision-original-assert"),
            request_id=_uuid("revision-original-assert-request"),
        )
    )
    temporal.attest(
        VerificationAttestCommand(
            principal=fixture.principal("editor"),
            space_id=fixture.space_id,
            item_id=fixture.item_id,
            expected_revision_id=fixture.revision_id,
            expected_temporal_summary_sha256=asserted["temporal_summary_sha256"],
            expected_head_event_id=None,
            expected_head_generation=0,
            method="original review",
            evidence_sha256=sha256(b"verified original").hexdigest(),
            evidence_reference="ticket:REVISION-1",
            note=None,
            subject_verified_at=None,
            operation_id=_uuid("revision-original-attest"),
            request_id=_uuid("revision-original-attest-request"),
        )
    )

    editor, acquire_command, acquired = _edit_acquire(
        fixture, label="temporal-new-revision"
    )
    saved = editor.save(
        _edit_save_command(
            fixture,
            acquire_command,
            acquired,
            label="temporal-new-revision",
            expected_row_version=2,
        )
    )
    new_fixture = replace(fixture, revision_id=saved.revision_id)
    scope = ScopeResolver(_flags())
    kernel = DurableWorkKernel(
        fixture.factory,
        HandlerRegistry(
            (
                CanonicalCurationRescanHandler(
                    fixture.factory, scope_resolver=scope
                ).registered_handler(),
            )
        ),
        scope_resolver=scope,
    )
    item_state = None
    for index in range(5):
        _recovery, claim = kernel.recover_and_claim(
            claim_owner=f"temporal-revision-curation-{index}",
            lease_ms=30_000,
        )
        if claim is None:
            break
        kernel.run_claim(claim)
        connection = fixture.factory.open(read_only=True)
        try:
            item_state = connection.execute(
                """SELECT current_revision_id,curation_status
                     FROM knowledge_item WHERE id=?""",
                (fixture.item_id,),
            ).fetchone()
        finally:
            connection.close()
        if tuple(item_state) == (saved.revision_id, "published"):
            break
    assert item_state is not None
    assert tuple(item_state) == (saved.revision_id, "published")

    current = temporal.read(
        TemporalReadRequest(
            principal=new_fixture.principal("editor"),
            space_id=new_fixture.space_id,
            item_id=new_fixture.item_id,
        )
    )
    assert current["revision_id"] == saved.revision_id
    assert current["revision_no"] == 2
    assert current["source_modified_at"] is None
    assert current["information_effective_at"] is None
    assert current["last_verified_at"] is None

    connection = fixture.factory.open(read_only=True)
    try:
        assert connection.execute(
            """SELECT COUNT(*) FROM revision_temporal_assertion
                 WHERE revision_id=?""",
            (fixture.revision_id,),
        ).fetchone()[0] == 1
        assert connection.execute(
            """SELECT COUNT(*) FROM revision_verification_attestation
                 WHERE revision_id=?""",
            (fixture.revision_id,),
        ).fetchone()[0] == 1
        assert connection.execute(
            """SELECT COUNT(*) FROM revision_temporal_head
                 WHERE revision_id=?""",
            (saved.revision_id,),
        ).fetchone()[0] == 0
        assert connection.execute(
            """SELECT COUNT(*) FROM revision_verification_head
                 WHERE revision_id=?""",
            (saved.revision_id,),
        ).fetchone()[0] == 0
    finally:
        connection.close()
