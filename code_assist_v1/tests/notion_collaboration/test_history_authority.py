from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest
from flask import Flask

from code_assist_v1 import notion_workspace_history_authority_migration as migration
from code_assist_v1 import routes_notion_workspace
from code_assist_v1.notion_collaboration.canonical_authority import NotFoundOrDenied
from code_assist_v1.notion_collaboration.history_authority import (
    CollaborativeHistoryAuthority,
    HistoryIntegrityError,
)
from code_assist_v1.notion_collaboration.iam_authority import InMemoryIdentityDirectory
from code_assist_v1.notion_collaboration.iam_runtime import CollaborativeWorkspaceStore
from code_assist_v1.notion_collaboration.page_settings import update_page_settings
from code_assist_v1.routes_notion_history import register_notion_history_routes
from code_assist_v1.tests.notion_collaboration.test_database_features import SUBJECTS, _apply
from code_assist_v1.tests.notion_collaboration.test_file_authority import (
    _authority as _file_authority,
    _begin,
    _page,
    _stage_all,
)


def _authority(tmp_path: Path) -> tuple[CollaborativeHistoryAuthority, Path, Path]:
    _files, root, catalog = _file_authority(tmp_path)
    _apply(migration, catalog, root, "before-2409.db")
    identities = InMemoryIdentityDirectory((*SUBJECTS, "outsider"))
    return CollaborativeHistoryAuthority.open(catalog, root, identities), root, catalog


def _save_changed(authority: CollaborativeHistoryAuthority) -> dict:
    page = _page(authority)
    return authority.save_document(
        "owner",
        "page-e1",
        title="Changed history title",
        blocks=[
            {
                "id": "block-history-changed",
                "position": 0,
                "type": "paragraph",
                "content": "Changed history body",
                "props": {},
            }
        ],
        expected_revision=int(page["revision"]),
        expected_workspace_epoch=int(page["workspace_epoch"]),
        expected_document_epoch=int(page["document_epoch"]),
        operation_id="op-f0-p3-save-changed-0001",
    )


def test_locked_collaborative_page_rejects_history_restore(tmp_path: Path) -> None:
    authority, _root, catalog = _authority(tmp_path)
    _save_changed(authority)
    page = _page(authority, "schema-editor")
    update_page_settings(
        authority, "schema-editor", page["workspace_id"], "page-e1",
        updates={"locked": True}, expected_document_sequence=page["document_sequence"],
        expected_workspace_epoch=page["workspace_epoch"],
        expected_document_epoch=page["document_epoch"], operation_id="locked-history-page",
    )
    page = _page(authority, "content-editor")
    with sqlite3.connect(catalog) as con:
        before = con.execute("SELECT COUNT(*) FROM workspace_collaboration_operations").fetchone()[0]
    with pytest.raises(PermissionError, match="page is locked"):
        authority.restore_revision(
            "content-editor", "page-e1", 1,
            expected_revision=page["revision"],
            expected_document_sequence=page["document_sequence"],
            expected_workspace_epoch=page["workspace_epoch"],
            expected_document_epoch=page["document_epoch"], operation_id="locked-history-restore",
        )
    assert _page(authority, "content-editor") == page
    with sqlite3.connect(catalog) as con:
        assert con.execute("SELECT COUNT(*) FROM workspace_collaboration_operations").fetchone()[0] == before


