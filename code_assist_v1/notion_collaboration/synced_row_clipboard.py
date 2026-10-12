"""Insert one empty synced reference in an exact page-authorized row body.

Clipboard data identifies a real original; it supplies neither content nor
authority. Capture/replay runs outside the writer, and every captured authority
and version is checked again in the atomic body/history/audit/outbox commit.
The frozen whole-body command and database-only grants are unchanged.
"""
from copy import deepcopy
from dataclasses import dataclass
import json
import time

from . import database_access, database_row_content, page_move
from . import synced_blocks as synced, synced_database_edit
from .canonical_authority import (
    AuthorityUnavailable, NotFoundOrDenied, _canonical_json, _operation_id,
    _request_sha256, _sha256, _stable_id,
)
from .page_copy_source import MAX_SOURCE_BYTES
from .synced_tree import content_hierarchy, content_tree

CONTRACT = 'code_assist.notion.synced_row_clipboard.v1'
ACTION = 'collaboration.synced_row_clipboard'
PLACEMENT = ('indent', 'visual_indent', 'layout_group', 'layout_column',
             'layout_columns', 'tab_group', 'tab_id')


def _positive(value, field):
    if type(value) is not int or value < 1:
        raise ValueError('positive ' + field + ' required')
    return value


def _request(actor, workspace, document, database, row, *, source, anchor_id,
             replace_empty, expected_target_version, expected_workspace_epoch,
             expected_document_epoch, expected_database_epoch, operation_id):
    for value, field in ((actor, 'actor_subject_id'), (workspace, 'workspace_id'),
                         (document, 'document_id'), (database, 'database_id'), (row, 'row_id')):
        synced._exact_id(value, field)
    _operation_id(operation_id)
    if not isinstance(source, dict) or set(source) != {'workspace_id', 'document_id', 'block_id'}:
        raise ValueError('exact clipboard original required')
    for field, value in source.items():
        synced._exact_id(value, field)
    if anchor_id is not None:
        synced._exact_id(anchor_id, 'block_id')
    if type(replace_empty) is not bool or replace_empty and anchor_id is None:
        raise ValueError('exact clipboard placement required')
    for field, value in (('target_version', expected_target_version),
                         ('workspace_epoch', expected_workspace_epoch),
                         ('document_epoch', expected_document_epoch)):
        _positive(value, field)
    if expected_database_epoch is not None:
        _positive(expected_database_epoch, 'database_epoch')
    return _request_sha256(actor, ACTION, {
        'workspace_id': workspace, 'document_id': document, 'database_id': database, 'row_id': row,
        'source': source, 'anchor_id': anchor_id, 'replace_empty': replace_empty,
        'expected_target_version': expected_target_version,
        'expected_workspace_epoch': expected_workspace_epoch,
        'expected_document_epoch': expected_document_epoch,
        'expected_database_epoch': expected_database_epoch,
    })


def _row(source, database, row):
    descriptor = json.loads(source.descriptor_json)
    matches = [(location, blocks) for location, blocks in synced.body_entries(descriptor)
               if location.kind == 'row' and location.database_id == database and location.row_id == row]
    if len(matches) != 1:
        raise NotFoundOrDenied('not found or permission denied')
    value = next(item for db in descriptor['databases'] if db['id'] == database
                 for item in db['rows'] if item['id'] == row)
    return matches[0][0], matches[0][1], value['version']


def _read_location(authority, con, actor, workspace, document, location):
    page_move._authorize(authority, con, actor, workspace, document, 'view')
    if location.kind == 'page' or not database_access.installed(con):
        return None
    access = database_access.resolve_on_connection(authority, con, actor, workspace, location.database_id)
    if access.document_id != document or database_access.PAGE_ROLE_LEVEL[access.role] < database_access.PAGE_ROLE_LEVEL['view']:
        raise NotFoundOrDenied('not found or permission denied')
    return access.database_epoch


