from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib

import pytest

from code_assist_v1.knowledge_core.db import transaction
from code_assist_v1.knowledge_core.repositories.findings import FindingRepository
from code_assist_v1.knowledge_core.repositories.proposals import (
    fixed_preview_payload,
    sha256_canonical,
)
from code_assist_v1.knowledge_core.scope import ScopeResolver
from code_assist_v1.knowledge_core.services.editing import (
    AcquireLeaseCommand,
    LeaseHmacKeyRing,
    LeaseIdempotencyError,
)
from code_assist_v1.knowledge_core.services.proposals import (
    ProposalApplyCommand,
    ProposalPreviewCommand,
    ProposalPreviewStaleError,
    ProposalRebaseCommand,
    ProposalReviewStateError,
    TransactionalProposalService,
)
from code_assist_v1.knowledge_core.services.publication import (
    CreateManualPreviewCommand,
    PublicationPreviewService,
    PublicationService,
    PublishPreviewCommand,
)
from code_assist_v1.tests.knowledge_spaces.unit.test_item_edit_service import (
    _Fixture,
    _KEY,
    _Stager,
    _acquire,
    _assert_postconditions,
    _flags,
    _save_command,
    _seed,
    _token,
    _uuid,
)


pytestmark = pytest.mark.unit


@dataclass(frozen=True)
class _ProposalFixture:
    source: _Fixture
    target: _Fixture
    source_artifact_id: str
    extraction_snapshot_id: str
    extraction_snapshot_sha256: str


def _both_fixture(layout) -> _ProposalFixture:
    seeded = _seed(layout)
    resolver = ScopeResolver(_flags())
    preview = PublicationPreviewService(
        seeded.factory, resolver, _Stager()
    ).create_manual_preview(
        CreateManualPreviewCommand(
            request_id=_uuid("proposal-preview-request"),
            operation_id=_uuid("proposal-preview-operation"),
            actor_user_id="editor",
            actor_scope_epoch=seeded.epochs["editor"],
            markdown="# Shared fork base\nInitial shared body.\n",
            request_meta={},
        )
    )
    result = PublicationService(seeded.factory, resolver).publish(
        PublishPreviewCommand(
            request_id=_uuid("proposal-publish-request"),
            operation_id=_uuid("proposal-publish-operation"),
            actor_user_id="editor",
            actor_scope_epoch=seeded.epochs["editor"],
            preview_id=preview.preview_id,
            source_snapshot_sha256=preview.source_snapshot_sha256,
            target_mode="both",
            request_meta={},
        )
    )
    assert len(result.targets) == 2
    by_kind: dict[str, tuple[str, str, str]] = {}
    connection = seeded.factory.open(read_only=True)
    try:
        for target in result.targets:
            row = connection.execute(
                """SELECT s.space_kind,i.space_id
                     FROM knowledge_item i JOIN knowledge_space s ON s.id=i.space_id
                    WHERE i.id=?""",
                (target["item_id"],),
            ).fetchone()
            assert row is not None
            by_kind[str(row[0])] = (
                str(row[1]),
                str(target["item_id"]),
                str(target["resolved_revision_id"]),
            )
    finally:
        connection.close()
    personal_space, personal_item, personal_revision = by_kind["personal"]
    part_space, part_item, part_revision = by_kind["part"]
    return _ProposalFixture(
        source=_Fixture(
            seeded.factory,
            seeded.part_id,
            personal_space,
            personal_item,
            personal_revision,
            seeded.epochs,
        ),
        target=_Fixture(
            seeded.factory,
            seeded.part_id,
            part_space,
            part_item,
            part_revision,
            seeded.epochs,
        ),
        source_artifact_id=preview.artifact_id,
        extraction_snapshot_id=preview.extraction_snapshot_id,
        extraction_snapshot_sha256=preview.source_snapshot_sha256,
    )


