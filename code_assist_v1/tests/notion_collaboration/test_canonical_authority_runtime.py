from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from code_assist_v1 import notion_workspace_collaboration_migration as migration
from code_assist_v1 import notion_workspace_store as workspace_store
from code_assist_v1.notion_collaboration.canonical_authority import (
    CanonicalAuthority,
    InjectedFailure,
    NotFoundOrDenied,
    OperationConflict,
    queue_frame,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


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


def _authority(tmp_path: Path) -> tuple[CanonicalAuthority, Path, Path, str]:
    root = tmp_path / "canonical-runtime"
    catalog = Path(workspace_store.catalog_path_for_user_data_root(root))
    store = workspace_store.WorkspaceStore(catalog)
    store.bootstrap()
    created = store.create_workspace(
        "owner", name="Runtime", operation_id="op-runtime-workspace-create-0001"
    )
    workspace_id = str(created["workspace"]["id"])
    store.set_member(
        "owner",
        workspace_id,
        "member",
        role="member",
        base_version=int(created["workspace"]["version"]),
        operation_id="op-runtime-member-set-0001",
    )
    backup = _backup(catalog, root / "backups" / "before-collaboration.db")
    plan = migration.build_plan(catalog, root)
    migration.apply_plan(
        catalog,
        root,
        exact_plan_checksum=plan["plan_checksum"],
        backup_confirmed=True,
        catalog_quiesced_confirmed=True,
        backup_path=backup,
    )
    return CanonicalAuthority.open(catalog, root), root, catalog, workspace_id


def _create(authority: CanonicalAuthority, workspace_id: str) -> dict:
    return authority.create_document(
        "owner",
        workspace_id,
        "page-runtime",
        title="Canonical",
        blocks=[{"id": "block-runtime", "type": "text", "content": "base", "props": {}}],
        expected_workspace_epoch=2,
        operation_id="op-runtime-document-create-0001",
    )


@pytest.mark.integration
def test_a0b_p1_one_transaction_owns_content_revision_event_audit_and_outbox(
    tmp_path: Path,
) -> None:
    authority, _, _, workspace_id = _authority(tmp_path)
    created = _create(authority, workspace_id)
    assert created["revision"] == created["document_sequence"] == 1
    before = authority.counts()
    saved = authority.save_document(
        "member",
        "page-runtime",
        title="Canonical saved",
        blocks=[{"id": "block-runtime", "type": "text", "content": "saved", "props": {"bold": True}}],
        expected_revision=1,
        expected_workspace_epoch=2,
        expected_document_epoch=1,
        operation_id="op-runtime-document-save-0001",
    )
    replay = authority.save_document(
        "member",
        "page-runtime",
        title="Canonical saved",
        blocks=[{"id": "block-runtime", "type": "text", "content": "saved", "props": {"bold": True}}],
        expected_revision=1,
        expected_workspace_epoch=2,
        expected_document_epoch=1,
        operation_id="op-runtime-document-save-0001",
    )
    assert replay == saved
    after = authority.counts()
    for table in (
        "workspace_collaboration_revisions",
        "workspace_collaboration_operations",
        "workspace_collaboration_events",
        "workspace_collaboration_audit_events",
        "workspace_collaboration_outbox",
    ):
        assert after[table] == before[table] + 1
    page = authority.read_document(
        "member",
        "page-runtime",
        expected_workspace_epoch=2,
        expected_document_epoch=1,
    )
    assert page["revision"] == page["document_sequence"] == 2
    assert page["blocks"][0]["content"] == "saved"
    assert page["content_sha256"] == saved["content_sha256"]
    for table in (
        "workspace_collaboration_events",
        "workspace_collaboration_audit_events",
        "workspace_collaboration_outbox",
    ):
        assert len(authority.rows(table, operation_id="op-runtime-document-save-0001")) == 1
    with pytest.raises(OperationConflict):
        authority.save_document(
            "member",
            "page-runtime",
            title="different",
            blocks=[],
            expected_revision=1,
            expected_workspace_epoch=2,
            expected_document_epoch=1,
            operation_id="op-runtime-document-save-0001",
        )


@pytest.mark.integration
@pytest.mark.parametrize(
    "fault", ["after_domain", "after_event", "after_audit", "after_outbox", "after_operation"]
)
def test_a0b_p1_every_transaction_fault_rolls_back_all_effects(
    tmp_path: Path, fault: str
) -> None:
    authority, _, _, workspace_id = _authority(tmp_path)
    _create(authority, workspace_id)
    before_counts = authority.counts()
    before_page = authority.read_document("owner", "page-runtime")
    with pytest.raises(InjectedFailure, match=fault):
        authority.save_document(
            "owner",
            "page-runtime",
            title=fault,
            blocks=[{"id": "block-runtime", "type": "text", "content": fault}],
            expected_revision=1,
            expected_workspace_epoch=2,
            expected_document_epoch=1,
            operation_id=f"op-runtime-fault-{fault}-0001",
            fault=fault,
        )
    assert authority.counts() == before_counts
    assert authority.read_document("owner", "page-runtime") == before_page
    assert authority.integrity()["quick_check"] == "ok"


@pytest.mark.integration
def test_a0b_p1_page_guest_revoke_fences_old_frame_and_all_derived_access(
    tmp_path: Path,
) -> None:
    authority, _, _, workspace_id = _authority(tmp_path)
    _create(authority, workspace_id)
    granted = authority.grant_page_access(
        "owner",
        "page-runtime",
        "guest",
        "comment",
        expected_workspace_epoch=2,
        expected_document_epoch=1,
        operation_id="op-runtime-guest-grant-0001",
    )
    assert granted["document_epoch"] == 2
    assert authority.read_document("guest", "page-runtime")["role"] == "comment"
    frame = queue_frame(
        authority, "guest", "page-runtime", {"protected": "must-not-leak"}
    )
    assert authority.authorize_frame_send(frame).delivered is True
    revoked = authority.revoke_subject(
        "owner",
        workspace_id,
        "guest",
        expected_workspace_epoch=2,
        operation_id="op-runtime-guest-revoke-0001",
    )
    assert revoked["workspace_epoch"] == 3
    assert revoked["affected_document_epochs"] == {"page-runtime": 3}
    assert authority.authorize_frame_send(frame).descriptor() == {
        "delivered": False,
        "reason": "denied_or_authority_unavailable",
        "payload": None,
        "document_sequence": None,
    }
    with pytest.raises(NotFoundOrDenied):
        authority.read_document("guest", "page-runtime")
    with pytest.raises(NotFoundOrDenied) as absent:
        authority.read_document("guest", "missing-page")
    assert str(absent.value) == "not found or permission denied"


def _child(command: dict) -> subprocess.Popen[str]:
    script = (
        "import json,sys;"
        "from code_assist_v1.notion_collaboration.canonical_authority "
        "import execute_command_document;"
        "print(json.dumps(execute_command_document(json.load(sys.stdin)),sort_keys=True))"
    )
    return subprocess.Popen(
        [sys.executable, "-c", script],
        cwd=REPOSITORY_ROOT,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


@pytest.mark.integration
def test_a0b_p1_real_process_restart_and_concurrent_response_loss_replay_are_exactly_once(
    tmp_path: Path,
) -> None:
    authority, root, catalog, workspace_id = _authority(tmp_path)
    _create(authority, workspace_id)
    command = {
        "catalog_path": str(catalog),
        "user_data_root": str(root),
        "command": "save_document",
        "arguments": {
            "actor_subject_id": "member",
            "document_id": "page-runtime",
            "title": "Cross process",
            "blocks": [{"id": "block-runtime", "type": "text", "content": "once"}],
            "expected_revision": 1,
            "expected_workspace_epoch": 2,
            "expected_document_epoch": 1,
            "operation_id": "op-runtime-cross-process-save-0001",
        },
    }
    first = _child(command)
    second = _child(command)
    first_out, first_err = first.communicate(json.dumps(command), timeout=15)
    second_out, second_err = second.communicate(json.dumps(command), timeout=15)
    assert (first.returncode, second.returncode, first_err, second_err) == (0, 0, "", "")
    assert json.loads(first_out) == json.loads(second_out)
    restarted = CanonicalAuthority.open(catalog, root)
    assert restarted.read_document("member", "page-runtime")["revision"] == 2
    assert len(
        restarted.rows(
            "workspace_collaboration_events",
            operation_id="op-runtime-cross-process-save-0001",
        )
    ) == 1
    assert restarted.integrity()["foreign_key_error_count"] == 0
