"""Prepare a complete independent instance; never commit a partial unsync.

This internal plan is not a client payload, lease, or permission cache. A writer
must resolve every resource dependency and revalidate inside its transaction.
"""
from copy import deepcopy
from dataclasses import dataclass
import json
from urllib.parse import quote, unquote, urlsplit, urlunsplit

from . import synced_blocks as synced, page_move
from .canonical_authority import _canonical_json, _operation_id, _sha256, _stable_id
from .page_copy_remap import DATABASE_BLOCKS, _RichLinks
from .page_copy_source import MAX_SOURCE_BYTES
from .synced_edit import _witnesses

CONTRACT = 'code_assist.notion.synced_detach_plan.v1'
PLACEMENT = ('indent', 'visual_indent', 'layout_group', 'layout_column',
             'layout_columns', 'tab_group', 'tab_id')


@dataclass(frozen=True)
class PreparedSyncedDetach:
    actor: str
    workspace_id: str
    document_id: str
    block_id: str
    operation_id: str
    origin: tuple[str, str]
    sources: tuple
    witnesses_json: str
    plan_json: str
    plan_sha256: str

    def value(self):
        return json.loads(self.plan_json)


def source_body(source, block_id):
    """Resolve one physical body; a main-page fallback would copy wrong data."""
    return synced.locate_block(json.loads(source.descriptor_json), block_id)


def destination_location(prepared):
    source = next(s for s in prepared.sources if s.document_id == prepared.document_id)
    return source_body(source, prepared.block_id)[0]


