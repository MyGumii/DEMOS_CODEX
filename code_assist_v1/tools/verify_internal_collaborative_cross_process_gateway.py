"""Fail-closed verifier and disposable browser probe for T0B-P3."""

from __future__ import annotations

import argparse
import base64
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
from typing import Any, Sequence
from uuid import uuid4

from code_assist_v1 import notion_workspace_collaboration_migration as authority_migration
from code_assist_v1 import notion_workspace_realtime_transport_migration as transport_migration
from code_assist_v1 import notion_workspace_store as workspace_store
from code_assist_v1.notion_collaboration.canonical_authority import CanonicalAuthority
from code_assist_v1.notion_collaboration.cross_process_gateway import CrossProcessGatewayRuntime
from code_assist_v1.notion_collaboration.durable_transport import DurableTransportAuthority
from code_assist_v1.notion_collaboration.protocol_contract import session_binding_sha256


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_PARENT = PROJECT_ROOT.parent
DEFAULT_CONTRACT = (
    PROJECT_ROOT
    / "docs"
    / "notion_parity"
    / "core_free_web_ui"
    / "internal_collaborative_cross_process_gateway.v1.json"
)
CONTRACT_ID = "code_assist.notion.internal_collaborative_cross_process_gateway.v1"
EXPECTED_SOURCE_PATHS = (
    "code_assist_v1/collaboration_runtime/node/compactor.mjs",
    "code_assist_v1/notion_collaboration/node_compactor.py",
    "code_assist_v1/notion_collaboration/cross_process_gateway.py",
    "code_assist_v1/notion_collaboration/gateway_service.py",
)
EXPECTED_TEST_PATHS = (
    "code_assist_v1/tests/notion_collaboration/test_runtime_gateway_cross_process.py",
    "code_assist_v1/tests/notion_collaboration/browser/test_runtime_gateway_browser_transport.py",
)
EXPECTED_BROWSER_EVIDENCE_PATH = (
    "code_assist_v1/docs/notion_parity/evidence/internal_collaboration_browser/"
    "t0b-p3-manual-browser-20260824.json"
)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _repository_path(value: str) -> Path | None:
    candidate = (REPOSITORY_PARENT / value).resolve(strict=False)
    try:
        candidate.relative_to(REPOSITORY_PARENT.resolve(strict=True))
    except ValueError:
        return None
    return candidate


def _error(code: str, subject: str) -> dict[str, str]:
    return {"code": code, "subject": subject}


