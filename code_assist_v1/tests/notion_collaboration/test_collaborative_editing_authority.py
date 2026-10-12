from __future__ import annotations

import base64
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

from flask import Flask
import pytest

from code_assist_v1 import notion_workspace_collaboration_migration as canonical_migration
from code_assist_v1 import notion_workspace_editing_authority_migration as editing_migration
from code_assist_v1 import notion_workspace_iam_migration as iam_migration
from code_assist_v1 import notion_workspace_live_collaboration_migration as live_migration
from code_assist_v1 import notion_workspace_realtime_transport_migration as transport_migration
from code_assist_v1 import notion_workspace_store as workspace_store
from code_assist_v1 import routes_notion_workspace
from code_assist_v1.notion_collaboration.canonical_authority import (
    InjectedFailure,
    NotFoundOrDenied,
)
from code_assist_v1.notion_collaboration.editing_authority import (
    CheckpointConflict,
    CollaborativeEditingAuthority,
    CollaborativeEditingTransportAuthority,
    DocumentSequenceConflict,
)
from code_assist_v1.notion_collaboration.cross_process_gateway import (
    CrossProcessGatewayRuntime,
)
from code_assist_v1.notion_collaboration.iam_authority import InMemoryIdentityDirectory
from code_assist_v1.notion_collaboration.iam_runtime import CollaborativeWorkspaceStore
from code_assist_v1.notion_collaboration.live_authority import CollaborativeLiveAuthority
from code_assist_v1.notion_collaboration.node_compactor import NodeCompactor
from code_assist_v1.notion_collaboration.protocol_contract import (
    ProtocolEnvelope,
    session_binding_sha256,
)
from code_assist_v1.notion_collaboration.runtime_gateway import RuntimeGateway
from code_assist_v1.notion_collaboration.selected_runtime_contract import (
    COMPACTION_RESPONSE_CONTRACT,
    runtime_binding_sha256,
    sha256_bytes,
)
from code_assist_v1.notion_collaboration.durable_transport import YJS_UPDATE_ENCODING


ORIGIN = "http://127.0.0.1:10112"
SUBJECTS = ("owner", "content-editor", "schema-editor")


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


def _apply(migration, catalog: Path, root: Path, name: str) -> None:
    backup = _backup(catalog, root / "backups" / name)
    plan = migration.build_plan(catalog, root)
    migration.apply_plan(
        catalog,
        root,
        exact_plan_checksum=plan["plan_checksum"],
        backup_confirmed=True,
        catalog_quiesced_confirmed=True,
        backup_path=backup,
    )


def _environment(tmp_path: Path):
    root = tmp_path / "e1-editing"
    catalog = Path(workspace_store.catalog_path_for_user_data_root(root))
    store = workspace_store.WorkspaceStore(catalog)
    store.bootstrap()
    created = store.create_workspace(
        "owner", name="E1 workspace", operation_id="op-e1-workspace-create-0001"
    )
    workspace_id = str(created["workspace"]["id"])
    version = int(created["workspace"]["version"])
    for index, subject in enumerate(SUBJECTS[1:], start=1):
        result = store.set_member(
            "owner",
            workspace_id,
            subject,
            role="member",
            base_version=version,
            operation_id=f"op-e1-member-set-{index:04d}",
        )
        version = int(result["workspace"]["version"])
    _apply(canonical_migration, catalog, root, "before-2401.db")
    _apply(transport_migration, catalog, root, "before-2402.db")
    _apply(iam_migration, catalog, root, "before-2403.db")
    _apply(live_migration, catalog, root, "before-2404.db")
    identities = InMemoryIdentityDirectory(SUBJECTS)
    live = CollaborativeLiveAuthority.open(catalog, root, identities)
    con = sqlite3.connect(catalog)
    try:
        workspace_epoch = int(
            con.execute(
                "SELECT security_epoch FROM workspaces WHERE id=?", (workspace_id,)
            ).fetchone()[0]
        )
    finally:
        con.close()
    live.create_document(
        "owner",
        workspace_id,
        "page-e1",
        title="Collaborative editing",
        blocks=[
            {"id": "block-a", "type": "paragraph", "content": "A"},
            {"id": "block-b", "type": "paragraph", "content": "B"},
        ],
        expected_workspace_epoch=workspace_epoch,
        operation_id="op-e1-page-create-0001",
    )
    for index, (subject, role) in enumerate(
        (("content-editor", "edit_content"), ("schema-editor", "edit")), start=1
    ):
        page = live.read_document("owner", "page-e1")
        live.set_page_grant(
            "owner",
            "page-e1",
            "subject",
            subject,
            role=role,
            inherit_to_descendants=False,
            expected_workspace_epoch=int(page["workspace_epoch"]),
            expected_document_epoch=int(page["document_epoch"]),
            operation_id=f"op-e1-page-grant-{index:04d}",
        )
    _apply(editing_migration, catalog, root, "before-2405.db")
    con = sqlite3.connect(catalog)
    try:
        now = 1_800_000_000
        con.execute(
            "INSERT INTO workspace_collaboration_databases("
            "id,document_id,title,row_version,created_at,updated_at) VALUES(?,?,?,1,?,?)",
            ("database-e1", "page-e1", "Tasks", now, now),
        )
        con.execute(
            "INSERT INTO workspace_collaboration_database_properties("
            "id,database_id,name,property_type,position,config_json,row_version,created_at,updated_at) "
            "VALUES(?,?,?,?,0,'{}',1,?,?)",
            ("property-e1", "database-e1", "Status", "text", now, now),
        )
        con.execute(
            "INSERT INTO workspace_collaboration_database_rows("
            "id,database_id,position,row_version,created_at,updated_at) VALUES(?,?,0,1,?,?)",
            ("row-e1", "database-e1", now, now),
        )
        con.execute(
            "INSERT INTO workspace_collaboration_database_cells("
            "row_id,property_id,value_json,row_version,updated_at) VALUES(?,?,?,1,?)",
            ("row-e1", "property-e1", '"todo"', now),
        )
        con.commit()
    finally:
        con.close()
    authority = CollaborativeEditingAuthority.open(catalog, root, identities)
    transport = CollaborativeEditingTransportAuthority.open(catalog, root)
    return authority, transport, root, catalog, workspace_id