def _plan(tree, instance, *, actor, workspace, document, origin_document,
          operation, destination_ids, local_origin):
    parsed = urlsplit(local_origin) if local_origin else None
    if parsed and (parsed.scheme not in {'http', 'https'} or not parsed.netloc
                   or parsed.username or parsed.password or parsed.path not in {'', '/'}
                   or parsed.query or parsed.fragment):
        raise ValueError('trusted application origin required')
    local = (parsed.scheme.lower(), parsed.netloc.lower()) if parsed else None
    nodes = {}
    def scan(node, parent=None, depth=0):
        if depth > 64 or not isinstance(node, dict):
            raise synced.SyncedShapeUnavailable('invalid detached hierarchy')
        identity = synced._exact_id(node.get('id'), 'block_id')
        if identity in nodes or not isinstance(node.get('props'), dict) or not isinstance(node.get('children'), list):
            raise synced.SyncedShapeUnavailable('duplicate or malformed detached child')
        if not isinstance(node.get('content', ''), str) or not isinstance(node.get('type'), str):
            raise synced.SyncedShapeUnavailable('typed detached block required')
        if node['type'] == 'synced_block':
            synced.origin_of(node, origin_document)
            if node['props']['synced_from'] is not None and node['children']:
                raise synced.SyncedShapeUnavailable('reference cannot own copied children')
        nodes[identity] = (node, parent)
        for child in node['children']:
            scan(child, identity, depth + 1)
    scan(tree)
    if tree['type'] != 'synced_block' or tree['props'].get('synced_from', False) is not None:
        raise synced.SyncedShapeUnavailable('explicit original required')
    target = synced._exact_id(instance['id'], 'block_id')
    unwrap = not tree.get('content') and not tree['props'].get('rich_text_html') and bool(tree['children'])
    # An empty synced wrapper disappears. Keep its existing destination anchor
    # on the first surviving child; links to either source block then still land
    # on a real block. Only this pair may intentionally share a mapped ID.
    promoted = tree['children'][0]['id'] if unwrap else None
    def allocate(kind, *parts):
        return _stable_id(kind, CONTRACT, actor, workspace, document, target, operation, *parts)
    ids = {identity: target if identity in {tree['id'], promoted} else allocate('block', origin_document, identity)
           for identity in nodes}
    if len(set(ids.values())) != len(ids) - int(unwrap) or (set(ids.values()) - {target}) & set(destination_ids):
        raise synced.SyncedShapeUnavailable('detached block identity collision')
    tabs = {}
    layouts = {}
    for identity, (node, _parent) in nodes.items():
        props = node['props']
        if props.get('layout_group'):
            layouts.setdefault(props['layout_group'], allocate('layout', props['layout_group']))
        if node['type'] == 'tabs':
            for tab in props.get('tabs', []):
                old = synced._exact_id(tab.get('id'), 'tab_id')
                if (identity, old) in tabs:
                    raise synced.SyncedShapeUnavailable('duplicate tab in detached controller')
                tabs[identity, old] = allocate('tab', identity, old)
    dependencies = {}
    def dependency(kind, identity, **details):
        synced._exact_id(identity, kind + '_id')
        key = (kind, identity)
        value = {'kind':kind, 'source_id':identity, **details}
        if key in dependencies and dependencies[key] != value:
            raise synced.SyncedShapeUnavailable('inconsistent detached resource')
        dependencies[key] = value
        return value
    def file_id(old):
        new = allocate('file', origin_document, old)
        dependency('file', old, destination_id=new, source_document_id=origin_document)
        return new
    def url(value):
        if not isinstance(value, str) or not value:
            return value
        try:
            parts = urlsplit(value)
        except ValueError:
            return value
        if parts.scheme or parts.netloc:
            if local != (parts.scheme.lower(), parts.netloc.lower()):
                return value
        path, fragment = parts.path, parts.fragment
        prefix = '/api/notion/collaboration/files/'
        if path.startswith(prefix) and '/' not in path[len(prefix):]:
            path = prefix + quote(file_id(unquote(path[len(prefix):])), safe='')
        elif fragment.startswith('block-'):
            old = unquote(fragment[6:])
            source_path = '/page/' + quote(origin_document, safe='')
            if old in ids and path in {'', source_path}:
                fragment = 'block-' + quote(ids[old], safe='')
                if path:
                    path = '/page/' + quote(document, safe='')
        return urlunsplit((parts.scheme, parts.netloc, path, parts.query, fragment))
    def properties(value):
        # Preserve unknown metadata; a future resource writer must not treat
        # this tree as a ready-to-insert document or silently skip dependencies.
        if isinstance(value, list):
            return [properties(item) for item in value]
        if not isinstance(value, dict):
            return deepcopy(value)
        result = {}
        for key, item in value.items():
            if key == 'file_id' and isinstance(item, str) and item:
                result[key] = file_id(item)
            elif key in {'url', 'src'}:
                result[key] = url(item)
            elif key == 'rich_text_html' and isinstance(item, str):
                result[key] = _RichLinks(item, url).result()
            else:
                result[key] = properties(item)
        return result
    def build(node):
        identity, kind = node['id'], node['type']
        props = properties(node['props'])
        props.pop('children', None)
        if props.get('layout_group'):
            props['layout_group'] = layouts[props['layout_group']]
        if props.get('tab_group') in ids:
            controller, tab = props['tab_group'], props.get('tab_id')
            if (controller, tab) not in tabs:
                raise synced.SyncedShapeUnavailable('unresolved detached tab membership')
            props['tab_group'], props['tab_id'] = ids[controller], tabs[controller, tab]
        if kind == 'tabs':
            for tab in props.get('tabs', []):
                tab['id'] = tabs[identity, tab['id']]
            if props.get('active_tab_id'):
                if (identity, props['active_tab_id']) not in tabs:
                    raise synced.SyncedShapeUnavailable('unresolved detached active tab')
                props['active_tab_id'] = tabs[identity, props['active_tab_id']]
        if identity == tree['id']:
            kind = 'paragraph'
            props.pop('synced_from', None);props.pop('sync_id', None)
        elif kind == 'synced_block':
            target_document, target_block = synced.origin_of(node, origin_document)
            owned = nodes.get(target_block, (None,))[0]
            if target_document == origin_document and target_block != tree['id'] and owned and owned['type'] == 'synced_block' and owned['props'].get('synced_from', False) is None:
                target_document, target_block = document, ids[target_block]
            if props['synced_from'] is not None:
                props['synced_from'] = {'document_id':target_document, 'block_id':target_block}
            else:
                target_document, target_block = document, ids[identity]
            props['sync_id'] = synced.group_id(target_document, target_block)
        if 'linked_database' in props:
            from .linked_databases import source_of
            source_document, database = source_of(node)
            views = {view['id']:allocate('localview', identity, view['id']) for view in props['views']}
            for view in props['views']:
                view['id'] = views[view['id']]
            props['active_view_id'] = views[props['active_view_id']]
            dependency('linked_database', identity, source_document_id=source_document, database_id=database)
        elif kind in DATABASE_BLOCKS or props.get('database_id'):
            database = props.get('database_id', identity)
            props['database_id'] = allocate('db', origin_document, database)
            dependency('database', database, source_document_id=origin_document, destination_id=props['database_id'])
        if kind == 'child_page':
            dependency('child_page', props.get('page_id'), source_document_id=origin_document)
        if kind == 'synced_block' and props.get('synced_from') is not None:
            dependency('synced_reference', identity, origin=deepcopy(props['synced_from']))
        return {'id':ids[identity], 'type':kind, 'content':node.get('content', ''),
                'props':props, 'children':[build(child) for child in node['children']]}
    result = build(tree)
    # Placement belongs to the reference's page. Keep source placement metadata
    # for the structural writer, which must resolve flat/tab/column ancestry.
    destination_placement = {k:deepcopy(instance.get('props', {})[k]) for k in PLACEMENT if k in instance.get('props', {})}
    source_placement = {k:deepcopy(tree['props'][k]) for k in PLACEMENT if k in tree['props']}
    return {'contract':CONTRACT, 'commit_ready':False, 'application_origin':local_origin,
            'blocks':result['children'] if unwrap else [result], 'block_ids':ids,
            'source_placement':source_placement, 'destination_placement':destination_placement,
            'unwrap_empty_root':unwrap,
            # Inherited presentation needs resolving along with placement before
            # insertion. Do not drop custom/colour metadata with the wrapper.
            'wrapper_props':result['props'] if unwrap else None,
            'dependencies':list(dependencies.values()),
            'requires_resource_resolution':bool(dependencies)}


