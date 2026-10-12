"""Durable Notion-side coordination for exact-revision SBE publication.

Review happens entirely in the Notion authority.  Approval first validates the
source fence and changes the durable intent to ``sbe_committing`` in the same
SQLite transaction that observes current Notion permission epochs.  Only then
does the coordinator invoke the existing Second Brain service APIs.  The two
databases are never attached or written in one transaction.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import sqlite3
import time
from typing import Any, Callable, Literal, Mapping
from uuid import UUID, uuid5

from code_assist_v1 import notion_workspace_second_brain_publication_migration as migration
from code_assist_v1.knowledge_core.repositories.publication_authority import (
    HigherPublicationTargetDescriptor,
)
from code_assist_v1.knowledge_core.services.publication import (
    CreateNotionPreviewCommand,
    PublicationPreviewService,
    PublicationResult,
    PublicationService,
    PublishPreviewCommand,
)
from code_assist_v1.notion_collaboration.canonical_authority import (
    AuthorityUnavailable,
    EpochConflict,
    NotFoundOrDenied,
)
from code_assist_v1.notion_collaboration.iam_authority import (
    CollaborativeIAMAuthority,
    InMemoryIdentityDirectory,
    PAGE_ROLE_LEVEL,
)

from .second_brain_boundary import (
    BoundNotionRevisionSnapshot,
    CollaborativeNotionSnapshotProvider,
    PublicationSourceConflict,
    canonical_publication_request_sha256,
)


PUBLICATION_INTENT_CONTRACT = "code_assist.notion.second_brain_publication_intent.v1"
_INTENT_NAMESPACE = UUID("53350728-f88f-450a-8afd-a9cbebee08a3")
_OUTBOX_NAMESPACE = UUID("d1ce9115-0ba2-4f62-8f33-6dd2f365f00a")
_REVOKE_CANCEL_NAMESPACE = UUID("6f1420a4-1382-48e5-9d65-06ac62555e6c")
_STATES = frozenset(
    {
        "prepared",
        "sbe_committing",
        "committed",
        "canceled",
        "reconciliation_required",
    }
)


class PublicationIntentError(RuntimeError):
    """Base class for fail-closed Notion publication coordination errors."""


class PublicationIntentConflict(PublicationIntentError):
    """An operation identity or state transition conflicts with durable state."""


class PublicationIntentNotFound(PublicationIntentError):
    """The intent is absent or not visible to the requesting actor."""


class PublicationResponseAmbiguous(PublicationIntentError):
    """The SBE result must be reconciled with the same operation and hash."""


class PublicationRevocationPending(PublicationIntentError):
    """Revocation cannot complete while a linearized SBE commit is unresolved."""

    def __init__(self, intent_ids: tuple[str, ...]) -> None:
        self.intent_ids = intent_ids
        super().__init__("publication reconciliation is required before revoke completion")


class PublicationSourceStillAuthorized(PublicationIntentError):
    """A revocation-completion fence was requested before access was revoked."""


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _sha256(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _text(value: object, *, field_name: str, maximum: int = 1_000) -> str:
    result = str(value or "").strip()
    if not result or len(result) > maximum or "\x00" in result:
        raise ValueError(f"valid {field_name} is required")
    return result


def _uuid(value: object, *, field_name: str) -> str:
    result = _text(value, field_name=field_name, maximum=64)
    try:
        parsed = UUID(result)
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError(f"valid {field_name} UUID is required") from exc
    if str(parsed) != result.lower():
        raise ValueError(f"canonical lowercase {field_name} UUID is required")
    return result.lower()


def _target_mapping(
    target: HigherPublicationTargetDescriptor | None,
) -> dict[str, object] | None:
    return None if target is None else dict(target.to_mapping())


def _target_from_json(value: str | None) -> HigherPublicationTargetDescriptor | None:
    if value is None:
        return None
    decoded = json.loads(value)
    if not isinstance(decoded, dict):
        raise PublicationIntentConflict("stored target descriptor is invalid")
    return HigherPublicationTargetDescriptor(**decoded)


@dataclass(frozen=True, slots=True)
class PreparePublicationIntentCommand:
    actor_user_id: str
    page_id: str
    preview_operation_id: str


@dataclass(frozen=True, slots=True)
class ApprovePublicationIntentCommand:
    request_id: str
    operation_id: str
    actor_user_id: str
    actor_scope_epoch: int
    intent_id: str
    target_mode: Literal["personal", "part", "both", "explicit"]
    target: HigherPublicationTargetDescriptor | None = None
    personal_space_id: str | None = None
    request_meta: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CancelPublicationIntentCommand:
    actor_user_id: str
    intent_id: str
    operation_id: str


@dataclass(frozen=True, slots=True)
class PublicationIntentRecord:
    intent_id: str
    workspace_id: str
    page_id: str
    actor_user_id: str
    state: str
    source_revision: int
    source_document_sequence: int
    source_workspace_epoch: int
    source_document_epoch: int
    source_history_checkpoint_id: str
    source_canonical_content_sha256: str
    source_content_sha256: str
    source_asset_manifest_sha256: str
    source_snapshot_sha256: str
    source_fence_sha256: str
    preview_operation_id: str
    preview_request_sha256: str
    approve_request_id: str | None
    publish_operation_id: str | None
    publication_request_sha256: str | None
    sbe_actor_scope_epoch: int | None
    target_mode: str | None
    personal_space_id: str | None
    target: HigherPublicationTargetDescriptor | None
    sbe_preview_id: str | None
    publication_result: Mapping[str, object] | None
    cancel_operation_id: str | None
    cancel_request_sha256: str | None
    terminal_reason: str | None
    row_version: int

    def to_mapping(self) -> dict[str, object]:
        return {
            "contract": PUBLICATION_INTENT_CONTRACT,
            "intent_id": self.intent_id,
            "workspace_id": self.workspace_id,
            "page_id": self.page_id,
            "actor_user_id": self.actor_user_id,
            "state": self.state,
            "source_revision": self.source_revision,
            "source_document_sequence": self.source_document_sequence,
            "source_workspace_epoch": self.source_workspace_epoch,
            "source_document_epoch": self.source_document_epoch,
            "source_history_checkpoint_id": self.source_history_checkpoint_id,
            "source_canonical_content_sha256": self.source_canonical_content_sha256,
            "source_content_sha256": self.source_content_sha256,
            "source_asset_manifest_sha256": self.source_asset_manifest_sha256,
            "source_snapshot_sha256": self.source_snapshot_sha256,
            "source_fence_sha256": self.source_fence_sha256,
            "preview_operation_id": self.preview_operation_id,
            "preview_request_sha256": self.preview_request_sha256,
            "approve_request_id": self.approve_request_id,
            "publish_operation_id": self.publish_operation_id,
            "publication_request_sha256": self.publication_request_sha256,
            "sbe_actor_scope_epoch": self.sbe_actor_scope_epoch,
            "target_mode": self.target_mode,
            "personal_space_id": self.personal_space_id,
            "target": None if self.target is None else self.target.to_mapping(),
            "sbe_preview_id": self.sbe_preview_id,
            "publication_result": (
                None
                if self.publication_result is None
                else dict(self.publication_result)
            ),
            "cancel_operation_id": self.cancel_operation_id,
            "cancel_request_sha256": self.cancel_request_sha256,
            "terminal_reason": self.terminal_reason,
            "row_version": self.row_version,
        }


class SecondBrainPublicationCoordinator:
    """Coordinate one exact Notion revision with existing SBE service APIs."""

    def __init__(
        self,
        snapshot_provider: CollaborativeNotionSnapshotProvider,
        preview_service: PublicationPreviewService,
        publication_service: PublicationService,
        *,
        failpoint: Callable[[str], None] | None = None,
    ) -> None:
        if not isinstance(snapshot_provider, CollaborativeNotionSnapshotProvider):
            raise TypeError("snapshot_provider must use the collaborative Notion authority")
        if not isinstance(preview_service, PublicationPreviewService):
            raise TypeError("preview_service must be a PublicationPreviewService")
        if not isinstance(publication_service, PublicationService):
            raise TypeError("publication_service must be a PublicationService")
        if failpoint is not None and not callable(failpoint):
            raise TypeError("failpoint must be callable or None")
        self._provider = snapshot_provider
        self._previews = preview_service
        self._publications = publication_service
        self._failpoint = failpoint

    @classmethod
    def open(
        cls,
        catalog_path: str | Path,
        user_data_root: str | Path,
        identity_directory: InMemoryIdentityDirectory,
        preview_service: PublicationPreviewService,
        publication_service: PublicationService,
        *,
        failpoint: Callable[[str], None] | None = None,
    ) -> "SecondBrainPublicationCoordinator":
        provider = CollaborativeNotionSnapshotProvider.open(
            catalog_path, user_data_root, identity_directory
        )
        return cls(provider, preview_service, publication_service, failpoint=failpoint)

    def _hit(self, name: str) -> None:
        if self._failpoint is not None:
            self._failpoint(name)

    @staticmethod
    def _record(row: sqlite3.Row) -> PublicationIntentRecord:
        state = str(row["state"])
        if state not in _STATES:
            raise PublicationIntentConflict("stored publication intent state is invalid")
        result_raw = row["publication_result_json"]
        result = None if result_raw is None else json.loads(str(result_raw))
        if result is not None and not isinstance(result, dict):
            raise PublicationIntentConflict("stored publication result is invalid")
        return PublicationIntentRecord(
            intent_id=str(row["id"]),
            workspace_id=str(row["workspace_id"]),
            page_id=str(row["document_id"]),
            actor_user_id=str(row["actor_subject_id"]),
            state=state,
            source_revision=int(row["source_revision"]),
            source_document_sequence=int(row["source_document_sequence"]),
            source_workspace_epoch=int(row["source_workspace_epoch"]),
            source_document_epoch=int(row["source_document_epoch"]),
            source_history_checkpoint_id=str(row["source_history_checkpoint_id"]),
            source_canonical_content_sha256=str(row["source_canonical_content_sha256"]),
            source_content_sha256=str(row["source_content_sha256"]),
            source_asset_manifest_sha256=str(row["source_asset_manifest_sha256"]),
            source_snapshot_sha256=str(row["source_snapshot_sha256"]),
            source_fence_sha256=str(row["source_fence_sha256"]),
            preview_operation_id=str(row["preview_operation_id"]),
            preview_request_sha256=str(row["preview_request_sha256"]),
            approve_request_id=(
                None if row["approve_request_id"] is None else str(row["approve_request_id"])
            ),
            publish_operation_id=(
                None
                if row["publish_operation_id"] is None
                else str(row["publish_operation_id"])
            ),
            publication_request_sha256=(
                None
                if row["publication_request_sha256"] is None
                else str(row["publication_request_sha256"])
            ),
            sbe_actor_scope_epoch=(
                None
                if row["sbe_actor_scope_epoch"] is None
                else int(row["sbe_actor_scope_epoch"])
            ),
            target_mode=None if row["target_mode"] is None else str(row["target_mode"]),
            personal_space_id=(
                None if row["personal_space_id"] is None else str(row["personal_space_id"])
            ),
            target=_target_from_json(
                None
                if row["target_descriptor_json"] is None
                else str(row["target_descriptor_json"])
            ),
            sbe_preview_id=(
                None if row["sbe_preview_id"] is None else str(row["sbe_preview_id"])
            ),
            publication_result=result,
            cancel_operation_id=(
                None
                if row["cancel_operation_id"] is None
                else str(row["cancel_operation_id"])
            ),
            cancel_request_sha256=(
                None
                if row["cancel_request_sha256"] is None
                else str(row["cancel_request_sha256"])
            ),
            terminal_reason=(
                None if row["terminal_reason"] is None else str(row["terminal_reason"])
            ),
            row_version=int(row["row_version"]),
        )

    @staticmethod
    def _load(
        con: sqlite3.Connection,
        intent_id: str,
        actor_user_id: str,
    ) -> sqlite3.Row:
        row = con.execute(
            "SELECT * FROM workspace_collaboration_publication_intents "
            "WHERE id=? AND actor_subject_id=?",
            (intent_id, actor_user_id),
        ).fetchone()
        if row is None:
            raise PublicationIntentNotFound("publication intent not found")
        return row

    @staticmethod
    def _event(
        con: sqlite3.Connection,
        *,
        intent_id: str,
        event_type: str,
        state: str,
        actor_user_id: str,
        operation_id: str,
        request_sha256: str,
        details: Mapping[str, object],
        now: int,
    ) -> None:
        row = con.execute(
            "SELECT COALESCE(MAX(event_sequence),0)+1 "
            "FROM workspace_collaboration_publication_intent_events WHERE intent_id=?",
            (intent_id,),
        ).fetchone()
        sequence = int(row[0])
        details_json = _canonical_json(dict(details))
        con.execute(
            "INSERT INTO workspace_collaboration_publication_intent_events("
            "intent_id,event_sequence,event_type,state,actor_subject_id,operation_id,"
            "request_sha256,details_json,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                intent_id,
                sequence,
                event_type,
                state,
                actor_user_id,
                operation_id,
                request_sha256,
                details_json,
                now,
            ),
        )
        payload = {
            "contract": PUBLICATION_INTENT_CONTRACT,
            "details": dict(details),
            "event_sequence": sequence,
            "event_type": event_type,
            "intent_id": intent_id,
            "state": state,
        }
        payload_json = _canonical_json(payload)
        con.execute(
            "INSERT INTO workspace_collaboration_publication_outbox("
            "id,intent_id,event_sequence,topic,payload_sha256,payload_json,"
            "attempt_count,next_attempt_at,delivered_at,created_at,updated_at) "
            "VALUES(?,?,?,'notion.second_brain.publication_intent',?,?,0,?,NULL,?,?)",
            (
                str(uuid5(_OUTBOX_NAMESPACE, f"{intent_id}:{sequence}")),
                intent_id,
                sequence,
                _sha256(payload_json),
                payload_json,
                now,
                now,
                now,
            ),
        )

    def _validate_source_on_connection(
        self,
        con: sqlite3.Connection,
        binding: BoundNotionRevisionSnapshot,
    ) -> None:
        subject = self._provider._require_active(binding.actor_user_id)
        document = self._provider._document(con, binding.page_id)
        workspace = self._provider._workspace(con, str(document["workspace_id"]))
        self._provider._exact_access(
            con,
            subject,
            binding.page_id,
            minimum_role="edit_content",
            expected_workspace_epoch=binding.workspace_epoch,
            expected_document_epoch=binding.document_epoch,
        )
        if (
            str(workspace["id"]) != binding.workspace_id
            or int(document["revision"]) != binding.revision
            or int(document["document_sequence"]) != binding.document_sequence
        ):
            raise PublicationSourceConflict("the selected Notion source is stale")
        checkpoint = con.execute(
            "SELECT id,document_sequence,content_sha256 FROM "
            "workspace_collaboration_revision_checkpoints "
            "WHERE document_id=? AND revision=?",
            (binding.page_id, binding.revision),
        ).fetchone()
        revision = con.execute(
            "SELECT document_sequence,content_sha256 FROM "
            "workspace_collaboration_revisions WHERE document_id=? AND revision=?",
            (binding.page_id, binding.revision),
        ).fetchone()
        if (
            checkpoint is None
            or revision is None
            or str(checkpoint["id"]) != binding.history_checkpoint_id
            or int(checkpoint["document_sequence"]) != binding.document_sequence
            or int(revision["document_sequence"]) != binding.document_sequence
            or str(checkpoint["content_sha256"])
            != binding.canonical_content_sha256
            or str(revision["content_sha256"])
            != binding.canonical_content_sha256
            or str(document["content_sha256"])
            != binding.canonical_content_sha256
        ):
            raise PublicationSourceConflict("the selected source fence is inconsistent")

    @staticmethod
    def _binding_from_record(
        record: PublicationIntentRecord,
        current: BoundNotionRevisionSnapshot,
    ) -> BoundNotionRevisionSnapshot:
        if (
            current.workspace_id != record.workspace_id
            or current.page_id != record.page_id
            or current.actor_user_id != record.actor_user_id
            or current.revision != record.source_revision
            or current.document_sequence != record.source_document_sequence
            or current.workspace_epoch != record.source_workspace_epoch
            or current.document_epoch != record.source_document_epoch
            or current.history_checkpoint_id != record.source_history_checkpoint_id
            or current.canonical_content_sha256
            != record.source_canonical_content_sha256
            or current.content_sha256 != record.source_content_sha256
            or current.asset_manifest_sha256 != record.source_asset_manifest_sha256
            or current.source_snapshot_sha256 != record.source_snapshot_sha256
            or current.source_fence_sha256 != record.source_fence_sha256
        ):
            raise PublicationSourceConflict("current source does not match the intent")
        return current

    @staticmethod
    def _prepare_request_hash(
        actor_user_id: str,
        preview_operation_id: str,
        binding: BoundNotionRevisionSnapshot,
    ) -> str:
        return _sha256(
            _canonical_json(
                {
                    "actor_user_id": actor_user_id,
                    "operation_id": preview_operation_id,
                    "operation_type": "notion.second_brain.publication_intent.prepare",
                    "page_id": binding.page_id,
                    "schema_version": 1,
                    "source_fence_sha256": binding.source_fence_sha256,
                    "source_snapshot_sha256": binding.source_snapshot_sha256,
                }
            )
        )

    def prepare(
        self, command: PreparePublicationIntentCommand
    ) -> PublicationIntentRecord:
        if not isinstance(command, PreparePublicationIntentCommand):
            raise TypeError("PreparePublicationIntentCommand is required")
        actor = _text(command.actor_user_id, field_name="actor_user_id")
        page = _text(command.page_id, field_name="page_id")
        operation = _uuid(command.preview_operation_id, field_name="preview_operation_id")
        binding = self._provider.capture_bound(actor_user_id=actor, page_id=page)
        request_hash = self._prepare_request_hash(actor, operation, binding)
        intent_id = str(uuid5(_INTENT_NAMESPACE, f"{actor}:{page}:{operation}"))
        now = int(time.time())
        con = self._provider._connect(query_only=False)
        try:
            con.execute("BEGIN IMMEDIATE")
            existing = con.execute(
                "SELECT * FROM workspace_collaboration_publication_intents "
                "WHERE actor_subject_id=? AND preview_operation_id=?",
                (actor, operation),
            ).fetchone()
            if existing is not None:
                record = self._record(existing)
                if (
                    record.intent_id != intent_id
                    or record.preview_request_sha256 != request_hash
                ):
                    raise PublicationIntentConflict(
                        "preview operation identity is already in use"
                    )
                con.rollback()
                return record
            self._validate_source_on_connection(con, binding)
            con.execute(
                "INSERT INTO workspace_collaboration_publication_intents("
                "id,workspace_id,document_id,actor_subject_id,state,source_revision,"
                "source_document_sequence,source_workspace_epoch,source_document_epoch,"
                "source_history_checkpoint_id,source_canonical_content_sha256,"
                "source_content_sha256,source_asset_manifest_sha256,"
                "source_snapshot_sha256,source_fence_sha256,preview_operation_id,"
                "preview_request_sha256,row_version,created_at,updated_at) "
                "VALUES(?,?,?,?,'prepared',?,?,?,?,?,?,?,?,?,?,?,?,1,?,?)",
                (
                    intent_id,
                    binding.workspace_id,
                    binding.page_id,
                    actor,
                    binding.revision,
                    binding.document_sequence,
                    binding.workspace_epoch,
                    binding.document_epoch,
                    binding.history_checkpoint_id,
                    binding.canonical_content_sha256,
                    binding.content_sha256,
                    binding.asset_manifest_sha256,
                    binding.source_snapshot_sha256,
                    binding.source_fence_sha256,
                    operation,
                    request_hash,
                    now,
                    now,
                ),
            )
            self._event(
                con,
                intent_id=intent_id,
                event_type="intent.prepared",
                state="prepared",
                actor_user_id=actor,
                operation_id=operation,
                request_sha256=request_hash,
                details={
                    "document_sequence": binding.document_sequence,
                    "page_id": page,
                    "revision": binding.revision,
                    "source_fence_sha256": binding.source_fence_sha256,
                },
                now=now,
            )
            con.commit()
            row = self._load(con, intent_id, actor)
            return self._record(row)
        except Exception:
            if con.in_transaction:
                con.rollback()
            raise
        finally:
            con.close()

    def get(self, *, actor_user_id: str, intent_id: str) -> PublicationIntentRecord:
        actor = _text(actor_user_id, field_name="actor_user_id")
        intent = _uuid(intent_id, field_name="intent_id")
        con = self._provider._connect(query_only=True)
        try:
            return self._record(self._load(con, intent, actor))
        finally:
            con.close()

    @staticmethod
    def _approval_request_hash(
        command: ApprovePublicationIntentCommand,
        binding: BoundNotionRevisionSnapshot,
    ) -> str:
        return canonical_publication_request_sha256(
            actor_user_id=command.actor_user_id,
            actor_scope_epoch=command.actor_scope_epoch,
            operation_id=command.operation_id,
            source=binding,
            target_mode=command.target_mode,
            personal_space_id=command.personal_space_id,
            target_descriptor=_target_mapping(command.target),
        )

    @staticmethod
    def _validate_target(command: ApprovePublicationIntentCommand) -> None:
        if command.target_mode not in {"personal", "part", "both", "explicit"}:
            raise ValueError("target_mode is invalid")
        if (command.target_mode == "explicit") != (command.target is not None):
            raise ValueError("target is required exactly for explicit mode")
        if command.personal_space_id is not None:
            _uuid(command.personal_space_id, field_name="personal_space_id")
            if command.target_mode not in {"personal", "both"}:
                raise ValueError("personal_space_id is valid only for personal or both")
        epoch = command.actor_scope_epoch
        if isinstance(epoch, bool) or not isinstance(epoch, int) or epoch < 1:
            raise ValueError("actor_scope_epoch must be a positive integer")

    def approve(
        self, command: ApprovePublicationIntentCommand
    ) -> PublicationIntentRecord:
        if not isinstance(command, ApprovePublicationIntentCommand):
            raise TypeError("ApprovePublicationIntentCommand is required")
        self._validate_target(command)
        actor = _text(command.actor_user_id, field_name="actor_user_id")
        intent_id = _uuid(command.intent_id, field_name="intent_id")
        request_id = _uuid(command.request_id, field_name="request_id")
        operation_id = _uuid(command.operation_id, field_name="operation_id")
        normalized = ApprovePublicationIntentCommand(
            request_id=request_id,
            operation_id=operation_id,
            actor_user_id=actor,
            actor_scope_epoch=command.actor_scope_epoch,
            intent_id=intent_id,
            target_mode=command.target_mode,
            target=command.target,
            personal_space_id=command.personal_space_id,
            request_meta=dict(command.request_meta),
        )
        record = self.get(actor_user_id=actor, intent_id=intent_id)
        if record.state == "canceled":
            raise PublicationIntentConflict("a canceled intent cannot be approved")
        if record.state in {"sbe_committing", "reconciliation_required", "committed"}:
            return self._resume_existing(normalized, record)

        current = self._provider.capture_bound(actor_user_id=actor, page_id=record.page_id)
        binding = self._binding_from_record(record, current)
        request_hash = self._approval_request_hash(normalized, binding)
        now = int(time.time())
        con = self._provider._connect(query_only=False)
        try:
            con.execute("BEGIN IMMEDIATE")
            row = self._load(con, intent_id, actor)
            durable = self._record(row)
            if durable.state != "prepared":
                con.rollback()
                return self._resume_existing(normalized, durable)
            self._validate_source_on_connection(con, binding)
            target_json = (
                None
                if normalized.target is None
                else _canonical_json(normalized.target.to_mapping())
            )
            con.execute(
                "UPDATE workspace_collaboration_publication_intents SET "
                "state='sbe_committing',approve_request_id=?,publish_operation_id=?,"
                "publication_request_sha256=?,sbe_actor_scope_epoch=?,target_mode=?,"
                "personal_space_id=?,target_descriptor_json=?,row_version=row_version+1,"
                "updated_at=? WHERE id=? AND state='prepared'",
                (
                    request_id,
                    operation_id,
                    request_hash,
                    normalized.actor_scope_epoch,
                    normalized.target_mode,
                    normalized.personal_space_id,
                    target_json,
                    now,
                    intent_id,
                ),
            )
            self._event(
                con,
                intent_id=intent_id,
                event_type="intent.approved",
                state="sbe_committing",
                actor_user_id=actor,
                operation_id=operation_id,
                request_sha256=request_hash,
                details={
                    "source_fence_sha256": binding.source_fence_sha256,
                    "target_mode": normalized.target_mode,
                },
                now=now,
            )
            con.commit()
        except Exception:
            if con.in_transaction:
                con.rollback()
            raise
        finally:
            con.close()
        return self._drive(intent_id=intent_id, actor_user_id=actor)

    def _resume_existing(
        self,
        command: ApprovePublicationIntentCommand,
        record: PublicationIntentRecord,
    ) -> PublicationIntentRecord:
        if (
            record.publish_operation_id != command.operation_id
            or record.sbe_actor_scope_epoch != command.actor_scope_epoch
            or record.target_mode != command.target_mode
            or record.personal_space_id != command.personal_space_id
            or _target_mapping(record.target) != _target_mapping(command.target)
        ):
            raise PublicationIntentConflict("approval operation identity is already in use")
        if record.state == "committed":
            return record
        return self._drive(intent_id=record.intent_id, actor_user_id=record.actor_user_id)

    def _drive(self, *, intent_id: str, actor_user_id: str) -> PublicationIntentRecord:
        record = self.get(actor_user_id=actor_user_id, intent_id=intent_id)
        if record.state == "committed":
            return record
        if record.state not in {"sbe_committing", "reconciliation_required"}:
            raise PublicationIntentConflict("intent is not eligible for reconciliation")
        assert record.approve_request_id is not None
        assert record.publish_operation_id is not None
        assert record.publication_request_sha256 is not None
        assert record.sbe_actor_scope_epoch is not None
        assert record.target_mode is not None

        try:
            preview = self._previews.create_notion_preview(
                CreateNotionPreviewCommand(
                    request_id=record.approve_request_id,
                    operation_id=record.preview_operation_id,
                    actor_user_id=record.actor_user_id,
                    actor_scope_epoch=record.sbe_actor_scope_epoch,
                    page_id=record.page_id,
                    expected_source_snapshot_sha256=record.source_snapshot_sha256,
                    target=record.target,
                    request_meta={
                        "notion_intent_id": record.intent_id,
                        "source_fence_sha256": record.source_fence_sha256,
                    },
                )
            )
            self._bind_preview(record, preview.preview_id)
            if preview.source_snapshot_sha256 is None:
                raise PublicationIntentConflict(
                    "ready SBE preview has no extracted source snapshot"
                )
            result = self._publications.publish(
                PublishPreviewCommand(
                    request_id=record.approve_request_id,
                    operation_id=record.publish_operation_id,
                    actor_user_id=record.actor_user_id,
                    actor_scope_epoch=record.sbe_actor_scope_epoch,
                    preview_id=preview.preview_id,
                    source_snapshot_sha256=preview.source_snapshot_sha256,
                    target_mode=record.target_mode,  # type: ignore[arg-type]
                    target=record.target,
                    personal_space_id=record.personal_space_id,
                    request_meta={
                        "notion_intent_id": record.intent_id,
                        "notion_publication_request_sha256": (
                            record.publication_request_sha256
                        ),
                    },
                )
            )
            self._hit("after_sbe_commit_before_notion_record")
            return self._record_commit(record, preview.preview_id, result)
        except PublicationResponseAmbiguous:
            raise
        except Exception as exc:
            self._mark_reconciliation_required(record, exc)
            raise PublicationResponseAmbiguous(
                "the SBE response is unresolved; retry the same durable intent"
            ) from exc

    def _bind_preview(self, record: PublicationIntentRecord, preview_id: str) -> None:
        if record.sbe_preview_id == preview_id:
            return
        now = int(time.time())
        con = self._provider._connect(query_only=False)
        try:
            con.execute("BEGIN IMMEDIATE")
            row = self._load(con, record.intent_id, record.actor_user_id)
            current = self._record(row)
            if current.state == "committed":
                con.rollback()
                return
            if current.state not in {"sbe_committing", "reconciliation_required"}:
                raise PublicationIntentConflict("intent cannot bind an SBE preview")
            if current.sbe_preview_id is not None and current.sbe_preview_id != preview_id:
                raise PublicationIntentConflict("SBE preview identity changed")
            if current.sbe_preview_id is None:
                con.execute(
                    "UPDATE workspace_collaboration_publication_intents SET "
                    "sbe_preview_id=?,state='sbe_committing',row_version=row_version+1,"
                    "updated_at=? WHERE id=?",
                    (preview_id, now, record.intent_id),
                )
                self._event(
                    con,
                    intent_id=record.intent_id,
                    event_type="sbe.preview_bound",
                    state="sbe_committing",
                    actor_user_id=record.actor_user_id,
                    operation_id=record.preview_operation_id,
                    request_sha256=record.preview_request_sha256,
                    details={"sbe_preview_id": preview_id},
                    now=now,
                )
            con.commit()
        except Exception:
            if con.in_transaction:
                con.rollback()
            raise
        finally:
            con.close()

    def _record_commit(
        self,
        record: PublicationIntentRecord,
        preview_id: str,
        result: PublicationResult,
    ) -> PublicationIntentRecord:
        result_json = _canonical_json(result.to_mapping())
        now = int(time.time())
        con = self._provider._connect(query_only=False)
        try:
            con.execute("BEGIN IMMEDIATE")
            row = self._load(con, record.intent_id, record.actor_user_id)
            current = self._record(row)
            if current.state == "committed":
                if current.publication_result != result.to_mapping():
                    raise PublicationIntentConflict("committed SBE result changed")
                con.rollback()
                return current
            if current.state not in {"sbe_committing", "reconciliation_required"}:
                raise PublicationIntentConflict("intent cannot record an SBE commit")
            con.execute(
                "UPDATE workspace_collaboration_publication_intents SET "
                "state='committed',sbe_preview_id=?,publication_result_json=?,"
                "terminal_reason='sbe_publication_committed',row_version=row_version+1,"
                "updated_at=? WHERE id=?",
                (preview_id, result_json, now, record.intent_id),
            )
            self._event(
                con,
                intent_id=record.intent_id,
                event_type="sbe.committed",
                state="committed",
                actor_user_id=record.actor_user_id,
                operation_id=record.publish_operation_id or "missing",
                request_sha256=record.publication_request_sha256 or "sha256:" + "0" * 64,
                details={
                    "publication_operation_id": result.publication_operation_id,
                    "sbe_preview_id": preview_id,
                    "target_count": len(result.targets),
                },
                now=now,
            )
            con.commit()
            return self._record(self._load(con, record.intent_id, record.actor_user_id))
        except Exception:
            if con.in_transaction:
                con.rollback()
            raise
        finally:
            con.close()

    def _mark_reconciliation_required(
        self,
        record: PublicationIntentRecord,
        error_value: BaseException,
    ) -> None:
        now = int(time.time())
        con = self._provider._connect(query_only=False)
        try:
            con.execute("BEGIN IMMEDIATE")
            row = self._load(con, record.intent_id, record.actor_user_id)
            current = self._record(row)
            if current.state == "sbe_committing":
                con.execute(
                    "UPDATE workspace_collaboration_publication_intents SET "
                    "state='reconciliation_required',row_version=row_version+1,"
                    "updated_at=? WHERE id=?",
                    (now, record.intent_id),
                )
                self._event(
                    con,
                    intent_id=record.intent_id,
                    event_type="sbe.commit_ambiguous",
                    state="reconciliation_required",
                    actor_user_id=record.actor_user_id,
                    operation_id=record.publish_operation_id or "missing",
                    request_sha256=(
                        record.publication_request_sha256 or "sha256:" + "0" * 64
                    ),
                    details={"error_type": type(error_value).__name__},
                    now=now,
                )
            con.commit()
        except Exception:
            if con.in_transaction:
                con.rollback()
            raise
        finally:
            con.close()

    def recover(
        self, *, actor_user_id: str, intent_id: str
    ) -> PublicationIntentRecord:
        actor = _text(actor_user_id, field_name="actor_user_id")
        intent = _uuid(intent_id, field_name="intent_id")
        return self._drive(intent_id=intent, actor_user_id=actor)

    def cancel(
        self, command: CancelPublicationIntentCommand
    ) -> PublicationIntentRecord:
        if not isinstance(command, CancelPublicationIntentCommand):
            raise TypeError("CancelPublicationIntentCommand is required")
        actor = _text(command.actor_user_id, field_name="actor_user_id")
        intent = _uuid(command.intent_id, field_name="intent_id")
        operation = _uuid(command.operation_id, field_name="operation_id")
        request_hash = _sha256(
            _canonical_json(
                {
                    "actor_user_id": actor,
                    "intent_id": intent,
                    "operation_id": operation,
                    "operation_type": "notion.second_brain.publication_intent.cancel",
                    "schema_version": 1,
                }
            )
        )
        now = int(time.time())
        con = self._provider._connect(query_only=False)
        try:
            con.execute("BEGIN IMMEDIATE")
            current = self._record(self._load(con, intent, actor))
            if current.state == "canceled":
                if (
                    current.cancel_operation_id != operation
                    or current.cancel_request_sha256 != request_hash
                ):
                    raise PublicationIntentConflict("cancel operation identity is in use")
                con.rollback()
                return current
            if current.state != "prepared":
                raise PublicationIntentConflict(
                    "only a prepared Notion-only intent can be canceled"
                )
            con.execute(
                "UPDATE workspace_collaboration_publication_intents SET "
                "state='canceled',cancel_operation_id=?,cancel_request_sha256=?,"
                "terminal_reason='user_canceled',row_version=row_version+1,updated_at=? "
                "WHERE id=?",
                (operation, request_hash, now, intent),
            )
            self._event(
                con,
                intent_id=intent,
                event_type="intent.canceled",
                state="canceled",
                actor_user_id=actor,
                operation_id=operation,
                request_sha256=request_hash,
                details={"reason": "user_canceled"},
                now=now,
            )
            con.commit()
            return self._record(self._load(con, intent, actor))
        except Exception:
            if con.in_transaction:
                con.rollback()
            raise
        finally:
            con.close()

    def complete_source_revocation(
        self,
        *,
        actor_user_id: str,
        page_id: str,
        operation_id: str,
    ) -> dict[str, object]:
        """Fence prepared intents after IAM revoke; never mutate committed SBE items."""

        actor = _text(actor_user_id, field_name="actor_user_id")
        page = _text(page_id, field_name="page_id")
        operation = _uuid(operation_id, field_name="operation_id")
        now = int(time.time())
        con = self._provider._connect(query_only=False)
        try:
            con.execute("BEGIN IMMEDIATE")
            subject = self._provider._require_active(actor)
            document = self._provider._document(con, page)
            workspace = self._provider._workspace(con, str(document["workspace_id"]))
            role, _sources = CollaborativeIAMAuthority._resolution_on_connection(
                con, subject, str(workspace["id"]), page
            )
            if role is not None and PAGE_ROLE_LEVEL[role] >= PAGE_ROLE_LEVEL["edit_content"]:
                raise PublicationSourceStillAuthorized(
                    "source edit_content authority is still active"
                )
            rows = con.execute(
                "SELECT * FROM workspace_collaboration_publication_intents "
                "WHERE actor_subject_id=? AND document_id=? ORDER BY created_at,id",
                (actor, page),
            ).fetchall()
            unresolved = tuple(
                str(row["id"])
                for row in rows
                if str(row["state"])
                in {"sbe_committing", "reconciliation_required"}
            )
            if unresolved:
                raise PublicationRevocationPending(unresolved)
            canceled: list[str] = []
            committed: list[str] = []
            for row in rows:
                current = self._record(row)
                if current.state == "committed":
                    committed.append(current.intent_id)
                    continue
                if current.state != "prepared":
                    continue
                cancel_operation = str(
                    uuid5(_REVOKE_CANCEL_NAMESPACE, f"{operation}:{current.intent_id}")
                )
                request_hash = _sha256(
                    _canonical_json(
                        {
                            "actor_user_id": actor,
                            "intent_id": current.intent_id,
                            "operation_id": cancel_operation,
                            "operation_type": "notion.second_brain.source_revocation",
                            "schema_version": 1,
                        }
                    )
                )
                con.execute(
                    "UPDATE workspace_collaboration_publication_intents SET "
                    "state='canceled',cancel_operation_id=?,cancel_request_sha256=?,"
                    "terminal_reason='source_authority_revoked',"
                    "row_version=row_version+1,updated_at=? WHERE id=?",
                    (cancel_operation, request_hash, now, current.intent_id),
                )
                self._event(
                    con,
                    intent_id=current.intent_id,
                    event_type="intent.canceled",
                    state="canceled",
                    actor_user_id=actor,
                    operation_id=cancel_operation,
                    request_sha256=request_hash,
                    details={"reason": "source_authority_revoked"},
                    now=now,
                )
                canceled.append(current.intent_id)
            con.commit()
            return {
                "contract": PUBLICATION_INTENT_CONTRACT,
                "actor_user_id": actor,
                "page_id": page,
                "revocation_complete": True,
                "canceled_prepared_intent_ids": canceled,
                "committed_intent_ids_unchanged": committed,
                "unresolved_intent_ids": [],
            }
        except Exception:
            if con.in_transaction:
                con.rollback()
            raise
        finally:
            con.close()


__all__ = [
    "ApprovePublicationIntentCommand",
    "CancelPublicationIntentCommand",
    "PreparePublicationIntentCommand",
    "PUBLICATION_INTENT_CONTRACT",
    "PublicationIntentConflict",
    "PublicationIntentError",
    "PublicationIntentNotFound",
    "PublicationIntentRecord",
    "PublicationResponseAmbiguous",
    "PublicationRevocationPending",
    "PublicationSourceStillAuthorized",
    "SecondBrainPublicationCoordinator",
]
