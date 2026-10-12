"""UI-PAGE-SETTINGS: metadata-only writes, current roles and durable live events."""
import json
from dataclasses import replace
import sqlite3
import subprocess
import sys
import time
from types import SimpleNamespace

from flask import Flask
import pytest

from code_assist_v1 import routes_notion_workspace as routes
from code_assist_v1 import routes_notion
from code_assist_v1.notion_collaboration.canonical_authority import InjectedFailure, NotFoundOrDenied, OperationConflict, bind_temporary_unlock
from code_assist_v1.notion_collaboration.editing_authority import DocumentSequenceConflict
from code_assist_v1.notion_collaboration.durable_transport import YJS_UPDATE_ENCODING
from code_assist_v1.notion_collaboration.page_settings import current_page_lock_generation, update_page_settings
from code_assist_v1.notion_collaboration.page_appearance import project_page_metadata
from code_assist_v1.notion_collaboration.iam_runtime import collaborative_page_payload
from code_assist_v1.notion_collaboration.protocol_contract import ProtocolEnvelope, session_binding_sha256
from code_assist_v1.tests.notion_collaboration.test_collaborative_editing_authority import _environment
from code_assist_v1.tests.notion_collaboration.test_page_appearance import snapshot


def request(authority, updates=None, operation="page-settings-1"):
    page = authority.read_document("owner", "page-e1")
    return dict(updates=updates or {"font": "serif", "small_text": True, "full_width": True},
        operation_id=operation, expected_document_sequence=page["document_sequence"],
        expected_workspace_epoch=page["workspace_epoch"], expected_document_epoch=page["document_epoch"])


def test_ui_page_settings_metadata_only_replay_and_live_delivery(tmp_path):
    authority, transport, _, catalog, ws = _environment(tmp_path)
    before = snapshot(catalog)
    body = authority.read_document("owner", "page-e1")
    req = request(authority)
    saved = update_page_settings(authority, "schema-editor", ws, "page-e1", **req)
    assert saved == update_page_settings(authority, "schema-editor", ws, "page-e1", **req)
    after = authority.read_document("owner", "page-e1")
    assert after["settings"] == req["updates"]
    assert after["blocks"] == body["blocks"] and after["revision"] == body["revision"]
    assert after["document_sequence"] == body["document_sequence"] + 1
    changed = {"workspace_collaboration_documents", "workspace_collaboration_events", "workspace_collaboration_audit_events",
               "workspace_collaboration_outbox", "workspace_collaboration_operations"}
    assert {k: v for k, v in snapshot(catalog).items() if k not in changed} == {k: v for k, v in before.items() if k not in changed}
    replay = transport.canonical_authority().replay_domain_events("content-editor", "page-e1",
        after_document_sequence=req["expected_document_sequence"])
    assert [event.event_type for event in replay.events] == ["page.settings.updated"]
    update_page_settings(authority, "owner", ws, "page-e1", **request(authority, {"font": "mono"}, "next-font"))
    assert saved == update_page_settings(authority, "schema-editor", ws, "page-e1", **req)
    with pytest.raises(OperationConflict):
        update_page_settings(authority, "schema-editor", ws, "page-e1", **{**req, "updates": {"font": "default"}})


def test_ui_page_outline_setting_persists_with_current_settings_authority(tmp_path):
    authority, _, _, _catalog, ws = _environment(tmp_path)
    before = authority.read_document("owner", "page-e1")
    off = update_page_settings(authority, "schema-editor", ws, "page-e1",
        **request(authority, {"show_outline": False}, "outline-off"))
    assert off["page"]["settings"]["show_outline"] is False
    after = authority.read_document("owner", "page-e1")
    assert after["settings"]["show_outline"] is False
    assert after["blocks"] == before["blocks"]
    assert after["document_sequence"] == before["document_sequence"] + 1
    with pytest.raises(ValueError, match="boolean"):
        update_page_settings(authority, "schema-editor", ws, "page-e1",
            **request(authority, {"show_outline": "false"}, "outline-invalid"))
    on = update_page_settings(authority, "schema-editor", ws, "page-e1",
        **request(authority, {"show_outline": True}, "outline-on"))
    assert on["page"]["settings"]["show_outline"] is True


