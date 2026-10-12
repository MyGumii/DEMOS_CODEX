from __future__ import annotations

import base64
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

import pytest

from code_assist_v1 import notion_workspace_collaboration_migration as authority_migration
from code_assist_v1 import notion_workspace_realtime_transport_migration as transport_migration
from code_assist_v1 import notion_workspace_store as workspace_store
from code_assist_v1.notion_collaboration.canonical_authority import CanonicalAuthority
from code_assist_v1.notion_collaboration.cross_process_gateway import CrossProcessGatewayRuntime
from code_assist_v1.notion_collaboration.durable_transport import DurableTransportAuthority
from code_assist_v1.notion_collaboration.node_compactor import NodeCompactor
from code_assist_v1.notion_collaboration.protocol_contract import session_binding_sha256


def _backup(source_path: Path, destination_path: Path) -> Path:
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect(source_path)
    destination = sqlite3.connect(destination_path)
    try:
        source.backup(destination)
        destination.commit()
    finally:
        destination.close()
        source.close()
    return destination_path


def _environment(tmp_path: Path):
    data_root = tmp_path / "canonical-data"
    catalog = Path(workspace_store.catalog_path_for_user_data_root(data_root))
    store = workspace_store.WorkspaceStore(catalog)
    store.bootstrap()
    created = store.create_workspace("owner", name="Cross Process", operation_id="op-cross-workspace-create-0001")
    workspace_id = str(created["workspace"]["id"])
    store.set_member(
        "owner",
        workspace_id,
        "member",
        role="member",
        base_version=int(created["workspace"]["version"]),
        operation_id="op-cross-member-set-0001",
    )
    backup_2401 = _backup(catalog, data_root / "backups" / "before-2401.db")
    plan_2401 = authority_migration.build_plan(catalog, data_root)
    authority_migration.apply_plan(
        catalog,
        data_root,
        exact_plan_checksum=plan_2401["plan_checksum"],
        backup_confirmed=True,
        catalog_quiesced_confirmed=True,
        backup_path=backup_2401,
    )
    authority = CanonicalAuthority.open(catalog, data_root)
    authority.create_document(
        "owner",
        workspace_id,
        "page-cross-process",
        title="Cross Process",
        blocks=[{"id": "block-cross", "type": "text", "content": "base"}],
        expected_workspace_epoch=2,
        operation_id="op-cross-document-create-0001",
    )
    backup_2402 = _backup(catalog, data_root / "backups" / "before-2402.db")
    plan_2402 = transport_migration.build_plan(catalog, data_root)
    transport_migration.apply_plan(
        catalog,
        data_root,
        exact_plan_checksum=plan_2402["plan_checksum"],
        backup_confirmed=True,
        catalog_quiesced_confirmed=True,
        backup_path=backup_2402,
    )
    return DurableTransportAuthority.open(catalog, data_root), data_root, catalog, workspace_id


def _generate_yjs_updates(runtime: CrossProcessGatewayRuntime) -> tuple[bytes, bytes]:
    assert runtime.node_executable is not None and runtime.node_runtime is not None
    script = """
import * as Y from 'yjs';
const document = new Y.Doc({gc:false});
const updates = [];
document.on('update', update => updates.push(Buffer.from(update).toString('base64')));
const text = document.getText('body');
text.insert(0, 'hello');
text.insert(5, ' world');
process.stdout.write(JSON.stringify(updates));
"""
    result = subprocess.run(
        [str(runtime.node_executable), "--input-type=module", "-e", script],
        cwd=runtime.node_runtime,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="strict",
        shell=False,
        check=True,
        timeout=10,
    )
    encoded = json.loads(result.stdout)
    return tuple(base64.b64decode(item, validate=True) for item in encoded)  # type: ignore[return-value]


