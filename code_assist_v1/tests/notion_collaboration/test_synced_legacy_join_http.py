"""UI-SYNC-LEGACY-JOIN-HTTP: known original candidates, old receipts and recovery."""
import json

from code_assist_v1.notion_collaboration import synced_blocks as synced
from code_assist_v1.notion_collaboration import synced_legacy_adoption_commit as commit
from code_assist_v1.notion_collaboration.canonical_authority import _canonical_json
from code_assist_v1.tests.notion_collaboration.test_synced_legacy_adoption_http import client_for, enabled, base, reviewed
from code_assist_v1.tests.notion_collaboration.test_synced_legacy_join import (
    environment, member_environment, native_environment, archive_environment,
    guest_environment, image_environment, insert_legacy, setup_selection, endpoint,
    dump, text_update, create,
)


def started(store, ws, monkeypatch):
    child = setup_selection(store, ws)
    client, actor = client_for(monkeypatch, store)
    enabled(monkeypatch, store)
    body = reviewed(client, ws, child, operation="legacy-http-first-original")
    assert client.post(base(ws) + "/intent", json=body).status_code == 201
    response = client.post(base(ws) + "/adopt", json=body)
    assert response.status_code == 201
    later = create(store, ws, operation="legacy-http-later-page")["page"]["id"]
    insert_legacy(store, later, "later-member")
    return client, actor, child, later, response.json


def joined_request(client, ws, later):
    choice = {"origin": endpoint("page-e1", "chosen-original"), "references": [endpoint(later, "later-member")]}
    response = client.post(base(ws) + "/prepare", json=choice)
    assert response.status_code == 200 and response.json["original"]["already_designated"] is True
    return {**choice, "preparation_sha256": response.json["preparation_sha256"], "operation_id": "legacy-http-existing-original-join"}


def test_ui_legacy_join_http_candidates_require_committed_mapping_and_keep_the_same_group(environment, monkeypatch):
    store, ws = environment
    client, actor, child, later, first = started(store, ws, monkeypatch)
    before = dump(store)
    candidates = client.post(base(ws) + "/candidates", json={"anchor": endpoint(later, "later-member"), "document_id": "page-e1"})
    assert candidates.status_code == 200
    assert candidates.json["items"] == [{"endpoint": endpoint("page-e1", "chosen-original"),
        "content_preview": "조합된 한글 A", "unavailable_reason": "", "original_only": True}]
    body = joined_request(client, ws, later)
    assert dump(store) == before
    original_before = store.iam.read_document("owner", "page-e1")
    assert client.post(base(ws) + "/intent", json=body).status_code == 201
    saved = client.post(base(ws) + "/adopt", json=body)
    assert saved.status_code == 201 and saved.json["result"]["sync_id"] == first["result"]["sync_id"]
    assert store.iam.read_document("owner", "page-e1") == original_before
    assert len(client.get(base(ws) + "/requests").json["items"]) == 2
    stable = dump(store)
    assert client.post(base(ws) + "/result", json=body).json["state"] == "committed"
    assert client.post(base(ws) + "/adopt", json=body).status_code == 200
    assert client.post(base(ws) + "/cancel", json=body).json["state"] == "committed"
    actor["id"] = "outsider"
    assert client.post(base(ws) + "/candidates", json={"anchor": endpoint(later, "later-member"), "document_id": "page-e1"}).status_code == 404
    assert client.post(base(ws) + "/result", json=body).status_code == 404
    assert dump(store) == stable


def test_ui_legacy_join_http_older_receipt_uses_its_exact_committed_intent_without_backfill(environment, monkeypatch):
    store, ws = environment
    # A sanitized historical fixture representing the prior HTTP packet, not
    # operating migration/backfill: old terminal had no legacy_sync_id field.
    # Write it once in the older shape; immutable operation rows are not edited.
    finish = store.iam._finish
    def previous_packet(con, **values):
        if values.get("action") == commit.ACTION:
            result = dict(values["result"]); result.pop("legacy_sync_id", None)
            values = {**values, "result": result, "event_payload": result}
        return finish(con, **values)
    monkeypatch.setattr(store.iam, "_finish", previous_packet)
    client, _actor, _child, later, first = started(store, ws, monkeypatch)
    monkeypatch.setattr(store.iam, "_finish", finish)
    assert "legacy_sync_id" not in first["result"]
    before = dump(store)
    body = joined_request(client, ws, later)
    assert dump(store) == before, "no old ledger backfill while reading provenance"
    assert client.post(base(ws) + "/intent", json=body).status_code == 201
    assert client.post(base(ws) + "/adopt", json=body).status_code == 201
    assert synced.read_synced_block(store.iam, "owner", later, "later-member")["origin"] == endpoint("page-e1", "chosen-original")


def test_ui_legacy_join_http_changed_source_cannot_overwrite_a_later_member_and_cancellation_keeps_original(environment, monkeypatch):
    store, ws = environment
    client, _actor, _child, later, _first = started(store, ws, monkeypatch)
    body = joined_request(client, ws, later)
    assert client.post(base(ws) + "/intent", json=body).status_code == 201
    text_update(store, ws, "page-e1", "chosen-original", "changed current original")
    before = dump(store)
    rejected = client.post(base(ws) + "/adopt", json=body)
    assert rejected.status_code == 409 and rejected.json["code"] == "synced_source_changed"
    assert dump(store) == before
    candidate = client.post(base(ws) + "/candidates", json={"anchor": endpoint(later, "later-member"), "document_id": "page-e1"})
    assert candidate.status_code == 200 and candidate.json["items"][0]["unavailable_reason"]
    source_before = store.iam.read_document("owner", "page-e1")
    target_before = store.iam.read_document("owner", later)
    assert client.post(base(ws) + "/cancel", json=body).json["state"] == "cancelled"
    assert client.post(base(ws) + "/adopt", json=body).json["state"] == "cancelled"
    assert store.iam.read_document("owner", "page-e1") == source_before
    assert store.iam.read_document("owner", later) == target_before


def test_ui_legacy_join_http_unrelated_explicit_original_is_not_a_candidate(environment, monkeypatch):
    store, ws = environment
    client, _actor, _child, later, _first = started(store, ws, monkeypatch)
    insert_legacy(store, later, "unrelated-explicit-original", synced_from=None,
        sync_id=synced.group_id(later, "unrelated-explicit-original"))
    before = dump(store)
    response = client.post(base(ws) + "/candidates", json={"anchor": endpoint(later, "later-member"), "document_id": later})
    assert response.status_code == 200
    assert [item["endpoint"]["block_id"] for item in response.json["items"]] == ["later-member"]
    choice = {"origin": endpoint(later, "unrelated-explicit-original"), "references": [endpoint(later, "later-member")]}
    assert client.post(base(ws) + "/prepare", json=choice).status_code == 422
    assert dump(store) == before