def _insert_proposal(
    fixture: _ProposalFixture,
    *,
    kind: str,
    label: str,
    proposed_blob_markdown: str | None = None,
) -> tuple[str, str, str]:
    proposal_id = _uuid(f"proposal:{label}")
    finding_id = _uuid(f"proposal-finding:{label}")
    rule_version = f"proposal-rule:{label}"
    connection = fixture.target.factory.open()
    try:
        with transaction(connection):
            target = connection.execute(
                """SELECT i.lineage_group_id,i.current_revision_id,b.content_md,
                          b.content_blob_hash,b.content_size_bytes,b.content_sha256
                     FROM knowledge_item i JOIN knowledge_revision_body b
                       ON b.item_id=i.id AND b.revision_id=i.current_revision_id
                    WHERE i.id=?""",
                (fixture.target.item_id,),
            ).fetchone()
            source = connection.execute(
                """SELECT i.current_revision_id,b.content_md,b.content_blob_hash,
                          b.content_size_bytes,b.content_sha256
                     FROM knowledge_item i JOIN knowledge_revision_body b
                       ON b.item_id=i.id AND b.revision_id=i.current_revision_id
                    WHERE i.id=?""",
                (fixture.source.item_id,),
            ).fetchone()
            assert target is not None and source is not None
            if kind == "personal_to_part":
                member = connection.execute(
                    """SELECT derived_from_item_id,derived_from_revision_id
                         FROM lineage_member WHERE lineage_group_id=? AND item_id=?""",
                    (target["lineage_group_id"], fixture.target.item_id),
                ).fetchone()
                assert member is not None
                base_item_id, base_revision_id = str(member[0]), str(member[1])
                source_item_id = fixture.source.item_id
                source_revision_id = str(source["current_revision_id"])
                lineage_group_id = str(target["lineage_group_id"])
                source_artifact_id = None
                extraction_snapshot_id = None
                extraction_sha = None
                proposed_text = (
                    "# Reviewed personal update\nPart-safe result.\n"
                    if proposed_blob_markdown is None
                    else proposed_blob_markdown
                )
                proposed_bytes = proposed_text.encode("utf-8")
                proposed_sha = hashlib.sha256(proposed_bytes).hexdigest()
                proposed_size = len(proposed_bytes)
                proposed_md = (
                    proposed_text if proposed_blob_markdown is None else None
                )
                proposed_blob = (
                    None if proposed_blob_markdown is None else proposed_sha
                )
            else:
                assert proposed_blob_markdown is None
                extraction = connection.execute(
                    """SELECT b.content_md,b.content_blob_hash,b.content_size_bytes,
                              b.content_sha256
                         FROM extraction_snapshot_body b
                        WHERE b.snapshot_id=? AND b.artifact_id=?""",
                    (
                        fixture.extraction_snapshot_id,
                        fixture.source_artifact_id,
                    ),
                ).fetchone()
                assert extraction is not None
                base_item_id = fixture.target.item_id
                base_revision_id = str(target["current_revision_id"])
                source_item_id = None
                source_revision_id = None
                lineage_group_id = None
                source_artifact_id = fixture.source_artifact_id
                extraction_snapshot_id = fixture.extraction_snapshot_id
                extraction_sha = fixture.extraction_snapshot_sha256
                proposed_md = extraction["content_md"]
                proposed_blob = extraction["content_blob_hash"]
                proposed_size = int(extraction["content_size_bytes"])
                proposed_sha = str(extraction["content_sha256"])
            target_revision_id = str(target["current_revision_id"])
            if proposed_blob is not None:
                connection.execute(
                    """INSERT INTO blob_object(
                         sha256,size_bytes,media_type,relative_path,
                         verification_status,created_at,verified_at)
                       VALUES (?,?,'text/markdown',?,'verified',499,499)""",
                    (
                        proposed_blob,
                        proposed_size,
                        f"{proposed_blob[:2]}/{proposed_blob[2:4]}/{proposed_blob}",
                    ),
                )
            preview_sha = sha256_canonical(
                fixed_preview_payload(
                    proposal_kind=kind,
                    proposal_version=1,
                    base_item_id=base_item_id,
                    base_revision_id=base_revision_id,
                    source_item_id=source_item_id,
                    source_revision_id=source_revision_id,
                    target_item_id=fixture.target.item_id,
                    target_revision_id=target_revision_id,
                    source_artifact_id=source_artifact_id,
                    extraction_snapshot_id=extraction_snapshot_id,
                    extraction_snapshot_sha256=extraction_sha,
                    proposed_content_sha256=proposed_sha,
                    finding_input_row_version=1,
                    rule_version=rule_version,
                )
            )
            connection.execute(
                """INSERT INTO curation_finding(
                     id,space_id,finding_kind,risk_tier,severity,status,
                     decision_mode,required_reviewer,visibility_effect,
                     proposed_action,source_item_id,source_revision_id,
                     target_item_id,target_revision_id,detector_name,
                     detector_version,rule_version,model_id,prompt_version,
                     confidence,evidence_json,proposed_action_json,reviewed_by,
                     reviewed_at,review_reason,applied_operation_id,
                     applied_dedup_id,applied_job_id,applied_by,applied_at,
                     last_application_error_json,created_actor_kind,created_by,
                     created_at,updated_at,row_version)
                   VALUES (?,?,'update','T2','info','pending','human','space_editor',
                     'agent_hidden','update',?,?,NULL,NULL,'proposal-test','1',?,
                     NULL,NULL,1.0,'{}','{}',NULL,NULL,NULL,NULL,NULL,NULL,NULL,
                     NULL,NULL,'user','editor',500,500,1)""",
                (
                    finding_id,
                    fixture.target.space_id,
                    fixture.target.item_id,
                    target_revision_id,
                    rule_version,
                ),
            )
            connection.execute(
                """INSERT INTO change_proposal(
                     id,finding_id,finding_input_row_version,proposal_kind,
                     proposal_series_id,proposal_version,supersedes_proposal_id,
                     lineage_group_id,base_item_id,base_revision_id,source_item_id,
                     source_revision_id,target_item_id,target_revision_id,
                     source_artifact_id,extraction_snapshot_id,proposed_content_md,
                     proposed_content_blob_hash,proposed_content_size_bytes,
                     proposed_content_sha256,preview_sha256,state,
                     applied_operation_id,applied_dedup_id,last_application_error_json,
                     created_by,updated_by,created_at,updated_at,row_version)
                   VALUES (?,?,1,?,?,1,NULL,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'pending_review',
                     NULL,NULL,NULL,'editor','editor',500,500,1)""",
                (
                    proposal_id,
                    finding_id,
                    kind,
                    proposal_id,
                    lineage_group_id,
                    base_item_id,
                    base_revision_id,
                    source_item_id,
                    source_revision_id,
                    fixture.target.item_id,
                    target_revision_id,
                    source_artifact_id,
                    extraction_snapshot_id,
                    proposed_md,
                    proposed_blob,
                    proposed_size,
                    proposed_sha,
                    preview_sha,
                ),
            )
    finally:
        connection.close()
    return proposal_id, finding_id, preview_sha


