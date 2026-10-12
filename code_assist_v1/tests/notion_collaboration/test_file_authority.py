from __future__ import annotations

from io import BytesIO
import hashlib
from pathlib import Path
import sqlite3

import pytest
from flask import Flask

from code_assist_v1 import routes_notion_workspace
from code_assist_v1.routes_notion_files import register_notion_file_routes
from code_assist_v1 import notion_workspace_file_authority_migration as migration
from code_assist_v1.notion_collaboration.canonical_authority import NotFoundOrDenied
from code_assist_v1.notion_collaboration.file_authority import (
    CapacityUnavailable,
    CollaborativeFileAuthority,
)
from code_assist_v1.notion_collaboration.iam_authority import InMemoryIdentityDirectory
from code_assist_v1.notion_collaboration.iam_runtime import CollaborativeWorkspaceStore
from code_assist_v1.notion_collaboration.page_settings import update_page_settings
from code_assist_v1.tests.notion_collaboration.test_database_features import (
    SUBJECTS,
    _apply,
    _authority as _database_authority,
)


class BoundedBytesIO(BytesIO):
    def __init__(self, value: bytes, maximum_read: int) -> None:
        super().__init__(value)
        self.maximum_read = maximum_read
        self.largest_read = 0

    def read(self, size: int = -1) -> bytes:
        assert 0 <= size <= self.maximum_read
        self.largest_read = max(self.largest_read, size)
        return super().read(size)


def _authority(tmp_path: Path, **kwargs) -> tuple[CollaborativeFileAuthority, Path, Path]:
    _database, root, catalog = _database_authority(tmp_path)
    _apply(migration, catalog, root, "before-2408.db")
    identities = InMemoryIdentityDirectory((*SUBJECTS, "outsider"))
    return (
        CollaborativeFileAuthority.open(
            catalog, root, identities, initialize_storage=True, **kwargs
        ),
        root,
        catalog,
    )


def _page(authority: CollaborativeFileAuthority, subject: str = "content-editor") -> dict:
    return authority.read_document(subject, "page-e1")


def _begin(
    authority: CollaborativeFileAuthority,
    payload: bytes,
    operation: str,
    *,
    name: str = "evidence.bin",
    actor: str = "content-editor",
) -> dict:
    page = _page(authority, actor)
    return authority.begin_upload(
        actor,
        "page-e1",
        original_name=name,
        mime_type="application/octet-stream",
        total_size=len(payload),
        expected_sha256="sha256:" + hashlib.sha256(payload).hexdigest(),
        expected_document_sequence=int(page["document_sequence"]),
        expected_workspace_epoch=int(page["workspace_epoch"]),
        expected_document_epoch=int(page["document_epoch"]),
        operation_id=operation,
        chunk_size=65_536,
    )


def _stage_all(
    authority: CollaborativeFileAuthority,
    session: dict,
    payload: bytes,
    *,
    order: tuple[int, ...] | None = None,
) -> list[dict]:
    chunk_size = int(session["chunk_size"])
    indexes = order or tuple(range(int(session["chunk_count"])))
    results = []
    for index in indexes:
        value = payload[index * chunk_size : (index + 1) * chunk_size]
        stream = BoundedBytesIO(value, 1_048_576)
        results.append(
            authority.stage_chunk(
                "content-editor",
                str(session["session_id"]),
                chunk_index=index,
                stream=stream,
                operation_id=f"op-f0-p2-chunk-{session['session_id']}-{index}",
            )
        )
        assert stream.largest_read <= 1_048_576
    return results