def _epochs(authority: CollaborativeEditingAuthority, subject: str) -> tuple[int, int, int, int]:
    page = authority.read_document(subject, "page-e1")
    return (
        int(page["workspace_epoch"]),
        int(page["document_epoch"]),
        int(page["revision"]),
        int(page["document_sequence"]),
    )


@pytest.mark.integration
def test_e1_p2_transport_canonical_adapter_replays_live_domain_events(
    tmp_path: Path,
) -> None:
    authority, transport, _root, _catalog, _workspace_id = _environment(tmp_path)
    workspace_epoch, document_epoch, _revision, sequence = _epochs(authority, "owner")
    created = authority.add_comment(
        "owner",
        "page-e1",
        "comment-e1-p2-replay",
        content="gateway replay",
        expected_workspace_epoch=workspace_epoch,
        expected_document_epoch=document_epoch,
        operation_id="op-e1-p2-comment-replay-0001",
    )

    replay = transport.canonical_authority().replay_domain_events(
        "content-editor",
        "page-e1",
        after_document_sequence=sequence,
    )

    assert replay.high_water_sequence == created["document_sequence"]
    assert replay.next_document_sequence == created["document_sequence"]
    assert replay.has_more is False
    assert [event.event_type for event in replay.events] == ["comment.added"]
    assert replay.events[0].operation_id == "op-e1-p2-comment-replay-0001"


@pytest.mark.integration
def test_e1_p2_document_created_after_2405_accepts_its_first_crdt_update(
    tmp_path: Path,
) -> None:
    authority, transport, _root, catalog, workspace_id = _environment(tmp_path)
    workspace_epoch, _document_epoch, _revision, _sequence = _epochs(authority, "owner")
    created = authority.create_document(
        "owner",
        workspace_id,
        "page-created-after-2405",
        title="Post-migration collaborative page",
        blocks=[{"id": "block-post-2405", "type": "paragraph", "content": "seed"}],
        expected_workspace_epoch=workspace_epoch,
        operation_id="op-e1-p2-page-create-0001",
    )

    session = session_binding_sha256("e1-p2-post-migration-session")
    issued = transport.issue_ticket(
        csrf_valid=True,
        subject_id="owner",
        workspace_id=workspace_id,
        document_id=created["document_id"],
        client_id="client-post-2405",
        session_binding_sha256=session,
        origin=ORIGIN,
        capabilities=("read", "write"),
        now_ms=3_000,
    )
    grant = transport.consume_ticket(
        issued.token,
        origin=ORIGIN,
        subject_id="owner",
        workspace_id=workspace_id,
        document_id=created["document_id"],
        client_id="client-post-2405",
        session_binding_sha256=session,
        now_ms=3_001,
    )
    update = ProtocolEnvelope.create(
        grant,
        message_type="crdt.update",
        operation_id="op-e1-p2-first-update-0001",
        client_sequence=1,
        acknowledgement_sequence=created["document_sequence"],
        payload=b"opaque-post-migration-yjs-update",
        capabilities=("write",),
        payload_encoding=YJS_UPDATE_ENCODING,
    )
    acknowledgement = transport.accept_update(update, grant)

    con = sqlite3.connect(catalog)
    try:
        generation = con.execute(
            "SELECT generation,state,yjs_document_key FROM "
            "workspace_collaboration_crdt_generations WHERE document_id=?",
            (created["document_id"],),
        ).fetchone()
        checkpoint = con.execute(
            "SELECT revision,document_sequence,crdt_generation,checkpoint_reason "
            "FROM workspace_collaboration_revision_checkpoints WHERE document_id=?",
            (created["document_id"],),
        ).fetchone()
        binding = con.execute(
            "SELECT generation,rich_text_scope FROM workspace_collaboration_crdt_update_bindings "
            "WHERE document_id=?",
            (created["document_id"],),
        ).fetchone()
    finally:
        con.close()
    assert generation == (
        1,
        "current",
        "document:page-created-after-2405:rich-text:g1",
    )
    assert checkpoint == (1, 1, 1, "initial")
    assert binding == (1, "document_rich_text_leaves")
    assert acknowledgement.document_sequence == 2


