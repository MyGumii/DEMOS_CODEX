"""Bind an independent forest to its exact destination slot without flattening.

The flat editor cannot yet express every nested column, tab and presentation
scope. Keep the complete ordered context for the writer/renderer integration;
never guess a lossy flat body or treat this internal artifact as a write grant.
"""
from copy import deepcopy
from dataclasses import dataclass
import json

from . import synced_blocks as synced, synced_detach_remap as remap
from .canonical_authority import _canonical_json, _sha256
from .page_copy_source import MAX_SOURCE_BYTES
from .synced_detach_plan import PLACEMENT, source_body
from .synced_tree import content_hierarchy, content_tree

CONTRACT = 'code_assist.notion.synced_detach_placement.v1'


@dataclass(frozen=True)
class PreparedDetachedPlacement:
    content: remap.PreparedDetachedContent
    payload_json: str
    payload_sha256: str

    def value(self):
        return json.loads(self.payload_json)


def _layer(block, role):
    props = deepcopy(block.get('props') or {})
    props.pop('children', None)
    return {'role':role, 'block_id':block['id'], 'type':block['type'], 'props':props}


def bind_detached_placement(prepared):
    captured, detach = prepared.resources, prepared.resources.detach
    for raw, digest in ((prepared.payload_json,prepared.payload_sha256),
                        (captured.payload_json,captured.payload_sha256),
                        (detach.plan_json,detach.plan_sha256)):
        if _sha256(raw) != digest:
            raise ValueError('detach preparation was modified')
    value = prepared.value()
    destination = next((s for s in detach.sources if s.document_id == detach.document_id),None)
    original = next((s for s in detach.sources if s.document_id == detach.origin[0]),None)
    if destination is None or original is None:
        raise synced.SyncedShapeUnavailable('detach placement source is missing')
    body = source_body(destination, detach.block_id)[1]
    hierarchy = content_hierarchy(body)
    instance = hierarchy.indexed.get(detach.block_id)
    if instance is None or instance.get('type') != 'synced_block' or synced.origin_of(instance,detach.document_id) != detach.origin:
        raise synced.SyncedSourceChanged('detach placement endpoint changed')
    placement = {k:deepcopy(instance.get('props',{})[k]) for k in PLACEMENT if k in instance.get('props',{})}
    if placement != value['destination_placement']:
        raise synced.SyncedSourceChanged('detach placement metadata changed')
    ancestors = hierarchy.ancestors(detach.block_id)
    parent = hierarchy.parents[detach.block_id]
    siblings = hierarchy.children[parent] if parent is not None else [i for i in hierarchy.order if hierarchy.parents[i] is None]
    index = siblings.index(detach.block_id)
    original_tree = content_tree(source_body(original, detach.origin[1])[1], detach.origin[1])
    original_nodes = {}
    def original_index(node):
        original_nodes[node['id']] = node
        for child in node['children']: original_index(child)
    original_index(original_tree)
    # Empty-root promotion deliberately aliases the old wrapper and its first
    # child. The wrapper has its own context layer, never a second block node.
    reverse = {new:old for old,new in value['block_ids'].items()
               if not (value['unwrap_empty_root'] and old == detach.origin[1])}
    layers = {}
    outer = []
    def add_layer(key, block, role):
        layers[key] = _layer(block,role)
        return key
    for identity in ancestors:
        outer.append(add_layer('destination:'+identity,hierarchy.indexed[identity],'destination_ancestor'))
    outer.append(add_layer('destination:'+detach.block_id,instance,'destination_slot'))
    wrapper = None
    if value['unwrap_empty_root']:
        wrapper = 'source-wrapper'
        # Layout IDs on the vanished wrapper belong to its source page; its
        # placement is not a new column in the destination. Presentation and
        # custom metadata are retained separately and applied exactly once.
        wrapper_block = {**original_tree,'props':deepcopy(value['wrapper_props'])}
        add_layer(wrapper,wrapper_block,'removed_source_wrapper')
    nodes, seen = [], set()
    def visit(node, path, owners, source_parent):
        identity = node['id']
        if identity in seen or identity not in reverse:
            raise synced.SyncedShapeUnavailable('invalid placed block identity')
        if identity in hierarchy.indexed and identity != detach.block_id:
            raise synced.SyncedShapeUnavailable('placed block collides with destination')
        seen.add(identity)
        source = original_nodes.get(reverse[identity])
        if source is None:
            raise synced.SyncedShapeUnavailable('placed source block is missing')
        key = add_layer('replacement:'+identity,node,'replacement_block')
        nodes.append({'block_id':identity,'path':path,'owner_layers':owners,'own_layer':key,
                      'source_parent_placement':None if source_parent is None else {k:deepcopy(source_parent['props'][k]) for k in PLACEMENT if k in source_parent['props']},
                      'source_placement':{k:deepcopy(source['props'][k]) for k in PLACEMENT if k in source['props']}})
        for ordinal, child in enumerate(node['children']):
            visit(child,path+[ordinal],owners+[key],source)
    owners = outer+([wrapper] if wrapper else [])
    for ordinal,node in enumerate(value['blocks']):
        visit(node,[ordinal],owners,original_tree if wrapper else None)
    if seen != set(value['block_ids'].values()) or not value['blocks'] or value['blocks'][0]['id'] != detach.block_id:
        raise synced.SyncedShapeUnavailable('incomplete detached placement')
    # A flat reference can have following indented local children even though
    # its own props cannot contain children. They are destination content, not
    # part of the copied original. Preserve them outside source presentation.
    retained = [hierarchy.tree(identity) for identity in hierarchy.children[detach.block_id]]
    result = {'contract':CONTRACT,'commit_ready':False,
        'destination_document_id':detach.document_id,
        'destination_body_sha256':_sha256(_canonical_json(body)),
        'slot':{'block_id':detach.block_id,'parent_id':parent,'ancestor_ids':list(ancestors),
                'sibling_index':index,'before_id':siblings[index-1] if index else None,
                'after_id':siblings[index+1] if index+1 < len(siblings) else None},
        'layers':layers,'nodes':nodes,'replacement_blocks':deepcopy(value['blocks']),
        'source_placement':deepcopy(value['source_placement']),
        'destination_placement':placement,'removed_wrapper_layer':wrapper,
        'retained_destination_children':retained,'retained_owner_layers':outer,
        'required_integrations':['nested_placement_renderer','atomic_destination_writer']}
    encoded = _canonical_json(result)
    if len(encoded.encode('utf-8')) > MAX_SOURCE_BYTES:
        raise synced.SyncedShapeUnavailable('detach placement exceeds preparation budget')
    return PreparedDetachedPlacement(prepared,encoded,_sha256(encoded))


def revalidate_detached_placement(authority, con, prepared):
    if _sha256(prepared.payload_json) != prepared.payload_sha256:
        raise ValueError('detached placement was modified')
    remap.revalidate_detached_content(authority,con,prepared.content)


def prepare_detached_placement(authority, prepared):
    result = bind_detached_placement(prepared)
    with authority._reader() as con:
        con.execute('BEGIN')
        revalidate_detached_placement(authority,con,result)
    return result
