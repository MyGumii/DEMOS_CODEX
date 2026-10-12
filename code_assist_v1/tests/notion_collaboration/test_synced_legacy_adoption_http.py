"""UI-SYNC-LEGACY-HTTP: reviewed request, durable recovery and current owner."""
from copy import deepcopy

from code_assist_v1.notion_collaboration import synced_blocks as synced
from code_assist_v1.notion_collaboration import synced_legacy_adoption_service as service
from code_assist_v1.tests.notion_collaboration.test_page_archive_http import client_for
from code_assist_v1.tests.notion_collaboration.test_synced_legacy_adoption import (
    environment, member_environment, native_environment, archive_environment,
    guest_environment, image_environment, setup_selection, insert_legacy, endpoint, prepare,
    request, dump, text_update,
)


def base(ws):
    return f"/api/notion/workspaces/{ws}/synced-legacy"


def choice(child):
    return {"origin": endpoint("page-e1", "chosen-original"), "references": [endpoint(child, "chosen-instance")]}


def enabled(monkeypatch, store):
    capabilities = store.capabilities
    monkeypatch.setattr(store, "capabilities", lambda: {**capabilities(), "collaborative_synced_legacy_adoption": True})


def reviewed(client, ws, child, operation="legacy-http-request"):
    response = client.post(base(ws) + "/prepare", json=choice(child))
    assert response.status_code == 200
    return {**choice(child), "preparation_sha256": response.json["preparation_sha256"], "operation_id": operation}


def test_ui_legacy_http_default_capability_is_false_and_no_legacy_fallback(environment, monkeypatch):
    store, ws = environment
    child = setup_selection(store, ws)
    client, _actor = client_for(monkeypatch, store)
    selected = prepare(store, ws, child)
    body = {**choice(child), "preparation_sha256": selected.payload_sha256, "operation_id": "legacy-disabled"}
    before = dump(store)
    assert store.capabilities()["collaborative_synced_legacy_adoption"] is False
    for action in ("candidates", "prepare", "intent", "adopt", "result", "cancel"):
        data = {"anchor": body["origin"], "document_id": child} if action == "candidates" else choice(child) if action == "prepare" else body
        response = client.post(base(ws) + "/" + action, json=data)
        assert response.status_code == 503 and response.json["code"] == "collaboration_authority_unavailable"
        assert "no-store" in response.headers["Cache-Control"]
    assert client.get(base(ws) + "/requests").status_code == 503
    assert dump(store) == before


def test_ui_legacy_http_candidates_are_current_scoped_paginated_and_read_only(environment, monkeypatch):
    store, ws = environment
    child = setup_selection(store, ws)
    insert_legacy(store, child, "candidate-second", indent=0)
    insert_legacy(store, child, "candidate-divergent", content="keep my different text", indent=0)
    client, actor = client_for(monkeypatch, store)
    enabled(monkeypatch, store)
    monkeypatch.setattr(service, "CANDIDATE_PAGE_SIZE", 2)
    args = {"anchor": endpoint("page-e1", "chosen-original"), "document_id": child}
    before = dump(store)
    first = client.post(base(ws) + "/candidates", json=args)
    assert first.status_code == 200 and "no-store" in first.headers["Cache-Control"]
    assert len(first.json["items"]) == 2 and first.json["next_cursor"]
    second = client.post(base(ws) + "/candidates", json={**args, "cursor": first.json["next_cursor"]})
    assert second.status_code == 200 and len(second.json["items"]) == 1 and second.json["next_cursor"] is None
    all_items = first.json["items"] + second.json["items"]
    assert {item["endpoint"]["block_id"] for item in all_items} == {"chosen-instance", "candidate-second", "candidate-divergent"}
    assert next(item for item in all_items if item["endpoint"]["block_id"] == "candidate-divergent")["unavailable_reason"]
    assert dump(store) == before
    assert client.post(base(ws) + "/candidates", json={**args, "extra": True}).status_code == 400
    assert client.post(base(ws) + "/candidates", json=args, headers={"Origin": "https://elsewhere.example"}).status_code == 400
    actor["id"] = "outsider"
    assert client.post(base(ws) + "/candidates", json=args).status_code == 404
    actor["id"] = "owner"
    text_update(store, ws, child, "chosen-instance", "new live content")
    stable = dump(store)
    stale = client.post(base(ws) + "/candidates", json={**args, "cursor": first.json["next_cursor"]})
    assert stale.status_code == 409 and stale.json["code"] == "synced_source_changed"
    assert dump(store) == stable


