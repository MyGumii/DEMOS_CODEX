from __future__ import annotations

from pathlib import Path
import sqlite3
import time

from flask import Flask
import pytest

from code_assist_v1 import notion_workspace_collaboration_migration as canonical_migration
from code_assist_v1 import notion_workspace_iam_migration as iam_migration
from code_assist_v1 import notion_workspace_live_collaboration_migration as live_migration
from code_assist_v1 import notion_workspace_realtime_transport_migration as transport_migration
from code_assist_v1 import notion_workspace_store as workspace_store
from code_assist_v1 import routes_notion_workspace
from code_assist_v1.notion_collaboration.canonical_authority import (
    InjectedFailure,
    NotFoundOrDenied,
    _lock_generation_on_connection,
    bind_temporary_unlock,
)
from code_assist_v1.notion_collaboration.iam_authority import InMemoryIdentityDirectory
from code_assist_v1.notion_collaboration.live_authority import CollaborativeLiveAuthority
from code_assist_v1.notion_collaboration.iam_runtime import (
    CollaborativeDurableTransportAuthority,
    CollaborativeWorkspaceStore,
    issue_realtime_ticket,
)
from code_assist_v1.notion_collaboration.protocol_contract import (
    StaleEpoch,
    session_binding_sha256,
)
from code_assist_v1.notion_collaboration.runtime_gateway import RuntimeGateway


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
    root = tmp_path / "c0-live"
    catalog = Path(workspace_store.catalog_path_for_user_data_root(root))
    store = workspace_store.WorkspaceStore(catalog)
    store.bootstrap()
    created = store.create_workspace(
        "owner", name="C0 workspace", operation_id="op-c0-workspace-create-0001"
    )
    workspace_id = str(created["workspace"]["id"])
    version = int(created["workspace"]["version"])
    for index, subject in enumerate(("author", "mentioned"), start=1):
        result = store.set_member(
            "owner",
            workspace_id,
            subject,
            role="member",
            base_version=version,
            operation_id=f"op-c0-member-set-{index:04d}",
        )
        version = int(result["workspace"]["version"])
    _apply(canonical_migration, catalog, root, "before-2401.db")
    _apply(transport_migration, catalog, root, "before-2402.db")
    _apply(iam_migration, catalog, root, "before-2403.db")
    _apply(live_migration, catalog, root, "before-2404.db")
    identities = InMemoryIdentityDirectory(("owner", "author", "mentioned"))
    authority = CollaborativeLiveAuthority.open(catalog, root, identities)
    con = sqlite3.connect(catalog)
    try:
        workspace_epoch = int(
            con.execute(
                "SELECT security_epoch FROM workspaces WHERE id=?", (workspace_id,)
            ).fetchone()[0]
        )
    finally:
        con.close()
    authority.create_document(
        "owner",
        workspace_id,
        "page-live",
        title="Live",
        blocks=[{"id": "block-live", "type": "text", "content": "body"}],
        expected_workspace_epoch=workspace_epoch,
        operation_id="op-c0-page-create-0001",
    )
    for index, subject in enumerate(("author", "mentioned"), start=1):
        page = authority.read_document("owner", "page-live")
        authority.set_page_grant(
            "owner",
            "page-live",
            "subject",
            subject,
            role="comment",
            inherit_to_descendants=False,
            expected_workspace_epoch=int(page["workspace_epoch"]),
            expected_document_epoch=int(page["document_epoch"]),
            operation_id=f"op-c0-page-grant-{index:04d}",
        )
    return authority, root, catalog, workspace_id


def _epochs(authority: CollaborativeLiveAuthority, subject: str = "author") -> tuple[int, int]:
    page = authority.read_document(subject, "page-live")
    return int(page["workspace_epoch"]), int(page["document_epoch"])


