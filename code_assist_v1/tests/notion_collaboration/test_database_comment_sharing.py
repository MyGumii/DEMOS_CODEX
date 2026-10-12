"""A directly shared item comment must not disclose its private owner page."""
import hashlib
from io import BytesIO
from types import SimpleNamespace
from urllib.parse import quote
import pytest

from code_assist_v1 import notion_workspace_database_row_page_placement_migration as placement
from code_assist_v1 import notion_workspace_custom_emoji_migration as custom_emoji
from code_assist_v1 import notion_workspace_database_item_page_layout_migration as item_layout
from code_assist_v1 import notion_workspace_database_template_default_migration as template_default
from code_assist_v1 import notion_workspace_database_sharing_migration as sharing
from code_assist_v1 import notion_workspace_database_upload_scope_migration as upload_scope
from code_assist_v1.notion_collaboration import database_access
from code_assist_v1.notion_collaboration import database_file_upload
from code_assist_v1.notion_collaboration.database_comments import (
    _checked_text, _safe_content, add_row_comment, mutate_row_comment,
)
from code_assist_v1.notion_collaboration.database_notifications import mark_direct_inbox_read
from code_assist_v1.notion_collaboration.canonical_authority import (
    InjectedFailure, NotFoundOrDenied,
)
from code_assist_v1.tests.notion_collaboration.test_database_features import _apply, SUBJECTS
from code_assist_v1.tests.notion_collaboration.test_database_move_commit import _upload_png
from code_assist_v1.tests.notion_collaboration.test_database_row_icon_batch import (
    icon_batch_environment, appearance_environment, body_environment,
    team_environment, file_environment, member_environment,
    native_environment, archive_environment, guest_environment,
    image_environment,
)
from code_assist_v1.tests.notion_collaboration.test_database_activity_migration import (
    activity_environment, environment,
)
from code_assist_v1.tests.notion_collaboration.test_page_archive_http import client_for


@pytest.mark.regression
def test_database_comment_nested_internal_links_are_redacted_and_rejected():
    access = SimpleNamespace(workspace_id="ws-e1", database_id="database-e1",
                             document_id="page-e1")

    def encoded(value, layers):
        for _ in range(layers):
            value = quote(value, safe="")
        return value

    private = encoded("".join(f"%{byte:02X}" for byte in
                              b"/api/notion/pages/page-e1"), 5)
    assert _safe_content(None, access, f"secret {private}", frozenset()) == (
        "[접근할 수 없는 첨부 파일]")
    with pytest.raises(ValueError, match="internal file or page link"):
        _checked_text(private, private_document_id="page-e1")
    external = encoded("https://example.org/public", 5)
    assert _safe_content(None, access, external, frozenset()) == external
    assert _checked_text(external, private_document_id="page-e1") == external
    overly_nested = encoded("https://example.org/public", 18)
    assert _safe_content(None, access, overly_nested, frozenset()) == (
        "[접근할 수 없는 첨부 파일]")
    with pytest.raises(ValueError, match="too deeply nested"):
        _checked_text(overly_nested, private_document_id="page-e1")


