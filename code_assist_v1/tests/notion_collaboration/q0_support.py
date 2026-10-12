"""Runner-owned synthetic fixtures shared by the six Q0 acceptance modules."""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import hashlib
from pathlib import Path
import sqlite3
from typing import Any, Callable, Iterable

from code_assist_v1 import notion_workspace_collaboration_migration as m2401
from code_assist_v1 import notion_workspace_realtime_transport_migration as m2402
from code_assist_v1 import notion_workspace_iam_migration as m2403
from code_assist_v1 import notion_workspace_live_collaboration_migration as m2404
from code_assist_v1 import notion_workspace_editing_authority_migration as m2405
from code_assist_v1 import notion_workspace_second_brain_publication_migration as m2406
from code_assist_v1 import notion_workspace_database_features_migration as m2407
from code_assist_v1 import notion_workspace_file_authority_migration as m2408
from code_assist_v1 import notion_workspace_store as workspace_store
from code_assist_v1.notion_collaboration.canonical_authority import CanonicalAuthority
from code_assist_v1.notion_collaboration.file_authority import CollaborativeFileAuthority
from code_assist_v1.notion_collaboration.iam_authority import (
    CollaborativeIAMAuthority,
    InMemoryIdentityDirectory,
)
from code_assist_v1.notion_collaboration.iam_runtime import (
    CollaborativeDurableTransportAuthority,
)
from code_assist_v1.notion_collaboration.protocol_contract import session_binding_sha256
from code_assist_v1.notion_collaboration.runtime_gateway import (
    GatewaySessionDescriptor,
    RuntimeGateway,
)


ORIGIN = "http://127.0.0.1:49400"
PAGE_ID = "page-q0-load"


