"""UI-PARENT-STRUCTURE: read-only copy preparation retains the proven structure."""
import pytest

from code_assist_v1.notion_collaboration import page_copy_source
from code_assist_v1.tests.notion_collaboration.test_parent_structure_rebase import (
    environment, archive_environment, guest_environment, image_environment, setup, body, dump,
)
from code_assist_v1.tests.notion_collaboration.test_page_copy_source import request as fences
from code_assist_v1.tests.notion_collaboration.test_synced_copy_references import text_update


@pytest.mark.parametrize('action', ['delete', 'move'])
def test_ui_copy_preparation_keeps_pending_structure_and_raw_source_exact(environment, action):
    store, ws = environment
    original, expected = setup(store, ws, action)
    before = dump(store)
    prepared = page_copy_source.prepare_page_copy(store.iam, 'owner', ws, 'page-e1', **fences(store))
    descriptor = prepared.documents[0].descriptor()
    assert descriptor['blocks'] == expected and descriptor['title'] == original['title']
    assert dump(store) == before
    assert body(store) == original, 'read-only preparation must not rewrite the original frames'


@pytest.mark.parametrize('action', ['delete', 'move'])
def test_ui_copy_preparation_refuses_older_uncertified_structure_without_writes(environment, action, monkeypatch):
    from code_assist_v1.notion_collaboration import parent_historical_structure
    monkeypatch.setattr(parent_historical_structure, 'capture', lambda *args: None)
    store, ws = environment
    setup(store, ws, action)
    text_update(store, ws, 'page-e1', 'retained-structure-note', '현재 문단')
    before = dump(store)
    with pytest.raises(page_copy_source.CopySourceChanged, match='structures differ'):
        page_copy_source.prepare_page_copy(store.iam, 'owner', ws, 'page-e1', **fences(store))
    assert dump(store) == before
