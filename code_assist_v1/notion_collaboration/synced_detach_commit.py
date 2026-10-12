"""Atomic detached destination write for fully prepared synced references.

The service is internal until public recovery and UI are connected.
Preparation is done before entering its single write transaction.
"""
from __future__ import annotations

import json
import shutil
import time
from urllib.parse import urlsplit

from . import page_archive, page_copy, page_move, synced_blocks as synced, synced_detach_delta as delta
from . import synced_database_edit, synced_detach_database as database_body
from .canonical_authority import AuthorityUnavailable, NotFoundOrDenied, _canonical_json, _operation_id, _request_sha256, _sha256, _stable_id
from .durable_transport import YJS_UPDATE_ENCODING
from .file_authority import CapacityUnavailable
from .selected_runtime_contract import runtime_binding_sha256
from .synced_edit import _witnesses

CONTRACT = 'code_assist.notion.synced_detach_commit.v1'
ACTION = 'collaboration.synced_detach'
FRAME_ACTION = 'collaboration.synced_detach_delta'


def request_sha256(actor, workspace_id, document_id, block_id, *, origin, witnesses,
                   operation_id, local_origin=''):
    """Bind retry/status to the exact caller request, before any source read."""
    actor = synced._exact_id(actor, 'actor_subject_id')
    workspace_id = synced._exact_id(workspace_id, 'workspace_id')
    document_id = synced._exact_id(document_id, 'document_id')
    block_id = synced._exact_id(block_id, 'block_id')
    _operation_id(operation_id)
    if not isinstance(origin, dict) or set(origin) != {'document_id','block_id'}:
        raise ValueError('exact detached original required')
    endpoint = {'document_id':synced._exact_id(origin['document_id'], 'document_id'),
                'block_id':synced._exact_id(origin['block_id'], 'block_id')}
    expected = _witnesses(witnesses)
    if set(expected) != {document_id,endpoint['document_id']} or expected[document_id]['workspace_id'] != workspace_id:
        raise ValueError('detach witness scope mismatch')
    if not isinstance(local_origin, str):
        raise ValueError('trusted application origin required')
    parsed = urlsplit(local_origin) if local_origin else None
    if parsed and (parsed.scheme not in {'http','https'} or not parsed.netloc or parsed.username or parsed.password
                   or parsed.path not in {'','/'} or parsed.query or parsed.fragment):
        raise ValueError('trusted application origin required')
    return _request_sha256(actor, ACTION, {
        'workspace_id':workspace_id,'document_id':document_id,'block_id':block_id,
        'origin':endpoint,'witnesses':expected,'application_origin':local_origin})


def _request(prepared):
    detached = prepared.body.placement.content.resources.detach
    return request_sha256(detached.actor, detached.workspace_id, detached.document_id, detached.block_id,
        origin={'document_id':detached.origin[0],'block_id':detached.origin[1]},
        witnesses=list(json.loads(detached.witnesses_json).values()),
        operation_id=detached.operation_id, local_origin=detached.value()['application_origin'])


def _receipt_destination(authority, con, actor, workspace_id, document_id):
    page = authority._document(con, document_id)
    if page['archived']:
        # Archived bodies have no ordinary page role. Only the current owner
        # can recover an exact result for a page in their own workspace trash.
        return page_archive._owner(authority, con, actor, workspace_id, document_id)[1]
    return page_move._authorize(authority, con, actor, workspace_id, document_id, 'view')


def _replay_scoped(authority, con, actor, workspace_id, document_id, block_id, origin,
                   operation_id, digest):
    def authorize():
        _receipt_destination(authority, con, actor, workspace_id, document_id)
    previous = authority._replay(con, actor=actor, action=ACTION,
                                 operation_id=operation_id, request_sha256=digest,
                                 authorize=authorize)
    if previous is None:
        return None
    if (previous.get('contract') != CONTRACT or previous.get('workspace_id') != workspace_id
            or previous.get('document_id') != document_id or previous.get('block_id') != block_id
            or previous.get('origin') != origin or previous.get('operation_id') != operation_id):
        raise AuthorityUnavailable('stored detached result scope is invalid')
    if previous.get('storage') == 'database_body':
        _receipt_body(authority, con, actor, workspace_id, document_id, previous.get('location'))
    return {**previous, 'replayed':True}