def test_ui_page_customize_comment_display_settings_are_validated_and_preserve_body(tmp_path):
    authority, _, _, _catalog, ws = _environment(tmp_path)
    before = authority.read_document("owner", "page-e1")
    saved = update_page_settings(authority, "schema-editor", ws, "page-e1",
        **request(authority, {"show_page_comments": False, "inline_comments": "minimized"}, "comment-display"))
    assert saved["page"]["settings"]["show_page_comments"] is False
    assert saved["page"]["settings"]["inline_comments"] == "minimized"
    after = authority.read_document("owner", "page-e1")
    assert after["blocks"] == before["blocks"] and after["revision"] == before["revision"]
    for invalid in ({"show_page_comments": "false"}, {"inline_comments": "hidden"}):
        with pytest.raises(ValueError):
            update_page_settings(authority, "schema-editor", ws, "page-e1",
                **request(authority, invalid, f"invalid-{next(iter(invalid))}"))


def test_ui_page_settings_role_lock_unlock_and_current_authority(tmp_path):
    authority, _, _, catalog, ws = _environment(tmp_path)
    req = request(authority, {"locked": True})
    before = snapshot(catalog)
    with pytest.raises(NotFoundOrDenied):
        update_page_settings(authority, "content-editor", ws, "page-e1", **req)
    assert snapshot(catalog) == before
    locked = update_page_settings(authority, "schema-editor", ws, "page-e1", **req)
    assert locked["page"]["can_change_settings"] and not locked["page"]["can_change_appearance"]
    assert not locked["page"]["can_move"]
    assert update_page_settings(authority, "schema-editor", ws, "page-e1", **req) == locked
    with pytest.raises(PermissionError):
        update_page_settings(authority, "owner", ws, "page-e1", **request(authority, {"font": "mono"}, "locked-font"))
    unlocked = update_page_settings(authority, "schema-editor", ws, "page-e1",
        **request(authority, {"locked": False}, "unlock-page"))
    assert unlocked["page"]["settings"]["locked"] is False and unlocked["page"]["can_change_appearance"]
    page = authority.read_document("owner", "page-e1")
    authority.revoke_page_grant("owner", "page-e1", "subject", "schema-editor",
        expected_workspace_epoch=page["workspace_epoch"], expected_document_epoch=page["document_epoch"], operation_id="settings-revoke")
    with pytest.raises(NotFoundOrDenied):
        update_page_settings(authority, "schema-editor", ws, "page-e1", **req)