def test_ui_legacy_http_same_operation_finishing_during_replay_returns_exact_receipt(environment, monkeypatch):
    store, ws = environment
    child = setup_selection(store, ws)
    client, _actor = client_for(monkeypatch, store)
    enabled(monkeypatch, store)
    body = reviewed(client, ws, child, "legacy-replay-race")
    assert client.post(base(ws) + "/intent", json=body).status_code == 201
    original = service._prepare_reviewed
    winner = []
    def completed_elsewhere(authority, actor, workspace, request_body):
        monkeypatch.setattr(service, "_prepare_reviewed", original)
        winner.append(service.adopt_legacy_selection(authority, actor, workspace, **request_body))
        return original(authority, actor, workspace, request_body)
    monkeypatch.setattr(service, "_prepare_reviewed", completed_elsewhere)
    response = client.post(base(ws) + "/adopt", json=body)
    assert response.status_code == 200 and response.json["state"] == "committed"
    assert {**response.json["result"], "replayed": False} == winner[0]["result"]


def test_ui_legacy_http_review_registration_commit_and_exact_result_after_later_edit(environment, monkeypatch):
    store, ws = environment
    child = setup_selection(store, ws)
    client, actor = client_for(monkeypatch, store)
    enabled(monkeypatch, store)
    before = dump(store)
    body = reviewed(client, ws, child)
    assert dump(store) == before, "review is read-only"
    assert client.post(base(ws) + "/adopt", json=body).status_code == 409
    assert dump(store) == before
    for action in ("prepare", "intent", "adopt", "result", "cancel"):
        args = choice(child) if action == "prepare" else body
        assert client.post(base(ws) + "/" + action, json={**args, "extra": 1}).status_code == 400
        assert client.post(base(ws) + "/" + action, json=args, headers={"Origin": "https://elsewhere.example"}).status_code == 400
    assert dump(store) == before
    assert client.post(base(ws) + "/result", json=body).json == {"state": "unknown"}
    first = client.post(base(ws) + "/intent", json=body)
    assert first.status_code == 201 and first.json["state"] == "pending"
    assert client.post(base(ws) + "/intent", json=body).status_code == 200
    pending = client.get(base(ws) + "/requests").json
    assert len(pending["items"]) == 1 and pending["items"][0]["request"] == body
    assert pending["items"][0]["state"] == "pending"
    saved = client.post(base(ws) + "/adopt", json=body)
    assert saved.status_code == 201 and saved.json["state"] == "committed"
    assert saved.json["result"]["replayed"] is False
    assert len(saved.json["result"]["documents"]) == 2
    text_update(store, ws, "page-e1", "chosen-original", "저장 이후 변경된 원본")
    stable = dump(store)
    result = client.post(base(ws) + "/result", json=body)
    assert result.status_code == 200 and result.json["state"] == "committed"
    assert {**result.json["result"], "replayed": False} == saved.json["result"]
    assert client.post(base(ws) + "/adopt", json=body).status_code == 200
    assert client.post(base(ws) + "/cancel", json=body).json["state"] == "committed"
    assert client.get(base(ws) + "/requests").json["items"][0]["state"] == "committed"
    assert dump(store) == stable
    for action in ("intent", "adopt", "result", "cancel"):
        changed = deepcopy(body)
        changed["preparation_sha256"] = "sha256:" + "a" * 64
        assert client.post(base(ws) + "/" + action, json=changed).status_code == 409
    actor["id"] = "outsider"
    for action in ("intent", "adopt", "result", "cancel"):
        assert client.post(base(ws) + "/" + action, json=body).status_code == 404
    assert client.get(base(ws) + "/requests").status_code == 404
    assert dump(store) == stable


def test_ui_legacy_http_stale_review_keeps_intent_until_explicit_cancel(environment, monkeypatch):
    store, ws = environment
    child = setup_selection(store, ws)
    client, _actor = client_for(monkeypatch, store)
    enabled(monkeypatch, store)
    body = reviewed(client, ws, child)
    assert client.post(base(ws) + "/intent", json=body).status_code == 201
    store.iam.execute_typed_command("owner", "page-e1", command_kind="block.insert", target_id="after-review-note",
        payload={"type": "paragraph", "content": "preserve new note", "after_block_id": None},
        operation_id="legacy-http-changed-note", **request(store))
    stable = dump(store)
    failed = client.post(base(ws) + "/adopt", json=body)
    assert failed.status_code == 409 and failed.json["code"] == "synced_source_changed"
    assert dump(store) == stable
    corrected = reviewed(client, ws, child, operation="legacy-http-corrected")
    assert client.post(base(ws) + "/intent", json=corrected).status_code == 409
    assert client.get(base(ws) + "/requests").json["items"][0]["state"] == "pending"
    cancelled = client.post(base(ws) + "/cancel", json=body)
    assert cancelled.status_code == 201 and cancelled.json["state"] == "cancelled"
    terminal = dump(store)
    assert client.post(base(ws) + "/adopt", json=body).json["state"] == "cancelled"
    assert client.post(base(ws) + "/intent", json=body).json["state"] == "cancelled"
    assert dump(store) == terminal
    assert client.post(base(ws) + "/intent", json=corrected).status_code == 201
    assert client.post(base(ws) + "/adopt", json=corrected).status_code == 201
    assert synced.read_synced_block(store.iam, "owner", child, "chosen-instance")["block"]["content"] == "조합된 한글 A"


