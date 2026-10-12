"""Reviewed, dual-fenced transactional merge service.

Preview is a read-only fixed-revision view.  Apply repeats the exact confirmed
Markdown, recomputes the preview outside the write transaction, validates both
held leases in sorted item-ID order, and commits one target revision plus the
source redirect without automatically moving semantic edges.
"""

from __future__ import annotations

import base64
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from hashlib import sha256
import re
from typing import Literal
from uuid import UUID, uuid5

from ..db import ConnectionFactory, ManagedConnection, TransactionMode, db_now_ms, transaction
from ..repositories.audit import AuditEventDraft, AuditRepository
from ..repositories.jobs import (
    JobEffectConflictError,
    JobEnqueueDraft,
    JobRepository,
    JobRepositoryError,
    JobStateConflictError,
    canonical_job_input_sha256,
)
from ..repositories.leases import (
    LeaseIntegrityError,
    LeaseItemNotFoundError,
    LeaseLostRepositoryError,
)
from ..repositories.merges import (
    MergeApplyDraft,
    MergeConflictError,
    MergeIntegrityError,
    MergeNotFoundError,
    MergeReadSnapshot,
    MergeRepository,
    MergeWriteResult,
    canonical_json_bytes,
    sha256_canonical,
)
from ..repositories.outbox import OutboxEventDraft, OutboxRepository
from ..repositories.users import UserRecord
from ..scope import Capability, ScopeResolver, SessionPrincipal
from .editing import (
    DEFAULT_ABSOLUTE_AFTER_MS,
    DEFAULT_STALE_AFTER_MS,
    EditFenceCheck,
    FencedLeaseService,
    LeaseAuthorizationError,
    LeaseHmacKeyRing,
    LeaseIdempotencyError,
    LeaseLostError,
    LeaseNotFoundError,
    LeaseOperationInProgressError,
    LeaseRevisionConflictError,
    LeaseServiceError,
    LeaseServiceIntegrityError,
    LeaseValidationError,
)


_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.ASCII,
)
_SHA_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{43}$", re.ASCII)
_MAX_MARKDOWN_CHARACTERS = 2_000_000
_MERGE_RULE_VERSION = "reviewed-merge-v1"
_RESCAN_RULE_VERSION = "curation-rescan-v1"
_MERGE_DEDUP_NAMESPACE = UUID("2ecbeac4-119f-40ef-a318-9a5dd8384eb3")
_MERGE_REVISION_NAMESPACE = UUID("f411334a-c32f-4a47-a743-1233b2970545")
_MERGE_TRANSITION_NAMESPACE = UUID("c80d7aa7-ccf0-4962-a91f-af89c27d175c")
_MERGE_JOB_NAMESPACE = UUID("fd2400e4-5c70-487c-be5e-67dac2672f94")
_MERGE_JOB_DEDUP_NAMESPACE = UUID("bd30993e-ef72-4c86-a770-6c066f60040e")
_MERGE_RELATION_NAMESPACE = UUID("0c2f8bea-a011-4c96-b70c-86ca3e7e44a0")
_MERGE_EVENT_NAMESPACE = UUID("30fb71e5-59f5-4c1a-9856-6785279b6170")
_MERGE_AUDIT_NAMESPACE = UUID("91b960c9-3913-4cba-b6b4-afd7fe5e65a0")
_REQUEST_META_ALLOWLIST = frozenset(
    {"remote_addr", "method", "path", "host", "user_agent"}
)
_SENSITIVE_META_FRAGMENTS = (
    "authorization",
    "cookie",
    "csrf",
    "lease",
    "password",
    "secret",
    "token",
)


class MergeServiceIntegrityError(LeaseServiceIntegrityError):
    """Canonical merge evidence or effect failed closed."""


class MergePreviewStaleError(LeaseRevisionConflictError):
    """The fixed preview no longer matches the requested apply."""


@dataclass(frozen=True, slots=True)
class MergePreviewCommand:
    principal: SessionPrincipal
    space_id: str
    source_item_id: str
    source_revision_id: str
    target_item_id: str
    target_revision_id: str
    result_markdown: str = field(repr=False)
    request_id: str = ""


@dataclass(frozen=True, slots=True)
class MergeDiffPane:
    label: Literal["merge_source", "merge_target", "result"]
    item_id: str
    revision_id: str
    content_sha256: str
    markdown: str = field(repr=False)

    def as_dict(self) -> dict[str, object]:
        return {
            "content_sha256": self.content_sha256,
            "item_id": self.item_id,
            "label": self.label,
            "markdown": self.markdown,
            "revision_id": self.revision_id,
        }


@dataclass(frozen=True, slots=True)
class MergePreviewResult:
    request_id: str
    source_revision_id: str
    target_revision_id: str
    panes: tuple[MergeDiffPane, MergeDiffPane, MergeDiffPane]
    edge_impact: Mapping[str, object]
    reference_impact: Mapping[str, object]
    preview_sha256: str
    stale: Literal[False] = False

    def as_dict(self) -> dict[str, object]:
        return {
            "edge_impact": dict(self.edge_impact),
            "panes": [pane.as_dict() for pane in self.panes],
            "preview_sha256": self.preview_sha256,
            "reference_impact": dict(self.reference_impact),
            "request_id": self.request_id,
            "source_revision_id": self.source_revision_id,
            "stale": self.stale,
            "target_revision_id": self.target_revision_id,
        }


