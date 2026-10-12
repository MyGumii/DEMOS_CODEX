"""UI-LINKED-COPY: shared source data, local views and current source access."""
import base64
from contextlib import contextmanager
from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from code_assist_v1.notion_collaboration import linked_databases as linked
from code_assist_v1.notion_collaboration.canonical_authority import NotFoundOrDenied
from code_assist_v1.notion_collaboration.editing_authority import CollaborativeEditingTransportAuthority
from code_assist_v1.notion_collaboration.page_copy_remap import CopyShapeUnavailable
from code_assist_v1.tests.notion_collaboration.test_page_copy_commit import (
    environment, member_environment, native_environment, archive_environment, guest_environment, image_environment,
    copy, create, request, dump, registrations, insert_block,
)
from code_assist_v1.tests.notion_collaboration.test_title_projection import append
from code_assist_v1.tests.notion_collaboration.test_search_authority import _grant
from code_assist_v1.tests.notion_collaboration.test_guest_navigation import revoke
from code_assist_v1.tests.notion_collaboration.test_database_features import _schema


def setup_pair(store, ws):
    _schema(store.iam, "database.property.create", "linked-title", {"name": "Name", "type": "title", "config": {}},
        "linked-title-property", actor="owner")
    child = create(store, ws, operation="linked-instance-page")["page"]["id"]
    database = store.iam.read_database("owner", "database-e1")
    title = next(prop["id"] for prop in database["properties"] if prop["type"] == "title")
    props = {"linked_database": {"document_id": "page-e1", "database_id": "database-e1"},
        "views": [{"id": "linked-view", "name": "Local A", "type": "table",
            "config": {"filters": [{"property": title, "operator": "contains", "value": "A"}]}}],
        "active_view_id": "linked-view", "indent": 1}
    store.iam.execute_typed_command("owner", child, command_kind="block.insert", target_id="linked-block",
        payload={"type": "table", "content": "", "props": props, "after_block_id": None},
        operation_id="linked-insert", **request(store, child))
    return child, title


def read(store, document, block="linked-block", actor="owner"):
    return linked.read_linked_database(store.iam, actor, document, block)


def instance(store, document):
    return next(block for block in store.iam.read_document("owner", document)["blocks"]
        if "linked_database" in block["props"])


def update_views(store, ws, document, block_id, value):
    with store.iam._reader() as con:
        frames = linked.synced_blocks._frames(con, document, linked.synced_blocks._generation(con, document))
        seed = store.iam._editing_snapshot(con, store.iam._document(con, document))
    bundle = Path(__file__).parents[2] / "notion_static/vendor/yjs-13.6.32.bundle.js"
    script = r"""
const fs=require('node:fs');require(process.argv[1]);const Y=globalThis.NotionYjs;
const input=JSON.parse(fs.readFileSync(0,'utf8')),d=new Y.Doc();
for(const frame of input.frames)Y.applyUpdate(d,new Uint8Array(Buffer.from(frame,'base64')));
const vector=Y.encodeStateVector(d);
if(!d.share.has('block-order')){
  d.getArray('block-order').insert(0,input.seed.blocks.map(b=>b.id));
  for(const b of input.seed.blocks){const m=new Y.Map();d.getMap('blocks').set(b.id,m);
    m.set('type',b.type);m.set('props',JSON.stringify(b.props));
    const t=new Y.Text();m.set('content',t);t.insert(0,b.content);}
}
const block=d.getMap('blocks').get(input.block);
if(!block)throw Error('Missing active source');
const props=JSON.parse(block.get('props'));props.views[0].config.filters[0].value=input.value;
block.set('props',JSON.stringify(props));
process.stdout.write(Buffer.from(Y.encodeStateAsUpdate(d,vector)).toString('base64'));
"""
    result = subprocess.run([shutil.which("node"), "-e", script, str(bundle)],
        input=json.dumps({"seed": seed, "frames": [base64.b64encode(frame).decode() for frame in frames],
            "block": block_id, "value": value}).encode(), capture_output=True, check=True, timeout=15)
    transport = CollaborativeEditingTransportAuthority.open(store.db_path, store.user_data_root)
    append(store.iam, transport, ws, [base64.b64decode(result.stdout)], document=document)


def set_title(store, title, value, operation):
    database = store.iam.read_database("owner", "database-e1")
    row = database["rows"][0]
    store.iam.execute_typed_command("owner", "page-e1", command_kind="database.cell.set",
        target_id=f'{row["id"]}:{title}', payload={"row_id": row["id"], "property_id": title, "value": value},
        expected_target_version=row["cell_versions"].get(title, 0), operation_id=operation, **request(store))


