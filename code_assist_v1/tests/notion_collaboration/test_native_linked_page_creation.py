"""Creating a page around an existing data source keeps one canonical database."""
import pytest

from code_assist_v1.notion_collaboration import linked_databases, page_archive, page_creation
from code_assist_v1.notion_collaboration.canonical_authority import InjectedFailure, NotFoundOrDenied, OperationConflict
from code_assist_v1.tests.notion_collaboration.test_member_navigation import (
    environment, native_environment, archive_environment, guest_environment, image_environment, dump,
)
from code_assist_v1.tests.notion_collaboration.test_native_page_creation_http import client_for
from code_assist_v1.tests.notion_collaboration.test_page_archive import request as archive_request


def request(store, workspace, operation="native-linked-page"):
    return dict(title="연결된 표", template="linked_database", source_document_id="page-e1",
        source_database_id="database-e1", base_version=store.get_workspace("owner", workspace)["version"],
        operation_id=operation)


def test_linked_page_creation_is_atomic_and_replays_without_copying_source(environment):
    store, workspace = environment
    original = store.iam.read_database("owner", "database-e1")
    options = request(store, workspace)
    created = page_creation.create_page(store.iam, "owner", workspace, **options)
    page_id = created["page"]["id"]
    document = store.iam.read_document("owner", page_id)
    assert len(document["blocks"]) == 1
    block = document["blocks"][0]
    assert linked_databases.source_of(block) == ("page-e1", "database-e1")
    assert linked_databases.read_linked_database(store.iam, "owner", page_id, block["id"])["source"]["database_id"] == "database-e1"
    assert store.iam.read_database("owner", "database-e1") == original
    with store.iam._reader() as con:
        assert con.execute("SELECT COUNT(*) FROM workspace_collaboration_databases WHERE document_id=?", (page_id,)).fetchone()[0] == 0
    committed = dump(store)
    replay = page_creation.create_page(store.iam, "owner", workspace, **options)
    assert replay["replayed"] and replay["page"]["id"] == page_id and dump(store) == committed
    with pytest.raises(OperationConflict):
        page_creation.create_page(store.iam, "owner", workspace, **{**options, "source_database_id":"other-db"})


def test_linked_page_creation_denies_missing_or_mismatched_source_without_effect(environment):
    store, workspace = environment
    before = dump(store)
    for changed in ({"source_document_id":"missing-page"}, {"source_database_id":"missing-db"}):
        with pytest.raises(NotFoundOrDenied):
            page_creation.create_page(store.iam, "owner", workspace,
                **{**request(store, workspace, "missing-linked-source"), **changed})
        assert dump(store) == before
    with pytest.raises(ValueError):
        page_creation.create_page(store.iam, "owner", workspace,
            **{**request(store, workspace), "template":"database"})
    assert dump(store) == before
    with pytest.raises(InjectedFailure):
        page_creation.create_page(store.iam, "owner", workspace,
            **request(store, workspace, "linked-fault"), fault="after_outbox")
    assert dump(store) == before


def test_linked_page_creation_denies_archived_source_without_orphan(environment):
    store, workspace = environment
    page_archive.set_page_archived(store.iam, "owner", workspace, "page-e1",
        **archive_request(store, workspace, "page-e1", operation="linked-source-archive"))
    before = dump(store)
    with pytest.raises(NotFoundOrDenied):
        page_creation.create_page(store.iam, "owner", workspace,
            **request(store, workspace, "archived-linked-source"))
    assert dump(store) == before


def test_linked_page_creation_http_contract(environment, monkeypatch):
    store, workspace = environment
    client = client_for(store, monkeypatch)
    path = f"/api/notion/workspaces/{workspace}/pages"
    body = request(store, workspace, "http-linked-page")
    created = client.post(path, json={**body, "parent_id":""})
    assert created.status_code == 201, created.json
    replay = client.post(path, json={**body, "parent_id":""})
    assert replay.status_code == 200 and replay.json["replayed"]
    missing = client.post(path, json={**request(store, workspace, "http-linked-missing"),
        "parent_id":"", "source_database_id":"missing-db"})
    assert missing.status_code == 404
    invalid = client.post(path, json={**request(store, workspace, "http-linked-invalid"),
        "parent_id":"", "template":"database"})
    assert invalid.status_code == 400
    id_only = client.post(path, json={key: value for key, value in
        {**request(store, workspace, "http-linked-id-only"), "parent_id": ""}.items()
        if key != "source_document_id"})
    assert id_only.status_code == 400
