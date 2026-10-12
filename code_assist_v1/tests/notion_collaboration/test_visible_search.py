"""UI search: visible fields, durable body replay and projection commit fences."""
import base64
from contextlib import contextmanager
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess

import pytest

from code_assist_v1.notion_collaboration import search_projection, visible_text
from code_assist_v1.notion_collaboration.canonical_authority import AuthorityUnavailable, NotFoundOrDenied
from code_assist_v1.notion_collaboration.editing_authority import CollaborativeEditingTransportAuthority
from code_assist_v1.notion_collaboration.search_authority import _text
from code_assist_v1.tests.notion_collaboration.test_search_authority import _authority, _grant
from code_assist_v1.tests.notion_collaboration.test_title_projection import append, compact


def test_ui_search_visible_fields_preserve_literals_and_exclude_configuration():
    descriptor = {"blocks": [
        {"id": "private-block-id", "type": "paragraph", "position": 12345,
         "content": "paragraph 42 550e8400-e29b-41d4-a716-446655440000 <literal>",
         "props": {"sync_id": "private-sync-id", "version": 987654}},
        {"id": "rich-id", "type": "heading1", "content": "stale-rich-content",
         "props": {"rich_text_html": '<b>A&amp;B</b><br>C <a href="/page/private-target-id">visible label</a>'
                                           '<script>hidden script</script>'}},
        {"type": "code", "content": '<tag attr="x">&amp;</tag>',
         "props": {"language": "private-language", "caption": "code caption"}},
        {"type": "simple_table", "props": {"rows": [["표 A", 7], ["표 B", "표 C"]], "width": 234567}},
        {"type": "file", "content": "attachment caption", "props": {"name": "memo.pdf", "file_id": "private-file-id"}},
        {"type": "button", "content": "private-button-config", "props": {"label": "실행", "action": "private-action"}},
        {"type": "tabs", "props": {"tabs": [{"id": "private-tab-id", "name": "안건"}]}},
        {"type": "ai_notes", "props": {"transcript": "회의 원문", "translation_text": "translated notes",
           "model_id": "private-model-id", "instructions": "private-prompt",
           "result": {"summary": "결과 요약", "action_items": [{"id": "private-task-id", "task": "다음 작업"}]}}},
    ]}
    text = visible_text.document_text(descriptor)
    assert "private-block-id" in _text(descriptor)  # Reproduces the old generic traversal.
    assert "private-" not in text and "987654" not in text and "234567" not in text
    for expected in ("paragraph 42 550e8400-e29b-41d4-a716-446655440000 <literal>",
                     "A&B", "visible label", '<tag attr="x">&amp;</tag>', "code caption",
                     "표 A", "7", "memo.pdf", "실행", "안건", "회의 원문", "translated notes", "결과 요약", "다음 작업"):
        assert expected in text
    assert "stale-rich-content" not in text and "hidden script" not in text
    assert visible_text.document_text(descriptor, limit=12) == text[:12]


def test_ui_search_database_uses_canonical_cells_and_only_display_values():
    props = [{"id": "title-id", "name": "Name", "type": "title"},
             {"id": "number-id", "name": "Estimate", "type": "number"},
             {"id": "tags-id", "name": "Tags", "type": "multi_select"},
             {"id": "file-id", "name": "Files", "type": "files"},
             {"id": "person-id", "name": "Owner", "type": "person"},
             {"id": "relation-id", "name": "Related", "type": "relation"}]
    cells = {"title-id": "Ship release", "number-id": 42, "tags-id": ["ready", "verified"],
             "file-id": [{"name": "results.pdf", "file_id": "hidden-file-reference"}],
             "person-id": ["hidden-person-reference"], "relation-id": ["hidden-page-reference"]}
    descriptor = {"blocks": [{"id": "db-block", "type": "table", "content": "stale DB title",
       "props": {"database_id": "db-id", "rows": [{"title-id": "stale row"}],
                 "properties": props, "filters": [{"value": "hidden-filter"}]}}],
       "databases": [{"id": "db-id", "title": "Current database", "properties": props,
                      "rows": [{"id": "hidden-row-id", "version": 919191, "cells": cells}],
                      "views": [{"name": "Active view"}, {"name": "hidden old view", "state": "archived"}],
                      "templates": [{"body": "hidden template"}]}]}
    text = visible_text.document_text(descriptor)
    assert "hidden" not in text and "stale" not in text and "919191" not in text
    for expected in ("Current database", "Ship release", "42", "ready", "verified", "results.pdf", "Active view"):
        assert expected in text