def test_ui_page_lock_generation_changes_only_after_global_relock(tmp_path):
    authority, _, _, _catalog, ws = _environment(tmp_path)
    with pytest.raises(ValueError, match="page is not locked"):
        current_page_lock_generation(authority, "owner", ws, "page-e1")
    update_page_settings(authority, "schema-editor", ws, "page-e1",
                         **request(authority, {"locked": True}, "generation-first-lock"))
    first = current_page_lock_generation(authority, "schema-editor", ws, "page-e1")
    assert first == current_page_lock_generation(authority, "owner", ws, "page-e1")
    assert authority.read_document("owner", "page-e1", include_lock_generation=True)["lock_generation"] == first
    assert collaborative_page_payload(authority, "owner", "page-e1")["page"]["lock_generation"] == first
    assert project_page_metadata(authority, "owner", ws, [{"id": "page-e1"}])[0]["lock_generation"] == first
    with pytest.raises(NotFoundOrDenied):
        current_page_lock_generation(authority, "content-editor", ws, "page-e1")

    update_page_settings(authority, "schema-editor", ws, "page-e1",
                         **request(authority, {"locked": False}, "generation-global-unlock"))
    update_page_settings(authority, "schema-editor", ws, "page-e1",
                         **request(authority, {"font": "mono"}, "generation-unrelated-setting"))
    update_page_settings(authority, "schema-editor", ws, "page-e1",
                         **request(authority, {"locked": True}, "generation-second-lock"))
    second = current_page_lock_generation(authority, "owner", ws, "page-e1")
    assert second != first
    assert collaborative_page_payload(authority, "owner", "page-e1")["page"]["lock_generation"] == second
    assert project_page_metadata(authority, "owner", ws, [{"id": "page-e1"}])[0]["lock_generation"] == second

    page = authority.read_document("owner", "page-e1")
    with bind_temporary_unlock({
        "user_id": "owner", "workspace_id": ws, "page_id": "page-e1",
        "lock_generation": second, "expires_at_ms": int(time.time() * 1000) + 60_000,
    }):
        authority.save_document(
            "owner", "page-e1", title=page["title"], blocks=page["blocks"],
            settings={**page["settings"], "locked": False},
            expected_revision=page["revision"], expected_workspace_epoch=page["workspace_epoch"],
            expected_document_epoch=page["document_epoch"], operation_id="generation-legacy-unlock",
        )
    page = authority.read_document("owner", "page-e1")
    authority.save_document(
        "owner", "page-e1", title=page["title"], blocks=page["blocks"],
        settings={**page["settings"], "locked": True},
        expected_revision=page["revision"], expected_workspace_epoch=page["workspace_epoch"],
        expected_document_epoch=page["document_epoch"], operation_id="generation-legacy-relock",
    )
    assert current_page_lock_generation(authority, "owner", ws, "page-e1") != second

    page = authority.read_document("owner", "page-e1")
    authority.create_document(
        "owner", ws, "initially-locked-page", title="Initially locked", blocks=[],
        settings={"locked": True}, expected_workspace_epoch=page["workspace_epoch"],
        operation_id="generation-initial-lock",
    )
    assert current_page_lock_generation(authority, "owner", ws, "initially-locked-page")


def test_ui_page_lock_rejects_canonical_body_structure_and_live_writes(tmp_path):
    authority, transport, _, catalog, ws = _environment(tmp_path)
    session = session_binding_sha256("locked-page-session")
    issued = transport.issue_ticket(
        csrf_valid=True, subject_id="owner", workspace_id=ws, document_id="page-e1",
        client_id="locked-page-client", session_binding_sha256=session,
        origin="http://127.0.0.1:10112", capabilities=("read", "write"), now_ms=3_000,
    )
    grant = transport.consume_ticket(
        issued.token, origin="http://127.0.0.1:10112", subject_id="owner",
        workspace_id=ws, document_id="page-e1", client_id="locked-page-client",
        session_binding_sha256=session, now_ms=3_001,
    )
    update_page_settings(authority, "schema-editor", ws, "page-e1",
                         **request(authority, {"locked": True}, "lock-live-page"))
    page = authority.read_document("owner", "page-e1")
    before = snapshot(catalog)

    with pytest.raises(PermissionError, match="page is locked"):
        authority.save_document(
            "owner", "page-e1", title="Bypassed lock", blocks=page["blocks"],
            settings=page["settings"], expected_revision=page["revision"],
            expected_workspace_epoch=page["workspace_epoch"],
            expected_document_epoch=page["document_epoch"], operation_id="locked-body-save",
        )
    with pytest.raises(PermissionError, match="page is locked"):
        authority.execute_typed_command(
            "owner", "page-e1", command_kind="block.insert", target_id="locked-block",
            payload={"type": "paragraph", "content": "bypass", "after_block_id": "block-a"},
            expected_document_sequence=page["document_sequence"],
            expected_workspace_epoch=page["workspace_epoch"],
            expected_document_epoch=page["document_epoch"], operation_id="locked-typed-command",
        )
    envelope = ProtocolEnvelope.create(
        grant, message_type="crdt.update", operation_id="locked-live-update",
        client_sequence=1, acknowledgement_sequence=page["document_sequence"],
        payload=b"opaque-locked-page-update", capabilities=("write",),
        payload_encoding=YJS_UPDATE_ENCODING,
    )
    with pytest.raises(PermissionError, match="page is locked"):
        transport.accept_update(envelope, grant)
    assert snapshot(catalog) == before