def test_locked_collaborative_page_rejects_file_begin_finalize_and_delete(tmp_path: Path) -> None:
    authority, _root, catalog = _authority(tmp_path)
    payload = b"locked-page-file"
    existing = _begin(authority, payload, "locked-file-existing-begin")
    _stage_all(authority, existing, payload)
    file_id = authority.finalize_upload(
        "content-editor", existing["session_id"], operation_id="locked-file-existing-final",
    )["file_id"]
    pending = _begin(authority, payload, "locked-file-pending-begin")
    _stage_all(authority, pending, payload)
    page = _page(authority, "schema-editor")
    update_page_settings(
        authority, "schema-editor", page["workspace_id"], "page-e1",
        updates={"locked": True}, expected_document_sequence=page["document_sequence"],
        expected_workspace_epoch=page["workspace_epoch"],
        expected_document_epoch=page["document_epoch"], operation_id="locked-file-page-settings",
    )
    page = _page(authority, "content-editor")
    with sqlite3.connect(catalog) as con:
        before = con.execute("SELECT COUNT(*) FROM workspace_collaboration_operations").fetchone()[0]
    with pytest.raises(PermissionError, match="page is locked"):
        _begin(authority, payload, "locked-file-denied-begin")
    with pytest.raises(PermissionError, match="page is locked"):
        authority.finalize_upload(
            "content-editor", pending["session_id"], operation_id="locked-file-denied-final",
        )
    with pytest.raises(PermissionError, match="page is locked"):
        authority.delete_file(
            "content-editor", file_id, expected_document_sequence=page["document_sequence"],
            expected_workspace_epoch=page["workspace_epoch"],
            expected_document_epoch=page["document_epoch"], operation_id="locked-file-denied-delete",
        )
    assert _page(authority, "content-editor") == page
    assert authority.get_file("content-editor", file_id)["file_id"] == file_id
    with sqlite3.connect(catalog) as con:
        assert con.execute("SELECT COUNT(*) FROM workspace_collaboration_operations").fetchone()[0] == before


@pytest.mark.integration
def test_ui_icon_preview_long_chunk_operation_preserves_retry_binding_and_bytes(tmp_path: Path) -> None:
    authority, _root, catalog = _authority(tmp_path)
    payload = b"UI-ICON-PREVIEW-WINDOWS" * 32
    begun = _begin(authority, payload, "op-ui-icon-preview-long-begin")
    operation = 'op-ui-icon-preview-' + 'x' * (200 - len('op-ui-icon-preview-'))
    session = str(begun['session_id'])
    first = authority.stage_chunk('content-editor', session, chunk_index=0,
        stream=BytesIO(payload), operation_id=operation)
    assert not first['replayed']
    second = authority.stage_chunk('content-editor', session, chunk_index=0,
        stream=BytesIO(payload), operation_id=operation)
    assert second['replayed'] and first['content_sha256'] == second['content_sha256']
    with sqlite3.connect(catalog) as con:
        row = con.execute('SELECT operation_id,relative_path FROM workspace_collaboration_upload_chunks WHERE session_id=?', (session,)).fetchone()
    assert row[0] == operation
    assert (authority.storage_root / row[1]).read_bytes() == payload
    assert not list(authority._session_directory(session).glob('*.tmp'))
    result = authority.finalize_upload('content-editor', session, operation_id='op-ui-icon-preview-long-final')
    assert Path(authority.get_file('content-editor', result['file_id'])['path']).read_bytes() == payload


@pytest.mark.integration
def test_f0_p2_resumable_bounded_upload_restart_authorized_download_and_delete(
    tmp_path: Path,
) -> None:
    authority, root, catalog = _authority(tmp_path)
    payload = (b"bounded-file-authority\x00" * 7_000) + b"tail"
    begun = _begin(authority, payload, "op-f0-p2-begin-0001")
    assert int(begun["chunk_count"]) == 3

    _stage_all(authority, begun, payload, order=(1,))
    status = authority.upload_status("content-editor", str(begun["session_id"]))
    assert status["received_chunks"] == [1]
    assert status["missing_chunks"] == [0, 2]

    restarted = CollaborativeFileAuthority.open(
        catalog,
        root,
        InMemoryIdentityDirectory((*SUBJECTS, "outsider")),
    )
    _stage_all(restarted, begun, payload, order=(2, 0))
    ready = restarted.upload_status("content-editor", str(begun["session_id"]))
    assert ready["state"] == "ready"
    assert ready["missing_chunks"] == []

    finalized = restarted.finalize_upload(
        "content-editor",
        str(begun["session_id"]),
        operation_id="op-f0-p2-finalize-0001",
    )
    replay = restarted.finalize_upload(
        "content-editor",
        str(begun["session_id"]),
        operation_id="op-f0-p2-finalize-0001",
    )
    assert replay == finalized
    downloaded = restarted.get_file("owner", str(finalized["file_id"]))
    assert downloaded["path"].read_bytes() == payload
    with pytest.raises(NotFoundOrDenied, match="not found or permission denied"):
        restarted.get_file("outsider", str(finalized["file_id"]))

    page = _page(restarted)
    deleted = restarted.delete_file(
        "content-editor",
        str(finalized["file_id"]),
        expected_document_sequence=int(page["document_sequence"]),
        expected_workspace_epoch=int(page["workspace_epoch"]),
        expected_document_epoch=int(page["document_epoch"]),
        operation_id="op-f0-p2-delete-0001",
    )
    assert deleted["state"] == "deleted"
    assert restarted.list_files("owner", "page-e1")["files"] == []
    with pytest.raises(NotFoundOrDenied, match="not found or permission denied"):
        restarted.get_file("owner", str(finalized["file_id"]))
    assert not downloaded["path"].exists()

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


