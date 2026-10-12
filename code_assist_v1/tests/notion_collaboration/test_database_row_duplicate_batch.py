"""Selected database rows duplicate their canonical content in one receipt."""

import pytest

from code_assist_v1.notion_collaboration.canonical_authority import (
    AuthorityUnavailable, InjectedFailure, OperationConflict,
)
from code_assist_v1.notion_collaboration.editing_authority import TargetVersionConflict
from code_assist_v1.notion_collaboration.synced_blocks import group_id
from code_assist_v1.tests.notion_collaboration.test_database_row_icon_batch import (
    icon_batch_environment, appearance_environment, activity_environment,
    environment, body_environment, team_environment, file_environment,
    member_environment, native_environment, archive_environment,
    guest_environment, image_environment,
)
from code_assist_v1.tests.notion_collaboration.test_database_features import _surface
from code_assist_v1.tests.notion_collaboration.test_database_trash_http import _client


def _request(store, operation="duplicate-rows"):
    page = store.iam.read_document("owner", "page-e1")
    database = store.iam.read_database("owner", "database-e1")
    return {"database_id": "database-e1", "target_id": "database-e1",
        "payload": {"rows": [{"row_id": row["id"],
            "expected_target_version": row["version"]} for row in database["rows"]]},
        "expected_document_sequence": page["document_sequence"],
        "expected_workspace_epoch": page["workspace_epoch"],
        "expected_document_epoch": page["document_epoch"],
        "expected_target_version": None, "operation_id": operation}


@pytest.mark.integration
def test_selected_row_duplicate_requires_exact_2423(appearance_environment):
    store, _ = appearance_environment
    assert store.iam.read_database("owner", "database-e1")["capabilities"]["row_duplicate_batch"] is False
    with pytest.raises(AuthorityUnavailable):
        store.iam.execute_row_duplicate_batch("owner", "page-e1", **_request(store))


@pytest.mark.integration
@pytest.mark.regression
def test_selected_row_duplicate_copies_cells_body_appearance_and_replays_once(icon_batch_environment):
    store, _ = icon_batch_environment
    authority = store.iam
    body = [{"id": "source-heading", "type": "heading1", "content": "본문 제목", "props": {}},
        {"id": "source-link", "type": "paragraph", "content": "링크", "props": {
            "rich_text_html": '<a href="#block-source-heading">본문 제목</a>'}},
        {"id": "source-sync", "type": "synced_block", "content": "원본", "props": {
            "synced_from": None, "sync_id": group_id("page-e1", "source-sync")}},
        {"id": "source-sync-ref", "type": "synced_block", "content": "", "props": {
            "synced_from": {"document_id": "page-e1", "block_id": "source-sync"},
            "sync_id": group_id("page-e1", "source-sync")}}]
    _surface(authority, "database.row.content.set", "row-e1", {"blocks": body},
        "duplicate-seed-body", actor="owner", target_version=1)
    _surface(authority, "database.row.appearance.set", "row-e1",
        {"icon": "🚀", "cover": "linear-gradient(135deg, #ecb3be, #677fbb)",
         "cover_position": 0.3}, "duplicate-seed-appearance", actor="owner", target_version=2)
    request = _request(store)
    before = authority.read_database("owner", "database-e1")
    assert before["capabilities"]["row_duplicate_batch"] is True
    with pytest.raises(InjectedFailure):
        authority.execute_row_duplicate_batch("owner", "page-e1", **request, fault="after_domain")
    assert authority.read_database("owner", "database-e1") == before
    stale = {**request, "payload": {"rows": [request["payload"]["rows"][0],
        {**request["payload"]["rows"][1], "expected_target_version": 999}]}}
    with pytest.raises(TargetVersionConflict):
        authority.execute_row_duplicate_batch("owner", "page-e1", **stale)
    assert authority.read_database("owner", "database-e1") == before

    result = authority.execute_row_duplicate_batch("owner", "page-e1", **request)
    assert result["command_kind"] == "database.rows.duplicate"
    assert result["document_sequence"] == request["expected_document_sequence"] + 1
    assert [item["source_row_id"] for item in result["rows"]] == ["row-e1", "row-e2"]
    assert authority.execute_row_duplicate_batch("owner", "page-e1", **request) == result
    with pytest.raises(OperationConflict):
        authority.execute_row_duplicate_batch("owner", "page-e1", **{
            **request, "payload": {"rows": request["payload"]["rows"][:1]}})

    after = authority.read_database("owner", "database-e1")
    assert [row["id"] for row in after["rows"]] == [
        "row-e1", result["rows"][0]["row_id"],
        "row-e2", result["rows"][1]["row_id"]]
    for source_id, copied in zip(("row-e1", "row-e2"), result["rows"]):
        original = next(row for row in before["rows"] if row["id"] == source_id)
        clone = next(row for row in after["rows"] if row["id"] == copied["row_id"])
        assert clone["cells"] == original["cells"]
        assert clone["cell_versions"] == {key: 1 for key in original["cells"]}
        assert clone.get("appearance") == original.get("appearance")
        assert clone["version"] == copied["target_version"] == 1
    first = after["rows"][1]
    assert [block["content"] for block in first["body"]["blocks"]] == ["본문 제목", "링크", "원본", ""]
    assert first["body"]["blocks"][0]["id"] != "source-heading"
    assert first["body"]["blocks"][1]["props"]["rich_text_html"] == (
        f'<a href="#block-{first["body"]["blocks"][0]["id"]}">본문 제목</a>')
    copied_sync = first["body"]["blocks"][2]
    copied_reference = first["body"]["blocks"][3]
    assert copied_reference["props"]["synced_from"] == {
        "document_id": "page-e1", "block_id": copied_sync["id"]}
    assert copied_sync["props"]["sync_id"] == copied_reference["props"]["sync_id"] == (
        group_id("page-e1", copied_sync["id"]))
    assert after["rows"][0]["body"]["blocks"][0]["id"] == "source-heading"
    with authority._reader() as con:
        for table in ("operations", "events", "audit_events", "outbox"):
            count = con.execute(f"SELECT COUNT(*) FROM workspace_collaboration_{table} "
                "WHERE operation_id=?", (request["operation_id"],)).fetchone()[0]
            assert count == 1
        assert not con.execute("PRAGMA foreign_key_check").fetchall()


@pytest.mark.integration
@pytest.mark.regression
def test_selected_row_duplicate_http_gate_and_receipt(icon_batch_environment, monkeypatch):
    store, workspace_id = icon_batch_environment
    client, _ = _client(store, monkeypatch, enabled=False)
    request = _request(store, operation="duplicate-http")
    path = f"/api/notion/workspaces/{workspace_id}/pages/page-e1/databases/commands"
    body = {**request, "command_kind": "database.rows.duplicate"}
    assert client.post(path, json=body).status_code == 503
    capabilities = store.capabilities
    monkeypatch.setattr(store, "capabilities", lambda: {
        **capabilities(), "collaborative_database_row_duplicate_batch": True})
    result = client.post(path, json=body)
    assert result.status_code == 201, result.json
    assert result.json["contract"] == "code_assist.notion.collaborative_database_row_duplicate_batch.v1"
    assert client.post(path, json=body).json == result.json
    assert len(store.iam.read_database("owner", "database-e1")["rows"]) == 4
