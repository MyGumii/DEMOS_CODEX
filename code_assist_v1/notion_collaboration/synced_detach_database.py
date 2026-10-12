"""Prepare one physical database-owned detach without main-page Yjs writes."""
from copy import deepcopy
from dataclasses import dataclass
import json

from . import database_row_content, synced_blocks as synced, synced_database_edit
from . import synced_detach_body as body, synced_detach_plan as plan
from .canonical_authority import _canonical_json, _sha256
from .synced_tree import content_hierarchy


@dataclass(frozen=True)
class PreparedDetachedDatabaseBody:
    body: body.PreparedDetachedBody
    body_edit: synced_database_edit.PreparedBodyEdit

    @property
    def update_frames(self):
        return ()


def _prepare(prepared):
    if body.materialize_detached_body(prepared.placement) != prepared:
        raise synced.SyncedShapeUnavailable('prepared database detach body changed')
    detach = prepared.placement.content.resources.detach
    source = next(s for s in detach.sources if s.document_id == detach.document_id)
    location, blocks, _instance = plan.source_body(source, detach.block_id)
    if location.kind not in {'row', 'template'} or detach.value().get('destination_location') != location.payload():
        raise synced.SyncedShapeUnavailable('exact database detach location required')
    descriptor = json.loads(source.descriptor_json)
    database = next(item for item in descriptor['databases'] if item['id'] == location.database_id)
    key, identity = ('rows', location.row_id) if location.kind == 'row' else ('templates', location.template_id)
    target = next(item for item in database[key] if item['id'] == identity)
    version = target['version']
    if type(version) is not int or version < 1:
        raise synced.SyncedShapeUnavailable('exact database detach body version required')
    hierarchy = content_hierarchy(prepared.value()['blocks'])
    if len(hierarchy.indexed) > database_row_content.MAX_BODY_BLOCKS:
        raise ValueError('database detach body exceeds block limit')

    original = content_hierarchy(blocks)
    retained = set()
    def retain(identity):
        retained.add(identity)
        for child in original.children[identity]: retain(child)
    for identity in original.children[detach.block_id]: retain(identity)

    def convert(tree):
        identity = tree['id']
        if identity in retained:
            old = original.indexed[identity]
            children = old.get('props', {}).get('children', [])
            if all(isinstance(child, dict) for child in children) and [child['id'] for child in children] == [child['id'] for child in tree['children']]:
                return deepcopy(old)
        result = {key:deepcopy(value) for key,value in tree.items() if key != 'children'}
        result.pop('position', None)
        if tree['children']:
            result['props']['children'] = [convert(child) for child in tree['children']]
        return result

    # Splice only the reference slot. Unrelated subtrees keep their exact
    # nested/flat representation, metadata and child positions.
    slot = prepared.value()['slot_container_id']
    replacement_slot = convert(hierarchy.tree(slot))
    found = 0
    def splice(items):
        nonlocal found
        result = []
        for item in items:
            if isinstance(item, str):
                if item not in retained: result.append(slot if item == detach.block_id else item)
                continue
            if item['id'] in retained: continue
            if item['id'] == detach.block_id:
                found += 1
                replacement = deepcopy(replacement_slot)
                if 'position' in item: replacement['position'] = item['position']
                result.append(replacement); continue
            item = deepcopy(item)
            if isinstance(item.get('props', {}).get('children'), list):
                item['props']['children'] = splice(item['props']['children'])
            result.append(item)
        return result
    replacement = splice(blocks)
    if found != 1 or set(synced.index_blocks(replacement)) != set(hierarchy.indexed):
        raise synced.SyncedShapeUnavailable('database detach lost its exact body slot')
    database_row_content.clean_blocks(replacement)
    encoded = _canonical_json(replacement)
    edit = synced_database_edit.PreparedBodyEdit(detach.document_id, detach.block_id, location, version,
                                                _canonical_json(blocks), encoded, _sha256(encoded))
    return PreparedDetachedDatabaseBody(prepared, edit)


def prepare_detached_database_body(authority, prepared):
    result = _prepare(prepared)
    with authority._reader() as con:
        con.execute('BEGIN')
        revalidate_detached_database_body(authority, con, result)
    return result


def revalidate_detached_database_body(authority, con, prepared):
    body.revalidate_detached_body(authority, con, prepared.body)
    if _prepare(prepared.body) != prepared:
        raise synced.SyncedShapeUnavailable('prepared database detach write changed')