@pytest.mark.integration
def test_f0_p2_shared_blob_cancel_expiry_cleanup_and_operational_not_plan_capacity(
    tmp_path: Path,
) -> None:
    authority, _root, catalog = _authority(tmp_path, max_inflight_bytes=300_000)
    payload = b"same-content" * 8_000

    first = _begin(authority, payload, "op-f0-p2-begin-shared-0001", name="first.bin")
    _stage_all(authority, first, payload)
    first_file = authority.finalize_upload(
        "content-editor", str(first["session_id"]), operation_id="op-f0-p2-finalize-shared-0001"
    )
    second = _begin(authority, payload, "op-f0-p2-begin-shared-0002", name="second.bin")
    _stage_all(authority, second, payload)
    second_file = authority.finalize_upload(
        "content-editor", str(second["session_id"]), operation_id="op-f0-p2-finalize-shared-0002"
    )
    first_path = authority.get_file("owner", str(first_file["file_id"]))["path"]
    second_path = authority.get_file("owner", str(second_file["file_id"]))["path"]
    assert first_path == second_path

    page = _page(authority)
    authority.delete_file(
        "content-editor",
        str(first_file["file_id"]),
        expected_document_sequence=int(page["document_sequence"]),
        expected_workspace_epoch=int(page["workspace_epoch"]),
        expected_document_epoch=int(page["document_epoch"]),
        operation_id="op-f0-p2-delete-shared-0001",
    )
    assert second_path.exists()
    assert authority.get_file("owner", str(second_file["file_id"]))["path"].read_bytes() == payload

    canceled = _begin(authority, b"cancel-me", "op-f0-p2-begin-cancel-0001")
    cancel_result = authority.cancel_upload(
        "content-editor", str(canceled["session_id"]), operation_id="op-f0-p2-cancel-0001"
    )
    cancel_replay = authority.cancel_upload(
        "content-editor", str(canceled["session_id"]), operation_id="op-f0-p2-cancel-0001"
    )
    assert cancel_replay == cancel_result
    assert authority.upload_status("content-editor", str(canceled["session_id"]))["state"] == "cleaned"

    expired = _begin(authority, b"expire-me", "op-f0-p2-begin-expire-0001")
    with sqlite3.connect(catalog) as con:
        con.execute(
            "UPDATE workspace_collaboration_upload_sessions SET expires_at=0,"
            "row_version=row_version+1,updated_at=updated_at+1 WHERE id=?",
            (str(expired["session_id"]),),
        )
    cleanup = authority.cleanup_orphans(now=1_000_000_000, max_items=32)
    assert str(expired["session_id"]) in cleanup["marked_sessions"]
    assert authority.upload_status("content-editor", str(expired["session_id"]))["state"] == "cleaned"

    reservation = _begin(authority, b"r" * 200_000, "op-f0-p2-begin-reserve-0001")
    with pytest.raises(CapacityUnavailable, match="operational inflight capacity"):
        _begin(authority, b"s" * 150_000, "op-f0-p2-begin-reserve-0002")
    authority.cancel_upload(
        "content-editor", str(reservation["session_id"]), operation_id="op-f0-p2-cancel-reserve-0001"
    )
    assert authority.capacity_policy()["plan_entitlement_limits"] is False


@pytest.mark.integration
def test_f0_p2_direct_metadata_mutation_and_failed_chunk_are_fail_closed(
    tmp_path: Path,
) -> None:
    authority, _root, catalog = _authority(tmp_path)
    payload = b"x" * 65_536
    begun = _begin(authority, payload, "op-f0-p2-begin-guard-0001")
    with pytest.raises(ValueError, match="chunk exceeds expected size"):
        authority.stage_chunk(
            "content-editor",
            str(begun["session_id"]),
            chunk_index=0,
            stream=BytesIO(payload + b"overflow"),
            operation_id="op-f0-p2-chunk-overflow-0001",
        )
    directory = authority._session_directory(str(begun["session_id"]))
    assert list(directory.glob("*.tmp")) == []

    _stage_all(authority, begun, payload)
    finalized = authority.finalize_upload(
        "content-editor", str(begun["session_id"]), operation_id="op-f0-p2-finalize-guard-0001"
    )
    with sqlite3.connect(catalog) as con:
        with pytest.raises(sqlite3.IntegrityError, match="file transition is invalid"):
            con.execute(
                "UPDATE workspace_collaboration_files SET original_name='tampered.bin',"
                "row_version=row_version+1,updated_at=updated_at+1 WHERE id=?",
                (str(finalized["file_id"]),),
            )