@pytest.fixture(scope="module")
def frames():
    bundle = Path(__file__).parents[2] / "notion_static/vendor/yjs-13.6.32.bundle.js"
    script = r'''
require(process.argv[1]);const Y=globalThis.NotionYjs,d=new Y.Doc();d.clientID=753159;
const blocks=d.getMap('blocks'),order=d.getArray('block-order');d.getText('page-title').insert(0,'Visible projection');
function add(id,type,content,props){const b=new Y.Map();blocks.set(id,b);b.set('type',type);b.set('props',JSON.stringify(props));
 const text=new Y.Text();b.set('content',text);text.insert(0,content);order.push([id]);}
add('hidden-body-id','paragraph','OldBodyToken',{});
add('hidden-table-id','simple_table','',{rows:[['OldTableToken','42']],layout_group:'hidden-layout-id'});
const enc=()=>Buffer.from(Y.encodeStateAsUpdate(d)).toString('base64');const base=enc();
const text=blocks.get('hidden-body-id').get('content');text.delete(0,text.length);text.insert(0,'LatestBodyToken');
blocks.get('hidden-body-id').set('props',JSON.stringify({rich_text_html:'<b>LatestBodyToken</b> <span data-notion-reminder="2027-01-01T00:00:00Z" data-notion-reminder-user="owner">Follow up</span>'}));
blocks.get('hidden-table-id').set('props',JSON.stringify({rows:[['LatestTableToken','42']]}));
const current=enc(),currentVector=Buffer.from(Y.encodeStateVector(d)).toString('base64');
order.delete(0,order.length);const empty=enc();
process.stdout.write(JSON.stringify({base,current,currentVector,empty}));
'''
    result = subprocess.run([shutil.which("node"), "-e", script, str(bundle)],
                            capture_output=True, check=True, timeout=15)
    return {key: base64.b64decode(value) for key, value in json.loads(result.stdout).items()}


def _environment(tmp_path):
    authority, root, catalog = _authority(tmp_path)
    transport = CollaborativeEditingTransportAuthority.open(catalog, root)
    workspace = authority.read_document("owner", "page-e1")["workspace_id"]
    return authority, transport, root, catalog, workspace


def _index(catalog):
    with sqlite3.connect(catalog) as con:
        return tuple(con.execute("SELECT * FROM workspace_collaboration_search_documents ORDER BY document_id")), tuple(
            con.execute("SELECT * FROM workspace_collaboration_search_state ORDER BY workspace_id"))


def test_ui_search_latest_body_table_reminder_and_deleted_blocks(tmp_path, frames):
    authority, transport, _, _, workspace = _environment(tmp_path)
    append(authority, transport, workspace, [frames["base"]])
    assert authority.search("owner", workspace, query="OldBodyToken")["count"] == 1
    grant = append(authority, transport, workspace, [frames["current"]])
    assert "LatestBodyToken" not in str(authority.read_document("owner", "page-e1")["blocks"])
    result = authority.search("owner", workspace, query="LatestBodyToken LatestTableToken")
    assert result["count"] == 1 and "hidden-" not in result["items"][0]["snippet"]
    assert authority.search("owner", workspace, query="OldBodyToken")["count"] == 0
    assert len(authority.list_reminders("owner", workspace)["items"]) == 1
    compact(transport, grant, {"snapshot": frames["current"], "stateVector": frames["currentVector"]})
    authority.rebuild_search_index("owner", workspace)
    assert authority.search("owner", workspace, query="LatestBodyToken LatestTableToken")["count"] == 1
    append(authority, transport, workspace, [frames["empty"]])
    assert authority.search("owner", workspace, query="LatestBodyToken")["count"] == 0
    assert authority.search("owner", workspace, query="LatestTableToken")["count"] == 0
    assert authority.list_reminders("owner", workspace)["items"] == []