@pytest.mark.integration
@pytest.mark.regression
def test_database_only_comment_read_filters_and_redacts_parent_links(
        icon_batch_environment, monkeypatch):
    store, workspace_id = icon_batch_environment
    client, actor = client_for(monkeypatch, store)
    base = (f"/api/notion/workspaces/{workspace_id}/databases/database-e1"
            "/rows/row-e1/comments")
    assert client.get(base).status_code == 404
    private_file, _ = _upload_png(store, "private-comment-attachment")
    shared_file, png = _upload_png(store, "shared-comment-attachment")
    for target, backup in (
        (placement, "before-comment-placement.db"),
        (custom_emoji, "before-comment-emoji.db"),
        (item_layout, "before-comment-layout.db"),
        (template_default, "before-comment-default.db"),
        (sharing, "before-comment-sharing.db"),
        (upload_scope, "before-comment-upload.db"),
    ):
        _apply(target, store.db_path, store.user_data_root, backup)
    viewer = "database-comment-only-viewer"
    store.iam.identity_directory.replace([*SUBJECTS, viewer,
                                          "database-comment-ungranted"])
    owner = database_access.resolve(store.iam, "owner", workspace_id, "database-e1")
    database_access.change_grant(
        store.iam, "owner", workspace_id, "database-e1", "subject", viewer,
        role="view", expected_workspace_epoch=owner.workspace_epoch,
        expected_document_epoch=owner.document_epoch,
        expected_database_epoch=owner.database_epoch,
        operation_id="database-comment-view-grant",
    )
    page = store.iam.read_document("owner", "page-e1")
    item = next(row for row in store.iam.read_database("owner", "database-e1")["rows"]
                if row["id"] == "row-e1")
    store.iam.execute_surface_command(
        "owner", "page-e1", database_id="database-e1",
        command_kind="database.row.content.set", target_id="row-e1",
        payload={"blocks": [{"id": "shared-comment-image", "type": "image",
                             "content": "", "props": {"file_id": shared_file}}]},
        expected_target_version=item["version"],
        expected_document_sequence=page["document_sequence"],
        expected_workspace_epoch=page["workspace_epoch"],
        expected_document_epoch=page["document_epoch"],
        operation_id="database-comment-attach-shared-file",
    )

    def add(comment_id, content, anchor):
        page = store.iam.read_document("owner", "page-e1")
        store.iam.add_comment(
            "owner", "page-e1", comment_id, content=content, anchor=anchor,
            expected_workspace_epoch=page["workspace_epoch"],
            expected_document_epoch=page["document_epoch"],
            operation_id=f"database-comment-{comment_id}",
        )

    row = {"kind": "database_row", "database_id": "database-e1", "row_id": "row-e1"}
    nested_link = "".join(f"%{byte:02X}" for byte in
                          b"/api/notion/pages/page-e1")
    for _ in range(5):
        nested_link = quote(nested_link, safe="")
    add("comment-safe", "Visible item comment", row)
    add("comment-file", f"[secret-name.png](/api/notion/collaboration/files/{private_file})", row)
    add("comment-shared-file", f"[shared.png](/api/notion/collaboration/files/{shared_file})", row)
    add("comment-page-link", "See /api/notion/pages/page-e1", row)
    add("comment-z-encoded", f"%2Fapi%2Fnotion%2Fcollaboration%2Ffiles%2F{private_file}", row)
    add("comment-parent", "Private page comment", {})
    add("comment-other-row", "Different item", {**row, "row_id": "row-e2"})
    add("comment-nested", nested_link, {**row, "row_id": "row-e2"})

    actor["id"] = viewer
    assert client.get(f"/api/notion/workspaces/{workspace_id}/pages/page-e1/comments").status_code == 404
    response = client.get(base)
    assert response.status_code == 200, response.json
    mention_url = (f"/api/notion/workspaces/{workspace_id}/databases/"
                   "database-e1/mention-candidates")
    assert client.post(mention_url, json={"subject_ids": ["owner", viewer]}).status_code == 404
    assert "no-store" in response.headers["Cache-Control"]
    items_by_id = {item["id"]: item for item in response.json["items"]}
    assert set(items_by_id) == {
        "comment-file", "comment-page-link", "comment-safe", "comment-shared-file",
        "comment-z-encoded",
    }
    assert response.json["count"] == 5
    assert items_by_id["comment-safe"]["content"] == "Visible item comment"
    assert "접근할 수 없는 첨부 파일" in items_by_id["comment-file"]["content"]
    assert items_by_id["comment-shared-file"]["content"] == (
        f"[shared.png](/api/notion/workspaces/{workspace_id}/databases/"
        f"database-e1/files/{shared_file})")
    assert items_by_id["comment-z-encoded"]["content"] == "[접근할 수 없는 첨부 파일]"
    assert "page-e1" not in response.get_data(as_text=True)
    assert private_file not in response.get_data(as_text=True)
    assert "secret-name.png" not in response.get_data(as_text=True)
    assert "mention_subject_ids" not in response.get_data(as_text=True)
    assert client.get(base.replace("database-e1", "database-other")).status_code == 404
    assert client.get(base.replace("row-e1", "row-missing")).status_code == 404
    other_row = client.get(base.replace("row-e1", "row-e2"))
    assert other_row.status_code == 200, other_row.json
    other_row_items = {item["id"]: item for item in other_row.json["items"]}
    assert other_row_items["comment-other-row"]["content"] == "Different item"
    assert other_row_items["comment-nested"]["content"] == "[접근할 수 없는 첨부 파일]"
    assert nested_link not in other_row.get_data(as_text=True)

    request = {
        "comment_id": "comment-direct", "content": "Direct database comment",
        "expected_workspace_epoch": response.json["workspace_epoch"],
        "expected_document_epoch": response.json["document_epoch"],
        "expected_database_epoch": response.json["database_epoch"],
        "operation_id": "database-comment-direct-create",
    }
    assert client.post(base, json=request).status_code == 404
    actor["id"] = "owner"
    owner = database_access.resolve(store.iam, "owner", workspace_id, "database-e1")
    database_access.change_grant(
        store.iam, "owner", workspace_id, "database-e1", "subject", viewer,
        role="comment", expected_workspace_epoch=owner.workspace_epoch,
        expected_document_epoch=owner.document_epoch,
        expected_database_epoch=owner.database_epoch,
        operation_id="database-comment-comment-grant",
    )
    actor["id"] = viewer
    current = client.get(base).json
    request["expected_database_epoch"] = current["database_epoch"]
    forbidden = {**request, "comment_id": "comment-direct-file",
                 "content": f"/api/notion/collaboration/files/{private_file}",
                 "operation_id": "database-comment-direct-file"}
    assert client.post(base, json=forbidden).status_code == 400
    assert client.post(base, json={**request, "comment_id": "comment-direct-nested-link",
                                   "content": nested_link,
                                   "operation_id": "database-comment-direct-nested-link"}).status_code == 400
    foreign_parent = {**request, "comment_id": "comment-direct-foreign-parent",
                      "parent_comment_id": "comment-other-row",
                      "operation_id": "database-comment-direct-foreign-parent"}
    assert client.post(base, json=foreign_parent).status_code == 404
    created = client.post(base, json=request)
    assert created.status_code == 201, created.json
    assert created.json["replayed"] is False
    assert "page-e1" not in created.get_data(as_text=True)
    assert client.post(base, json=request).json["replayed"] is True
    assert client.get(base).json["count"] == 6
    reply = {**request, "comment_id": "comment-direct-reply",
             "parent_comment_id": "comment-direct",
             "content": "Reply in the same item",
             "operation_id": "database-comment-direct-reply"}
    with pytest.raises(InjectedFailure):
        add_row_comment(
            store.iam, viewer, workspace_id, "database-e1", "row-e1",
            fault="after_domain", **reply,
        )
    assert client.get(base).json["count"] == 6
    assert client.post(base, json=reply).status_code == 201
    assert client.post(base, json=reply).json["replayed"] is True
    assert client.get(base).json["count"] == 7

    item_base = base + "/comment-direct"
    forbidden_edit = {
        "content": "Cannot change somebody else's comment",
        "expected_row_version": 1,
        "expected_workspace_epoch": request["expected_workspace_epoch"],
        "expected_document_epoch": request["expected_document_epoch"],
        "expected_database_epoch": request["expected_database_epoch"],
        "operation_id": "database-comment-forbidden-edit",
    }
    assert client.patch(base + "/comment-safe", json=forbidden_edit).status_code == 404
    assert client.patch(base.replace("row-e1", "row-e2") + "/comment-direct",
                        json=forbidden_edit).status_code == 404
    edited_request = {**forbidden_edit, "content": "Edited direct database comment",
                      "operation_id": "database-comment-direct-edit"}
    assert client.patch(item_base, json={
        **edited_request, "content": nested_link,
        "operation_id": "database-comment-direct-nested-link-edit",
    }).status_code == 400
    with pytest.raises(InjectedFailure):
        mutate_row_comment(
            store.iam, viewer, workspace_id, "database-e1", "row-e1",
            "comment-direct", mutation="edit", fault="after_domain",
            **edited_request,
        )
    assert next(item for item in client.get(base).json["items"]
                if item["id"] == "comment-direct")["content"] == "Direct database comment"
    edited = client.patch(item_base, json=edited_request)
    assert edited.status_code == 200, edited.json
    assert edited.json["row_version"] == 2
    assert "page-e1" not in edited.get_data(as_text=True)
    assert client.patch(item_base, json=edited_request).json["replayed"] is True
    assert client.patch(item_base, json={**edited_request,
                        "content": "stale", "operation_id": "database-comment-stale"}).status_code == 409

    reaction_url = item_base + "/reactions"
    reaction = {key: edited_request[key] for key in (
        "expected_workspace_epoch", "expected_document_epoch", "expected_database_epoch")}
    reaction.update(emoji="👍", expected_row_version=2,
                    operation_id="database-comment-reaction-add")
    reacted = client.post(reaction_url, json=reaction)
    assert reacted.status_code == 200 and reacted.json["added"] is True
    assert reacted.json["reactions"] == {"👍": [viewer]}
    assert client.post(reaction_url, json=reaction).json["replayed"] is True
    assert client.post(reaction_url, json={**reaction,
                       "expected_row_version": 1,
                       "operation_id": "database-comment-reaction-stale"}).status_code == 409
    resolution = {**reaction, "expected_row_version": 3,
                  "operation_id": "database-comment-resolve"}
    resolution.pop("emoji")
    resolution["resolved"] = True
    resolved = client.patch(item_base, json=resolution)
    assert resolved.status_code == 200 and resolved.json["resolved"] is True
    assert resolved.json["row_version"] == 4
    assert client.patch(item_base, json=resolution).json["replayed"] is True
    deletion = {key: reaction[key] for key in (
        "expected_workspace_epoch", "expected_document_epoch", "expected_database_epoch")}
    deletion.update(expected_row_version=1, operation_id="database-comment-reply-delete")
    deleted = client.delete(base + "/comment-direct-reply", json=deletion)
    assert deleted.status_code == 200 and deleted.json["deleted"] is True
    assert "page-e1" not in deleted.get_data(as_text=True)
    assert client.delete(base + "/comment-direct-reply", json=deletion).json["replayed"] is True
    assert client.get(base).json["count"] == 6

    actor["id"] = "owner"
    managed = client.patch(item_base, json={
        **edited_request, "content": "Managed by the database owner",
        "expected_row_version": 4,
        "operation_id": "database-comment-owner-edit",
    })
    assert managed.status_code == 200 and managed.json["row_version"] == 5
    assert "page-e1" not in managed.get_data(as_text=True)
    owner_reply = client.post(base, json={
        **request, "comment_id": "comment-owner-reply",
        "parent_comment_id": "comment-direct", "content": "Owner answered",
        "operation_id": "database-comment-owner-reply",
    })
    assert owner_reply.status_code == 201, owner_reply.json
    reaction["expected_row_version"] = 5
    reaction["operation_id"] = "database-comment-owner-reaction"
    assert client.post(reaction_url, json=reaction).status_code == 200
    inbox_url = f"/api/notion/workspaces/{workspace_id}/inbox"
    actor["id"] = viewer
    assert store.iam.list_inbox(viewer, workspace_id=workspace_id)["count"] == 0
    inbox = client.get(inbox_url)
    assert inbox.status_code == 200, inbox.json
    assert inbox.json["count"] == 2
    assert {item["kind"] for item in inbox.json["items"]} == {
        "comment_replied", "comment_reacted"}
    assert all(item["database_id"] == "database-e1"
               and item["row_id"] == "row-e1" for item in inbox.json["items"])
    assert "page-e1" not in inbox.get_data(as_text=True)
    replied = next(item for item in inbox.json["items"]
                   if item["kind"] == "comment_replied")
    read = {"read": True, "operation_id": "database-comment-read-inbox",
            **{f"expected_{field}": replied[field] for field in (
                "workspace_epoch", "document_epoch", "database_epoch",
                "notification_epoch")}}
    with pytest.raises(InjectedFailure):
        mark_direct_inbox_read(
            store.iam, viewer, workspace_id, replied["id"],
            fault="after_domain", **{key: value for key, value in read.items()
                                   if key != "read"},
        )
    assert client.get(inbox_url).json["unread_count"] == 2
    read_url = inbox_url + "/" + replied["id"]
    receipt = client.patch(read_url, json=read)
    assert receipt.status_code == 200, receipt.json
    assert receipt.json["read"] is True
    assert "page-e1" not in receipt.get_data(as_text=True)
    assert client.patch(read_url, json=read).json["replayed"] is True
    assert client.get(inbox_url).json["unread_count"] == 1
    unread = next(item for item in client.get(inbox_url).json["items"]
                  if item["kind"] == "comment_reacted")
    unread_request = {"read": True, "operation_id": "database-comment-read-revoked",
                      **{f"expected_{field}": unread[field] for field in (
                          "workspace_epoch", "document_epoch", "database_epoch",
                          "notification_epoch")}}
    actor["id"] = "owner"
    owner = database_access.resolve(store.iam, "owner", workspace_id, "database-e1")
    database_access.change_grant(
        store.iam, "owner", workspace_id, "database-e1", "subject", viewer,
        role=None, expected_workspace_epoch=owner.workspace_epoch,
        expected_document_epoch=owner.document_epoch,
        expected_database_epoch=owner.database_epoch,
        operation_id="database-comment-view-revoke",
    )
    actor["id"] = viewer
    assert client.get(base).status_code == 404
    assert client.post(mention_url, json={"subject_ids": ["owner"]}).status_code == 404
    assert client.post(base, json=request).status_code == 404
    assert client.patch(item_base, json=edited_request).status_code == 404
    assert client.get(inbox_url).json["count"] == 0
    assert client.patch(inbox_url + "/" + unread["id"],
                        json=unread_request).status_code == 404
    actor["id"] = "owner"
    owner = database_access.resolve(store.iam, "owner", workspace_id, "database-e1")
    database_access.change_grant(
        store.iam, "owner", workspace_id, "database-e1", "subject", viewer,
        role="comment", expected_workspace_epoch=owner.workspace_epoch,
        expected_document_epoch=owner.document_epoch,
        expected_database_epoch=owner.database_epoch,
        operation_id="database-comment-comment-regrant",
    )
    actor["id"] = viewer
    assert client.post(base, json=request).status_code == 404
    assert client.patch(item_base, json=edited_request).status_code == 404
    assert client.patch(inbox_url + "/" + unread["id"],
                        json=unread_request).status_code == 404
    current = client.get(base).json
    eligible = client.post(mention_url, json={
        "subject_ids": [viewer, "database-comment-ungranted", "owner", viewer],
    })
    assert eligible.status_code == 200, eligible.json
    assert eligible.json["subject_ids"] == [viewer, "owner"]
    assert "document_id" not in eligible.json
    assert "page-e1" not in eligible.get_data(as_text=True)
    assert "no-store" in eligible.headers["Cache-Control"]
    assert client.post(mention_url, json={"subject_ids": "owner"}).status_code == 400
    assert client.post(mention_url, json={"subject_ids": ["owner"],
                                          "document_id": "page-e1"}).status_code == 400
    page_reply = client.post(base, json={
        **request, "comment_id": "comment-page-owner-reply",
        "parent_comment_id": "comment-safe", "content": "Visible to owner",
        "expected_database_epoch": current["database_epoch"],
        "operation_id": "database-comment-page-owner-reply",
    })
    assert page_reply.status_code == 201, page_reply.json
    actor["id"] = "owner"
    owner_inbox = client.get(inbox_url)
    assert owner_inbox.status_code == 200, owner_inbox.json
    page_note = next(item for item in owner_inbox.json["items"]
                     if item["comment_id"] == "comment-page-owner-reply")
    assert page_note["document_id"] == "page-e1"
    assert client.patch(inbox_url + "/" + page_note["id"], json={
        "read": True, "operation_id": "database-comment-page-owner-read",
    }).status_code == 200
    mention_request = {
        **request, "comment_id": "comment-direct-mention",
        "content": f"Hello @{viewer}", "mention_subject_ids": [viewer],
        "expected_database_epoch": current["database_epoch"],
        "operation_id": "database-comment-direct-mention",
    }
    with pytest.raises(InjectedFailure):
        add_row_comment(
            store.iam, "owner", workspace_id, "database-e1", "row-e1",
            fault="after_domain", **mention_request,
        )
    actor["id"] = viewer
    assert not any(item["comment_id"] == "comment-direct-mention"
                   for item in client.get(inbox_url).json["items"])
    actor["id"] = "owner"
    denied_mention = {**mention_request,
        "comment_id": "comment-direct-mention-denied",
        "mention_subject_ids": ["database-comment-ungranted"],
        "operation_id": "database-comment-direct-mention-denied"}
    assert client.post(base, json=denied_mention).status_code == 404
    mentioned = client.post(base, json=mention_request)
    assert mentioned.status_code == 201, mentioned.json
    assert mentioned.json["notification_count"] == 1
    assert "page-e1" not in mentioned.get_data(as_text=True)
    assert client.post(base, json=mention_request).json["replayed"] is True
    actor["id"] = viewer
    direct_mentions = [item for item in client.get(inbox_url).json["items"]
                       if item["comment_id"] == "comment-direct-mention"]
    assert len(direct_mentions) == 1
    assert direct_mentions[0]["kind"] == "comment_mentioned"
    assert "page-e1" not in str(direct_mentions[0])
    actor["id"] = "owner"
    edited_mention = client.patch(base + "/comment-direct-mention", json={
        "content": f"Edited @{viewer}", "mention_subject_ids": [viewer],
        "expected_row_version": 1,
        "expected_workspace_epoch": current["workspace_epoch"],
        "expected_document_epoch": current["document_epoch"],
        "expected_database_epoch": current["database_epoch"],
        "operation_id": "database-comment-direct-mention-edit",
    })
    assert edited_mention.status_code == 200, edited_mention.json
    assert edited_mention.json["notification_count"] == 1
    actor["id"] = viewer
    assert len([item for item in client.get(inbox_url).json["items"]
                if item["comment_id"] == "comment-direct-mention"
                and item["kind"] == "comment_mentioned"]) == 2
    actor["id"] = "owner"
    projection = database_access.read_projection(
        store.iam, "owner", workspace_id, "database-e1")
    uploaded = database_file_upload.begin_upload(
        store.iam, "owner", workspace_id, "database-e1",
        original_name="comment.png", mime_type="image/png",
        total_size=len(png), expected_sha256="sha256:" + hashlib.sha256(png).hexdigest(),
        chunk_size=65536,
        expected_document_sequence=projection["document_sequence"],
        expected_workspace_epoch=projection["workspace_epoch"],
        expected_document_epoch=projection["document_epoch"],
        expected_database_epoch=projection["database_epoch"],
        operation_id="database-comment-file-upload",
    )
    store.iam.stage_chunk("owner", uploaded["session_id"], chunk_index=0,
                          stream=BytesIO(png),
                          operation_id="database-comment-file-chunk")
    finished = store.iam.finalize_upload(
        "owner", uploaded["session_id"],
        operation_id="database-comment-file-finalize")
    file_id = finished["file_id"]
    actor["id"] = viewer
    file_url = (f"/api/notion/workspaces/{workspace_id}/databases/"
                f"database-e1/files/{file_id}")
    with pytest.raises(NotFoundOrDenied):
        database_access.get_file(
            store.iam, viewer, workspace_id, "database-e1", file_id)
    actor["id"] = "owner"
    file_comment = client.post(base, json={
        **request, "comment_id": "comment-direct-attachment",
        "content": f"[comment.png]({file_url})",
        "expected_database_epoch": projection["database_epoch"],
        "operation_id": "database-comment-direct-attachment",
    })
    assert file_comment.status_code == 201, file_comment.json
    assert "page-e1" not in file_comment.get_data(as_text=True)
    private_url = (f"/api/notion/workspaces/{workspace_id}/databases/"
                   f"database-e1/files/{private_file}")
    assert client.post(base, json={
        **request, "comment_id": "comment-private-file-denied",
        "content": f"[private.png]({private_url})",
        "expected_database_epoch": projection["database_epoch"],
        "operation_id": "database-comment-private-file-denied",
    }).status_code == 404
    actor["id"] = viewer
    assert next(item for item in client.get(base).json["items"]
                if item["id"] == "comment-direct-attachment")["content"] == (
                    f"[comment.png]({file_url})")
    assert database_access.get_file(
        store.iam, viewer, workspace_id, "database-e1", file_id
    )["path"].read_bytes() == png
    actor["id"] = "owner"
    assert client.delete(base + "/comment-direct-attachment", json={
        "expected_row_version": 1,
        "expected_workspace_epoch": projection["workspace_epoch"],
        "expected_document_epoch": projection["document_epoch"],
        "expected_database_epoch": projection["database_epoch"],
        "operation_id": "database-comment-direct-attachment-delete",
    }).status_code == 200
    actor["id"] = viewer
    with pytest.raises(NotFoundOrDenied):
        database_access.get_file(
            store.iam, viewer, workspace_id, "database-e1", file_id)
