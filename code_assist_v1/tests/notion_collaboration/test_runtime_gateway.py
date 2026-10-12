from __future__ import annotations

from pathlib import Path
import sqlite3
import time

import pytest

from code_assist_v1 import notion_workspace_collaboration_migration as authority_migration
from code_assist_v1 import notion_workspace_realtime_transport_migration as transport_migration
from code_assist_v1 import notion_workspace_store as workspace_store
from code_assist_v1.notion_collaboration.canonical_authority import (
    CanonicalAuthority, _lock_generation_on_connection, bind_temporary_unlock,
)
from code_assist_v1.notion_collaboration.durable_transport import DurableTransportAuthority
from code_assist_v1.notion_collaboration.protocol_contract import session_binding_sha256
from code_assist_v1.notion_collaboration.runtime_gateway import (
    ConnectionCapacityExceeded,
    GatewayUnavailable,
    ReconnectThrottled,
    RuntimeGateway,
    SessionClosed,
)


ORIGIN = "http://127.0.0.1:49321"


def _backup(source: Path, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect(source)
    target = sqlite3.connect(destination)
    try:
        source.backup(target)
        target.commit()
    finally:
        target.close()
        source.close()
    return destination


def _environment(
    tmp_path: Path,
    *,
    max_queue_frames: int = 8,
    max_connections: int = 1_024,
    reconnect_attempts: int = 5,
):
    root = tmp_path / "runtime-gateway"
    catalog = Path(workspace_store.catalog_path_for_user_data_root(root))
    store = workspace_store.WorkspaceStore(catalog)
    store.bootstrap()
    created = store.create_workspace("owner", name="Runtime", operation_id="op-runtime-workspace-create-0001")
    workspace_id = str(created["workspace"]["id"])
    store.set_member(
        "owner",
        workspace_id,
        "member",
        role="member",
        base_version=int(created["workspace"]["version"]),
        operation_id="op-runtime-member-set-0001",
    )
    backup_2401 = _backup(catalog, root / "backups" / "before-2401.db")
    plan_2401 = authority_migration.build_plan(catalog, root)
    authority_migration.apply_plan(
        catalog,
        root,
        exact_plan_checksum=plan_2401["plan_checksum"],
        backup_confirmed=True,
        catalog_quiesced_confirmed=True,
        backup_path=backup_2401,
    )
    authority = CanonicalAuthority.open(catalog, root)
    authority.create_document(
        "owner",
        workspace_id,
        "page-runtime",
        title="Realtime",
        blocks=[{"id": "block-runtime", "type": "text", "content": "base"}],
        expected_workspace_epoch=2,
        operation_id="op-runtime-document-create-0001",
    )
    backup_2402 = _backup(catalog, root / "backups" / "before-2402.db")
    plan_2402 = transport_migration.build_plan(catalog, root)
    transport_migration.apply_plan(
        catalog,
        root,
        exact_plan_checksum=plan_2402["plan_checksum"],
        backup_confirmed=True,
        catalog_quiesced_confirmed=True,
        backup_path=backup_2402,
    )
    transport = DurableTransportAuthority.open(catalog, root)
    gateway = RuntimeGateway(
        transport=transport,
        aggregate_enabled=True,
        realtime_enabled=True,
        max_queue_frames=max_queue_frames,
        max_connections=max_connections,
        reconnect_attempts=reconnect_attempts,
    )
    health = gateway.start(compactor_handshake_matches=True)
    assert health["ready_for_admission"] is True
    return gateway, transport, root, catalog, workspace_id


def _connect(
    gateway: RuntimeGateway,
    transport: DurableTransportAuthority,
    workspace_id: str,
    *,
    client_id: str,
    binding_secret: str,
    now_ms: int,
):
    binding = session_binding_sha256(binding_secret)
    issued = transport.issue_ticket(
        csrf_valid=True,
        subject_id="member",
        workspace_id=workspace_id,
        document_id="page-runtime",
        client_id=client_id,
        session_binding_sha256=binding,
        origin=ORIGIN,
        capabilities=("read", "write", "presence"),
        now_ms=now_ms,
    )
    descriptor = gateway.connect(
        issued.token,
        origin=ORIGIN,
        subject_id="member",
        workspace_id=workspace_id,
        page_id="page-runtime",
        client_id=client_id,
        session_binding_sha256=binding,
        now_ms=now_ms + 1,
    )
    return descriptor


@pytest.mark.integration
def test_t0b_p2_temporary_write_gateway_rechecks_global_relock(tmp_path: Path) -> None:
    gateway, transport, _root, catalog, workspace_id = _environment(tmp_path)
    authority = transport.canonical_authority()
    page = authority.read_document("owner", "page-runtime")
    authority.save_document(
        "owner", "page-runtime", title=page["title"], blocks=page["blocks"],
        settings={"locked": True}, expected_revision=page["revision"],
        expected_workspace_epoch=page["workspace_epoch"],
        expected_document_epoch=page["document_epoch"], operation_id="runtime-temporary-lock",
    )
    locked = authority.read_document("owner", "page-runtime")
    with authority._reader() as con:
        generation = _lock_generation_on_connection(con, authority._document(con, "page-runtime"))
    now_ms = int(time.time() * 1000)
    binding = session_binding_sha256("runtime-temporary-session")
    with bind_temporary_unlock({
        "user_id": "owner", "workspace_id": workspace_id, "page_id": "page-runtime",
        "lock_generation": generation, "expires_at_ms": now_ms + 60_000,
    }):
        issued = transport.issue_ticket(
            csrf_valid=True, subject_id="owner", workspace_id=workspace_id,
            document_id="page-runtime", client_id="runtime-temporary-client",
            session_binding_sha256=binding, origin=ORIGIN, capabilities=("read", "presence"),
            now_ms=now_ms, include_temporary_write=True,
        )
    connection = gateway.connect(
        issued.token, origin=ORIGIN, subject_id="owner", workspace_id=workspace_id,
        page_id="page-runtime", client_id="runtime-temporary-client",
        session_binding_sha256=binding, now_ms=now_ms + 1,
    )
    assert "write" in connection.capabilities
    committed = gateway.submit_payload(
        connection.connection_id, operation_id="runtime-temporary-edit", client_sequence=1,
        acknowledgement_sequence=locked["document_sequence"], payload=b"temporary-gateway-update",
    )
    assert committed.acknowledgement.durable

    page = authority.read_document("owner", "page-runtime")
    with bind_temporary_unlock({
        "user_id": "owner", "workspace_id": workspace_id, "page_id": "page-runtime",
        "lock_generation": generation, "expires_at_ms": now_ms + 60_000,
    }):
        authority.save_document(
            "owner", "page-runtime", title=page["title"], blocks=page["blocks"],
            settings={"locked": False}, expected_revision=page["revision"],
            expected_workspace_epoch=page["workspace_epoch"],
            expected_document_epoch=page["document_epoch"], operation_id="runtime-temporary-unlock-global",
        )
    page = authority.read_document("owner", "page-runtime")
    authority.save_document(
        "owner", "page-runtime", title=page["title"], blocks=page["blocks"],
        settings={"locked": True}, expected_revision=page["revision"],
        expected_workspace_epoch=page["workspace_epoch"],
        expected_document_epoch=page["document_epoch"], operation_id="runtime-temporary-relock-global",
    )
    before = authority.read_document("owner", "page-runtime")
    with pytest.raises(PermissionError, match="page is locked"):
        gateway.submit_payload(
            connection.connection_id, operation_id="runtime-temporary-stale-edit", client_sequence=2,
            acknowledgement_sequence=before["document_sequence"], payload=b"must-not-commit",
        )
    assert authority.read_document("owner", "page-runtime")["document_sequence"] == before["document_sequence"]


@pytest.mark.integration
def test_t0b_p2_durable_commit_broadcast_replay_and_restart(tmp_path: Path) -> None:
    gateway, transport, root, catalog, workspace_id = _environment(tmp_path)
    writer = _connect(gateway, transport, workspace_id, client_id="writer", binding_secret="writer-session", now_ms=1_000)
    reader = _connect(gateway, transport, workspace_id, client_id="reader", binding_secret="reader-session", now_ms=2_000)

    receipt = gateway.submit_payload(
        writer.connection_id,
        operation_id="op-runtime-update-0001",
        client_sequence=1,
        acknowledgement_sequence=0,
        payload=b"opaque-yjs-update",
    )
    assert receipt.acknowledgement.durable is True
    assert receipt.broadcast == receipt.broadcast.__class__(2, 2, 0, 0, 0)
    delivered = gateway.drain(reader.connection_id, max_frames=8)
    assert [(frame.kind, frame.document_sequence, frame.payload) for frame in delivered] == [
        ("crdt.update", receipt.acknowledgement.document_sequence, b"opaque-yjs-update")
    ]
    gateway.stop()

    restarted = RuntimeGateway(
        transport=DurableTransportAuthority.open(catalog, root),
        aggregate_enabled=True,
        realtime_enabled=True,
    )
    restarted.start(compactor_handshake_matches=True)
    replay_client = _connect(
        restarted,
        restarted.transport,
        workspace_id,
        client_id="replay",
        binding_secret="replay-session",
        now_ms=3_000,
    )
    replay = restarted.replay_to_queue(replay_client.connection_id, after_document_sequence=0, limit=16)
    assert replay.high_water_sequence == receipt.acknowledgement.document_sequence
    assert replay.event_frames_enqueued == 1
    assert restarted.drain(replay_client.connection_id, max_frames=8)[0].payload == b"opaque-yjs-update"


@pytest.mark.integration
def test_t0b_p2_same_binding_reconnect_fences_old_session_and_degraded_compactor_blocks_new_admission(
    tmp_path: Path,
) -> None:
    gateway, transport, _root, _catalog, workspace_id = _environment(tmp_path)
    first = _connect(gateway, transport, workspace_id, client_id="same", binding_secret="same-session", now_ms=1_000)
    second = _connect(gateway, transport, workspace_id, client_id="same", binding_secret="same-session", now_ms=2_000)
    assert gateway.session_status(first.connection_id)["close_reason"] == "superseded_by_reconnect"
    assert gateway.session_status(second.connection_id)["active"] is True

    health = gateway.set_compactor_ready(False)
    assert health["ready_for_admission"] is False
    assert health["ready_for_realtime"] is True
    with pytest.raises(GatewayUnavailable, match="collaboration gateway unavailable"):
        _connect(gateway, transport, workspace_id, client_id="blocked", binding_secret="blocked-session", now_ms=3_000)


@pytest.mark.integration
def test_t0b_p2_capacity_and_reconnect_storm_reject_before_ticket_consumption(tmp_path: Path) -> None:
    gateway, transport, _root, _catalog, workspace_id = _environment(
        tmp_path,
        max_connections=1,
        reconnect_attempts=2,
    )
    _connect(gateway, transport, workspace_id, client_id="first", binding_secret="first-session", now_ms=1_000)
    binding = session_binding_sha256("capacity-session")
    capacity_ticket = transport.issue_ticket(
        csrf_valid=True,
        subject_id="member",
        workspace_id=workspace_id,
        document_id="page-runtime",
        client_id="capacity",
        session_binding_sha256=binding,
        origin=ORIGIN,
        capabilities=("read", "write"),
        now_ms=2_000,
    )
    with pytest.raises(ConnectionCapacityExceeded):
        gateway.connect(
            capacity_ticket.token,
            origin=ORIGIN,
            subject_id="member",
            workspace_id=workspace_id,
            page_id="page-runtime",
            client_id="capacity",
            session_binding_sha256=binding,
            now_ms=2_001,
        )
    capacity_row = next(row for row in transport.ticket_rows() if row["jti"] == capacity_ticket.claims.jti)
    assert capacity_row["consumed_at_ms"] is None

    # A separate gateway isolates the reconnect limiter from the capacity case.
    storm = RuntimeGateway(
        transport=transport,
        aggregate_enabled=True,
        realtime_enabled=True,
        max_connections=4,
        reconnect_attempts=2,
    )
    storm.start(compactor_handshake_matches=True)
    _connect(storm, transport, workspace_id, client_id="storm", binding_secret="storm-session", now_ms=10_000)
    _connect(storm, transport, workspace_id, client_id="storm", binding_secret="storm-session", now_ms=10_001)
    with pytest.raises(ReconnectThrottled):
        _connect(storm, transport, workspace_id, client_id="storm", binding_secret="storm-session", now_ms=10_002)


@pytest.mark.integration
def test_t0b_p2_slow_consumer_is_fenced_only_after_durable_commit(tmp_path: Path) -> None:
    gateway, transport, _root, _catalog, workspace_id = _environment(tmp_path, max_queue_frames=1)
    writer = _connect(gateway, transport, workspace_id, client_id="slow", binding_secret="slow-session", now_ms=1_000)
    first = gateway.submit_payload(
        writer.connection_id,
        operation_id="op-runtime-slow-update-0001",
        client_sequence=1,
        acknowledgement_sequence=0,
        payload=b"one",
    )
    assert first.broadcast.enqueued_sessions == 1
    second = gateway.submit_payload(
        writer.connection_id,
        operation_id="op-runtime-slow-update-0002",
        client_sequence=2,
        acknowledgement_sequence=first.acknowledgement.document_sequence,
        payload=b"two",
    )
    assert second.acknowledgement.durable is True
    assert second.broadcast.backpressured_sessions == 1
    assert gateway.session_status(writer.connection_id) == {
        "connection_id": writer.connection_id,
        "active": False,
        "close_reason": "backpressure_exceeded",
        "queued_frames": 0,
        "queued_payload_bytes": 0,
        "last_queued_document_sequence": None,
    }
    assert transport.counts()["workspace_collaboration_crdt_updates"] == 2
    with pytest.raises(SessionClosed):
        gateway.drain(writer.connection_id, max_frames=1)


@pytest.mark.integration
def test_c0_p1_presence_is_ephemeral_coalesced_and_expires_with_leave(
    tmp_path: Path,
) -> None:
    gateway, transport, _root, catalog, workspace_id = _environment(tmp_path)
    first = _connect(
        gateway,
        transport,
        workspace_id,
        client_id="presence-a",
        binding_secret="presence-a-session",
        now_ms=1_000,
    )
    second = _connect(
        gateway,
        transport,
        workspace_id,
        client_id="presence-b",
        binding_secret="presence-b-session",
        now_ms=2_000,
    )
    now_ms = int(time.time() * 1000)
    receipt = gateway.update_presence(
        first.connection_id,
        payload={"state": "active", "cursor_block_id": "block-runtime"},
        now_ms=now_ms,
        ttl_ms=5_000,
    )
    assert receipt.considered_sessions == receipt.enqueued_sessions == 2
    frames = gateway.drain(second.connection_id, max_frames=8)
    assert [frame.kind for frame in frames] == ["presence.update"]
    assert b"member" in frames[0].payload

    assert gateway.expire_presence(now_ms=now_ms + 5_000) == 1
    frames = gateway.drain(second.connection_id, max_frames=8)
    assert [frame.kind for frame in frames] == ["presence.leave"]
    con = sqlite3.connect(catalog)
    try:
        assert all(
            "presence" not in str(row[0])
            for row in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        )
    finally:
        con.close()
