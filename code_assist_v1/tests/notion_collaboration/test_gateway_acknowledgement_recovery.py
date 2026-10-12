from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
import json
import sqlite3

import pytest

from code_assist_v1.notion_collaboration.gateway_service import RuntimeGatewayASGI
from code_assist_v1.notion_collaboration.protocol_contract import AcknowledgementConflict, OperationConflict


def run_session(failure):
    @dataclass
    class Connection:
        connection_id: str = 'test-connection'

    class Gateway:
        def connect(self, *args, **kwargs):
            return Connection()

        def submit_payload(self, *args, **kwargs):
            raise failure

        def drain(self, *args, **kwargs):
            return []

        def disconnect(self, connection_id):
            assert connection_id == 'test-connection'

    submit = {'op': 'submit', 'operation_id': 'test-operation', 'client_sequence': 2,
              'acknowledgement_sequence': 3, 'payload_base64': base64.b64encode(b'update').decode()}
    incoming = iter([{'type': 'websocket.connect'},
                     *({'type': 'websocket.receive', 'text': json.dumps(value)} for value in
                       [{'op': 'connect'}, submit, {'op': 'drain', 'max_frames': 1}]),
                     {'type': 'websocket.disconnect'}])
    sent = []

    async def receive():
        return next(incoming)

    async def send(value):
        sent.append(value)

    app = RuntimeGatewayASGI(Gateway(), compactor_handshake_matches=True)
    asyncio.run(app({'type': 'websocket', 'path': '/realtime', 'headers': [(b'origin', b'http://localhost')]}, receive, send))
    return sent, [json.loads(value['text']) for value in sent if value['type'] == 'websocket.send']


def test_ui_outbox_ack_definite_rejection_identifies_exact_request_and_keeps_replay_available():
    sent, messages = run_session(AcknowledgementConflict('acknowledgement is outside durable bounds'))
    assert messages[1] == {'type': 'submit_rejected', 'error': 'ACKNOWLEDGEMENT_CONFLICT',
                           'operation_id': 'test-operation', 'client_sequence': 2, 'acknowledgement_sequence': 3}
    assert messages[2] == {'type': 'frames', 'frames': []}
    assert not any(value['type'] == 'websocket.close' for value in sent)


def test_ui_outbox_ack_uncertain_or_changed_operation_never_grants_rebase_permission():
    for error in [OperationConflict('changed request'), RuntimeError('after commit, before acknowledgement')]:
        sent, messages = run_session(error)
        assert messages[1] == {'type': 'error', 'error': 'PROTOCOL_REJECTED'}
        assert sent[-1]['type'] == 'websocket.close'


def test_ui_outbox_ack_durable_rejection_rolls_back_and_exact_lost_ack_retry_stays_idempotent(tmp_path):
    from code_assist_v1.notion_collaboration.canonical_authority import OperationConflict as DurableOperationConflict
    from code_assist_v1.tests.notion_collaboration.test_runtime_gateway import _connect, _environment

    gateway, transport, _root, catalog, workspace = _environment(tmp_path)
    writer = _connect(gateway, transport, workspace, client_id='ack-writer', binding_secret='ack-session', now_ms=1000)

    def submit(operation, sequence, ack):
        return gateway.submit_payload(writer.connection_id, operation_id=operation,
                                      client_sequence=sequence, acknowledgement_sequence=ack, payload=b'opaque-yjs-update')

    def dump():
        con = sqlite3.connect(f'{catalog.as_uri()}?mode=ro', uri=True)
        try:
            return tuple(con.iterdump())
        finally:
            con.close()

    first = submit('op-ack-recovery-0001', 1, 0)
    second = submit('op-ack-recovery-0002', 2, first.acknowledgement.document_sequence)
    before = dump()
    with pytest.raises(AcknowledgementConflict):
        submit('op-ack-recovery-0003', 3, 0)
    assert dump() == before
    assert gateway.session_status(writer.connection_id)['active'] is True
    assert submit('op-ack-recovery-0001', 1, 0).acknowledgement == first.acknowledgement
    assert dump() == before
    corrected = submit('op-ack-recovery-0003', 3, second.acknowledgement.document_sequence)
    assert corrected.acknowledgement.document_sequence == second.acknowledgement.document_sequence + 1
    after = dump()
    with pytest.raises(DurableOperationConflict):
        submit('op-ack-recovery-0003', 3, corrected.acknowledgement.document_sequence)
    assert dump() == after
    gateway.stop()
