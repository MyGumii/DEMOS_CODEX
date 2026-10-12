from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import time

import pytest

from code_assist_v1.notion_collaboration.canonical_authority import (
    InjectedFailure,
    NotFoundOrDenied,
)
from code_assist_v1.notion_collaboration.durable_transport import YJS_UPDATE_ENCODING
from code_assist_v1.notion_collaboration.cross_process_gateway import (
    CrossProcessGatewayRuntime,
)
from code_assist_v1.notion_collaboration.editing_authority import (
    CollaborativeEditingAuthority,
    CollaborativeEditingTransportAuthority,
    DocumentSequenceConflict,
    TargetVersionConflict,
)
from code_assist_v1.notion_collaboration.node_compactor import NodeCompactor
from code_assist_v1.notion_collaboration.protocol_contract import (
    ProtocolEnvelope,
    session_binding_sha256,
)
from code_assist_v1.tests.notion_collaboration.test_collaborative_editing_authority import (
    ORIGIN,
    _environment,
    _epochs,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
REPOSITORY_ROOT = PROJECT_ROOT.parent
NODE_CORPUS = Path(__file__).with_name("e1_p3_host_editor_corpus.js")


def _node_json(*arguments: str, stdin: dict[str, object] | None = None) -> dict[str, object]:
    executable = shutil.which("node")
    assert executable is not None
    run = subprocess.run(
        [executable, "--unhandled-rejections=strict", str(NODE_CORPUS), *arguments],
        cwd=REPOSITORY_ROOT,
        input=None if stdin is None else json.dumps(stdin, separators=(",", ":")),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="strict",
        shell=False,
        check=False,
        timeout=30,
    )
    assert run.returncode == 0, run.stdout + run.stderr
    value = json.loads(run.stdout)
    assert isinstance(value, dict)
    return value


def _transport_grant(
    transport: CollaborativeEditingTransportAuthority,
    workspace_id: str,
    *,
    client_id: str,
):
    now_ms = int(time.time() * 1_000)
    binding = session_binding_sha256(f"e1-p3:{client_id}")
    issued = transport.issue_ticket(
        csrf_valid=True,
        subject_id="content-editor",
        workspace_id=workspace_id,
        document_id="page-e1",
        client_id=client_id,
        session_binding_sha256=binding,
        origin=ORIGIN,
        capabilities=("read", "write"),
        now_ms=now_ms,
    )
    return transport.consume_ticket(
        issued.token,
        origin=ORIGIN,
        subject_id="content-editor",
        workspace_id=workspace_id,
        document_id="page-e1",
        client_id=client_id,
        session_binding_sha256=binding,
        now_ms=now_ms + 1,
    )


def _submit_updates(
    transport: CollaborativeEditingTransportAuthority,
    grant,
    payloads: list[bytes],
    *,
    initial_document_sequence: int,
) -> tuple[list[ProtocolEnvelope], list[object]]:
    envelopes: list[ProtocolEnvelope] = []
    acknowledgements: list[object] = []
    acknowledgement_sequence = initial_document_sequence
    for index, payload in enumerate(payloads, start=1):
        envelope = ProtocolEnvelope.create(
            grant,
            message_type="crdt.update",
            operation_id=f"op-e1-p3-update-{index:04d}",
            client_sequence=index,
            acknowledgement_sequence=acknowledgement_sequence,
            payload=payload,
            capabilities=("write",),
            payload_encoding=YJS_UPDATE_ENCODING,
        )
        acknowledgement = transport.accept_update(envelope, grant)
        envelopes.append(envelope)
        acknowledgements.append(acknowledgement)
        acknowledgement_sequence = acknowledgement.document_sequence
    return envelopes, acknowledgements


def _canonical_structure_hash(catalog: Path) -> str:
    con = sqlite3.connect(catalog)
    con.row_factory = sqlite3.Row
    try:
        value = {
            "blocks": [
                dict(row)
                for row in con.execute(
                    "SELECT id,position,block_type,content,props_json,row_version "
                    "FROM workspace_collaboration_blocks WHERE document_id='page-e1' "
                    "ORDER BY position,id"
                ).fetchall()
            ],
            "properties": [
                dict(row)
                for row in con.execute(
                    "SELECT property.id,property.name,property.property_type,property.row_version "
                    "FROM workspace_collaboration_database_properties property "
                    "JOIN workspace_collaboration_databases database ON database.id=property.database_id "
                    "WHERE database.document_id='page-e1' ORDER BY property.position,property.id"
                ).fetchall()
            ],
            "cells": [
                dict(row)
                for row in con.execute(
                    "SELECT cell.row_id,cell.property_id,cell.value_json,cell.row_version "
                    "FROM workspace_collaboration_database_cells cell "
                    "JOIN workspace_collaboration_database_rows row_item ON row_item.id=cell.row_id "
                    "JOIN workspace_collaboration_databases database ON database.id=row_item.database_id "
                    "WHERE database.document_id='page-e1' ORDER BY cell.row_id,cell.property_id"
                ).fetchall()
            ],
            "tombstones": [
                dict(row)
                for row in con.execute(
                    "SELECT block_id,crdt_generation,through_update_sequence,recovery_json "
                    "FROM workspace_collaboration_block_tombstones WHERE document_id='page-e1' "
                    "ORDER BY block_id"
                ).fetchall()
            ],
        }
    finally:
        con.close()
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return "sha256:" + hashlib.sha256(raw).hexdigest()


@pytest.mark.integration
def test_e1_p3_host_editor_schedule_corpus_has_zero_loss_and_one_hash() -> None:
    receipt = _node_json()
    repeated = _node_json()
    assert receipt["status"] == "pass"
    assert receipt["scenario_count"] == 4
    assert receipt["schedule_count_per_scenario"] == 4
    assert receipt["acknowledged_character_loss_count"] == 0
    assert receipt["duplicate_acknowledged_fragment_count"] == 0
    assert receipt["convergence_mismatch_count"] == 0
    assert receipt["different_block_edits_survive"] is True
    assert receipt["same_block_character_loss_zero"] is True
    assert receipt["local_undo_preserves_remote"] is True
    assert receipt["typed_structure_commands_observed"] is True
    assert repeated["canonical_corpus_sha256"] == receipt["canonical_corpus_sha256"]
    assert repeated["scenarios"] == receipt["scenarios"]
    assert receipt["product_complete"] is False


@pytest.mark.integration
def test_e1_p3_server_compacted_snapshot_matches_every_client_canonical_hash(
    tmp_path: Path,
) -> None:
    authority, transport, root, _catalog, workspace_id = _environment(tmp_path)
    fixture = _node_json("--transport-fixture")
    payloads = [
        base64.b64decode(str(value), validate=True)
        for value in fixture["update_base64"]  # type: ignore[index]
    ]
    grant = _transport_grant(transport, workspace_id, client_id="e1-p3-compaction")
    sequence = _epochs(authority, "content-editor")[3]
    envelopes, acknowledgements = _submit_updates(
        transport,
        grant,
        payloads,
        initial_document_sequence=sequence,
    )
    replayed = transport.accept_update(envelopes[-1], grant)
    assert replayed == acknowledgements[-1]
    work = transport.prepare_compaction(grant)
    runtime = CrossProcessGatewayRuntime(
        tmp_path / "e1-p3-selected-runtime",
        allowed_parent=tmp_path,
        catalog_path=_catalog,
        user_data_root=root,
    )
    try:
        runtime.provision()
        assert runtime.node_executable is not None and runtime.node_runtime is not None
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
    finally:
        runtime.close()
    committed = transport.commit_compaction(
        grant,
        operation_id="op-e1-p3-compaction-0001",
        work=work,
        response=response,
        snapshot=snapshot,
        state_vector=state_vector,
    )
    projected = _node_json(
        "--project-snapshot",
        stdin={
            "baseline": fixture["baseline"],
            "snapshot_base64": base64.b64encode(snapshot).decode("ascii"),
        },
    )
    assert projected["canonical_projection_sha256"] == fixture["canonical_projection_sha256"]
    text = "\n".join(
        str(item.get("content", ""))
        for item in projected["projection"]["blocks"]  # type: ignore[index,union-attr]
    )
    assert "A-왼쪽" in text and "B-오른쪽" in text
    assert committed["through_update_sequence"] == len(payloads)
    reopened = CollaborativeEditingTransportAuthority.open(_catalog, root)
    counts = reopened.counts()
    assert counts["workspace_collaboration_crdt_updates"] == len(payloads)
    assert counts["workspace_collaboration_crdt_snapshots"] == 1


@pytest.mark.integration
def test_e1_p3_structure_database_conflicts_faults_and_restart_are_deterministic(
    tmp_path: Path,
) -> None:
    authority, transport, root, catalog, workspace_id = _environment(tmp_path)
    fixture = _node_json("--transport-fixture")
    grant = _transport_grant(transport, workspace_id, client_id="e1-p3-delete-recovery")
    sequence = _epochs(authority, "content-editor")[3]
    _envelopes, acknowledgements = _submit_updates(
        transport,
        grant,
        [base64.b64decode(str(fixture["update_base64"][0]), validate=True)],  # type: ignore[index]
        initial_document_sequence=sequence,
    )
    workspace_epoch, document_epoch, _revision, current_sequence = _epochs(
        authority, "content-editor"
    )
    deleted = authority.execute_typed_command(
        "content-editor",
        "page-e1",
        command_kind="block.delete",
        target_id="block-b",
        payload={},
        expected_document_sequence=current_sequence,
        expected_workspace_epoch=workspace_epoch,
        expected_document_epoch=document_epoch,
        expected_target_version=1,
        operation_id="op-e1-p3-delete-0001",
    )
    assert deleted["tombstoned"] is True
    assert deleted["recovery_through_update_sequence"] == 1
    con = sqlite3.connect(catalog)
    try:
        recovery = json.loads(
            con.execute(
                "SELECT recovery_json FROM workspace_collaboration_block_tombstones "
                "WHERE block_id='block-b'"
            ).fetchone()[0]
        )
    finally:
        con.close()
    assert recovery["rich_text_recovery"] == {
        "crdt_generation": 1,
        "node_materialization_required": True,
        "through_update_sequence": 1,
    }
    assert acknowledgements[0].document_sequence < deleted["document_sequence"]

    with pytest.raises(DocumentSequenceConflict):
        authority.execute_typed_command(
            "content-editor",
            "page-e1",
            command_kind="block.move",
            target_id="block-b",
            payload={"after_block_id": "block-a"},
            expected_document_sequence=current_sequence,
            expected_workspace_epoch=workspace_epoch,
            expected_document_epoch=document_epoch,
            expected_target_version=1,
            operation_id="op-e1-p3-stale-move-0001",
        )

    workspace_epoch, document_epoch, _revision, current_sequence = _epochs(
        authority, "content-editor"
    )
    first_cell = authority.execute_typed_command(
        "content-editor",
        "page-e1",
        command_kind="database.cell.set",
        target_id="row-e1:property-e1",
        payload={"row_id": "row-e1", "property_id": "property-e1", "value": "first"},
        expected_document_sequence=current_sequence,
        expected_workspace_epoch=workspace_epoch,
        expected_document_epoch=document_epoch,
        expected_target_version=1,
        operation_id="op-e1-p3-cell-first-0001",
    )
    with pytest.raises(DocumentSequenceConflict):
        authority.execute_typed_command(
            "schema-editor",
            "page-e1",
            command_kind="database.cell.set",
            target_id="row-e1:property-e1",
            payload={"row_id": "row-e1", "property_id": "property-e1", "value": "stale"},
            expected_document_sequence=current_sequence,
            expected_workspace_epoch=workspace_epoch,
            expected_document_epoch=document_epoch,
            expected_target_version=1,
            operation_id="op-e1-p3-cell-stale-sequence-0001",
        )
    with pytest.raises(TargetVersionConflict):
        authority.execute_typed_command(
            "schema-editor",
            "page-e1",
            command_kind="database.cell.set",
            target_id="row-e1:property-e1",
            payload={"row_id": "row-e1", "property_id": "property-e1", "value": "stale"},
            expected_document_sequence=first_cell["document_sequence"],
            expected_workspace_epoch=workspace_epoch,
            expected_document_epoch=document_epoch,
            expected_target_version=1,
            operation_id="op-e1-p3-cell-stale-version-0001",
        )
    with pytest.raises(NotFoundOrDenied):
        authority.execute_typed_command(
            "content-editor",
            "page-e1",
            command_kind="database.property.rename",
            target_id="property-e1",
            payload={"name": "Denied"},
            expected_document_sequence=first_cell["document_sequence"],
            expected_workspace_epoch=workspace_epoch,
            expected_document_epoch=document_epoch,
            expected_target_version=1,
            operation_id="op-e1-p3-schema-denied-0001",
        )

    before_fault_hash = _canonical_structure_hash(catalog)
    with pytest.raises(InjectedFailure, match="after_outbox"):
        authority.execute_typed_command(
            "schema-editor",
            "page-e1",
            command_kind="database.property.rename",
            target_id="property-e1",
            payload={"name": "Never committed"},
            expected_document_sequence=first_cell["document_sequence"],
            expected_workspace_epoch=workspace_epoch,
            expected_document_epoch=document_epoch,
            expected_target_version=1,
            operation_id="op-e1-p3-schema-fault-0001",
            fault="after_outbox",
        )
    assert _canonical_structure_hash(catalog) == before_fault_hash
    restarted = CollaborativeEditingAuthority.open(
        catalog, root, authority.identity_directory
    )
    assert _canonical_structure_hash(catalog) == before_fault_hash
    assert _epochs(restarted, "content-editor")[3] == first_cell["document_sequence"]