def _service(fixture: _ProposalFixture, *, content_loader=None, failpoint=None):
    return TransactionalProposalService(
        fixture.target.factory,
        key_ring=LeaseHmacKeyRing(
            active_key_id="lease-v1", keys={"lease-v1": _KEY}
        ),
        scope_resolver=ScopeResolver(_flags()),
        content_loader=content_loader,
        failpoint=failpoint,
    )


def _preview(service, fixture: _ProposalFixture, proposal_id: str):
    return service.preview(
        ProposalPreviewCommand(
            principal=fixture.target.principal("editor"),
            space_id=fixture.target.space_id,
            proposal_id=proposal_id,
            request_id=_uuid(f"proposal-preview:{proposal_id}"),
        )
    )


def _approve(fixture: _ProposalFixture, finding_id: str) -> None:
    connection = fixture.target.factory.open()
    try:
        with transaction(connection):
            FindingRepository().review(
                connection,
                finding_id=finding_id,
                actor_user_id="editor",
                decision="approve",
                reason="Reviewed proposal approved.",
                expected_row_version=1,
                now_ms=600,
            )
    finally:
        connection.close()


def _pair_versions(fixture: _ProposalFixture, proposal_id: str):
    connection = fixture.target.factory.open(read_only=True)
    try:
        row = connection.execute(
            """SELECT p.state,p.row_version,f.status,f.row_version
                 FROM change_proposal p JOIN curation_finding f ON f.id=p.finding_id
                WHERE p.id=?""",
            (proposal_id,),
        ).fetchone()
        assert row is not None
        return tuple(row)
    finally:
        connection.close()


def _apply_command(
    fixture: _ProposalFixture,
    proposal_id: str,
    preview,
    acquire_command,
    lease,
    *,
    label: str = "proposal-apply",
) -> ProposalApplyCommand:
    return ProposalApplyCommand(
        principal=fixture.target.principal("editor"),
        space_id=fixture.target.space_id,
        proposal_id=proposal_id,
        expected_proposal_row_version=2,
        expected_finding_row_version=2,
        expected_target_item_row_version=preview.current_target_row_version,
        expected_target_revision_id=preview.current_target_revision_id,
        expected_source_revision_id=preview.current_source_revision_id,
        preview_sha256=preview.preview_sha256,
        editor_session_id=acquire_command.editor_session_id,
        lease_id=lease.lease_id,
        fencing_generation=lease.fencing_generation,
        raw_token=acquire_command.raw_token,
        operation_id=_uuid(f"{label}-operation"),
        request_id=_uuid(f"{label}-request"),
        request_meta={"method": "POST", "authorization": "Bearer secret"},
    )


def _state(fixture: _ProposalFixture) -> dict[str, object]:
    connection = fixture.target.factory.open(read_only=True)
    try:
        tables = (
            "knowledge_revision",
            "knowledge_revision_body",
            "item_curation_transition",
            "durable_job",
            "operation_dedup",
            "audit_event",
            "outbox_event",
            "curation_finding",
            "change_proposal",
        )
        return {
            "counts": {
                table: int(
                    connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                )
                for table in tables
            },
            "item": tuple(
                connection.execute(
                    """SELECT current_revision_id,curation_status,
                              last_curation_transition_id,row_version
                         FROM knowledge_item WHERE id=?""",
                    (fixture.target.item_id,),
                ).fetchone()
            ),
            "lease": tuple(
                connection.execute(
                    """SELECT holder_user_id,editor_session_id,token_hmac,
                              hmac_key_id,fencing_generation,
                              base_item_row_version,base_revision_id
                         FROM item_lease WHERE item_id=?""",
                    (fixture.target.item_id,),
                ).fetchone()
            ),
            "pairs": tuple(
                tuple(row)
                for row in connection.execute(
                    """SELECT p.id,p.state,p.row_version,f.id,f.status,f.row_version
                         FROM change_proposal p JOIN curation_finding f ON f.id=p.finding_id
                        ORDER BY p.id"""
                ).fetchall()
            ),
        }
    finally:
        connection.close()