def test_locked_collaborative_page_issues_read_only_live_ticket(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    authority, root, catalog, workspace_id = _environment(tmp_path)
    page = authority.read_document("owner", "page-live")
    authority.save_document(
        "owner", "page-live", title=page["title"], blocks=page["blocks"],
        settings={"locked": True}, expected_revision=page["revision"],
        expected_workspace_epoch=page["workspace_epoch"],
        expected_document_epoch=page["document_epoch"], operation_id="locked-ticket-page",
    )
    monkeypatch.setenv("CODE_ASSIST_COLLABORATION_GATEWAY_URL", "ws://127.0.0.1:10113/realtime")
    store = CollaborativeWorkspaceStore(
        catalog, user_data_root=root, identity_directory=InMemoryIdentityDirectory(("owner", "author", "mentioned")),
    )
    binding = session_binding_sha256("locked-ticket-session")
    ticket = issue_realtime_ticket(
        store, "owner", workspace_id, "page-live", client_id="locked-ticket-client",
        session_binding_sha256=binding, origin="http://127.0.0.1:10112", now_ms=3_000,
    )
    assert ticket["capabilities"] == ["read", "presence", "comment"]
    transport = CollaborativeDurableTransportAuthority.open(catalog, root)
    with pytest.raises(PermissionError, match="page is locked"):
        transport.issue_ticket(
            csrf_valid=True, subject_id="owner", workspace_id=workspace_id,
            document_id="page-live", client_id="locked-direct-write",
            session_binding_sha256=binding, origin="http://127.0.0.1:10112",
            capabilities=("read", "write"), now_ms=3_001,
        )


def test_locked_collaborative_page_issues_temporary_write_ticket_only_for_bound_tab(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    authority, root, catalog, workspace_id = _environment(tmp_path)
    page = authority.read_document("owner", "page-live")
    authority.save_document(
        "owner", "page-live", title=page["title"], blocks=page["blocks"],
        settings={"locked": True}, expected_revision=page["revision"],
        expected_workspace_epoch=page["workspace_epoch"],
        expected_document_epoch=page["document_epoch"], operation_id="temporary-ticket-lock-page",
    )
    monkeypatch.setenv("CODE_ASSIST_COLLABORATION_GATEWAY_URL", "ws://127.0.0.1:10113/realtime")
    store = CollaborativeWorkspaceStore(
        catalog, user_data_root=root, identity_directory=InMemoryIdentityDirectory(("owner", "author", "mentioned")),
    )
    binding = session_binding_sha256("temporary-ticket-session")
    now_ms = int(time.time() * 1000)
    common = dict(client_id="temporary-ticket-client", session_binding_sha256=binding,
                  origin="http://127.0.0.1:10112", now_ms=now_ms)
    with authority._reader() as con:
        generation = _lock_generation_on_connection(con, authority._document(con, "page-live"))
    with bind_temporary_unlock({
        "user_id": "owner", "workspace_id": workspace_id, "page_id": "page-live",
        "lock_generation": generation,
        "expires_at_ms": now_ms + 60_000,
    }):
        ticket = issue_realtime_ticket(store, "owner", workspace_id, "page-live", **common)
    assert "write" in ticket["capabilities"]
    transport = CollaborativeDurableTransportAuthority.open(catalog, root)
    grant = transport.consume_ticket(
        ticket["token"], origin=common["origin"], subject_id="owner",
        workspace_id=workspace_id, document_id="page-live",
        client_id=common["client_id"], session_binding_sha256=binding, now_ms=now_ms + 1,
    )
    assert grant.allows("write") and grant.temporary_unlock is not None
    without = issue_realtime_ticket(store, "owner", workspace_id, "page-live",
                                    **{**common, "client_id": "temporary-ticket-unbound"})
    assert "write" not in without["capabilities"]


@pytest.mark.migration
def test_c0_p1_2404_migration_is_forward_only_checksummed_and_presence_free(
    tmp_path: Path,
) -> None:
    authority, root, catalog, _workspace_id = _environment(tmp_path)
    plan = live_migration.build_plan(catalog, root)
    assert plan["state"] == "already_current"
    assert live_migration.SOURCE_SQL == iam_migration.TARGET_SQL
    assert live_migration.TARGET_SQL.endswith(
        live_migration.LIVE_COLLABORATION_SCHEMA_SQL
    )
    con = sqlite3.connect(catalog)
    try:
        tables = {
            str(row[0])
            for row in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        marker = con.execute(
            "SELECT name,checksum,actual_schema_sha256 "
            "FROM workspace_schema_migrations WHERE version=2026082404"
        ).fetchone()
    finally:
        con.close()
    assert "workspace_collaboration_notifications" in tables
    assert "workspace_collaboration_comment_mentions" in tables
    assert all("presence" not in name for name in tables)
    assert marker == (
        live_migration.TARGET_NAME,
        live_migration.TARGET_CHECKSUM,
        live_migration.TARGET_ACTUAL_SCHEMA_SHA256,
    )
    assert authority.list_inbox("mentioned") == {
        "contract": "code_assist.notion.live_collaboration_authority.v1",
        "items": [],
        "count": 0,
        "unread_count": 0,
    }


@pytest.mark.integration
def test_c0_p1_comments_reactions_mentions_inbox_and_atomic_outbox(tmp_path: Path) -> None:
    authority, _root, catalog, _workspace_id = _environment(tmp_path)
    workspace_epoch, document_epoch = _epochs(authority)
    created = authority.add_comment(
        "author",
        "page-live",
        "comment-live",
        content="hello @mentioned",
        mention_subject_ids=["mentioned"],
        expected_workspace_epoch=workspace_epoch,
        expected_document_epoch=document_epoch,
        operation_id="op-c0-comment-add-0001",
    )
    assert created["notification_count"] == 1
    inbox = authority.list_inbox("mentioned")
    assert inbox["count"] == inbox["unread_count"] == 1
    assert inbox["items"][0]["kind"] == "comment_mentioned"
    assert "title" not in inbox["items"][0]

    listed = authority.list_comments("mentioned", "page-live")
    comment = listed["items"][0]
    assert comment["mention_subject_ids"] == ["mentioned"]
    reaction = authority.toggle_reaction(
        "mentioned",
        "page-live",
        "comment-live",
        emoji="👍",
        expected_row_version=comment["row_version"],
        expected_workspace_epoch=workspace_epoch,
        expected_document_epoch=document_epoch,
        operation_id="op-c0-reaction-0001",
    )
    assert reaction["added"] is True
    owner_inbox = authority.list_inbox("author")
    assert owner_inbox["items"][0]["kind"] == "comment_reacted"

    current = authority.list_comments("author", "page-live")["items"][0]
    updated = authority.update_comment(
        "author",
        "page-live",
        "comment-live",
        content="edited",
        mention_subject_ids=[],
        expected_row_version=current["row_version"],
        expected_workspace_epoch=workspace_epoch,
        expected_document_epoch=document_epoch,
        operation_id="op-c0-comment-update-0001",
    )
    resolved = authority.set_comment_resolved(
        "mentioned",
        "page-live",
        "comment-live",
        resolved=True,
        expected_row_version=updated["row_version"],
        expected_workspace_epoch=workspace_epoch,
        expected_document_epoch=document_epoch,
        operation_id="op-c0-comment-resolve-0001",
    )
    assert resolved["resolved"] is True

    con = sqlite3.connect(catalog)
    try:
        assert con.execute(
            "SELECT COUNT(*) FROM workspace_collaboration_events"
        ).fetchone()[0] == con.execute(
            "SELECT COUNT(*) FROM workspace_collaboration_audit_events"
        ).fetchone()[0]
        assert con.execute(
            "SELECT COUNT(*) FROM workspace_collaboration_events"
        ).fetchone()[0] == con.execute(
            "SELECT COUNT(*) FROM workspace_collaboration_outbox"
        ).fetchone()[0]
    finally:
        con.close()

    current = authority.list_comments("author", "page-live")["items"][0]
    before = authority.counts()
    with pytest.raises(InjectedFailure):
        authority.delete_comment(
            "author",
            "page-live",
            "comment-live",
            expected_row_version=current["row_version"],
            expected_workspace_epoch=workspace_epoch,
            expected_document_epoch=document_epoch,
            operation_id="op-c0-comment-delete-fault",
            fault="after_event",
        )
    assert authority.counts() == before
    assert authority.list_comments("author", "page-live")["count"] == 1


@pytest.mark.integration
def test_c0_p1_revoke_fences_inbox_and_live_replay_without_identifier_leak(
    tmp_path: Path,
) -> None:
    authority, _root, _catalog, workspace_id = _environment(tmp_path)
    workspace_epoch, document_epoch = _epochs(authority)
    created = authority.add_comment(
        "author",
        "page-live",
        "comment-revoke",
        content="mention",
        mention_subject_ids=["mentioned"],
        expected_workspace_epoch=workspace_epoch,
        expected_document_epoch=document_epoch,
        operation_id="op-c0-comment-revoke-0001",
    )
    replay = authority.replay_domain_events(
        "mentioned",
        "page-live",
        after_document_sequence=0,
        limit=100,
    )
    assert replay.events[-1].event_id == created["event_id"]
    revoked = authority.revoke_workspace_member(
        "owner",
        workspace_id,
        "mentioned",
        expected_workspace_epoch=workspace_epoch,
        operation_id="op-c0-member-revoke-0001",
    )
    assert revoked["workspace_epoch"] > workspace_epoch
    with pytest.raises(NotFoundOrDenied):
        authority.list_comments("mentioned", "page-live")
    with pytest.raises(NotFoundOrDenied):
        authority.replay_domain_events(
            "mentioned",
            "page-live",
            after_document_sequence=0,
            limit=100,
        )
    assert authority.list_inbox("mentioned") == {
        "contract": "code_assist.notion.live_collaboration_authority.v1",
        "items": [],
        "count": 0,
        "unread_count": 0,
    }


@pytest.mark.integration
def test_c0_p1_gateway_replays_only_committed_live_event_then_epoch_fences(
    tmp_path: Path,
) -> None:
    authority, root, catalog, workspace_id = _environment(tmp_path)
    transport = CollaborativeDurableTransportAuthority.open(catalog, root)
    before = authority.binding("mentioned", "page-live").document_sequence
    binding = session_binding_sha256("c0-live-gateway-session")
    origin = "http://127.0.0.1:10112"
    issued = transport.issue_ticket(
        csrf_valid=True,
        subject_id="mentioned",
        workspace_id=workspace_id,
        document_id="page-live",
        client_id="c0-live-reader",
        session_binding_sha256=binding,
        origin=origin,
        capabilities=("read", "comment", "presence"),
        now_ms=1_000,
    )
    gateway = RuntimeGateway(
        transport=transport,
        aggregate_enabled=True,
        realtime_enabled=True,
    )
    gateway.start(compactor_handshake_matches=True)
    session = gateway.connect(
        issued.token,
        origin=origin,
        subject_id="mentioned",
        workspace_id=workspace_id,
        page_id="page-live",
        client_id="c0-live-reader",
        session_binding_sha256=binding,
        now_ms=1_001,
    )
    workspace_epoch, document_epoch = _epochs(authority)
    committed = authority.add_comment(
        "author",
        "page-live",
        "comment-gateway",
        content="committed before delivery",
        mention_subject_ids=["mentioned"],
        expected_workspace_epoch=workspace_epoch,
        expected_document_epoch=document_epoch,
        operation_id="op-c0-comment-gateway-0001",
    )
    receipt = gateway.replay_live_to_queue(
        session.connection_id,
        after_document_sequence=before,
        limit=100,
    )
    assert receipt.event_frames_enqueued == 1
    frame = gateway.drain(session.connection_id, max_frames=8)[0]
    assert frame.kind == "comment.added"
    assert frame.document_sequence == committed["document_sequence"]
    assert b"committed before delivery" not in frame.payload

    authority.revoke_workspace_member(
        "owner",
        workspace_id,
        "mentioned",
        expected_workspace_epoch=workspace_epoch,
        operation_id="op-c0-gateway-member-revoke-0001",
    )
    with pytest.raises(StaleEpoch):
        gateway.replay_live_to_queue(
            session.connection_id,
            after_document_sequence=receipt.next_document_sequence,
            limit=100,
        )
    assert gateway.session_status(session.connection_id)["close_reason"] == "stale_epoch"


@pytest.mark.integration
def test_c0_p1_live_replay_rebinds_event_after_page_grant_epoch_change(
    tmp_path: Path,
) -> None:
    authority, root, catalog, workspace_id = _environment(tmp_path)
    before = authority.binding("mentioned", "page-live").document_sequence
    workspace_epoch, document_epoch = _epochs(authority)
    committed = authority.add_comment(
        "author",
        "page-live",
        "comment-before-grant-change",
        content="historical visible comment",
        mention_subject_ids=["mentioned"],
        expected_workspace_epoch=workspace_epoch,
        expected_document_epoch=document_epoch,
        operation_id="op-c0-comment-before-grant-change-0001",
    )
    page = authority.read_document("owner", "page-live")
    authority.set_page_grant(
        "owner",
        "page-live",
        "subject",
        "mentioned",
        role="view",
        inherit_to_descendants=False,
        expected_workspace_epoch=int(page["workspace_epoch"]),
        expected_document_epoch=int(page["document_epoch"]),
        operation_id="op-c0-mentioned-view-0001",
    )
    page = authority.read_document("owner", "page-live")
    authority.set_page_grant(
        "owner",
        "page-live",
        "subject",
        "mentioned",
        role="comment",
        inherit_to_descendants=False,
        expected_workspace_epoch=int(page["workspace_epoch"]),
        expected_document_epoch=int(page["document_epoch"]),
        operation_id="op-c0-mentioned-comment-0001",
    )

    transport = CollaborativeDurableTransportAuthority.open(catalog, root)
    binding = session_binding_sha256("c0-live-rebind-session")
    origin = "http://127.0.0.1:10112"
    ticket = transport.issue_ticket(
        csrf_valid=True,
        subject_id="mentioned",
        workspace_id=workspace_id,
        document_id="page-live",
        client_id="c0-live-rebind-reader",
        session_binding_sha256=binding,
        origin=origin,
        capabilities=("read", "comment", "presence"),
        now_ms=1_000,
    )
    gateway = RuntimeGateway(
        transport=transport,
        aggregate_enabled=True,
        realtime_enabled=True,
    )
    gateway.start(compactor_handshake_matches=True)
    session = gateway.connect(
        ticket.token,
        origin=origin,
        subject_id="mentioned",
        workspace_id=workspace_id,
        page_id="page-live",
        client_id="c0-live-rebind-reader",
        session_binding_sha256=binding,
        now_ms=1_001,
    )
    replay = gateway.replay_live_to_queue(
        session.connection_id,
        after_document_sequence=before,
        limit=100,
    )
    frames = gateway.drain(session.connection_id, max_frames=100)

    assert replay.event_frames_enqueued >= 1
    assert any(
        frame.kind == "comment.added"
        and frame.document_sequence == committed["document_sequence"]
        for frame in frames
    )
    assert all(frame.workspace_epoch == ticket.claims.workspace_epoch for frame in frames)
    assert all(frame.page_epoch == ticket.claims.page_epoch for frame in frames)
    assert gateway.session_status(session.connection_id)["active"] is True
    gateway.stop()


@pytest.mark.integration
def test_c0_p1_http_routes_issue_bound_ticket_and_expose_canonical_comments_inbox(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    authority, root, catalog, workspace_id = _environment(tmp_path)
    identities = InMemoryIdentityDirectory(("owner", "author", "mentioned"))
    store = CollaborativeWorkspaceStore(
        catalog,
        user_data_root=root,
        identity_directory=identities,
    )
    actor = {"id": "author"}
    monkeypatch.setattr(routes_notion_workspace, "_store", lambda: store)
    monkeypatch.setattr(
        routes_notion_workspace, "_collaborative_runtime_enabled", lambda: True
    )
    monkeypatch.setattr(
        routes_notion_workspace, "current_user_id", lambda: actor["id"]
    )
    monkeypatch.setattr(
        routes_notion_workspace,
        "list_public_users",
        lambda include_data=False: [
            {"id": value, "display_name": value.title(), "disabled": False}
            for value in ("owner", "author", "mentioned")
        ],
    )
    monkeypatch.setenv(
        "CODE_ASSIST_COLLABORATION_GATEWAY_URL",
        "ws://127.0.0.1:10113/realtime",
    )
    app = Flask("c0-live-routes")
    app.config.update(TESTING=True, SECRET_KEY="c0-live-routes")
    routes_notion_workspace.register_notion_workspace_routes(app)
    client = app.test_client()
    workspace_epoch, document_epoch = _epochs(authority)
    base = f"/api/notion/workspaces/{workspace_id}/pages/page-live"
    response = client.post(
        base + "/comments",
        base_url="http://127.0.0.1:10112",
        json={
            "comment_id": "comment-http",
            "content": "@mentioned route",
            "anchor": {},
            "block_id": None,
            "parent_comment_id": None,
            "mention_subject_ids": ["mentioned"],
            "expected_workspace_epoch": workspace_epoch,
            "expected_document_epoch": document_epoch,
            "operation_id": "op-c0-http-comment-0001",
        },
    )
    assert response.status_code == 201, response.get_json()
    comments = client.get(base + "/comments").get_json()
    assert comments["count"] == 1
    assert comments["items"][0]["author_subject_id"] == "author"

    page = authority.read_document("owner", "page-live")
    authority.set_page_grant(
        "owner",
        "page-live",
        "subject",
        "author",
        role="edit_content",
        inherit_to_descendants=False,
        expected_workspace_epoch=int(page["workspace_epoch"]),
        expected_document_epoch=int(page["document_epoch"]),
        operation_id="op-c0-http-edit-content-grant-0001",
    )
    binding = session_binding_sha256("http-route-ticket-session")
    response = client.post(
        base + "/realtime-ticket",
        base_url="http://127.0.0.1:10112",
        headers={"Origin": "http://127.0.0.1:10112"},
        json={
            "client_id": "http-route-client",
            "session_binding_sha256": binding,
        },
    )
    assert response.status_code == 201, response.get_json()
    ticket = response.get_json()
    assert ticket["gateway_url"] == "ws://127.0.0.1:10113/realtime"
    assert ticket["capabilities"] == ["read", "presence", "comment", "write"]
    token = ticket["token"]
    con = sqlite3.connect(catalog)
    try:
        stored = con.execute(
            "SELECT ticket_sha256 FROM workspace_collaboration_tickets "
            "ORDER BY created_at DESC LIMIT 1"
        ).fetchone()[0]
    finally:
        con.close()
    assert token not in stored

    actor["id"] = "mentioned"
    inbox = client.get(
        f"/api/notion/workspaces/{workspace_id}/inbox",
        base_url="http://127.0.0.1:10112",
    ).get_json()
    assert inbox["count"] == inbox["unread_count"] == 1
    assert inbox["items"][0]["kind"] == "comment_mentioned"