def _receipt_body(authority, con, actor, workspace_id, document_id, location):
    """Current physical-body access also fences receipts after DB/row archival."""
    from . import database_access, database_row_trash, database_trash
    if not isinstance(location, dict) or location.get('kind') not in {'row', 'template'}:
        raise AuthorityUnavailable('stored detached body location is invalid')
    field = 'row_id' if location['kind'] == 'row' else 'template_id'
    if set(location) != {'kind', 'database_id', field} or any(not isinstance(location[key], str) or not location[key] for key in ('database_id', field)):
        raise AuthorityUnavailable('stored detached body location is invalid')
    db = con.execute('SELECT document_id FROM workspace_collaboration_databases WHERE id=?', (location['database_id'],)).fetchone()
    if db is None or db['document_id'] != document_id:
        raise NotFoundOrDenied('not found or permission denied')
    page = authority._document(con, document_id)
    archived = bool(page['archived']) or database_trash.is_trashed(con, location['database_id'])
    if field == 'row_id':
        row = con.execute('SELECT 1 FROM workspace_collaboration_database_rows WHERE database_id=? AND id=?', (location['database_id'], location[field])).fetchone()
        try:
            database_row_trash.require_active(con, location['database_id'], location[field])
        except NotFoundOrDenied:
            archived = True
    else:
        row = con.execute('SELECT state FROM workspace_collaboration_database_templates WHERE database_id=? AND id=?', (location['database_id'], location[field])).fetchone()
        archived = archived or row is not None and row['state'] != 'active'
    if row is None:
        raise NotFoundOrDenied('not found or permission denied')
    if archived:
        database_trash._owner(authority, con, actor, workspace_id, document_id)
    elif database_access.installed(con):
        database_access.resolve_on_connection(authority, con, actor, workspace_id, location['database_id'])
    return bool(archived)


def _replay(authority, con, prepared, request_sha256):
    detached = prepared.body.placement.content.resources.detach
    return _replay_scoped(authority, con, detached.actor, detached.workspace_id,
        detached.document_id, detached.block_id,
        {'document_id':detached.origin[0],'block_id':detached.origin[1]},
        detached.operation_id, request_sha256)


def _capacity(authority, prepared):
    # Files reuse verified immutable blob bytes; SQLite still needs headroom for
    # both canonical rows and the WAL-backed CRDT/revision/audit records.
    content = prepared.body.placement.content.value()
    metadata = len(prepared.body.payload_json.encode('utf-8')) + len(_canonical_json(content['databases']).encode('utf-8'))
    metadata += len(_canonical_json(content['files']).encode('utf-8')) + sum(map(len, prepared.update_frames))
    usage = shutil.disk_usage(authority.catalog_path.parent)
    remaining = usage.free - metadata * 4 - 4 * 1024 * 1024
    if (remaining < authority.minimum_free_bytes
            or usage.total and remaining * 100 / usage.total < authority.minimum_free_percent):
        raise CapacityUnavailable('operational detach disk reserve unavailable')


def _insert_files(con, prepared, now):
    detached = prepared.body.placement.content.resources.detach
    captured = prepared.body.placement.content.resources.value()
    content = prepared.body.placement.content.value()
    for item in content['files']:
        original = con.execute(
            "SELECT * FROM workspace_collaboration_files WHERE id=? AND workspace_id=? AND document_id=? AND state='active'",
            (item['source_file_id'], captured['source_workspace_id'], item['source_document_id']),
        ).fetchone()
        if (original is None or item['source_document_id'] != detached.origin[0]
                or any(original[key] != item[key] for key in
                       ('original_name','mime_type','size_bytes','content_sha256','blob_relative_path'))):
            raise synced.SyncedSourceChanged('detached attachment changed')
        con.execute(
            "INSERT INTO workspace_collaboration_files(id,workspace_id,document_id,upload_session_id,uploader_subject_id,original_name,mime_type,size_bytes,content_sha256,blob_relative_path,state,row_version,created_at,updated_at,origin_kind,source_file_id,source_document_id,copied_by_subject_id,copy_operation_id) VALUES(?,?,?,NULL,?,?,?,?,?,?,'active',1,?,?,'copy',?,?,?,?)",
            (item['id'], detached.workspace_id, detached.document_id, original['uploader_subject_id'],
             item['original_name'], item['mime_type'], item['size_bytes'], item['content_sha256'],
             item['blob_relative_path'], now, now, item['source_file_id'], item['source_document_id'],
             detached.actor, detached.operation_id),
        )


