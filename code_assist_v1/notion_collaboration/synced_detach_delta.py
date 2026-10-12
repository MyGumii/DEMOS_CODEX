"""Prepare and revalidate a durable Yjs body update before synced detach writes.

Node replays the captured destination and prepares bounded update frames outside
SQLite transactions. The future writer owns atomic persistence and receipts.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
import shutil
import subprocess

from . import synced_blocks as synced, synced_detach_body as body
from .canonical_authority import AuthorityUnavailable, _canonical_json, _sha256
from .page_copy_source import MAX_SOURCE_BYTES
from .title_projection import _frames, _generation

CONTRACT = 'code_assist.notion.synced_detach_delta.v1'
MAX_UPDATE_BYTES = 1_048_576


def _frames_digest(frames):
    digest = hashlib.sha256()
    for frame in frames:
        digest.update(len(frame).to_bytes(8, 'big'))
        digest.update(frame)
    return 'sha256:' + digest.hexdigest()


@dataclass(frozen=True)
class PreparedDetachedDelta:
    body: body.PreparedDetachedBody
    generation: int
    source_frames_sha256: str
    update_frames: tuple[bytes, ...]
    payload_json: str
    payload_sha256: str

    def value(self):
        return json.loads(self.payload_json)


def _validate_delta_response(response, prepared, generation):
    destination = prepared.placement.content.resources.detach
    if (not isinstance(response, dict) or response.get('contract') != CONTRACT
            or response.get('document_id') != destination.document_id
            or response.get('generation') != generation
            or not isinstance(response.get('updates'), list)
            or not response['updates'] or len(response['updates']) > 100000
            or not all(isinstance(response.get(k), str) and len(response[k]) == 64
                       for k in ('before_sha256', 'after_sha256'))):
        raise AuthorityUnavailable('detached Yjs preparation returned invalid metadata')
    updates = []
    for encoded in response['updates']:
        if not isinstance(encoded, str):
            raise AuthorityUnavailable('detached Yjs update is invalid')
        try:
            frame = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise AuthorityUnavailable('detached Yjs update is invalid') from exc
        if not 0 < len(frame) <= MAX_UPDATE_BYTES:
            raise AuthorityUnavailable('detached Yjs update exceeds durable frame limits')
        updates.append(frame)
    source = next(s for s in destination.sources if s.document_id == destination.document_id)
    before = json.loads(source.descriptor_json)['blocks']
    after = prepared.value()['blocks']
    old = {block['id'] for block in before}
    new = {block['id'] for block in after}
    if (response.get('source_update_count') != len(source.frames)
            or response.get('added_ids') != [block['id'] for block in after if block['id'] not in old]
            or response.get('removed_ids') != [block['id'] for block in before if block['id'] not in new]):
        raise AuthorityUnavailable('detached Yjs preparation returned inconsistent identities')
    return tuple(updates)


def prepare_detached_delta(authority, prepared):
    if _sha256(prepared.payload_json) != prepared.payload_sha256:
        raise ValueError('detached body was modified')
    if body.materialize_detached_body(prepared.placement) != prepared:
        raise ValueError('detached body no longer matches its placement')
    detach = prepared.placement.content.resources.detach
    captured = next(s for s in detach.sources if s.document_id == detach.document_id)
    generation = captured.stamp[-1]
    seed = json.loads(captured.descriptor_json)
    if _sha256(_canonical_json(seed['blocks'])) != prepared.value()['destination_body_sha256']:
        raise ValueError('detached destination body was modified')
    with authority._reader() as con:
        con.execute('BEGIN')
        body.revalidate_detached_body(authority, con, prepared)
        if _generation(con, detach.document_id) != generation:
            raise synced.SyncedSourceChanged('detached destination generation changed')
        if _frames_digest(_frames(con, detach.document_id, generation)) != _frames_digest(captured.frames):
            raise synced.SyncedSourceChanged('detached destination frames changed')
    if sum(map(len, captured.frames)) > MAX_SOURCE_BYTES:
        raise synced.SyncedShapeUnavailable('detached destination replay exceeds preparation budget')
    node = shutil.which('node')
    if not node:
        raise AuthorityUnavailable('detached collaborative preparation requires Node.js')
    request = {'contract': CONTRACT, 'document_id':detach.document_id, 'generation':generation,
               'frames':[base64.b64encode(frame).decode('ascii') for frame in captured.frames],
               'seed':{'title':seed['title'], 'blocks':seed['blocks']},
               'replacement':prepared.value()['blocks']}
    try:
        process = subprocess.run(
            [node, '--max-old-space-size=512', str(Path(__file__).with_suffix('.cjs'))],
            input=json.dumps(request, ensure_ascii=False).encode('utf-8'),
            capture_output=True, timeout=60,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
        )
        if process.returncode:
            raise ValueError('detached Yjs decoder rejected the captured body')
        response = json.loads(process.stdout)
        updates = _validate_delta_response(response, prepared, generation)
    except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired) as exc:
        raise AuthorityUnavailable('detached collaborative body could not be prepared') from exc
    if sum(map(len, updates)) > MAX_SOURCE_BYTES:
        raise synced.SyncedShapeUnavailable('detached updates exceed preparation budget')
    metadata = {'contract':CONTRACT, 'commit_ready':False,
                'document_id':detach.document_id, 'generation':generation,
                'operation_id':detach.operation_id, 'body_payload_sha256':prepared.payload_sha256,
                'destination_body_sha256':prepared.value()['destination_body_sha256'],
                'before_sha256':response['before_sha256'], 'after_sha256':response['after_sha256'],
                'source_frames_sha256':_frames_digest(captured.frames),
                'update_sha256':[_sha256(frame) for frame in updates],
                'update_bytes':[len(frame) for frame in updates],
                'added_ids':response['added_ids'], 'removed_ids':response['removed_ids'],
                'required_integrations':prepared.value()['required_integrations']}
    encoded = _canonical_json(metadata)
    result = PreparedDetachedDelta(prepared, generation, metadata['source_frames_sha256'],
                                   updates, encoded, _sha256(encoded))
    with authority._reader() as con:
        con.execute('BEGIN')
        revalidate_detached_delta(authority, con, result)
    return result


def revalidate_detached_delta(authority, con, prepared):
    if _sha256(prepared.payload_json) != prepared.payload_sha256:
        raise ValueError('detached Yjs preparation was modified')
    metadata = prepared.value()
    detach = prepared.body.placement.content.resources.detach
    if (metadata.get('contract') != CONTRACT or metadata.get('commit_ready') is not False
            or metadata.get('document_id') != detach.document_id
            or metadata.get('operation_id') != detach.operation_id
            or metadata.get('body_payload_sha256') != prepared.body.payload_sha256
            or metadata.get('destination_body_sha256') != prepared.body.value()['destination_body_sha256']
            or metadata.get('required_integrations') != prepared.body.value()['required_integrations']
            or metadata.get('source_frames_sha256') != prepared.source_frames_sha256
            or metadata.get('generation') != prepared.generation
            or metadata.get('update_sha256') != [_sha256(frame) for frame in prepared.update_frames]
            or metadata.get('update_bytes') != [len(frame) for frame in prepared.update_frames]
            or not prepared.update_frames
            or sum(map(len, prepared.update_frames)) > MAX_SOURCE_BYTES
            or any(not 0 < len(frame) <= MAX_UPDATE_BYTES for frame in prepared.update_frames)):
        raise ValueError('detached Yjs update was modified')
    body.revalidate_detached_body(authority, con, prepared.body)
    if _generation(con, detach.document_id) != prepared.generation:
        raise synced.SyncedSourceChanged('detached destination generation changed')
    if _frames_digest(_frames(con, detach.document_id, prepared.generation)) != prepared.source_frames_sha256:
        raise synced.SyncedSourceChanged('detached destination frames changed')