def test_ui_page_temporary_unlock_live_ticket_rechecks_global_relock(tmp_path):
    authority, transport, _, _catalog, ws = _environment(tmp_path)
    update_page_settings(authority, "owner", ws, "page-e1",
                         **request(authority, {"locked": True}, "live-temporary-lock"))
    page = authority.read_document("owner", "page-e1")
    now_ms = int(time.time() * 1000)
    common = dict(csrf_valid=True, subject_id="owner", workspace_id=ws, document_id="page-e1",
                  session_binding_sha256=session_binding_sha256("live-temporary-session"),
                  origin="http://127.0.0.1:10112", capabilities=("read", "presence", "comment"))
    without = transport.issue_ticket(**common, client_id="live-temporary-no-grant",
                                     now_ms=now_ms, include_temporary_write=True)
    assert "write" not in without.claims.capabilities

    with bind_temporary_unlock({
        "user_id": "owner", "workspace_id": ws, "page_id": "page-e1",
        "lock_generation": current_page_lock_generation(authority, "owner", ws, "page-e1"),
        "expires_at_ms": now_ms + 60_000,
    }):
        issued = transport.issue_ticket(**common, client_id="live-temporary-granted",
                                        now_ms=now_ms, include_temporary_write=True)
    assert "write" in issued.claims.capabilities
    grant = transport.consume_ticket(
        issued.token, origin=common["origin"], subject_id="owner", workspace_id=ws,
        document_id="page-e1", client_id="live-temporary-granted",
        session_binding_sha256=common["session_binding_sha256"], now_ms=now_ms + 1,
    )
    assert grant.temporary_unlock is not None
    first = ProtocolEnvelope.create(
        grant, message_type="crdt.update", operation_id="live-temporary-first-update",
        client_sequence=1, acknowledgement_sequence=page["document_sequence"],
        payload=b"temporary-live-update", capabilities=("write",),
        payload_encoding=YJS_UPDATE_ENCODING,
    )
    expired = replace(grant, temporary_unlock={
        **grant.temporary_unlock, "expires_at_ms": now_ms - 1,
    })
    with pytest.raises(PermissionError, match="page is locked"):
        transport.accept_update(first, expired)
    assert authority.read_document("owner", "page-e1")["document_sequence"] == page["document_sequence"]
    ack = transport.accept_update(first, grant)
    assert ack.document_sequence == page["document_sequence"] + 1

    update_page_settings(authority, "owner", ws, "page-e1",
                         **request(authority, {"locked": False}, "live-temporary-global-unlock"))
    update_page_settings(authority, "owner", ws, "page-e1",
                         **request(authority, {"locked": True}, "live-temporary-global-relock"))
    after_relock = authority.read_document("owner", "page-e1")
    second = ProtocolEnvelope.create(
        grant, message_type="crdt.update", operation_id="live-temporary-stale-update",
        client_sequence=2, acknowledgement_sequence=after_relock["document_sequence"],
        payload=b"must-not-commit", capabilities=("write",),
        payload_encoding=YJS_UPDATE_ENCODING,
    )
    with pytest.raises(PermissionError, match="page is locked"):
        transport.accept_update(second, grant)
    assert authority.read_document("owner", "page-e1")["document_sequence"] == after_relock["document_sequence"]