def verify(contract_path: Path = DEFAULT_CONTRACT) -> dict[str, Any]:
    errors: list[dict[str, str]] = []
    try:
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        contract = {}
        errors.append(_error("CONTRACT_READ_FAILED", str(contract_path)))
    if not isinstance(contract, dict) or contract.get("contract") != CONTRACT_ID:
        errors.append(_error("CONTRACT_ID_INVALID", "contract"))
    if (
        contract.get("packet_id") != "T0B-P3"
        or contract.get("status") != "gate_passed"
    ):
        errors.append(_error("PACKET_STATUS_INVALID", "packet_id/status"))

    runtime = contract.get("selected_runtime", {})
    expected_runtime = {
        "gateway": "CPython_3.14.3_uvicorn_0.52.4_websockets_17.0.1",
        "compactor": "Node_24.14.1_Yjs_13.6.32",
        "wire_format": "Yjs_v1_update_bytes",
    }
    if runtime != expected_runtime:
        errors.append(_error("SELECTED_RUNTIME_DRIFT", "selected_runtime"))
    topology = contract.get("runtime_topology", {})
    required_topology = {
        "gateway_process_count": 1,
        "compactor_process_count_per_gateway": 1,
        "compactor_opens_listener": False,
        "listener_host": "127.0.0.1",
        "listener_port_assignment": "OS_port_zero",
        "graceful_marker_bound_restart": True,
    }
    if topology != required_topology:
        errors.append(_error("TOPOLOGY_DRIFT", "runtime_topology"))
    safety = contract.get("test_boundary", {})
    if safety != {
        "temporary_dependency_install_only": True,
        "temporary_SQLite_only": True,
        "ephemeral_browser_tab_only": True,
        "signed_in_profile_data_read": False,
        "fixed_operator_port_accessed": False,
        "development_pair_changed": False,
        "production_changed": False,
    }:
        errors.append(_error("TEST_BOUNDARY_DRIFT", "test_boundary"))
    successor_boundary = contract.get("successor_extension_boundary", {})
    if successor_boundary != {
        "packet_id": "D0B-P1",
        "approval_id": "AP-DEV-PAIR",
        "fixed_gateway_port": 10113,
        "legacy_default_remains": "OS_port_zero",
        "unapproved_fixed_ports_rejected": True,
        "t0b_test_boundary_unchanged": True,
    }:
        errors.append(_error("SUCCESSOR_EXTENSION_BOUNDARY_DRIFT", "successor_extension_boundary"))

    for label, expected_paths in (
        ("source_manifest", EXPECTED_SOURCE_PATHS),
        ("test_manifest", EXPECTED_TEST_PATHS),
    ):
        rows = contract.get(label, [])
        observed_paths = tuple(
            row.get("path") for row in rows if isinstance(row, dict) and isinstance(row.get("path"), str)
        ) if isinstance(rows, list) else ()
        if observed_paths != expected_paths:
            errors.append(_error("MANIFEST_PATH_DRIFT", label))
            continue
        for row in rows:
            path = _repository_path(row["path"])
            if path is None or not path.is_file():
                errors.append(_error("MANIFEST_FILE_MISSING", row["path"]))
            elif row.get("sha256") != _sha256_file(path):
                errors.append(_error("MANIFEST_HASH_DRIFT", row["path"]))

    evidence_binding = contract.get("browser_evidence", {})
    evidence_path = _repository_path(evidence_binding.get("path", "")) if isinstance(evidence_binding, dict) else None
    if (
        not isinstance(evidence_binding, dict)
        or evidence_binding.get("path") != EXPECTED_BROWSER_EVIDENCE_PATH
        or evidence_path is None
        or not evidence_path.is_file()
        or evidence_binding.get("sha256") != _sha256_file(evidence_path)
    ):
        errors.append(_error("BROWSER_EVIDENCE_BINDING_INVALID", "browser_evidence"))
        browser_evidence: dict[str, Any] = {}
    else:
        try:
            loaded_evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
            loaded_evidence = {}
        browser_evidence = loaded_evidence if isinstance(loaded_evidence, dict) else {}
    browser_attestation = browser_evidence.get("browser_attestation", {})
    transport_attestation = browser_evidence.get("transport_attestation", {})
    cleanup_attestation = browser_evidence.get("cleanup_attestation", {})
    if (
        browser_evidence.get("contract") != "code_assist.notion.t0b_p3.manual_browser_evidence.v1"
        or browser_evidence.get("packet_id") != "T0B-P3"
        or browser_evidence.get("status") != "pass"
        or browser_attestation.get("actual_browser_user_agent_detected") is not True
        or browser_attestation.get("persistent_profile_data_read") is not False
        or browser_attestation.get("cookies_or_local_storage_read") is not False
        or browser_attestation.get("personal_content_read") is not False
        or transport_attestation.get("actual_browser_websocket_clients") != 2
        or transport_attestation.get("durable_acknowledgement") is not True
        or transport_attestation.get("reader_received_exact_submitted_payload") is not True
        or transport_attestation.get("durable_update_count") != 1
        or transport_attestation.get("fixed_operator_port_accessed") is not False
        or cleanup_attestation.get("owned_ports_closed") is not True
        or cleanup_attestation.get("owned_processes_stopped") is not True
        or cleanup_attestation.get("runtime_root_removed") is not True
        or cleanup_attestation.get("unrelated_processes_targeted") is not False
    ):
        errors.append(_error("BROWSER_EVIDENCE_CLAIM_INVALID", "browser_evidence"))

    verification = contract.get("verification", {})
    required = {
        "real_gateway_process": True,
        "real_node_yjs_process": True,
        "real_websocket_clients": 2,
        "real_browser_websocket": True,
        "duplicate_idempotency": True,
        "out_of_order_rejection": True,
        "backpressure_fence": True,
        "restart_replay": True,
        "yjs_compaction_snapshot": True,
        "revocation_old_epoch_delivery_bytes": 0,
        "owned_process_port_and_root_cleanup": True,
    }
    if verification != required:
        errors.append(_error("VERIFICATION_CLAIM_DRIFT", "verification"))
    claims = contract.get("claims", {})
    if claims != {
        "T0B_P3_gate_passed": True,
        "RT_001_closed": True,
        "runtime_or_feature_enabled": False,
        "development_pair_changed": False,
        "production_target_changed": False,
        "product_complete": False,
    }:
        errors.append(_error("PACKET_CLAIM_DRIFT", "claims"))
    errors = [
        {"code": code, "subject": subject}
        for code, subject in sorted({(item["code"], item["subject"]) for item in errors})
    ]
    return {
        "report_schema_version": 1,
        "contract": "code_assist.notion.t0b_p3.verification_report.v1",
        "status": "pass" if not errors else "fail",
        "error_count": len(errors),
        "errors": errors,
        "contract_sha256": _sha256_file(contract_path) if contract_path.is_file() else None,
        "source_count": len(EXPECTED_SOURCE_PATHS),
        "test_count": len(EXPECTED_TEST_PATHS),
        "gate_passed": contract.get("claims", {}).get("T0B_P3_gate_passed") is True,
    }


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