def test_ui_linked_copy_external_shared_data_and_independent_current_yjs_views(environment, monkeypatch):
    store, ws = environment
    child, title = setup_pair(store, ws)
    update_views(store, ws, child, "linked-block", "current A")
    opened, original_reader = 0, store.iam._reader
    @contextmanager
    def tracked_reader():
        nonlocal opened
        with original_reader() as con:
            opened += 1
            try:
                yield con
            finally:
                opened -= 1
    materialize = linked.synced_blocks.search_projection.materialize
    def outside(*args, **kwargs):
        assert opened == 0, "Yjs replay cannot hold a SQLite reader open"
        return materialize(*args, **kwargs)
    monkeypatch.setattr(store.iam, "_reader", tracked_reader)
    monkeypatch.setattr(linked.synced_blocks.search_projection, "materialize", outside)
    before = dump(store)
    assert read(store, child)["views"][0]["config"]["filters"][0]["value"] == "current A"
    assert dump(store) == before
    source_views = store.iam.read_database("owner", "database-e1")["views"]
    copied = copy(store, ws, child, operation="linked-external-copy")["page"]["id"]
    block = instance(store, copied)
    assert block["props"]["linked_database"] == {"document_id": "page-e1", "database_id": "database-e1"}
    assert block["props"]["views"][0]["id"] != "linked-view"
    snapshot = store.iam.get_revision("owner", copied, 1)["snapshot"]
    assert snapshot["databases"] == []
    assert block["content"] == "" and not linked.CACHE_KEYS.intersection(block["props"])
    update_views(store, ws, copied, block["id"], "B")
    set_title(store, title, "A changed from source", "linked-source-change")
    original, duplicate = read(store, child), read(store, copied, block["id"])
    assert original["database"]["rows"] == duplicate["database"]["rows"]
    assert original["database"]["rows"][0]["cells"][title] == "A changed from source"
    assert original["views"][0]["config"]["filters"][0]["value"] == "current A"
    assert duplicate["views"][0]["config"]["filters"][0]["value"] == "B"
    assert store.iam.read_database("owner", "database-e1")["views"] == source_views
    again = copy(store, ws, copied, operation="linked-copy-again")["page"]["id"]
    again_block = instance(store, again)
    assert read(store, again, again_block["id"])["views"][0]["config"]["filters"][0]["value"] == "B"


def test_ui_linked_copy_internal_source_and_property_remap(environment):
    store, ws = environment
    child, title = setup_pair(store, ws)
    copied = copy(store, ws, operation="linked-subtree-copy")["page"]["id"]
    mapped = {row["source_document_id"]: row["document_id"] for row in registrations(store)}
    block = instance(store, mapped[child])
    result = read(store, mapped[child], block["id"])
    assert result["source"]["document_id"] == copied
    assert result["source"]["database_id"] != "database-e1"
    mapped_title = next(prop["id"] for prop in result["database"]["properties"] if prop["type"] == "title")
    assert mapped_title != title
    assert result["views"][0]["config"]["filters"][0]["property"] == mapped_title
    rows = deepcopy(result["database"]["rows"])
    set_title(store, title, "original changed independently", "linked-original-independent")
    assert read(store, mapped[child], block["id"])["database"]["rows"] == rows


@pytest.mark.parametrize("revoked", ["instance", "source", "identity"])
def test_ui_linked_reader_rechecks_both_grants_and_identity_after_replay(environment, monkeypatch, revoked):
    store, ws = environment
    child, _title = setup_pair(store, ws)
    with pytest.raises(NotFoundOrDenied):
        read(store, child, actor="outsider")
    revoke(store, ws)
    _grant(store.iam, child, "outsider", "linked-view-instance")
    with pytest.raises(NotFoundOrDenied):
        read(store, child, actor="outsider")
    _grant(store.iam, "page-e1", "outsider", "linked-view-source")
    assert read(store, child, actor="outsider")["database"]["effective_role"] == "view"
    materialize, after = linked.synced_blocks.search_projection.materialize, []
    def revoke_during_replay(*args, **kwargs):
        result = materialize(*args, **kwargs)
        if revoked == "identity":
            active = store.iam.identity_directory.is_active_human
            monkeypatch.setattr(store.iam.identity_directory, "is_active_human", lambda subject: subject != "outsider" and active(subject))
        else:
            document = child if revoked == "instance" else "page-e1"
            page = store.iam.read_document("owner", document)
            store.iam.revoke_page_grant("owner", document, "subject", "outsider",
                expected_workspace_epoch=page["workspace_epoch"], expected_document_epoch=page["document_epoch"],
                operation_id="linked-revoke-during-replay")
        after.extend(dump(store))
        return result
    monkeypatch.setattr(linked.synced_blocks.search_projection, "materialize", revoke_during_replay)
    with pytest.raises(NotFoundOrDenied):
        read(store, child, actor="outsider")
    assert dump(store) == tuple(after)


def test_ui_linked_reader_rejects_instance_race_and_reads_latest_source_snapshot(environment, monkeypatch):
    store, ws = environment
    child, title = setup_pair(store, ws)
    materialize = linked.synced_blocks.search_projection.materialize
    def source_changed(*args, **kwargs):
        result = materialize(*args, **kwargs)
        set_title(store, title, "updated during instance replay", "linked-concurrent-row")
        return result
    with monkeypatch.context() as patch:
        patch.setattr(linked.synced_blocks.search_projection, "materialize", source_changed)
        assert read(store, child)["database"]["rows"][0]["cells"][title] == "updated during instance replay"
    after = []
    def instance_changed(*args, **kwargs):
        result = materialize(*args, **kwargs)
        insert_block(store, child, "linked-concurrent-block")
        after.extend(dump(store))
        return result
    monkeypatch.setattr(linked.synced_blocks.search_projection, "materialize", instance_changed)
    with pytest.raises(linked.LinkedDatabaseSourceChanged):
        read(store, child)
    assert dump(store) == tuple(after)


