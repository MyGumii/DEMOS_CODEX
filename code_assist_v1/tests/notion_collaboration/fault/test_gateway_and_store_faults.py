from __future__ import annotations

from pathlib import Path
import sqlite3
from types import MethodType

import pytest

from code_assist_v1.notion_collaboration.canonical_authority import InjectedFailure
from code_assist_v1.notion_collaboration.durable_transport import (
    InjectedTransportFailure,
    YJS_UPDATE_ENCODING,
)
from code_assist_v1.notion_collaboration.file_authority import (
    CapacityUnavailable,
    CollaborativeFileAuthority,
)
from code_assist_v1.notion_collaboration.protocol_contract import ProtocolEnvelope
from code_assist_v1.notion_collaboration.q0_validation import is_sqlite_busy_error
from code_assist_v1.tests.notion_collaboration.q0_support import (
    ORIGIN,
    PAGE_ID,
    build_q0_environment,
    table_count,
    upload_one,
)


def _grant(environment, *, client_id: str, now_ms: int):
    ticket, binding = environment.issue_ticket(
        environment.subjects[1],
        client_id=client_id,
        binding_secret=client_id + "-binding",
        now_ms=now_ms,
    )
    grant = environment.transport.consume_ticket(
        ticket.token,
        origin=ORIGIN,
        subject_id=environment.subjects[1],
        workspace_id=environment.workspace_id,
        document_id=PAGE_ID,
        client_id=client_id,
        session_binding_sha256=binding,
        now_ms=now_ms + 1,
    )
    return grant


def _envelope(grant, *, operation_id: str, client_sequence: int = 1):
    return ProtocolEnvelope.create(
        grant,
        message_type="crdt.update",
        operation_id=operation_id,
        client_sequence=client_sequence,
        acknowledgement_sequence=0,
        payload=(operation_id + "-payload").encode(),
        capabilities=("write",),
        payload_encoding=YJS_UPDATE_ENCODING,
    )


@pytest.mark.integration
@pytest.mark.fault
@pytest.mark.scenario("OPS-005")
def test_q0_store_faults_roll_back_and_after_commit_retry_is_exactly_once(
    tmp_path: Path,
) -> None:
    environment = build_q0_environment(tmp_path / "store-faults", registered_users=2)
    grant = _grant(environment, client_id="fault-writer", now_ms=1_000)
    before = environment.transport.counts()
    fault_points = (
        "after_domain",
        "after_event",
        "after_audit",
        "after_outbox",
        "after_operation",
    )
    for index, fault in enumerate(fault_points, start=1):
        envelope = _envelope(
            grant,
            operation_id=f"op-q0-rollback-{index:04d}",
        )
        with pytest.raises(InjectedFailure, match=fault):
            environment.transport.accept_update(envelope, grant, fault=fault)
        assert environment.transport.counts() == before

    committed = _envelope(grant, operation_id="op-q0-after-commit-0001")
    with pytest.raises(InjectedTransportFailure, match="after_commit_before_ack"):
        environment.transport.accept_update(
            committed,
            grant,
            fault="after_commit_before_ack",
        )
    after_uncertain = environment.transport.counts()
    assert after_uncertain["workspace_collaboration_crdt_updates"] == 1
    acknowledgement = environment.transport.accept_update(committed, grant)
    assert acknowledgement.durable is True
    assert environment.transport.counts() == after_uncertain

    reader = environment.connect(
        environment.subjects[1],
        client_id="fault-replay-reader",
        binding_secret="fault-replay-reader-binding",
        now_ms=10_000,
    )
    replay = environment.gateway.replay_to_queue(
        reader.connection_id,
        after_document_sequence=acknowledgement.document_sequence - 1,
        limit=8,
    )
    assert replay.event_frames_enqueued == 1
    frames = environment.gateway.drain(reader.connection_id, max_frames=8)
    assert [item.operation_id for item in frames] == [acknowledgement.operation_id]
    environment.gateway.stop()


