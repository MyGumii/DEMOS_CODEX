"""Fixed-input change-proposal preview, rebase, and apply service.

Preview and blob materialization are read-only and run outside write
transactions.  Rebase is one idempotent create-and-obsolete transaction.
Apply revalidates the reviewed pair and fixed preview, consumes one fenced
target lease, creates exactly one approved-update revision, and commits the
rescan, pair state, audit, outbox, generation, and idempotency result together.
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
from ..repositories.outbox import OutboxEventDraft, OutboxRepository
from ..repositories.proposals import (
    ProposalApplyDraft,
    ProposalApplyWriteResult,
    ProposalBodySnapshot,
    ProposalConflictError,
    ProposalIntegrityError,
    ProposalNotFoundError,
    ProposalReadSnapshot,
    ProposalRebaseDraft,
    ProposalRebaseWriteResult,
    ProposalRepository,
    ProposalRevisionSnapshot,
    canonical_json_bytes,
    fixed_preview_payload,
    sha256_canonical,
)
from ..repositories.users import UserRecord
from ..scope import Capability, ScopeResolver, SessionPrincipal
from .editing import (
    DEFAULT_ABSOLUTE_AFTER_MS,
    DEFAULT_STALE_AFTER_MS,
    EditFenceCheck,
    FencedLeaseService,
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
_RESCAN_RULE_VERSION = "curation-rescan-v1"
_REBASE_DEDUP_NAMESPACE = UUID("d2c87c1e-5514-41e7-adbf-71c37140de07")
_REBASE_FINDING_NAMESPACE = UUID("542953ff-5077-408e-a5c3-16d03b3ddcb5")
_REBASE_PROPOSAL_NAMESPACE = UUID("709c415b-221f-4517-a220-4717226d5f0b")
_APPLY_REVISION_NAMESPACE = UUID("461e8ffc-0b5e-4a35-aa6a-7a2217dc8bd7")
_APPLY_TRANSITION_NAMESPACE = UUID("cf231391-539e-493d-b520-aae12bf856b9")
_APPLY_JOB_NAMESPACE = UUID("a3dfcfe9-0e18-4535-9892-380a7657d711")
_APPLY_JOB_DEDUP_NAMESPACE = UUID("d4d2bc2c-e1ad-453a-9886-fcb2ac389735")
_REBASE_AUDIT_NAMESPACE = UUID("7ab10185-aa3d-47d6-a59f-62f18b7ad86c")
_APPLY_AUDIT_NAMESPACE = UUID("305649d8-fe9d-4022-8ab8-1b83100a7763")
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


class ProposalServiceIntegrityError(LeaseServiceIntegrityError):
    """Canonical proposal evidence or effect failed closed."""


class ProposalPreviewStaleError(LeaseRevisionConflictError):
    """A proposal no longer names the current source or target revision."""


class ProposalReviewStateError(LeaseServiceError):
    """The proposal/finding pair is not in the required reviewed state."""


@dataclass(frozen=True, slots=True)
class ProposalPreviewCommand:
    principal: SessionPrincipal
    space_id: str
    proposal_id: str
    request_id: str


@dataclass(frozen=True, slots=True)
class ProposalDiffPane:
    label: Literal["fork_base", "source_current", "target_current"]
    item_id: str | None
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
class ProposalResultPane:
    content_sha256: str
    content_size_bytes: int
    markdown: str = field(repr=False)

    def as_dict(self) -> dict[str, object]:
        return {
            "content_sha256": self.content_sha256,
            "content_size_bytes": self.content_size_bytes,
            "markdown": self.markdown,
        }


@dataclass(frozen=True, slots=True)
class ProposalPreviewResult:
    request_id: str
    proposal_id: str
    finding_id: str
    proposal_kind: str
    proposal_version: int
    proposal_row_version: int
    finding_row_version: int
    state: str
    panes: tuple[ProposalDiffPane, ProposalDiffPane, ProposalDiffPane]
    proposed_result: ProposalResultPane
    preview_sha256: str
    stale: bool
    current_source_revision_id: str | None
    current_target_revision_id: str
    current_target_row_version: int

    def as_dict(self) -> dict[str, object]:
        return {
            "current_source_revision_id": self.current_source_revision_id,
            "current_target_revision_id": self.current_target_revision_id,
            "current_target_row_version": self.current_target_row_version,
            "finding_id": self.finding_id,
            "finding_row_version": self.finding_row_version,
            "panes": [pane.as_dict() for pane in self.panes],
            "preview_sha256": self.preview_sha256,
            "proposal_id": self.proposal_id,
            "proposal_kind": self.proposal_kind,
            "proposal_row_version": self.proposal_row_version,
            "proposal_version": self.proposal_version,
            "proposed_result": self.proposed_result.as_dict(),
            "request_id": self.request_id,
            "stale": self.stale,
            "state": self.state,
        }


@dataclass(frozen=True, slots=True)
class ProposalRebaseCommand:
    principal: SessionPrincipal
    space_id: str
    proposal_id: str
    expected_proposal_row_version: int
    expected_finding_row_version: int
    expected_target_revision_id: str
    expected_source_revision_id: str | None
    proposed_markdown: str | None = field(default=None, repr=False)
    operation_id: str = ""
    request_id: str = ""
    request_meta: Mapping[str, object] = field(default_factory=dict, repr=False)


@dataclass(frozen=True, slots=True)
class ProposalRebaseResult:
    request_id: str
    operation_id: str
    old_proposal_id: str
    proposal_id: str
    finding_id: str
    proposal_series_id: str
    proposal_version: int
    proposal_row_version: int
    finding_row_version: int
    preview_sha256: str
    target_revision_id: str
    source_revision_id: str | None
    status: Literal["pending_review"] = "pending_review"

    def as_dict(self) -> dict[str, object]:
        return {
            "finding_id": self.finding_id,
            "finding_row_version": self.finding_row_version,
            "old_proposal_id": self.old_proposal_id,
            "operation_id": self.operation_id,
            "preview_sha256": self.preview_sha256,
            "proposal_id": self.proposal_id,
            "proposal_row_version": self.proposal_row_version,
            "proposal_series_id": self.proposal_series_id,
            "proposal_version": self.proposal_version,
            "request_id": self.request_id,
            "source_revision_id": self.source_revision_id,
            "status": self.status,
            "target_revision_id": self.target_revision_id,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "ProposalRebaseResult":
        expected = {
            "finding_id", "finding_row_version", "old_proposal_id", "operation_id",
            "preview_sha256", "proposal_id", "proposal_row_version",
            "proposal_series_id", "proposal_version", "request_id",
            "source_revision_id", "status", "target_revision_id",
        }
        if set(value) != expected or value.get("status") != "pending_review":
            raise ValueError("Stored proposal rebase result shape is invalid.")
        source = value.get("source_revision_id")
        if source is not None:
            source = _uuid(source, "source_revision_id")
        return cls(
            request_id=_uuid(value["request_id"], "request_id"),
            operation_id=_uuid(value["operation_id"], "operation_id"),
            old_proposal_id=_uuid(value["old_proposal_id"], "old_proposal_id"),
            proposal_id=_uuid(value["proposal_id"], "proposal_id"),
            finding_id=_uuid(value["finding_id"], "finding_id"),
            proposal_series_id=_uuid(value["proposal_series_id"], "proposal_series_id"),
            proposal_version=_positive(value["proposal_version"], "proposal_version"),
            proposal_row_version=_positive(value["proposal_row_version"], "proposal_row_version"),
            finding_row_version=_positive(value["finding_row_version"], "finding_row_version"),
            preview_sha256=_sha(value["preview_sha256"], "preview_sha256"),
            target_revision_id=_uuid(value["target_revision_id"], "target_revision_id"),
            source_revision_id=source,  # type: ignore[arg-type]
        )


@dataclass(frozen=True, slots=True)
class ProposalApplyCommand:
    principal: SessionPrincipal
    space_id: str
    proposal_id: str
    expected_proposal_row_version: int
    expected_finding_row_version: int
    expected_target_item_row_version: int
    expected_target_revision_id: str
    expected_source_revision_id: str | None
    preview_sha256: str
    editor_session_id: str
    lease_id: str
    fencing_generation: int
    raw_token: str = field(repr=False)
    operation_id: str = ""
    request_id: str = ""
    request_meta: Mapping[str, object] = field(default_factory=dict, repr=False)


@dataclass(frozen=True, slots=True)
class ProposalApplyResult:
    request_id: str
    operation_id: str
    proposal_id: str
    finding_id: str
    target_item_id: str
    result_revision_id: str
    rescan_job_id: str
    item_row_version: int
    proposal_row_version: int
    finding_row_version: int
    status: Literal["applied"] = "applied"

    def as_dict(self) -> dict[str, object]:
        return {
            "finding_id": self.finding_id,
            "finding_row_version": self.finding_row_version,
            "item_row_version": self.item_row_version,
            "operation_id": self.operation_id,
            "proposal_id": self.proposal_id,
            "proposal_row_version": self.proposal_row_version,
            "request_id": self.request_id,
            "rescan_job_id": self.rescan_job_id,
            "result_revision_id": self.result_revision_id,
            "status": self.status,
            "target_item_id": self.target_item_id,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "ProposalApplyResult":
        expected = {
            "finding_id", "finding_row_version", "item_row_version", "operation_id",
            "proposal_id", "proposal_row_version", "request_id", "rescan_job_id",
            "result_revision_id", "status", "target_item_id",
        }
        if set(value) != expected or value.get("status") != "applied":
            raise ValueError("Stored proposal apply result shape is invalid.")
        return cls(
            request_id=_uuid(value["request_id"], "request_id"),
            operation_id=_uuid(value["operation_id"], "operation_id"),
            proposal_id=_uuid(value["proposal_id"], "proposal_id"),
            finding_id=_uuid(value["finding_id"], "finding_id"),
            target_item_id=_uuid(value["target_item_id"], "target_item_id"),
            result_revision_id=_uuid(value["result_revision_id"], "result_revision_id"),
            rescan_job_id=_uuid(value["rescan_job_id"], "rescan_job_id"),
            item_row_version=_positive(value["item_row_version"], "item_row_version"),
            proposal_row_version=_positive(value["proposal_row_version"], "proposal_row_version"),
            finding_row_version=_positive(value["finding_row_version"], "finding_row_version"),
        )


@dataclass(frozen=True, slots=True)
class _PreparedMarkdown:
    markdown: str = field(repr=False)
    raw_sha256: str
    normalized_sha256: str
    size_bytes: int


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
            "The proposal lease token must be 43 unpadded base64url characters."
        )
    try:
        raw = base64.b64decode(value + "=", altchars=b"-_", validate=True)
    except (TypeError, ValueError) as exc:
        raise LeaseValidationError("The proposal lease token is invalid.") from exc
    if len(raw) != 32 or base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii") != value:
        raise LeaseValidationError("The proposal lease token must decode to 32 bytes.")
    return raw


def _prepare_markdown(value: object) -> _PreparedMarkdown:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= _MAX_MARKDOWN_CHARACTERS
        or "\x00" in value
    ):
        raise LeaseValidationError(
            "Proposal Markdown must contain 1..2000000 non-NUL characters."
        )
    try:
        raw = value.encode("utf-8")
        normalized = value.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")
    except UnicodeEncodeError as exc:
        raise LeaseValidationError("Proposal Markdown must be valid Unicode.") from exc
    return _PreparedMarkdown(
        value,
        sha256(raw).hexdigest(),
        sha256(normalized).hexdigest(),
        len(raw),
    )


def _safe_request_meta(
    value: object, *, secret_values: tuple[str, ...] = ()
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


def _materialize_revision(
    value: ProposalRevisionSnapshot,
    loader: Callable[[str, int], bytes | str] | None,
) -> str:
    if value.content_md is not None:
        markdown = value.content_md
    else:
        if value.content_blob_hash is None or loader is None:
            raise ProposalServiceIntegrityError(
                "A fixed proposal revision body is not materializable."
            )
        loaded = loader(value.content_blob_hash, value.content_size_bytes)
        if isinstance(loaded, bytes):
            try:
                markdown = loaded.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ProposalServiceIntegrityError(
                    "A proposal blob is not UTF-8 Markdown."
                ) from exc
        elif isinstance(loaded, str):
            markdown = loaded
        else:
            raise ProposalServiceIntegrityError("A proposal blob loader returned invalid data.")
    encoded = markdown.encode("utf-8")
    if len(encoded) != value.content_size_bytes or sha256(encoded).hexdigest() != value.raw_content_sha256:
        raise ProposalServiceIntegrityError(
            "A proposal revision body does not match immutable evidence."
        )
    return markdown


def _materialize_body(
    value: ProposalBodySnapshot,
    loader: Callable[[str, int], bytes | str] | None,
) -> str:
    if value.content_md is not None:
        markdown = value.content_md
    else:
        if value.content_blob_hash is None or loader is None:
            raise ProposalServiceIntegrityError("The proposed body is not materializable.")
        loaded = loader(value.content_blob_hash, value.content_size_bytes)
        if isinstance(loaded, bytes):
            try:
                markdown = loaded.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ProposalServiceIntegrityError(
                    "The proposed body blob is not UTF-8 Markdown."
                ) from exc
        elif isinstance(loaded, str):
            markdown = loaded
        else:
            raise ProposalServiceIntegrityError("The proposed body loader returned invalid data.")
    encoded = markdown.encode("utf-8")
    if len(encoded) != value.content_size_bytes or sha256(encoded).hexdigest() != value.content_sha256:
        raise ProposalServiceIntegrityError("The proposed body evidence is inconsistent.")
    return markdown


class TransactionalProposalService(FencedLeaseService):
    """Read-only proposal preview plus atomic rebase/apply mutations."""

    __slots__ = ("_content_loader", "_jobs", "_proposals")

    def __init__(
        self,
        factory: ConnectionFactory,
        *,
        key_ring: LeaseHmacKeyRing,
        scope_resolver: ScopeResolver,
        stale_after_ms: int = DEFAULT_STALE_AFTER_MS,
        absolute_after_ms: int = DEFAULT_ABSOLUTE_AFTER_MS,
        content_loader: Callable[[str, int], bytes | str] | None = None,
        proposals: ProposalRepository | None = None,
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
        self._proposals = proposals or ProposalRepository()
        self._jobs = jobs or JobRepository()

    @staticmethod
    def _validate_proposal_common(
        *, principal: SessionPrincipal, space_id: str, proposal_id: str, request_id: str
    ) -> None:
        if not isinstance(principal, SessionPrincipal):
            raise TypeError("Proposal commands require a SessionPrincipal.")
        FencedLeaseService._validate_common(
            principal=principal,
            space_id=space_id,
            item_id=proposal_id,
            request_id=request_id,
        )

    def _read_authorized_snapshot(
        self,
        *,
        principal: SessionPrincipal,
        space_id: str,
        proposal_id: str,
    ) -> ProposalReadSnapshot:
        connection = self._factory.open(read_only=True)
        try:
            with transaction(connection, TransactionMode.DEFERRED):
                self._resolve_scope(
                    connection,
                    principal=principal,
                    space_id=space_id,
                    capability=Capability.CURATE,
                )
                snapshot = self._proposals.read_snapshot(
                    connection, space_id=space_id, proposal_id=proposal_id
                )
                if (
                    snapshot.proposal_kind == "personal_to_part"
                    and snapshot.source_current is not None
                    and snapshot.source_current.owner_user_id != principal.user_id
                ):
                    raise ProposalReviewStateError(
                        "A personal-to-part proposal belongs to another personal owner."
                    )
                return snapshot
        except ProposalNotFoundError as exc:
            raise LeaseNotFoundError(str(exc)) from exc
        except ProposalConflictError as exc:
            raise ProposalPreviewStaleError(
                str(exc),
                details={
                    "current_revision_id": exc.current_revision_id,
                    "current_row_version": exc.current_row_version,
                },
            ) from exc
        except ProposalIntegrityError as exc:
            raise ProposalServiceIntegrityError(str(exc)) from exc
        finally:
            connection.close()

    def _materialized(
        self, snapshot: ProposalReadSnapshot
    ) -> tuple[str, str, str, str]:
        base = _materialize_revision(snapshot.base, self._content_loader)
        source = (
            _materialize_body(snapshot.proposed, self._content_loader)
            if snapshot.source is None
            else _materialize_revision(snapshot.source, self._content_loader)
        )
        target = _materialize_revision(snapshot.target, self._content_loader)
        proposed = _materialize_body(snapshot.proposed, self._content_loader)
        return base, source, target, proposed

    @staticmethod
    def _preview_result(
        snapshot: ProposalReadSnapshot,
        *,
        request_id: str,
        base_markdown: str,
        source_markdown: str,
        target_markdown: str,
        proposed_markdown: str,
    ) -> ProposalPreviewResult:
        if sha256_canonical(snapshot.fixed_preview_payload()) != snapshot.fixed_preview_sha256:
            raise ProposalServiceIntegrityError("The stored fixed preview hash is invalid.")
        source_item_id = None if snapshot.source is None else snapshot.source.item_id
        source_revision_id = (
            snapshot.extraction_snapshot_id
            if snapshot.source is None
            else snapshot.source.revision_id
        )
        if source_revision_id is None:
            raise ProposalServiceIntegrityError("The proposal source pane has no identity.")
        source_sha = (
            snapshot.proposed.content_sha256
            if snapshot.source is None
            else snapshot.source.raw_content_sha256
        )
        panes = (
            ProposalDiffPane(
                "fork_base", snapshot.base.item_id, snapshot.base.revision_id,
                snapshot.base.raw_content_sha256, base_markdown,
            ),
            ProposalDiffPane(
                "source_current", source_item_id, source_revision_id, source_sha,
                source_markdown,
            ),
            ProposalDiffPane(
                "target_current", snapshot.target.item_id, snapshot.target.revision_id,
                snapshot.target.raw_content_sha256, target_markdown,
            ),
        )
        return ProposalPreviewResult(
            request_id=request_id,
            proposal_id=snapshot.proposal_id,
            finding_id=snapshot.finding_id,
            proposal_kind=snapshot.proposal_kind,
            proposal_version=snapshot.proposal_version,
            proposal_row_version=snapshot.proposal_row_version,
            finding_row_version=snapshot.finding_row_version,
            state=snapshot.proposal_state,
            panes=panes,
            proposed_result=ProposalResultPane(
                snapshot.proposed.content_sha256,
                snapshot.proposed.content_size_bytes,
                proposed_markdown,
            ),
            preview_sha256=snapshot.fixed_preview_sha256,
            stale=snapshot.stale,
            current_source_revision_id=(
                None if snapshot.source_current is None else snapshot.source_current.revision_id
            ),
            current_target_revision_id=snapshot.target_current.revision_id,
            current_target_row_version=snapshot.target_current.item_row_version,
        )

    def preview(self, command: ProposalPreviewCommand) -> ProposalPreviewResult:
        if not isinstance(command, ProposalPreviewCommand):
            raise TypeError("preview requires a ProposalPreviewCommand.")
        self._validate_proposal_common(
            principal=command.principal,
            space_id=command.space_id,
            proposal_id=command.proposal_id,
            request_id=command.request_id,
        )
        snapshot = self._read_authorized_snapshot(
            principal=command.principal,
            space_id=command.space_id,
            proposal_id=command.proposal_id,
        )
        bodies = self._materialized(snapshot)
        current = self._read_authorized_snapshot(
            principal=command.principal,
            space_id=command.space_id,
            proposal_id=command.proposal_id,
        )
        if current != snapshot:
            raise ProposalPreviewStaleError(
                "Proposal evidence changed while its bodies were materialized."
            )
        return self._preview_result(
            snapshot,
            request_id=command.request_id,
            base_markdown=bodies[0],
            source_markdown=bodies[1],
            target_markdown=bodies[2],
            proposed_markdown=bodies[3],
        )

    @staticmethod
    def _rebase_request_binding(
        command: ProposalRebaseCommand,
        snapshot: ProposalReadSnapshot,
    ) -> tuple[str, str, str]:
        if snapshot.proposal_kind == "stable_source_update":
            if command.proposed_markdown is not None:
                raise LeaseValidationError(
                    "A stable-source rebase preserves its immutable source body."
                )
            proposed_sha256 = snapshot.proposed.content_sha256
        else:
            source_current = snapshot.source_current
            if source_current is None:
                raise ProposalServiceIntegrityError(
                    "The personal source current is missing."
                )
            proposed_sha256 = (
                source_current.raw_content_sha256
                if command.proposed_markdown is None
                else _prepare_markdown(command.proposed_markdown).raw_sha256
            )
        new_source = snapshot.source_current
        new_base = snapshot.target_current if snapshot.source is None else snapshot.base
        fixed_sha = sha256_canonical(
            fixed_preview_payload(
                proposal_kind=snapshot.proposal_kind,
                proposal_version=snapshot.proposal_version + 1,
                base_item_id=new_base.item_id,
                base_revision_id=new_base.revision_id,
                source_item_id=None if new_source is None else new_source.item_id,
                source_revision_id=None if new_source is None else new_source.revision_id,
                target_item_id=snapshot.target_current.item_id,
                target_revision_id=snapshot.target_current.revision_id,
                source_artifact_id=snapshot.source_artifact_id,
                extraction_snapshot_id=snapshot.extraction_snapshot_id,
                extraction_snapshot_sha256=snapshot.extraction_snapshot_sha256,
                proposed_content_sha256=proposed_sha256,
                finding_input_row_version=1,
                rule_version=snapshot.finding_rule_version,
            )
        )
        request_sha = sha256_canonical(
            {
                "actor_scope_epoch": command.principal.captured_scope_epoch,
                "actor_user_id": command.principal.user_id,
                "expected_finding_row_version": command.expected_finding_row_version,
                "expected_proposal_row_version": command.expected_proposal_row_version,
                "expected_source_revision_id": command.expected_source_revision_id,
                "expected_target_revision_id": command.expected_target_revision_id,
                "fixed_preview_sha256": fixed_sha,
                "operation_type": "proposal_rebase",
                "proposal_id": command.proposal_id,
                "proposed_content_sha256": proposed_sha256,
                "schema_version": 1,
                "space_id": command.space_id,
            }
        )
        return proposed_sha256, fixed_sha, request_sha

    @staticmethod
    def _require_rebase_replay_closure(
        connection: ManagedConnection,
        *,
        command: ProposalRebaseCommand,
        stored: ProposalRebaseResult,
    ) -> None:
        if stored.old_proposal_id != command.proposal_id:
            raise LeaseIdempotencyError(
                "The stored rebase is not bound to this proposal."
            )
        closure = connection.execute(
            """SELECT p.finding_id,p.proposal_series_id,p.proposal_version,
                      p.supersedes_proposal_id,p.preview_sha256,p.state,newf.status,
                      old.state,oldf.status
                 FROM change_proposal p
                 JOIN curation_finding newf ON newf.id=p.finding_id
                 JOIN change_proposal old ON old.id=p.supersedes_proposal_id
                 JOIN curation_finding oldf ON oldf.id=old.finding_id
                WHERE p.id=?""",
            (stored.proposal_id,),
        ).fetchone()
        if closure is None or tuple(closure) != (
            stored.finding_id,
            stored.proposal_series_id,
            stored.proposal_version,
            command.proposal_id,
            stored.preview_sha256,
            "pending_review",
            "pending",
            "obsolete",
            "obsolete",
        ):
            raise LeaseIdempotencyError(
                "The stored rebase has no canonical version closure."
            )

    def _preflight_rebase_replay(
        self,
        command: ProposalRebaseCommand,
        *,
        dedup_id: str,
        request_sha: str,
    ) -> ProposalRebaseResult | None:
        connection = self._factory.open(read_only=True)
        try:
            with transaction(connection, TransactionMode.DEFERRED):
                self._resolve_scope(
                    connection,
                    principal=command.principal,
                    space_id=command.space_id,
                    capability=Capability.CURATE,
                )
                existing = self._users.get_operation(connection, dedup_id)
                if existing is None:
                    return None
                if existing.request_sha256 != request_sha:
                    raise LeaseIdempotencyError(
                        "The proposal rebase operation ID is bound differently."
                    )
                if existing.status != "committed" or existing.result is None:
                    raise LeaseOperationInProgressError(
                        "The proposal rebase is already in progress."
                    )
                stored = ProposalRebaseResult.from_mapping(existing.result)
                self._require_rebase_replay_closure(
                    connection, command=command, stored=stored
                )
                return replace(stored, request_id=command.request_id)
        finally:
            connection.close()

    def rebase(self, command: ProposalRebaseCommand) -> ProposalRebaseResult:
        if not isinstance(command, ProposalRebaseCommand):
            raise TypeError("rebase requires a ProposalRebaseCommand.")
        self._validate_proposal_common(
            principal=command.principal,
            space_id=command.space_id,
            proposal_id=command.proposal_id,
            request_id=command.request_id,
        )
        _uuid(command.operation_id, "operation_id")
        _positive(command.expected_proposal_row_version, "expected_proposal_row_version")
        _positive(command.expected_finding_row_version, "expected_finding_row_version")
        _uuid(command.expected_target_revision_id, "expected_target_revision_id")
        if command.expected_source_revision_id is not None:
            _uuid(command.expected_source_revision_id, "expected_source_revision_id")
        safe_meta = _safe_request_meta(command.request_meta)
        snapshot = self._read_authorized_snapshot(
            principal=command.principal,
            space_id=command.space_id,
            proposal_id=command.proposal_id,
        )
        new_proposal_id = _derived_uuid(
            _REBASE_PROPOSAL_NAMESPACE, command.principal.user_id, command.operation_id
        )
        new_finding_id = _derived_uuid(
            _REBASE_FINDING_NAMESPACE, command.principal.user_id, command.operation_id
        )
        dedup_id = _derived_uuid(
            _REBASE_DEDUP_NAMESPACE, command.principal.user_id, command.operation_id
        )
        proposed_sha256, fixed_sha, request_sha = self._rebase_request_binding(
            command, snapshot
        )
        replay = self._preflight_rebase_replay(
            command, dedup_id=dedup_id, request_sha=request_sha
        )
        if replay is not None:
            return replay
        bodies = self._materialized(snapshot)
        current = self._read_authorized_snapshot(
            principal=command.principal,
            space_id=command.space_id,
            proposal_id=command.proposal_id,
        )
        if current != snapshot:
            raise ProposalPreviewStaleError("Proposal evidence changed during rebase preparation.")
        if snapshot.proposal_kind == "stable_source_update":
            prepared = _prepare_markdown(bodies[3])
            body_blob = snapshot.proposed.content_blob_hash
            body_md = snapshot.proposed.content_md
        else:
            source_current = snapshot.source_current
            if source_current is None:
                raise ProposalServiceIntegrityError("The personal source current is missing.")
            source_current_markdown = _materialize_revision(
                source_current, self._content_loader
            )
            prepared = _prepare_markdown(
                source_current_markdown
                if command.proposed_markdown is None
                else command.proposed_markdown
            )
            body_blob = None
            body_md = prepared.markdown
        if prepared.raw_sha256 != proposed_sha256:
            raise ProposalServiceIntegrityError(
                "The materialized rebase body does not match its stored request evidence."
            )
        result: ProposalRebaseResult | None = None
        connection = self._factory.open()
        try:
            with transaction(connection):
                actor, _resolved = self._resolve_scope(
                    connection,
                    principal=command.principal,
                    space_id=command.space_id,
                    capability=Capability.CURATE,
                )
                now_ms = db_now_ms(connection)
                begun = self._begin(
                    connection,
                    actor=actor,
                    operation_type="proposal_rebase",
                    operation_id=command.operation_id,
                    request_hash=request_sha,
                    dedup_id=dedup_id,
                    now_ms=now_ms,
                )
                if not begun.inserted:
                    if begun.operation.status != "committed" or begun.operation.result is None:
                        raise LeaseOperationInProgressError(
                            "The proposal rebase is already in progress."
                        )
                    stored = ProposalRebaseResult.from_mapping(begun.operation.result)
                    self._require_rebase_replay_closure(
                        connection, command=command, stored=stored
                    )
                    result = replace(stored, request_id=command.request_id)
                else:
                    fixed = self._proposals.read_snapshot(
                        connection, space_id=command.space_id, proposal_id=command.proposal_id
                    )
                    if fixed != snapshot:
                        raise ProposalConflictError(
                            "The proposal changed before the rebase transaction."
                        )
                    written = self._proposals.rebase(
                        connection,
                        ProposalRebaseDraft(
                            old_proposal_id=command.proposal_id,
                            old_finding_id=snapshot.finding_id,
                            expected_old_proposal_row_version=command.expected_proposal_row_version,
                            expected_old_finding_row_version=command.expected_finding_row_version,
                            new_proposal_id=new_proposal_id,
                            new_finding_id=new_finding_id,
                            operation_id=command.operation_id,
                            dedup_id=dedup_id,
                            actor_user_id=actor.id,
                            expected_target_revision_id=command.expected_target_revision_id,
                            expected_source_revision_id=command.expected_source_revision_id,
                            proposed_content_md=body_md,
                            proposed_content_blob_hash=body_blob,
                            proposed_content_size_bytes=prepared.size_bytes,
                            proposed_content_sha256=prepared.raw_sha256,
                            fixed_preview_sha256=fixed_sha,
                            created_at_ms=now_ms,
                        ),
                        failpoint=self._hit,
                    )
                    self._hit("proposal.after_rebase_insert")
                    self._append_rebase_events(
                        connection,
                        actor=actor,
                        command=command,
                        dedup_id=dedup_id,
                        written=written,
                        request_meta=safe_meta,
                        now_ms=now_ms,
                    )
                    result = ProposalRebaseResult(
                        request_id=command.request_id,
                        operation_id=command.operation_id,
                        old_proposal_id=command.proposal_id,
                        proposal_id=written.proposal_id,
                        finding_id=written.finding_id,
                        proposal_series_id=written.proposal_series_id,
                        proposal_version=written.proposal_version,
                        proposal_row_version=written.proposal_row_version,
                        finding_row_version=written.finding_row_version,
                        preview_sha256=written.preview_sha256,
                        target_revision_id=written.target_revision_id,
                        source_revision_id=written.source_revision_id,
                    )
                    self._commit_result(
                        connection,
                        dedup_id=dedup_id,
                        result=result.as_dict(),
                        http_status=200,
                        now_ms=now_ms,
                    )
                    self._hit("proposal_rebase.before_commit")
        except ProposalNotFoundError as exc:
            raise LeaseNotFoundError(str(exc)) from exc
        except ProposalConflictError as exc:
            raise ProposalPreviewStaleError(
                str(exc),
                details={
                    "current_revision_id": exc.current_revision_id,
                    "current_row_version": exc.current_row_version,
                },
            ) from exc
        except ProposalIntegrityError as exc:
            raise ProposalServiceIntegrityError(str(exc)) from exc
        finally:
            connection.close()
        if result is None:
            raise ProposalServiceIntegrityError("The rebase produced no terminal result.")
        return result

    def _append_rebase_events(
        self,
        connection: ManagedConnection,
        *,
        actor: UserRecord,
        command: ProposalRebaseCommand,
        dedup_id: str,
        written: ProposalRebaseWriteResult,
        request_meta: Mapping[str, object],
        now_ms: int,
    ) -> None:
        details = {
            "finding_id": written.finding_id,
            "old_proposal_id": command.proposal_id,
            "preview_sha256": written.preview_sha256,
            "proposal_id": written.proposal_id,
            "proposal_series_id": written.proposal_series_id,
            "proposal_version": written.proposal_version,
            "schema_version": 1,
            "source_revision_id": written.source_revision_id,
            "target_revision_id": written.target_revision_id,
        }
        self._audit.append(
            connection,
            AuditEventDraft(
                event_uuid=_derived_uuid(_REBASE_AUDIT_NAMESPACE, dedup_id),
                occurred_at_ms=now_ms,
                request_id=command.request_id,
                operation_id=command.operation_id,
                action="change_proposal.rebase",
                outcome="pending_review",
                actor_kind="user",
                actor_user_id=actor.id,
                actor_display_name=actor.display_name,
                actor_role_snapshot=actor.system_role,
                space_id=command.space_id,
                item_id=None,
                base_revision_id=command.expected_target_revision_id,
                new_revision_id=None,
                reason="Explicit change-proposal rebase.",
                details=details,
                request_meta=request_meta,
            ),
        )
        self._hit("proposal_rebase.after_audit")
        self._outbox.append(
            connection,
            OutboxEventDraft(
                logical_key=f"change-proposal:rebased:{dedup_id}",
                space_id=command.space_id,
                aggregate_type="change_proposal",
                aggregate_id=written.proposal_id,
                aggregate_version=written.proposal_row_version,
                event_type="change_proposal.rebased",
                payload=details,
                available_at_ms=now_ms,
            ),
        )
        self._hit("proposal_rebase.after_outbox")

    def _apply_extra(
        self, command: ProposalApplyCommand, snapshot: ProposalReadSnapshot
    ) -> dict[str, object]:
        return {
            "expected_finding_row_version": command.expected_finding_row_version,
            "expected_proposal_row_version": command.expected_proposal_row_version,
            "expected_source_revision_id": command.expected_source_revision_id,
            "expected_target_item_row_version": command.expected_target_item_row_version,
            "expected_target_revision_id": command.expected_target_revision_id,
            "finding_id": snapshot.finding_id,
            "preview_sha256": command.preview_sha256,
            "proposal_id": command.proposal_id,
            "proposed_content_sha256": snapshot.proposed.content_sha256,
            "schema_version": 1,
        }

    def _preflight_apply_replay(
        self,
        command: ProposalApplyCommand,
        snapshot: ProposalReadSnapshot,
        raw_token: bytes,
        *,
        result_revision_id: str,
        rescan_job_id: str,
    ) -> ProposalApplyResult | None:
        connection = self._factory.open(read_only=True)
        try:
            with transaction(connection, TransactionMode.DEFERRED):
                self._resolve_scope(
                    connection,
                    principal=command.principal,
                    space_id=command.space_id,
                    capability=Capability.CURATE,
                )
                dedup_id, _key, _hmac, request_sha = self._operation_token_binding(
                    connection,
                    operation_type="proposal_apply",
                    operation_id=command.operation_id,
                    principal=command.principal,
                    space_id=command.space_id,
                    item_id=snapshot.target.item_id,
                    editor_session_id=command.editor_session_id,
                    lease_id=command.lease_id,
                    fencing_generation=command.fencing_generation,
                    raw_token=raw_token,
                    extra=self._apply_extra(command, snapshot),
                )
                existing = self._users.get_operation(connection, dedup_id)
                if existing is None:
                    return None
                if existing.request_sha256 != request_sha:
                    raise LeaseIdempotencyError(
                        "The proposal apply operation ID is bound differently."
                    )
                if existing.status != "committed" or existing.result is None:
                    raise LeaseOperationInProgressError(
                        "The proposal apply operation is already in progress."
                    )
                stored = ProposalApplyResult.from_mapping(existing.result)
                if (
                    stored.proposal_id != command.proposal_id
                    or stored.finding_id != snapshot.finding_id
                    or stored.target_item_id != snapshot.target.item_id
                    or stored.result_revision_id != result_revision_id
                    or stored.rescan_job_id != rescan_job_id
                ):
                    raise LeaseIdempotencyError(
                        "The stored proposal apply result is not exact."
                    )
                closure = connection.execute(
                    """SELECT p.state,p.applied_operation_id,p.applied_dedup_id,
                              f.status,f.applied_operation_id,f.applied_dedup_id,
                              i.current_revision_id
                         FROM change_proposal p JOIN curation_finding f ON f.id=p.finding_id
                         JOIN knowledge_item i ON i.id=p.target_item_id
                        WHERE p.id=?""",
                    (command.proposal_id,),
                ).fetchone()
                if closure is None or tuple(closure) != (
                    "applied",
                    command.operation_id,
                    dedup_id,
                    "applied",
                    command.operation_id,
                    dedup_id,
                    result_revision_id,
                ):
                    raise LeaseIdempotencyError(
                        "The stored proposal apply has no canonical closure."
                    )
                return replace(stored, request_id=command.request_id)
        finally:
            connection.close()

    def apply(self, command: ProposalApplyCommand) -> ProposalApplyResult:
        if not isinstance(command, ProposalApplyCommand):
            raise TypeError("apply requires a ProposalApplyCommand.")
        self._validate_proposal_common(
            principal=command.principal,
            space_id=command.space_id,
            proposal_id=command.proposal_id,
            request_id=command.request_id,
        )
        _uuid(command.operation_id, "operation_id")
        _uuid(command.expected_target_revision_id, "expected_target_revision_id")
        if command.expected_source_revision_id is not None:
            _uuid(command.expected_source_revision_id, "expected_source_revision_id")
        _positive(command.expected_proposal_row_version, "expected_proposal_row_version")
        _positive(command.expected_finding_row_version, "expected_finding_row_version")
        _positive(command.expected_target_item_row_version, "expected_target_item_row_version")
        _sha(command.preview_sha256, "preview_sha256")
        _uuid(command.editor_session_id, "editor_session_id")
        _uuid(command.lease_id, "lease_id")
        _positive(command.fencing_generation, "fencing_generation")
        raw_token = _decode_token(command.raw_token)
        safe_meta = _safe_request_meta(
            command.request_meta, secret_values=(command.raw_token,)
        )
        snapshot = self._read_authorized_snapshot(
            principal=command.principal,
            space_id=command.space_id,
            proposal_id=command.proposal_id,
        )
        result_revision_id = _derived_uuid(
            _APPLY_REVISION_NAMESPACE, command.principal.user_id, command.operation_id
        )
        transition_id = _derived_uuid(
            _APPLY_TRANSITION_NAMESPACE, command.principal.user_id, command.operation_id
        )
        rescan_job_id = _derived_uuid(
            _APPLY_JOB_NAMESPACE, command.principal.user_id, command.operation_id
        )
        job_dedup_id = _derived_uuid(
            _APPLY_JOB_DEDUP_NAMESPACE, command.principal.user_id, command.operation_id
        )
        replay = self._preflight_apply_replay(
            command,
            snapshot,
            raw_token,
            result_revision_id=result_revision_id,
            rescan_job_id=rescan_job_id,
        )
        if replay is not None:
            return replay
        proposed_markdown = _materialize_body(snapshot.proposed, self._content_loader)
        prepared = _prepare_markdown(proposed_markdown)
        current = self._read_authorized_snapshot(
            principal=command.principal,
            space_id=command.space_id,
            proposal_id=command.proposal_id,
        )
        if current != snapshot:
            raise ProposalPreviewStaleError("Proposal evidence changed during apply preparation.")
        if snapshot.stale or snapshot.fixed_preview_sha256 != command.preview_sha256:
            raise ProposalPreviewStaleError(
                "The approved proposal preview is stale and requires rebase.",
                details={
                    "current_revision_id": snapshot.target_current.revision_id,
                    "current_row_version": snapshot.target_current.item_row_version,
                },
            )
        if snapshot.proposal_state != "approved" or snapshot.finding_status != "approved":
            raise ProposalReviewStateError("The proposal/finding pair is not approved.")
        result: ProposalApplyResult | None = None
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
                    dedup_id, _key_id, token_hmac, request_sha = self._operation_token_binding(
                        connection,
                        operation_type="proposal_apply",
                        operation_id=command.operation_id,
                        principal=command.principal,
                        space_id=command.space_id,
                        item_id=snapshot.target.item_id,
                        editor_session_id=command.editor_session_id,
                        lease_id=command.lease_id,
                        fencing_generation=command.fencing_generation,
                        raw_token=raw_token,
                        extra=self._apply_extra(command, snapshot),
                    )
                    begun = self._begin(
                        connection,
                        actor=actor,
                        operation_type="proposal_apply",
                        operation_id=command.operation_id,
                        request_hash=request_sha,
                        dedup_id=dedup_id,
                        now_ms=now_ms,
                    )
                    if not begun.inserted:
                        if begun.operation.status != "committed" or begun.operation.result is None:
                            raise LeaseOperationInProgressError(
                                "The proposal apply is already in progress."
                            )
                        result = replace(
                            ProposalApplyResult.from_mapping(begun.operation.result),
                            request_id=command.request_id,
                        )
                    else:
                        fixed = self._proposals.read_snapshot(
                            connection,
                            space_id=command.space_id,
                            proposal_id=command.proposal_id,
                        )
                        if fixed != snapshot:
                            raise ProposalConflictError(
                                "The proposal changed before the apply transaction."
                            )
                        if fixed.finding_reviewed_by != actor.id:
                            raise ProposalReviewStateError(
                                "The approved proposal must be applied by its reviewer."
                            )
                        try:
                            proof = self.validate_edit_fence_in_transaction(
                                connection,
                                EditFenceCheck(
                                    principal=command.principal,
                                    space_id=command.space_id,
                                    item_id=fixed.target.item_id,
                                    editor_session_id=command.editor_session_id,
                                    lease_id=command.lease_id,
                                    fencing_generation=command.fencing_generation,
                                    expected_item_row_version=command.expected_target_item_row_version,
                                    expected_revision_id=command.expected_target_revision_id,
                                    raw_token=command.raw_token,
                                ),
                            )
                        except (LeaseLostError, LeaseRevisionConflictError, LeaseNotFoundError) as exc:
                            final_error = exc
                            self._fail_final(
                                connection, dedup_id=dedup_id, error=exc, now_ms=now_ms
                            )
                        else:
                            if (
                                proof.base_revision_id != command.expected_target_revision_id
                                or proof.base_item_row_version
                                != command.expected_target_item_row_version
                            ):
                                raise ProposalServiceIntegrityError(
                                    "The target lease proof is not bound to the proposal base."
                                )
                            job_input = {
                                "item_id": fixed.target.item_id,
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
                                    "The proposal rescan enqueue identity is in use."
                                )
                            capabilities = tuple(
                                sorted(capability.value for capability in resolved.effective_capabilities)
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
                                    effect_key=f"curation-rescan:{fixed.target.item_id}:{result_revision_id}",
                                    input_ref=job_input,
                                    expected_state={
                                        "_authorization_fence": authorization_fence,
                                        "base_revision_id": command.expected_target_revision_id,
                                        "content_size_bytes": prepared.size_bytes,
                                        "normalizer_version": "markdown_lf_v1",
                                        "normalized_content_sha256": prepared.normalized_sha256,
                                        "preview_sha256": command.preview_sha256,
                                        "proposal_id": command.proposal_id,
                                        "raw_content_sha256": prepared.raw_sha256,
                                        "result_revision_id": result_revision_id,
                                        "schema_version": 1,
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
                            self._hit("proposal_apply.after_rescan_enqueue")
                            written = self._proposals.apply(
                                connection,
                                ProposalApplyDraft(
                                    proposal_id=command.proposal_id,
                                    finding_id=fixed.finding_id,
                                    expected_proposal_row_version=command.expected_proposal_row_version,
                                    expected_finding_row_version=command.expected_finding_row_version,
                                    space_id=command.space_id,
                                    actor_user_id=actor.id,
                                    operation_id=command.operation_id,
                                    dedup_id=dedup_id,
                                    expected_target_item_row_version=command.expected_target_item_row_version,
                                    expected_target_revision_id=command.expected_target_revision_id,
                                    expected_source_revision_id=command.expected_source_revision_id,
                                    preview_sha256=command.preview_sha256,
                                    result_revision_id=result_revision_id,
                                    transition_id=transition_id,
                                    rescan_job_id=job.id,
                                    rescan_job_dedup_id=job_dedup_id,
                                    content_md=prepared.markdown,
                                    raw_content_sha256=prepared.raw_sha256,
                                    normalized_content_sha256=prepared.normalized_sha256,
                                    content_size_bytes=prepared.size_bytes,
                                    normalizer_version="markdown_lf_v1",
                                    metadata={
                                        "fencing_generation": command.fencing_generation,
                                        "operation_id": command.operation_id,
                                        "preview_sha256": command.preview_sha256,
                                        "proposal_id": command.proposal_id,
                                        "schema_version": 1,
                                    },
                                    reason="Approved change-proposal application.",
                                    created_at_ms=now_ms,
                                ),
                                failpoint=self._hit,
                            )
                            self._hit("proposal.after_apply_revision")
                            try:
                                released = self._leases.release(
                                    connection,
                                    item_id=fixed.target.item_id,
                                    space_id=command.space_id,
                                    actor_user_id=actor.id,
                                    editor_session_id=command.editor_session_id,
                                    token_hmac=token_hmac,
                                    fencing_generation=command.fencing_generation,
                                    now_ms=now_ms,
                                )
                            except (LeaseLostRepositoryError, LeaseItemNotFoundError, LeaseIntegrityError) as exc:
                                raise ProposalServiceIntegrityError(
                                    "The proposal target lease could not be released atomically."
                                ) from exc
                            if (
                                released.item.current_revision_id != result_revision_id
                                or released.item.row_version != written.item_row_version
                            ):
                                raise ProposalServiceIntegrityError(
                                    "The released lease does not cover the proposal effect."
                                )
                            self._hit("proposal_apply.after_lease_release")
                            self._append_apply_events(
                                connection,
                                actor=actor,
                                command=command,
                                dedup_id=dedup_id,
                                written=written,
                                request_meta=safe_meta,
                                now_ms=now_ms,
                            )
                            result = ProposalApplyResult(
                                request_id=command.request_id,
                                operation_id=command.operation_id,
                                proposal_id=command.proposal_id,
                                finding_id=fixed.finding_id,
                                target_item_id=fixed.target.item_id,
                                result_revision_id=result_revision_id,
                                rescan_job_id=job.id,
                                item_row_version=written.item_row_version,
                                proposal_row_version=written.proposal_row_version,
                                finding_row_version=written.finding_row_version,
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
                                dedup_id=dedup_id,
                                result=result.as_dict(),
                                http_status=200,
                                now_ms=now_ms,
                            )
                            self._hit("proposal_apply.before_commit")
            except ProposalNotFoundError as exc:
                raise LeaseNotFoundError(str(exc)) from exc
            except ProposalConflictError as exc:
                raise ProposalPreviewStaleError(
                    str(exc),
                    details={
                        "current_revision_id": exc.current_revision_id,
                        "current_row_version": exc.current_row_version,
                    },
                ) from exc
            except (ProposalIntegrityError, JobEffectConflictError, JobStateConflictError, JobRepositoryError) as exc:
                raise ProposalServiceIntegrityError(str(exc)) from exc
        finally:
            connection.close()
        if final_error is not None:
            raise final_error
        if result is None:
            raise ProposalServiceIntegrityError("The proposal apply produced no result.")
        return result

    def _append_apply_events(
        self,
        connection: ManagedConnection,
        *,
        actor: UserRecord,
        command: ProposalApplyCommand,
        dedup_id: str,
        written: ProposalApplyWriteResult,
        request_meta: Mapping[str, object],
        now_ms: int,
    ) -> None:
        details = {
            "finding_id": written.finding_id,
            "finding_row_version": written.finding_row_version,
            "fencing_generation": command.fencing_generation,
            "obsoleted_finding_count": written.obsoleted_finding_count,
            "obsoleted_proposal_count": written.obsoleted_proposal_count,
            "preview_sha256": command.preview_sha256,
            "proposal_id": written.proposal_id,
            "proposal_row_version": written.proposal_row_version,
            "removed_search_document_count": written.removed_search_document_count,
            "rescan_job_id": written.rescan_job_id,
            "revalidation_edge_count": written.revalidation_edge_count,
            "schema_version": 1,
            "space_generation": written.space_generation,
            "transition_id": written.transition_id,
        }
        self._audit.append(
            connection,
            AuditEventDraft(
                event_uuid=_derived_uuid(_APPLY_AUDIT_NAMESPACE, dedup_id),
                occurred_at_ms=now_ms,
                request_id=command.request_id,
                operation_id=command.operation_id,
                action="change_proposal.apply",
                outcome="applied",
                actor_kind="user",
                actor_user_id=actor.id,
                actor_display_name=actor.display_name,
                actor_role_snapshot=actor.system_role,
                space_id=command.space_id,
                item_id=written.target_item_id,
                base_revision_id=command.expected_target_revision_id,
                new_revision_id=written.result_revision_id,
                reason="Reviewed change-proposal application.",
                details=details,
                request_meta=request_meta,
            ),
        )
        self._hit("proposal_apply.after_audit")
        self._outbox.append(
            connection,
            OutboxEventDraft(
                logical_key=f"change-proposal:applied:{dedup_id}",
                space_id=command.space_id,
                aggregate_type="knowledge_item",
                aggregate_id=written.target_item_id,
                aggregate_version=written.item_row_version,
                event_type="change_proposal.applied",
                payload={"result_revision_id": written.result_revision_id, **details},
                available_at_ms=now_ms,
            ),
        )
        self._hit("proposal_apply.after_outbox")


__all__ = [
    "ProposalApplyCommand",
    "ProposalApplyResult",
    "ProposalDiffPane",
    "ProposalPreviewCommand",
    "ProposalPreviewResult",
    "ProposalPreviewStaleError",
    "ProposalRebaseCommand",
    "ProposalRebaseResult",
    "ProposalResultPane",
    "ProposalReviewStateError",
    "ProposalServiceIntegrityError",
    "TransactionalProposalService",
]
