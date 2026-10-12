from __future__ import annotations

import pytest

from code_assist_v1.notion_workspace_store import (
    ActiveLegacyPageBindingCursor,
    LegacyPageSourceProof,
    WorkspacePageSourceSuperseded,
    WorkspaceStore,
    catalog_path_for_user_data_root,
)


def _proof(page_id: str, *, revision: int = 1) -> LegacyPageSourceProof:
    return LegacyPageSourceProof(
        actor_subject_id="owner",
        owner_subject_id="owner",
        page_id=page_id,
        revision=revision,
        updated_at=revision,
        content_sha256="sha256:" + f"{revision:064x}",
    )


def _fixture(tmp_path):
    store = WorkspaceStore(catalog_path_for_user_data_root(tmp_path))
    workspace = store.create_workspace(
        "owner", name="Reconcile", operation_id="create-reconcile"
    )["workspace"]
    active_a = store.bind_page_source(
        "owner",
        workspace["id"],
        _proof("page-a"),
        source_revalidator=lambda _proof: True,
        base_version=workspace["version"],
        operation_id="bind-a",
    )
    pending_b = store.stage_page_source_binding(
        "owner",
        workspace["id"],
        _proof("page-b"),
        base_version=active_a["workspace"]["version"],
        operation_id="stage-b",
    )
    active_c = store.bind_page_source(
        "owner",
        workspace["id"],
        _proof("page-c"),
        source_revalidator=lambda _proof: True,
        base_version=pending_b["workspace"]["version"],
        operation_id="bind-c",
    )
    return store, workspace["id"], active_c["workspace"]["version"]


def test_raw_row_keyset_bounds_and_advances_over_pending_rows(tmp_path) -> None:
    store, workspace_id, _version = _fixture(tmp_path)
    before = store.audit_counts()

    first = store.scan_active_page_source_bindings(
        "owner", workspace_id, limit=1
    )
    assert first.scanned_count == 1
    assert [item.source_page_id for item in first.items] == ["page-a"]
    assert first.has_more is True
    assert first.next_cursor is not None

    second = store.scan_active_page_source_bindings(
        "owner", workspace_id, after=first.next_cursor, limit=1
    )
    assert second.scanned_count == 1
    assert second.items == ()
    assert second.has_more is True
    assert second.next_cursor is not None

    third = store.scan_active_page_source_bindings(
        "owner", workspace_id, after=second.next_cursor, limit=1
    )
    assert third.scanned_count == 1
    assert [item.source_page_id for item in third.items] == ["page-c"]
    assert third.has_more is False
    assert third.next_cursor is None
    store.assert_active_page_source_binding_authorized("owner", third.items[0])
    assert store.audit_counts() == before


def test_scan_is_admin_scoped_and_cursor_is_workspace_bound(tmp_path) -> None:
    store, workspace_id, version = _fixture(tmp_path)
    added = store.set_member(
        "owner",
        workspace_id,
        "member-one",
        role="member",
        base_version=version,
        operation_id="add-member-one",
    )
    with pytest.raises(PermissionError):
        store.scan_active_page_source_bindings("member-one", workspace_id, limit=1)
    with pytest.raises(FileNotFoundError):
        store.scan_active_page_source_bindings("outsider", workspace_id, limit=1)

    other = store.create_workspace(
        "owner", name="Other", operation_id="create-other"
    )["workspace"]
    cursor = ActiveLegacyPageBindingCursor(workspace_id, "owner", "page-a")
    with pytest.raises(ValueError, match="cursor workspace"):
        store.scan_active_page_source_bindings(
            "owner", other["id"], after=cursor, limit=1
        )
    assert added["workspace"]["version"] == version + 1


@pytest.mark.parametrize("invalid", [0, 33, False, "1"])
def test_invalid_limit_fails_before_catalog_open(tmp_path, monkeypatch, invalid) -> None:
    store = WorkspaceStore(catalog_path_for_user_data_root(tmp_path))
    monkeypatch.setattr(
        store,
        "_read_connection",
        lambda: (_ for _ in ()).throw(AssertionError("catalog must not open")),
    )
    with pytest.raises(ValueError):
        store.scan_active_page_source_bindings(
            "owner", "ws_not_opened", limit=invalid
        )


def test_old_enumerated_generation_is_superseded(tmp_path) -> None:
    store, workspace_id, version = _fixture(tmp_path)
    old = store.scan_active_page_source_bindings(
        "owner", workspace_id, limit=3
    ).items[0]
    store.stage_page_source_binding(
        "owner",
        workspace_id,
        _proof("page-a", revision=2),
        base_version=version,
        operation_id="restage-a",
    )
    with pytest.raises(WorkspacePageSourceSuperseded):
        store.assert_active_page_source_binding_authorized("owner", old)