class _WebSocketClientProcess:
    _WORKER = r"""
import json,sys
sys.path.insert(0,sys.argv[1])
from websockets.sync.client import connect
socket_client=connect(sys.argv[2],origin=sys.argv[3],subprotocols=['code-assist-collaboration-v1'],open_timeout=5,close_timeout=2)
for line in sys.stdin:
    request=json.loads(line)
    if request.get('control') == 'close':
        socket_client.close()
        break
    socket_client.send(json.dumps(request['message'],separators=(',',':')))
    response=json.loads(socket_client.recv(timeout=5))
    print(json.dumps(response,separators=(',',':')),flush=True)
"""

    def __init__(self, runtime: CrossProcessGatewayRuntime) -> None:
        assert runtime.python_packages is not None
        base_executable = Path(getattr(sys, "_base_executable", sys.executable)).resolve(strict=True)
        self.process = subprocess.Popen(
            [
                str(base_executable),
                "-E",
                "-s",
                "-c",
                self._WORKER,
                str(runtime.python_packages),
                runtime.websocket_url,
                runtime.origin,
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="strict",
            shell=False,
            close_fds=True,
        )

    def request(self, message: dict) -> dict:
        assert self.process.stdin is not None and self.process.stdout is not None
        self.process.stdin.write(json.dumps({"message": message}, separators=(",", ":")) + "\n")
        self.process.stdin.flush()
        line = self.process.stdout.readline()
        if not line:
            diagnostic = ""
            if self.process.stderr is not None:
                diagnostic = self.process.stderr.read()[-2_000:]
            raise AssertionError(f"isolated websocket client exited without a response: {diagnostic}")
        return json.loads(line)

    def close(self) -> None:
        if self.process.poll() is None and self.process.stdin is not None:
            try:
                self.process.stdin.write('{"control":"close"}\n')
                self.process.stdin.flush()
                self.process.wait(timeout=5)
            except (BrokenPipeError, OSError, subprocess.TimeoutExpired):
                self.process.terminate()
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=5)
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            if stream is not None:
                stream.close()


def _ticket(
    transport: DurableTransportAuthority,
    runtime: CrossProcessGatewayRuntime,
    workspace_id: str,
    *,
    client_id: str,
    binding_secret: str,
):
    now_ms = int(time.time() * 1_000)
    binding = session_binding_sha256(binding_secret)
    issued = transport.issue_ticket(
        csrf_valid=True,
        subject_id="member",
        workspace_id=workspace_id,
        document_id="page-cross-process",
        client_id=client_id,
        session_binding_sha256=binding,
        origin=runtime.origin,
        capabilities=("read", "write"),
        now_ms=now_ms,
    )
    return issued, binding, now_ms


def _open_client(transport, runtime, workspace_id: str, *, client_id: str):
    issued, binding, _now = _ticket(
        transport,
        runtime,
        workspace_id,
        client_id=client_id,
        binding_secret=f"{client_id}-binding",
    )
    socket_client = _WebSocketClientProcess(runtime)
    connected = socket_client.request(
        {
            "op": "connect",
            "token": issued.token,
            "subject_id": "member",
            "workspace_id": workspace_id,
            "page_id": "page-cross-process",
            "client_id": client_id,
            "session_binding_sha256": binding,
        }
    )
    assert connected["type"] == "connected"
    return socket_client


def _send(client: _WebSocketClientProcess, value: dict) -> dict:
    return client.request(value)