@pytest.mark.integration
@pytest.mark.fault
@pytest.mark.scenario("OPS-005")
def test_q0_sqlite_busy_is_countable_and_clean_retry_preserves_the_operation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment = build_q0_environment(tmp_path / "sqlite-busy", registered_users=2)
    grant = _grant(environment, client_id="busy-writer", now_ms=1_000)
    envelope = _envelope(grant, operation_id="op-q0-sqlite-busy-0001")
    transport = environment.transport

    def short_connect(self, *, query_only: bool):
        if query_only:
            connection = sqlite3.connect(
                f"{self.catalog_path.as_uri()}?mode=ro",
                uri=True,
                timeout=0.02,
                isolation_level=None,
            )
        else:
            connection = sqlite3.connect(
                self.catalog_path,
                timeout=0.02,
                isolation_level=None,
            )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=20")
        if query_only:
            connection.execute("PRAGMA query_only=ON")
        return connection

    monkeypatch.setattr(transport, "_connect", MethodType(short_connect, transport))
    locker = sqlite3.connect(environment.catalog, isolation_level=None)
    try:
        locker.execute("BEGIN EXCLUSIVE")
        with pytest.raises(sqlite3.OperationalError) as locked:
            transport.accept_update(envelope, grant)
        assert is_sqlite_busy_error(locked.value) is True
        locker.rollback()
        assert table_count(environment.catalog, "workspace_collaboration_crdt_updates") == 0
        acknowledgement = transport.accept_update(envelope, grant)
    finally:
        try:
            locker.rollback()
        finally:
            locker.close()
    assert acknowledgement.durable is True
    assert table_count(environment.catalog, "workspace_collaboration_crdt_updates") == 1
    environment.gateway.stop()


@pytest.mark.integration
@pytest.mark.fault
@pytest.mark.scenario("OPS-005")
def test_q0_gateway_stop_restart_replays_acknowledged_update(tmp_path: Path) -> None:
    environment = build_q0_environment(tmp_path / "gateway-restart", registered_users=2)
    writer = environment.connect(
        environment.subjects[1],
        client_id="restart-writer",
        binding_secret="restart-writer-binding",
        now_ms=1_000,
    )
    acknowledgement = environment.gateway.submit_payload(
        writer.connection_id,
        operation_id="op-q0-gateway-restart-0001",
        client_sequence=1,
        acknowledgement_sequence=0,
        payload=b"acknowledged-before-stop",
    ).acknowledgement
    environment.gateway.stop()
    assert environment.gateway.start(compactor_handshake_matches=True)[
        "ready_for_admission"
    ] is True
    reader = environment.connect(
        environment.subjects[1],
        client_id="restart-reader",
        binding_secret="restart-reader-binding",
        now_ms=20_000,
    )
    environment.gateway.replay_to_queue(
        reader.connection_id,
        after_document_sequence=acknowledgement.document_sequence - 1,
        limit=8,
    )
    frame = environment.gateway.drain(reader.connection_id, max_frames=8)[0]
    assert frame.operation_id == acknowledgement.operation_id
    assert frame.payload == b"acknowledged-before-stop"
    metrics = environment.gateway.metrics_snapshot()
    restart_count = next(
        item["value"]
        for item in metrics["counters"]
        if item["name"] == "gateway_restart_total" and not item["labels"]
    )
    assert restart_count == 1
    environment.gateway.stop()


@pytest.mark.integration
@pytest.mark.fault
@pytest.mark.scenario("OPS-005")
def test_q0_disk_reserve_rejects_new_capacity_without_hiding_existing_file(
    tmp_path: Path,
) -> None:
    environment = build_q0_environment(
        tmp_path / "disk-reserve",
        registered_users=2,
        through_file_authority=True,
    )
    assert environment.files is not None
    actor = environment.subjects[1]
    existing = upload_one(
        environment.files,
        actor=actor,
        payload=b"existing-file-remains-readable",
        operation_suffix="disk-existing-0001",
    )
    bounded = CollaborativeFileAuthority.open(
        environment.catalog,
        environment.root,
        environment.identities,
        minimum_free_bytes=2**63,
        minimum_free_percent=100.0,
    )
    page = bounded.read_document(actor, PAGE_ID)
    with pytest.raises(CapacityUnavailable, match="disk reserve"):
        bounded.begin_upload(
            actor,
            PAGE_ID,
            original_name="blocked.bin",
            mime_type="application/octet-stream",
            total_size=1,
            expected_sha256="sha256:" + "0" * 64,
            expected_document_sequence=int(page["document_sequence"]),
            expected_workspace_epoch=int(page["workspace_epoch"]),
            expected_document_epoch=int(page["document_epoch"]),
            operation_id="op-q0-disk-reserve-blocked-0001",
            chunk_size=65_536,
        )
    visible = bounded.get_file(actor, str(existing["file_id"]))
    assert visible["path"].read_bytes() == b"existing-file-remains-readable"
    environment.gateway.stop()