@pytest.mark.scenario("PROP-001")
def test_stable_source_preview_binds_snapshot_base_target_and_proposed_body(
    isolated_knowledge_layout,
) -> None:
    fixture = _both_fixture(isolated_knowledge_layout)
    proposal_id, finding_id, preview_sha = _insert_proposal(
        fixture, kind="stable_source_update", label="stable"
    )
    result = _preview(_service(fixture), fixture, proposal_id)
    assert result.finding_id == finding_id
    assert result.preview_sha256 == preview_sha
    assert result.proposal_kind == "stable_source_update"
    assert [pane.label for pane in result.panes] == [
        "fork_base",
        "source_current",
        "target_current",
    ]
    assert result.panes[0].revision_id == fixture.target.revision_id
    assert result.panes[1].revision_id == fixture.extraction_snapshot_id
    assert result.panes[2].revision_id == fixture.target.revision_id
    assert result.proposed_result.markdown == result.panes[1].markdown
    assert result.stale is False


@pytest.mark.scenario("PROP-001")
@pytest.mark.scenario("PROP-004")
def test_approved_stable_source_proposal_applies_the_frozen_snapshot_once(
    isolated_knowledge_layout,
) -> None:
    fixture = _both_fixture(isolated_knowledge_layout)
    proposal_id, finding_id, _ = _insert_proposal(
        fixture, kind="stable_source_update", label="stable-apply"
    )
    service = _service(fixture)
    preview = _preview(service, fixture, proposal_id)
    _approve(fixture, finding_id)
    _lease_service, acquire_command, lease = _acquire(
        fixture.target, label="stable-proposal-target"
    )
    result = service.apply(
        _apply_command(
            fixture,
            proposal_id,
            preview,
            acquire_command,
            lease,
            label="stable-proposal-apply",
        )
    )
    connection = fixture.target.factory.open(read_only=True)
    try:
        row = connection.execute(
            """SELECT r.change_kind,b.content_md,p.source_artifact_id,
                      p.extraction_snapshot_id,p.state,f.status
                 FROM knowledge_revision r JOIN knowledge_revision_body b
                   ON b.revision_id=r.id AND b.item_id=r.item_id
                 JOIN change_proposal p ON p.id=?
                 JOIN curation_finding f ON f.id=p.finding_id
                WHERE r.id=?""",
            (proposal_id, result.result_revision_id),
        ).fetchone()
        assert row is not None
        assert tuple(row) == (
            "approved_update",
            preview.proposed_result.markdown,
            fixture.source_artifact_id,
            fixture.extraction_snapshot_id,
            "applied",
            "applied",
        )
    finally:
        connection.close()
    _assert_postconditions(fixture.target.factory)


@pytest.mark.scenario("PROP-002")
@pytest.mark.scenario("PROP-004")
@pytest.mark.scenario("PROP-005")
def test_personal_proposal_preview_apply_and_response_loss_replay_are_exact(
    isolated_knowledge_layout,
) -> None:
    fixture = _both_fixture(isolated_knowledge_layout)
    proposal_id, finding_id, _ = _insert_proposal(
        fixture, kind="personal_to_part", label="personal-apply"
    )
    service = _service(fixture)
    preview = _preview(service, fixture, proposal_id)
    assert preview.panes[0].item_id == fixture.source.item_id
    assert preview.panes[1].item_id == fixture.source.item_id
    assert preview.panes[2].item_id == fixture.target.item_id
    assert preview.stale is False
    _approve(fixture, finding_id)
    assert _pair_versions(fixture, proposal_id) == ("approved", 2, "approved", 2)
    _lease_service, acquire_command, lease = _acquire(
        fixture.target, label="proposal-target"
    )
    command = _apply_command(
        fixture, proposal_id, preview, acquire_command, lease
    )
    result = service.apply(command)
    replay = _service(fixture).apply(
        replace(command, request_id=_uuid("proposal-apply-replay-request"))
    )
    assert replay == replace(
        result, request_id=_uuid("proposal-apply-replay-request")
    )
    assert _pair_versions(fixture, proposal_id) == ("applied", 3, "applied", 3)
    connection = fixture.target.factory.open(read_only=True)
    try:
        row = connection.execute(
            """SELECT i.current_revision_id,i.curation_status,i.row_version,
                      r.change_kind,r.parent_revision_id,r.base_revision_id,b.content_md,
                      l.holder_user_id,j.state
                 FROM knowledge_item i JOIN knowledge_revision r ON r.id=i.current_revision_id
                 JOIN knowledge_revision_body b ON b.revision_id=r.id
                 JOIN item_lease l ON l.item_id=i.id
                 JOIN durable_job j ON j.id=?
                WHERE i.id=?""",
            (result.rescan_job_id, fixture.target.item_id),
        ).fetchone()
        assert row is not None
        assert tuple(row[:6]) == (
            result.result_revision_id,
            "provisional",
            result.item_row_version,
            "approved_update",
            fixture.target.revision_id,
            fixture.target.revision_id,
        )
        assert row[6] == preview.proposed_result.markdown
        assert row[7] is None
        assert row[8] == "queued"
        assert connection.execute(
            "SELECT count(*) FROM knowledge_revision WHERE item_id=?",
            (fixture.target.item_id,),
        ).fetchone()[0] == 2
        persisted = "\n".join(
            str(value)
            for row in connection.execute(
                """SELECT request_meta_json,details_json FROM audit_event
                   UNION ALL SELECT payload_json,NULL FROM outbox_event
                   UNION ALL SELECT result_json,error_json FROM operation_dedup"""
            ).fetchall()
            for value in row
            if value is not None
        )
        assert command.raw_token not in persisted
        assert "Bearer secret" not in persisted
    finally:
        connection.close()
    with pytest.raises(LeaseIdempotencyError):
        _service(fixture).apply(
            replace(
                command,
                preview_sha256="f" * 64,
                request_id=_uuid("proposal-apply-rebind-request"),
            )
        )
    _assert_postconditions(fixture.target.factory)