def _target_access(authority, con, actor, workspace, document, location, request=None):
    epochs = synced.authorize_edit_locations(authority, con, actor, workspace, document, [location])
    page = authority._document(con, document)
    if request is not None:
        ws = authority._workspace(con, workspace)
        if (ws['security_epoch'] != request['expected_workspace_epoch']
                or page['access_epoch'] != request['expected_document_epoch']
                or epochs.get(location.database_id) != request['expected_database_epoch']):
            raise synced.SyncedSourceChanged('clipboard destination authority changed')
    return page, epochs.get(location.database_id)


def _replay(authority, con, actor, workspace, document, database, row, operation, digest):
    # A committed insertion can be recovered even if its original later changes
    # or is revoked. Its receipt never contains source content.
    location = synced.BodyLocation('row', database, row)
    def authorize():
        from . import database_row_trash, database_row_page_placement
        authority._database_for_document(con, document, database)
        _read_location(authority, con, actor, workspace, document, location)
        database_row_trash.require_active(con, database, row)
        if row in database_row_page_placement.page_row_ids(con, database):
            raise NotFoundOrDenied('not found or permission denied')
        if not con.execute('SELECT 1 FROM workspace_collaboration_database_rows WHERE id=? AND database_id=?', (row, database)).fetchone():
            raise NotFoundOrDenied('not found or permission denied')
    result = authority._replay(con, actor=actor, action=ACTION, operation_id=operation,
                              request_sha256=digest, authorize=authorize)
    if result is None:
        return None
    if (result.get('contract') != CONTRACT or result.get('workspace_id') != workspace
            or result.get('document_id') != document or result.get('location') != location.payload()
            or result.get('operation_id') != operation):
        raise AuthorityUnavailable('stored row clipboard result scope is invalid')
    return {**result, 'replayed': True}


def _splice(blocks, anchor, replace, reference):
    result = deepcopy(blocks)
    if anchor is None:
        if result:
            raise synced.SyncedSourceChanged('clipboard destination anchor is required')
        return [{**reference, 'position': 0}]
    hierarchy = content_hierarchy(blocks)
    if anchor in hierarchy.indexed and any(hierarchy.indexed[parent]['type'] == 'synced_block'
                                          for parent in hierarchy.ancestors(anchor)):
        raise synced.SyncedShapeUnavailable('clipboard cannot insert inside a synced block')
    found = 0
    def visit(items, inside_reference=False):
        nonlocal found
        for index, item in enumerate(list(items)):
            if not isinstance(item, dict):
                continue
            if item['id'] == anchor:
                if inside_reference:
                    raise synced.SyncedShapeUnavailable('clipboard cannot insert inside a synced reference')
                found += 1
                if replace and (item['type'] != 'paragraph' or item.get('content')
                                or item.get('props', {}).get('children')
                                or item.get('props', {}).get('rich_text_html')):
                    raise synced.SyncedSourceChanged('clipboard empty destination changed')
                reference['props'].update({key: deepcopy(item.get('props', {})[key])
                                          for key in PLACEMENT if key in item.get('props', {})})
                items[index + (0 if replace else 1):index + 1] = [reference]
                if any('position' in child for child in items):
                    for position, child in enumerate(items):
                        child['position'] = position
                continue
            children = item.get('props', {}).get('children', [])
            if children and all(isinstance(child, dict) for child in children):
                visit(children, inside_reference or item['type'] == 'synced_block')
    visit(result)
    if found != 1:
        raise synced.SyncedSourceChanged('clipboard destination anchor changed')
    return result


@dataclass(frozen=True)
class PreparedRowPaste:
    sources: tuple
    origin_location: synced.BodyLocation
    origin_epoch: int | None
    target_epoch: int | None
    edit: synced_database_edit.PreparedBodyEdit
    reference_id: str


