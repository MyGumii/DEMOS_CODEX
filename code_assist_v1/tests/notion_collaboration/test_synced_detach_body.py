"""UI-SYNC-DETACH-BODY: preserve content and ownership before atomic writes."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from code_assist_v1.notion_collaboration import synced_detach_body as body
from code_assist_v1.notion_collaboration import synced_detach_placement as placement
from code_assist_v1.notion_collaboration import synced_detach_remap as remap
from code_assist_v1.notion_collaboration import synced_detach_resources as resources
from code_assist_v1.notion_collaboration import synced_blocks as synced
from code_assist_v1.notion_collaboration.canonical_authority import _canonical_json, _sha256
from code_assist_v1.notion_collaboration.synced_tree import content_hierarchy
from code_assist_v1.tests.notion_collaboration.test_synced_detach_placement import block, fixture
from code_assist_v1.tests.notion_collaboration.test_synced_detach_resources import (
    environment, member_environment, native_environment, archive_environment,
    guest_environment, image_environment, setup_resources, prepared, dump, text_update,
)


def materialize(content):
    return body.materialize_detached_body(placement.bind_detached_placement(content))


def editor_outline(value):
    """Run the shipped editor hierarchy, not a Python copy of its behavior."""
    node = shutil.which('node')
    assert node, 'The editor integration check requires Node.js'
    core = Path(__file__).resolve().parents[2] / 'notion_static/notion_editor_core.js'
    script = """
