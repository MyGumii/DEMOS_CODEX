"""UI-NATIVE-LIVE: an already connected editor can replay service-created links."""
from code_assist_v1.tests.notion_collaboration.test_member_navigation import (
    environment, native_environment, archive_environment, guest_environment, image_environment, create,
)
from code_assist_v1.notion_collaboration.editing_authority import CollaborativeEditingTransportAuthority
from code_assist_v1.notion_collaboration.protocol_contract import session_binding_sha256
from code_assist_v1.notion_collaboration.runtime_gateway import RuntimeGateway

def test_ui_native_live_replay_service_links_have_safe_page_cursor(environment):
    store, ws = environment
    transport = CollaborativeEditingTransportAuthority.open(store.db_path, store.user_data_root)
    gateway = RuntimeGateway(transport=transport, aggregate_enabled=True, realtime_enabled=True)
    gateway.start(compactor_handshake_matches=True)
    binding = session_binding_sha256('native-service-replay-session')
    origin = 'http://127.0.0.1:49321'
    ticket = transport.issue_ticket(csrf_valid=True, subject_id='owner', workspace_id=ws,
        document_id='page-e1', client_id='native-service-reader', session_binding_sha256=binding,
        origin=origin, capabilities=('read','write'), now_ms=1000)
    session = gateway.connect(ticket.token, origin=origin, subject_id='owner', workspace_id=ws,
        page_id='page-e1', client_id='native-service-reader', session_binding_sha256=binding, now_ms=1001)
    try:
        before = store.iam.read_document('owner','page-e1')['document_sequence']
        create(store,ws,operation='native-live-first-child')
        create(store,ws,operation='native-live-second-child')
        # HTTP service writes have no browser submit broadcast. A live-domain
        # refresh alone therefore cannot deliver the CRDT body changes.
        assert gateway.drain(session.connection_id,max_frames=100) == ()
        first = gateway.replay_to_queue(session.connection_id,after_document_sequence=before,limit=1)
        frames = gateway.drain(session.connection_id,max_frames=100)
        assert first.has_more and len(frames) == 1
        assert first.next_document_sequence == frames[0].document_sequence < first.high_water_sequence
        tail = gateway.replay_to_queue(session.connection_id,after_document_sequence=first.next_document_sequence,limit=100)
        later = gateway.drain(session.connection_id,max_frames=100)
        assert not tail.has_more and tail.next_document_sequence == tail.high_water_sequence
        assert len(later) == 1 and later[0].document_sequence > frames[0].document_sequence
        assert all(frame.kind == 'crdt.update' for frame in (*frames,*later))
        empty = gateway.replay_to_queue(session.connection_id,after_document_sequence=tail.next_document_sequence,limit=100)
        assert not empty.has_more and empty.event_frames_enqueued == 0
        assert empty.next_document_sequence == tail.next_document_sequence
    finally:
        gateway.stop()
