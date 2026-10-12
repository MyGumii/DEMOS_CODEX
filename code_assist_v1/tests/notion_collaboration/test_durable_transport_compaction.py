from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest

from code_assist_v1 import notion_workspace_collaboration_migration as authority_migration
from code_assist_v1 import notion_workspace_realtime_transport_migration as transport_migration
from code_assist_v1 import notion_workspace_store as workspace_store
from code_assist_v1.notion_collaboration.canonical_authority import CanonicalAuthority, InjectedFailure
from code_assist_v1.notion_collaboration.durable_transport import (
    DurableTransportAuthority,
    YJS_UPDATE_ENCODING,
)
from code_assist_v1.notion_collaboration.protocol_contract import ProtocolEnvelope, session_binding_sha256
from code_assist_v1.notion_collaboration.selected_runtime_contract import (
    COMPACTION_RESPONSE_CONTRACT,
    SelectedRuntimeContractError,
    runtime_binding_sha256,
    sha256_bytes,
)


ORIGIN = "http://127.0.0.1:10112"


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


def _environment(tmp_path: Path):
    root = tmp_path / "t0b-compaction"
    catalog = Path(workspace_store.catalog_path_for_user_data_root(root))
    store = workspace_store.WorkspaceStore(catalog)
    store.bootstrap()
    created = store.create_workspace(
        "owner", name="T0B", operation_id="op-t0b-compact-workspace-0001"
    )
    workspace_id = str(created["workspace"]["id"])
    backup_2401 = _backup(catalog, root / "backups" / "before-2401.db")
    plan_2401 = authority_migration.build_plan(catalog, root)
    authority_migration.apply_plan(
        catalog, root, exact_plan_checksum=plan_2401["plan_checksum"],
        backup_confirmed=True, catalog_quiesced_confirmed=True, backup_path=backup_2401,
    )
    CanonicalAuthority.open(catalog, root).create_document(
        "owner", workspace_id, "page-t0b", title="Compact",
        blocks=[{"id": "block-t0b", "type": "text", "content": "base"}],
        expected_workspace_epoch=1, operation_id="op-t0b-compact-document-0001",
    )
    backup_2402 = _backup(catalog, root / "backups" / "before-2402.db")
    plan_2402 = transport_migration.build_plan(catalog, root)
    transport_migration.apply_plan(
        catalog, root, exact_plan_checksum=plan_2402["plan_checksum"],
        backup_confirmed=True, catalog_quiesced_confirmed=True, backup_path=backup_2402,
    )
    transport = DurableTransportAuthority.open(catalog, root)
    session = session_binding_sha256("compact-session")
    issued = transport.issue_ticket(
        csrf_valid=True, subject_id="owner", workspace_id=workspace_id,
        document_id="page-t0b", client_id="client-compact",
        session_binding_sha256=session, origin=ORIGIN,
        capabilities=("read", "write"), now_ms=1_000,
    )
    grant = transport.consume_ticket(
        issued.token, origin=ORIGIN, subject_id="owner", workspace_id=workspace_id,
        document_id="page-t0b", client_id="client-compact",
        session_binding_sha256=session, now_ms=1_001,
    )
    return transport, grant


def _update(transport, grant, sequence: int, ack: int, payload: bytes) -> int:
    envelope = ProtocolEnvelope.create(
        grant, message_type="crdt.update", operation_id=f"op-t0b-compact-update-{sequence:04d}",
        client_sequence=sequence, acknowledgement_sequence=ack, payload=payload,
        capabilities=("write",), payload_encoding=YJS_UPDATE_ENCODING,
    )
    return transport.accept_update(envelope, grant).document_sequence


def _response(request, snapshot: bytes, state_vector: bytes) -> dict:
    return {
        "contract": COMPACTION_RESPONSE_CONTRACT,
        "request_id": request["request_id"],
        "request_sha256": request["request_sha256"],
        "base_high_water_sequence": request["base_high_water_sequence"],
        "target_high_water_sequence": request["target_high_water_sequence"],
        "applied_update_count": request["ordered_update_count"],
        "snapshot_bytes": len(snapshot),
        "snapshot_sha256": sha256_bytes(snapshot),
        "state_vector_bytes": len(state_vector),
        "state_vector_sha256": sha256_bytes(state_vector),
        "runtime_binding_sha256": runtime_binding_sha256(),
    }


@pytest.mark.integration
def test_t0b_p1_selected_runtime_compaction_snapshot_then_tail_replay(tmp_path: Path) -> None:
    transport, grant = _environment(tmp_path)
    first_sequence = _update(transport, grant, 1, 0, b"yjs-one")
    second_sequence = _update(transport, grant, 2, first_sequence, b"yjs-two")
    work = transport.prepare_compaction(grant)
    assert work.request["ordered_update_count"] == 2
    assert work.ordered_updates == (b"yjs-one", b"yjs-two")
    snapshot = b"opaque-yjs-snapshot"
    state_vector = b"opaque-state-vector"
    committed = transport.commit_compaction(
        grant,
        operation_id="op-t0b-compaction-0001",
        work=work,
        response=_response(work.request, snapshot, state_vector),
        snapshot=snapshot,
        state_vector=state_vector,
    )
    assert committed["through_update_sequence"] == 2
    replay = transport.replay(grant, after_document_sequence=0, limit=10)
    assert replay.snapshot is not None
    assert replay.snapshot.snapshot == snapshot
    assert replay.events == ()
    third_sequence = _update(transport, grant, 3, second_sequence, b"yjs-three")
    replay = transport.replay(grant, after_document_sequence=0, limit=10)
    assert replay.snapshot is not None
    assert [event.document_sequence for event in replay.events] == [third_sequence]
    assert replay.events[0].payload == b"yjs-three"


@pytest.mark.integration
def test_t0b_p1_compaction_hash_tamper_and_fault_never_publish_snapshot(tmp_path: Path) -> None:
    transport, grant = _environment(tmp_path)
    _update(transport, grant, 1, 0, b"yjs-one")
    work = transport.prepare_compaction(grant)
    snapshot = b"snapshot"
    vector = b"vector"
    response = _response(work.request, snapshot, vector)
    response["snapshot_sha256"] = "sha256:" + "0" * 64
    with pytest.raises(SelectedRuntimeContractError, match="bytes do not match"):
        transport.commit_compaction(
            grant, operation_id="op-t0b-compaction-tamper-0001", work=work,
            response=response, snapshot=snapshot, state_vector=vector,
        )
    assert transport.counts()["workspace_collaboration_crdt_snapshots"] == 0

    with pytest.raises(InjectedFailure, match="after_outbox"):
        transport.commit_compaction(
            grant, operation_id="op-t0b-compaction-fault-0001", work=work,
            response=_response(work.request, snapshot, vector), snapshot=snapshot,
            state_vector=vector, fault="after_outbox",
        )
    assert transport.counts()["workspace_collaboration_crdt_snapshots"] == 0
