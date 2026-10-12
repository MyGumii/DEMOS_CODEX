from __future__ import annotations

from pathlib import Path
import json
import sqlite3
import subprocess
import sys

from flask import Flask
import pytest

from code_assist_v1 import notion_workspace_search_authority_migration as migration
from code_assist_v1 import routes_notion_workspace
from code_assist_v1.notion_collaboration.canonical_authority import (
    NotFoundOrDenied,
    OperationConflict,
)
from code_assist_v1.notion_collaboration.iam_authority import InMemoryIdentityDirectory
from code_assist_v1.notion_collaboration.iam_runtime import CollaborativeWorkspaceStore
from code_assist_v1.notion_collaboration.search_authority import (
    CollaborativeSearchAuthority,
)
from code_assist_v1.routes_notion_search import register_notion_search_routes
from code_assist_v1.tests.notion_collaboration.test_database_features import (
    SUBJECTS,
    _apply,
    _schema,
    _surface,
)
from code_assist_v1.tests.notion_collaboration.test_history_authority import (
    _authority as _history_authority,
)


IDENTITIES = (*SUBJECTS, "outsider")
REMIND_AT = "2027-01-01T00:00:00+00:00"
SEARCH_WORKER = r"""
import json
from pathlib import Path
import sys
from code_assist_v1.notion_collaboration.iam_authority import InMemoryIdentityDirectory
from code_assist_v1.notion_collaboration.search_authority import CollaborativeSearchAuthority
authority = CollaborativeSearchAuthority.open(
    Path(sys.argv[1]), Path(sys.argv[2]),
    InMemoryIdentityDirectory(("owner", "content-editor", "schema-editor", "outsider")),
)
print(json.dumps(authority.search(sys.argv[3], sys.argv[4], query=sys.argv[5]), sort_keys=True))
"""


def _authority(tmp_path: Path) -> tuple[CollaborativeSearchAuthority, Path, Path]:
    _history, root, catalog = _history_authority(tmp_path)
    _apply(migration, catalog, root, "before-2410.db")
    return (
        CollaborativeSearchAuthority.open(
            catalog, root, InMemoryIdentityDirectory(IDENTITIES)
        ),
        root,
        catalog,
    )


def _search_in_fresh_process(
    catalog: Path, root: Path, actor: str, workspace_id: str, query: str
) -> dict:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            SEARCH_WORKER,
            str(catalog),
            str(root),
            actor,
            workspace_id,
            query,
        ],
        cwd=Path(__file__).resolve().parents[3],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return json.loads(completed.stdout.strip().splitlines()[-1])


def _grant(
    authority: CollaborativeSearchAuthority,
    document_id: str,
    subject: str,
    operation_id: str,
) -> dict:
    page = authority.read_document("owner", document_id)
    return authority.set_page_grant(
        "owner",
        document_id,
        "subject",
        subject,
        role="view",
        inherit_to_descendants=False,
        expected_workspace_epoch=int(page["workspace_epoch"]),
        expected_document_epoch=int(page["document_epoch"]),
        operation_id=operation_id,
    )


def test_database_date_reminder_reaches_inbox_and_respects_recipient(tmp_path: Path) -> None:
    authority, _root, _catalog = _authority(tmp_path)
    _schema(
        authority, "database.property.create", "due",
        {"name": "Due", "type": "date", "config": {}},
        "op-date-reminder-property-create",
    )
    workspace_id = str(authority.read_document("owner", "page-e1")["workspace_id"])

    def set_date(value, version: int, operation_id: str):
        page = authority.read_document("content-editor", "page-e1")
        return authority.execute_cell_batch(
            "content-editor", "page-e1",
            database_id="database-e1", target_id="database-e1",
            payload={"cells": [{"row_id": "row-e1", "property_id": "due",
                                "value": value, "expected_target_version": version}]},
            expected_document_sequence=page["document_sequence"],
            expected_workspace_epoch=page["workspace_epoch"],
            expected_document_epoch=page["document_epoch"],
            operation_id=operation_id,
        )

    value = {"start": "2027-01-01", "reminder": {
        "at": "2027-01-01T09:00:00+09:00", "lead": 0,
        "user_id": "content-editor",
    }}
    with pytest.raises(ValueError, match="another user's"):
        set_date({**value, "reminder": {**value["reminder"], "user_id": "owner"}},
                 0, "op-date-reminder-foreign")
    assert authority.list_reminders("content-editor", workspace_id)["items"] == []

    first = set_date(value, 0, "op-date-reminder-create")
    assert authority.read_database("content-editor", "database-e1")["rows"][0]["cells"]["due"]["reminder"] == value["reminder"]
    items = authority.list_reminders("content-editor", workspace_id)["items"]
    assert len(items) == 1
    assert items[0]["page_id"] == "page-e1"
    assert items[0]["remind_at"] == value["reminder"]["at"]
    assert authority.list_reminders("owner", workspace_id)["items"] == []

    set_date("2027-01-02", first["cells"][0]["target_version"],
             "op-date-reminder-clear")
    assert authority.list_reminders("content-editor", workspace_id)["items"] == []


