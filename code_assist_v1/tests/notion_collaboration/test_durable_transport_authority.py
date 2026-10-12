from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest

from code_assist_v1 import notion_workspace_collaboration_migration as authority_migration
from code_assist_v1 import notion_workspace_realtime_transport_migration as transport_migration
from code_assist_v1 import notion_workspace_store as workspace_store
from code_assist_v1.notion_collaboration.canonical_authority import (
    CanonicalAuthority,
    InjectedFailure,
    NotFoundOrDenied,
    OperationConflict,
)
from code_assist_v1.notion_collaboration.durable_transport import (
    DurableTransportAuthority,
    ForwardCompatibleCanonicalAuthority,
    InjectedTransportFailure,
    YJS_UPDATE_ENCODING,
)
from code_assist_v1.notion_collaboration.protocol_contract import (
    ProtocolEnvelope,
    StaleEpoch,
    TicketRejected,
    session_binding_sha256,
)


ORIGIN = "http://127.0.0.1:10112"
SESSION = session_binding_sha256("t0b-session")


def _backup(source: Path, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_con = sqlite3.connect(source)
    target_con = sqlite3.connect(destination)
    try:
        source_con.backup(target_con)
        target_con.commit()
    finally:
        target_con.close()
        source_con.close()
    return destination


def _environment(tmp_path: Path) -> tuple[DurableTransportAuthority, CanonicalAuthority, Path, Path, str]:
    root = tmp_path / "t0b-authority"
    catalog = Path(workspace_store.catalog_path_for_user_data_root(root))
    store = workspace_store.WorkspaceStore(catalog)
    store.bootstrap()
    created = store.create_workspace(
        "owner", name="T0B", operation_id="op-t0b-workspace-create-0001"
    )
    workspace_id = str(created["workspace"]["id"])
    store.set_member(
        "owner",
        workspace_id,
        "member",
        role="member",
        base_version=int(created["workspace"]["version"]),
        operation_id="op-t0b-member-set-0001",
    )
    backup_2401 = _backup(catalog, root / "backups" / "before-2401.db")
    plan_2401 = authority_migration.build_plan(catalog, root)
    authority_migration.apply_plan(
        catalog, root, exact_plan_checksum=plan_2401["plan_checksum"],
        backup_confirmed=True, catalog_quiesced_confirmed=True, backup_path=backup_2401,
    )
    authority = CanonicalAuthority.open(catalog, root)
    authority.create_document(
        "owner",
        workspace_id,
        "page-t0b",
        title="Realtime",
        blocks=[{"id": "block-t0b", "type": "text", "content": "base"}],
        expected_workspace_epoch=2,
        operation_id="op-t0b-document-create-0001",
    )
    backup_2402 = _backup(catalog, root / "backups" / "before-2402.db")
    plan_2402 = transport_migration.build_plan(catalog, root)
    transport_migration.apply_plan(
        catalog, root, exact_plan_checksum=plan_2402["plan_checksum"],
        backup_confirmed=True, catalog_quiesced_confirmed=True, backup_path=backup_2402,
    )
    return (
        DurableTransportAuthority.open(catalog, root),
        ForwardCompatibleCanonicalAuthority.open(catalog, root),
        root,
        catalog,
        workspace_id,
    )


def _grant(
    transport: DurableTransportAuthority,
    workspace_id: str,
    *,
    subject: str = "member",
    client: str = "client-a",
    now_ms: int = 1_000,
):
    issued = transport.issue_ticket(
        csrf_valid=True,
        subject_id=subject,
        workspace_id=workspace_id,
        document_id="page-t0b",
        client_id=client,
        session_binding_sha256=SESSION,
        origin=ORIGIN,
        capabilities=("read", "write"),
        now_ms=now_ms,
    )
    grant = transport.consume_ticket(
        issued.token,
        origin=ORIGIN,
        subject_id=subject,
        workspace_id=workspace_id,
        document_id="page-t0b",
        client_id=client,
        session_binding_sha256=SESSION,
        now_ms=now_ms + 1,
    )
    return issued, grant


def _envelope(grant, *, operation: str, sequence: int, ack: int, payload: bytes) -> ProtocolEnvelope:
    return ProtocolEnvelope.create(
        grant,
        message_type="crdt.update",
        operation_id=operation,
        client_sequence=sequence,
        acknowledgement_sequence=ack,
        payload=payload,
        capabilities=("write",),
        payload_encoding=YJS_UPDATE_ENCODING,
    )


@pytest.mark.integration
def test_t0b_p1_ticket_is_digest_only_one_time_and_non_oracle(tmp_path: Path) -> None:
    transport, _canonical, _root, catalog, workspace_id = _environment(tmp_path)
    issued = transport.issue_ticket(
        csrf_valid=True,
        subject_id="member",
        workspace_id=workspace_id,
        document_id="page-t0b",
        client_id="client-a",
        session_binding_sha256=SESSION,
        origin=ORIGIN,
        capabilities=("read", "write"),
        now_ms=1_000,
    )
    assert issued.token.encode("ascii") not in catalog.read_bytes()
    with pytest.raises(TicketRejected, match="collaboration ticket rejected"):
        transport.consume_ticket(
            issued.token,
            origin="https://attacker.example",
            subject_id="member",
            workspace_id=workspace_id,
            document_id="page-t0b",
            client_id="client-a",
            session_binding_sha256=SESSION,
            now_ms=1_001,
        )
    with pytest.raises(TicketRejected, match="collaboration ticket rejected"):
        transport.consume_ticket(
            issued.token,
            origin=ORIGIN,
            subject_id="member",
            workspace_id=workspace_id,
            document_id="page-t0b",
            client_id="client-a",
            session_binding_sha256=SESSION,
            now_ms=1_002,
        )
    assert transport.ticket_rows() == [{
        "ticket_sha256": transport.ticket_rows()[0]["ticket_sha256"],
        "jti": issued.claims.jti,
        "consumed_at_ms": 1001,
        "burn_reason": "rejected",
    }]


@pytest.mark.integration
def test_t0b_p1_update_ack_replay_and_restart_are_exactly_once(tmp_path: Path) -> None:
    transport, _canonical, root, catalog, workspace_id = _environment(tmp_path)
    _issued, grant = _grant(transport, workspace_id)
    envelope = _envelope(
        grant,
        operation="op-t0b-update-0001",
        sequence=1,
        ack=0,
        payload=b"yjs-update-one",
    )
    before = transport.counts()
    first = transport.accept_update(envelope, grant)
    replay = DurableTransportAuthority.open(catalog, root).accept_update(envelope, grant)
    assert replay == first
    assert first.document_sequence == 2
    after = transport.counts()
    for table in (
        "workspace_collaboration_crdt_updates",
        "workspace_collaboration_client_ack_state",
        "workspace_collaboration_operations",
        "workspace_collaboration_events",
        "workspace_collaboration_audit_events",
        "workspace_collaboration_outbox",
    ):
        assert after[table] == before[table] + 1
    batch = DurableTransportAuthority.open(catalog, root).replay(
        grant, after_document_sequence=0, limit=10
    )
    assert batch.snapshot is None
    assert [event.document_sequence for event in batch.events] == [2]
    assert batch.events[0].payload == b"yjs-update-one"
    with pytest.raises(OperationConflict):
        transport.accept_update(
            _envelope(
                grant,
                operation="op-t0b-update-0001",
                sequence=1,
                ack=0,
                payload=b"different",
            ),
            grant,
        )


@pytest.mark.integration
@pytest.mark.parametrize(
    "fault", ["after_domain", "after_event", "after_audit", "after_outbox", "after_operation"]
)
def test_t0b_p1_update_faults_roll_back_payload_ack_event_audit_outbox_and_operation(
    tmp_path: Path, fault: str
) -> None:
    transport, _canonical, _root, _catalog, workspace_id = _environment(tmp_path)
    _issued, grant = _grant(transport, workspace_id)
    before = transport.counts()
    with pytest.raises(InjectedFailure, match=fault):
        transport.accept_update(
            _envelope(
                grant,
                operation=f"op-t0b-fault-{fault}-0001",
                sequence=1,
                ack=0,
                payload=fault.encode(),
            ),
            grant,
            fault=fault,
        )
    assert transport.counts() == before


@pytest.mark.integration
def test_t0b_p1_commit_before_ack_retry_returns_original_ack(tmp_path: Path) -> None:
    transport, _canonical, _root, _catalog, workspace_id = _environment(tmp_path)
    _issued, grant = _grant(transport, workspace_id)
    envelope = _envelope(
        grant,
        operation="op-t0b-response-loss-0001",
        sequence=1,
        ack=0,
        payload=b"committed-before-ack",
    )
    before = transport.counts()
    with pytest.raises(InjectedTransportFailure, match="after_commit_before_ack"):
        transport.accept_update(envelope, grant, fault="after_commit_before_ack")
    recovered = transport.accept_update(envelope, grant)
    assert recovered.operation_id == "op-t0b-response-loss-0001"
    after = transport.counts()
    for table in (
        "workspace_collaboration_crdt_updates",
        "workspace_collaboration_client_ack_state",
        "workspace_collaboration_operations",
        "workspace_collaboration_events",
        "workspace_collaboration_audit_events",
        "workspace_collaboration_outbox",
    ):
        assert after[table] == before[table] + 1


@pytest.mark.integration
def test_t0b_p1_revoke_fences_old_grant_before_write_and_replay(tmp_path: Path) -> None:
    transport, canonical, _root, _catalog, workspace_id = _environment(tmp_path)
    _issued, grant = _grant(transport, workspace_id)
    transport.accept_update(
        _envelope(
            grant,
            operation="op-t0b-before-revoke-0001",
            sequence=1,
            ack=0,
            payload=b"before-revoke",
        ),
        grant,
    )
    canonical.revoke_subject(
        "owner",
        workspace_id,
        "member",
        expected_workspace_epoch=2,
        operation_id="op-t0b-revoke-member-0001",
    )
    with pytest.raises((NotFoundOrDenied, StaleEpoch)):
        transport.replay(grant, after_document_sequence=0, limit=10)
    with pytest.raises((NotFoundOrDenied, StaleEpoch)):
        transport.accept_update(
            _envelope(
                grant,
                operation="op-t0b-after-revoke-0001",
                sequence=2,
                ack=2,
                payload=b"must-not-commit",
            ),
            grant,
        )
    assert transport.counts()["workspace_collaboration_crdt_updates"] == 1