def test_ui_linked_reader_and_copy_reject_ambiguous_cached_source_and_budget(environment, monkeypatch):
    store, ws = environment
    child, _title = setup_pair(store, ws)
    block = instance(store, child)
    for mutate in (
        lambda b: b.update(content="private cached title"),
        lambda b: b["props"].update(rows=[]),
        lambda b: b["props"].update(database_id="different-source"),
        lambda b: b["props"].update(active_view_id="missing-view"),
        lambda b: b["props"]["views"].append(deepcopy(b["props"]["views"][0])),
    ):
        bad = deepcopy(block)
        mutate(bad)
        with pytest.raises(linked.LinkedDatabaseShapeUnavailable):
            linked.source_of(bad)
    bad = deepcopy(block)
    bad["props"]["linked_database"]["document_id"] = child
    store.iam.execute_typed_command("owner", child, command_kind="block.insert", target_id="linked-wrong-owner",
        payload={"type": "table", "content": "", "props": bad["props"], "after_block_id": None},
        operation_id="linked-wrong-owner-insert", **request(store, child))
    before = dump(store)
    with pytest.raises(NotFoundOrDenied):
        read(store, child, "linked-wrong-owner")
    with pytest.raises(CopyShapeUnavailable, match="owned database"):
        copy(store, ws, operation="linked-wrong-owner-copy")
    monkeypatch.setattr(linked, "MAX_SOURCE_BYTES", 1)
    def forbidden(*args, **kwargs):
        raise AssertionError("budget failure must precede Node replay")
    monkeypatch.setattr(linked.synced_blocks.search_projection, "materialize", forbidden)
    with pytest.raises(linked.LinkedDatabaseShapeUnavailable, match="budget"):
        read(store, child)
    assert dump(store) == before


def test_ui_linked_table_display_settings_are_local_boolean_view_config(environment):
    store, ws = environment
    child, _title = setup_pair(store, ws)
    block = instance(store, child)
    keys = ("show_source_title", "show_vertical_lines", "show_page_icons", "wrap_all_content")
    for key in keys:
        local = deepcopy(block)
        local["props"]["views"][0]["config"][key] = False
        assert linked.source_of(local) == linked.source_of(block)
        invalid = deepcopy(local)
        invalid["props"]["views"][0]["config"][key] = "false"
        with pytest.raises(linked.LinkedDatabaseShapeUnavailable, match="display setting"):
            linked.source_of(invalid)
        leaked = deepcopy(local)
        leaked["props"][key] = False
        with pytest.raises(linked.LinkedDatabaseShapeUnavailable, match="caches"):
            linked.source_of(leaked)


def test_ui_linked_advanced_filter_group_accepts_three_layers_and_rejects_invalid_shape(environment):
    store, ws = environment
    child, title = setup_pair(store, ws)
    block = instance(store, child)
    group = {"logic": "and", "children": [
        {"property": title, "operator": "equals", "value": "Alpha"},
        {"logic": "or", "children": [
            {"property": title, "operator": "contains", "value": "Beta"},
            {"logic": "and", "children": [
                {"property": title, "operator": "not_empty", "value": ""},
            ]},
        ]},
    ]}
    local = deepcopy(block)
    local["props"]["views"][0]["config"]["filter_group"] = group
    assert linked.source_of(local) == linked.source_of(block)
    for bad_group in (
        {"logic": "or", "children": [{"logic": "and", "children": [
            {"logic": "or", "children": [{"logic": "and", "children": []}]},
        ]}]},
        {"logic": "and", "children": [{"property": title, "operator": "equals"}] * 17},
        {"logic": [], "children": []},
        {"logic": "and", "children": [{"property": "", "operator": "equals"}]},
    ):
        invalid = deepcopy(local)
        invalid["props"]["views"][0]["config"]["filter_group"] = bad_group
        with pytest.raises(linked.LinkedDatabaseShapeUnavailable):
            linked.source_of(invalid)


def test_ui_linked_group_order_accepts_saved_modes_and_rejects_invalid_shape(environment):
    store, ws = environment
    child, _title = setup_pair(store, ws)
    block = instance(store, child)
    local = deepcopy(block)
    local['props']['views'][0]['config'].update(
        group_sort='manual', group_order=['진행', '완료'])
    assert linked.source_of(local) == linked.source_of(block)
    for key, value in [('group_sort', 'random'), ('group_sort', []), ('group_order', ['같음', '같음']),
                       ('group_order', '완료')]:
        invalid = deepcopy(local)
        invalid['props']['views'][0]['config'][key] = value
        with pytest.raises(linked.LinkedDatabaseShapeUnavailable):
            linked.source_of(invalid)
