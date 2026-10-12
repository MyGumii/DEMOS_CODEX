from __future__ import annotations

import hashlib
from pathlib import Path
import sqlite3

import pytest

from code_assist_v1 import notion_workspace_collaboration_migration as canonical_migration
from code_assist_v1 import notion_workspace_editing_authority_migration as editing_migration
from code_assist_v1 import notion_workspace_iam_migration as iam_migration
from code_assist_v1 import notion_workspace_live_collaboration_migration as live_migration
from code_assist_v1 import notion_workspace_realtime_transport_migration as transport_migration
from code_assist_v1 import notion_workspace_store as workspace_store
from code_assist_v1.knowledge_core.services.publication import (
    _validate_notion_snapshot,
)
from code_assist_v1.notion_collaboration.iam_authority import (
    InMemoryIdentityDirectory,
)
from code_assist_v1.notion_collaboration.live_authority import (
    CollaborativeLiveAuthority,
)
from code_assist_v1.notion_collaboration.second_brain_boundary import (
    BOUNDARY_CONTRACT,
    CollaborativeNotionSnapshotProvider,
    PublicationSourceConflict,
    PublicationSourceNotCheckpointed,
    PublicationSourceUnsupported,
    canonical_publication_request_sha256,
    render_committed_revision_markdown,
)


SUBJECTS = ("owner", "editor", "outsider")


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
    root = tmp_path / "s0a-boundary"
    catalog = Path(workspace_store.catalog_path_for_user_data_root(root))
    store = workspace_store.WorkspaceStore(catalog)
    store.bootstrap()
    created = store.create_workspace(
        "owner", name="S0A workspace", operation_id="op-s0a-workspace-create-0001"
    )
    workspace_id = str(created["workspace"]["id"])
    membership = store.set_member(
        "owner",
        workspace_id,
        "editor",
        role="member",
        base_version=int(created["workspace"]["version"]),
        operation_id="op-s0a-member-set-0001",
    )
    assert membership["workspace"]["id"] == workspace_id
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
        "page-s0a",
        title="Publication boundary",
        blocks=[
            {"id": "heading", "type": "heading_1", "content": "Contract"},
            {"id": "body", "type": "paragraph", "content": "Timestamp-free body"},
        ],
        expected_workspace_epoch=workspace_epoch,
        operation_id="op-s0a-page-create-0001",
    )
    page = live.read_document("owner", "page-s0a")
    live.set_page_grant(
        "owner",
        "page-s0a",
        "subject",
        "editor",
        role="edit_content",
        inherit_to_descendants=False,
        expected_workspace_epoch=int(page["workspace_epoch"]),
        expected_document_epoch=int(page["document_epoch"]),
        operation_id="op-s0a-page-grant-0001",
    )
    _apply(editing_migration, catalog, root, "before-2405.db")
    return (
        CollaborativeNotionSnapshotProvider.open(catalog, root, identities),
        catalog,
        workspace_id,
    )


@pytest.mark.integration
def test_s0a_p1_exact_checkpoint_snapshot_is_sbe_compatible_and_timestamp_free(
    tmp_path: Path,
) -> None:
    provider, catalog, workspace_id = _environment(tmp_path)

    first = provider.capture_bound(actor_user_id="editor", page_id="page-s0a")
    validated = _validate_notion_snapshot(
        first.as_sbe_snapshot(), actor_user_id="editor", requested_page_id="page-s0a"
    )
    con = sqlite3.connect(catalog)
    try:
        con.execute(
            "UPDATE workspace_collaboration_documents SET updated_at=updated_at+999 "
            "WHERE id='page-s0a'"
        )
        con.commit()
    finally:
        con.close()
    second = provider.capture_bound(actor_user_id="editor", page_id="page-s0a")

    assert first == second
    assert first.workspace_id == workspace_id
    assert first.descriptor()["contract"] == BOUNDARY_CONTRACT
    assert first.descriptor()["timestamp_fields_in_publication_identity"] == []
    assert first.markdown == (
        "# Publication boundary\n\n## Contract\n\nTimestamp-free body\n"
    )
    assert first.content_sha256 == hashlib.sha256(first.markdown.encode()).hexdigest()
    assert first.canonical_content_sha256.startswith("sha256:")
    assert first.source_fence_sha256.startswith("sha256:")
    assert validated.updated_at is None
    assert validated.source_snapshot_sha256 == first.source_snapshot_sha256


