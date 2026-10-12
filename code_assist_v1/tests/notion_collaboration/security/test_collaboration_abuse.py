from __future__ import annotations

from pathlib import Path

import pytest

from code_assist_v1.notion_collaboration.canonical_authority import NotFoundOrDenied
from code_assist_v1.notion_collaboration.file_authority import (
    CapacityUnavailable,
    CollaborativeFileAuthority,
)
from code_assist_v1.notion_collaboration.protocol_contract import (
    EnvelopeRejected,
    MAX_PAYLOAD_BYTES,
    StaleEpoch,
    TicketRejected,
)
from code_assist_v1.notion_collaboration.runtime_gateway import GatewayRejected
from code_assist_v1.tests.notion_collaboration.q0_support import (
    ORIGIN,
    PAGE_ID,
    build_q0_environment,
    table_count,
    upload_one,
)


@pytest.mark.integration
@pytest.mark.scenario("AUTH-004")
def test_q0_csrf_origin_ticket_replay_subject_binding_and_non_oracle_rejection(
    tmp_path: Path,
) -> None:
    environment = build_q0_environment(tmp_path / "ticket-abuse", registered_users=3)
    subject = environment.subjects[1]
    with pytest.raises(TicketRejected, match="collaboration ticket rejected"):
        environment.transport.issue_ticket(
            csrf_valid=False,
            subject_id=subject,
            workspace_id=environment.workspace_id,
            document_id=PAGE_ID,
            client_id="csrf-denied",
            session_binding_sha256="sha256:" + "1" * 64,
            origin=ORIGIN,
            capabilities=("read",),
            now_ms=1_000,
        )
    assert table_count(environment.catalog, "workspace_collaboration_tickets") == 0

    ticket, binding = environment.issue_ticket(
        subject,
        client_id="origin-bound",
        binding_secret="origin-bound-secret",
        now_ms=2_000,
    )
    with pytest.raises(GatewayRejected) as wrong_origin:
        environment.gateway.connect(
            ticket.token,
            origin="http://127.0.0.1:49401",
            subject_id=subject,
            workspace_id=environment.workspace_id,
            page_id=PAGE_ID,
            client_id="origin-bound",
            session_binding_sha256=binding,
            now_ms=2_001,
        )
    with pytest.raises(GatewayRejected) as burned_replay:
        environment.gateway.connect(
            ticket.token,
            origin=ORIGIN,
            subject_id=subject,
            workspace_id=environment.workspace_id,
            page_id=PAGE_ID,
            client_id="origin-bound",
            session_binding_sha256=binding,
            now_ms=2_002,
        )
    with pytest.raises(GatewayRejected) as unknown_token:
        environment.gateway.connect(
            "A" * 43,
            origin=ORIGIN,
            subject_id=subject,
            workspace_id=environment.workspace_id,
            page_id=PAGE_ID,
            client_id="origin-bound",
            session_binding_sha256=binding,
            now_ms=2_003,
        )
    assert str(wrong_origin.value) == str(burned_replay.value) == str(unknown_token.value)
    stored = next(
        item
        for item in environment.transport.ticket_rows()
        if item["jti"] == ticket.claims.jti
    )
    assert stored["consumed_at_ms"] == 2_001
    assert stored["burn_reason"] == "rejected"

    subject_ticket, subject_binding = environment.issue_ticket(
        subject,
        client_id="subject-bound",
        binding_secret="subject-bound-secret",
        now_ms=3_000,
    )
    with pytest.raises(GatewayRejected):
        environment.gateway.connect(
            subject_ticket.token,
            origin=ORIGIN,
            subject_id=environment.subjects[2],
            workspace_id=environment.workspace_id,
            page_id=PAGE_ID,
            client_id="subject-bound",
            session_binding_sha256=subject_binding,
            now_ms=3_001,
        )
    environment.gateway.stop()


@pytest.mark.integration
@pytest.mark.fault
@pytest.mark.scenario("AUTH-004")
def test_q0_oversized_update_and_revocation_fence_leave_no_protected_payload(
    tmp_path: Path,
) -> None:
    environment = build_q0_environment(tmp_path / "epoch-abuse", registered_users=3)
    subject = environment.subjects[1]
    session = environment.connect(
        subject,
        client_id="abuse-client",
        binding_secret="abuse-binding",
        now_ms=1_000,
    )
    before = table_count(environment.catalog, "workspace_collaboration_crdt_updates")
    with pytest.raises(EnvelopeRejected, match="maximum"):
        environment.gateway.submit_payload(
            session.connection_id,
            operation_id="op-q0-oversized-update-0001",
            client_sequence=1,
            acknowledgement_sequence=0,
            payload=b"x" * (MAX_PAYLOAD_BYTES + 1),
        )
    assert table_count(environment.catalog, "workspace_collaboration_crdt_updates") == before
    assert environment.gateway.session_status(session.connection_id)["active"] is True

    assert environment.gateway.queue_derived_frame(
        session.connection_id,
        kind="search",
        payload=b'{"private":"must-be-purged"}',
    ) == "enqueued"
    page = environment.iam.read_document("owner", PAGE_ID)
    environment.iam.revoke_workspace_member(
        "owner",
        environment.workspace_id,
        subject,
        expected_workspace_epoch=int(page["workspace_epoch"]),
        operation_id="op-q0-security-revoke-0001",
    )
    with pytest.raises(StaleEpoch):
        environment.gateway.drain(session.connection_id, max_frames=8)
    assert environment.gateway.session_status(session.connection_id) == {
        "connection_id": session.connection_id,
        "active": False,
        "close_reason": "stale_epoch",
        "queued_frames": 0,
        "queued_payload_bytes": 0,
        "last_queued_document_sequence": None,
    }
    environment.gateway.stop()


@pytest.mark.integration
@pytest.mark.concurrency
@pytest.mark.scenario("AUTH-004")
def test_q0_upload_capacity_and_authorized_read_are_fail_closed(tmp_path: Path) -> None:
    environment = build_q0_environment(
        tmp_path / "upload-abuse",
        registered_users=2,
        through_file_authority=True,
    )
    assert environment.files is not None
    actor = environment.subjects[1]
    result = upload_one(
        environment.files,
        actor=actor,
        payload=b"authorized-upload",
        operation_suffix="authorized-0001",
    )
    with pytest.raises(NotFoundOrDenied, match="not found or permission denied"):
        environment.files.get_file("outsider", str(result["file_id"]))
    assert environment.files.get_file(actor, str(result["file_id"]))["path"].read_bytes() == b"authorized-upload"

    bounded = CollaborativeFileAuthority.open(
        environment.catalog,
        environment.root,
        environment.identities,
        max_inflight_bytes=1_024,
    )
    page = bounded.read_document(actor, PAGE_ID)
    sessions_before = table_count(
        environment.catalog, "workspace_collaboration_upload_sessions"
    )
    with pytest.raises(CapacityUnavailable, match="inflight capacity"):
        bounded.begin_upload(
            actor,
            PAGE_ID,
            original_name="too-large.bin",
            mime_type="application/octet-stream",
            total_size=2_048,
            expected_sha256="sha256:" + "0" * 64,
            expected_document_sequence=int(page["document_sequence"]),
            expected_workspace_epoch=int(page["workspace_epoch"]),
            expected_document_epoch=int(page["document_epoch"]),
            operation_id="op-q0-upload-too-large-0001",
            chunk_size=65_536,
        )
    assert table_count(
        environment.catalog, "workspace_collaboration_upload_sessions"
    ) == sessions_before
    environment.gateway.stop()