def _backup(source: Path, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_connection = sqlite3.connect(source)
    destination_connection = sqlite3.connect(destination)
    try:
        source_connection.backup(destination_connection)
        destination_connection.commit()
    finally:
        destination_connection.close()
        source_connection.close()
    return destination


def _apply(migration, catalog: Path, root: Path) -> None:
    version = int(migration.TARGET_VERSION)
    backup = _backup(catalog, root / "backups" / f"before-{version}.db")
    plan = migration.build_plan(catalog, root)
    migration.apply_plan(
        catalog,
        root,
        exact_plan_checksum=plan["plan_checksum"],
        backup_confirmed=True,
        catalog_quiesced_confirmed=True,
        backup_path=backup,
    )


@dataclass(slots=True)
class Q0Environment:
    root: Path
    catalog: Path
    workspace_id: str
    subjects: tuple[str, ...]
    identities: InMemoryIdentityDirectory
    transport: CollaborativeDurableTransportAuthority
    gateway: RuntimeGateway
    iam: Any
    files: CollaborativeFileAuthority | None

    def issue_ticket(
        self,
        subject: str,
        *,
        client_id: str,
        binding_secret: str,
        now_ms: int,
        capabilities: Iterable[str] = ("read", "write", "presence"),
    ):
        binding = session_binding_sha256(binding_secret)
        ticket = self.transport.issue_ticket(
            csrf_valid=True,
            subject_id=subject,
            workspace_id=self.workspace_id,
            document_id=PAGE_ID,
            client_id=client_id,
            session_binding_sha256=binding,
            origin=ORIGIN,
            capabilities=tuple(capabilities),
            now_ms=now_ms,
        )
        return ticket, binding

    def connect(
        self,
        subject: str,
        *,
        client_id: str,
        binding_secret: str,
        now_ms: int,
        capabilities: Iterable[str] = ("read", "write", "presence"),
    ) -> GatewaySessionDescriptor:
        ticket, binding = self.issue_ticket(
            subject,
            client_id=client_id,
            binding_secret=binding_secret,
            now_ms=now_ms,
            capabilities=capabilities,
        )
        return self.gateway.connect(
            ticket.token,
            origin=ORIGIN,
            subject_id=subject,
            workspace_id=self.workspace_id,
            page_id=PAGE_ID,
            client_id=client_id,
            session_binding_sha256=binding,
            now_ms=now_ms + 1,
        )


def build_q0_environment(
    root: Path,
    *,
    registered_users: int = 3,
    max_queue_frames: int = 4_096,
    max_queue_bytes: int = 32 * 1024 * 1024,
    max_connections: int = 1_024,
    reconnect_attempts: int = 5,
    through_file_authority: bool = False,
) -> Q0Environment:
    if registered_users < 2:
        raise ValueError("Q0 fixture requires owner plus at least one member")
    root = Path(root)
    catalog = Path(workspace_store.catalog_path_for_user_data_root(root))
    store = workspace_store.WorkspaceStore(catalog)
    store.bootstrap()
    created = store.create_workspace(
        "owner",
        name="Q0 synthetic load workspace",
        operation_id="op-q0-workspace-create-0001",
    )
    workspace_id = str(created["workspace"]["id"])
    version = int(created["workspace"]["version"])
    members = tuple(f"user-{index:03d}" for index in range(1, registered_users))
    for index, subject in enumerate(members, start=1):
        result = store.set_member(
            "owner",
            workspace_id,
            subject,
            role="member",
            base_version=version,
            operation_id=f"op-q0-member-set-{index:04d}",
        )
        version = int(result["workspace"]["version"])

    _apply(m2401, catalog, root)
    con = sqlite3.connect(catalog)
    try:
        workspace_epoch = int(
            con.execute(
                "SELECT security_epoch FROM workspaces WHERE id=?", (workspace_id,)
            ).fetchone()[0]
        )
    finally:
        con.close()
    canonical = CanonicalAuthority.open(catalog, root)
    canonical.create_document(
        "owner",
        workspace_id,
        PAGE_ID,
        title="Q0 synthetic load page",
        blocks=[{"id": "block-q0", "type": "text", "content": "base"}],
        expected_workspace_epoch=workspace_epoch,
        operation_id="op-q0-document-create-0001",
    )
    document_epoch = 1
    for index, subject in enumerate(members, start=1):
        grant = canonical.grant_page_access(
            "owner",
            PAGE_ID,
            subject,
            "edit",
            expected_workspace_epoch=workspace_epoch,
            expected_document_epoch=document_epoch,
            operation_id=f"op-q0-page-grant-{index:04d}",
        )
        document_epoch = int(grant["document_epoch"])
    for migration in (m2402, m2403):
        _apply(migration, catalog, root)
    if through_file_authority:
        for migration in (m2404, m2405, m2406, m2407, m2408):
            _apply(migration, catalog, root)

    subjects = ("owner", *members)
    identities = InMemoryIdentityDirectory((*subjects, "outsider"))
    transport = CollaborativeDurableTransportAuthority.open(catalog, root)
    gateway = RuntimeGateway(
        transport=transport,
        aggregate_enabled=True,
        realtime_enabled=True,
        max_queue_frames=max_queue_frames,
        max_queue_bytes=max_queue_bytes,
        max_connections=max_connections,
        reconnect_attempts=reconnect_attempts,
    )
    health = gateway.start(compactor_handshake_matches=True)
    assert health["ready_for_admission"] is True
    files = (
        CollaborativeFileAuthority.open(
            catalog,
            root,
            identities,
            initialize_storage=True,
        )
        if through_file_authority
        else None
    )
    iam = files if files is not None else CollaborativeIAMAuthority.open(
        catalog, root, identities
    )
    return Q0Environment(
        root,
        catalog,
        workspace_id,
        subjects,
        identities,
        transport,
        gateway,
        iam,
        files,
    )


def upload_one(
    authority: CollaborativeFileAuthority,
    *,
    actor: str,
    payload: bytes,
    operation_suffix: str,
    before_finalize: Callable[[], None] | None = None,
) -> dict:
    page = authority.read_document(actor, PAGE_ID)
    digest = "sha256:" + hashlib.sha256(payload).hexdigest()
    session = authority.begin_upload(
        actor,
        PAGE_ID,
        original_name=f"q0-{operation_suffix}.bin",
        mime_type="application/octet-stream",
        total_size=len(payload),
        expected_sha256=digest,
        expected_document_sequence=int(page["document_sequence"]),
        expected_workspace_epoch=int(page["workspace_epoch"]),
        expected_document_epoch=int(page["document_epoch"]),
        operation_id=f"op-q0-upload-begin-{operation_suffix}",
        chunk_size=65_536,
    )
    chunk_size = int(session["chunk_size"])
    for index in range(int(session["chunk_count"])):
        chunk = payload[index * chunk_size : (index + 1) * chunk_size]
        authority.stage_chunk(
            actor,
            str(session["session_id"]),
            chunk_index=index,
            stream=BytesIO(chunk),
            operation_id=f"op-q0-upload-chunk-{operation_suffix}-{index:04d}",
        )
    if before_finalize is not None:
        before_finalize()
    return authority.finalize_upload(
        actor,
        str(session["session_id"]),
        operation_id=f"op-q0-upload-finalize-{operation_suffix}",
    )


def table_count(catalog: Path, table: str) -> int:
    con = sqlite3.connect(catalog)
    try:
        return int(con.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
    finally:
        con.close()


__all__ = [
    "ORIGIN",
    "PAGE_ID",
    "Q0Environment",
    "build_q0_environment",
    "table_count",
    "upload_one",
]