const fs = require('node:fs'), core = require(process.argv[1]);
const blocks = JSON.parse(fs.readFileSync(0, 'utf8'));
const outline = core.blockOutline(blocks);
console.log(JSON.stringify(Object.fromEntries(blocks.map((b, i) => [b.id, {
  parent: blocks[outline[i].parentIndex]?.id || null,
  children: outline[i].childIndices.map(j => blocks[j].id),
  visualIndent: outline[i].visualIndent,
  collapsed: outline[i].collapsed,
  hiddenByTab: outline[i].hiddenByTab,
  transparent: core.isLayoutContainer(b),
  selection: core.expandBlockSelection(blocks, [i]).map(j => blocks[j].id),
}]))));
"""
    run = subprocess.run([node, '-e', script, str(core)],
                         input=json.dumps(value['blocks']), capture_output=True,
                         text=True, encoding='utf-8', timeout=15, check=True)
    return json.loads(run.stdout)


@pytest.mark.parametrize('empty', [False, True])
def test_ui_detach_body_preserves_text_styles_anchors_and_editor_depth(empty):
    content = fixture(empty=empty)
    before = deepcopy(content)
    result = materialize(content)
    assert result == materialize(content) and content == before
    value = result.value()
    assert _sha256(result.payload_json) == result.payload_sha256
    assert value['commit_ready'] is False
    assert value['required_integrations'] == ['atomic_destination_writer']
    assert value['anchor_block_id'] == 'reference'
    h = content_hierarchy(value['blocks'])
    slot, scope = value['slot_container_id'], value['source_container_id']
    ids = content.value()['block_ids']
    assert h.parents[slot] == 'before' and h.parents[scope] == slot
    assert h.parents[ids['callout']] == scope and h.parents[ids['tail']] == scope
    assert h.parents[ids['text']] == ids['callout']
    assert h.indexed[ids['text']]['content'] == '한😀글'
    assert h.indexed[ids['tail']]['content'] == '마지막'
    assert h.indexed[ids['callout']]['props']['background_color'] == 'blue'
    assert h.indexed[scope]['props']['background_color'] == 'yellow'
    assert h.indexed[scope]['props']['text_color'] == 'red'
    assert sum(b['props'].get('background_color') == 'yellow' for b in value['blocks']) == 1
    assert all(b['type'] != 'synced_block' for b in value['blocks'])
    if not empty:
        assert h.indexed['reference']['content'] == '원본'
        assert h.children[scope][0] == 'reference'
    assert [b['position'] for b in value['blocks']] == list(range(len(value['blocks'])))
    outline = editor_outline(value)
    assert {k: v['parent'] for k, v in outline.items()} == h.parents
    assert outline[slot]['transparent'] and outline[scope]['transparent']
    assert outline[ids['callout']]['visualIndent'] == 5
    assert outline[ids['text']]['visualIndent'] == 7
    assert set(outline[slot]['selection']) == set(h.indexed) - {'before', 'after'}


@pytest.mark.parametrize('representation', ['inline', 'ids'])
def test_ui_detach_body_preserves_explicit_sibling_order_and_unrelated_metadata(representation):
    def destination(reference):
        reference['props']['indent'] = 0
        parent = block('outer', 'callout', '바깥', text_color='green', custom={'keep': [1, 2]})
        before, after = block('before', content='앞'), block('after', content='뒤')
        parent['props']['children'] = [after, reference, before] if representation == 'inline' else ['after', 'reference', 'before']
        return [parent] if representation == 'inline' else [parent, before, reference, after]
    value = materialize(fixture(destination=destination)).value()
    h = content_hierarchy(value['blocks'])
    assert h.children['outer'] == ['after', value['slot_container_id'], 'before']
    assert h.indexed['outer']['props']['custom'] == {'keep': [1, 2]}
    assert h.indexed['before']['content'] == '앞' and h.indexed['after']['content'] == '뒤'
    assert editor_outline(value)['outer']['children'] == list(h.children['outer'])


@pytest.mark.parametrize('empty', [False, True])
def test_ui_detach_body_keeps_inner_columns_tabs_and_destination_visibility(empty):
    def source(root):
        root['props'].update(layout_group='old-outer', layout_column=1, layout_columns=2,
                             tab_group='old-tabs', tab_id='old-tab')
        root['props']['children'] = [block('tabs', 'tabs', tabs=[{'id': 'a', 'name': 'A'}, {'id': 'b', 'name': 'B'}], active_tab_id='b', children=[
            block('fold', 'toggle', '닫힘', tab_group='tabs', tab_id='b', open=False, children=[
                block('left', content='왼쪽', layout_group='inner', layout_column=0, layout_columns=2),
                block('right', content='오른쪽', layout_group='inner', layout_column=1, layout_columns=2)])])]
    def destination(reference):
        reference['props'].update(indent=0, tab_group='outer-tabs', tab_id='outer-b',
                                   layout_group='outer-columns', layout_column=1, layout_columns=3)
        return [block('outer-tabs', 'tabs', tabs=[{'id': 'outer-a', 'name': 'A'}, {'id': 'outer-b', 'name': 'B'}], active_tab_id='outer-a'), reference]
    content = fixture(empty=empty, source=source, destination=destination)
    value = materialize(content).value(); ids = content.value()['block_ids']
    h = content_hierarchy(value['blocks']); outline = editor_outline(value)
    slot, scope = value['slot_container_id'], value['source_container_id']
    assert h.indexed[slot]['props']['layout_group'] == 'outer-columns'
    assert h.indexed[slot]['props']['tab_group'] == 'outer-tabs'
    assert 'layout_group' not in h.indexed[scope]['props']
    assert 'tab_group' not in h.indexed[scope]['props']
    left, right = h.indexed[ids['left']], h.indexed[ids['right']]
    assert left['props']['layout_group'] == right['props']['layout_group'] != 'inner'
    assert [left['props']['layout_column'], right['props']['layout_column']] == [0, 1]
    assert h.indexed[ids['fold']]['props']['tab_group'] == ids['tabs']
    assert h.indexed[ids['fold']]['props']['tab_id'] == h.indexed[ids['tabs']]['props']['active_tab_id']
    assert outline[ids['left']]['hiddenByTab'] and outline[ids['left']]['collapsed']
    assert outline[ids['right']]['hiddenByTab'] and outline[ids['right']]['collapsed']


def test_ui_detach_body_local_children_do_not_inherit_source_colors():
    def destination(reference):
        return [block('before'), reference, block('local', 'callout', '별도 글', indent=3,
                background_color='green', children=[block('local-inline', content='그대로', custom={'v': 1})]), block('after')]
    value = materialize(fixture(empty=True, destination=destination)).value()
    h = content_hierarchy(value['blocks']); scope = value['source_container_id']
    assert h.children[value['slot_container_id']] == [scope, 'local']
    assert scope not in h.ancestors('local')
    assert h.indexed['local']['content'] == '별도 글'
    assert h.indexed['local']['props']['background_color'] == 'green'
    assert h.indexed['local-inline']['content'] == '그대로'
    assert h.indexed['local-inline']['props']['custom'] == {'v': 1}
    assert editor_outline(value)['local']['visualIndent'] == 4


@pytest.mark.parametrize('empty', [False, True])
def test_ui_detach_body_same_page_leaves_original_tree_unchanged(empty):
    content = fixture(empty=empty, same_page=True)
    original = content_hierarchy(json.loads(content.resources.detach.sources[0].descriptor_json)['blocks']).tree('original')
    h = content_hierarchy(materialize(content).value()['blocks'])
    # Flattening assigns global positions; the semantic source tree stays exact.
    def semantic(node):
        node.pop('position', None)
        for child in node['children']:
            semantic(child)
        return node
    assert semantic(h.tree('original')) == original


def test_ui_detach_body_preserves_rich_text_without_copying_source_outer_membership():
    def source(root):
        root['props'].update(rich_text_html='<strong>원본</strong>', custom={'source': True},
                             layout_group='source-column', layout_column=1, layout_columns=2)
        root['props']['children'][1]['props'].update(layout_group='source-column', layout_column=1, layout_columns=2)
    content = fixture(source=source); value = materialize(content).value()
    h = content_hierarchy(value['blocks']); ids = content.value()['block_ids']
    assert h.indexed['reference']['props']['rich_text_html'] == '<strong>원본</strong>'
    assert h.indexed[value['source_container_id']]['props']['custom'] == {'source': True}
    assert 'layout_group' not in h.indexed[ids['tail']]['props']


def test_ui_detach_body_rejects_modified_or_rehashed_placement():
    placed = placement.bind_detached_placement(fixture())
    with pytest.raises(ValueError, match='modified'):
        body.materialize_detached_body(replace(placed, payload_json='{}'))
    changed = placed.value(); changed['slot']['parent_id'] = 'wrong'
    encoded = _canonical_json(changed)
    with pytest.raises(ValueError, match='does not match'):
        body.materialize_detached_body(replace(placed, payload_json=encoded, payload_sha256=_sha256(encoded)))


def test_ui_detach_body_rejects_container_collision(monkeypatch):
    placed = placement.bind_detached_placement(fixture())
    monkeypatch.setattr(body, '_stable_id', lambda *args: 'before')
    with pytest.raises(synced.SyncedShapeUnavailable, match='collision'):
        body.materialize_detached_body(placed)


def test_ui_detach_body_enforces_size_budget_without_mutating_preparation(monkeypatch):
    placed = placement.bind_detached_placement(fixture()); before = deepcopy(placed)
    monkeypatch.setattr(body, 'MAX_SOURCE_BYTES', 10)
    with pytest.raises(synced.SyncedShapeUnavailable, match='budget'):
        body.materialize_detached_body(placed)
    assert placed == before


def test_ui_detach_body_refuses_indentation_overflow_without_clipping_content():
    def destination(reference):
        reference['props']['indent'] = 32
        return [reference]
    content = fixture(destination=destination); before = deepcopy(content)
    with pytest.raises(synced.SyncedShapeUnavailable, match='indentation'):
        materialize(content)
    assert content == before


def test_ui_detach_body_empty_original_still_retains_a_focusable_anchor():
    def source(root):
        root['props']['children'] = []
    value = materialize(fixture(empty=True, source=source)).value()
    h = content_hierarchy(value['blocks'])
    assert h.indexed['reference']['content'] == ''
    assert h.indexed['reference']['type'] == 'paragraph'
    assert h.children[value['source_container_id']] == ['reference']
    assert editor_outline(value)['reference']['transparent'] is False


def test_ui_detach_body_real_capture_remains_read_only_and_rechecks_source(environment):
    store, ws = environment; child, _, _ = setup_resources(store, ws)
    captured = resources.prepare_synced_resources(store.iam, prepared(store, ws, child))
    content = remap.prepare_detached_content(store.iam, captured)
    placed = placement.prepare_detached_placement(store.iam, content)
    before = dump(store); result = body.prepare_detached_body(store.iam, placed)
    assert dump(store) == before
    assert result.value()['anchor_block_id'] == 'synced-instance'
    assert result.placement.content.value()['databases'] == content.value()['databases']
    assert result.placement.content.value()['files'] == content.value()['files']
    with store.iam._reader() as con:
        con.execute('BEGIN'); body.revalidate_detached_body(store.iam, con, result)
        with pytest.raises(ValueError, match='modified'):
            body.revalidate_detached_body(store.iam, con, replace(result, payload_json='{}'))
    text_update(store, ws, 'page-e1', 'synced-original', '조립 뒤 원본 변경')
    before = dump(store)
    with store.iam._reader() as con:
        con.execute('BEGIN')
        with pytest.raises(synced.SyncedSourceChanged):
            body.revalidate_detached_body(store.iam, con, result)
    assert dump(store) == before
