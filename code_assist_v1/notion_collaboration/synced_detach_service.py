"""Internal request boundary for one synced-reference detach and its receipt.

Replay precedes expensive source capture so a lost response never requires the
old source versions to remain available. Routes and browser recovery follow.
"""
from __future__ import annotations

from . import synced_detach_body as body, synced_detach_commit as commit
from . import synced_detach_delta as delta, synced_detach_placement as placement
from . import synced_detach_plan as plan, synced_detach_remap as remap
from . import synced_detach_resources as resources
from . import synced_detach_database as database


def detach_synced_block(authority, actor_subject_id, workspace_id, document_id, block_id, *,
                        origin, witnesses, operation_id, local_origin='', fault=None):
    actor = authority._require_active(actor_subject_id)
    digest = commit.request_sha256(actor, workspace_id, document_id, block_id,
        origin=origin, witnesses=witnesses, operation_id=operation_id, local_origin=local_origin)
    with authority._reader() as con:
        con.execute('BEGIN')
        previous = commit._replay_scoped(authority, con, actor, workspace_id, document_id,
            block_id, origin, operation_id, digest)
        if previous is not None:
            return previous
    captured = plan.prepare_synced_detach(authority, actor, workspace_id, document_id, block_id,
        origin=origin, witnesses=witnesses, operation_id=operation_id, local_origin=local_origin)
    selected = resources.prepare_synced_resources(authority, captured)
    mapped = remap.prepare_detached_content(authority, selected)
    located = placement.prepare_detached_placement(authority, mapped)
    complete_body = body.prepare_detached_body(authority, located)
    if plan.destination_location(captured).kind == 'page':
        prepared = delta.prepare_detached_delta(authority, complete_body)
    else:
        prepared = database.prepare_detached_database_body(authority, complete_body)
    if commit._request(prepared) != digest:
        raise ValueError('detached request changed during preparation')
    return commit.commit_prepared_detach(authority, prepared, fault=fault)


def read_synced_detach_result(authority, actor_subject_id, workspace_id, document_id, block_id, *,
                              origin, witnesses, operation_id, local_origin=''):
    """Read an exact receipt without re-reading the potentially changed source."""
    actor = authority._require_active(actor_subject_id)
    digest = commit.request_sha256(actor, workspace_id, document_id, block_id,
        origin=origin, witnesses=witnesses, operation_id=operation_id, local_origin=local_origin)
    with authority._reader() as con:
        con.execute('BEGIN')
        page = commit._receipt_destination(authority, con, actor, workspace_id, document_id)
        result = commit._replay_scoped(authority, con, actor, workspace_id, document_id,
            block_id, origin, operation_id, digest)
        if result is None:
            return {'state':'unknown'}
        current = {
            'archived':bool(page['archived']), 'document_sequence':page['document_sequence'],
            'document_epoch':page['access_epoch']}
        if result.get('storage') == 'database_body':
            current['body_archived'] = commit._receipt_body(authority, con, actor, workspace_id, document_id, result['location'])
        return {'state':'committed', 'result':result, 'current':current}
