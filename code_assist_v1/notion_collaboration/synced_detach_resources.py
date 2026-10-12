"""Capture only resources owned by one detached subtree, without any writes.

This is not a commit-ready copy or an access grant. Resource remapping, child
pages, placement and the writer still follow this verified immutable capture.
"""
from dataclasses import dataclass
import json
from urllib.parse import unquote, urlsplit

from . import synced_blocks as synced, synced_detach_plan as detach, page_copy_source
from .canonical_authority import _canonical_json, _sha256, NotFoundOrDenied
from .page_copy_remap import DATABASE_BLOCKS, _RichLinks
from .linked_databases import source_of
from .synced_tree import content_tree

CONTRACT = 'code_assist.notion.synced_detach_resources.v1'


@dataclass(frozen=True)
class PreparedSyncedResources:
    detach: detach.PreparedSyncedDetach
    payload_json: str
    payload_sha256: str

    def value(self):
        return json.loads(self.payload_json)

    @property
    def files_json(self):
        return _canonical_json(self.value()['files'])


def _select(tree, descriptor, document, local_origin):
    parsed = urlsplit(local_origin) if local_origin else None
    if parsed and (parsed.scheme not in {'http','https'} or not parsed.netloc or parsed.username
                   or parsed.password or parsed.path not in {'','/'} or parsed.query or parsed.fragment):
        raise ValueError('trusted application origin required')
    local = (parsed.scheme.lower(),parsed.netloc.lower()) if parsed else None
    databases = {}
    for value in descriptor.get('databases', []):
        identity = synced._exact_id(value.get('id'),'database_id')
        if identity in databases:
            raise synced.SyncedShapeUnavailable('duplicate canonical database')
        databases[identity] = value
    wanted, files, references = {}, set(), {}

    def file(identity):
        files.add(synced._exact_id(identity,'file_id'))

    def url(value):
        if not isinstance(value,str) or not value:
            return value
        try:
            parts = urlsplit(value)
        except ValueError:
            return value
        if (parts.scheme or parts.netloc) and local != (parts.scheme.lower(),parts.netloc.lower()):
            return value
        prefix = '/api/notion/collaboration/files/'
        if parts.path.startswith(prefix) and '/' not in parts.path[len(prefix):]:
            file(unquote(parts.path[len(prefix):]))
        return value

    def reference(kind, **values):
        item = {'kind':kind, **values}
        references[_canonical_json(item)] = item

    def walk(value, depth=0):
        if depth > 128:
            raise synced.SyncedShapeUnavailable('deep detach resource structure')
        if isinstance(value,list):
            for item in value:walk(item,depth+1)
        elif isinstance(value,dict):
            props = value.get('props')
            database_block = False
            if isinstance(props,dict) and isinstance(value.get('id'),str) and isinstance(value.get('type'),str):
                kind = value['type']
                database_block = 'linked_database' in props or kind in DATABASE_BLOCKS or bool(props.get('database_id'))
                if 'linked_database' in props:
                    source_document,database = source_of(value)
                    reference('linked_database',document_id=source_document,database_id=database)
                elif kind in DATABASE_BLOCKS or props.get('database_id'):
                    database = synced._exact_id(props.get('database_id',value['id']),'database_id')
                    if database not in databases:
                        raise synced.SyncedShapeUnavailable('owned database is missing from current canonical source')
                    wanted.setdefault(database,databases[database])
                if kind == 'child_page':
                    reference('child_page',document_id=synced._exact_id(props.get('page_id'),'document_id'))
                if kind == 'synced_block':
                    endpoint = synced.origin_of(value,document)
                    if props['synced_from'] is not None:
                        reference('synced_reference',document_id=endpoint[0],block_id=endpoint[1])
            for key,item in value.items():
                if key == 'props' and database_block:
                    # Data caches can be stale or refer to an external linked
                    # source. Only the selected canonical database owns rows.
                    item = {k:v for k,v in item.items() if k not in {'rows','properties','views','templates','_canonical_version'}}
                if key == 'file_id' and item:file(item)
                elif key in {'url','src','cover'}:url(item)
                elif key == 'icon' and isinstance(item,str):
                    file(item[3:]) if item.startswith('nf:') else url(item)
                elif key == 'rich_text_html' and isinstance(item,str):_RichLinks(item,url).result()
                walk(item,depth+1)

    walk(tree)
    visited = set()
    while set(wanted)-visited:
        identity = sorted(set(wanted)-visited)[0]
        visited.add(identity)
        database = wanted[identity]
        walk({key:[item for item in value if item.get('state') != 'archived']
              if key in {'views','templates'} else value for key,value in database.items()})
    return ([wanted[key] for key in sorted(wanted)], sorted(files),
            [references[key] for key in sorted(references)])


def _file_records(con, prepared, ids):
    origin = next(source for source in prepared.sources if source.document_id == prepared.origin[0])
    records = []
    for start in range(0,len(ids),400):
        batch = ids[start:start+400]
        rows = con.execute("SELECT * FROM workspace_collaboration_files WHERE document_id=? AND workspace_id=? "
            "AND state='active' AND id IN ("+','.join('?' for _ in batch)+") ORDER BY id",
            (prepared.origin[0],origin.stamp[0],*batch)).fetchall()
        records.extend(dict(row) for row in rows)
    if len(records) != len(ids):
        raise NotFoundOrDenied('a detached attachment is unavailable')
    return sorted(records,key=lambda item:item['id'])


def revalidate_synced_resources(authority, con, prepared):
    detach.revalidate_synced_detach(authority,con,prepared.detach)
    if _sha256(prepared.payload_json) != prepared.payload_sha256:
        raise ValueError('detach resource capture was modified')
    value = prepared.value()
    actual = _file_records(con,prepared.detach,[item['id'] for item in value['files']])
    if _canonical_json(actual) != _canonical_json(value['files']):
        raise synced.SyncedSourceChanged('detach file metadata changed')


def prepare_synced_resources(authority, prepared):
    with authority._reader() as con:
        con.execute('BEGIN')
        detach.revalidate_synced_detach(authority,con,prepared)
    origin = next(source for source in prepared.sources if source.document_id == prepared.origin[0])
    descriptor = json.loads(origin.descriptor_json)
    tree = content_tree(detach.source_body(origin, prepared.origin[1])[1], prepared.origin[1])
    databases, ids, references = _select(tree,descriptor,prepared.origin[0],prepared.value()['application_origin'])
    with authority._reader() as con:
        con.execute('BEGIN')
        detach.revalidate_synced_detach(authority,con,prepared)
        files = _file_records(con,prepared,ids)
    encoded = _canonical_json({'contract':CONTRACT,'commit_ready':False,'source_document_id':prepared.origin[0],
        'source_workspace_id':origin.stamp[0],'databases':databases,'files':files,'references':references})
    if len(encoded.encode('utf-8')) > page_copy_source.MAX_SOURCE_BYTES:
        raise synced.SyncedShapeUnavailable('detach resources exceed preparation budget')
    result = PreparedSyncedResources(prepared,encoded,_sha256(encoded))
    # Existing immutable-blob validation runs with no open SQLite transaction.
    page_copy_source._verify_blobs(authority,[result])
    with authority._reader() as con:
        con.execute('BEGIN')
        revalidate_synced_resources(authority,con,result)
    return result
