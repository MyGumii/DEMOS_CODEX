"""Read-only database export models with current source/permission witnesses."""
from copy import deepcopy

from .canonical_authority import NotFoundOrDenied
from . import linked_databases, synced_blocks
from .search_preview import _relation_labels


class HtmlExportChanged(RuntimeError):
    """A source changed after preparation; rebuild the entire output."""


DATABASE_TYPES = {'table', 'board', 'list', 'gallery', 'calendar', 'timeline',
                  'chart', 'dashboard', 'form', 'feed', 'map'}
VIEW_KEYS = {'filters', 'filter_group', 'sorts', 'group_property', 'group_sort', 'group_order', 'month', 'timeline_anchor',
             'timeline_scale', 'timeline_date_property', 'board_group_property',
             'calendar_date_property', 'property_visibility', 'property_order',
             'show_source_title', 'show_vertical_lines', 'show_page_icons', 'wrap_all_content'}


def _stamp(authority, con, actor, document):
    return synced_blocks._authorize(authority, con, actor, document)[1]


def _check_bundle(stamp, bundle):
    if (stamp[0], stamp[1], stamp[4], stamp[5]) != (
        bundle['source_workspace_id'], bundle['source_workspace_epoch'],
        bundle['source_document_sequence'], bundle['source_document_epoch'],
    ):
        raise HtmlExportChanged('export document changed')


def _relations(authority, con, actor, database, witnesses):
    database['relation_labels'] = _relation_labels(authority, con, actor, database)
    for prop in database['properties']:
        if prop['type'] != 'relation':
            continue
        target = con.execute('SELECT document_id FROM workspace_collaboration_databases WHERE id=?',
                             (str(prop.get('config', {}).get('target_database_id') or ''),)).fetchone()
        if target is None:
            continue
        try:
            identity = str(target['document_id'])
            stamp = _stamp(authority, con, actor, identity)
            if identity in witnesses and witnesses[identity] != stamp:
                raise HtmlExportChanged('relation source changed')
            witnesses[identity] = stamp
        except NotFoundOrDenied:
            # The materializer already removes inaccessible relation values.
            continue


def _view(block, database, views=None, active_id=None):
    p = block.get('props') or {}
    canonical_views = views is None
    views = [v for v in (database.get('views', []) if canonical_views else views)
             if v.get('state') != 'archived' and v.get('type') in DATABASE_TYPES]
    active_id = p.get('active_view_id') if active_id is None else active_id
    active = next((v for v in views if v.get('id') == active_id), views[0] if views else None)
    config = {key: deepcopy(p[key]) for key in VIEW_KEYS if key in p}
    if active:
        values = active.get('config', {})
        config = {key: deepcopy(values[key]) for key in VIEW_KEYS if key in values}
    return {'database': database, 'config': config,
            'type': active.get('type') if active else p.get('view') or block.get('type') or 'table',
            'view_name': active.get('name') if active else '', 'unavailable': False}


def prepare(authority, actor, bundle, snapshot):
    """Use canonical rows, never client row caches stored on a block."""
    document = bundle['source_document_id']
    witnesses, models = {}, {}
    blocks = [b for b in snapshot.get('blocks', []) if isinstance(b, dict) and b.get('type') in DATABASE_TYPES]
    with authority._reader() as con:
        con.execute('BEGIN')
        row, stamp = synced_blocks._authorize(authority, con, actor, document)
        _check_bundle(stamp, bundle)
        witnesses[document] = stamp
        databases = {db['id']: db for db in authority._editing_snapshot(con, row)['databases']} if blocks else {}
        for block in blocks:
            p = block.get('props') or {}
            if 'linked_database' in p:
                continue
            raw = databases.get(str(p.get('database_id') or block.get('id') or ''))
            if raw is None:
                models[str(block.get('id'))] = {'unavailable': True}
                continue
            database = deepcopy(raw)
            database['rows'] = authority._materialize_rows(con, actor, database)
            _relations(authority, con, actor, database, witnesses)
            models[str(block.get('id'))] = _view(block, database)
    for block in blocks:
        if 'linked_database' not in (block.get('props') or {}):
            continue
        identity = str(block.get('id'))
        try:
            result = linked_databases.read_linked_database(authority, actor, document, identity,
                                                            workspace_id=bundle['source_workspace_id'])
        except (NotFoundOrDenied, linked_databases.LinkedDatabaseShapeUnavailable):
            models[identity] = {'unavailable': True}
            continue
        except linked_databases.LinkedDatabaseSourceChanged as exc:
            raise HtmlExportChanged('linked source changed') from exc
        with authority._reader() as con:
            con.execute('BEGIN')
            for witness in result['witnesses']:
                source_id = witness['document_id']
                try:
                    stamp = _stamp(authority, con, actor, source_id)
                except NotFoundOrDenied as exc:
                    raise HtmlExportChanged('linked source no longer available') from exc
                expected = (witness['workspace_id'], witness['workspace_epoch'], witness['document_sequence'],
                            witness['document_epoch'], witness['generation'])
                if (stamp[0], stamp[1], stamp[4], stamp[5], stamp[6]) != expected:
                    raise HtmlExportChanged('linked source changed')
                if source_id in witnesses and witnesses[source_id] != stamp:
                    raise HtmlExportChanged('inconsistent export sources')
                witnesses[source_id] = stamp
            database = result['database']
            # Recompute relation-derived values in the same reader as their
            # labels and witnesses; do not certify an older computed value.
            database['rows'] = authority._materialize_rows(con, actor, database)
            _relations(authority, con, actor, database, witnesses)
        models[identity] = _view(block, database, result['views'], result['active_view_id'])
    verify(authority, actor, witnesses)
    return models, witnesses


def verify(authority, actor, witnesses):
    authority._require_active(actor)
    with authority._reader() as con:
        con.execute('BEGIN')
        for document, expected in witnesses.items():
            try:
                current = _stamp(authority, con, actor, document)
            except NotFoundOrDenied as exc:
                raise HtmlExportChanged('export source no longer available') from exc
            if current != expected:
                raise HtmlExportChanged('export source changed')
