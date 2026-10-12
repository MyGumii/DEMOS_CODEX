"""Bounded copy-only attributed text sidecar and independently checked seed.

Descriptors retain the existing four-field block contract. This optional
sidecar owns raw Y.Text runs; no source state or browser ACK is fabricated.
"""
import base64
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import subprocess

from .canonical_authority import AuthorityUnavailable, _canonical_json, _sha256, _stable_id
from . import parent_restore_recovery, search_projection
from .durable_transport import YJS_UPDATE_ENCODING
from .selected_runtime_contract import runtime_binding_sha256

CONTRACT = 'code_assist.notion.page_copy_rich.v1'
MAX_SEED_BYTES = 1_048_576


def _text(delta):
    if not isinstance(delta, list) or any(not isinstance(run, dict)
            or set(run) - {'insert', 'attributes'} or not isinstance(run.get('insert'), str)
            or 'attributes' in run and not isinstance(run['attributes'], dict) for run in delta):
        raise AuthorityUnavailable('copied attributed text requires string runs')
    return ''.join(run['insert'] for run in delta)


def capture(source, descriptor):
    """Use current/deletion-bound state already authorized by the source witness."""
    restores = source.get('post_frame_structure', {}).get('restores', [])
    if not source['frames'] and not restores:
        return ''
    latest = {item['target_id']: item for item in restores}
    wanted = {block['id'] for block in descriptor['blocks']}
    items = [item for identity, item in latest.items() if identity in wanted]
    current, *archives = search_projection.materialize([source['frames'],
        *[[base64.b64decode(frame['payload'], validate=True) for frame in item['frames']]
          for item in items]], rich=True, identities=bool(items))
    rich = deepcopy(current['rich_text'])
    visible = {block['id']: block for block in current['blocks'] or []}
    for item, archived in zip(items, archives):
        identity = item['target_id']
        block = next((value for value in archived['blocks'] or [] if value['id'] == identity), None)
        if item['frames'] and block is None:
            raise AuthorityUnavailable('deleted rich copy body is unavailable')
        if parent_restore_recovery.keep_current(item, visible.get(identity), block,
                current['block_identities'].get(identity), archived['state_vector']):
            continue
        rich['blocks'][identity] = (archived['rich_text']['blocks'][identity] if block == item['values']
            else [{'insert': item['values']['content']}] if item['values']['content'] else [])
    result = {'blocks': {}}
    if rich['title'] is not None and any(run.get('attributes') for run in rich['title']):
        if _text(rich['title']) != descriptor['title']:
            raise AuthorityUnavailable('copied title text differs from its formatting')
        result['title'] = rich['title']
    for block in descriptor['blocks']:
        delta = rich['blocks'].get(block['id'], [])
        if any(run.get('attributes') for run in delta):
            if _text(delta) != block['content']:
                raise AuthorityUnavailable('copied body text differs from its formatting')
            result['blocks'][block['id']] = delta
    return _canonical_json(result) if result['blocks'] or 'title' in result else ''


def remap(value, scope, remapper):
    if not value:
        return ''
    rich = json.loads(value)
    for delta in [*rich['blocks'].values(), *([rich['title']] if 'title' in rich else [])]:
        _text(delta)
        for run in delta:
            attributes = run.get('attributes', {})
            for key in ('link', 'href', 'url', 'src'):
                if isinstance(attributes.get(key), str):
                    attributes[key] = remapper.url(attributes[key], scope)
    rich['blocks'] = {remapper.ref('block', scope, identity): delta
                      for identity, delta in rich['blocks'].items()}
    return _canonical_json(rich)


def prepare(plan, contents):
    """Prepare new independent Yjs identities, then use a separate decoder."""
    seeds = {}
    from .page_copy_source import MAX_SOURCE_BYTES
    total = sum(len(document.descriptor_json.encode()) + len(document.files_json.encode())
        + len(document.rich_text_json.encode()) for document in plan.documents)
    node = shutil.which('node')
    for document in plan.documents:
        if not document.rich_text_json:
            continue
        descriptor = contents[document.document_id]
        body = [{key: value for key, value in block.items() if key != 'position'}
                for block in descriptor['blocks']]
        rich = json.loads(document.rich_text_json)
        blocks = {block['id']: block for block in descriptor['blocks']}
        if (set(rich) - {'title', 'blocks'} or not set(rich['blocks']).issubset(blocks)
                or 'title' in rich and _text(rich['title']) != descriptor['title']
                or any(_text(delta) != blocks[identity]['content'] for identity, delta in rich['blocks'].items())):
            raise AuthorityUnavailable('copied rich sidecar differs from its descriptor')
        request = {'contract': CONTRACT, 'document_id': document.document_id,
                   'title': descriptor['title'], 'blocks': body, 'rich': rich}
        try:
            if not node:
                raise ValueError('missing pinned runtime')
            process = subprocess.run([node, '--max-old-space-size=256', str(Path(__file__).with_suffix('.cjs'))],
                input=_canonical_json(request).encode(), capture_output=True, timeout=15,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            response = json.loads(process.stdout) if process.returncode == 0 else {}
            payload = base64.b64decode(response['update'], validate=True)
            if response.get('contract') != CONTRACT or not 0 < len(payload) <= MAX_SEED_BYTES:
                raise ValueError('invalid copied seed')
        except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired) as exc:
            raise AuthorityUnavailable('copied rich generation could not be prepared') from exc
        decoded = search_projection.materialize([[payload]], rich=True)[0]
        expected = {'title': rich.get('title', [{'insert': descriptor['title']}] if descriptor['title'] else []),
                    'blocks': {block['id']: rich['blocks'].get(block['id'],
                        [{'insert': block['content']}] if block['content'] else []) for block in descriptor['blocks']}}
        if (decoded['title'] != descriptor['title'] or decoded['blocks'] != body
                or decoded['rich_text'] != expected):
            raise AuthorityUnavailable('copied rich generation changed content or formatting')
        total += len(payload)
        if total > MAX_SOURCE_BYTES:
            raise AuthorityUnavailable('copied rich generation exceeds the preparation budget')
        seeds[document.document_id] = payload
    return seeds


def insert(con, plan, document, payload, workspace, now):
    """Persist one initial generation frame in the owning copy transaction."""
    if not con.in_transaction:
        raise ValueError('copy seed insertion requires its transaction')
    actor, ws, identity = plan.source.actor, plan.source.workspace_id, document.document_id
    operation = _stable_id('pcseed', plan.operation_id, identity)
    update = _stable_id('cupd', actor, identity, operation)
    con.execute('INSERT INTO workspace_collaboration_crdt_updates('
        'id,workspace_id,document_id,update_sequence,document_sequence,actor_subject_id,client_id,'
        'session_binding_sha256,operation_id,client_sequence,payload_encoding,payload_bytes,payload_sha256,payload,'
        'workspace_epoch,document_epoch,runtime_binding_sha256,created_at) VALUES(?,?,?,1,1,?,?,?, ?,1,?,?,?,?,?,1,?,?)',
        (update, ws, identity, actor, 'page-copy-service', _sha256(CONTRACT + ws), operation,
         YJS_UPDATE_ENCODING, len(payload), _sha256(payload), payload, workspace['security_epoch'], runtime_binding_sha256(), now))
    con.execute("INSERT INTO workspace_collaboration_crdt_update_bindings(update_id,document_id,generation,rich_text_scope,created_at) VALUES(?,?,1,'document_rich_text_leaves',?)",
                (update, identity, now))