def revalidate_synced_detach(authority, con, prepared):
    if not con.in_transaction:
        raise ValueError('detach validation requires a transaction')
    if _sha256(prepared.plan_json) != prepared.plan_sha256:
        raise ValueError('detach plan was modified')
    expected = json.loads(prepared.witnesses_json)
    for source in prepared.sources:
        page, stamp = synced._authorize(authority, con, prepared.actor, source.document_id)
        witness = expected[source.document_id]
        if stamp != source.stamp or stamp[0] != witness['workspace_id'] or (stamp[1], stamp[4], stamp[5], stamp[6]) != tuple(witness[k] for k in ('workspace_epoch','document_sequence','document_epoch','generation')):
            raise synced.SyncedSourceChanged('detach source or destination changed')
        if source.document_id == prepared.document_id:
            location = source_body(source, prepared.block_id)[0]
            synced.authorize_edit_locations(authority, con, prepared.actor, prepared.workspace_id,
                                            prepared.document_id, [location])


def prepare_synced_detach(authority, actor_subject_id, workspace_id, document_id, block_id,
                          *, origin, witnesses, operation_id, local_origin=''):
    actor = authority._require_active(actor_subject_id)
    workspace = synced._exact_id(workspace_id, 'workspace_id')
    document = synced._exact_id(document_id, 'document_id')
    block = synced._exact_id(block_id, 'block_id')
    operation = _operation_id(operation_id)
    expected = _witnesses(witnesses)
    if not isinstance(origin, dict) or set(origin) != {'document_id','block_id'}:
        raise ValueError('exact original endpoint required')
    target = (synced._exact_id(origin['document_id'], 'document_id'), synced._exact_id(origin['block_id'], 'block_id'))
    if target == (document, block):
        raise synced.SyncedShapeUnavailable('original requires a separate unsync-all transaction')
    if set(expected) != {document, target[0]} or expected[document]['workspace_id'] != workspace:
        raise ValueError('detach witness scope mismatch')
    sources, _original, tree, actual_document, actual_block = synced._resolve(
        authority, actor, document, block, workspace, strict_structure=True, allow_database_bodies=True)
    if target != (actual_document, actual_block):
        raise synced.SyncedSourceChanged('detach original changed')
    location, _blocks, instance = source_body(sources[0], block)
    # Synced endpoints share a document-wide namespace even though ordinary
    # blocks may reuse IDs in separate database-owned bodies.
    destination_ids = {identity for _location, body in synced.body_entries(json.loads(sources[0].descriptor_json))
                       for identity in synced.index_blocks(body)}
    plan = _plan(tree, instance, actor=actor, workspace=workspace,
                 document=document, origin_document=target[0], operation=operation,
                 destination_ids=destination_ids, local_origin=local_origin)
    plan['destination_location'] = location.payload()
    encoded = _canonical_json(plan)
    if len(encoded.encode('utf-8')) > MAX_SOURCE_BYTES:
        raise synced.SyncedShapeUnavailable('detach plan exceeds preparation budget')
    prepared = PreparedSyncedDetach(actor, workspace, document, block, operation, target,
                                   tuple(sources), _canonical_json(expected), encoded, _sha256(encoded))
    with authority._reader() as con:
        con.execute('BEGIN')
        revalidate_synced_detach(authority, con, prepared)
    return prepared
