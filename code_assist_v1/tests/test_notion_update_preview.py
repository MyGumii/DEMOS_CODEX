from __future__ import annotations

import pytest

from code_assist_v1 import notion_store as ns
from code_assist_v1.notion_update_preview import page_change_preview


def test_page_change_preview_names_real_block_edits_and_bounds_output() -> None:
    before = {"title": "Before", "icon": "", "blocks": [
        {"id": "a", "type": "paragraph", "content": "old text", "props": {}},
        {"id": "b", "type": "paragraph", "content": "removed text", "props": {}},
    ]}
    after = {"title": "After", "icon": "", "blocks": [
        {"id": "a", "type": "paragraph", "content": "new text", "props": {}},
        {"id": "c", "type": "paragraph", "content": "added text", "props": {}},
    ]}
    preview = page_change_preview(before, after)
    assert preview == {"items": [
        {"kind": "title", "text": "After"},
        {"kind": "edited", "text": "new text"},
        {"kind": "added", "text": "added text"},
    ], "remaining": 1}
    assert page_change_preview(after, after) == {"items": [], "remaining": 0}


def test_page_change_preview_includes_saved_cover_lock_and_database_changes() -> None:
    before = {"title": "Page", "icon": "", "cover": "old", "settings": {"locked": False},
              "blocks": [], "databases": [{"id": "db-1", "title": "Tasks", "rows": []}]}
    after = {"title": "Page", "icon": "", "cover": "new", "settings": {"locked": True},
             "blocks": [], "databases": [{"id": "db-1", "title": "Tasks", "rows": [{"id": "row-1"}]}]}
    assert page_change_preview(before, after) == {"items": [
        {"kind": "cover", "text": "페이지 커버 변경"},
        {"kind": "locked", "text": "페이지 잠금"},
        {"kind": "database", "text": "Tasks"},
    ], "remaining": 0}
    unlocked = {**after, "settings": {"locked": False}}
    assert page_change_preview(after, unlocked)["items"] == [
        {"kind": "locked", "text": "페이지 잠금 해제"},
    ]


def test_personal_page_update_feed_paginates_snapshots_and_denies_others(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(ns, "USER_DATA_DIR", tmp_path)
    page_id = ns.create_page("owner", title="Preview page")["page"]["id"]
    ns.replace_blocks("owner", page_id, [
        {"id": "a", "type": "paragraph", "content": "first", "props": {}},
    ])
    ns.replace_blocks("owner", page_id, [
        {"id": "a", "type": "paragraph", "content": "changed", "props": {}},
        {"id": "b", "type": "paragraph", "content": "removed later", "props": {}},
    ])
    ns.replace_blocks("owner", page_id, [
        {"id": "a", "type": "paragraph", "content": "changed", "props": {}},
    ])
    first = ns.list_page_updates("owner", page_id, limit=2)
    assert len(first["items"]) == 2
    assert first["has_more"] is True
    historical = ns.get_page_version("owner", page_id, first["items"][1]["id"])
    assert historical["blocks"][1]["content"] == "removed later"
    assert historical["current"] is False
    assert ns.get_page("owner", page_id)["blocks"][-1]["content"] == "changed"
    assert first["items"][0]["preview"]["items"] == [
        {"kind": "deleted", "text": "removed later"},
    ]
    assert {entry["kind"] for entry in first["items"][1]["preview"]["items"]} == {"edited", "added"}
    second = ns.list_page_updates("owner", page_id, limit=2,
                                  before_version_id=first["next_before_id"])
    assert len(second["items"]) == 2
    assert second["has_more"] is False
    assert second["items"][0]["preview"]["items"][0]["text"] == "first"
    with pytest.raises((FileNotFoundError, PermissionError)):
        ns.list_page_updates("outsider", page_id)
    with pytest.raises((FileNotFoundError, PermissionError)):
        ns.get_page_version("outsider", page_id, first["items"][1]["id"])
    with pytest.raises(FileNotFoundError):
        ns.get_page_version("owner", page_id, "missing")
    with pytest.raises(FileNotFoundError):
        ns.list_page_updates("owner", page_id, before_version_id="unknown")