def _browser_environment(root: Path):
    data_root = root / "canonical-data"
    catalog = Path(workspace_store.catalog_path_for_user_data_root(data_root))
    store = workspace_store.WorkspaceStore(catalog)
    store.bootstrap()
    created = store.create_workspace("owner", name="Browser Probe", operation_id="op-browser-workspace-create-0001")
    workspace_id = str(created["workspace"]["id"])
    store.set_member(
        "owner",
        workspace_id,
        "member",
        role="member",
        base_version=int(created["workspace"]["version"]),
        operation_id="op-browser-member-set-0001",
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
    CanonicalAuthority.open(catalog, data_root).create_document(
        "owner",
        workspace_id,
        "page-browser-probe",
        title="Browser Probe",
        blocks=[{"id": "block-browser", "type": "text", "content": "base"}],
        expected_workspace_epoch=2,
        operation_id="op-browser-document-create-0001",
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


def _browser_ticket(
    transport: DurableTransportAuthority,
    runtime: CrossProcessGatewayRuntime,
    workspace_id: str,
    client_id: str,
    *,
    origin: str | None = None,
) -> dict[str, str]:
    now_ms = int(time.time() * 1_000)
    binding = session_binding_sha256(f"{client_id}-{uuid4().hex}")
    ticket = transport.issue_ticket(
        csrf_valid=True,
        subject_id="member",
        workspace_id=workspace_id,
        document_id="page-browser-probe",
        client_id=client_id,
        session_binding_sha256=binding,
        origin=runtime.origin if origin is None else origin,
        capabilities=("read", "write"),
        now_ms=now_ms,
        ttl_ms=30_000,
    )
    return {
        "op": "connect",
        "token": ticket.token,
        "subject_id": "member",
        "workspace_id": workspace_id,
        "page_id": "page-browser-probe",
        "client_id": client_id,
        "session_binding_sha256": binding,
    }


def _manual_probe_html(
    *,
    nonce: str,
    websocket_url: str,
    writer_connect: dict[str, str],
    reader_connect: dict[str, str],
) -> bytes:
    payload = base64.b64encode(b"browser-selected-runtime-update").decode("ascii")
    configuration = json.dumps(
        {
            "websocketUrl": websocket_url,
            "writerConnect": writer_connect,
            "readerConnect": reader_connect,
            "submit": {
                "op": "submit",
                "operation_id": "op-manual-browser-update-0001",
                "client_sequence": 1,
                "acknowledgement_sequence": 0,
                "payload_base64": payload,
            },
            "expectedPayload": payload,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).replace("<", "\\u003c")
    return f"""<!doctype html>
<meta charset="utf-8">
<title>Collaboration browser verification</title>
<main id="status" role="status">Running isolated collaboration verification…</main>
<script nonce="{nonce}">
const configuration = {configuration};
const statusNode = document.getElementById('status');
function receive(socket) {{
  return new Promise((resolve, reject) => {{
    const timer = setTimeout(() => reject(new Error('browser_receive_timeout')), 5000);
    socket.addEventListener('message', event => {{
      clearTimeout(timer);
      resolve(JSON.parse(event.data));
    }}, {{once:true}});
    socket.addEventListener('error', () => {{
      clearTimeout(timer);
      reject(new Error('browser_websocket_error'));
    }}, {{once:true}});
  }});
}}
async function connect(envelope) {{
  const socket = new WebSocket(configuration.websocketUrl, 'code-assist-collaboration-v1');
  await new Promise((resolve, reject) => {{
    const timer = setTimeout(() => reject(new Error('browser_open_timeout')), 5000);
    socket.addEventListener('open', () => {{ clearTimeout(timer); resolve(); }}, {{once:true}});
    socket.addEventListener('error', () => {{ clearTimeout(timer); reject(new Error('browser_open_error')); }}, {{once:true}});
  }});
  const pending = receive(socket);
  socket.send(JSON.stringify(envelope));
  const connected = await pending;
  if (connected.type !== 'connected') throw new Error('browser_connect_rejected');
  return socket;
}}
async function report(value) {{
  await fetch('/result', {{method:'POST', cache:'no-store', headers:{{'content-type':'application/json'}}, body:JSON.stringify(value)}});
}}
(async () => {{
  let writer;
  let reader;
  try {{
    reader = await connect(configuration.readerConnect);
    writer = await connect(configuration.writerConnect);
    const ackPending = receive(writer);
    writer.send(JSON.stringify(configuration.submit));
    const ack = await ackPending;
    if (ack.type !== 'ack' || ack.acknowledgement?.durable !== true) throw new Error('browser_ack_invalid');
    const framesPending = receive(reader);
    reader.send(JSON.stringify({{op:'drain', max_frames:8}}));
    const frames = await framesPending;
    if (frames.type !== 'frames' || frames.frames?.length !== 1 || frames.frames[0].payload_base64 !== configuration.expectedPayload) {{
      throw new Error('browser_delivery_invalid');
    }}
    await report({{status:'pass', durable:true, connected_clients:2, delivered_payload_base64:frames.frames[0].payload_base64}});
    statusNode.textContent = 'PASS — isolated browser collaboration verified. This tab can be closed.';
  }} catch (error) {{
    await report({{status:'fail', error:'browser_verification_failed'}}).catch(() => {{}});
    statusNode.textContent = 'FAIL — return to the Codex chat for details.';
  }} finally {{
    writer?.close();
    reader?.close();
  }}
}})();
</script>""".encode("utf-8")


def run_manual_browser_probe() -> int:
    probe_id = uuid4().hex
    with tempfile.TemporaryDirectory(prefix="code-assist-t0b-p3-manual-browser-") as temporary:
        root = Path(temporary).resolve(strict=True)
        transport, data_root, catalog, workspace_id = _browser_environment(root)
        runtime = CrossProcessGatewayRuntime(
            root / "selected-runtime",
            allowed_parent=root,
            catalog_path=catalog,
            user_data_root=data_root,
            timeout_seconds=30,
        )
        state: dict[str, Any] = {
            "issued": False,
            "result": None,
            "user_agent_sha256": None,
            "browser_user_agent_detected": False,
        }
        result_event = threading.Event()
        server: ThreadingHTTPServer | None = None
        try:
            runtime.start()

            class ManualProbeHandler(BaseHTTPRequestHandler):
                server_version = "CodeAssistManualBrowserProbe/1"
                sys_version = ""

                def _headers(self, status: int, *, content_type: str, length: int, csp: str | None = None) -> None:
                    self.send_response(status)
                    self.send_header("Content-Type", content_type)
                    self.send_header("Content-Length", str(length))
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("X-Content-Type-Options", "nosniff")
                    if csp is not None:
                        self.send_header("Content-Security-Policy", csp)
                    self.end_headers()

                def do_GET(self) -> None:  # noqa: N802 - stdlib callback name
                    if self.path != "/" or state["issued"]:
                        body = b"probe unavailable"
                        self._headers(410, content_type="text/plain; charset=utf-8", length=len(body))
                        self.wfile.write(body)
                        return
                    assert server is not None
                    origin = f"http://127.0.0.1:{server.server_port}"
                    nonce = uuid4().hex
                    state["issued"] = True
                    user_agent = self.headers.get("User-Agent", "")
                    state["user_agent_sha256"] = "sha256:" + hashlib.sha256(user_agent.encode("utf-8")).hexdigest()
                    state["browser_user_agent_detected"] = user_agent.startswith("Mozilla/") and any(
                        marker in user_agent for marker in ("Chrome/", "Chromium/", "Firefox/", "Safari/")
                    )
                    body = _manual_probe_html(
                        nonce=nonce,
                        websocket_url=runtime.websocket_url,
                        writer_connect=_browser_ticket(
                            transport, runtime, workspace_id, "manual-browser-writer", origin=origin
                        ),
                        reader_connect=_browser_ticket(
                            transport, runtime, workspace_id, "manual-browser-reader", origin=origin
                        ),
                    )
                    csp = (
                        f"default-src 'none'; script-src 'nonce-{nonce}'; "
                        f"connect-src 'self' {runtime.websocket_url}; style-src 'none'; img-src 'none'"
                    )
                    self._headers(
                        200,
                        content_type="text/html; charset=utf-8",
                        length=len(body),
                        csp=csp,
                    )
                    self.wfile.write(body)

                def do_POST(self) -> None:  # noqa: N802 - stdlib callback name
                    if self.path != "/result":
                        self.send_error(404)
                        return
                    try:
                        size = int(self.headers.get("Content-Length", "0"))
                        if size < 2 or size > 4096:
                            raise ValueError
                        document = json.loads(self.rfile.read(size))
                    except (TypeError, ValueError, json.JSONDecodeError):
                        self.send_error(400)
                        return
                    state["result"] = document
                    result_event.set()
                    body = b"accepted"
                    self._headers(200, content_type="text/plain; charset=utf-8", length=len(body))
                    self.wfile.write(body)

                def log_message(self, _format: str, *_args: object) -> None:
                    return

            server = ThreadingHTTPServer(("127.0.0.1", 0), ManualProbeHandler)
            thread = threading.Thread(target=server.serve_forever, name="manual-browser-probe", daemon=True)
            thread.start()
            print(
                json.dumps(
                    {
                        "contract": "code_assist.notion.t0b_p3.manual_browser_probe.v1",
                        "probe_id": probe_id,
                        "page_url": f"http://127.0.0.1:{server.server_port}/",
                        "status": "waiting_for_browser",
                        "ticket_issued": False,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                flush=True,
            )
            result_event.wait(timeout=600)
            server.shutdown()
            thread.join(timeout=10)
            update_count = transport.counts()["workspace_collaboration_crdt_updates"]
            result = state["result"]
            passed = (
                state["issued"] is True
                and state["browser_user_agent_detected"] is True
                and isinstance(result, dict)
                and result == {
                    "status": "pass",
                    "durable": True,
                    "connected_clients": 2,
                    "delivered_payload_base64": base64.b64encode(
                        b"browser-selected-runtime-update"
                    ).decode("ascii"),
                }
                and update_count == 1
            )
        finally:
            if server is not None:
                server.server_close()
            cleanup = runtime.close()
        print(
            json.dumps(
                {
                    "contract": "code_assist.notion.t0b_p3.manual_browser_probe_result.v1",
                    "probe_id": probe_id,
                    "status": "pass" if passed else "fail",
                    "actual_browser_user_agent_detected": state["browser_user_agent_detected"],
                    "user_agent_sha256": state["user_agent_sha256"],
                    "durable_update_count": update_count,
                    "cleanup": cleanup,
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
            flush=True,
        )
    return 0 if passed and all(cleanup.values()) else 1


def run_browser_probe() -> int:
    probe_id = uuid4().hex
    with tempfile.TemporaryDirectory(prefix="code-assist-t0b-p3-browser-") as temporary:
        root = Path(temporary).resolve(strict=True)
        transport, data_root, catalog, workspace_id = _browser_environment(root)
        runtime = CrossProcessGatewayRuntime(
            root / "selected-runtime",
            allowed_parent=root,
            catalog_path=catalog,
            user_data_root=data_root,
            timeout_seconds=30,
        )
        try:
            runtime.start()
            document = {
                "contract": "code_assist.notion.t0b_p3.browser_probe.v1",
                "probe_id": probe_id,
                "page_url": f"{runtime.origin}/test-client",
                "websocket_url": runtime.websocket_url,
                "forbidden_operator_port_accessed": False,
                "next_control": f"ISSUE {probe_id}",
            }
            print(json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")), flush=True)
            issue_control = input().strip()
            if issue_control != f"ISSUE {probe_id}":
                passed = False
            else:
                credentials = {
                    "contract": "code_assist.notion.t0b_p3.browser_probe_credentials.v1",
                    "probe_id": probe_id,
                    "writer_connect": _browser_ticket(transport, runtime, workspace_id, "browser-writer"),
                    "reader_connect": _browser_ticket(transport, runtime, workspace_id, "browser-reader"),
                    "submit": {
                        "op": "submit",
                        "operation_id": "op-browser-update-0001",
                        "client_sequence": 1,
                        "acknowledgement_sequence": 0,
                        "payload_base64": base64.b64encode(b"browser-selected-runtime-update").decode("ascii"),
                    },
                    "expected_payload_base64": base64.b64encode(b"browser-selected-runtime-update").decode("ascii"),
                }
                print(json.dumps(credentials, sort_keys=True, separators=(",", ":")), flush=True)
                response = input().strip()
                passed = response == f"PASS {probe_id}"
        finally:
            cleanup = runtime.close()
        print(
            json.dumps(
                {
                    "contract": "code_assist.notion.t0b_p3.browser_probe_result.v1",
                    "probe_id": probe_id,
                    "status": "pass" if passed else "fail",
                    "cleanup": cleanup,
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
            flush=True,
        )
    return 0 if passed and all(cleanup.values()) else 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--browser-probe", action="store_true")
    parser.add_argument("--manual-browser-probe", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.browser_probe:
        return run_browser_probe()
    if args.manual_browser_probe:
        return run_manual_browser_probe()
    report = verify(args.contract.resolve(strict=False))
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