def test_ui_page_temporary_unlock_live_ticket_survives_cross_process_handoff(tmp_path):
    authority, transport, root, catalog, ws = _environment(tmp_path)
    update_page_settings(authority, "owner", ws, "page-e1",
                         **request(authority, {"locked": True}, "cross-process-temporary-lock"))
    page = authority.read_document("owner", "page-e1")
    now_ms = int(time.time() * 1000)
    binding = session_binding_sha256("cross-process-temporary-session")
    with bind_temporary_unlock({
        "user_id": "owner", "workspace_id": ws, "page_id": "page-e1",
        "lock_generation": current_page_lock_generation(authority, "owner", ws, "page-e1"),
        "expires_at_ms": now_ms + 60_000,
    }):
        issued = transport.issue_ticket(
            csrf_valid=True, subject_id="owner", workspace_id=ws, document_id="page-e1",
            client_id="cross-process-temporary-client", session_binding_sha256=binding,
            origin="http://127.0.0.1:10112", capabilities=("read", "presence"),
            now_ms=now_ms, include_temporary_write=True,
        )
    script = r'''
import contextlib,sys
with contextlib.redirect_stdout(sys.stderr):
    from code_assist_v1.notion_collaboration.durable_transport import YJS_UPDATE_ENCODING
    from code_assist_v1.notion_collaboration.editing_authority import CollaborativeEditingTransportAuthority
    from code_assist_v1.notion_collaboration.protocol_contract import ProtocolEnvelope
transport=CollaborativeEditingTransportAuthority.open(sys.argv[1],sys.argv[2])
grant=transport.consume_ticket(sys.argv[3],origin='http://127.0.0.1:10112',subject_id='owner',
    workspace_id=sys.argv[4],document_id='page-e1',client_id='cross-process-temporary-client',
    session_binding_sha256=sys.argv[7],now_ms=int(sys.argv[6])+1)
assert grant.allows('write') and grant.temporary_unlock is not None
envelope=ProtocolEnvelope.create(grant,message_type='crdt.update',operation_id='cross-process-temporary-update',
    client_sequence=1,acknowledgement_sequence=int(sys.argv[5]),payload=b'cross-process-update',
    capabilities=('write',),payload_encoding=YJS_UPDATE_ENCODING)
print(transport.accept_update(envelope,grant).document_sequence)
'''
    child = subprocess.run(
        [sys.executable, "-c", script, str(catalog), str(root), issued.token, ws,
         str(page["document_sequence"]), str(now_ms), binding],
        cwd=str(__import__("pathlib").Path(__file__).resolve().parents[3]),
        capture_output=True, text=True, timeout=40,
    )
    assert child.returncode == 0, child.stderr
    assert int(child.stdout.strip()) == page["document_sequence"] + 1
    assert authority.read_document("owner", "page-e1")["document_sequence"] == page["document_sequence"] + 1


def test_ui_page_settings_validation_cas_and_atomic_rollback(tmp_path):
    authority, _, _, catalog, ws = _environment(tmp_path)
    req = request(authority)
    before = snapshot(catalog)
    for updates in ({}, {"font": "Comic Sans"}, {"full_width": 1}, {"small_text": "true"}, {"locked": None}, {"title": "wrong field"}):
        with pytest.raises(ValueError):
            update_page_settings(authority, "owner", ws, "page-e1", **{**req, "updates": updates})
    with pytest.raises(NotFoundOrDenied):
        update_page_settings(authority, "owner", "ws-wrong", "page-e1", **req)
    with pytest.raises(DocumentSequenceConflict):
        update_page_settings(authority, "owner", ws, "page-e1", **{**req, "expected_document_sequence": 999})
    for fault in ("after_domain", "after_event", "after_audit", "after_outbox"):
        with pytest.raises(InjectedFailure):
            update_page_settings(authority, "owner", ws, "page-e1", **req, fault=fault)
        assert snapshot(catalog) == before
    with sqlite3.connect(catalog) as con:
        con.execute("UPDATE workspace_collaboration_documents SET archived=1 WHERE id='page-e1'")
    with pytest.raises(NotFoundOrDenied):
        update_page_settings(authority, "owner", ws, "page-e1", **req)