def test_ui_legacy_http_new_client_discovers_pending_and_terminal_without_local_request(environment, monkeypatch):
    store, ws = environment
    child = setup_selection(store, ws)
    client, _actor = client_for(monkeypatch, store)
    enabled(monkeypatch, store)
    body = reviewed(client, ws, child)
    assert client.post(base(ws) + "/intent", json=body).status_code == 201
    fresh = client.application.test_client()
    history = fresh.get(base(ws) + "/requests")
    assert history.status_code == 200 and "no-store" in history.headers["Cache-Control"]
    discovered = history.json["items"][0]
    assert discovered["state"] == "pending" and discovered["request"] == body
    assert fresh.post(base(ws) + "/cancel", json=discovered["request"]).status_code == 201
    terminal = client.get(base(ws) + "/requests").json["items"][0]
    assert terminal["state"] == "cancelled" and terminal["request"] == body
    assert client.get(base(ws) + "/requests?limit=0").status_code == 400
    assert client.get(base(ws) + "/requests?cursor=invalid").status_code == 400
    assert client.get(base(ws) + "/requests?unexpected=1").status_code == 400


def test_ui_legacy_http_history_pagination_scope_and_pending_group_exclusion(environment, monkeypatch):
    store, ws = environment
    child = setup_selection(store, ws)
    insert_legacy(store, "page-e1", "another-group", sync_id="legacy-group-B")
    client, _actor = client_for(monkeypatch, store)
    enabled(monkeypatch, store)
    first = reviewed(client, ws, child, operation="legacy-pagination-A")
    other_choice = {"origin": endpoint("page-e1", "another-group"), "references": []}
    other_review = client.post(base(ws) + "/prepare", json=other_choice).json
    second = {**other_choice, "preparation_sha256": other_review["preparation_sha256"], "operation_id": "legacy-pagination-B"}
    assert client.post(base(ws) + "/intent", json=first).status_code == 201
    overlapping = {**first, "operation_id": "legacy-pagination-overlap"}
    assert client.post(base(ws) + "/intent", json=overlapping).status_code == 409
    assert client.post(base(ws) + "/intent", json=second).status_code == 201
    page = client.get(base(ws) + "/requests?limit=1").json
    assert len(page["items"]) == 1 and page["next_cursor"]
    following = client.get(base(ws) + "/requests", query_string={"limit": 1, "cursor": page["next_cursor"]}).json
    assert len(following["items"]) == 1 and following["next_cursor"] is None
    assert {page["items"][0]["operation_id"], following["items"][0]["operation_id"]} == {first["operation_id"], second["operation_id"]}
    assert client.get(base("other-workspace") + "/requests", query_string={"cursor": page["next_cursor"]}).status_code == 400


def test_ui_legacy_http_late_failure_leaves_only_intent_and_exact_retry_commits_once(environment, monkeypatch):
    store, ws = environment
    child = setup_selection(store, ws)
    client, _actor = client_for(monkeypatch, store)
    enabled(monkeypatch, store)
    body = reviewed(client, ws, child)
    assert client.post(base(ws) + "/intent", json=body).status_code == 201
    before = dump(store)
    actual = service.commit.commit_prepared_adoption
    with monkeypatch.context() as fail:
        def late(authority, prepared, **kwargs):
            return actual(authority, prepared, **{**kwargs, "fault": "after_outbox"})
        fail.setattr(service.commit, "commit_prepared_adoption", late)
        assert client.post(base(ws) + "/adopt", json=body).status_code == 500
    assert dump(store) == before
    assert client.post(base(ws) + "/result", json=body).json == {"state": "unknown"}
    assert client.get(base(ws) + "/requests").json["items"][0]["state"] == "pending"
    assert client.post(base(ws) + "/adopt", json=body).status_code == 201
    stable = dump(store)
    assert client.post(base(ws) + "/adopt", json=body).status_code == 200
    assert dump(store) == stable


def test_ui_legacy_http_source_change_during_delta_cannot_partially_write(environment, monkeypatch):
    store, ws = environment
    child = setup_selection(store, ws)
    client, _actor = client_for(monkeypatch, store)
    enabled(monkeypatch, store)
    body = reviewed(client, ws, child)
    assert client.post(base(ws) + "/intent", json=body).status_code == 201
    actual = service.commit._prepare_updates
    after = []
    def changed(selected):
        updates = actual(selected)
        store.iam.execute_typed_command("owner", child, command_kind="block.insert", target_id="during-legacy-note",
            payload={"type": "paragraph", "content": "preserve concurrent note", "after_block_id": None},
            operation_id="legacy-http-note-during-delta", **request(store, child))
        after.extend(dump(store))
        return updates
    monkeypatch.setattr(service.commit, "_prepare_updates", changed)
    result = client.post(base(ws) + "/adopt", json=body)
    assert result.status_code == 409 and result.json["code"] == "synced_source_changed"
    assert dump(store) == tuple(after)
    assert client.get(base(ws) + "/requests").json["items"][0]["state"] == "pending"