def test_personal_source_change_requires_rebase_and_captures_new_source_revision(
    isolated_knowledge_layout,
) -> None:
    fixture = _both_fixture(isolated_knowledge_layout)
    proposal_id, _finding_id, _ = _insert_proposal(
        fixture, kind="personal_to_part", label="personal-source-rebase"
    )
    service = _service(fixture)
    original = _preview(service, fixture, proposal_id)
    source_edit_service, acquire_command, lease = _acquire(
        fixture.source, label="proposal-source-change"
    )
    source_edit = source_edit_service.save(
        _save_command(
            fixture.source,
            acquire_command,
            lease,
            label="proposal-source-change",
            expected_revision_id=fixture.source.revision_id,
            expected_row_version=1,
        )
    )
    stale = _preview(service, fixture, proposal_id)
    assert stale.stale is True
    assert stale.current_source_revision_id == source_edit.revision_id
    old_pair = _pair_versions(fixture, proposal_id)
    assert old_pair == ("pending_review", 1, "pending", 1)
    rebased = service.rebase(
        ProposalRebaseCommand(
            principal=fixture.target.principal("editor"),
            space_id=fixture.target.space_id,
            proposal_id=proposal_id,
            expected_proposal_row_version=1,
            expected_finding_row_version=1,
            expected_target_revision_id=original.current_target_revision_id,
            expected_source_revision_id=source_edit.revision_id,
            proposed_markdown=None,
            operation_id=_uuid("personal-source-rebase-operation"),
            request_id=_uuid("personal-source-rebase-request"),
            request_meta={},
        )
    )
    refreshed = _preview(service, fixture, rebased.proposal_id)
    assert refreshed.stale is False
    assert refreshed.panes[0].revision_id == original.panes[0].revision_id
    assert refreshed.panes[1].revision_id == source_edit.revision_id
    assert refreshed.panes[2].revision_id == original.panes[2].revision_id
    assert refreshed.proposed_result.content_sha256 == refreshed.panes[1].content_sha256
    assert refreshed.proposed_result.markdown == refreshed.panes[1].markdown
    assert _pair_versions(fixture, proposal_id)[0::2] == ("obsolete", "obsolete")
    _assert_postconditions(fixture.target.factory)


def test_only_the_recorded_reviewer_can_apply_an_approved_proposal(
    isolated_knowledge_layout,
) -> None:
    fixture = _both_fixture(isolated_knowledge_layout)
    proposal_id, finding_id, _ = _insert_proposal(
        fixture, kind="personal_to_part", label="reviewer-bound-apply"
    )
    service = _service(fixture)
    preview = _preview(service, fixture, proposal_id)
    _approve(fixture, finding_id)
    raw_token = _token("proposal-member-lease")
    acquire_command = AcquireLeaseCommand(
        principal=fixture.target.principal("member"),
        space_id=fixture.target.space_id,
        item_id=fixture.target.item_id,
        editor_session_id=_uuid("proposal-member-session"),
        operation_id=_uuid("proposal-member-acquire-operation"),
        request_id=_uuid("proposal-member-acquire-request"),
        raw_token=raw_token,
        request_meta={},
    )
    lease = service.acquire(acquire_command)
    command = _apply_command(
        fixture,
        proposal_id,
        preview,
        acquire_command,
        lease,
        label="reviewer-bound-apply",
    )
    command = replace(command, principal=fixture.target.principal("member"))
    baseline = _state(fixture)
    with pytest.raises(ProposalReviewStateError):
        service.apply(command)
    assert _state(fixture) == baseline
    assert _pair_versions(fixture, proposal_id) == (
        "approved",
        2,
        "approved",
        2,
    )
    _assert_postconditions(fixture.target.factory)


@pytest.mark.scenario("PROP-005")
def test_committed_blob_proposal_replay_does_not_reopen_content_storage(
    isolated_knowledge_layout,
) -> None:
    fixture = _both_fixture(isolated_knowledge_layout)
    blob_markdown = "# Blob-backed reviewed update\nApply exactly once.\n"
    blob_bytes = blob_markdown.encode("utf-8")
    blob_sha = hashlib.sha256(blob_bytes).hexdigest()
    proposal_id, finding_id, _ = _insert_proposal(
        fixture,
        kind="personal_to_part",
        label="blob-response-replay",
        proposed_blob_markdown=blob_markdown,
    )
    load_count = 0

    def load_blob(content_sha256: str, size_bytes: int) -> bytes:
        nonlocal load_count
        load_count += 1
        assert content_sha256 == blob_sha
        assert size_bytes == len(blob_bytes)
        return blob_bytes

    service = _service(fixture, content_loader=load_blob)
    preview = _preview(service, fixture, proposal_id)
    _approve(fixture, finding_id)
    _lease_service, acquire_command, lease = _acquire(
        fixture.target, label="blob-response-replay"
    )
    command = _apply_command(
        fixture,
        proposal_id,
        preview,
        acquire_command,
        lease,
        label="blob-response-replay",
    )
    applied = service.apply(command)
    assert load_count >= 2

    def unavailable_loader(_content_sha256: str, _size_bytes: int) -> bytes:
        raise AssertionError("committed replay must not reopen content storage")

    replay = _service(fixture, content_loader=unavailable_loader).apply(
        replace(command, request_id=_uuid("blob-response-replay-second-request"))
    )
    assert replay == replace(
        applied, request_id=_uuid("blob-response-replay-second-request")
    )
    _assert_postconditions(fixture.target.factory)