def test_database_date_reminder_follows_another_editor_date_change(tmp_path: Path) -> None:
    authority, _root, _catalog = _authority(tmp_path)
    _schema(authority, "database.property.create", "due",
            {"name": "Due", "type": "date", "config": {}},
            "op-date-rebase-property-create")
    workspace_id = str(authority.read_document("owner", "page-e1")["workspace_id"])

    def set_date(actor: str, value: dict, version: int, operation_id: str):
        page = authority.read_document(actor, "page-e1")
        return authority.execute_cell_batch(
            actor, "page-e1", database_id="database-e1", target_id="database-e1",
            payload={"cells": [{"row_id": "row-e1", "property_id": "due",
                                "value": value, "expected_target_version": version}]},
            expected_document_sequence=page["document_sequence"],
            expected_workspace_epoch=page["workspace_epoch"],
            expected_document_epoch=page["document_epoch"],
            operation_id=operation_id,
        )

    reminder = {"at": "2027-01-01T00:00:00.000Z", "lead": 15,
                "user_id": "content-editor", "preset": "minute_15", "zone": "Asia/Seoul"}
    value = {"start": "2027-01-01", "time": "09:00", "time_zone": "Asia/Seoul",
             "reminder": reminder}
    first = set_date("content-editor", value, 0, "op-date-rebase-create")
    first_version = first["cells"][0]["target_version"]
    with pytest.raises(ValueError, match="another user's"):
        set_date("schema-editor", {**value, "reminder": {**reminder, "user_id": "owner"}},
                 first_version, "op-date-rebase-foreign-recipient")
    with pytest.raises(ValueError, match="another user's"):
        set_date("schema-editor", {**value, "reminder": {**reminder, "preset": "minute_30", "lead": 30}},
                 first_version, "op-date-rebase-foreign-preset")
    with pytest.raises(ValueError, match="does not match"):
        set_date("content-editor", {**value, "reminder": {**reminder, "at": "2027-01-01T01:00:00.000Z"}},
                 first_version, "op-date-rebase-bad-time")
    moved = {**value, "start": "2027-01-02"}
    second = set_date("schema-editor", moved, first_version, "op-date-rebase-other-editor")
    stored = authority.read_database("content-editor", "database-e1")["rows"][0]["cells"]["due"]
    assert stored["reminder"]["at"] == "2027-01-02T00:00:00.000Z"
    assert [item["remind_at"] for item in authority.list_reminders("content-editor", workspace_id)["items"]] == [stored["reminder"]["at"]]
    assert authority.list_reminders("schema-editor", workspace_id)["items"] == []
    shifted_zone = {**moved, "time_zone": "America/New_York", "reminder": stored["reminder"]}
    set_date("schema-editor", shifted_zone, second["cells"][0]["target_version"],
             "op-date-rebase-other-editor-zone")
    zone_stored = authority.read_database("content-editor", "database-e1")["rows"][0]["cells"]["due"]
    assert zone_stored["reminder"] == {"at": "2027-01-02T14:00:00.000Z", "lead": 15,
                                       "user_id": "content-editor", "preset": "minute_15", "zone": "America/New_York"}
    assert [item["remind_at"] for item in authority.list_reminders("content-editor", workspace_id)["items"]] == [zone_stored["reminder"]["at"]]