@pytest.mark.integration
def test_f0_p2_http_resumable_contract_authorization_and_no_plan_entitlement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    authority, root, catalog = _authority(tmp_path)
    store = CollaborativeWorkspaceStore(
        catalog,
        user_data_root=root,
        identity_directory=InMemoryIdentityDirectory((*SUBJECTS, "outsider")),
    )
    actor = {"id": "content-editor"}
    monkeypatch.setattr(routes_notion_workspace, "_uid", lambda: actor["id"])
    monkeypatch.setattr(routes_notion_workspace, "_store", lambda: store)
    monkeypatch.setattr(
        routes_notion_workspace, "_collaborative_runtime_enabled", lambda: True
    )
    app = Flask("f0-p2-file-http", static_folder=None)
    app.config.update(TESTING=True, SECRET_KEY="f0-p2-test")
    routes_notion_workspace.register_notion_workspace_routes(app)
    register_notion_file_routes(app)
    client = app.test_client()

    page = _page(authority)
    workspace_id = str(page["workspace_id"])
    base = f"/api/notion/workspaces/{workspace_id}/pages/page-e1/files"
    policy = client.get(base + "/policy").get_json()
    assert policy["plan_entitlement_limits"] is False

    payload = b"http-streaming-file" * 9_000
    begun_response = client.post(
        base + "/uploads",
        headers={"Origin": "http://localhost"},
        json={
            "original_name": "http.bin",
            "mime_type": "application/octet-stream",
            "total_size": len(payload),
            "expected_sha256": "sha256:" + hashlib.sha256(payload).hexdigest(),
            "expected_document_sequence": int(page["document_sequence"]),
            "expected_workspace_epoch": int(page["workspace_epoch"]),
            "expected_document_epoch": int(page["document_epoch"]),
            "operation_id": "op-f0-p2-http-begin-0001",
            "chunk_size": 65_536,
        },
    )
    assert begun_response.status_code == 201
    begun = begun_response.get_json()
    session_id = str(begun["session_id"])
    for index in range(int(begun["chunk_count"])):
        start = index * int(begun["chunk_size"])
        value = payload[start : start + int(begun["chunk_size"])]
        chunk_response = client.put(
            f"{base}/uploads/{session_id}/chunks/{index}",
            headers={
                "Origin": "http://localhost",
                "Content-Type": "application/octet-stream",
                "X-Notion-Operation-Id": f"op-f0-p2-http-chunk-{index:04d}",
            },
            data=value,
        )
        assert chunk_response.status_code == 200

    uploaded_response = client.post(
        f"{base}/uploads/{session_id}/finalize",
        headers={"Origin": "http://localhost"},
        json={"operation_id": "op-f0-p2-http-finalize-0001"},
    )
    assert uploaded_response.status_code == 200
    uploaded = uploaded_response.get_json()["file"]
    assert uploaded["url"].startswith("/api/notion/collaboration/files/file_")
    assert client.get(uploaded["url"]).data == payload
    listed = client.get(base).get_json()["files"]
    assert [item["id"] for item in listed] == [uploaded["id"]]

    actor["id"] = "outsider"
    denied = client.get(uploaded["url"])
    assert denied.status_code == 404
    assert denied.get_json()["code"] == "not_found_or_permission_denied"
    actor["id"] = "content-editor"

    current = authority.read_document("content-editor", "page-e1")
    deleted = client.delete(
        f"{base}/{uploaded['id']}",
        headers={"Origin": "http://localhost"},
        json={
            "expected_document_sequence": int(current["document_sequence"]),
            "expected_workspace_epoch": int(current["workspace_epoch"]),
            "expected_document_epoch": int(current["document_epoch"]),
            "operation_id": "op-f0-p2-http-delete-0001",
        },
    )
    assert deleted.status_code == 200
    assert client.get(uploaded["url"]).status_code == 404
