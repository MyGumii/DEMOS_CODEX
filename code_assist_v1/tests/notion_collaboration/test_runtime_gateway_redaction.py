from __future__ import annotations

import json
from pathlib import Path
import sqlite3

import pytest

from code_assist_v1 import notion_workspace_collaboration_migration as authority_migration
from code_assist_v1 import notion_workspace_realtime_transport_migration as transport_migration
from code_assist_v1 import notion_workspace_store as workspace_store
from code_assist_v1.notion_collaboration.canonical_authority import CanonicalAuthority
from code_assist_v1.notion_collaboration.durable_transport import DurableTransportAuthority, ForwardCompatibleCanonicalAuthority
from code_assist_v1.notion_collaboration.protocol_contract import StaleEpoch, session_binding_sha256
from code_assist_v1.notion_collaboration.runtime_gateway import RuntimeGateway


ORIGIN = "http://127.0.0.1:49322"


def _backup(source: Path, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_connection = sqlite3.connect(source)
    destination_connection = sqlite3.connect(destination)
    try:
        source_connection.backup(destination_connection)
        destination_connection.commit()
    finally:
        destination_connection.close()
        source_connection.close()
    return destination


def _environment(tmp_path: Path):
    root = tmp_path / "redaction"
    catalog = Path(workspace_store.catalog_path_for_user_data_root(root))
    store = workspace_store.WorkspaceStore(catalog)
    store.bootstrap()
    created = store.create_workspace("owner", name="Secret workspace title", operation_id="op-redact-workspace-create-0001")
    workspace_id = str(created["workspace"]["id"])
    store.set_member(
        "owner",
        workspace_id,
        "member-secret-name",
        role="member",
        base_version=int(created["workspace"]["version"]),
        operation_id="op-redact-member-set-0001",
    )
    backup_2401 = _backup(catalog, root / "backups" / "2401.db")
    plan_2401 = authority_migration.build_plan(catalog, root)
    authority_migration.apply_plan(
        catalog,
        root,
        exact_plan_checksum=plan_2401["plan_checksum"],
        backup_confirmed=True,
        catalog_quiesced_confirmed=True,
        backup_path=backup_2401,
    )
    canonical = CanonicalAuthority.open(catalog, root)
    canonical.create_document(
        "owner",
        workspace_id,
        "page-secret-id",
        title="Secret page title",
        blocks=[{"id": "block-secret", "type": "text", "content": "private unpublished body"}],
        expected_workspace_epoch=2,
        operation_id="op-redact-document-create-0001",
    )
    backup_2402 = _backup(catalog, root / "backups" / "2402.db")
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
    gateway = RuntimeGateway(transport=transport, aggregate_enabled=True, realtime_enabled=True)
    gateway.start(compactor_handshake_matches=True)
    binding = session_binding_sha256("redaction-session")
    ticket = transport.issue_ticket(
        csrf_valid=True,
        subject_id="member-secret-name",
        workspace_id=workspace_id,
        document_id="page-secret-id",
        client_id="redaction-client",
        session_binding_sha256=binding,
        origin=ORIGIN,
        capabilities=("read", "write"),
        now_ms=1_000,
    )
    session = gateway.connect(
        ticket.token,
        origin=ORIGIN,
        subject_id="member-secret-name",
        workspace_id=workspace_id,
        page_id="page-secret-id",
        client_id="redaction-client",
        session_binding_sha256=binding,
        now_ms=1_001,
    )
    return gateway, ForwardCompatibleCanonicalAuthority.open(catalog, root), session, workspace_id


@pytest.mark.integration
def test_t0b_p2_cache_search_and_inbox_frames_are_purged_after_revoke(tmp_path: Path) -> None:
    gateway, canonical, session, workspace_id = _environment(tmp_path)
    payloads = {
        "cache": b'{"body":"private-cache-result"}',
        "search": b'{"body":"private-search-result"}',
        "inbox": b'{"body":"private-inbox-result"}',
    }
    for kind, payload in payloads.items():
        assert gateway.queue_derived_frame(session.connection_id, kind=kind, payload=payload) == "enqueued"
    assert gateway.session_status(session.connection_id)["queued_frames"] == 3

    canonical.revoke_subject(
        "owner",
        workspace_id,
        "member-secret-name",
        expected_workspace_epoch=2,
        operation_id="op-redact-member-revoke-0001",
    )
    with pytest.raises(StaleEpoch):
        gateway.drain(session.connection_id, max_frames=3)
    status = gateway.session_status(session.connection_id)
    assert status["active"] is False
    assert status["close_reason"] == "stale_epoch"
    assert status["queued_frames"] == 0
    assert status["queued_payload_bytes"] == 0


@pytest.mark.integration
def test_t0b_p2_metrics_export_hashes_scope_and_contains_no_identity_or_content(tmp_path: Path) -> None:
    gateway, _canonical, session, workspace_id = _environment(tmp_path)
    private_payload = b'{"body":"private-search-result"}'
    gateway.queue_derived_frame(session.connection_id, kind="search", payload=private_payload)
    gateway.drain(session.connection_id, max_frames=1)
    exported = json.dumps(gateway.metrics.snapshot(), ensure_ascii=False, sort_keys=True)
    for forbidden in (
        workspace_id,
        "page-secret-id",
        "member-secret-name",
        "Secret workspace title",
        "Secret page title",
        "private unpublished body",
        "private-search-result",
        session.connection_id,
    ):
        assert forbidden not in exported
    assert "workspace_id_hash" in exported
    assert "page_id_hash" in exported
    assert "sha256:" in exported