def test_committed_blob_proposal_rebase_replay_does_not_reopen_content_storage(
    isolated_knowledge_layout,
) -> None:
    fixture = _both_fixture(isolated_knowledge_layout)
    blob_markdown = "# Blob-backed proposal before rebase\n"
    blob_bytes = blob_markdown.encode("utf-8")
    blob_sha = hashlib.sha256(blob_bytes).hexdigest()
    proposal_id, _finding_id, _ = _insert_proposal(
        fixture,
        kind="personal_to_part",
        label="blob-rebase-replay",
        proposed_blob_markdown=blob_markdown,
    )

    def load_blob(content_sha256: str, size_bytes: int) -> bytes:
        assert content_sha256 == blob_sha
        assert size_bytes == len(blob_bytes)
        return blob_bytes

    service = _service(fixture, content_loader=load_blob)
    preview = _preview(service, fixture, proposal_id)
    command = ProposalRebaseCommand(
        principal=fixture.target.principal("editor"),
        space_id=fixture.target.space_id,
        proposal_id=proposal_id,
        expected_proposal_row_version=preview.proposal_row_version,
        expected_finding_row_version=preview.finding_row_version,
        expected_target_revision_id=preview.current_target_revision_id,
        expected_source_revision_id=preview.current_source_revision_id,
        proposed_markdown="# Explicitly rebased result\n",
        operation_id=_uuid("blob-rebase-replay-operation"),
        request_id=_uuid("blob-rebase-replay-request"),
        request_meta={},
    )
    rebased = service.rebase(command)

    def unavailable_loader(_content_sha256: str, _size_bytes: int) -> bytes:
        raise AssertionError("committed rebase replay must not reopen content storage")

    replay = _service(fixture, content_loader=unavailable_loader).rebase(
        replace(command, request_id=_uuid("blob-rebase-replay-second-request"))
    )
    assert replay == replace(
        rebased, request_id=_uuid("blob-rebase-replay-second-request")
    )
    _assert_postconditions(fixture.target.factory)


@pytest.mark.scenario("PROP-003")
def test_stale_target_cannot_apply_and_explicit_rebase_versions_the_pair(
    isolated_knowledge_layout,
) -> None:
    fixture = _both_fixture(isolated_knowledge_layout)
    proposal_id, finding_id, _ = _insert_proposal(
        fixture, kind="personal_to_part", label="personal-rebase"
    )
    service = _service(fixture)
    original = _preview(service, fixture, proposal_id)
    _approve(fixture, finding_id)
    edit_service, acquire_command, lease = _acquire(
        fixture.target, label="proposal-stale-target"
    )
    edit = edit_service.save(
        _save_command(
            fixture.target,
            acquire_command,
            lease,
            label="proposal-stale-target",
            expected_revision_id=fixture.target.revision_id,
            expected_row_version=original.current_target_row_version,
        )
    )
    stale = _preview(service, fixture, proposal_id)
    assert stale.stale is True
    old_pair = _pair_versions(fixture, proposal_id)
    assert old_pair[0] == old_pair[2] == "obsolete"
    _new_lease_service, stale_acquire, stale_lease = _acquire(
        replace(fixture.target, revision_id=edit.revision_id),
        label="proposal-stale-apply",
    )
    with pytest.raises(ProposalPreviewStaleError):
        service.apply(
            ProposalApplyCommand(
                principal=fixture.target.principal("editor"),
                space_id=fixture.target.space_id,
                proposal_id=proposal_id,
                expected_proposal_row_version=int(old_pair[1]),
                expected_finding_row_version=int(old_pair[3]),
                expected_target_item_row_version=original.current_target_row_version,
                expected_target_revision_id=original.current_target_revision_id,
                expected_source_revision_id=original.current_source_revision_id,
                preview_sha256=original.preview_sha256,
                editor_session_id=stale_acquire.editor_session_id,
                lease_id=stale_lease.lease_id,
                fencing_generation=stale_lease.fencing_generation,
                raw_token=stale_acquire.raw_token,
                operation_id=_uuid("proposal-stale-apply-operation"),
                request_id=_uuid("proposal-stale-apply-request"),
                request_meta={},
            )
        )
    rebased = service.rebase(
        ProposalRebaseCommand(
            principal=fixture.target.principal("editor"),
            space_id=fixture.target.space_id,
            proposal_id=proposal_id,
            expected_proposal_row_version=int(old_pair[1]),
            expected_finding_row_version=int(old_pair[3]),
            expected_target_revision_id=edit.revision_id,
            expected_source_revision_id=stale.current_source_revision_id,
            proposed_markdown="# Rebasing reviewed result\nCurrent target considered.\n",
            operation_id=_uuid("proposal-rebase-operation"),
            request_id=_uuid("proposal-rebase-request"),
            request_meta={},
        )
    )
    replay = service.rebase(
        ProposalRebaseCommand(
            principal=fixture.target.principal("editor"),
            space_id=fixture.target.space_id,
            proposal_id=proposal_id,
            expected_proposal_row_version=int(old_pair[1]),
            expected_finding_row_version=int(old_pair[3]),
            expected_target_revision_id=edit.revision_id,
            expected_source_revision_id=stale.current_source_revision_id,
            proposed_markdown="# Rebasing reviewed result\nCurrent target considered.\n",
            operation_id=_uuid("proposal-rebase-operation"),
            request_id=_uuid("proposal-rebase-replay-request"),
            request_meta={},
        )
    )
    assert replay == replace(
        rebased, request_id=_uuid("proposal-rebase-replay-request")
    )
    assert rebased.proposal_version == 2
    assert rebased.target_revision_id == edit.revision_id
    assert _pair_versions(fixture, proposal_id)[0] == "obsolete"
    assert _pair_versions(fixture, rebased.proposal_id) == (
        "pending_review",
        1,
        "pending",
        1,
    )
    assert _preview(service, fixture, rebased.proposal_id).stale is False
    _assert_postconditions(fixture.target.factory)