@pytest.mark.migration
def test_e1_p1_2405_migration_is_frozen_forward_only_and_backfills_identity(
    tmp_path: Path,
) -> None:
    authority, _transport, root, catalog, _workspace_id = _environment(tmp_path)
    plan = editing_migration.build_plan(catalog, root)
    assert plan["state"] == "already_current"
    assert editing_migration.SOURCE_SQL == live_migration.TARGET_SQL
    assert editing_migration.TARGET_SQL.endswith(
        editing_migration.EDITING_AUTHORITY_SCHEMA_SQL
    )
    con = sqlite3.connect(catalog)
    try:
        marker = con.execute(
            "SELECT name,checksum,actual_schema_sha256 FROM workspace_schema_migrations "
            "WHERE version=?",
            (editing_migration.TARGET_VERSION,),
        ).fetchone()
        generation = con.execute(
            "SELECT generation,state,yjs_document_key FROM "
            "workspace_collaboration_crdt_generations WHERE document_id='page-e1'"
        ).fetchone()
        initial_checkpoint = con.execute(
            "SELECT id,revision,crdt_generation,checkpoint_reason "
            "FROM workspace_collaboration_revision_checkpoints "
            "WHERE document_id='page-e1' AND revision=1"
        ).fetchone()
        presence_tables = con.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name LIKE '%presence%'"
        ).fetchone()[0]
    finally:
        con.close()
    assert marker == (
        editing_migration.TARGET_NAME,
        editing_migration.TARGET_CHECKSUM,
        editing_migration.TARGET_ACTUAL_SCHEMA_SHA256,
    )
    assert generation == (1, "current", "document:page-e1:rich-text:g1")
    assert initial_checkpoint == ("hist:page-e1:r1", 1, 1, "initial")
    assert presence_tables == 0
    assert authority.list_revision_checkpoints("owner", "page-e1")[-1]["revision"] == 1