def _rich_pages(authority: CollaborativeSearchAuthority) -> tuple[str, dict]:
    page = authority.read_document("owner", "page-e1")
    workspace_id = str(page["workspace_id"])
    authority.create_document(
        "owner",
        workspace_id,
        "page-search-target",
        title="Search target",
        blocks=[
            {
                "id": "target-body",
                "type": "paragraph",
                "content": "A target visible after source revocation",
                "props": {},
            }
        ],
        expected_workspace_epoch=int(page["workspace_epoch"]),
        operation_id="op-f0-p4-target-create-0001",
    )
    page = authority.read_document("owner", "page-e1")
    authority.save_document(
        "owner",
        "page-e1",
        title="Canonical searchable source",
        blocks=[
            {
                "id": "source-body",
                "type": "paragraph",
                "content": (
                    "SecretToken links /page/page-search-target "
                    f'<span data-notion-reminder="{REMIND_AT}" '
                    'data-notion-reminder-user="outsider" '
                    'data-notion-reminder-id="client-reminder">follow up</span>'
                ),
                "props": {
                    "nested": {
                        "id": "client-reminder",
                        "at": REMIND_AT,
                        "user_id": "outsider",
                        "lead": 15,
                    }
                },
            }
        ],
        expected_revision=int(page["revision"]),
        expected_workspace_epoch=int(page["workspace_epoch"]),
        expected_document_epoch=int(page["document_epoch"]),
        operation_id="op-f0-p4-source-save-0001",
    )
    _grant(authority, "page-e1", "outsider", "op-f0-p4-source-grant-0001")
    _grant(
        authority,
        "page-search-target",
        "outsider",
        "op-f0-p4-target-grant-0001",
    )
    return workspace_id, authority.read_document("owner", "page-e1")


def test_f0_p4_permission_aware_search_backlink_reminder_and_revoke_fence(
    tmp_path: Path,
) -> None:
    authority, _root, catalog = _authority(tmp_path)
    workspace_id, _source = _rich_pages(authority)

    found = authority.search("outsider", workspace_id, query="SecretToken")
    assert [item["document_id"] for item in found["items"]] == ["page-e1"]
    assert found["cache_policy"] == "no_store_reauthorize_every_request"
    backlinks = authority.list_backlinks(
        "outsider", workspace_id, "page-search-target"
    )
    assert [item["document_id"] for item in backlinks["items"]] == ["page-e1"]
    reminders = authority.list_reminders("outsider", workspace_id)
    assert len(reminders["items"]) == 2
    assert len({item["id"] for item in reminders["items"]}) == 2

    source = authority.read_document("owner", "page-e1")
    authority.revoke_page_grant(
        "owner",
        "page-e1",
        "subject",
        "outsider",
        expected_workspace_epoch=int(source["workspace_epoch"]),
        expected_document_epoch=int(source["document_epoch"]),
        operation_id="op-f0-p4-source-revoke-0001",
    )
    assert authority.search("outsider", workspace_id, query="SecretToken")["items"] == []
    assert authority.list_backlinks(
        "outsider", workspace_id, "page-search-target"
    )["items"] == []
    assert authority.list_reminders("outsider", workspace_id)["items"] == []
    with pytest.raises(NotFoundOrDenied):
        authority.export_markdown("outsider", "page-e1")

    con = sqlite3.connect(catalog)
    try:
        # The shared projection may retain owner-authorized source text.  There is
        # deliberately no principal-specific result/snippet cache to leak it.
        assert "SecretToken" in con.execute(
            "SELECT body_text FROM workspace_collaboration_search_documents "
            "WHERE document_id='page-e1'"
        ).fetchone()[0]
    finally:
        con.close()