@pytest.mark.integration
def test_t0b_p3_real_python_node_websocket_restart_order_backpressure_and_revoke(tmp_path: Path) -> None:
    transport, data_root, catalog, workspace_id = _environment(tmp_path)
    runtime = CrossProcessGatewayRuntime(
        tmp_path / "selected-runtime",
        allowed_parent=tmp_path,
        catalog_path=catalog,
        user_data_root=data_root,
        max_queue_frames=1,
        timeout_seconds=30,
    )
    clients: list[_WebSocketClientProcess] = []
    try:
        runtime.provision()
        updates = _generate_yjs_updates(runtime)
        first_descriptor = runtime.start()
        assert first_descriptor["gateway_pid"] != first_descriptor["compactor_pid"]
        assert first_descriptor["runtime_versions"] == {
            "node": "24.14.1",
            "uvicorn": "0.52.4",
            "websockets": "17.0.1",
            "yjs": "13.6.32",
        }
        assert runtime.python_packages is not None

        writer = _open_client(transport, runtime, workspace_id, client_id="writer")
        clients.append(writer)
        slow_reader = _open_client(transport, runtime, workspace_id, client_id="slow-reader")
        clients.append(slow_reader)
        first = _send(
            writer,
            {
                "op": "submit",
                "operation_id": "op-cross-update-0001",
                "client_sequence": 1,
                "acknowledgement_sequence": 0,
                "payload_base64": base64.b64encode(updates[0]).decode("ascii"),
            },
        )
        assert first["type"] == "ack" and first["acknowledgement"]["durable"] is True
        assert _send(writer, {"op": "drain", "max_frames": 1})["frames"][0]["payload_base64"] == base64.b64encode(updates[0]).decode("ascii")

        duplicate = _send(
            writer,
            {
                "op": "submit",
                "operation_id": "op-cross-update-0001",
                "client_sequence": 1,
                "acknowledgement_sequence": 0,
                "payload_base64": base64.b64encode(updates[0]).decode("ascii"),
            },
        )
        assert duplicate["acknowledgement"] == first["acknowledgement"]
        second = _send(
            writer,
            {
                "op": "submit",
                "operation_id": "op-cross-update-0002",
                "client_sequence": 2,
                "acknowledgement_sequence": first["acknowledgement"]["document_sequence"],
                "payload_base64": base64.b64encode(updates[1]).decode("ascii"),
            },
        )
        assert second["type"] == "ack" and second["broadcast"]["backpressured_sessions"] == 1
        slow_result = _send(slow_reader, {"op": "drain", "max_frames": 1})
        assert slow_result == {"error": "SESSION_CLOSED", "type": "error"}

        out_of_order = _open_client(transport, runtime, workspace_id, client_id="out-of-order")
        clients.append(out_of_order)
        rejected = _send(
            out_of_order,
            {
                "op": "submit",
                "operation_id": "op-cross-update-invalid-0001",
                "client_sequence": 2,
                "acknowledgement_sequence": 0,
                "payload_base64": base64.b64encode(updates[0]).decode("ascii"),
            },
        )
        assert rejected == {"error": "PROTOCOL_REJECTED", "type": "error"}
        writer.close()
        slow_reader.close()
        out_of_order.close()

        second_descriptor = runtime.restart()
        assert second_descriptor["gateway_pid"] != first_descriptor["gateway_pid"]
        assert second_descriptor["compactor_pid"] != first_descriptor["compactor_pid"]
        replay_client = _open_client(transport, runtime, workspace_id, client_id="replay")
        clients.append(replay_client)
        queued = _send(replay_client, {"op": "replay", "after_document_sequence": 0, "limit": 16})
        assert queued == {"error": "SESSION_CLOSED", "type": "error"}
        replay_client.close()

        issued, binding, now_ms = _ticket(
            transport,
            runtime,
            workspace_id,
            client_id="compactor-authority",
            binding_secret="compactor-authority-binding",
        )
        grant = transport.consume_ticket(
            issued.token,
            origin=runtime.origin,
            subject_id="member",
            workspace_id=workspace_id,
            document_id="page-cross-process",
            client_id="compactor-authority",
            session_binding_sha256=binding,
            now_ms=now_ms + 1,
        )
        work = transport.prepare_compaction(grant)
        assert runtime.node_executable is not None and runtime.node_runtime is not None
        with NodeCompactor(
            node_executable=runtime.node_executable,
            entrypoint=runtime.node_runtime / "compactor.mjs",
            runtime_root=runtime.node_runtime,
        ) as compactor:
            response, snapshot, state_vector = compactor.compact(
                request=work.request,
                base_snapshot=work.base_snapshot,
                ordered_updates=work.ordered_updates,
            )
        committed = transport.commit_compaction(
            grant,
            operation_id="op-cross-compact-0001",
            work=work,
            response=response,
            snapshot=snapshot,
            state_vector=state_vector,
        )
        assert committed["through_update_sequence"] == 2

        runtime.restart()
        revoke_client = _open_client(transport, runtime, workspace_id, client_id="revoke")
        clients.append(revoke_client)
        queued = _send(revoke_client, {"op": "replay", "after_document_sequence": 0, "limit": 16})
        assert queued["snapshot_enqueued"] is True and queued["event_frames_enqueued"] == 0
        canonical = transport.canonical_authority()
        canonical.revoke_subject(
            "owner",
            workspace_id,
            "member",
            expected_workspace_epoch=2,
            operation_id="op-cross-member-revoke-0001",
        )
        assert _send(revoke_client, {"op": "drain", "max_frames": 1}) == {"error": "PROTOCOL_REJECTED", "type": "error"}
        revoke_client.close()
    finally:
        for client in clients:
            client.close()
        cleanup = runtime.close()

    assert cleanup == {
        "owned_processes_stopped": True,
        "owned_ports_closed": True,
        "runtime_root_removed": True,
    }
    assert transport.counts()["workspace_collaboration_crdt_updates"] == 2
    assert not (tmp_path / "selected-runtime").exists()