def test_apply_failed_pair_can_be_reapproved_only_with_both_states_cleared(
    isolated_knowledge_layout,
) -> None:
    fixture = _both_fixture(isolated_knowledge_layout)
    proposal_id, finding_id, _ = _insert_proposal(
        fixture, kind="personal_to_part", label="apply-failed-review"
    )
    _approve(fixture, finding_id)
    dedup_id = _uuid("apply-failed-dedup")
    operation_id = _uuid("apply-failed-operation")
    connection = fixture.target.factory.open()
    try:
        with transaction(connection):
            connection.execute(
                """INSERT INTO operation_dedup(
                     id,actor_user_id,operation_type,operation_id,request_sha256,
                     status,http_status,result_json,error_json,created_at,
                     completed_at,last_seen_at)
                   VALUES (?,'editor','proposal_apply',?,?,'in_progress',
                     NULL,NULL,NULL,700,NULL,700)""",
                (dedup_id, operation_id, "a" * 64),
            )
            connection.execute(
                """UPDATE curation_finding
                      SET status='apply_failed',applied_operation_id=?,
                          applied_dedup_id=?,applied_by='editor',applied_at=701,
                          last_application_error_json='{"code":"POLICY_REJECTED"}',
                          updated_at=701,row_version=row_version+1
                    WHERE id=? AND status='approved'""",
                (operation_id, dedup_id, finding_id),
            )
            connection.execute(
                """UPDATE change_proposal
                      SET state='apply_failed',applied_operation_id=?,
                          applied_dedup_id=?,
                          last_application_error_json='{"code":"POLICY_REJECTED"}',
                          updated_by='editor',updated_at=701,
                          row_version=row_version+1
                    WHERE id=? AND state='approved'""",
                (operation_id, dedup_id, proposal_id),
            )
        assert _pair_versions(fixture, proposal_id) == (
            "apply_failed",
            3,
            "apply_failed",
            3,
        )
        with transaction(connection):
            result = FindingRepository().review(
                connection,
                finding_id=finding_id,
                actor_user_id="editor",
                decision="approve",
                reason="Corrected and re-approved.",
                expected_row_version=3,
                now_ms=702,
            )
            assert result.outcome == "approved"
    finally:
        connection.close()
    assert _pair_versions(fixture, proposal_id) == (
        "approved",
        4,
        "approved",
        4,
    )
    connection = fixture.target.factory.open(read_only=True)
    try:
        row = connection.execute(
            """SELECT p.applied_operation_id,p.applied_dedup_id,
                      p.last_application_error_json,f.applied_operation_id,
                      f.applied_dedup_id,f.applied_by,f.applied_at,
                      f.last_application_error_json
                 FROM change_proposal p JOIN curation_finding f ON f.id=p.finding_id
                WHERE p.id=?""",
            (proposal_id,),
        ).fetchone()
        assert row is not None and all(value is None for value in row)
    finally:
        connection.close()