@pytest.mark.integration
def test_e1_p1_server_sequences_structure_database_and_enforces_schema_role(
    tmp_path: Path,
) -> None:
    authority, _transport, _root, catalog, _workspace_id = _environment(tmp_path)
    workspace_epoch, document_epoch, revision, sequence = _epochs(
        authority, "content-editor"
    )
    inserted = authority.execute_typed_command(
        "content-editor",
        "page-e1",
        command_kind="block.insert",
        target_id="block-c",
        payload={"type": "paragraph", "content": "C", "after_block_id": "block-a"},
        expected_document_sequence=sequence,
        expected_workspace_epoch=workspace_epoch,
        expected_document_epoch=document_epoch,
        operation_id="op-e1-block-insert-0001",
    )
    assert inserted["command_sequence"] == 1
    assert inserted["document_sequence"] == sequence + 1
    assert authority.execute_typed_command(
        "content-editor",
        "page-e1",
        command_kind="block.insert",
        target_id="block-c",
        payload={"type": "paragraph", "content": "C", "after_block_id": "block-a"},
        expected_document_sequence=sequence,
        expected_workspace_epoch=workspace_epoch,
        expected_document_epoch=document_epoch,
        operation_id="op-e1-block-insert-0001",
    ) == inserted
    with pytest.raises(DocumentSequenceConflict):
        authority.execute_typed_command(
            "content-editor",
            "page-e1",
            command_kind="block.move",
            target_id="block-b",
            payload={"after_block_id": None},
            expected_document_sequence=sequence,
            expected_workspace_epoch=workspace_epoch,
            expected_document_epoch=document_epoch,
            expected_target_version=1,
            operation_id="op-e1-stale-move-0001",
        )
    with pytest.raises(NotFoundOrDenied):
        authority.execute_typed_command(
            "content-editor",
            "page-e1",
            command_kind="database.property.rename",
            target_id="property-e1",
            payload={"name": "State"},
            expected_document_sequence=inserted["document_sequence"],
            expected_workspace_epoch=workspace_epoch,
            expected_document_epoch=document_epoch,
            expected_target_version=1,
            operation_id="op-e1-content-schema-denied-0001",
        )
    renamed = authority.execute_typed_command(
        "schema-editor",
        "page-e1",
        command_kind="database.property.rename",
        target_id="property-e1",
        payload={"name": "State"},
        expected_document_sequence=inserted["document_sequence"],
        expected_workspace_epoch=workspace_epoch,
        expected_document_epoch=document_epoch,
        expected_target_version=1,
        operation_id="op-e1-schema-rename-0001",
    )
    cell = authority.execute_typed_command(
        "content-editor",
        "page-e1",
        command_kind="database.cell.set",
        target_id="row-e1:property-e1",
        payload={"row_id": "row-e1", "property_id": "property-e1", "value": "done"},
        expected_document_sequence=renamed["document_sequence"],
        expected_workspace_epoch=workspace_epoch,
        expected_document_epoch=document_epoch,
        expected_target_version=1,
        operation_id="op-e1-cell-set-0001",
    )
    deleted = authority.execute_typed_command(
        "content-editor",
        "page-e1",
        command_kind="block.delete",
        target_id="block-c",
        payload={},
        expected_document_sequence=cell["document_sequence"],
        expected_workspace_epoch=workspace_epoch,
        expected_document_epoch=document_epoch,
        expected_target_version=1,
        operation_id="op-e1-block-delete-0001",
    )
    assert deleted["tombstoned"] is True
    assert _epochs(authority, "content-editor")[2] == revision
    con = sqlite3.connect(catalog)
    try:
        assert con.execute(
            "SELECT value_json,row_version FROM workspace_collaboration_database_cells "
            "WHERE row_id='row-e1' AND property_id='property-e1'"
        ).fetchone() == ('"done"', 2)
        assert con.execute(
            "SELECT recovery_json FROM workspace_collaboration_block_tombstones "
            "WHERE block_id='block-c'"
        ).fetchone() is not None
    finally:
        con.close()


@pytest.mark.integration
def test_e1_p1_command_fault_rolls_back_and_uncompacted_update_blocks_history(
    tmp_path: Path,
) -> None:
    authority, transport, _root, catalog, workspace_id = _environment(tmp_path)
    workspace_epoch, document_epoch, revision, sequence = _epochs(
        authority, "content-editor"
    )
    with pytest.raises(InjectedFailure, match="after_outbox"):
        authority.execute_typed_command(
            "content-editor",
            "page-e1",
            command_kind="block.insert",
            target_id="block-rolled-back",
            payload={"type": "paragraph", "content": "never committed"},
            expected_document_sequence=sequence,
            expected_workspace_epoch=workspace_epoch,
            expected_document_epoch=document_epoch,
            operation_id="op-e1-command-fault-0001",
            fault="after_outbox",
        )
    assert _epochs(authority, "content-editor")[3] == sequence
    con = sqlite3.connect(catalog)
    try:
        assert con.execute(
            "SELECT COUNT(*) FROM workspace_collaboration_blocks WHERE id='block-rolled-back'"
        ).fetchone()[0] == 0
        assert con.execute(
            "SELECT COUNT(*) FROM workspace_collaboration_typed_commands "
            "WHERE operation_id='op-e1-command-fault-0001'"
        ).fetchone()[0] == 0
    finally:
        con.close()

    session = session_binding_sha256("e1-uncompacted-session")
    issued = transport.issue_ticket(
        csrf_valid=True,
        subject_id="content-editor",
        workspace_id=workspace_id,
        document_id="page-e1",
        client_id="client-uncompacted",
        session_binding_sha256=session,
        origin=ORIGIN,
        capabilities=("read", "write"),
        now_ms=2_000,
    )
    grant = transport.consume_ticket(
        issued.token,
        origin=ORIGIN,
        subject_id="content-editor",
        workspace_id=workspace_id,
        document_id="page-e1",
        client_id="client-uncompacted",
        session_binding_sha256=session,
        now_ms=2_001,
    )
    envelope = ProtocolEnvelope.create(
        grant,
        message_type="crdt.update",
        operation_id="op-e1-uncompacted-update-0001",
        client_sequence=1,
        acknowledgement_sequence=sequence,
        payload=b"opaque-uncompacted-yjs-update",
        capabilities=("write",),
        payload_encoding=YJS_UPDATE_ENCODING,
    )
    acknowledgement = transport.accept_update(envelope, grant)
    with pytest.raises(CheckpointConflict, match="uncompacted"):
        authority.create_revision_checkpoint(
            "content-editor",
            "page-e1",
            expected_revision=revision,
            expected_document_sequence=acknowledgement.document_sequence,
            expected_workspace_epoch=workspace_epoch,
            expected_document_epoch=document_epoch,
            operation_id="op-e1-uncompacted-checkpoint-0001",
        )
    assert _epochs(authority, "content-editor")[2] == revision