def test_f0_p4_projection_integrity_restart_and_high_water_convergence(
    tmp_path: Path,
) -> None:
    authority, root, catalog = _authority(tmp_path)
    workspace_id, _source = _rich_pages(authority)
    initial = authority.search("owner", workspace_id, query="SecretToken")
    assert initial["index"]["converged"] is True

    con = sqlite3.connect(catalog)
    try:
        con.execute(
            "UPDATE workspace_collaboration_search_documents "
            "SET body_text='TamperedLeak',search_text='tamperedleak' "
            "WHERE document_id='page-e1'"
        )
        con.commit()
    finally:
        con.close()
    repaired = authority.search("owner", workspace_id, query="TamperedLeak")
    assert repaired["items"] == []
    assert authority.search("owner", workspace_id, query="SecretToken")["count"] == 1

    page = authority.read_document("owner", "page-e1")
    authority.save_document(
        "owner",
        "page-e1",
        title="Canonical searchable source",
        blocks=[
            {
                "id": "source-body",
                "type": "paragraph",
                "content": "RestartConverged links /page/page-search-target",
                "props": {},
            }
        ],
        expected_revision=int(page["revision"]),
        expected_workspace_epoch=int(page["workspace_epoch"]),
        expected_document_epoch=int(page["document_epoch"]),
        operation_id="op-f0-p4-source-save-restart-0001",
    )
    con = sqlite3.connect(catalog)
    try:
        desired, built = con.execute(
            "SELECT desired_generation,built_generation "
            "FROM workspace_collaboration_search_state WHERE workspace_id=?",
            (workspace_id,),
        ).fetchone()
        assert desired > built
    finally:
        con.close()
    restarted = CollaborativeSearchAuthority.open(
        catalog, root, InMemoryIdentityDirectory(IDENTITIES)
    )
    result = restarted.search("owner", workspace_id, query="RestartConverged")
    assert result["count"] == 1
    assert result["index"]["desired_generation"] == result["index"]["built_generation"]
    con = sqlite3.connect(catalog)
    try:
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        con.close()


@pytest.mark.integration
def test_f0_p4_l4_real_multiprocess_reauthorizes_after_revoke(tmp_path: Path) -> None:
    authority, root, catalog = _authority(tmp_path)
    workspace_id, _source = _rich_pages(authority)
    before = _search_in_fresh_process(
        catalog, root, "outsider", workspace_id, "SecretToken"
    )
    assert [item["document_id"] for item in before["items"]] == ["page-e1"]

    source = authority.read_document("owner", "page-e1")
    authority.revoke_page_grant(
        "owner",
        "page-e1",
        "subject",
        "outsider",
        expected_workspace_epoch=int(source["workspace_epoch"]),
        expected_document_epoch=int(source["document_epoch"]),
        operation_id="op-f0-p4-multiprocess-revoke-0001",
    )
    after = _search_in_fresh_process(
        catalog, root, "outsider", workspace_id, "SecretToken"
    )
    assert after["items"] == []
    assert after["cache_policy"] == "no_store_reauthorize_every_request"


def test_f0_p4_markdown_and_csv_adopted_round_trip_is_exact_and_idempotent(
    tmp_path: Path,
) -> None:
    authority, _root, _catalog = _authority(tmp_path)
    workspace_id, _source = _rich_pages(authority)
    exported = authority.export_markdown("owner", "page-e1")
    assert "code-assist-notion-snapshot-v1" in exported["markdown"]
    epoch = int(authority.read_document("owner", "page-e1")["workspace_epoch"])
    imported = authority.import_markdown(
        "owner",
        workspace_id,
        exported["markdown"],
        title="Canonical searchable source",
        expected_workspace_epoch=epoch,
        operation_id="op-f0-p4-markdown-import-0001",
    )
    replay = authority.import_markdown(
        "owner",
        workspace_id,
        exported["markdown"],
        title="Canonical searchable source",
        expected_workspace_epoch=epoch,
        operation_id="op-f0-p4-markdown-import-0001",
    )
    assert replay == imported
    imported_page = authority.read_document("owner", imported["document_id"])
    original_page = authority.read_document("owner", "page-e1")
    assert [
        {key: block[key] for key in ("type", "content", "props")}
        for block in imported_page["blocks"]
    ] == [
        {key: block[key] for key in ("type", "content", "props")}
        for block in original_page["blocks"]
    ]
    assert imported["block_id_map"]["source-body"] == imported_page["blocks"][0]["id"]
    with pytest.raises(OperationConflict):
        authority.import_markdown(
            "owner",
            workspace_id,
            exported["markdown"],
            title="Changed title",
            expected_workspace_epoch=epoch,
            operation_id="op-f0-p4-markdown-import-0001",
        )

    csv_import = authority.import_csv(
        "owner",
        workspace_id,
        "Name,Status\nAlpha,Done\nBeta,Next\n",
        title="Imported tasks",
        expected_workspace_epoch=epoch,
        operation_id="op-f0-p4-csv-import-0001",
    )
    csv_export = authority.export_csv(
        "owner",
        csv_import["document_id"],
        block_id=csv_import["block_id_map"]["import-database-1"],
    )["csv"]
    assert "Name,Status" in csv_export
    assert "Alpha,Done" in csv_export
    assert csv_import["database_restore_owned_by_F0_P5"] is True


