"""UI-META-003: durable collaborative titles across current UI read surfaces."""
import base64
from contextlib import contextmanager
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess

import pytest

from code_assist_v1.notion_collaboration import title_projection as projection
from code_assist_v1.notion_collaboration.canonical_authority import AuthorityUnavailable, NotFoundOrDenied
from code_assist_v1.notion_collaboration.durable_transport import YJS_UPDATE_ENCODING
from code_assist_v1.notion_collaboration.editing_authority import CollaborativeEditingTransportAuthority
from code_assist_v1.notion_collaboration.iam_runtime import collaborative_page_payload
from code_assist_v1.notion_collaboration.page_appearance import project_page_metadata
from code_assist_v1.notion_collaboration.protocol_contract import ProtocolEnvelope, session_binding_sha256
from code_assist_v1.notion_collaboration.selected_runtime_contract import (
    COMPACTION_RESPONSE_CONTRACT, runtime_binding_sha256, sha256_bytes,
)
from code_assist_v1.tests.notion_collaboration.test_collaborative_editing_authority import _environment, ORIGIN
from code_assist_v1.tests.notion_collaboration.test_page_appearance import snapshot
from code_assist_v1.tests.notion_collaboration.test_search_authority import _authority as search_environment


@pytest.fixture(scope="module")
def yjs():
    bundle = Path(__file__).parents[2] / "notion_static/vendor/yjs-13.6.32.bundle.js"
    script = r"""
require(process.argv[1]); const Y=globalThis.NotionYjs;
const encode=v=>Buffer.from(v).toString('base64');
const d=new Y.Doc();d.clientID=1234;const title=d.getText('page-title');title.insert(0,'Collaborative editing');
const base=Y.encodeStateAsUpdate(d), vector=Y.encodeStateVector(d);
title.delete(0,title.length);title.insert(0,'새 협업 제목');
const rename=Y.encodeStateAsUpdate(d,vector), snapshot=Y.encodeStateAsUpdate(d), stateVector=Y.encodeStateVector(d);
title.delete(0,title.length);const clear=Y.encodeStateAsUpdate(d,stateVector);
const left=new Y.Doc(),right=new Y.Doc();left.clientID=2345;right.clientID=3456;
Y.applyUpdate(left,base);Y.applyUpdate(right,base);
left.getText('page-title').insert(0,'왼쪽 ');right.getText('page-title').insert(0,'오른쪽 ');
const a=Y.encodeStateAsUpdate(left,vector),b=Y.encodeStateAsUpdate(right,vector);
Y.applyUpdate(left,b);Y.applyUpdate(right,a);
if(left.getText('page-title').toString()!==right.getText('page-title').toString())throw Error('No convergence');
const body=new Y.Doc();body.getText('body-only').insert(0,'body');
process.stdout.write(JSON.stringify({base:encode(base),rename:encode(rename),clear:encode(clear),
snapshot:encode(snapshot),stateVector:encode(stateVector),a:encode(a),b:encode(b),
mergedTitle:left.getText('page-title').toString(),body:encode(Y.encodeStateAsUpdate(body))}));
"""
    result = subprocess.run([shutil.which("node"), "-e", script, str(bundle)],
                            capture_output=True, check=True, timeout=15)
    raw = json.loads(result.stdout)
    return {key: value if key == "mergedTitle" else base64.b64decode(value) for key, value in raw.items()}


def append(authority, transport, workspace, frames, document="page-e1"):
    page = authority.read_document("owner", document)
    sequence = page["document_sequence"]
    client = f"client-title-{document}-{sequence}"
    session = session_binding_sha256(client)
    args = dict(origin=ORIGIN, subject_id="owner", workspace_id=workspace, document_id=document,
                client_id=client, session_binding_sha256=session)
    ticket = transport.issue_ticket(csrf_valid=True, capabilities=("read", "write"), now_ms=1000, **args)
    grant = transport.consume_ticket(ticket.token, now_ms=1001, **args)
    for index, payload in enumerate(frames, 1):
        envelope = ProtocolEnvelope.create(grant, message_type="crdt.update",
            operation_id=f"op-title-{document}-{sequence}-{index:04d}", client_sequence=index,
            acknowledgement_sequence=sequence, payload=payload, capabilities=("write",),
            payload_encoding=YJS_UPDATE_ENCODING)
        transport.accept_update(envelope, grant)
    return grant


def title(authority, workspace, actor="owner", document="page-e1"):
    return projection.read_current_titles(authority, actor, workspace, [{"id": document}])