def prepare(authority, actor, workspace, document, database, row, request):
    target = synced._current(authority, actor, document, MAX_SOURCE_BYTES, workspace, strict_structure=True)
    source = target if request['source']['document_id'] == document else synced._current(
        authority, actor, request['source']['document_id'], MAX_SOURCE_BYTES - target.size,
        request['source']['workspace_id'], strict_structure=True)
    if source.stamp[0] != request['source']['workspace_id']:
        raise NotFoundOrDenied('not found or permission denied')
    origin = (request['source']['document_id'], request['source']['block_id'])
    origin_location, origin_body, original = synced.locate_block(json.loads(source.descriptor_json), origin[1])
    if synced.origin_of(original, origin[0]) != origin:
        raise synced.SyncedShapeUnavailable('clipboard must identify a real original')
    content_tree(origin_body, origin[1])
    location, blocks, version = _row(target, database, row)
    if version != request['expected_target_version']:
        raise synced.SyncedSourceChanged('clipboard destination row changed')
    reference_id = _stable_id('block', CONTRACT, actor, workspace, document, database, row, request['operation_id'])
    if any(reference_id in synced.index_blocks(body) for _location, body in synced.body_entries(json.loads(target.descriptor_json))):
        raise synced.SyncedShapeUnavailable('clipboard reference identity collision')
    reference = {'id': reference_id, 'type': 'synced_block', 'content': '', 'props': {
        'synced_from': {'document_id': origin[0], 'block_id': origin[1]},
        'sync_id': synced.group_id(*origin),
    }}
    result = _splice(blocks, request['anchor_id'], request['replace_empty'], reference)
    database_row_content.clean_blocks(result)
    if len(synced.index_blocks(result)) > database_row_content.MAX_BODY_BLOCKS:
        raise ValueError('clipboard row exceeds block limit')
    encoded = _canonical_json(result)
    if len(encoded.encode('utf-8')) > database_row_content.MAX_BODY_BYTES:
        raise ValueError('clipboard row exceeds body limit')
    edit = synced_database_edit.PreparedBodyEdit(document, reference_id, location, version,
        _canonical_json(blocks), encoded, _sha256(encoded))
    with authority._reader() as con:
        con.execute('BEGIN')
        _page, target_epoch = _target_access(authority, con, actor, workspace, document, location, request)
        origin_epoch = _read_location(authority, con, actor, source.stamp[0], source.document_id, origin_location)
    return PreparedRowPaste(tuple([target] if source is target else [target, source]),
                           origin_location, origin_epoch, target_epoch, edit, reference_id)