@pytest.mark.integration
def test_e1_p1_opaque_crdt_binding_compaction_and_checkpoint_identities(
    tmp_path: Path,
) -> None:
    authority, transport, root, catalog, workspace_id = _environment(tmp_path)
    workspace_epoch, document_epoch, revision, sequence = _epochs(
        authority, "content-editor"
    )
    session = session_binding_sha256("e1-editing-session")
    issued = transport.issue_ticket(
        csrf_valid=True,
        subject_id="content-editor",
        workspace_id=workspace_id,
        document_id="page-e1",
        client_id="client-e1",
        session_binding_sha256=session,
        origin=ORIGIN,
        capabilities=("read", "write"),
        now_ms=1_000,
    )
    grant = transport.consume_ticket(
        issued.token,
        origin=ORIGIN,
        subject_id="content-editor",
        workspace_id=workspace_id,
        document_id="page-e1",
        client_id="client-e1",
        session_binding_sha256=session,
        now_ms=1_001,
    )
    update = ProtocolEnvelope.create(
        grant,
        message_type="crdt.update",
        operation_id="op-e1-crdt-update-0001",
        client_sequence=1,
        acknowledgement_sequence=sequence,
        payload=b"opaque-yjs-v1-update",
        capabilities=("write",),
        payload_encoding=YJS_UPDATE_ENCODING,
    )
    acknowledgement = transport.accept_update(update, grant)
    assert _epochs(authority, "content-editor")[2] == revision
    work = transport.prepare_compaction(grant)
    snapshot = b"opaque-yjs-v1-snapshot"
    vector = b"opaque-yjs-v1-state-vector"
    response = {
        "contract": COMPACTION_RESPONSE_CONTRACT,
        "request_id": work.request["request_id"],
        "request_sha256": work.request["request_sha256"],
        "base_high_water_sequence": work.request["base_high_water_sequence"],
        "target_high_water_sequence": work.request["target_high_water_sequence"],
        "applied_update_count": work.request["ordered_update_count"],
        "snapshot_bytes": len(snapshot),
        "snapshot_sha256": sha256_bytes(snapshot),
        "state_vector_bytes": len(vector),
        "state_vector_sha256": sha256_bytes(vector),
        "runtime_binding_sha256": runtime_binding_sha256(),
    }
    compacted = transport.commit_compaction(
        grant,
        operation_id="op-e1-crdt-compaction-0001",
        work=work,
        response=response,
        snapshot=snapshot,
        state_vector=vector,
    )
    checkpoint = authority.create_revision_checkpoint(
        "content-editor",
        "page-e1",
        expected_revision=revision,
        expected_document_sequence=acknowledgement.document_sequence,
        expected_workspace_epoch=workspace_epoch,
        expected_document_epoch=document_epoch,
        operation_id="op-e1-history-checkpoint-0001",
        checkpoint_reason="automatic",
        publication_revision_id="publication-revision-e1-0001",
    )
    assert checkpoint["revision"] == revision + 1
    identities = checkpoint["identities"]
    assert identities == {
        "client_operation_id": "op-e1-history-checkpoint-0001",
        "document_event_sequence": acknowledgement.document_sequence + 1,
        "crdt_generation": 1,
        "crdt_snapshot_id": compacted["snapshot_id"],
        "history_checkpoint_id": checkpoint["identities"]["history_checkpoint_id"],
        "user_revision": revision + 1,
        "publication_revision_id": "publication-revision-e1-0001",
    }
    assert len({
        str(identities["client_operation_id"]),
        str(identities["document_event_sequence"]),
        str(identities["crdt_generation"]),
        str(identities["crdt_snapshot_id"]),
        str(identities["history_checkpoint_id"]),
        str(identities["user_revision"]),
        str(identities["publication_revision_id"]),
    }) == 7
    restarted = CollaborativeEditingAuthority.open(
        catalog, root, InMemoryIdentityDirectory(SUBJECTS)
    )
    assert restarted.create_revision_checkpoint(
        "content-editor",
        "page-e1",
        expected_revision=revision,
        expected_document_sequence=acknowledgement.document_sequence,
        expected_workspace_epoch=workspace_epoch,
        expected_document_epoch=document_epoch,
        operation_id="op-e1-history-checkpoint-0001",
        checkpoint_reason="automatic",
        publication_revision_id="publication-revision-e1-0001",
    ) == checkpoint
    con = sqlite3.connect(catalog)
    try:
        assert con.execute(
            "SELECT generation FROM workspace_collaboration_crdt_update_bindings"
        ).fetchone() == (1,)
        assert con.execute(
            "SELECT generation FROM workspace_collaboration_crdt_snapshot_bindings"
        ).fetchone() == (1,)
    finally:
        con.close()