def compact(transport, grant, yjs):
    work = transport.prepare_compaction(grant)
    payload, vector = yjs["snapshot"], yjs["stateVector"]
    response = {"contract": COMPACTION_RESPONSE_CONTRACT,
        "request_id":work.request["request_id"], "request_sha256":work.request["request_sha256"],
        "base_high_water_sequence":work.request["base_high_water_sequence"],
        "target_high_water_sequence":work.request["target_high_water_sequence"],
        "applied_update_count":work.request["ordered_update_count"],
        "snapshot_bytes":len(payload), "snapshot_sha256":sha256_bytes(payload),
        "state_vector_bytes":len(vector), "state_vector_sha256":sha256_bytes(vector),
        "runtime_binding_sha256":runtime_binding_sha256()}
    return transport.commit_compaction(grant, operation_id="op-title-compaction-0001", work=work,
                                       response=response, snapshot=payload, state_vector=vector)


def test_ui_meta_003_api_list_and_history_remain_distinct(tmp_path, yjs):
    authority, transport, _, catalog, workspace = _environment(tmp_path)
    append(authority, transport, workspace, [yjs["base"], yjs["rename"]])
    before = snapshot(catalog)
    raw = authority.read_document("owner", "page-e1")
    assert raw["title"] == "Collaborative editing"
    payload = collaborative_page_payload(authority, "owner", "page-e1")
    listed = project_page_metadata(authority, "owner", workspace, [{"id":"page-e1"}, {"id":"not-bound"}])
    assert payload["page"]["title"] == listed[0]["title"] == "새 협업 제목"
    assert payload["page"]["document_sequence"] == raw["document_sequence"]
    assert payload["page"]["title_projection"]["document_sequence"] == raw["document_sequence"]
    assert len(listed) == 1
    assert payload["blocks"] == [{key: block[key] for key in ("id", "type", "content", "props")} for block in raw["blocks"]]
    assert snapshot(catalog) == before  # No revision, receipt, event, or legacy write.


def test_ui_meta_003_snapshot_tail_explicit_empty_and_missing_title(tmp_path, yjs):
    authority, transport, _, _, workspace = _environment(tmp_path)
    assert title(authority, workspace)["page-e1"]["title"] == "Collaborative editing"
    append(authority, transport, workspace, [yjs["body"]])
    assert title(authority, workspace)["page-e1"]["title"] == "Collaborative editing"
    grant = append(authority, transport, workspace, [yjs["base"], yjs["rename"]])
    # Compaction in this scenario must retain the unrelated body update too.
    # Test its real snapshot boundary separately on the title document.
    authority.create_document("owner", workspace, "page-title-only", title="Initial", blocks=[],
        expected_workspace_epoch=authority.read_document("owner", "page-e1")["workspace_epoch"],
        operation_id="op-title-only-create-0001")
    grant = append(authority, transport, workspace, [yjs["base"], yjs["rename"]], "page-title-only")
    compact(transport, grant, yjs)
    assert title(authority, workspace, document="page-title-only")["page-title-only"]["title"] == "새 협업 제목"
    append(authority, transport, workspace, [yjs["clear"]], "page-title-only")
    assert title(authority, workspace, document="page-title-only")["page-title-only"]["title"] == ""


@pytest.mark.parametrize("order", [("a", "b"), ("b", "a")])
def test_ui_meta_003_concurrent_edits_use_yjs_convergence(tmp_path, yjs, order):
    authority, transport, _, _, workspace = _environment(tmp_path)
    append(authority, transport, workspace, [yjs["base"], *(yjs[key] for key in order)])
    assert title(authority, workspace)["page-e1"]["title"] == yjs["mergedTitle"]


def test_ui_meta_003_permission_rechecked_after_decode_and_cache(tmp_path, yjs, monkeypatch):
    authority, transport, _, _, workspace = _environment(tmp_path)
    append(authority, transport, workspace, [yjs["base"], yjs["rename"]])
    assert title(authority, workspace, "content-editor")["page-e1"]["title"] == "새 협업 제목"
    decode = projection._materialize

    def revoke(documents):
        result = decode(documents)
        page = authority.read_document("owner", "page-e1")
        authority.revoke_page_grant("owner", "page-e1", "subject", "content-editor",
            expected_workspace_epoch=page["workspace_epoch"], expected_document_epoch=page["document_epoch"],
            operation_id="op-title-revoke-0001")
        return result

    monkeypatch.setattr(projection, "_materialize", revoke)
    assert title(authority, workspace, "content-editor") == {}
    monkeypatch.setattr(projection, "_materialize", lambda _: pytest.fail("unauthorized payload decoded"))
    assert title(authority, workspace, "content-editor") == {}


def test_ui_meta_003_changed_sequence_reloads_metadata_and_invalid_bytes_fail(tmp_path, yjs, monkeypatch):
    authority, transport, _, _, workspace = _environment(tmp_path)
    append(authority, transport, workspace, [yjs["base"], yjs["rename"]])
    decode = projection._materialize
    changed = False

    def change_once(documents):
        nonlocal changed
        result = decode(documents)
        if not changed:
            changed = True
            append(authority, transport, workspace, [yjs["clear"]])
        return result

    monkeypatch.setattr(projection, "_materialize", change_once)
    assert collaborative_page_payload(authority, "owner", "page-e1")["page"]["title"] == ""
    monkeypatch.setattr(projection, "_materialize", decode)
    append(authority, transport, workspace, [b"not a Yjs update"])
    with pytest.raises(AuthorityUnavailable, match="title is unavailable"):
        title(authority, workspace)