def test_csv_export_reads_current_canonical_cells_instead_of_block_cache(tmp_path: Path) -> None:
    authority, _root, _catalog = _authority(tmp_path)
    page = authority.read_document("owner", "page-e1")
    authority.save_document(
        "owner", "page-e1", title=page["title"],
        blocks=[*page["blocks"], {
            "id": "database-e1", "position": len(page["blocks"]), "type": "table",
            "content": "Old block title", "props": {
                "database_id": "database-e1",
                "properties": [{"id": "property-e1", "name": "Old heading"}],
                "rows": [{"id": "row-e1", "property-e1": "stale cell"}],
            },
        }],
        expected_revision=page["revision"],
        expected_workspace_epoch=page["workspace_epoch"],
        expected_document_epoch=page["document_epoch"],
        operation_id="csv-export-add-canonical-block",
    )
    initial = authority.export_csv("owner", "page-e1", block_id="database-e1")["csv"]
    assert "Status" in initial and "todo" in initial
    assert "Old heading" not in initial and "stale cell" not in initial

    page = authority.read_document("content-editor", "page-e1")
    authority.execute_cell_batch(
        "content-editor", "page-e1", database_id="database-e1", target_id="database-e1",
        payload={"cells": [{"row_id": "row-e1", "property_id": "property-e1",
                            "value": "current cell", "expected_target_version": 1}]},
        expected_document_sequence=page["document_sequence"],
        expected_workspace_epoch=page["workspace_epoch"],
        expected_document_epoch=page["document_epoch"],
        operation_id="csv-export-update-canonical-cell",
    )
    latest = authority.export_csv("owner", "page-e1", block_id="database-e1")["csv"]
    assert "current cell" in latest and "stale cell" not in latest and "todo" not in latest
    _surface(authority, "database.row.create", "row-export-extra", {},
             "csv-export-create-extra-row", target_version=0)
    _schema(authority, "database.property.create", "property-export-hidden",
            {"name": "Hidden", "type": "text", "config": {}},
            "csv-export-create-hidden-property")
    _schema(authority, "database.view.create", "view-export-current",
            {"name": "Current", "type": "table", "config": {
                "filters": [{"property": "property-e1", "operator": "equals", "value": "current cell"}],
                "property_visibility": {"property-export-hidden": False},
            }}, "csv-export-create-current-view")
    current = authority.export_csv("owner", "page-e1", block_id="database-e1",
                                   view_id="view-export-current")["csv"]
    assert current == "\ufeffStatus\ncurrent cell\n"
    html_bundle = authority.export_database_html("owner", "page-e1", block_id="database-e1",
                                                 view_id="view-export-current")
    current_html = html_bundle["html"]
    assert "current cell" in current_html and "Old block title" not in current_html
    assert "Hidden" not in current_html and "Collaborative editing" not in current_html
    assert html_bundle["csv"] == "Status\r\ncurrent cell\r\n"
    assert "Hidden" in html_bundle["all_csv"]
    assert "current cell" in html_bundle["all_csv"]
    all_rows = authority.export_csv("owner", "page-e1", block_id="database-e1")["csv"]
    assert "Hidden" in all_rows and "row-export-extra" not in current
    all_html = authority.export_database_html("owner", "page-e1", block_id="database-e1")["html"]
    assert "Hidden" in all_html and "current cell" in all_html
    with pytest.raises(NotFoundOrDenied):
        authority.export_csv("owner", "page-e1", block_id="database-e1", view_id="other-database-view")
    with pytest.raises(NotFoundOrDenied):
        authority.export_database_html("owner", "page-e1", block_id="database-e1", view_id="other-database-view")
    with pytest.raises(NotFoundOrDenied):
        authority.export_csv("outsider", "page-e1", block_id="database-e1")


