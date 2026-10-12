"""UI-PAGE-COPY: history keeps exact current titles, including an empty page."""
import pytest

from code_assist_v1.notion_collaboration.iam_runtime import CollaborativeWorkspaceStore
from code_assist_v1.tests.notion_collaboration.test_page_copy_commit import (
    environment, member_environment, native_environment, archive_environment,
    guest_environment, image_environment, copy, request, dump, current_update, insert_block,
)


@pytest.mark.parametrize("title,clear", [("", True), ("  제목 앞뒤 공백  ", False)])
def test_ui_copy_history_restores_exact_title_and_body_after_restart(environment, title, clear):
    store, ws = environment
    current_update(store, ws, clear=clear, title="삭제 전 제목" if clear else title)
    source_before = store.iam.read_document("owner", "page-e1")
    result = copy(store, ws)
    target = result["page"]["id"]
    initial = store.iam.get_revision("owner", target, 1)
    assert result["page"]["title"] == initial["snapshot"]["title"] == title
    if clear:
        assert initial["snapshot"]["blocks"] == []
    else:
        assert initial["snapshot"]["blocks"][0]["content"].startswith("<strong>최신 본문</strong>")
    insert_block(store, target, "copy-history-later-block")
    changed = store.iam.read_document("owner", target)
    args = dict(operation_id="copy-history-exact-restore", expected_revision=changed["revision"], **request(store, target))
    restored = store.iam.restore_revision("owner", target, 1, **args)
    restarted = CollaborativeWorkspaceStore(store.db_path, user_data_root=store.user_data_root,
        identity_directory=store.identity_directory)
    current = restarted.iam.read_document("owner", target)
    assert current["title"] == title, "restoring a revision must not trim or replace its title"
    assert current["blocks"] == initial["snapshot"]["blocks"]
    assert restored["crdt_generation"] == 2
    assert restarted.iam.get_revision("owner", target, restored["revision"])["snapshot"]["title"] == title
    committed = dump(restarted)
    assert restarted.iam.restore_revision("owner", target, 1, **args) == restored
    assert dump(restarted) == committed
    assert restarted.iam.read_document("owner", "page-e1") == source_before