@pytest.mark.scenario("PROP-001")
@pytest.mark.scenario("PROP-003")
def test_stable_source_rebase_preserves_source_snapshot_and_moves_only_target_base(
    isolated_knowledge_layout,
) -> None:
    fixture = _both_fixture(isolated_knowledge_layout)
    proposal_id, _finding_id, _ = _insert_proposal(
        fixture, kind="stable_source_update", label="stable-rebase"
    )
    service = _service(fixture)
    before = _preview(service, fixture, proposal_id)
    edit_service, acquire_command, lease = _acquire(
        fixture.target, label="stable-rebase-target"
    )
    edit = edit_service.save(
        _save_command(
            fixture.target,
            acquire_command,
            lease,
            label="stable-rebase-target",
            expected_revision_id=fixture.target.revision_id,
            expected_row_version=before.current_target_row_version,
        )
    )
    old = _pair_versions(fixture, proposal_id)
    rebased = service.rebase(
        ProposalRebaseCommand(
            principal=fixture.target.principal("editor"),
            space_id=fixture.target.space_id,
            proposal_id=proposal_id,
            expected_proposal_row_version=int(old[1]),
            expected_finding_row_version=int(old[3]),
            expected_target_revision_id=edit.revision_id,
            expected_source_revision_id=None,
            proposed_markdown=None,
            operation_id=_uuid("stable-rebase-operation"),
            request_id=_uuid("stable-rebase-request"),
            request_meta={},
        )
    )
    after = _preview(service, fixture, rebased.proposal_id)
    assert after.panes[1].revision_id == before.panes[1].revision_id
    assert after.panes[1].content_sha256 == before.panes[1].content_sha256
    assert after.panes[1].markdown == before.panes[1].markdown
    assert after.panes[0].revision_id == edit.revision_id
    assert after.panes[2].revision_id == edit.revision_id
    connection = fixture.target.factory.open(read_only=True)
    try:
        row = connection.execute(
            """SELECT source_artifact_id,extraction_snapshot_id,
                      supersedes_proposal_id,proposal_version
                 FROM change_proposal WHERE id=?""",
            (rebased.proposal_id,),
        ).fetchone()
        assert row is not None
        assert tuple(row) == (
            fixture.source_artifact_id,
            fixture.extraction_snapshot_id,
            proposal_id,
            2,
        )
    finally:
        connection.close()


@pytest.mark.parametrize(
    "failpoint",
    (
        "proposal.after_apply_revision",
        "proposal_apply.after_rescan_enqueue",
        "proposal_apply.after_revision",
        "proposal_apply.after_current_pointer",
        "proposal_apply.after_pair_applied",
        "proposal_apply.after_domain_effect",
        "proposal_apply.after_lease_release",
        "proposal_apply.after_audit",
        "proposal_apply.after_outbox",
        "proposal_apply.before_commit",
    ),
)
def test_every_proposal_apply_failpoint_rolls_back_pair_revision_job_lease_and_events(
    isolated_knowledge_layout,
    failpoint: str,
) -> None:
    fixture = _both_fixture(isolated_knowledge_layout)
    proposal_id, finding_id, _ = _insert_proposal(
        fixture, kind="personal_to_part", label=f"apply-failpoint:{failpoint}"
    )
    service = _service(fixture)
    preview = _preview(service, fixture, proposal_id)
    _approve(fixture, finding_id)
    _lease_service, acquire_command, lease = _acquire(
        fixture.target, label=f"apply-failpoint:{failpoint}"
    )
    command = _apply_command(
        fixture,
        proposal_id,
        preview,
        acquire_command,
        lease,
        label=f"apply-failpoint:{failpoint}",
    )
    baseline = _state(fixture)

    def stop(name: str) -> None:
        if name == failpoint:
            raise RuntimeError(name)

    with pytest.raises(RuntimeError, match=failpoint):
        _service(fixture, failpoint=stop).apply(command)
    assert _state(fixture) == baseline
    _assert_postconditions(fixture.target.factory)


@pytest.mark.parametrize(
    "failpoint",
    (
        "proposal.after_rebase_insert",
        "proposal_rebase.after_new_pair",
        "proposal_rebase.after_old_pair_obsolete",
        "proposal_rebase.after_audit",
        "proposal_rebase.after_outbox",
        "proposal_rebase.before_commit",
    ),
)
def test_every_proposal_rebase_failpoint_rolls_back_new_pair_old_pair_audit_and_outbox(
    isolated_knowledge_layout,
    failpoint: str,
) -> None:
    fixture = _both_fixture(isolated_knowledge_layout)
    proposal_id, _finding_id, _ = _insert_proposal(
        fixture, kind="personal_to_part", label=f"rebase-failpoint:{failpoint}"
    )
    service = _service(fixture)
    preview = _preview(service, fixture, proposal_id)
    baseline = _state(fixture)

    def stop(name: str) -> None:
        if name == failpoint:
            raise RuntimeError(name)

    with pytest.raises(RuntimeError, match=failpoint):
        _service(fixture, failpoint=stop).rebase(
            ProposalRebaseCommand(
                principal=fixture.target.principal("editor"),
                space_id=fixture.target.space_id,
                proposal_id=proposal_id,
                expected_proposal_row_version=preview.proposal_row_version,
                expected_finding_row_version=preview.finding_row_version,
                expected_target_revision_id=preview.current_target_revision_id,
                expected_source_revision_id=preview.current_source_revision_id,
                proposed_markdown="# Rebased failpoint body\n",
                operation_id=_uuid(f"rebase-failpoint-operation:{failpoint}"),
                request_id=_uuid(f"rebase-failpoint-request:{failpoint}"),
                request_meta={},
            )
        )
    assert _state(fixture) == baseline
    _assert_postconditions(fixture.target.factory)