@pytest.mark.integration
def test_s0a_p1_uncheckpointed_edit_is_rejected_then_new_checkpoint_is_stable(
    tmp_path: Path,
) -> None:
    provider, _catalog, _workspace_id = _environment(tmp_path)
    page = provider.read_document("editor", "page-s0a")
    provider.execute_typed_command(
        "editor",
        "page-s0a",
        command_kind="block.insert",
        target_id="new-block",
        payload={"type": "paragraph", "content": "New committed content", "after_block_id": "body"},
        expected_document_sequence=int(page["document_sequence"]),
        expected_workspace_epoch=int(page["workspace_epoch"]),
        expected_document_epoch=int(page["document_epoch"]),
        operation_id="op-s0a-block-insert-0001",
    )

    with pytest.raises(PublicationSourceNotCheckpointed):
        provider.capture_bound(actor_user_id="editor", page_id="page-s0a")

    current = provider.read_document("editor", "page-s0a")
    provider.create_revision_checkpoint(
        "editor",
        "page-s0a",
        expected_revision=int(current["revision"]),
        expected_document_sequence=int(current["document_sequence"]),
        expected_workspace_epoch=int(current["workspace_epoch"]),
        expected_document_epoch=int(current["document_epoch"]),
        operation_id="op-s0a-revision-checkpoint-0001",
        checkpoint_reason="manual",
    )
    captured = provider.capture_bound(actor_user_id="editor", page_id="page-s0a")

    assert captured.revision == int(current["revision"]) + 1
    assert "New committed content" in captured.markdown
    provider.revalidate(captured)


@pytest.mark.integration
def test_s0a_p1_permission_epoch_revalidation_fails_closed_after_revoke(
    tmp_path: Path,
) -> None:
    provider, _catalog, _workspace_id = _environment(tmp_path)
    binding = provider.capture_bound(actor_user_id="editor", page_id="page-s0a")
    page = provider.read_document("owner", "page-s0a")

    provider.revoke_page_grant(
        "owner",
        "page-s0a",
        "subject",
        "editor",
        expected_workspace_epoch=int(page["workspace_epoch"]),
        expected_document_epoch=int(page["document_epoch"]),
        operation_id="op-s0a-page-revoke-0001",
    )

    with pytest.raises(PublicationSourceConflict):
        provider.revalidate(binding)


@pytest.mark.integration
def test_s0a_p1_outsider_and_attachment_source_fail_without_publication(
    tmp_path: Path,
) -> None:
    provider, _catalog, _workspace_id = _environment(tmp_path)
    with pytest.raises(PermissionError):
        provider.capture_bound(actor_user_id="outsider", page_id="page-s0a")
    with pytest.raises(PublicationSourceUnsupported):
        render_committed_revision_markdown(
            {
                "title": "Attachment",
                "blocks": [
                    {
                        "id": "file-1",
                        "position": 0,
                        "type": "file",
                        "content": "attachment.pdf",
                        "props": {"file_id": "file-1"},
                    }
                ],
            }
        )


@pytest.mark.unit
def test_s0a_p1_publication_request_hash_binds_operation_source_and_target(
    tmp_path: Path,
) -> None:
    provider, _catalog, _workspace_id = _environment(tmp_path)
    source = provider.capture_bound(actor_user_id="owner", page_id="page-s0a")
    kwargs = {
        "actor_user_id": "owner",
        "actor_scope_epoch": 7,
        "operation_id": "11111111-1111-4111-8111-111111111111",
        "source": source,
        "target_mode": "both",
        "personal_space_id": "22222222-2222-4222-8222-222222222222",
    }

    first = canonical_publication_request_sha256(**kwargs)
    second = canonical_publication_request_sha256(**kwargs)
    changed = canonical_publication_request_sha256(
        **{**kwargs, "target_mode": "personal"}
    )

    assert first == second
    assert first.startswith("sha256:") and len(first) == 71
    assert changed != first