def _append_frames(authority, con, prepared, workspace, page, first_sequence, now):
    detached = prepared.body.placement.content.resources.detach
    current = con.execute("SELECT COALESCE(MAX(update_sequence),0) FROM workspace_collaboration_crdt_updates WHERE document_id=?",
                          (detached.document_id,)).fetchone()[0]
    binding = runtime_binding_sha256()
    last_sequence = first_sequence
    for ordinal, frame in enumerate(prepared.update_frames, 1):
        sequence, update_sequence = first_sequence + ordinal, current + ordinal
        frame_operation = _stable_id('detach_update', detached.actor, detached.operation_id, str(ordinal))
        identity = _stable_id('cupd', detached.actor, FRAME_ACTION, detached.operation_id, str(ordinal))
        checksum = _sha256(frame)
        con.execute("UPDATE workspace_collaboration_documents SET document_sequence=?,row_version=row_version+1,updated_at=? WHERE id=?",
                    (sequence, now, detached.document_id))
        con.execute(
            "INSERT INTO workspace_collaboration_crdt_updates(id,workspace_id,document_id,update_sequence,document_sequence,actor_subject_id,client_id,session_binding_sha256,operation_id,client_sequence,payload_encoding,payload_bytes,payload_sha256,payload,workspace_epoch,document_epoch,runtime_binding_sha256,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (identity, detached.workspace_id, detached.document_id, update_sequence, sequence,
             detached.actor, 'synced-detach-service', _sha256(CONTRACT + detached.workspace_id),
             frame_operation, update_sequence, YJS_UPDATE_ENCODING, len(frame), checksum, frame,
             workspace['security_epoch'], page['access_epoch'], binding, now),
        )
        con.execute("INSERT INTO workspace_collaboration_crdt_update_bindings(update_id,document_id,generation,rich_text_scope,created_at) VALUES(?,?,?,'document_rich_text_leaves',?)",
                    (identity, detached.document_id, prepared.generation, now))
        event = {'workspace_id':detached.workspace_id, 'page_id':detached.document_id,
                 'document_sequence':sequence, 'update_sequence':update_sequence,
                 'subject_id':detached.actor, 'client_id':'synced-detach-service',
                 'client_sequence':update_sequence, 'operation_id':frame_operation,
                 'message_type':'crdt.update', 'payload_encoding':YJS_UPDATE_ENCODING,
                 'payload_sha256':checksum, 'workspace_epoch':workspace['security_epoch'],
                 'page_epoch':page['access_epoch'], 'runtime_binding_sha256':binding}
        authority._finish(con, actor=detached.actor, action=FRAME_ACTION, operation_id=frame_operation,
                          request_sha256=_request_sha256(detached.actor, FRAME_ACTION,
                                                         {'operation_id':detached.operation_id,'ordinal':ordinal,'payload_sha256':checksum}),
                          workspace_id=detached.workspace_id, document_id=detached.document_id,
                          workspace_epoch=workspace['security_epoch'], document_epoch=page['access_epoch'],
                          document_sequence=sequence, event_type='crdt.update', event_payload=event,
                          result=event, fault=None)
        last_sequence = sequence
    return last_sequence


def _check_snapshot(before, after, prepared):
    content = prepared.body.placement.content.value()
    detached = prepared.body.placement.content.resources.detach
    captured = next(s for s in detached.sources if s.document_id == detached.document_id)
    descriptor = json.loads(captured.descriptor_json)
    for field in ('title','icon','cover','settings','databases','files'):
        if _canonical_json(before[field]) != _canonical_json(descriptor[field]):
            raise synced.SyncedSourceChanged('detached destination canonical metadata changed')
    for field in ('title','icon','cover','settings'):
        if _canonical_json(after[field]) != _canonical_json(before[field]):
            raise AuthorityUnavailable('detached destination metadata was changed')
    database_write = isinstance(prepared, database_body.PreparedDetachedDatabaseBody)
    expected_blocks = before['blocks'] if database_write else prepared.body.value()['blocks']
    if _canonical_json(after['blocks']) != _canonical_json(expected_blocks):
        raise AuthorityUnavailable('detached canonical body differs from prepared body')
    previous_databases = {item['id']:item for item in before['databases']}
    if database_write:
        # A single body/version is the only mutable part of existing DB state.
        # Cells, appearance, defaults, templates and sibling bodies stay exact.
        location = prepared.body_edit.location
        expected = json.loads(_canonical_json(previous_databases[location.database_id]))
        key, identity = ('rows',location.row_id) if location.kind == 'row' else ('templates',location.template_id)
        target = next(item for item in expected[key] if item['id'] == identity)
        target['version'] = prepared.body_edit.version + 1
        blocks = json.loads(prepared.body_edit.body_json)
        target['body'] = {'blocks':blocks} if location.kind == 'row' else blocks
        previous_databases[location.database_id] = expected
    copied_databases = {item['id']:item for item in content['databases']}
    actual_databases = {item['id']:item for item in after['databases']}
    if (set(previous_databases) & set(copied_databases)
            or set(actual_databases) != set(previous_databases) | set(copied_databases)
            or any(_canonical_json(actual_databases[key]) != _canonical_json(value)
                   for key,value in {**previous_databases,**copied_databases}.items())):
        raise AuthorityUnavailable('detached database resources differ from prepared content')
    previous_files = {item['id']:item for item in before['files']}
    copied_files = {item['id']:{'id':item['id'],'name':item['original_name'],
                 'mime_type':item['mime_type'],'size':item['size_bytes'],
                 'sha256':item['content_sha256'],'state':'active','version':1}
                 for item in content['files']}
    actual_files = {item['id']:item for item in after['files']}
    if (set(previous_files) & set(copied_files)
            or set(actual_files) != set(previous_files) | set(copied_files)
            or any(_canonical_json(actual_files[key]) != _canonical_json(value)
                   for key,value in {**previous_files,**copied_files}.items())):
        raise AuthorityUnavailable('detached file resources differ from prepared content')