def test_ui_page_settings_http_contract(tmp_path, monkeypatch):
    authority, _, _, _, ws = _environment(tmp_path)
    actor = {"id": "owner"}
    monkeypatch.setattr(routes, "_store", lambda: SimpleNamespace(iam=authority))
    monkeypatch.setattr(routes, "_collaborative_runtime_enabled", lambda: True)
    monkeypatch.setattr(routes, "current_user_id", lambda: actor["id"])
    app = Flask(__name__); app.config.update(TESTING=True)
    routes.register_notion_workspace_routes(app)
    client = app.test_client(); base = f"/api/notion/workspaces/{ws}/pages/page-e1"
    req = request(authority)
    saved = client.patch(base + "/settings", json=req)
    assert saved.status_code == 200 and "no-store" in saved.headers["Cache-Control"]
    assert client.patch(base + "/settings", json=req).json == saved.json
    loaded = client.get(base + "/collaboration").json
    assert loaded["page"]["can_change_settings"] and loaded["capabilities"]["collaborative_page_settings"]
    assert client.patch(base + "/settings", json={**req, "actor_subject_id": "someone"}).status_code == 400
    actor["id"] = "content-editor"
    assert client.get(base + "/collaboration").json["page"]["can_change_settings"] is False
    assert client.patch(base + "/settings", json=request(authority)).status_code == 404


def test_ui_collaborative_temporary_unlock_http_is_tab_scoped_and_relock_revokes_it(tmp_path, monkeypatch):
    authority, _, root, catalog, ws = _environment(tmp_path)
    actor = {"id": "owner"}
    monkeypatch.setattr(routes, "_store", lambda: SimpleNamespace(
        iam=authority, db_path=catalog, user_data_root=root,
    ))
    monkeypatch.setattr(routes, "_collaborative_runtime_enabled", lambda: True)
    monkeypatch.setattr(routes, "current_user_id", lambda: actor["id"])
    monkeypatch.setattr(routes_notion, "current_user_id", lambda: actor["id"])
    monkeypatch.setenv("CODE_ASSIST_COLLABORATION_GATEWAY_URL", "ws://127.0.0.1:10113/realtime")
    app = Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY="isolated-collaborative-temporary-unlock")
    routes_notion.register_notion_routes(app)
    routes.register_notion_workspace_routes(app)
    client = app.test_client()
    base = f"/api/notion/workspaces/{ws}/pages/page-e1"

    update_page_settings(authority, "owner", ws, "page-e1",
                         **request(authority, {"locked": True}, "temporary-first-lock"))
    issued = client.post(base + "/temporary-unlock")
    assert issued.status_code == 200 and issued.headers["Cache-Control"] == "no-store"
    token = issued.json["unlock_token"]
    first_generation = issued.json["lock_generation"]
    assert first_generation == collaborative_page_payload(authority, "owner", "page-e1")["page"]["lock_generation"]
    binding = session_binding_sha256("temporary-http-live-session")
    ticket_body = {"client_id": "temporary-http-no-token", "session_binding_sha256": binding}
    plain_ticket = client.post(base + "/realtime-ticket", json=ticket_body)
    assert plain_ticket.status_code == 201 and "write" not in plain_ticket.json["capabilities"]
    granted_ticket = client.post(base + "/realtime-ticket", json={**ticket_body, "client_id": "temporary-http-granted"},
                                 headers={"X-Notion-Temporary-Unlock": token})
    assert granted_ticket.status_code == 201 and "write" in granted_ticket.json["capabilities"]
    actor["id"] = "content-editor"
    content_ticket = client.post(base + "/realtime-ticket", json={**ticket_body, "client_id": "temporary-http-content"})
    assert content_ticket.status_code == 201 and "write" not in content_ticket.json["capabilities"]
    actor["id"] = "owner"

    def insert(block_id, operation_id, *, headers=None):
        page = authority.read_document("owner", "page-e1")
        return client.post(base + "/editing/commands", json={
            "command_kind": "block.insert", "target_id": block_id,
            "payload": {"type": "paragraph", "content": block_id, "after_block_id": "block-a"},
            "expected_document_sequence": page["document_sequence"],
            "expected_workspace_epoch": page["workspace_epoch"],
            "expected_document_epoch": page["document_epoch"], "operation_id": operation_id,
        }, headers=headers or {})

    assert insert("blocked-no-token", "temporary-no-token-0001").status_code == 403
    actor["id"] = "schema-editor"
    assert insert("blocked-other-user", "temporary-other-user-0001",
                  headers={"X-Notion-Temporary-Unlock": token}).status_code == 403
    actor["id"] = "owner"
    assert insert("allowed-temporary", "temporary-allowed-0001",
                  headers={"X-Notion-Temporary-Unlock": token}).status_code == 201
    changed = client.patch(base + "/settings", json=request(authority, {"font": "mono"}, "temporary-font"),
                           headers={"X-Notion-Temporary-Unlock": token})
    assert changed.status_code == 200 and changed.json["page"]["settings"]["font"] == "mono"
    assert insert("blocked-after-request", "temporary-after-request-0001").status_code == 403

    update_page_settings(authority, "owner", ws, "page-e1",
                         **request(authority, {"locked": False}, "temporary-global-unlock"))
    update_page_settings(authority, "owner", ws, "page-e1",
                         **request(authority, {"locked": True}, "temporary-global-relock"))
    assert insert("blocked-old-generation", "temporary-old-generation-0001",
                  headers={"X-Notion-Temporary-Unlock": token}).status_code == 403
    fresh = client.post(base + "/temporary-unlock")
    assert fresh.status_code == 200 and fresh.json["unlock_token"] != token
    assert fresh.json["lock_generation"] != first_generation
    assert insert("allowed-new-generation", "temporary-new-generation-0001",
                  headers={"X-Notion-Temporary-Unlock": fresh.json["unlock_token"]}).status_code == 201