@pytest.mark.integration
def test_e1_p1_replay_rebinds_committed_update_after_page_grant_epoch_change(
    tmp_path: Path,
) -> None:
    authority, transport, _root, _catalog, workspace_id = _environment(tmp_path)
    gateway = RuntimeGateway(
        transport=transport,
        aggregate_enabled=True,
        realtime_enabled=True,
    )
    gateway.start(compactor_handshake_matches=True)

    owner_binding = session_binding_sha256("e1-owner-before-share-session")
    owner_ticket = transport.issue_ticket(
        csrf_valid=True,
        subject_id="owner",
        workspace_id=workspace_id,
        document_id="page-e1",
        client_id="owner-before-share",
        session_binding_sha256=owner_binding,
        origin=ORIGIN,
        capabilities=("read", "write"),
        now_ms=1_000,
    )
    owner_session = gateway.connect(
        owner_ticket.token,
        origin=ORIGIN,
        subject_id="owner",
        workspace_id=workspace_id,
        page_id="page-e1",
        client_id="owner-before-share",
        session_binding_sha256=owner_binding,
        now_ms=1_001,
    )
    committed = gateway.submit_payload(
        owner_session.connection_id,
        operation_id="op-e1-owner-before-share-update-0001",
        client_sequence=1,
        acknowledgement_sequence=0,
        payload=b"owner-update-before-direct-share",
    )

    page = authority.read_document("owner", "page-e1")
    authority.set_page_grant(
        "owner",
        "page-e1",
        "subject",
        "content-editor",
        role="comment",
        inherit_to_descendants=False,
        expected_workspace_epoch=int(page["workspace_epoch"]),
        expected_document_epoch=int(page["document_epoch"]),
        operation_id="op-e1-replay-role-comment-0001",
    )
    page = authority.read_document("owner", "page-e1")
    authority.set_page_grant(
        "owner",
        "page-e1",
        "subject",
        "content-editor",
        role="edit_content",
        inherit_to_descendants=False,
        expected_workspace_epoch=int(page["workspace_epoch"]),
        expected_document_epoch=int(page["document_epoch"]),
        operation_id="op-e1-replay-role-edit-content-0001",
    )

    reader_binding = session_binding_sha256("e1-reader-after-share-session")
    reader_ticket = transport.issue_ticket(
        csrf_valid=True,
        subject_id="content-editor",
        workspace_id=workspace_id,
        document_id="page-e1",
        client_id="reader-after-share",
        session_binding_sha256=reader_binding,
        origin=ORIGIN,
        capabilities=("read", "write"),
        now_ms=2_000,
    )
    reader_session = gateway.connect(
        reader_ticket.token,
        origin=ORIGIN,
        subject_id="content-editor",
        workspace_id=workspace_id,
        page_id="page-e1",
        client_id="reader-after-share",
        session_binding_sha256=reader_binding,
        now_ms=2_001,
    )
    replay = gateway.replay_to_queue(
        reader_session.connection_id,
        after_document_sequence=0,
        limit=100,
    )
    frames = gateway.drain(reader_session.connection_id, max_frames=100)

    assert replay.event_frames_enqueued == 1
    assert len(frames) == 1
    assert frames[0].payload == b"owner-update-before-direct-share"
    assert frames[0].document_sequence == committed.acknowledgement.document_sequence
    assert frames[0].workspace_epoch == reader_ticket.claims.workspace_epoch
    assert frames[0].page_epoch == reader_ticket.claims.page_epoch
    assert gateway.session_status(reader_session.connection_id)["active"] is True
    gateway.stop()