@dataclass(frozen=True, slots=True)
class MergeApplyCommand:
    principal: SessionPrincipal
    space_id: str
    source_item_id: str
    source_revision_id: str
    target_item_id: str
    target_revision_id: str
    result_markdown: str = field(repr=False)
    preview_sha256: str = ""
    source_editor_session_id: str = ""
    source_lease_id: str = ""
    source_fencing_generation: int = 0
    source_raw_token: str = field(default="", repr=False)
    target_editor_session_id: str = ""
    target_lease_id: str = ""
    target_fencing_generation: int = 0
    target_raw_token: str = field(default="", repr=False)
    operation_id: str = ""
    request_id: str = ""
    request_meta: Mapping[str, object] = field(default_factory=dict, repr=False)


@dataclass(frozen=True, slots=True)
class MergeApplyResult:
    request_id: str
    operation_id: str
    merge_event_id: str
    source_item_id: str
    target_item_id: str
    result_revision_id: str
    redirect_relation_id: str
    status: Literal["applied"] = "applied"

    def as_dict(self) -> dict[str, object]:
        return {
            "merge_event_id": self.merge_event_id,
            "operation_id": self.operation_id,
            "redirect_relation_id": self.redirect_relation_id,
            "request_id": self.request_id,
            "result_revision_id": self.result_revision_id,
            "source_item_id": self.source_item_id,
            "status": self.status,
            "target_item_id": self.target_item_id,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "MergeApplyResult":
        if set(value) != {
            "merge_event_id",
            "operation_id",
            "redirect_relation_id",
            "request_id",
            "result_revision_id",
            "source_item_id",
            "status",
            "target_item_id",
        } or value.get("status") != "applied":
            raise ValueError("Stored merge result shape is invalid.")
        return cls(
            request_id=_uuid(value["request_id"], "request_id"),
            operation_id=_uuid(value["operation_id"], "operation_id"),
            merge_event_id=_uuid(value["merge_event_id"], "merge_event_id"),
            source_item_id=_uuid(value["source_item_id"], "source_item_id"),
            target_item_id=_uuid(value["target_item_id"], "target_item_id"),
            result_revision_id=_uuid(value["result_revision_id"], "result_revision_id"),
            redirect_relation_id=_uuid(
                value["redirect_relation_id"], "redirect_relation_id"
            ),
        )


@dataclass(frozen=True, slots=True)
class _PreparedMarkdown:
    markdown: str = field(repr=False)
    raw_sha256: str
    normalized_sha256: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class _TokenBinding:
    dedup_id: str
    source_key_id: str
    source_hmac: bytes = field(repr=False)
    target_key_id: str
    target_hmac: bytes = field(repr=False)
    request_sha256: str


def _uuid(value: object, field_name: str) -> str:
    if not isinstance(value, str) or _UUID_RE.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a canonical lowercase UUID.")
    return value


def _sha(value: object, field_name: str) -> str:
    if not isinstance(value, str) or _SHA_RE.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a lowercase SHA-256 digest.")
    return value


def _positive(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{field_name} must be a positive integer.")
    return value


def _derived_uuid(namespace: UUID, *parts: str) -> str:
    return str(uuid5(namespace, "\x1f".join(parts)))


def _decode_token(value: object) -> bytes:
    if not isinstance(value, str) or _TOKEN_RE.fullmatch(value) is None:
        raise LeaseValidationError(
            "Each merge lease token must be 43 unpadded base64url characters."
        )
    try:
        raw = base64.b64decode(value + "=", altchars=b"-_", validate=True)
    except (TypeError, ValueError) as exc:
        raise LeaseValidationError("A merge lease token is invalid.") from exc
    if len(raw) != 32 or base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii") != value:
        raise LeaseValidationError("A merge lease token must decode to 32 bytes.")
    return raw


def _prepare_markdown(value: object) -> _PreparedMarkdown:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= _MAX_MARKDOWN_CHARACTERS
        or "\x00" in value
    ):
        raise LeaseValidationError(
            "Result Markdown must contain 1..2000000 non-NUL characters."
        )
    try:
        raw = value.encode("utf-8")
        normalized = value.replace("\r\n", "\n").replace("\r", "\n").encode(
            "utf-8"
        )
    except UnicodeEncodeError as exc:
        raise LeaseValidationError("Result Markdown must be valid Unicode.") from exc
    return _PreparedMarkdown(
        value,
        sha256(raw).hexdigest(),
        sha256(normalized).hexdigest(),
        len(raw),
    )


def _safe_request_meta(
    value: object, *, secret_values: tuple[str, ...]
) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError("request_meta must be a mapping.")
    safe: dict[str, object] = {}
    for key, raw in value.items():
        if not isinstance(key, str):
            raise ValueError("request_meta keys must be strings.")
        folded = key.casefold()
        if any(fragment in folded for fragment in _SENSITIVE_META_FRAGMENTS):
            continue
        if key not in _REQUEST_META_ALLOWLIST:
            continue
        if raw is None:
            safe[key] = None
        elif isinstance(raw, str) and len(raw) <= 1000:
            if any(secret and secret in raw for secret in secret_values):
                continue
            safe[key] = raw
        else:
            raise ValueError("Allowed request metadata must be bounded text or null.")
    return safe


def _materialize(
    item,
    loader: Callable[[str, int], bytes | str] | None,
) -> str:
    if item.content_md is not None:
        try:
            encoded = item.content_md.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise MergeServiceIntegrityError("A merge body is not valid Unicode.") from exc
        markdown = item.content_md
    else:
        if item.content_blob_hash is None or loader is None:
            raise MergeServiceIntegrityError(
                "Blob-backed merge content is unavailable to the preview service."
            )
        try:
            payload = loader(item.content_blob_hash, item.content_size_bytes)
        except Exception as exc:
            raise MergeServiceIntegrityError("The merge content loader failed.") from exc
        if isinstance(payload, str):
            markdown = payload
            encoded = payload.encode("utf-8")
        elif isinstance(payload, bytes):
            encoded = bytes(payload)
            try:
                markdown = encoded.decode("utf-8", errors="strict")
            except UnicodeDecodeError as exc:
                raise MergeServiceIntegrityError(
                    "Blob-backed merge content is not valid UTF-8."
                ) from exc
        else:
            raise MergeServiceIntegrityError(
                "The merge content loader returned an invalid value."
            )
        if item.content_blob_hash != item.raw_content_sha256:
            raise MergeServiceIntegrityError(
                "Blob-backed merge content is not bound to its revision."
            )
    if (
        len(encoded) != item.content_size_bytes
        or sha256(encoded).hexdigest() != item.raw_content_sha256
    ):
        raise MergeServiceIntegrityError(
            "Merge content does not match immutable revision evidence."
        )
    return markdown


class TransactionalMergeService(FencedLeaseService):
    """Read-only preview plus one atomic reviewed merge mutation."""

    __slots__ = ("_content_loader", "_jobs", "_merges")

    def __init__(
        self,
        factory: ConnectionFactory,
        *,
        key_ring: LeaseHmacKeyRing,
        scope_resolver: ScopeResolver,
        stale_after_ms: int = DEFAULT_STALE_AFTER_MS,
        absolute_after_ms: int = DEFAULT_ABSOLUTE_AFTER_MS,
        content_loader: Callable[[str, int], bytes | str] | None = None,
        merges: MergeRepository | None = None,
        jobs: JobRepository | None = None,
        audit: AuditRepository | None = None,
        outbox: OutboxRepository | None = None,
        failpoint: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__(
            factory,
            key_ring=key_ring,
            scope_resolver=scope_resolver,
            stale_after_ms=stale_after_ms,
            absolute_after_ms=absolute_after_ms,
            audit=audit,
            outbox=outbox,
            failpoint=failpoint,
        )
        self._content_loader = content_loader
        self._merges = merges or MergeRepository()
        self._jobs = jobs or JobRepository()

    @staticmethod
    def _validate_pair(
        *,
        principal: SessionPrincipal,
        space_id: str,
        source_item_id: str,
        source_revision_id: str,
        target_item_id: str,
        target_revision_id: str,
        request_id: str,
    ) -> None:
        FencedLeaseService._validate_common(
            principal=principal,
            space_id=space_id,
            item_id=source_item_id,
            request_id=request_id,
        )
        FencedLeaseService._validate_common(
            principal=principal,
            space_id=space_id,
            item_id=target_item_id,
            request_id=request_id,
        )
        _uuid(source_revision_id, "source_revision_id")
        _uuid(target_revision_id, "target_revision_id")
        if source_item_id == target_item_id:
            raise LeaseValidationError("A merge requires two different items.")

    def _read_authorized_snapshot(
        self,
        *,
        principal: SessionPrincipal,
        space_id: str,
        source_item_id: str,
        source_revision_id: str,
        target_item_id: str,
        target_revision_id: str,
    ) -> MergeReadSnapshot:
        connection = self._factory.open(read_only=True)
        try:
            with transaction(connection, TransactionMode.DEFERRED):
                self._resolve_scope(
                    connection,
                    principal=principal,
                    space_id=space_id,
                    capability=Capability.CURATE,
                )
                snapshot = self._merges.read_snapshot(
                    connection,
                    space_id=space_id,
                    source_item_id=source_item_id,
                    source_revision_id=source_revision_id,
                    target_item_id=target_item_id,
                    target_revision_id=target_revision_id,
                )
                if (
                    snapshot.source.lifecycle_status != "active"
                    or snapshot.target.lifecycle_status != "active"
                ):
                    raise MergePreviewStaleError(
                        "Only two active fixed revisions can be merged."
                    )
                return snapshot
        except MergeNotFoundError as exc:
            raise LeaseNotFoundError(str(exc)) from exc
        except MergeConflictError as exc:
            raise MergePreviewStaleError(
                str(exc),
                details={
                    "current_revision_id": exc.current_revision_id,
                    "current_row_version": exc.current_row_version,
                    "item_id": exc.item_id,
                },
            ) from exc
        except MergeIntegrityError as exc:
            raise MergeServiceIntegrityError(str(exc)) from exc
        finally:
            connection.close()

    @staticmethod
    def _build_preview(
        *,
        request_id: str,
        snapshot: MergeReadSnapshot,
        source_markdown: str,
        target_markdown: str,
        result: _PreparedMarkdown,
    ) -> MergePreviewResult:
        result_identity = sha256_canonical(
            {
                "result": {
                    "content_size_bytes": result.size_bytes,
                    "normalized_content_sha256": result.normalized_sha256,
                    "raw_content_sha256": result.raw_sha256,
                },
                "rule_version": _MERGE_RULE_VERSION,
                "snapshot": snapshot.binding(),
            }
        )
        result_revision_id = _derived_uuid(
            _MERGE_REVISION_NAMESPACE,
            snapshot.target.item_id,
            result_identity,
        )
        panes = (
            MergeDiffPane(
                "merge_source",
                snapshot.source.item_id,
                snapshot.source.current_revision_id,
                snapshot.source.raw_content_sha256,
                source_markdown,
            ),
            MergeDiffPane(
                "merge_target",
                snapshot.target.item_id,
                snapshot.target.current_revision_id,
                snapshot.target.raw_content_sha256,
                target_markdown,
            ),
            MergeDiffPane(
                "result",
                snapshot.target.item_id,
                result_revision_id,
                result.raw_sha256,
                result.markdown,
            ),
        )
        edge_impact = {
            "affected_edge_count": len(snapshot.edges),
            "edge_ids": [edge.id for edge in snapshot.edges],
            "evidence_sha256": snapshot.edge_impact_sha256,
            "policy": "inactive_needs_revalidation_no_rewire",
        }
        reference_impact = {
            "affected_reference_count": len(snapshot.references),
            "evidence_sha256": snapshot.reference_impact_sha256,
            "existing_relation_ids": [row.id for row in snapshot.references],
            "new_relation_type": "merged_into",
        }
        payload = {
            "edge_impact": edge_impact,
            "panes": [
                {
                    "content_sha256": pane.content_sha256,
                    "item_id": pane.item_id,
                    "label": pane.label,
                    "revision_id": pane.revision_id,
                }
                for pane in panes
            ],
            "reference_impact": reference_impact,
            "rule_version": _MERGE_RULE_VERSION,
            "snapshot": snapshot.binding(),
        }
        return MergePreviewResult(
            request_id=request_id,
            source_revision_id=snapshot.source.current_revision_id,
            target_revision_id=snapshot.target.current_revision_id,
            panes=panes,
            edge_impact=edge_impact,
            reference_impact=reference_impact,
            preview_sha256=sha256_canonical(payload),
        )

    def preview(self, command: MergePreviewCommand) -> MergePreviewResult:
        if not isinstance(command, MergePreviewCommand):
            raise TypeError("preview requires a MergePreviewCommand.")
        self._validate_pair(
            principal=command.principal,
            space_id=command.space_id,
            source_item_id=command.source_item_id,
            source_revision_id=command.source_revision_id,
            target_item_id=command.target_item_id,
            target_revision_id=command.target_revision_id,
            request_id=command.request_id,
        )
        result = _prepare_markdown(command.result_markdown)
        snapshot = self._read_authorized_snapshot(
            principal=command.principal,
            space_id=command.space_id,
            source_item_id=command.source_item_id,
            source_revision_id=command.source_revision_id,
            target_item_id=command.target_item_id,
            target_revision_id=command.target_revision_id,
        )
        source_markdown = _materialize(snapshot.source, self._content_loader)
        target_markdown = _materialize(snapshot.target, self._content_loader)
        # External body reads happen outside SQLite.  Re-open an authorized read
        # snapshot and prove that every canonical input/impact is unchanged.
        current = self._read_authorized_snapshot(
            principal=command.principal,
            space_id=command.space_id,
            source_item_id=command.source_item_id,
            source_revision_id=command.source_revision_id,
            target_item_id=command.target_item_id,
            target_revision_id=command.target_revision_id,
        )
        if current != snapshot:
            raise MergePreviewStaleError(
                "A fixed merge input changed while the preview was materialized."
            )
        return self._build_preview(
            request_id=command.request_id,
            snapshot=snapshot,
            source_markdown=source_markdown,
            target_markdown=target_markdown,
            result=result,
        )

    def _request_sha256(
        self,
        command: MergeApplyCommand,
        prepared: _PreparedMarkdown,
        *,
        source_key_id: str,
        source_hmac: bytes,
        target_key_id: str,
        target_hmac: bytes,
    ) -> str:
        return sha256(
            canonical_json_bytes(
                {
                    "actor_scope_epoch": command.principal.captured_scope_epoch,
                    "actor_user_id": command.principal.user_id,
                    "operation_type": "knowledge_item.merge",
                    "preview_sha256": command.preview_sha256,
                    "result": {
                        "content_size_bytes": prepared.size_bytes,
                        "normalizer_version": "markdown_lf_v1",
                        "normalized_content_sha256": prepared.normalized_sha256,
                        "raw_content_sha256": prepared.raw_sha256,
                    },
                    "schema_version": 1,
                    "source": {
                        "editor_session_id": command.source_editor_session_id,
                        "fencing_generation": command.source_fencing_generation,
                        "hmac_key_id": source_key_id,
                        "item_id": command.source_item_id,
                        "lease_id": command.source_lease_id,
                        "revision_id": command.source_revision_id,
                        "token_hmac_sha256": source_hmac.hex(),
                    },
                    "space_id": command.space_id,
                    "target": {
                        "editor_session_id": command.target_editor_session_id,
                        "fencing_generation": command.target_fencing_generation,
                        "hmac_key_id": target_key_id,
                        "item_id": command.target_item_id,
                        "lease_id": command.target_lease_id,
                        "revision_id": command.target_revision_id,
                        "token_hmac_sha256": target_hmac.hex(),
                    },
                }
            )
        ).hexdigest()

    def _token_binding(
        self,
        connection: ManagedConnection,
        command: MergeApplyCommand,
        prepared: _PreparedMarkdown,
        *,
        source_raw: bytes,
        target_raw: bytes,
    ) -> _TokenBinding:
        dedup_id = _derived_uuid(
            _MERGE_DEDUP_NAMESPACE,
            command.principal.user_id,
            command.operation_id,
        )
        existing = self._users.get_operation(connection, dedup_id)
        sources = list(
            self._keys.candidate_digests(
                item_id=command.source_item_id, raw_token=source_raw
            )
        )
        targets = list(
            self._keys.candidate_digests(
                item_id=command.target_item_id, raw_token=target_raw
            )
        )
        for source_key, source_hmac in sources:
            for target_key, target_hmac in targets:
                request_sha = self._request_sha256(
                    command,
                    prepared,
                    source_key_id=source_key,
                    source_hmac=source_hmac,
                    target_key_id=target_key,
                    target_hmac=target_hmac,
                )
                if existing is not None and existing.request_sha256 == request_sha:
                    return _TokenBinding(
                        dedup_id,
                        source_key,
                        source_hmac,
                        target_key,
                        target_hmac,
                        request_sha,
                    )
        try:
            source_lease = self._leases.require_item(
                connection,
                item_id=command.source_item_id,
                space_id=command.space_id,
            ).lease
            target_lease = self._leases.require_item(
                connection,
                item_id=command.target_item_id,
                space_id=command.space_id,
            ).lease
        except LeaseItemNotFoundError as exc:
            raise LeaseNotFoundError(str(exc)) from exc
        if source_lease.held and source_lease.hmac_key_id is not None:
            sources.sort(key=lambda pair: pair[0] != source_lease.hmac_key_id)
        if target_lease.held and target_lease.hmac_key_id is not None:
            targets.sort(key=lambda pair: pair[0] != target_lease.hmac_key_id)
        source_key, source_hmac = sources[0]
        target_key, target_hmac = targets[0]
        return _TokenBinding(
            dedup_id,
            source_key,
            source_hmac,
            target_key,
            target_hmac,
            self._request_sha256(
                command,
                prepared,
                source_key_id=source_key,
                source_hmac=source_hmac,
                target_key_id=target_key,
                target_hmac=target_hmac,
            ),
        )

    @staticmethod
    def _validate_replay(
        result: MergeApplyResult,
        command: MergeApplyCommand,
        *,
        result_revision_id: str,
        relation_id: str,
        merge_event_id: str,
    ) -> MergeApplyResult:
        if (
            result.operation_id != command.operation_id
            or result.source_item_id != command.source_item_id
            or result.target_item_id != command.target_item_id
            or result.result_revision_id != result_revision_id
            or result.redirect_relation_id != relation_id
            or result.merge_event_id != merge_event_id
        ):
            raise LeaseIdempotencyError(
                "The stored merge result is not bound to this command."
            )
        return replace(result, request_id=command.request_id)

    def _append_events(
        self,
        connection: ManagedConnection,
        *,
        actor: UserRecord,
        command: MergeApplyCommand,
        dedup_id: str,
        written: MergeWriteResult,
        snapshot: MergeReadSnapshot,
        safe_meta: Mapping[str, object],
        now_ms: int,
    ) -> None:
        details = {
            "edge_impact_sha256": snapshot.edge_impact_sha256,
            "merge_event_id": written.merge_event_id,
            "obsoleted_finding_count": written.obsoleted_finding_count,
            "obsoleted_proposal_count": written.obsoleted_proposal_count,
            "reference_impact_sha256": snapshot.reference_impact_sha256,
            "relation_id": written.relation_id,
            "removed_search_document_count": written.removed_search_document_count,
            "rescan_job_id": written.rescan_job_id,
            "revalidation_edge_count": written.revalidation_edge_count,
            "schema_version": 1,
            "source_fencing_generation": command.source_fencing_generation,
            "source_item_id": command.source_item_id,
            "source_revision_id": command.source_revision_id,
            "space_generation": written.space_generation,
            "target_fencing_generation": command.target_fencing_generation,
            "target_item_id": command.target_item_id,
            "target_revision_id": command.target_revision_id,
            "transition_id": written.transition_id,
        }
        self._audit.append(
            connection,
            AuditEventDraft(
                event_uuid=_derived_uuid(_MERGE_AUDIT_NAMESPACE, dedup_id),
                occurred_at_ms=now_ms,
                request_id=command.request_id,
                operation_id=command.operation_id,
                action="knowledge_item.merge",
                outcome="applied",
                actor_kind="user",
                actor_user_id=actor.id,
                actor_display_name=actor.display_name,
                actor_role_snapshot=actor.system_role,
                space_id=command.space_id,
                item_id=command.target_item_id,
                base_revision_id=command.target_revision_id,
                new_revision_id=written.result_revision_id,
                reason="Reviewed merge application.",
                details=details,
                request_meta=safe_meta,
            ),
        )
        self._hit("merge.after_audit")
        self._outbox.append(
            connection,
            OutboxEventDraft(
                logical_key=f"knowledge-item:merged:{dedup_id}",
                space_id=command.space_id,
                aggregate_type="knowledge_item",
                aggregate_id=command.target_item_id,
                aggregate_version=written.target_row_version,
                event_type="knowledge_item.merged",
                payload={
                    "preview_sha256": command.preview_sha256,
                    "result_revision_id": written.result_revision_id,
                    **details,
                },
                available_at_ms=now_ms,
            ),
        )
        self._hit("merge.after_outbox")

    def _preflight_replay(
        self,
        command: MergeApplyCommand,
        prepared: _PreparedMarkdown,
        *,
        source_raw: bytes,
        target_raw: bytes,
        relation_id: str,
        merge_event_id: str,
    ) -> MergeApplyResult | None:
        """Return an exact terminal replay before current-state preview checks.

        A committed merge intentionally makes the source non-active and moves
        the target current revision, so current-state preview recomputation is
        valid only for a new operation.  The immutable operation request hash,
        result, relation, and merge event are the replay authority.
        """

        connection = self._factory.open(read_only=True)
        try:
            with transaction(connection, TransactionMode.DEFERRED):
                self._resolve_scope(
                    connection,
                    principal=command.principal,
                    space_id=command.space_id,
                    capability=Capability.CURATE,
                )
                binding = self._token_binding(
                    connection,
                    command,
                    prepared,
                    source_raw=source_raw,
                    target_raw=target_raw,
                )
                existing = self._users.get_operation(connection, binding.dedup_id)
                if existing is None:
                    return None
                if existing.request_sha256 != binding.request_sha256:
                    raise LeaseIdempotencyError(
                        "The merge operation ID is bound to a different request."
                    )
                if existing.status == "failed_final":
                    if existing.error is None:
                        raise LeaseIdempotencyError(
                            "The failed merge has no stored error."
                        )
                    raise self._replay_error(existing.error)
                if existing.status != "committed":
                    raise LeaseOperationInProgressError(
                        "The merge operation is already in progress."
                    )
                if existing.result is None:
                    raise LeaseIdempotencyError(
                        "The committed merge has no stored result."
                    )
                stored = MergeApplyResult.from_mapping(existing.result)
                if (
                    stored.operation_id != command.operation_id
                    or stored.source_item_id != command.source_item_id
                    or stored.target_item_id != command.target_item_id
                    or stored.redirect_relation_id != relation_id
                    or stored.merge_event_id != merge_event_id
                ):
                    raise LeaseIdempotencyError(
                        "The stored merge result is not bound to this command."
                    )
                closure = connection.execute(
                    """SELECT e.source_item_id,e.source_revision_id,e.target_item_id,
                              e.target_revision_id,e.result_revision_id,
                              e.preview_sha256,e.relation_id,r.relation_type,
                              r.relation_status,r.target_revision_id
                         FROM merge_event e JOIN item_relation r ON r.id=e.relation_id
                        WHERE e.id=? AND e.dedup_id=?""",
                    (merge_event_id, binding.dedup_id),
                ).fetchone()
                if closure is None or tuple(closure) != (
                    command.source_item_id,
                    command.source_revision_id,
                    command.target_item_id,
                    command.target_revision_id,
                    stored.result_revision_id,
                    command.preview_sha256,
                    relation_id,
                    "merged_into",
                    "active",
                    stored.result_revision_id,
                ):
                    raise LeaseIdempotencyError(
                        "The stored merge result has no exact canonical closure."
                    )
                return replace(stored, request_id=command.request_id)
        finally:
            connection.close()

    def apply(self, command: MergeApplyCommand) -> MergeApplyResult:
        if not isinstance(command, MergeApplyCommand):
            raise TypeError("apply requires a MergeApplyCommand.")
        self._validate_pair(
            principal=command.principal,
            space_id=command.space_id,
            source_item_id=command.source_item_id,
            source_revision_id=command.source_revision_id,
            target_item_id=command.target_item_id,
            target_revision_id=command.target_revision_id,
            request_id=command.request_id,
        )
        _uuid(command.operation_id, "operation_id")
        _sha(command.preview_sha256, "preview_sha256")
        for prefix in ("source", "target"):
            _uuid(getattr(command, f"{prefix}_editor_session_id"), f"{prefix}_editor_session_id")
            _uuid(getattr(command, f"{prefix}_lease_id"), f"{prefix}_lease_id")
            _positive(
                getattr(command, f"{prefix}_fencing_generation"),
                f"{prefix}_fencing_generation",
            )
        prepared = _prepare_markdown(command.result_markdown)
        source_raw = _decode_token(command.source_raw_token)
        target_raw = _decode_token(command.target_raw_token)
        safe_meta = _safe_request_meta(
            command.request_meta,
            secret_values=(command.source_raw_token, command.target_raw_token),
        )
        relation_id = _derived_uuid(
            _MERGE_RELATION_NAMESPACE,
            command.principal.user_id,
            command.operation_id,
        )
        merge_event_id = _derived_uuid(
            _MERGE_EVENT_NAMESPACE,
            command.principal.user_id,
            command.operation_id,
        )
        replay = self._preflight_replay(
            command,
            prepared,
            source_raw=source_raw,
            target_raw=target_raw,
            relation_id=relation_id,
            merge_event_id=merge_event_id,
        )
        if replay is not None:
            return replay
        recomputed = self.preview(
            MergePreviewCommand(
                principal=command.principal,
                space_id=command.space_id,
                source_item_id=command.source_item_id,
                source_revision_id=command.source_revision_id,
                target_item_id=command.target_item_id,
                target_revision_id=command.target_revision_id,
                result_markdown=command.result_markdown,
                request_id=command.request_id,
            )
        )
        if recomputed.preview_sha256 != command.preview_sha256:
            raise MergePreviewStaleError(
                "The merge preview hash no longer matches the confirmed result."
            )
        result_revision_id = recomputed.panes[2].revision_id
        transition_id = _derived_uuid(
            _MERGE_TRANSITION_NAMESPACE,
            command.principal.user_id,
            command.operation_id,
        )
        rescan_job_id = _derived_uuid(
            _MERGE_JOB_NAMESPACE,
            command.principal.user_id,
            command.operation_id,
        )
        job_dedup_id = _derived_uuid(
            _MERGE_JOB_DEDUP_NAMESPACE,
            command.principal.user_id,
            command.operation_id,
        )
        result: MergeApplyResult | None = None
        final_error: LeaseServiceError | None = None
        connection = self._factory.open()
        try:
            try:
                with transaction(connection):
                    actor, resolved = self._resolve_scope(
                        connection,
                        principal=command.principal,
                        space_id=command.space_id,
                        capability=Capability.CURATE,
                    )
                    now_ms = db_now_ms(connection)
                    binding = self._token_binding(
                        connection,
                        command,
                        prepared,
                        source_raw=source_raw,
                        target_raw=target_raw,
                    )
                    begun = self._begin(
                        connection,
                        actor=actor,
                        operation_type="knowledge_item.merge",
                        operation_id=command.operation_id,
                        request_hash=binding.request_sha256,
                        dedup_id=binding.dedup_id,
                        now_ms=now_ms,
                    )
                    if not begun.inserted:
                        if begun.operation.status == "committed":
                            stored = begun.operation.result
                            if stored is None:
                                raise LeaseIdempotencyError(
                                    "The committed merge has no result."
                                )
                            result = self._validate_replay(
                                MergeApplyResult.from_mapping(stored),
                                command,
                                result_revision_id=result_revision_id,
                                relation_id=relation_id,
                                merge_event_id=merge_event_id,
                            )
                        elif begun.operation.status == "failed_final":
                            stored_error = begun.operation.error
                            if stored_error is None:
                                raise LeaseIdempotencyError(
                                    "The failed merge has no stored error."
                                )
                            final_error = self._replay_error(stored_error)
                        else:
                            raise LeaseOperationInProgressError(
                                "The merge operation is already in progress."
                            )
                    else:
                        snapshot = self._merges.read_snapshot(
                            connection,
                            space_id=command.space_id,
                            source_item_id=command.source_item_id,
                            source_revision_id=command.source_revision_id,
                            target_item_id=command.target_item_id,
                            target_revision_id=command.target_revision_id,
                        )
                        fixed_preview = self._build_preview(
                            request_id=command.request_id,
                            snapshot=snapshot,
                            source_markdown=recomputed.panes[0].markdown,
                            target_markdown=recomputed.panes[1].markdown,
                            result=prepared,
                        )
                        if fixed_preview.preview_sha256 != command.preview_sha256:
                            raise MergeConflictError(
                                "The fixed merge inputs changed before the write transaction."
                            )
                        checks = {
                            command.source_item_id: (
                                command.source_editor_session_id,
                                command.source_lease_id,
                                command.source_fencing_generation,
                                command.source_revision_id,
                                snapshot.source.row_version,
                                command.source_raw_token,
                            ),
                            command.target_item_id: (
                                command.target_editor_session_id,
                                command.target_lease_id,
                                command.target_fencing_generation,
                                command.target_revision_id,
                                snapshot.target.row_version,
                                command.target_raw_token,
                            ),
                        }
                        try:
                            proofs = []
                            for item_id in sorted(checks):
                                session_id, lease_id, fence, revision_id, row_version, raw = checks[item_id]
                                proofs.append(
                                    self.validate_edit_fence_in_transaction(
                                        connection,
                                        EditFenceCheck(
                                            principal=command.principal,
                                            space_id=command.space_id,
                                            item_id=item_id,
                                            editor_session_id=session_id,
                                            lease_id=lease_id,
                                            fencing_generation=fence,
                                            expected_item_row_version=row_version,
                                            expected_revision_id=revision_id,
                                            raw_token=raw,
                                        ),
                                    )
                                )
                        except (
                            LeaseLostError,
                            LeaseRevisionConflictError,
                            LeaseNotFoundError,
                        ) as exc:
                            final_error = exc
                            self._fail_final(
                                connection,
                                dedup_id=binding.dedup_id,
                                error=exc,
                                now_ms=now_ms,
                            )
                        else:
                            if {proof.item_id for proof in proofs} != {
                                command.source_item_id,
                                command.target_item_id,
                            }:
                                raise MergeServiceIntegrityError(
                                    "The dual lease proofs are incomplete."
                                )
                            self._hit("merge.after_dual_acquire")
                            job_input = {
                                "item_id": command.target_item_id,
                                "revision_id": result_revision_id,
                                "rule_version": _RESCAN_RULE_VERSION,
                            }
                            job_begun = self._users.begin_operation(
                                connection,
                                actor_user_id=actor.id,
                                operation_type="job:curation.rescan",
                                operation_id=command.operation_id,
                                request_sha256=canonical_job_input_sha256(job_input),
                                dedup_id=job_dedup_id,
                                now_ms=now_ms,
                            )
                            if not job_begun.inserted:
                                raise LeaseIdempotencyError(
                                    "The merge rescan enqueue identity is already in use."
                                )
                            capabilities = tuple(
                                sorted(
                                    capability.value
                                    for capability in resolved.effective_capabilities
                                )
                            )
                            plan = resolved.access_plan
                            authorization_fence = (
                                None
                                if plan is None
                                else {
                                    "corpus_closure_generation": plan.corpus_closure.generation,
                                    "corpus_closure_sha256": plan.corpus_closure.sha256,
                                    "organization_snapshot_version": str(plan.corpus_closure.organization_snapshot_version),
                                    "native_mutation_space_id": plan.native_mutation_space_id,
                                    "role_assignment_generation": plan.role_assignment_generation,
                                    "role_assignment_id": plan.role_assignment_id,
                                }
                            )
                            job = self._jobs.enqueue(
                                connection,
                                JobEnqueueDraft(
                                    job_id=rescan_job_id,
                                    dedup_id=job_dedup_id,
                                    operation_id=command.operation_id,
                                    actor_kind="user",
                                    actor_user_id=actor.id,
                                    space_id=command.space_id,
                                    job_type="curation.rescan",
                                    handler_version="v1",
                                    effect_key=(
                                        f"curation-rescan:{command.target_item_id}:"
                                        f"{result_revision_id}"
                                    ),
                                    input_ref=job_input,
                                    expected_state={
                                        "_authorization_fence": authorization_fence,
                                        "base_revision_id": command.target_revision_id,
                                        "content_size_bytes": prepared.size_bytes,
                                        "normalizer_version": "markdown_lf_v1",
                                        "normalized_content_sha256": prepared.normalized_sha256,
                                        "preview_sha256": command.preview_sha256,
                                        "raw_content_sha256": prepared.raw_sha256,
                                        "result_revision_id": result_revision_id,
                                        "schema_version": 1,
                                        "source_item_id": command.source_item_id,
                                        "source_revision_id": command.source_revision_id,
                                        "target_item_id": command.target_item_id,
                                    },
                                    authorization_snapshot={
                                        "account_status": "active",
                                        "actor_kind": "user",
                                        "actor_user_id": actor.id,
                                        "capabilities": list(capabilities),
                                        "current_part_id": actor.current_part_id,
                                        "schema_version": 1,
                                        "scope_epoch": actor.scope_epoch,
                                        "space_id": command.space_id,
                                        "system_role": actor.system_role,
                                    },
                                    authorization_scope_epoch=actor.scope_epoch,
                                    authorization_part_id=actor.current_part_id,
                                    authorization_capabilities=capabilities,
                                    max_attempts=3,
                                    cancellable=False,
                                    available_at_ms=now_ms,
                                ),
                            ).job
                            self._hit("merge.after_rescan_enqueue")
                            written = self._merges.apply(
                                connection,
                                MergeApplyDraft(
                                    space_id=command.space_id,
                                    actor_user_id=actor.id,
                                    operation_id=command.operation_id,
                                    dedup_id=binding.dedup_id,
                                    source_item_id=command.source_item_id,
                                    source_revision_id=command.source_revision_id,
                                    source_expected_row_version=snapshot.source.row_version,
                                    target_item_id=command.target_item_id,
                                    target_revision_id=command.target_revision_id,
                                    target_expected_row_version=snapshot.target.row_version,
                                    result_revision_id=result_revision_id,
                                    transition_id=transition_id,
                                    rescan_job_id=job.id,
                                    rescan_job_dedup_id=job_dedup_id,
                                    relation_id=relation_id,
                                    merge_event_id=merge_event_id,
                                    preview_sha256=command.preview_sha256,
                                    edge_impact_sha256=snapshot.edge_impact_sha256,
                                    reference_impact_sha256=snapshot.reference_impact_sha256,
                                    result_markdown=prepared.markdown,
                                    raw_content_sha256=prepared.raw_sha256,
                                    normalized_content_sha256=prepared.normalized_sha256,
                                    content_size_bytes=prepared.size_bytes,
                                    normalizer_version="markdown_lf_v1",
                                    metadata={
                                        "operation_id": command.operation_id,
                                        "preview_sha256": command.preview_sha256,
                                        "rule_version": _MERGE_RULE_VERSION,
                                        "schema_version": 1,
                                        "source_item_id": command.source_item_id,
                                        "source_revision_id": command.source_revision_id,
                                    },
                                    relation_evidence={
                                        "edge_impact_sha256": snapshot.edge_impact_sha256,
                                        "preview_sha256": command.preview_sha256,
                                        "reference_impact_sha256": snapshot.reference_impact_sha256,
                                        "rule_version": _MERGE_RULE_VERSION,
                                        "schema_version": 1,
                                    },
                                    reason="Reviewed merge application.",
                                    created_at_ms=now_ms,
                                ),
                                failpoint=self._hit,
                            )
                            hmacs = {
                                command.source_item_id: binding.source_hmac,
                                command.target_item_id: binding.target_hmac,
                            }
                            for item_id in sorted(checks):
                                session_id, _lease_id, fence, _revision_id, _row_version, _raw = checks[item_id]
                                try:
                                    released = self._leases.release(
                                        connection,
                                        item_id=item_id,
                                        space_id=command.space_id,
                                        actor_user_id=actor.id,
                                        editor_session_id=session_id,
                                        token_hmac=hmacs[item_id],
                                        fencing_generation=fence,
                                        now_ms=now_ms,
                                    )
                                except (
                                    LeaseLostRepositoryError,
                                    LeaseItemNotFoundError,
                                    LeaseIntegrityError,
                                ) as exc:
                                    raise MergeServiceIntegrityError(
                                        "A merge lease could not be released atomically."
                                    ) from exc
                                expected_version = (
                                    written.source_row_version
                                    if item_id == command.source_item_id
                                    else written.target_row_version
                                )
                                if released.item.row_version != expected_version:
                                    raise MergeServiceIntegrityError(
                                        "A released merge lease is not bound to its effect."
                                    )
                            self._hit("merge.after_dual_release")
                            self._append_events(
                                connection,
                                actor=actor,
                                command=command,
                                dedup_id=binding.dedup_id,
                                written=written,
                                snapshot=snapshot,
                                safe_meta=safe_meta,
                                now_ms=now_ms,
                            )
                            result = MergeApplyResult(
                                request_id=command.request_id,
                                operation_id=command.operation_id,
                                merge_event_id=merge_event_id,
                                source_item_id=command.source_item_id,
                                target_item_id=command.target_item_id,
                                result_revision_id=result_revision_id,
                                redirect_relation_id=relation_id,
                            )
                            self._users.commit_operation(
                                connection,
                                dedup_id=job_dedup_id,
                                result={
                                    "job_id": job.id,
                                    "operation_id": command.operation_id,
                                    "revision_id": result_revision_id,
                                    "status": "queued",
                                },
                                http_status=202,
                                completed_at_ms=now_ms,
                            )
                            self._commit_result(
                                connection,
                                dedup_id=binding.dedup_id,
                                result=result.as_dict(),
                                http_status=200,
                                now_ms=now_ms,
                            )
                            self._hit("merge.before_commit")
            except MergeNotFoundError as exc:
                raise LeaseNotFoundError(str(exc)) from exc
            except MergeConflictError as exc:
                raise MergePreviewStaleError(
                    str(exc),
                    details={
                        "current_revision_id": exc.current_revision_id,
                        "current_row_version": exc.current_row_version,
                        "item_id": exc.item_id,
                    },
                ) from exc
            except (
                MergeIntegrityError,
                JobEffectConflictError,
                JobStateConflictError,
                JobRepositoryError,
            ) as exc:
                raise MergeServiceIntegrityError(str(exc)) from exc
        finally:
            connection.close()
        if final_error is not None:
            raise final_error
        if result is None:
            raise MergeServiceIntegrityError("The merge produced no terminal result.")
        return result


__all__ = [
    "MergeApplyCommand",
    "MergeApplyResult",
    "MergeDiffPane",
    "MergePreviewCommand",
    "MergePreviewResult",
    "MergePreviewStaleError",
    "MergeServiceIntegrityError",
    "TransactionalMergeService",
]