def insert_row_reference(authority, actor_subject_id, workspace_id, document_id, database_id, row_id, *, fault=None, **request):
    actor = authority._require_active(actor_subject_id)
    digest = _request(actor, workspace_id, document_id, database_id, row_id, **request)
    operation = request['operation_id']
    with authority._reader() as con:
        con.execute('BEGIN')
        prior = _replay(authority, con, actor, workspace_id, document_id, database_id, row_id, operation, digest)
        if prior is not None:
            return prior
    pending = prepare(authority, actor, workspace_id, document_id, database_id, row_id, request)
    with authority._transaction() as con:
        prior = _replay(authority, con, actor, workspace_id, document_id, database_id, row_id, operation, digest)
        if prior is not None:
            return prior
        for source in pending.sources:
            _page, stamp = synced._authorize(authority, con, actor, source.document_id)
            if stamp != source.stamp:
                raise synced.SyncedSourceChanged('clipboard source or destination changed during preparation')
        page, epoch = _target_access(authority, con, actor, workspace_id, document_id, pending.edit.location, request)
        origin_epoch = _read_location(authority, con, actor, request['source']['workspace_id'],
                                     request['source']['document_id'], pending.origin_location)
        if epoch != pending.target_epoch or origin_epoch != pending.origin_epoch:
            raise synced.SyncedSourceChanged('clipboard database authority changed during preparation')
        # Recompute only the deterministic splice, without replay or network in
        # SQLite; a prepared body cannot smuggle unrelated edits into this API.
        target = pending.sources[0]
        location, blocks, version = _row(target, database_id, row_id)
        reference = {'id': pending.reference_id, 'type': 'synced_block', 'content': '', 'props': {
            'synced_from': {key: request['source'][key] for key in ('document_id', 'block_id')},
            'sync_id': synced.group_id(request['source']['document_id'], request['source']['block_id'])}}
        if (location != pending.edit.location or version != pending.edit.version
                or _canonical_json(_splice(blocks, request['anchor_id'], request['replace_empty'], reference)) != pending.edit.body_json):
            raise synced.SyncedShapeUnavailable('prepared row clipboard body changed')
        now = int(time.time())
        before = authority._editing_snapshot(con, page)
        applied = synced_database_edit.commit(authority, con, actor, page, pending.edit, now)
        after = authority._editing_snapshot(con, authority._document(con, document_id))
        expected = deepcopy(before)
        expected_row = next(item for db in expected['databases'] if db['id'] == database_id
                            for item in db['rows'] if item['id'] == row_id)
        expected_row.update(version=pending.edit.version + 1, body={'blocks': json.loads(pending.edit.body_json)})
        if _canonical_json(expected) != _canonical_json(after):
            raise AuthorityUnavailable('row clipboard changed unrelated canonical state')
        revision = page['revision'] + 1
        con.execute('UPDATE workspace_collaboration_documents SET revision=? WHERE id=?', (revision, document_id))
        con.execute('INSERT INTO workspace_collaboration_revisions(document_id,revision,document_sequence,content_sha256,snapshot_json,actor_subject_id,operation_id,created_at) VALUES(?,?,?,?,?,?,?,?)',
                    (document_id, revision, applied['document_sequence'], applied['content_sha256'],
                     _canonical_json(after), actor, operation, now))
        con.execute('UPDATE workspaces SET row_version=row_version+1,updated_at=? WHERE id=?', (now, workspace_id))
        result = {'contract': CONTRACT, 'workspace_id': workspace_id, 'document_id': document_id,
            'database_id': database_id, 'row_id': row_id, 'reference_id': pending.reference_id,
            'source': deepcopy(request['source']), 'workspace_epoch': authority._workspace(con, workspace_id)['security_epoch'],
            'document_epoch': page['access_epoch'], 'revision': revision, **applied}
        return authority._finish(con, actor=actor, action=ACTION, operation_id=operation, request_sha256=digest,
            workspace_id=workspace_id, document_id=document_id, workspace_epoch=result['workspace_epoch'],
            document_epoch=page['access_epoch'], document_sequence=applied['document_sequence'],
            event_type='database.surface.committed', event_payload=result, result={**result, 'replayed': False}, fault=fault)


def read_row_reference_result(authority, actor_subject_id, workspace_id, document_id, database_id, row_id, **request):
    actor = authority._require_active(actor_subject_id)
    digest = _request(actor, workspace_id, document_id, database_id, row_id, **request)
    with authority._reader() as con:
        con.execute('BEGIN')
        result = _replay(authority, con, actor, workspace_id, document_id, database_id, row_id, request['operation_id'], digest)
        return {'state': 'unknown'} if result is None else {'state': 'committed', 'result': result}


def read_row_paste_target(authority, actor_subject_id, workspace_id, document_id, database_id, row_id):
    """Current target fence, without source content or a transferable grant."""
    actor = authority._require_active(actor_subject_id)
    for value, field in ((workspace_id, 'workspace_id'), (document_id, 'document_id'),
                         (database_id, 'database_id'), (row_id, 'row_id')):
        synced._exact_id(value, field)
    with authority._reader() as con:
        con.execute('BEGIN')
        page, epoch = _target_access(authority, con, actor, workspace_id, document_id, synced.BodyLocation('row', database_id, row_id))
        descriptor = authority._editing_snapshot(con, page)
        rows = [row for db in descriptor['databases'] if db['id'] == database_id
                for row in db['rows'] if row['id'] == row_id and 'body' in row]
        if len(rows) != 1:
            raise NotFoundOrDenied('not found or permission denied')
        return {'contract': CONTRACT + '.target', 'workspace_id': workspace_id,
                'document_id': document_id, 'database_id': database_id, 'row_id': row_id,
                'expected_target_version': rows[0]['version'],
                'expected_workspace_epoch': authority._workspace(con, workspace_id)['security_epoch'],
                'expected_document_epoch': page['access_epoch'], 'expected_database_epoch': epoch}