def test_ui_meta_003_search_filters_current_title_before_limit_and_restore(tmp_path, yjs):
    authority, root, catalog = search_environment(tmp_path)
    transport = CollaborativeEditingTransportAuthority.open(catalog, root)
    initial = authority.read_document("owner", "page-e1")
    workspace = initial["workspace_id"]
    authority.search("owner", workspace, query="Collaborative")  # Build the old-title index.
    append(authority, transport, workspace, [yjs["base"], yjs["rename"]])
    current = authority.search("owner", workspace, query="새 협업 제목", title_only=True, limit=1)
    assert current["count"] == 1 and current["items"][0]["title"] == "새 협업 제목"
    assert authority.search("owner", workspace, query="Collaborative editing", title_only=True)["count"] == 0
    page = authority.read_document("owner", "page-e1")
    authority.restore_revision("owner", "page-e1", 1, expected_revision=page["revision"],
        expected_document_sequence=page["document_sequence"], expected_workspace_epoch=page["workspace_epoch"],
        expected_document_epoch=page["document_epoch"], operation_id="op-title-restore-0001")
    restored = title(authority, workspace)["page-e1"]
    assert restored["title"] == initial["title"]
    assert restored["title_projection"]["generation"] == 2
    assert authority.search("owner", workspace, query="새 협업 제목")["count"] == 0


def test_ui_meta_003_node_runs_after_read_transaction_closes(tmp_path, yjs, monkeypatch):
    authority, transport, _, catalog, workspace = _environment(tmp_path)
    append(authority, transport, workspace, [yjs["base"], yjs["rename"]])
    original = projection._materialize
    reader = authority._reader
    active_readers = 0

    @contextmanager
    def tracked_reader():
        nonlocal active_readers
        with reader() as con:
            active_readers += 1
            try:
                yield con
            finally:
                active_readers -= 1

    def assert_no_lock(documents):
        assert active_readers == 0
        with sqlite3.connect(catalog, timeout=0.01) as con:
            con.execute("BEGIN EXCLUSIVE")
            con.rollback()
        return original(documents)

    monkeypatch.setattr(projection, "_materialize", assert_no_lock)
    monkeypatch.setattr(authority, "_reader", tracked_reader)
    assert collaborative_page_payload(authority, "owner", "page-e1")["page"]["title"] == "새 협업 제목"


def test_ui_meta_003_empty_body_does_not_match_old_title_and_title_sort(tmp_path, yjs):
    authority, root, catalog = search_environment(tmp_path)
    transport = CollaborativeEditingTransportAuthority.open(catalog, root)
    page = authority.read_document("owner", "page-e1")
    workspace = page["workspace_id"]
    for document, label in (("page-empty-title", "OldUniqueTitle"), ("page-sort-title", "AAA")):
        authority.create_document("owner", workspace, document, title=label, blocks=[],
            expected_workspace_epoch=page["workspace_epoch"], operation_id=f"op-title-create-{document}")
    authority.search("owner", workspace, query="OldUniqueTitle")
    append(authority, transport, workspace, [yjs["base"], yjs["rename"]], "page-empty-title")
    assert authority.search("owner", workspace, query="OldUniqueTitle")["count"] == 0
    current = authority.search("owner", workspace, query="새 협업 제목")
    assert current["items"][0]["snippet"] == "새 협업 제목"
    sorted_result = authority.search("owner", workspace, sort="title", limit=1)
    assert sorted_result["items"][0]["title"] == "AAA" and sorted_result["has_more"]


def test_ui_meta_003_meeting_title_filter_uses_current_yjs_title(tmp_path, yjs):
    authority, root, catalog = search_environment(tmp_path)
    transport = CollaborativeEditingTransportAuthority.open(catalog, root)
    page = authority.read_document("owner", "page-e1")
    workspace = page["workspace_id"]
    authority.create_document("owner", workspace, "page-meeting-title", title="Old meeting title",
        blocks=[{"id":"meeting-title-note", "type":"ai_notes", "content":"", "props":{}}],
        expected_workspace_epoch=page["workspace_epoch"], operation_id="op-title-meeting-create-0001")
    append(authority, transport, workspace, [yjs["base"], yjs["rename"]], "page-meeting-title")
    meeting = authority.list_meetings("owner", workspace, query="새 협업 제목")
    assert meeting["count"] == 1 and meeting["items"][0]["title"] == "새 협업 제목"
    assert authority.list_meetings("owner", workspace, query="Old meeting title")["count"] == 0
    with pytest.raises(NotFoundOrDenied):
        authority.list_meetings("outsider", workspace)