def test_f0_p4_search_http_contract(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    authority, root, catalog = _authority(tmp_path)
    workspace_id, _source = _rich_pages(authority)
    store = CollaborativeWorkspaceStore(
        catalog,
        user_data_root=root,
        identity_directory=InMemoryIdentityDirectory(IDENTITIES),
    )
    actor = {"id": "owner"}
    monkeypatch.setattr(routes_notion_workspace, "_store", lambda: store)
    monkeypatch.setattr(routes_notion_workspace, "_uid", lambda: actor["id"])
    monkeypatch.setattr(
        routes_notion_workspace, "_collaborative_runtime_enabled", lambda: True
    )
    app = Flask("f0-p4-search-http")
    app.config.update(TESTING=True, SECRET_KEY="f0-p4-search")
    register_notion_search_routes(app)
    client = app.test_client()
    root_url = f"/api/notion/workspaces/{workspace_id}"

    assert client.get(f"{root_url}/search?q=SecretToken").status_code == 200
    assert client.post(
        f"{root_url}/search/rebuild",
        headers={"Origin": "http://localhost"},
        json={},
    ).status_code == 200
    assert client.get(
        f"{root_url}/pages/page-search-target/backlinks"
    ).get_json()["count"] == 1
    assert client.get(f"{root_url}/reminders").status_code == 200
    markdown = client.get(f"{root_url}/pages/page-e1/export_markdown")
    assert markdown.status_code == 200
    assert "code-assist-notion-snapshot-v1" in markdown.get_data(as_text=True)
    assert client.get(f"{root_url}/pages/page-e1/export_html").status_code == 200

    epoch = int(authority.read_document("owner", "page-e1")["workspace_epoch"])
    imported_md = client.post(
        f"{root_url}/imports/markdown",
        headers={"Origin": "http://localhost"},
        json={
            "title": "HTTP Markdown",
            "markdown": "# HTTP Markdown\n\nBody",
            "parent_id": "",
            "expected_workspace_epoch": epoch,
            "operation_id": "op-f0-p4-http-md-0001",
        },
    )
    assert imported_md.status_code == 201
    imported_csv = client.post(
        f"{root_url}/imports/csv",
        headers={"Origin": "http://localhost"},
        json={
            "title": "HTTP CSV",
            "csv": "Name,Status\nAlpha,Done\n",
            "parent_id": "",
            "expected_workspace_epoch": epoch,
            "operation_id": "op-f0-p4-http-csv-0001",
        },
    )
    assert imported_csv.status_code == 201
    imported_payload = imported_csv.get_json()
    imported_id = imported_payload["document_id"]
    imported_block_id = imported_payload["block_id_map"]["import-database-1"]
    assert client.get(
        f"{root_url}/pages/{imported_id}/export_csv?block_id={imported_block_id}"
    ).status_code == 200

    actor["id"] = "outsider"
    denied = client.get(f"{root_url}/pages/page-e1/export_markdown")
    assert denied.status_code == 200  # The fixture grants this exact page.
    actor["id"] = "unknown-active-id"
    unavailable = client.get(f"{root_url}/search?q=SecretToken")
    assert unavailable.status_code in {404, 503}

    con = sqlite3.connect(catalog)
    try:
        marker = con.execute(
            "SELECT version,name,checksum,actual_schema_sha256 "
            "FROM workspace_schema_migrations ORDER BY version DESC LIMIT 1"
        ).fetchone()
    finally:
        con.close()
    assert marker == (
        migration.TARGET_VERSION,
        migration.TARGET_NAME,
        migration.TARGET_CHECKSUM,
        migration.TARGET_ACTUAL_SCHEMA_SHA256,
    )