def test_ui_page_settings_two_processes_one_commit(tmp_path):
    authority, _, root, catalog, ws = _environment(tmp_path)
    req = request(authority)
    script = r'''
import contextlib, json, sys
from pathlib import Path
with contextlib.redirect_stdout(sys.stderr):
    from code_assist_v1.notion_collaboration.editing_authority import CollaborativeEditingAuthority
    from code_assist_v1.notion_collaboration.iam_authority import InMemoryIdentityDirectory
    from code_assist_v1.notion_collaboration.page_settings import update_page_settings
authority = CollaborativeEditingAuthority.open(Path(sys.argv[1]), Path(sys.argv[2]), InMemoryIdentityDirectory(["owner"]))
print("READY", flush=True)
sys.stdin.readline()
print(json.dumps(update_page_settings(authority, "owner", sys.argv[3], "page-e1", **json.loads(sys.argv[4])), sort_keys=True), flush=True)
'''
    workers = [subprocess.Popen([sys.executable, "-c", script, str(catalog), str(root), ws, json.dumps(req)],
        cwd=str(__import__("pathlib").Path(__file__).resolve().parents[3]),
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(2)]
    try:
        for worker in workers:
            assert worker.stdout.readline().strip() == "READY"
        for worker in workers:
            worker.stdin.write("go\n"); worker.stdin.flush()
        results = []
        for worker in workers:
            stdout, stderr = worker.communicate(timeout=40)
            assert worker.returncode == 0, stderr
            results.append(json.loads(stdout))
        assert results[0] == results[1]
        after = authority.read_document("owner", "page-e1")
        assert after["document_sequence"] == req["expected_document_sequence"] + 1
        with sqlite3.connect(catalog) as con:
            assert con.execute("SELECT COUNT(*) FROM workspace_collaboration_outbox WHERE event_type='page.settings.updated'").fetchone()[0] == 1
    finally:
        for worker in workers:
            if worker.poll() is None:
                worker.kill(); worker.wait(timeout=10)