def test_f0_p3_history_list_diff_restore_restart_and_fail_closed(tmp_path: Path) -> None:
    authority, root, catalog = _authority(tmp_path)
    initial = authority.get_revision("owner", "page-e1", 1)
    changed = _save_changed(authority)
    assert changed["revision"] == 2

    history = authority.list_history("schema-editor", "page-e1", limit=1)
    assert [item["revision"] for item in history["items"]] == [2]
    assert history["has_more"] is True
    assert history["next_before_revision"] == 2
    older = authority.list_history(
        "schema-editor", "page-e1", before_revision=2, limit=10
    )
    assert [item["revision"] for item in older["items"]] == [1]
    diff = authority.diff_revisions(
        "schema-editor", "page-e1", from_revision=1, to_revision=2
    )
    assert diff["changed"] is True
    assert any("Changed history title" in line for line in diff["lines"])
    with pytest.raises(NotFoundOrDenied, match="not found or permission denied"):
        authority.list_history("outsider", "page-e1")

    updates = authority.list_update_previews("schema-editor", "page-e1", limit=1)
    assert [item["revision"] for item in updates["items"]] == [2]
    assert updates["next_before_revision"] == 2
    assert {entry["text"] for entry in updates["items"][0]["preview"]["items"]} >= {
        "Changed history title", "Changed history body",
    }
    older_updates = authority.list_update_previews(
        "schema-editor", "page-e1", before_revision=2, limit=1,
    )
    assert [item["revision"] for item in older_updates["items"]] == [1]
    assert older_updates["has_more"] is False
    with pytest.raises(NotFoundOrDenied, match="not found or permission denied"):
        authority.list_update_previews("outsider", "page-e1")

    page = _page(authority)
    restored = authority.restore_revision(
        "content-editor",
        "page-e1",
        1,
        expected_revision=int(page["revision"]),
        expected_document_sequence=int(page["document_sequence"]),
        expected_workspace_epoch=int(page["workspace_epoch"]),
        expected_document_epoch=int(page["document_epoch"]),
        operation_id="op-f0-p3-restore-0001",
    )
    assert restored["revision"] == 3
    assert restored["crdt_generation"] == 2
    assert restored["document_epoch"] == int(page["document_epoch"]) + 1
    assert restored["restored_from_content_sha256"] == initial["content_sha256"]
    assert restored["content_sha256"] != initial["content_sha256"]
    assert authority.restore_revision(
        "content-editor",
        "page-e1",
        1,
        expected_revision=int(page["revision"]),
        expected_document_sequence=int(page["document_sequence"]),
        expected_workspace_epoch=int(page["workspace_epoch"]),
        expected_document_epoch=int(page["document_epoch"]),
        operation_id="op-f0-p3-restore-0001",
    ) == restored
    current = _page(authority)
    assert current["title"] == initial["snapshot"]["title"]
    assert current["blocks"] == initial["snapshot"]["blocks"]

    restarted = CollaborativeHistoryAuthority.open(
        catalog, root, InMemoryIdentityDirectory((*SUBJECTS, "outsider"))
    )
    assert restarted.get_revision("schema-editor", "page-e1", 3)["snapshot"]["title"] == current["title"]
    con = sqlite3.connect(catalog)
    try:
        assert con.execute(
            "SELECT COUNT(*) FROM workspace_collaboration_history_restores"
        ).fetchone()[0] == 1
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []
        with pytest.raises(sqlite3.IntegrityError, match="history restore is immutable"):
            con.execute(
                "UPDATE workspace_collaboration_history_restores SET created_at=created_at+1"
            )
    finally:
        con.close()


def test_f0_p3_history_retains_and_restores_committed_attachment(tmp_path: Path) -> None:
    authority, _root, _catalog = _authority(tmp_path)
    payload = b"history-retained-file" * 5000
    session = _begin(authority, payload, "op-f0-p3-file-begin-0001")
    _stage_all(authority, session, payload)
    uploaded = authority.finalize_upload(
        "content-editor",
        str(session["session_id"]),
        operation_id="op-f0-p3-file-finalize-0001",
    )
    page = _page(authority)
    checkpoint = authority.create_revision_checkpoint(
        "content-editor",
        "page-e1",
        expected_revision=int(page["revision"]),
        expected_document_sequence=int(page["document_sequence"]),
        expected_workspace_epoch=int(page["workspace_epoch"]),
        expected_document_epoch=int(page["document_epoch"]),
        operation_id="op-f0-p3-file-checkpoint-0001",
        publication_revision_id="publication-revision-fixed-0001",
    )
    file_path = authority.get_file("owner", str(uploaded["file_id"]))["path"]
    page = _page(authority)
    authority.delete_file(
        "content-editor",
        str(uploaded["file_id"]),
        expected_document_sequence=int(page["document_sequence"]),
        expected_workspace_epoch=int(page["workspace_epoch"]),
        expected_document_epoch=int(page["document_epoch"]),
        operation_id="op-f0-p3-file-delete-0001",
    )
    assert file_path.is_file()
    page = _page(authority)
    restored = authority.restore_revision(
        "content-editor",
        "page-e1",
        int(checkpoint["revision"]),
        expected_revision=int(page["revision"]),
        expected_document_sequence=int(page["document_sequence"]),
        expected_workspace_epoch=int(page["workspace_epoch"]),
        expected_document_epoch=int(page["document_epoch"]),
        operation_id="op-f0-p3-file-restore-0001",
    )
    assert restored["revision"] == int(page["revision"]) + 1
    assert restored["preserved_publication_revision_id"] == "publication-revision-fixed-0001"
    downloaded = authority.get_file("owner", str(uploaded["file_id"]))
    assert downloaded["path"].read_bytes() == payload
    assert authority.retention_policy()["raw_crdt_pruning_enabled"] is False