@pytest.mark.integration
def test_e1_p1_http_command_and_checkpoint_routes_use_2405_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    authority, _transport, root, catalog, workspace_id = _environment(tmp_path)
    identities = InMemoryIdentityDirectory(SUBJECTS)
    store = CollaborativeWorkspaceStore(
        catalog, user_data_root=root, identity_directory=identities
    )
    actor = {"id": "content-editor"}
    monkeypatch.setattr(routes_notion_workspace, "_store", lambda: store)
    monkeypatch.setattr(
        routes_notion_workspace, "_collaborative_runtime_enabled", lambda: True
    )
    monkeypatch.setattr(
        routes_notion_workspace, "current_user_id", lambda: actor["id"]
    )
    app = Flask("e1-editing-routes")
    app.config.update(TESTING=True, SECRET_KEY="e1-editing-routes")
    routes_notion_workspace.register_notion_workspace_routes(app)
    client = app.test_client()
    workspace_epoch, document_epoch, revision, sequence = _epochs(
        authority, actor["id"]
    )
    base = f"/api/notion/workspaces/{workspace_id}/pages/page-e1"
    command = client.post(
        base + "/editing/commands",
        json={
            "command_kind": "block.insert",
            "target_id": "block-route",
            "payload": {
                "type": "paragraph",
                "content": "route",
                "after_block_id": "block-b",
            },
            "expected_document_sequence": sequence,
            "expected_workspace_epoch": workspace_epoch,
            "expected_document_epoch": document_epoch,
            "operation_id": "op-e1-route-command-0001",
        },
    )
    assert command.status_code == 201, command.get_json()
    command_body = command.get_json()
    denied = client.post(
        base + "/editing/commands",
        json={
            "command_kind": "database.property.rename",
            "target_id": "property-e1",
            "payload": {"name": "Denied"},
            "expected_document_sequence": command_body["document_sequence"],
            "expected_workspace_epoch": workspace_epoch,
            "expected_document_epoch": document_epoch,
            "expected_target_version": 1,
            "operation_id": "op-e1-route-schema-denied-0001",
        },
    )
    assert denied.status_code == 404
    checkpoint = client.post(
        base + "/history/checkpoints",
        json={
            "expected_revision": revision,
            "expected_document_sequence": command_body["document_sequence"],
            "expected_workspace_epoch": workspace_epoch,
            "expected_document_epoch": document_epoch,
            "checkpoint_reason": "manual",
            "operation_id": "op-e1-route-checkpoint-0001",
        },
    )
    assert checkpoint.status_code == 201, checkpoint.get_json()
    history = client.get(base + "/history/checkpoints")
    assert history.status_code == 200
    history_body = history.get_json()
    assert history_body["count"] == 2
    assert history_body["items"][0]["revision"] == revision + 1