def commit_prepared_detach(authority, prepared: delta.PreparedDetachedDelta, *, fault=None):
    """Commit a prepared destination in one transaction; never run Node here."""
    detached = prepared.body.placement.content.resources.detach
    request_sha256 = _request(prepared)
    with authority._reader() as con:
        con.execute('BEGIN')
        previous = _replay(authority, con, prepared, request_sha256)
        if previous is not None:
            return previous
    _capacity(authority, prepared)
    with authority._transaction() as con:
        previous = _replay(authority, con, prepared, request_sha256)
        if previous is not None:
            return previous
        database_write = isinstance(prepared, database_body.PreparedDetachedDatabaseBody)
        if database_write:
            database_body.revalidate_detached_database_body(authority, con, prepared)
        else:
            delta.revalidate_detached_delta(authority, con, prepared)
        # The prepared plan already revalidates source view and destination
        # edit rights. Copy's owner-only workspace helper is too restrictive
        # for an editor detaching a reference on a page they may edit.
        workspace = authority._workspace(con, detached.workspace_id)
        page = page_move._authorize(authority, con, detached.actor, detached.workspace_id,
                                    detached.document_id, 'edit_content')
        page_move._unlocked(con, page, detached.actor)
        before = authority._editing_snapshot(con, page)
        now = int(time.time())
        if not database_write:
            con.execute('DELETE FROM workspace_collaboration_blocks WHERE document_id=?', (detached.document_id,))
            authority._insert_blocks(con, detached.document_id, prepared.body.value()['blocks'], now=now)
        content = prepared.body.placement.content.value()
        for database in content['databases']:
            page_copy._insert_database(con, detached.document_id, database, now)
        _insert_files(con, prepared, now)
        written = (synced_database_edit.commit(authority, con, detached.actor, page, prepared.body_edit, now)
                   if database_write else None)
        after = authority._editing_snapshot(con, authority._document(con, detached.document_id))
        _check_snapshot(before, after, prepared)
        last_update_sequence = (_append_frames(authority, con, prepared, workspace, page,
                                               page['document_sequence'], now) if not database_write else page['document_sequence'])
        # The final detach receipt has its own document event sequence. Event
        # history forbids two events at one sequence, even inside one commit.
        sequence = last_update_sequence + 1
        revision = page['revision'] + 1
        checksum = _sha256(_canonical_json(after))
        con.execute('UPDATE workspace_collaboration_documents SET revision=?,document_sequence=?,row_version=row_version+?,content_sha256=?,updated_at=? WHERE id=?',
                    (revision, sequence, int(not database_write), checksum, now, detached.document_id))
        con.execute('INSERT INTO workspace_collaboration_revisions(document_id,revision,document_sequence,content_sha256,snapshot_json,actor_subject_id,operation_id,created_at) VALUES(?,?,?,?,?,?,?,?)',
                    (detached.document_id, revision, sequence, checksum, _canonical_json(after),
                     detached.actor, detached.operation_id, now))
        con.execute('UPDATE workspaces SET row_version=row_version+1,updated_at=? WHERE id=?',
                    (now, detached.workspace_id))
        result = {'contract':CONTRACT, 'workspace_id':detached.workspace_id,
                  'document_id':detached.document_id, 'block_id':detached.block_id,
                  'origin':{'document_id':detached.origin[0],'block_id':detached.origin[1]},
                  'operation_id':detached.operation_id, 'revision':revision,
                  'document_sequence':sequence, 'document_epoch':page['access_epoch'],
                  'workspace_epoch':workspace['security_epoch'], 'content_sha256':checksum,
                  'added_block_ids':prepared.body.value()['added_block_ids'],
                  'copied_database_ids':[item['id'] for item in content['databases']],
                  'copied_file_ids':[item['id'] for item in content['files']],
                  'update_frames':len(prepared.update_frames)}
        if written:
            result.update(storage=written['storage'], location=written['location'],
                          target_version=written['target_version'], body_sha256=written['body_sha256'])
        saved = authority._finish(con, actor=detached.actor, action=ACTION,
                                  operation_id=detached.operation_id, request_sha256=request_sha256,
                                  workspace_id=detached.workspace_id, document_id=detached.document_id,
                                  workspace_epoch=workspace['security_epoch'], document_epoch=page['access_epoch'],
                                  document_sequence=sequence, event_type='synced.detached',
                                  event_payload=result, result=result, fault=fault)
        return {**saved, 'replayed':False}