def test_f0_p3_history_http_contract(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    authority, root, catalog = _authority(tmp_path)
    _save_changed(authority)
    store = CollaborativeWorkspaceStore(
        catalog,
        user_data_root=root,
        identity_directory=InMemoryIdentityDirectory((*SUBJECTS, "outsider")),
    )
    actor = {"id": "content-editor"}
    monkeypatch.setattr(routes_notion_workspace, "_store", lambda: store)
    monkeypatch.setattr(routes_notion_workspace, "_uid", lambda: actor["id"])
    monkeypatch.setattr(
        routes_notion_workspace, "_collaborative_runtime_enabled", lambda: True
    )
    application = Flask("f0-p3-history-http")
    application.secret_key = "f0-p3-history-test"
    register_notion_history_routes(application)
    client = application.test_client()
    workspace_id = str(store.iam.read_document(actor["id"], "page-e1")["workspace_id"])
    base = f"/api/notion/workspaces/{workspace_id}/pages/page-e1/history"

    listed = client.get(base)
    assert listed.status_code == 200
    assert listed.get_json()["count"] == 2
    updates = client.get(f"/api/notion/workspaces/{workspace_id}/pages/page-e1/updates?limit=1")
    assert updates.status_code == 200
    assert updates.get_json()["items"][0]["preview"]["items"]
    assert updates.get_json()["next_before_revision"] == 2
    diff = client.get(f"{base}/diff?from_revision=1&to_revision=2")
    assert diff.status_code == 200
    assert diff.get_json()["changed"] is True
    assert client.get(f"{base}/policy").get_json()["plan_entitlement_limits"] is False
    assert client.get(f"{base}/revisions/1").status_code == 200

    page = store.iam.read_document(actor["id"], "page-e1")
    restored = client.post(
        f"{base}/revisions/1/restore",
        headers={"Origin": "http://localhost"},
        json={
            "expected_revision": int(page["revision"]),
            "expected_document_sequence": int(page["document_sequence"]),
            "expected_workspace_epoch": int(page["workspace_epoch"]),
            "expected_document_epoch": int(page["document_epoch"]),
            "operation_id": "op-f0-p3-http-restore-0001",
        },
    )
    assert restored.status_code == 200
    assert restored.get_json()["restored_from_revision"] == 1
    actor["id"] = "outsider"
    denied = client.get(base)
    assert denied.status_code == 404
    assert denied.get_json()["code"] == "not_found_or_permission_denied"
    assert client.get(f"/api/notion/workspaces/{workspace_id}/pages/page-e1/updates").status_code == 404

    marker = sqlite3.connect(catalog).execute(
        "SELECT version,name,checksum,actual_schema_sha256 "
        "FROM workspace_schema_migrations ORDER BY version DESC LIMIT 1"
    ).fetchone()
    assert marker == (
        migration.TARGET_VERSION,
        migration.TARGET_NAME,
        migration.TARGET_CHECKSUM,
        migration.TARGET_ACTUAL_SCHEMA_SHA256,
    )