@pytest.mark.integration
def test_e1_p1_real_gateway_node_compactor_restart_and_checkpoint(
    tmp_path: Path,
) -> None:
    authority, transport, root, catalog, workspace_id = _environment(tmp_path)
    runtime = CrossProcessGatewayRuntime(
        tmp_path / "e1-selected-runtime",
        allowed_parent=tmp_path,
        catalog_path=catalog,
        user_data_root=root,
        max_queue_frames=16,
        timeout_seconds=30,
    )
    socket_client = None
    try:
        runtime.provision()
        assert runtime.node_executable is not None and runtime.node_runtime is not None
        script = """
import * as Y from 'yjs';
const document = new Y.Doc({gc:false});
let update = null;
document.on('update', value => { update = Buffer.from(value).toString('base64'); });
document.getText('block:block-a').insert(0, '동시 편집 ');
process.stdout.write(JSON.stringify(update));
"""
        generated = subprocess.run(
            [str(runtime.node_executable), "--input-type=module", "-e", script],
            cwd=runtime.node_runtime,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="strict",
            shell=False,
            check=True,
            timeout=10,
        )
        update = base64.b64decode(json.loads(generated.stdout), validate=True)
        first_runtime = runtime.start()
        assert first_runtime["gateway_pid"] != first_runtime["compactor_pid"]
        now_ms = int(time.time() * 1_000)
        binding = session_binding_sha256("e1-cross-process-writer")
        issued = transport.issue_ticket(
            csrf_valid=True,
            subject_id="content-editor",
            workspace_id=workspace_id,
            document_id="page-e1",
            client_id="e1-writer",
            session_binding_sha256=binding,
            origin=runtime.origin,
            capabilities=("read", "write"),
            now_ms=now_ms,
        )
        _workspace_epoch, _document_epoch, revision, sequence = _epochs(
            authority, "content-editor"
        )
        assert runtime.python_packages is not None
        client_script = r"""
import json,sys
sys.path.insert(0,sys.argv[1])
from websockets.sync.client import connect
request=json.loads(sys.stdin.read())
socket_client=connect(request['url'],origin=request['origin'],subprotocols=['code-assist-collaboration-v1'],open_timeout=5,close_timeout=2)
socket_client.send(json.dumps(request['connect'],separators=(',',':')))
connected=json.loads(socket_client.recv(timeout=5))
socket_client.send(json.dumps(request['submit'],separators=(',',':')))
acknowledgement=json.loads(socket_client.recv(timeout=5))
socket_client.close()
print(json.dumps({'connected':connected,'acknowledgement':acknowledgement},separators=(',',':')))
"""
        base_executable = Path(
            getattr(sys, "_base_executable", sys.executable)
        ).resolve(strict=True)
        client_request = {
            "url": runtime.websocket_url,
            "origin": runtime.origin,
            "connect": {
                "op": "connect",
                "token": issued.token,
                "subject_id": "content-editor",
                "workspace_id": workspace_id,
                "page_id": "page-e1",
                "client_id": "e1-writer",
                "session_binding_sha256": binding,
            },
            "submit": {
                "op": "submit",
                "operation_id": "op-e1-cross-update-0001",
                "client_sequence": 1,
                "acknowledgement_sequence": sequence,
                "payload_base64": base64.b64encode(update).decode("ascii"),
            },
        }
        client_result = subprocess.run(
            [
                str(base_executable),
                "-E",
                "-s",
                "-c",
                client_script,
                str(runtime.python_packages),
            ],
            input=json.dumps(client_request, separators=(",", ":")),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="strict",
            shell=False,
            check=True,
            timeout=15,
        )
        observed = json.loads(client_result.stdout)
        assert observed["connected"]["type"] == "connected"
        acknowledgement = observed["acknowledgement"]
        assert acknowledgement["type"] == "ack"
        document_sequence = int(
            acknowledgement["acknowledgement"]["document_sequence"]
        )
        compactor_binding = session_binding_sha256("e1-cross-process-compactor")
        compactor_ticket = transport.issue_ticket(
            csrf_valid=True,
            subject_id="content-editor",
            workspace_id=workspace_id,
            document_id="page-e1",
            client_id="e1-compactor",
            session_binding_sha256=compactor_binding,
            origin=runtime.origin,
            capabilities=("read", "write"),
            now_ms=now_ms + 100,
        )
        grant = transport.consume_ticket(
            compactor_ticket.token,
            origin=runtime.origin,
            subject_id="content-editor",
            workspace_id=workspace_id,
            document_id="page-e1",
            client_id="e1-compactor",
            session_binding_sha256=compactor_binding,
            now_ms=now_ms + 101,
        )
        work = transport.prepare_compaction(grant)
        with NodeCompactor(
            node_executable=runtime.node_executable,
            entrypoint=runtime.node_runtime / "compactor.mjs",
            runtime_root=runtime.node_runtime,
        ) as compactor:
            response, snapshot, state_vector = compactor.compact(
                request=work.request,
                base_snapshot=work.base_snapshot,
                ordered_updates=work.ordered_updates,
            )
            compactor_pid = compactor.pid
        assert compactor_pid not in {first_runtime["gateway_pid"], first_runtime["compactor_pid"]}
        transport.commit_compaction(
            grant,
            operation_id="op-e1-cross-compaction-0001",
            work=work,
            response=response,
            snapshot=snapshot,
            state_vector=state_vector,
        )
        workspace_epoch, document_epoch, current_revision, current_sequence = _epochs(
            authority, "content-editor"
        )
        assert current_revision == revision and current_sequence == document_sequence
        checkpoint = authority.create_revision_checkpoint(
            "content-editor",
            "page-e1",
            expected_revision=current_revision,
            expected_document_sequence=current_sequence,
            expected_workspace_epoch=workspace_epoch,
            expected_document_epoch=document_epoch,
            operation_id="op-e1-cross-checkpoint-0001",
            checkpoint_reason="automatic",
        )
        second_runtime = runtime.restart()
        assert second_runtime["gateway_pid"] != first_runtime["gateway_pid"]
        reopened = CollaborativeEditingTransportAuthority.open(catalog, root)
        assert reopened.counts()["workspace_collaboration_crdt_updates"] == 1
        assert checkpoint["through_update_sequence"] == 1
        con = sqlite3.connect(catalog)
        try:
            assert con.execute(
                "SELECT COUNT(*) FROM workspace_collaboration_crdt_update_bindings"
            ).fetchone()[0] == 1
            assert con.execute(
                "SELECT COUNT(*) FROM workspace_collaboration_crdt_snapshot_bindings"
            ).fetchone()[0] == 1
        finally:
            con.close()
    finally:
        if socket_client is not None:
            socket_client.close()
        runtime.close()
