"""UI-R2/R10: a database-only favorite never needs its private parent."""
import pytest

from code_assist_v1 import notion_workspace_database_row_page_placement_migration as placement
from code_assist_v1 import notion_workspace_custom_emoji_migration as custom_emoji
from code_assist_v1 import notion_workspace_database_item_page_layout_migration as item_layout
from code_assist_v1 import notion_workspace_database_template_default_migration as template_default
from code_assist_v1 import notion_workspace_database_sharing_migration as sharing
from code_assist_v1 import notion_workspace_database_upload_scope_migration as upload_scope
from code_assist_v1 import notion_workspace_direct_database_favorite_migration as migration
from code_assist_v1.notion_collaboration import database_access, direct_database_favorites
from code_assist_v1.notion_collaboration.canonical_authority import (
    InjectedFailure, NotFoundOrDenied, OperationConflict,
)
from code_assist_v1.tests.notion_collaboration.test_database_features import _apply, SUBJECTS
from code_assist_v1.tests.notion_collaboration.test_database_row_icon_batch import (
    icon_batch_environment, appearance_environment, body_environment,
    team_environment, file_environment, member_environment,
    native_environment, archive_environment, guest_environment,
    image_environment,
)
from code_assist_v1.tests.notion_collaboration.test_database_activity_migration import activity_environment, environment
from code_assist_v1.tests.notion_collaboration.test_page_archive_http import client_for


@pytest.mark.integration
@pytest.mark.regression
def test_ui_r2_r10_direct_database_favorite_is_scoped_and_durable(
        icon_batch_environment, monkeypatch):
    store, workspace_id = icon_batch_environment
    for target, backup in (
        (placement, "before-direct-favorite-placement.db"),
        (custom_emoji, "before-direct-favorite-emoji.db"),
        (item_layout, "before-direct-favorite-layout.db"),
        (template_default, "before-direct-favorite-template.db"),
        (sharing, "before-direct-favorite-sharing.db"),
        (upload_scope, "before-direct-favorite-upload.db"),
        (migration, "before-direct-favorite-state.db"),
    ):
        _apply(target, store.db_path, store.user_data_root, backup)
    actor = "favorite-direct-viewer"
    store.iam.identity_directory.replace([*SUBJECTS, actor])
    owner = database_access.resolve(store.iam, "owner", workspace_id, "database-e1")
    database_access.change_grant(
        store.iam, "owner", workspace_id, "database-e1", "subject", actor,
        role="view", expected_workspace_epoch=owner.workspace_epoch,
        expected_document_epoch=owner.document_epoch,
        expected_database_epoch=owner.database_epoch,
        operation_id="direct-favorite-grant",
    )
    access = database_access.resolve(store.iam, actor, workspace_id, "database-e1")
    initial = direct_database_favorites.read(
        store.iam, actor, workspace_id, access_database_id="database-e1")
    assert initial["favorite_version"] == 0 and initial["items"] == []
    request = dict(remove=False, expected_favorite_version=0,
                   expected_workspace_epoch=access.workspace_epoch,
                   expected_document_epoch=access.document_epoch,
                   expected_database_epoch=access.database_epoch,
                   operation_id="direct-favorite-add")
    with pytest.raises(InjectedFailure):
        direct_database_favorites.change(
            store.iam, actor, workspace_id, "database-e1", **request,
            fault="after_domain")
    assert direct_database_favorites.read(
        store.iam, actor, workspace_id,
        access_database_id="database-e1")["items"] == []
    added = direct_database_favorites.change(
        store.iam, actor, workspace_id, "database-e1", **request)
    assert added["favorite_version"] == 1 and added["is_favorite"] is True
    assert added["replayed"] is False
    assert "page-e1" not in str(added)
    assert direct_database_favorites.change(
        store.iam, actor, workspace_id, "database-e1", **request
    )["replayed"] is True
    with pytest.raises(OperationConflict):
        direct_database_favorites.change(
            store.iam, actor, workspace_id, "database-e1",
            **{**request, "remove": True})
    listed = direct_database_favorites.read(
        store.iam, actor, workspace_id, access_database_id="database-e1")
    assert listed["favorite_version"] == 1
    assert listed["items"] == [{"database_id": "database-e1",
                               "title": "Tasks", "position": 0}]
    assert "page-e1" not in str(listed)
    current = database_access.resolve(store.iam, "owner", workspace_id, "database-e1")
    database_access.change_grant(
        store.iam, "owner", workspace_id, "database-e1", "subject", actor,
        role=None, expected_workspace_epoch=current.workspace_epoch,
        expected_document_epoch=current.document_epoch,
        expected_database_epoch=current.database_epoch,
        operation_id="direct-favorite-revoke",
    )
    with pytest.raises(NotFoundOrDenied):
        direct_database_favorites.read(
            store.iam, actor, workspace_id, access_database_id="database-e1")
    current = database_access.resolve(store.iam, "owner", workspace_id, "database-e1")
    database_access.change_grant(
        store.iam, "owner", workspace_id, "database-e1", "subject", actor,
        role="view", expected_workspace_epoch=current.workspace_epoch,
        expected_document_epoch=current.document_epoch,
        expected_database_epoch=current.database_epoch,
        operation_id="direct-favorite-regrant",
    )
    with pytest.raises(NotFoundOrDenied):
        direct_database_favorites.change(
            store.iam, actor, workspace_id, "database-e1", **request)
    restored = direct_database_favorites.read(
        store.iam, actor, workspace_id, access_database_id="database-e1")
    assert len(restored["items"]) == 1
    access = database_access.resolve(store.iam, actor, workspace_id, "database-e1")
    removed = direct_database_favorites.change(
        store.iam, actor, workspace_id, "database-e1", remove=True,
        expected_favorite_version=1,
        expected_workspace_epoch=access.workspace_epoch,
        expected_document_epoch=access.document_epoch,
        expected_database_epoch=access.database_epoch,
        operation_id="direct-favorite-remove",
    )
    assert removed["is_favorite"] is False and removed["favorite_version"] == 2
    assert direct_database_favorites.read(
        store.iam, actor, workspace_id,
        access_database_id="database-e1")["items"] == []
    client, http_actor = client_for(monkeypatch, store)
    http_actor["id"] = actor
    base = f"/api/notion/workspaces/{workspace_id}"
    assert client.get(base + "/pages/page-e1/collaboration").status_code == 404
    empty = client.get(base + "/database-favorites?access_database_id=database-e1")
    assert empty.status_code == 200 and empty.json["items"] == []
    access = database_access.resolve(store.iam, actor, workspace_id, "database-e1")
    body = dict(expected_favorite_version=2,
                expected_workspace_epoch=access.workspace_epoch,
                expected_document_epoch=access.document_epoch,
                expected_database_epoch=access.database_epoch,
                operation_id="direct-favorite-http-add")
    added_http = client.put(base + "/databases/database-e1/favorite", json=body)
    assert added_http.status_code == 200, added_http.json
    assert added_http.json["is_favorite"] is True
    assert "page-e1" not in added_http.text
    assert client.put(base + "/databases/database-e1/favorite", json=body).json["replayed"] is True
    listed_http = client.get(base + "/database-favorites?access_database_id=database-e1")
    assert listed_http.status_code == 200
    assert [item["database_id"] for item in listed_http.json["items"]] == ["database-e1"]
    assert "page-e1" not in listed_http.text
    removed_http = client.delete(base + "/databases/database-e1/favorite",
                                 json={**body, "expected_favorite_version": 3,
                                       "operation_id": "direct-favorite-http-remove"})
    assert removed_http.status_code == 200 and removed_http.json["is_favorite"] is False