def test_ui_search_extractor_contract_invalidates_existing_index_without_source_edit(tmp_path, monkeypatch):
    authority, _, _, catalog, workspace = _environment(tmp_path)
    page = authority.read_document("owner", "page-e1")
    with monkeypatch.context() as old:
        old.setattr(visible_text, "CONTRACT", "previous-visible-text-contract")
        old.setattr(visible_text, "document_text", lambda descriptor, **kwargs: _text(descriptor))
        authority.search("owner", workspace)
    old_state = _index(catalog)[1]
    result = authority.search("owner", workspace)
    assert result["index"]["converged"] is True
    assert _index(catalog)[1] != old_state
    assert authority.read_document("owner", "page-e1") == page


def test_ui_search_decode_outside_transactions_and_retry_current_source(tmp_path, frames, monkeypatch):
    authority, transport, _, catalog, workspace = _environment(tmp_path)
    append(authority, transport, workspace, [frames["base"]])
    decode, reader = search_projection.materialize, authority._reader
    active_readers, calls = 0, 0

    @contextmanager
    def tracked_reader():
        nonlocal active_readers
        with reader() as con:
            active_readers += 1
            try:
                yield con
            finally:
                active_readers -= 1

    def change_after_decode(documents):
        nonlocal calls
        assert active_readers == 0
        with sqlite3.connect(catalog, timeout=0.01) as con:
            con.execute("BEGIN EXCLUSIVE")
            con.rollback()
        result = decode(documents)
        calls += 1
        if calls == 1:
            append(authority, transport, workspace, [frames["current"]])
        return result

    monkeypatch.setattr(authority, "_reader", tracked_reader)
    monkeypatch.setattr(search_projection, "materialize", change_after_decode)
    assert authority.search("owner", workspace, query="LatestBodyToken")["count"] == 1
    assert calls == 2
    assert authority.search("owner", workspace, query="OldBodyToken")["count"] == 0


def test_ui_search_revoke_during_prepare_and_invalid_replay_do_not_replace_index(tmp_path, frames, monkeypatch):
    authority, transport, _, catalog, workspace = _environment(tmp_path)
    append(authority, transport, workspace, [frames["base"]])
    _grant(authority, "page-e1", "outsider", "op-visible-search-grant-0001")
    authority.search("outsider", workspace, query="OldBodyToken")
    append(authority, transport, workspace, [frames["current"]])
    before = _index(catalog)[0]
    decode = search_projection.materialize

    def revoke(documents):
        result = decode(documents)
        page = authority.read_document("owner", "page-e1")
        authority.revoke_page_grant("owner", "page-e1", "subject", "outsider",
            expected_workspace_epoch=page["workspace_epoch"], expected_document_epoch=page["document_epoch"],
            operation_id="op-visible-search-revoke-0001")
        return result

    monkeypatch.setattr(search_projection, "materialize", revoke)
    with pytest.raises(NotFoundOrDenied):
        authority.rebuild_search_index("outsider", workspace)
    assert _index(catalog)[0] == before
    monkeypatch.setattr(search_projection, "materialize", decode)
    append(authority, transport, workspace, [b"invalid Yjs bytes"])
    with pytest.raises(AuthorityUnavailable, match="search content is unavailable"):
        authority.rebuild_search_index("owner", workspace)
    assert _index(catalog)[0] == before


def test_ui_search_projection_failure_rolls_back_all_index_rows(tmp_path, frames, monkeypatch):
    authority, transport, _, catalog, workspace = _environment(tmp_path)
    append(authority, transport, workspace, [frames["base"]])
    authority.search("owner", workspace, query="OldBodyToken")
    append(authority, transport, workspace, [frames["current"]])
    before = _index(catalog)

    def fail_after_index_rows(*_args):
        raise RuntimeError("injected projection failure")

    monkeypatch.setattr(authority, "_projection_sha256", fail_after_index_rows)
    with pytest.raises(RuntimeError, match="injected projection failure"):
        authority.rebuild_search_index("owner", workspace)
    assert _index(catalog) == before
