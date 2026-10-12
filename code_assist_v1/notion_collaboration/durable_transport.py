"""Durable T0B-P1 ticket, ACK/replay, Yjs update, and snapshot authority.

This is a service-layer SQLite component, not a network gateway.  It persists
only opaque selected-runtime payloads and commits each accepted update with the
canonical operation, event, audit, and outbox records in one transaction.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
from typing import Any, Iterator, Mapping, Sequence

from code_assist_v1 import notion_workspace_healing_migration as migration_core
from code_assist_v1 import notion_workspace_realtime_transport_migration as migration
from code_assist_v1.notion_collaboration.canonical_authority import (
    AuthorityUnavailable,
    CanonicalAuthority,
    NotFoundOrDenied,
    OperationConflict,
    _canonical_json,
    _identifier,
    _operation_id,
    _require_unlocked_document,
    _sha256,
    _stable_id,
    _validated_temporary_unlock,
)
from code_assist_v1.notion_collaboration.protocol_contract import (
    Acknowledgement,
    AcknowledgementConflict,
    DurableProtocolEvent,
    EnvelopeRejected,
    IssuedTicket,
    MAX_REPLAY_BATCH,
    OperationConflict as ProtocolOperationConflict,
    ProtocolEnvelope,
    SequenceGap,
    StaleEpoch,
    TicketAuthority,
    TicketClaims,
    TicketGrant,
    TicketRejected,
)
from code_assist_v1.notion_collaboration.selected_runtime_contract import (
    SelectedRuntimeContractError,
    build_compaction_request,
    runtime_binding_sha256,
    sha256_bytes,
    validate_checked_in_binding,
    validate_compaction_response,
)


DURABLE_TRANSPORT_CONTRACT = "code_assist.notion.durable_transport_authority.v1"
YJS_UPDATE_ENCODING = "application/vnd.yjs-update"
UPDATE_ACTION = "crdt.update"
COMPACTION_ACTION = "crdt.compact"


class InjectedTransportFailure(RuntimeError):
    """Deterministic fault used only by isolated atomicity tests."""


class CompactionConflict(RuntimeError):
    """The update/snapshot high-water changed after work was prepared."""


@dataclass(frozen=True)
class SnapshotFrame:
    snapshot_id: str
    through_update_sequence: int
    through_document_sequence: int
    snapshot_sha256: str
    snapshot: bytes
    state_vector_sha256: str
    state_vector: bytes
    runtime_binding_sha256: str


@dataclass(frozen=True)
class DurableReplayBatch:
    after_document_sequence: int
    high_water_sequence: int
    snapshot: SnapshotFrame | None
    events: tuple[DurableProtocolEvent, ...]
    has_more: bool


@dataclass(frozen=True)
class CompactionWork:
    request: Mapping[str, Any]
    base_snapshot: bytes | None
    ordered_updates: tuple[bytes, ...]
    through_document_sequence: int


def _capability_role(capabilities: Sequence[str]) -> str:
    values = set(capabilities)
    if "write" in values:
        return "edit"
    if "comment" in values:
        return "comment"
    return "view"


def _acknowledgement(value: Mapping[str, Any]) -> Acknowledgement:
    return Acknowledgement(
        operation_id=str(value["operation_id"]),
        client_sequence=int(value["client_sequence"]),
        document_sequence=int(value["document_sequence"]),
        request_sha256=str(value["request_sha256"]),
        event_sha256=str(value["event_sha256"]),
        workspace_epoch=int(value["workspace_epoch"]),
        page_epoch=int(value["page_epoch"]),
        durable=True,
    )


def _ticket_record(row: sqlite3.Row) -> tuple[TicketClaims, Mapping[str, Any] | None]:
    stored = json.loads(str(row["capabilities_json"]))
    temporary_unlock = None
    if isinstance(stored, dict):
        if (set(stored) != {"version", "capabilities", "temporary_unlock"}
                or type(stored["version"]) is not int or stored["version"] != 2):
            raise AuthorityUnavailable("stored collaboration ticket is invalid")
        temporary_unlock = stored["temporary_unlock"]
        if (not isinstance(temporary_unlock, dict)
                or set(temporary_unlock) != {"lock_generation", "expires_at_ms"}
                or not isinstance(temporary_unlock["lock_generation"], str)
                or not temporary_unlock["lock_generation"]
                or type(temporary_unlock["expires_at_ms"]) is not int):
            raise AuthorityUnavailable("stored collaboration ticket is invalid")
        capabilities = stored["capabilities"]
    else:
        capabilities = stored
    if not isinstance(capabilities, list) or not all(isinstance(item, str) for item in capabilities):
        raise AuthorityUnavailable("stored collaboration ticket is invalid")
    if temporary_unlock is not None and "write" not in capabilities:
        raise AuthorityUnavailable("stored collaboration ticket is invalid")
    claims = TicketClaims(
        protocol_version=1,
        jti=str(row["jti"]),
        subject_id=str(row["subject_id"]),
        workspace_id=str(row["workspace_id"]),
        page_id=str(row["document_id"]),
        client_id=str(row["client_id"]),
        session_binding_sha256=str(row["session_binding_sha256"]),
        origin=str(row["origin"]),
        capabilities=tuple(capabilities),
        workspace_epoch=int(row["workspace_epoch"]),
        page_epoch=int(row["document_epoch"]),
        issued_at_ms=int(row["issued_at_ms"]),
        expires_at_ms=int(row["expires_at_ms"]),
    )
    return claims, temporary_unlock


def _ticket_temporary_grant(claims: TicketClaims,
                            temporary_unlock: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    if temporary_unlock is None:
        return None
    return {
        "user_id": claims.subject_id,
        "workspace_id": claims.workspace_id,
        "page_id": claims.page_id,
        "lock_generation": temporary_unlock["lock_generation"],
        "expires_at_ms": temporary_unlock["expires_at_ms"],
    }


class DurableTransportAuthority:
    """Exact 2026082402 service facade over the canonical workspace catalog."""

    def __init__(self, catalog_path: Path, user_data_root: Path) -> None:
        self.catalog_path = catalog_path
        self.user_data_root = user_data_root

    @classmethod
    def open(
        cls,
        catalog_path: str | os.PathLike[str],
        user_data_root: str | os.PathLike[str],
    ) -> "DurableTransportAuthority":
        try:
            catalog, root = migration_core._resolved_target(catalog_path, user_data_root)
            con = sqlite3.connect(f"{catalog.as_uri()}?mode=ro", uri=True)
            con.row_factory = sqlite3.Row
            try:
                con.execute("PRAGMA query_only=ON")
                if migration_core._inspect(con, migration._contract())["state"] != "already_current":
                    raise AuthorityUnavailable("durable transport schema is not current")
            finally:
                con.close()
            validate_checked_in_binding()
        except AuthorityUnavailable:
            raise
        except (OSError, sqlite3.Error, RuntimeError, SelectedRuntimeContractError) as exc:
            raise AuthorityUnavailable("durable transport authority is unavailable") from exc
        return cls(catalog, root)

    def _connect(self, *, query_only: bool) -> sqlite3.Connection:
        try:
            if query_only:
                con = sqlite3.connect(
                    f"{self.catalog_path.as_uri()}?mode=ro",
                    uri=True,
                    timeout=5.0,
                    isolation_level=None,
                )
            else:
                con = sqlite3.connect(self.catalog_path, timeout=5.0, isolation_level=None)
            con.row_factory = sqlite3.Row
            con.execute("PRAGMA foreign_keys=ON")
            con.execute("PRAGMA busy_timeout=5000")
            if query_only:
                con.execute("PRAGMA query_only=ON")
            marker = con.execute(
                "SELECT name,checksum,actual_schema_sha256 FROM workspace_schema_migrations "
                "WHERE version=?",
                (migration.TARGET_VERSION,),
            ).fetchone()
            if marker is None or tuple(marker) != (
                migration.TARGET_NAME,
                migration.TARGET_CHECKSUM,
                migration.TARGET_ACTUAL_SCHEMA_SHA256,
            ):
                con.close()
                raise AuthorityUnavailable("durable transport schema marker mismatch")
            return con
        except AuthorityUnavailable:
            raise
        except sqlite3.Error as exc:
            raise AuthorityUnavailable("durable transport authority is unavailable") from exc

    @contextmanager
    def _reader(self) -> Iterator[sqlite3.Connection]:
        con = self._connect(query_only=True)
        try:
            yield con
        finally:
            con.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        con = self._connect(query_only=False)
        try:
            con.execute("BEGIN IMMEDIATE")
            yield con
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    def _record_crdt_update_binding(
        self,
        con: sqlite3.Connection,
        *,
        update_id: str,
        envelope: ProtocolEnvelope,
        created_at: int,
    ) -> None:
        """Successor-schema hook; the exact 2402 transport has no binding table."""

    def _record_crdt_snapshot_binding(
        self,
        con: sqlite3.Connection,
        *,
        snapshot_id: str,
        document_id: str,
        created_at: int,
    ) -> None:
        """Successor-schema hook; the exact 2402 transport has no binding table."""

    def issue_ticket(
        self,
        *,
        csrf_valid: bool,
        subject_id: str,
        workspace_id: str,
        document_id: str,
        client_id: str,
        session_binding_sha256: str,
        origin: str,
        capabilities: Sequence[str],
        now_ms: int,
        ttl_ms: int = 30_000,
        include_temporary_write: bool = False,
    ) -> IssuedTicket:
        subject = _identifier(subject_id, field="subject_id")
        workspace = _identifier(workspace_id, field="workspace_id")
        document = _identifier(document_id, field="document_id")
        requested_capabilities = tuple(capabilities)
        if type(include_temporary_write) is not bool or (include_temporary_write and "write" in requested_capabilities):
            raise ValueError("invalid temporary ticket request")
        with self._transaction() as con:
            workspace_row, document_row, _role = self.canonical_authority()._access(
                con,
                subject,
                document,
                minimum_role=_capability_role(
                    requested_capabilities + (("write",) if include_temporary_write else ()),
                ),
            )
            if str(workspace_row["id"]) != workspace:
                raise NotFoundOrDenied("not found or permission denied")
            if "write" in requested_capabilities:
                _require_unlocked_document(document_row)
            temporary_unlock = None
            effective_capabilities = requested_capabilities
            if include_temporary_write:
                temporary_unlock = _validated_temporary_unlock(con, document_row, subject)
                if temporary_unlock is not None or not json.loads(str(document_row["settings_json"])).get("locked"):
                    effective_capabilities += ("write",)
            issued = TicketAuthority().issue(
                csrf_valid=csrf_valid,
                subject_id=subject,
                workspace_id=workspace,
                page_id=document,
                client_id=client_id,
                session_binding_sha256=session_binding_sha256,
                origin=origin,
                capabilities=effective_capabilities,
                workspace_epoch=int(workspace_row["security_epoch"]),
                page_epoch=int(document_row["access_epoch"]),
                now_ms=now_ms,
                ttl_ms=ttl_ms,
            )
            digest = _sha256(issued.token.encode("ascii"))
            stored_capabilities: Any = list(issued.claims.capabilities)
            if temporary_unlock is not None:
                stored_capabilities = {
                    "version": 2,
                    "capabilities": stored_capabilities,
                    "temporary_unlock": {
                        "lock_generation": temporary_unlock["lock_generation"],
                        "expires_at_ms": temporary_unlock["expires_at_ms"],
                    },
                }
            con.execute(
                "INSERT INTO workspace_collaboration_tickets("
                "ticket_sha256,jti,subject_id,workspace_id,document_id,client_id,"
                "session_binding_sha256,origin,capabilities_json,workspace_epoch,"
                "document_epoch,issued_at_ms,expires_at_ms,consumed_at_ms,burn_reason,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,NULL,NULL,?)",
                (
                    digest,
                    issued.claims.jti,
                    issued.claims.subject_id,
                    issued.claims.workspace_id,
                    issued.claims.page_id,
                    issued.claims.client_id,
                    issued.claims.session_binding_sha256,
                    issued.claims.origin,
                    _canonical_json(stored_capabilities),
                    issued.claims.workspace_epoch,
                    issued.claims.page_epoch,
                    issued.claims.issued_at_ms,
                    issued.claims.expires_at_ms,
                    int(time.time()),
                ),
            )
            return issued

    def consume_ticket(
        self,
        token: str,
        *,
        origin: str,
        subject_id: str,
        workspace_id: str,
        document_id: str,
        client_id: str,
        session_binding_sha256: str,
        now_ms: int,
    ) -> TicketGrant:
        try:
            candidate = str(token or "")
            digest = _sha256(candidate.encode("ascii"))
            presented = {
                "origin": str(origin),
                "subject_id": _identifier(subject_id, field="subject_id"),
                "workspace_id": _identifier(workspace_id, field="workspace_id"),
                "document_id": _identifier(document_id, field="document_id"),
                "client_id": _identifier(client_id, field="client_id"),
                "session_binding_sha256": str(session_binding_sha256),
                "now_ms": int(now_ms),
            }
            if len(candidate) != 43 or presented["now_ms"] < 0:
                raise ValueError
        except (TypeError, UnicodeError, ValueError):
            raise TicketRejected() from None

        claims: TicketClaims | None = None
        temporary_unlock: Mapping[str, Any] | None = None
        accepted = False
        with self._transaction() as con:
            row = con.execute(
                "SELECT * FROM workspace_collaboration_tickets WHERE ticket_sha256=?",
                (digest,),
            ).fetchone()
            if row is None or row["consumed_at_ms"] is not None:
                raise TicketRejected()
            try:
                claims, temporary_unlock = _ticket_record(row)
                workspace_row, document_row, _role = self.canonical_authority()._access(
                    con,
                    claims.subject_id,
                    claims.page_id,
                    minimum_role=_capability_role(claims.capabilities),
                )
                accepted = (
                    claims.issued_at_ms <= presented["now_ms"] < claims.expires_at_ms
                    and claims.origin == presented["origin"]
                    and claims.subject_id == presented["subject_id"]
                    and claims.workspace_id == presented["workspace_id"]
                    and claims.page_id == presented["document_id"]
                    and claims.client_id == presented["client_id"]
                    and claims.session_binding_sha256 == presented["session_binding_sha256"]
                    and claims.workspace_epoch == int(workspace_row["security_epoch"])
                    and claims.page_epoch == int(document_row["access_epoch"])
                )
                if accepted and "write" in claims.capabilities and json.loads(str(document_row["settings_json"])).get("locked"):
                    candidate = _ticket_temporary_grant(claims, temporary_unlock)
                    accepted = (candidate is not None
                                and _validated_temporary_unlock(con, document_row, claims.subject_id, candidate) is not None)
            except (AuthorityUnavailable, NotFoundOrDenied, ValueError, json.JSONDecodeError):
                accepted = False
            con.execute(
                "UPDATE workspace_collaboration_tickets SET consumed_at_ms=?,burn_reason=? "
                "WHERE ticket_sha256=?",
                (presented["now_ms"], "accepted" if accepted else "rejected", digest),
            )
        if not accepted or claims is None:
            raise TicketRejected()
        return TicketGrant(
            claims=claims, consumed_at_ms=presented["now_ms"], temporary_unlock=temporary_unlock,
        )

    def accept_update(
        self,
        envelope: ProtocolEnvelope,
        grant: TicketGrant,
        *,
        fault: str | None = None,
    ) -> Acknowledgement:
        allowed_faults = {
            None,
            "after_domain",
            "after_event",
            "after_audit",
            "after_outbox",
            "after_operation",
            "after_commit_before_ack",
        }
        if fault not in allowed_faults:
            raise ValueError("unknown durable transport fault point")
        if envelope.message_type != "crdt.update" or envelope.payload_encoding != YJS_UPDATE_ENCODING:
            raise EnvelopeRejected("durable transport accepts only selected Yjs update payloads")
        request_sha256 = envelope.request_sha256()
        result: dict[str, Any]
        with self._transaction() as con:
            workspace_row, document_row, _role = self.canonical_authority()._access(
                con,
                envelope.subject_id,
                envelope.page_id,
                minimum_role="edit",
                expected_workspace_epoch=envelope.workspace_epoch,
                expected_document_epoch=envelope.page_epoch,
            )
            envelope.validate(
                grant,
                current_workspace_epoch=int(workspace_row["security_epoch"]),
                current_page_epoch=int(document_row["access_epoch"]),
            )
            if str(workspace_row["id"]) != envelope.workspace_id:
                raise EnvelopeRejected("envelope workspace binding mismatch")

            def authorize_replay() -> None:
                self.canonical_authority()._access(
                    con,
                    envelope.subject_id,
                    envelope.page_id,
                    minimum_role="edit",
                    expected_workspace_epoch=envelope.workspace_epoch,
                    expected_document_epoch=envelope.page_epoch,
                )

            replay = CanonicalAuthority._replay(
                con,
                actor=envelope.subject_id,
                action=UPDATE_ACTION,
                operation_id=envelope.operation_id,
                request_sha256=request_sha256,
                authorize=authorize_replay,
            )
            if replay is not None:
                return _acknowledgement(replay)

            if json.loads(str(document_row["settings_json"])).get("locked"):
                candidate = _ticket_temporary_grant(grant.claims, grant.temporary_unlock)
                if (candidate is None
                        or _validated_temporary_unlock(con, document_row, envelope.subject_id, candidate) is None):
                    raise PermissionError("page is locked")

            client_key = (
                envelope.subject_id,
                envelope.page_id,
                envelope.client_id,
                envelope.session_binding_sha256,
            )
            state = con.execute(
                "SELECT * FROM workspace_collaboration_client_ack_state "
                "WHERE subject_id=? AND document_id=? AND client_id=? AND session_binding_sha256=?",
                client_key,
            ).fetchone()
            expected_client_sequence = 1 if state is None else int(state["next_client_sequence"])
            if envelope.client_sequence != expected_client_sequence:
                raise SequenceGap(expected_client_sequence)
            prior_ack = 0 if state is None else int(state["acknowledged_document_sequence"])
            current_document_sequence = int(document_row["document_sequence"])
            if (
                envelope.acknowledgement_sequence < prior_ack
                or envelope.acknowledgement_sequence > current_document_sequence
            ):
                raise AcknowledgementConflict("acknowledgement is outside durable bounds")
            update_sequence = 1 + int(
                con.execute(
                    "SELECT COALESCE(MAX(update_sequence),0) FROM workspace_collaboration_crdt_updates "
                    "WHERE document_id=?",
                    (envelope.page_id,),
                ).fetchone()[0]
            )
            document_sequence = current_document_sequence + 1
            now = int(time.time())
            update_id = _stable_id("cupd", envelope.subject_id, envelope.page_id, envelope.operation_id)
            binding_sha256 = runtime_binding_sha256()
            con.execute(
                "UPDATE workspace_collaboration_documents SET document_sequence=?,updated_at=?,"
                "row_version=row_version+1 WHERE id=?",
                (document_sequence, now, envelope.page_id),
            )
            con.execute(
                "INSERT INTO workspace_collaboration_crdt_updates("
                "id,workspace_id,document_id,update_sequence,document_sequence,actor_subject_id,"
                "client_id,session_binding_sha256,operation_id,client_sequence,payload_encoding,"
                "payload_bytes,payload_sha256,payload,workspace_epoch,document_epoch,"
                "runtime_binding_sha256,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    update_id,
                    envelope.workspace_id,
                    envelope.page_id,
                    update_sequence,
                    document_sequence,
                    envelope.subject_id,
                    envelope.client_id,
                    envelope.session_binding_sha256,
                    envelope.operation_id,
                    envelope.client_sequence,
                    envelope.payload_encoding,
                    envelope.payload_length,
                    envelope.payload_sha256,
                    envelope.payload,
                    envelope.workspace_epoch,
                    envelope.page_epoch,
                    binding_sha256,
                    now,
                ),
            )
            self._record_crdt_update_binding(
                con,
                update_id=update_id,
                envelope=envelope,
                created_at=now,
            )
            if state is None:
                con.execute(
                    "INSERT INTO workspace_collaboration_client_ack_state("
                    "subject_id,document_id,client_id,session_binding_sha256,next_client_sequence,"
                    "acknowledged_document_sequence,workspace_epoch,document_epoch,updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?)",
                    (*client_key, envelope.client_sequence + 1, envelope.acknowledgement_sequence,
                     envelope.workspace_epoch, envelope.page_epoch, now),
                )
            else:
                con.execute(
                    "UPDATE workspace_collaboration_client_ack_state SET next_client_sequence=?,"
                    "acknowledged_document_sequence=?,workspace_epoch=?,document_epoch=?,updated_at=? "
                    "WHERE subject_id=? AND document_id=? AND client_id=? AND session_binding_sha256=?",
                    (
                        envelope.client_sequence + 1,
                        envelope.acknowledgement_sequence,
                        envelope.workspace_epoch,
                        envelope.page_epoch,
                        now,
                        *client_key,
                    ),
                )
            event_descriptor = {
                "workspace_id": envelope.workspace_id,
                "page_id": envelope.page_id,
                "document_sequence": document_sequence,
                "update_sequence": update_sequence,
                "subject_id": envelope.subject_id,
                "client_id": envelope.client_id,
                "operation_id": envelope.operation_id,
                "client_sequence": envelope.client_sequence,
                "message_type": envelope.message_type,
                "payload_encoding": envelope.payload_encoding,
                "payload_sha256": envelope.payload_sha256,
                "workspace_epoch": envelope.workspace_epoch,
                "page_epoch": envelope.page_epoch,
                "runtime_binding_sha256": binding_sha256,
            }
            ack_result = {
                "contract": DURABLE_TRANSPORT_CONTRACT,
                "operation_id": envelope.operation_id,
                "client_sequence": envelope.client_sequence,
                "document_sequence": document_sequence,
                "update_sequence": update_sequence,
                "update_id": update_id,
                "request_sha256": request_sha256,
                "event_sha256": sha256_bytes(_canonical_json(event_descriptor).encode("utf-8")),
                "workspace_epoch": envelope.workspace_epoch,
                "page_epoch": envelope.page_epoch,
                "runtime_binding_sha256": binding_sha256,
            }
            result = CanonicalAuthority._finish(
                con,
                actor=envelope.subject_id,
                action=UPDATE_ACTION,
                operation_id=envelope.operation_id,
                request_sha256=request_sha256,
                workspace_id=envelope.workspace_id,
                document_id=envelope.page_id,
                workspace_epoch=envelope.workspace_epoch,
                document_epoch=envelope.page_epoch,
                document_sequence=document_sequence,
                event_type="crdt.update",
                event_payload=event_descriptor,
                result=ack_result,
                fault=None if fault == "after_commit_before_ack" else fault,
            )
        if fault == "after_commit_before_ack":
            raise InjectedTransportFailure("after_commit_before_ack")
        return _acknowledgement(result)

    def replay(
        self,
        grant: TicketGrant,
        *,
        after_document_sequence: int,
        limit: int,
    ) -> DurableReplayBatch:
        after = int(after_document_sequence)
        batch_limit = int(limit)
        if after < 0 or batch_limit < 1 or batch_limit > MAX_REPLAY_BATCH:
            raise ValueError("replay cursor or limit is invalid")
        claims = grant.claims
        with self._reader() as con:
            workspace_row, document_row, _role = self.canonical_authority()._access(
                con,
                claims.subject_id,
                claims.page_id,
                minimum_role="view",
                expected_workspace_epoch=claims.workspace_epoch,
                expected_document_epoch=claims.page_epoch,
            )
            high_water = int(document_row["document_sequence"])
            if str(workspace_row["id"]) != claims.workspace_id:
                raise StaleEpoch()
            if after > high_water:
                raise AcknowledgementConflict("replay cursor exceeds durable high-water mark")
            snapshot_row = con.execute(
                "SELECT * FROM workspace_collaboration_crdt_snapshots WHERE document_id=? "
                "ORDER BY through_update_sequence DESC LIMIT 1",
                (claims.page_id,),
            ).fetchone()
            snapshot: SnapshotFrame | None = None
            effective_after = after
            if snapshot_row is not None and after < int(snapshot_row["through_document_sequence"]):
                snapshot = SnapshotFrame(
                    snapshot_id=str(snapshot_row["id"]),
                    through_update_sequence=int(snapshot_row["through_update_sequence"]),
                    through_document_sequence=int(snapshot_row["through_document_sequence"]),
                    snapshot_sha256=str(snapshot_row["snapshot_sha256"]),
                    snapshot=bytes(snapshot_row["snapshot"]),
                    state_vector_sha256=str(snapshot_row["state_vector_sha256"]),
                    state_vector=bytes(snapshot_row["state_vector"]),
                    runtime_binding_sha256=str(snapshot_row["runtime_binding_sha256"]),
                )
                effective_after = snapshot.through_document_sequence
            rows = con.execute(
                "SELECT * FROM workspace_collaboration_crdt_updates "
                "WHERE document_id=? AND document_sequence>? "
                "ORDER BY document_sequence LIMIT ?",
                (claims.page_id, effective_after, batch_limit + 1),
            ).fetchall()
            selected = rows[:batch_limit]
            events = tuple(
                DurableProtocolEvent(
                    workspace_id=str(row["workspace_id"]),
                    page_id=str(row["document_id"]),
                    document_sequence=int(row["document_sequence"]),
                    subject_id=str(row["actor_subject_id"]),
                    client_id=str(row["client_id"]),
                    operation_id=str(row["operation_id"]),
                    client_sequence=int(row["client_sequence"]),
                    message_type="crdt.update",
                    payload_encoding=str(row["payload_encoding"]),
                    payload_sha256=str(row["payload_sha256"]),
                    payload=bytes(row["payload"]),
                    workspace_epoch=int(row["workspace_epoch"]),
                    page_epoch=int(row["document_epoch"]),
                )
                for row in selected
            )
            return DurableReplayBatch(after, high_water, snapshot, events, len(rows) > batch_limit)

    def prepare_compaction(self, grant: TicketGrant) -> CompactionWork:
        claims = grant.claims
        with self._reader() as con:
            workspace_row, document_row, _role = self.canonical_authority()._access(
                con,
                claims.subject_id,
                claims.page_id,
                minimum_role="edit",
                expected_workspace_epoch=claims.workspace_epoch,
                expected_document_epoch=claims.page_epoch,
            )
            if str(workspace_row["id"]) != claims.workspace_id:
                raise StaleEpoch()
            snapshot = con.execute(
                "SELECT * FROM workspace_collaboration_crdt_snapshots WHERE document_id=? "
                "ORDER BY through_update_sequence DESC LIMIT 1",
                (claims.page_id,),
            ).fetchone()
            base_sequence = 0 if snapshot is None else int(snapshot["through_update_sequence"])
            updates = con.execute(
                "SELECT * FROM workspace_collaboration_crdt_updates "
                "WHERE document_id=? AND update_sequence>? ORDER BY update_sequence",
                (claims.page_id, base_sequence),
            ).fetchall()
            if not updates:
                raise SelectedRuntimeContractError("at least one uncompacted update is required")
            request = build_compaction_request(
                request_id=_stable_id("creq", claims.page_id, str(updates[-1]["update_sequence"])),
                document_id=claims.page_id,
                workspace_epoch=claims.workspace_epoch,
                page_epoch=claims.page_epoch,
                base_snapshot_id=None if snapshot is None else str(snapshot["id"]),
                base_high_water_sequence=base_sequence,
                target_high_water_sequence=int(updates[-1]["update_sequence"]),
                ordered_update_bytes=sum(int(row["payload_bytes"]) for row in updates),
                ordered_update_sha256=[str(row["payload_sha256"]) for row in updates],
            )
            return CompactionWork(
                request=request,
                base_snapshot=None if snapshot is None else bytes(snapshot["snapshot"]),
                ordered_updates=tuple(bytes(row["payload"]) for row in updates),
                through_document_sequence=int(updates[-1]["document_sequence"]),
            )

    def commit_compaction(
        self,
        grant: TicketGrant,
        *,
        operation_id: str,
        work: CompactionWork,
        response: Mapping[str, Any],
        snapshot: bytes,
        state_vector: bytes,
        fault: str | None = None,
    ) -> dict[str, Any]:
        if fault not in {None, "after_domain", "after_event", "after_audit", "after_outbox", "after_operation"}:
            raise ValueError("unknown compaction fault point")
        operation = _operation_id(operation_id)
        validated = validate_compaction_response(response, request=work.request)
        snapshot_bytes = bytes(snapshot)
        state_vector_bytes = bytes(state_vector)
        if (
            len(snapshot_bytes) != validated["snapshot_bytes"]
            or sha256_bytes(snapshot_bytes) != validated["snapshot_sha256"]
            or len(state_vector_bytes) != validated["state_vector_bytes"]
            or sha256_bytes(state_vector_bytes) != validated["state_vector_sha256"]
        ):
            raise SelectedRuntimeContractError("compaction response bytes do not match hashes")
        claims = grant.claims
        request_sha256 = _sha256(
            _canonical_json(
                {
                    "operation_id": operation,
                    "request": dict(work.request),
                    "response": dict(validated),
                }
            )
        )
        with self._transaction() as con:
            workspace_row, document_row, _role = self.canonical_authority()._access(
                con,
                claims.subject_id,
                claims.page_id,
                minimum_role="edit",
                expected_workspace_epoch=claims.workspace_epoch,
                expected_document_epoch=claims.page_epoch,
            )
            if str(workspace_row["id"]) != claims.workspace_id:
                raise StaleEpoch()

            def authorize_replay() -> None:
                self.canonical_authority()._access(
                    con,
                    claims.subject_id,
                    claims.page_id,
                    minimum_role="edit",
                    expected_workspace_epoch=claims.workspace_epoch,
                    expected_document_epoch=claims.page_epoch,
                )

            replay = CanonicalAuthority._replay(
                con,
                actor=claims.subject_id,
                action=COMPACTION_ACTION,
                operation_id=operation,
                request_sha256=request_sha256,
                authorize=authorize_replay,
            )
            if replay is not None:
                return replay
            current_snapshot = con.execute(
                "SELECT id,through_update_sequence FROM workspace_collaboration_crdt_snapshots "
                "WHERE document_id=? ORDER BY through_update_sequence DESC LIMIT 1",
                (claims.page_id,),
            ).fetchone()
            base_id = work.request.get("base_snapshot_id")
            current_base_id = None if current_snapshot is None else str(current_snapshot["id"])
            current_base_sequence = 0 if current_snapshot is None else int(current_snapshot["through_update_sequence"])
            if current_base_id != base_id or current_base_sequence != work.request["base_high_water_sequence"]:
                raise CompactionConflict("snapshot base changed")
            rows = con.execute(
                "SELECT update_sequence,document_sequence,payload_bytes,payload_sha256 FROM "
                "workspace_collaboration_crdt_updates WHERE document_id=? AND update_sequence>? "
                "AND update_sequence<=? ORDER BY update_sequence",
                (
                    claims.page_id,
                    work.request["base_high_water_sequence"],
                    work.request["target_high_water_sequence"],
                ),
            ).fetchall()
            if (
                [str(row["payload_sha256"]) for row in rows] != work.request["ordered_update_sha256"]
                or sum(int(row["payload_bytes"]) for row in rows) != work.request["ordered_update_bytes"]
                or not rows
                or int(rows[-1]["document_sequence"]) != work.through_document_sequence
            ):
                raise CompactionConflict("compaction update range changed")
            snapshot_id = _stable_id(
                "csnap", claims.page_id, str(work.request["target_high_water_sequence"]), validated["snapshot_sha256"]
            )
            now = int(time.time())
            con.execute(
                "INSERT INTO workspace_collaboration_crdt_snapshots("
                "id,workspace_id,document_id,actor_subject_id,operation_id,base_snapshot_id,"
                "base_update_sequence,through_update_sequence,through_document_sequence,"
                "snapshot_bytes,snapshot_sha256,snapshot,state_vector_bytes,state_vector_sha256,"
                "state_vector,request_sha256,runtime_binding_sha256,workspace_epoch,document_epoch,"
                "created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    snapshot_id,
                    claims.workspace_id,
                    claims.page_id,
                    claims.subject_id,
                    operation,
                    base_id,
                    work.request["base_high_water_sequence"],
                    work.request["target_high_water_sequence"],
                    work.through_document_sequence,
                    len(snapshot_bytes),
                    validated["snapshot_sha256"],
                    snapshot_bytes,
                    len(state_vector_bytes),
                    validated["state_vector_sha256"],
                    state_vector_bytes,
                    work.request["request_sha256"],
                    runtime_binding_sha256(),
                    claims.workspace_epoch,
                    claims.page_epoch,
                    now,
                ),
            )
            self._record_crdt_snapshot_binding(
                con,
                snapshot_id=snapshot_id,
                document_id=claims.page_id,
                created_at=now,
            )
            result = {
                "contract": DURABLE_TRANSPORT_CONTRACT,
                "snapshot_id": snapshot_id,
                "base_snapshot_id": base_id,
                "through_update_sequence": work.request["target_high_water_sequence"],
                "through_document_sequence": work.through_document_sequence,
                "snapshot_sha256": validated["snapshot_sha256"],
                "state_vector_sha256": validated["state_vector_sha256"],
                "runtime_binding_sha256": runtime_binding_sha256(),
            }
            return CanonicalAuthority._finish(
                con,
                actor=claims.subject_id,
                action=COMPACTION_ACTION,
                operation_id=operation,
                request_sha256=request_sha256,
                workspace_id=claims.workspace_id,
                document_id=claims.page_id,
                workspace_epoch=claims.workspace_epoch,
                document_epoch=claims.page_epoch,
                document_sequence=None,
                event_type="crdt.snapshot.compacted",
                event_payload=result,
                result=result,
                fault=fault,
            )

    def counts(self) -> dict[str, int]:
        tables = (
            "workspace_collaboration_tickets",
            "workspace_collaboration_client_ack_state",
            "workspace_collaboration_crdt_updates",
            "workspace_collaboration_crdt_snapshots",
            "workspace_collaboration_operations",
            "workspace_collaboration_events",
            "workspace_collaboration_audit_events",
            "workspace_collaboration_outbox",
        )
        with self._reader() as con:
            return {table: int(con.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]) for table in tables}

    def operational_observation(self, *, now_seconds: int | None = None) -> dict[str, int]:
        """Return identifier-free compaction and durable-tail gauges."""

        now = int(time.time()) if now_seconds is None else now_seconds
        if type(now) is not int or now < 0:
            raise ValueError("valid now_seconds required")
        with self._reader() as con:
            row = con.execute(
                """
                WITH snapshot_heads AS (
                  SELECT document_id,MAX(through_update_sequence) AS through_update_sequence
                  FROM workspace_collaboration_crdt_snapshots
                  GROUP BY document_id
                ), uncompacted AS (
                  SELECT u.document_id,u.update_sequence,u.created_at,
                         COALESCE(h.through_update_sequence,0) AS snapshot_sequence
                  FROM workspace_collaboration_crdt_updates u
                  LEFT JOIN snapshot_heads h ON h.document_id=u.document_id
                  WHERE u.update_sequence>COALESCE(h.through_update_sequence,0)
                )
                SELECT COUNT(*) AS tail_length,
                       COALESCE(MAX(update_sequence-snapshot_sequence),0) AS maximum_lag,
                       MIN(created_at) AS oldest_created_at
                FROM uncompacted
                """
            ).fetchone()
        oldest = None if row is None else row["oldest_created_at"]
        return {
            "durable_event_tail_length": 0 if row is None else int(row["tail_length"]),
            "compaction_lag_sequences": 0 if row is None else int(row["maximum_lag"]),
            "oldest_uncompacted_event_age_seconds": (
                0 if oldest is None else max(0, now - int(oldest))
            ),
        }

    def ticket_rows(self) -> list[dict[str, Any]]:
        with self._reader() as con:
            return [dict(row) for row in con.execute(
                "SELECT ticket_sha256,jti,consumed_at_ms,burn_reason FROM "
                "workspace_collaboration_tickets ORDER BY created_at,jti"
            ).fetchall()]

    def canonical_authority(self) -> "ForwardCompatibleCanonicalAuthority":
        """Return the A0B service API over this exact additive 2402 catalog."""

        return ForwardCompatibleCanonicalAuthority(self.catalog_path, self.user_data_root)


class ForwardCompatibleCanonicalAuthority(CanonicalAuthority):
    """Compatibility adapter that preserves A0B operations on exact 2402."""

    @classmethod
    def open(
        cls,
        catalog_path: str | os.PathLike[str],
        user_data_root: str | os.PathLike[str],
    ) -> "ForwardCompatibleCanonicalAuthority":
        transport = DurableTransportAuthority.open(catalog_path, user_data_root)
        return cls(transport.catalog_path, transport.user_data_root)

    def _connect(self, *, query_only: bool) -> sqlite3.Connection:
        return DurableTransportAuthority(self.catalog_path, self.user_data_root)._connect(
            query_only=query_only
        )


__all__ = [
    "CompactionConflict",
    "CompactionWork",
    "DURABLE_TRANSPORT_CONTRACT",
    "DurableReplayBatch",
    "DurableTransportAuthority",
    "ForwardCompatibleCanonicalAuthority",
    "InjectedTransportFailure",
    "SnapshotFrame",
    "YJS_UPDATE_ENCODING",
]
