"""Current authorized synced endpoint inventory; never a cached group count."""
from __future__ import annotations

import json

from . import synced_blocks as synced, database_access
from .canonical_authority import NotFoundOrDenied

CONTRACT = 'code_assist.notion.synced_locations.v1'


def _visible_documents(authority, con, actor):
    # References can cross workspaces. Checking only the requesting workspace
    # would incorrectly advertise a complete count. Denied IDs stay server-side.
    result = {}
    for row in con.execute('SELECT id FROM workspace_collaboration_documents WHERE archived=0 ORDER BY id'):
        identity = str(row['id'])
        try:
            _, stamp = synced._authorize(authority, con, actor, identity)
        except NotFoundOrDenied:
            continue
        result[identity] = stamp
    return result


def _body_title(descriptor, location):
    if location.kind == 'page':
        return ''
    database = next(value for value in descriptor['databases'] if value['id'] == location.database_id)
    if location.kind == 'template':
        return str(next(value for value in database['templates'] if value['id'] == location.template_id)['name'])
    row = next(value for value in database['rows'] if value['id'] == location.row_id)
    prop = next((value for value in database['properties'] if value['type'] == 'title'), None)
    value = row['cells'].get(prop['id']) if prop else None
    return value if isinstance(value, str) else ''


def read_synced_locations(authority, actor_subject_id, document_id, block_id, *, workspace_id=None):
    actor = authority._require_active(actor_subject_id)
    document_id = synced._exact_id(document_id, 'document_id')
    block_id = synced._exact_id(block_id, 'block_id')
    workspace_id = synced._exact_id(workspace_id, 'workspace_id') if workspace_id is not None else None
    # Reauthorize the requesting endpoint and its real original before scanning
    # other documents. Wrong scope must not even start a body replay.
    sources, _, _, origin_document, origin_block = synced._resolve(
        authority, actor, document_id, block_id, workspace_id)
    origin = {'document_id': origin_document, 'block_id': origin_block}
    instance = {'document_id': document_id, 'block_id': block_id}
    with authority._reader() as con:
        con.execute('BEGIN')
        visible = _visible_documents(authority, con, actor)
        for source in sources:
            if visible.get(source.document_id) != source.stamp:
                raise synced.SyncedSourceChanged('synced locations changed during preparation')
    captured = {}
    remaining = synced.MAX_SOURCE_BYTES
    # Use one aggregate byte budget, not a document-count cutoff with an
    # apparently complete result. A budget failure returns no partial inventory.
    for identity, stamp in visible.items():
        value = synced._current(authority, actor, identity, remaining, include_title=True)
        if value.stamp != stamp:
            raise synced.SyncedSourceChanged('synced locations changed during preparation')
        remaining -= value.size
        captured[identity] = value
    entries = []
    with authority._reader() as con:
        con.execute('BEGIN')
        if _visible_documents(authority, con, actor) != visible:
            raise synced.SyncedSourceChanged('synced locations changed during preparation')
        for identity, source in captured.items():
            descriptor = json.loads(source.descriptor_json)
            for location, blocks in synced.body_entries(descriptor):
                if location.kind != 'page' and database_access.installed(con):
                    try:
                        database_access.resolve_on_connection(authority, con, actor, source.stamp[0], location.database_id)
                    except NotFoundOrDenied:
                        continue
                for candidate in synced.index_blocks(blocks).values():
                    if candidate.get('type') != 'synced_block':
                        continue
                    raw = candidate.get('props', {}).get('synced_from', 'legacy')
                    is_original = (identity, candidate['id']) == (origin_document, origin_block)
                    if not is_original and raw != origin:
                        continue
                    if synced.origin_of(candidate, identity) != (origin_document, origin_block):
                        raise synced.SyncedShapeUnavailable('synced location is not an explicit original or reference')
                    # A public document/block pair must be unambiguous across
                    # all visible bodies, including unrelated plain blocks.
                    actual = synced.locate_block(descriptor, candidate['id'])[0]
                    if actual != location:
                        raise synced.SyncedShapeUnavailable('ambiguous synced location')
                    entries.append({'endpoint': {'document_id': identity, 'block_id': candidate['id']},
                        'workspace_id': source.stamp[0], 'location': location.payload(),
                        'page_title': str(descriptor.get('title') or '제목 없음'),
                        'body_title': _body_title(descriptor, location), 'original': is_original})
    if not any(value['endpoint'] == origin and value['original'] for value in entries) or not any(value['endpoint'] == instance for value in entries):
        raise NotFoundOrDenied('not found or permission denied')
    entries.sort(key=lambda value: (not value['original'], value['endpoint']['document_id'], value['endpoint']['block_id']))
    return {'contract': CONTRACT, 'instance': instance, 'origin': origin,
        'entries': entries, 'other_count': sum(value['endpoint'] != instance for value in entries),
        'complete': True}
