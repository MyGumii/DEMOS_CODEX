"""Materialize detached scopes into the editor's ordinary flat block graph.

This is preparation only. The atomic writer must still validate resources and
commit canonical blocks and a matching CRDT update.
"""
from copy import deepcopy
from dataclasses import dataclass
import json

from . import synced_blocks as synced, synced_detach_placement as placement
from .canonical_authority import _canonical_json, _clean_blocks, _sha256, _stable_id
from .page_copy_source import MAX_SOURCE_BYTES
from .synced_detach_plan import PLACEMENT, source_body
from .synced_tree import content_hierarchy, number

CONTRACT = 'code_assist.notion.synced_detach_body.v1'


@dataclass(frozen=True)
class PreparedDetachedBody:
    placement: placement.PreparedDetachedPlacement
    payload_json: str
    payload_sha256: str

    def value(self):
        return json.loads(self.payload_json)


def materialize_detached_body(prepared):
    if _sha256(prepared.payload_json) != prepared.payload_sha256:
        raise ValueError('detached placement was modified')
    # Check the entire immutable preparation chain, including the slot and
    # source identities, rather than trusting an independently rehashed layer.
    if placement.bind_detached_placement(prepared.content) != prepared:
        raise ValueError('detached placement does not match its content')
    value = prepared.value()
    detached = prepared.content.resources.detach
    destination = next(s for s in detached.sources if s.document_id == detached.document_id)
    body = source_body(destination, detached.block_id)[1]
    hierarchy = content_hierarchy(body)
    target = detached.block_id
    occupied = set(hierarchy.indexed) | {n['block_id'] for n in value['nodes']}

    def scope_id(role):
        identity = _stable_id('block', CONTRACT, detached.actor, detached.workspace_id,
                              detached.document_id, target, detached.operation_id, role)
        if identity in occupied:
            raise synced.SyncedShapeUnavailable('detached scope identity collision')
        occupied.add(identity)
        return identity

    slot_id, source_id = scope_id('destination-slot'), scope_id('source-presentation')
    slot_props = deepcopy(value['layers']['destination:'+target]['props'])
    for key in ('synced_from', 'sync_id', 'children', 'rich_text_html'):
        slot_props.pop(key, None)
    slot_props['layout_container'] = True
    slot_indent = number(slot_props.get('indent'))
    slot_props['indent'] = slot_indent
    initial = prepared.content.value()
    roots = deepcopy(value['replacement_blocks'])
    original_props = (deepcopy(initial['wrapper_props']) if initial['unwrap_empty_root']
                      else deepcopy(roots[0]['props']))
    source_props = {k:v for k,v in original_props.items()
                    if k not in {*PLACEMENT, 'children', 'rich_text_html', 'synced_from', 'sync_id'}}
    source_props.update(layout_container=True, indent=slot_indent)
    if 'visual_indent' in original_props:
        source_props['visual_indent'] = original_props['visual_indent']
    original_placement = value['source_placement']
    root_indent = number(original_placement.get('indent'))
    nodes = {item['block_id']:item for item in value['nodes']}

    def adapt(node, parent_indent, old_parent_indent, *, top=False):
        props = node['props']
        old = nodes[node['id']]['source_placement']
        old_indent = number(old.get('indent'))
        # The synced wrapper is not an extra visible nesting level. Its direct
        # children become siblings inside a transparent presentation scope.
        delta = max(0, old_indent-old_parent_indent-(1 if top else 0))
        props['indent'] = parent_indent + delta
        if props['indent'] > 32:
            raise synced.SyncedShapeUnavailable('detached indentation exceeds editor limits')
        if (old.get('layout_group') and old.get('layout_group') == original_placement.get('layout_group')
                and number(old.get('layout_column'), 4) == number(original_placement.get('layout_column'), 4)):
            for key in ('layout_group', 'layout_column', 'layout_columns'):
                props.pop(key, None)
        if (old.get('tab_group') and old.get('tab_group') == original_placement.get('tab_group')
                and old.get('tab_id') == original_placement.get('tab_id')):
            props.pop('tab_group', None); props.pop('tab_id', None)
        for child in node['children']:
            adapt(child, props['indent'], old_indent)
        return node

    if initial['unwrap_empty_root']:
        copied = [adapt(root, slot_indent, root_indent, top=True) for root in roots]
    else:
        # Nonempty legacy originals carry a text row on the synced wrapper.
        # Preserve it as an ordinary row; presentation surrounds that row and
        # every original child once, without painting each child separately.
        root = roots[0]
        header_props = {'indent':slot_indent}
        if 'rich_text_html' in root['props']:
            header_props['rich_text_html'] = root['props']['rich_text_html']
        header = {'id':root['id'], 'type':root['type'], 'content':root['content'],
                  'props':header_props, 'children':[]}
        copied = [header, *[adapt(child, slot_indent, root_indent, top=True) for child in root['children']]]
    source_scope = {'id':source_id, 'type':'paragraph', 'content':'', 'props':source_props, 'children':copied}

    retained = deepcopy(value['retained_destination_children'])
    # These local children belong to the destination instance, not its source.
    # Retain their original content, styles, placement and entire hierarchy.
    slot = {'id':slot_id, 'type':'paragraph', 'content':'', 'props':slot_props,
            'children':[source_scope, *retained]}
    replaced = 0

    def replace(node):
        nonlocal replaced
        if node['id'] == target:
            replaced += 1
            return deepcopy(slot)
        node['children'] = [replace(child) for child in node['children']]
        return node

    forest = [replace(hierarchy.tree(identity)) for identity in hierarchy.order
              if hierarchy.parents[identity] is None]
    if replaced != 1:
        raise synced.SyncedShapeUnavailable('detached destination slot is not unique')
    flat, owners, seen = [], {}, set()

    def flatten(node, parent=None, depth=0):
        identity = node['id']
        if depth > 64 or identity in seen:
            raise synced.SyncedShapeUnavailable('cyclic or deep detached body')
        seen.add(identity); owners[identity] = parent
        props = deepcopy(node['props'])
        children = node['children']
        if children or 'children' in hierarchy.indexed.get(identity, {}).get('props', {}):
            props['children'] = [child['id'] for child in children]
        else:
            props.pop('children', None)
        flat.append({'id':identity, 'type':node['type'], 'content':node['content'], 'props':props})
        for child in children:
            flatten(child, identity, depth+1)

    for root in forest:
        flatten(root)
    expected = set(hierarchy.indexed) | set(nodes) | {slot_id, source_id}
    if seen != expected:
        raise synced.SyncedShapeUnavailable('detached body lost or duplicated a block')
    flat = _clean_blocks(flat)
    result_hierarchy = content_hierarchy(flat)
    if result_hierarchy.parents != owners:
        raise synced.SyncedShapeUnavailable('detached placement changed block ownership')
    required = ['atomic_destination_writer']
    result = {'contract':CONTRACT, 'commit_ready':False, 'destination_document_id':detached.document_id,
              'destination_body_sha256':value['destination_body_sha256'], 'blocks':flat,
              'slot_container_id':slot_id, 'source_container_id':source_id,
              'anchor_block_id':target, 'added_block_ids':sorted(seen-set(hierarchy.indexed)),
              'retained_block_ids':sorted(set(hierarchy.indexed)-{target}),
              'required_integrations':required}
    encoded = _canonical_json(result)
    if len(encoded.encode('utf-8')) > MAX_SOURCE_BYTES:
        raise synced.SyncedShapeUnavailable('detached body exceeds preparation budget')
    return PreparedDetachedBody(prepared, encoded, _sha256(encoded))


def revalidate_detached_body(authority, con, prepared):
    if _sha256(prepared.payload_json) != prepared.payload_sha256:
        raise ValueError('detached body was modified')
    placement.revalidate_detached_placement(authority, con, prepared.placement)


def prepare_detached_body(authority, prepared):
    result = materialize_detached_body(prepared)
    with authority._reader() as con:
        con.execute('BEGIN')
        revalidate_detached_body(authority, con, result)
    return result
