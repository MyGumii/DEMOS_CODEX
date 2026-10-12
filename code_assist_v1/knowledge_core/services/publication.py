"""Deterministic P4.1-P4.5 publication preparation and commit services.

Raw bytes, extractor output, exact hashing, Markdown normalization, and the
minimal structural/blocker lint are resolved before any write transaction is
opened.  P4.2 composes that immutable result with staging and durable previews;
P4.3 consumes an exact ready preview through an idempotent single/dual-target
transaction.  P4.4 records stable Notion provenance and turns a changed stable
source into a fixed human-reviewed update proposal.  No semantic/model call
runs inside a canonical transaction.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Callable, Literal, Protocol, Sequence
from uuid import UUID, uuid5

from ..db import (
    ConnectionFactory,
    ManagedConnection,
    TransactionMode,
    db_now_ms,
    transaction,
)
from ..errors import KnowledgeCoreError
from ..protected_source_snapshot import ProtectedExtractionProvenance
from ..protected_sources import (
    PolicyProtectedSourceClassifier, ProtectedSourceKind,
    classify_protected_for_actor,
)
from ..repositories.artifacts import (
    ArtifactExtractionSpec,
    ArtifactIds,
    ArtifactRepository,
    CanonicalEmbeddedAssetSpec,
    OrphanBodyCleanupResult,
    PreparedMarkdownBody,
    SourceArtifactBundle,
    StagedVerifiedBlob,
)
from ..repositories.audit import AuditEventDraft, AuditRepository
from ..repositories.conversations import (
    ConversationRepository,
    ConversationRepositoryError,
)
from ..repositories.jobs import (
    DurableJobRecord,
    JobEnqueueDraft,
    JobRepository,
    canonical_job_input_sha256,
)
from ..repositories.outbox import OutboxEventDraft, OutboxRepository
from ..repositories.publication import (
    PREVIEW_TTL_MS,
    PreviewFinding,
    PublicationPreviewRecord,
    PublicationPreviewConflictError,
    PublicationPreviewRepository,
    QueuedPreviewDraft,
    ReadyPreviewDraft,
)
from ..repositories.publication_commit import (
    PublicationCommitConflictError,
    PublicationCommitDraft,
    PublicationCommitIntegrityError,
    PublicationCommitRepository,
    PublicationCommitResult,
    PublicationCommitTarget,
    PublicationUpdateProposalRequiredError,
)
from ..repositories.publication_authority import (
    HigherPublicationAuthorityConflictError,
    HigherPublicationAuthorityIntegrityError,
    HigherPublicationAuthorityRepository,
    HigherPublicationPreviewAuthorityDraft,
    HigherPublicationPreviewAuthorityRecord,
    HigherPublicationTargetDescriptor,
    target_descriptor_sha256,
)
from ..repositories.users import (
    OperationIdempotencyConflictError,
    OperationInProgressError,
    UserRepository,
)
from ..scope import Capability, ScopeResolutionError, ScopeResolver, SessionPrincipal
from .curation import (
    DETERMINISTIC_INGEST_RULE_VERSION,
    DETERMINISTIC_NORMALIZER_VERSION,
    DeterministicIngestPolicyError,
    lint_markdown_deterministic,
)
from .explicit_references import (
    ExplicitReferenceSyntaxError,
    ParsedExplicitReference,
    parse_explicit_item_references,
)
from .wiki_publication import WikiPublicationSelector, publication_replay_options
from ..worker import (
    JobExpectedStateConflictError,
    PermanentJobExecutionError,
    PreparedJobEffect,
    RegisteredJobHandler,
    RetryableJobExecutionError,
)


_SHA_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_SAFE_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{0,79}$", re.ASCII)
_MAX_SAFE_TEXT = 500
NORMALIZER_VERSION = DETERMINISTIC_NORMALIZER_VERSION
PUBLICATION_PREVIEW_RULE_VERSION = DETERMINISTIC_INGEST_RULE_VERSION
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.ASCII,
)
_USER_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$", re.ASCII)
_PREVIEW_NAMESPACE = UUID("f96374fe-f519-4a89-a52f-ae8ed83be151")
_ARTIFACT_NAMESPACE = UUID("eeeb8fa5-a38d-40dd-aed8-4641e19dc854")
_RAW_MANIFEST_NAMESPACE = UUID("ab2aab5e-dc3b-4ed3-b90a-f67cc6475df2")
_EXTRACTION_NAMESPACE = UUID("21f625d1-f0d5-4f40-a558-da81c6fa6218")
_EMBEDDED_ASSET_NAMESPACE = UUID("64f49d73-6af7-4e17-a2f0-c62c4c44b12f")
_DEDUP_NAMESPACE = UUID("248db74b-25f1-45e2-ad4c-eed5bb51e472")
_AUDIT_NAMESPACE = UUID("a378b44d-ec4c-4555-a800-fe158541107f")
_JOB_NAMESPACE = UUID("53fb40aa-509c-40b0-9697-72b2c02eaf6c")
_JOB_DEDUP_NAMESPACE = UUID("ce65280e-939d-46c4-a837-74d689eb18a7")
_WORKER_REQUEST_NAMESPACE = UUID("f489fb73-e7c6-46bb-b859-dd59751339d5")
_CANCEL_DEDUP_NAMESPACE = UUID("ba0c9396-8b1a-47c2-bca8-5e6a18ea25e3")
_MAINTENANCE_NAMESPACE = UUID("e3705d74-a529-4372-aa6e-9065d519e930")
_PUBLISH_DEDUP_NAMESPACE = UUID("667b16a9-e74e-4441-a721-202e6c527239")
_PUBLISH_AUDIT_NAMESPACE = UUID("7416cc5d-5ed0-49b0-80a9-af3a5104e18c")
_CURATION_DECISION_JOB_NAMESPACE = UUID("60a2bc75-760a-41a0-a707-c8bf5bbbc854")
_SAFE_META_KEYS = frozenset({"correlation_id", "remote_addr_hash", "user_agent_hash"})
_SENSITIVE_META_FRAGMENTS = ("authorization", "cookie", "password", "secret", "token")
_MAX_CHAT_PROMOTION_MESSAGES = 100


class PublicationSourcePreparationError(ValueError):
    """Stable, body-free preparation failure for an invalid caller contract."""

    code = "PUBLICATION_SOURCE_INVALID"


class PublicationPreviewServiceError(KnowledgeCoreError):
    code = "PUBLICATION_PREVIEW_SERVICE_ERROR"
    http_status = 409


class PublicationPreviewValidationError(PublicationPreviewServiceError):
    code = "VALIDATION_ERROR"
    http_status = 400


class PublicationPreviewAuthorizationError(PublicationPreviewServiceError):
    code = "FORBIDDEN"
    http_status = 403


class PublicationPreviewIdempotencyError(PublicationPreviewServiceError):
    code = "IDEMPOTENCY_CONFLICT"
    http_status = 409


class PublicationPreviewStagingError(PublicationPreviewServiceError):
    code = "PUBLICATION_SOURCE_STAGING_FAILED"
    http_status = 503


@dataclass(frozen=True, slots=True)
class StructuralLintFinding:
    code: str
    severity: Literal["blocker", "error", "warning", "info"]
    line: int | None
    column: int | None


@dataclass(frozen=True, slots=True)
class PreparedPublicationArtifact:
    raw_sha256: str
    raw_size_bytes: int
    extraction: ArtifactExtractionSpec
    lint_findings: tuple[StructuralLintFinding, ...]
    preparation_sha256: str
    publishable: bool


class PublicationBlobLease(Protocol):
    def close(self) -> None: ...


class PublicationBlobObservation(Protocol):
    blob: StagedVerifiedBlob
    lease: PublicationBlobLease


class PublicationBlobStager(Protocol):
    def stage_and_verify(
        self,
        content: bytes,
        *,
        expected_sha256: str,
        media_type: str,
        effective_at_ms: int,
        normalized_content_sha256: str | None,
        normalizer_version: str | None,
    ) -> PublicationBlobObservation: ...


class ExistingPublicationBlobVerifier(Protocol):
    def verify_existing(
        self,
        *,
        expected_sha256: str,
        expected_size: int,
        media_type: str,
        normalized_content_sha256: str | None,
        normalizer_version: str | None,
    ) -> tuple[PublicationBlobObservation, bytes]: ...


@dataclass(frozen=True, slots=True)
class CreateManualPreviewCommand:
    request_id: str
    operation_id: str
    actor_user_id: str
    actor_scope_epoch: int
    markdown: str
    title: str | None = None
    target: HigherPublicationTargetDescriptor | None = None
    request_meta: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CreateUploadPreviewCommand:
    request_id: str
    operation_id: str
    actor_user_id: str
    actor_scope_epoch: int
    content: bytes = field(repr=False)
    original_filename: str
    media_type: str
    title: str | None = None
    target: HigherPublicationTargetDescriptor | None = None
    request_meta: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CreateChatPreviewCommand:
    request_id: str
    operation_id: str
    actor_user_id: str
    actor_scope_epoch: int
    conversation_id: str
    first_sequence_no: int
    last_sequence_no: int
    title: str | None = None
    expected_source_snapshot_sha256: str | None = None
    target: HigherPublicationTargetDescriptor | None = None
    request_meta: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AuthorizedNotionPageSnapshot:
    actor_user_id: str
    page_id: str
    owner_user_id: str
    title: str
    revision: int | None
    updated_at: int | None
    markdown: str = field(repr=False)
    content_sha256: str
    asset_manifest_sha256: str
    source_snapshot_sha256: str


class NotionPageSnapshotProvider(Protocol):
    def capture(
        self, *, actor_user_id: str, page_id: str
    ) -> AuthorizedNotionPageSnapshot: ...


@dataclass(frozen=True, slots=True)
class CreateNotionPreviewCommand:
    request_id: str
    operation_id: str
    actor_user_id: str
    actor_scope_epoch: int
    page_id: str
    expected_source_snapshot_sha256: str | None = None
    target: HigherPublicationTargetDescriptor | None = None
    request_meta: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CancelPublicationPreviewCommand:
    request_id: str
    operation_id: str
    actor_user_id: str
    actor_scope_epoch: int
    preview_id: str
    request_meta: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class GetPublicationPreviewQuery:
    actor_user_id: str
    actor_scope_epoch: int
    preview_id: str


@dataclass(frozen=True, slots=True)
class PublishPreviewCommand:
    request_id: str
    operation_id: str
    actor_user_id: str
    actor_scope_epoch: int
    preview_id: str
    source_snapshot_sha256: str
    target_mode: Literal["personal", "part", "both", "explicit"]
    target: HigherPublicationTargetDescriptor | None = None
    personal_space_id: str | None = None
    request_meta: Mapping[str, object] = field(default_factory=dict)
    # Internal workflow input; no HTTP/UI selection contract is enabled yet.
    authored_snapshot_id: str | None = None


@dataclass(frozen=True, slots=True)
class PublicationResult:
    request_id: str
    operation_id: str
    publication_operation_id: str
    preview_id: str
    source_artifact_id: str
    target_mode: Literal["personal", "part", "both", "explicit"]
    lineage_group_id: str | None
    targets: tuple[Mapping[str, object], ...]
    target: HigherPublicationTargetDescriptor | None = None
    authored_snapshot_id: str | None = None
    reused_extraction_snapshot_id: str | None = None
    body_source: Literal["authored_wiki", "extraction", "preserved_wiki", "native_wiki", "pptx_role_wiki", "pdf_wiki", "pdf_visual_wiki", "office_hybrid_wiki", "pdf_continuation_wiki"] | None = None
    preserved_snapshot_id: str | None = None
    native_snapshot_id: str | None = None
    pptx_role_snapshot_id: str | None = None
    pdf_snapshot_id: str | None = None
    pdf_visual_snapshot_id: str | None = None
    office_hybrid_snapshot_id: str | None = None
    pdf_continuation_snapshot_id: str | None = None

    def to_mapping(self) -> dict[str, object]:
        result: dict[str, object] = {
            "lineage_group_id": self.lineage_group_id,
            "operation_id": self.operation_id,
            "preview_id": self.preview_id,
            "publication_operation_id": self.publication_operation_id,
            "request_id": self.request_id,
            "schema_version": 1,
            "source_artifact_id": self.source_artifact_id,
            "status": "committed",
            "target_mode": self.target_mode,
            "targets": [dict(target) for target in self.targets],
        }
        if self.target is not None:
            result["target"] = self.target.to_mapping()
        if self.authored_snapshot_id is not None:
            result["authored_snapshot_id"] = self.authored_snapshot_id
        if self.reused_extraction_snapshot_id is not None:
            result["reused_extraction_snapshot_id"] = self.reused_extraction_snapshot_id
        if self.body_source is not None:
            result["body_source"] = self.body_source
        if self.preserved_snapshot_id is not None:
            result["preserved_snapshot_id"] = self.preserved_snapshot_id
        if self.native_snapshot_id is not None:
            result["native_snapshot_id"] = self.native_snapshot_id
        if self.pptx_role_snapshot_id is not None:
            result["pptx_role_snapshot_id"] = self.pptx_role_snapshot_id
        if self.pdf_snapshot_id is not None:
            result["pdf_snapshot_id"] = self.pdf_snapshot_id
        if self.pdf_visual_snapshot_id is not None:
            result["pdf_visual_snapshot_id"] = self.pdf_visual_snapshot_id
        if self.office_hybrid_snapshot_id is not None:
            result["office_hybrid_snapshot_id"] = self.office_hybrid_snapshot_id
        if self.pdf_continuation_snapshot_id is not None:
            result["pdf_continuation_snapshot_id"] = self.pdf_continuation_snapshot_id
        return result


@dataclass(frozen=True, slots=True)
class QueuedPublicationPreviewResult:
    request_id: str
    operation_id: str
    preview_id: str
    job_id: str
    status: Literal["queued", "extracting"]
    status_url: str
    expires_at: str
    created_at: str
    updated_at: str

    def to_mapping(self) -> dict[str, object]:
        return {
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "job_id": self.job_id,
            "operation_id": self.operation_id,
            "preview_id": self.preview_id,
            "request_id": self.request_id,
            "status": self.status,
            "status_url": self.status_url,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True, slots=True)
class LoadedPublicationSource:
    content: bytes = field(repr=False)
    media_type: str
    original_filename: str | None
    lease: PublicationBlobLease = field(repr=False, compare=False)


class PublicationSourceLoader(Protocol):
    def load(
        self,
        *,
        artifact_id: str,
        expected_sha256: str,
        expected_size: int,
        expected_media_type: str,
    ) -> LoadedPublicationSource: ...


@dataclass(frozen=True, slots=True)
class ExtractedPublicationContent:
    markdown: str | None
    failure_code: str | None
    extractor_name: str
    extractor_version: str
    warnings: tuple[str, ...] = ()
    assets: tuple["EmbeddedPublicationAsset", ...] = ()
    protected_provenance: ProtectedExtractionProvenance | None = None


@dataclass(frozen=True, slots=True)
class EmbeddedPublicationAsset:
    """One bounded embedded child whose bytes were read outside SQLite."""

    content: bytes = field(repr=False)
    logical_name: str
    media_type: str


@dataclass(frozen=True, slots=True)
class _ChatSourceSnapshot:
    conversation_id: str
    conversation_space_id: str
    conversation_row_version: int
    first_sequence_no: int
    last_sequence_no: int
    title: str
    markdown: str = field(repr=False)
    source_snapshot_sha256: str


class PublicationExtractor(Protocol):
    def extract(
        self,
        content: bytes,
        *,
        media_type: str,
        original_filename: str | None,
    ) -> ExtractedPublicationContent: ...


@dataclass(frozen=True, slots=True)
class _PreparedPreviewExtraction:
    prepared: PreparedPublicationArtifact
    title: str
    failure_code: str | None
    embedded_assets: tuple[CanonicalEmbeddedAssetSpec, ...] = ()
    protected_provenance: ProtectedExtractionProvenance | None = None


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _safe_code(value: object, *, field: str) -> str:
    if not isinstance(value, str) or _SAFE_CODE_RE.fullmatch(value) is None:
        raise PublicationSourcePreparationError(f"{field} is invalid.")
    return value


def _safe_text(value: object, *, field: str, maximum: int = _MAX_SAFE_TEXT) -> str:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= maximum
        or value != value.strip()
        or "\x00" in value
        or any(ord(character) < 0x20 and character not in "\t" for character in value)
    ):
        raise PublicationSourcePreparationError(f"{field} is invalid.")
    return value


def normalize_markdown_lf(markdown: str) -> bytes:
    """Return the frozen UTF-8/LF normalization used only as evidence."""

    if not isinstance(markdown, str) or "\x00" in markdown:
        raise PublicationSourcePreparationError("Extracted Markdown is invalid.")
    return markdown.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")


def _line_column(markdown: str, offset: int) -> tuple[int, int]:
    line = markdown.count("\n", 0, offset) + 1
    previous = markdown.rfind("\n", 0, offset)
    return line, offset - previous


def lint_markdown_structure(markdown: str) -> tuple[StructuralLintFinding, ...]:
    """Run the current versioned deterministic T0 structural policy."""

    try:
        findings = lint_markdown_deterministic(markdown)
    except DeterministicIngestPolicyError as exc:
        raise PublicationSourcePreparationError(
            "Extracted Markdown is invalid."
        ) from exc
    return tuple(
        StructuralLintFinding(
            finding.code,
            finding.severity,
            finding.line,
            finding.column,
        )
        for finding in findings
    )


def _warnings(values: Sequence[str]) -> tuple[str, ...]:
    if isinstance(values, (str, bytes, bytearray)) or not isinstance(values, Sequence):
        raise PublicationSourcePreparationError("warnings must be a sequence.")
    parsed = tuple(_safe_code(value, field="warning") for value in values)
    if len(parsed) > 100 or len(set(parsed)) != len(parsed):
        raise PublicationSourcePreparationError("warnings are invalid.")
    return tuple(sorted(parsed))


def _preparation_hash(
    *,
    raw_sha256: str,
    raw_size_bytes: int,
    extraction: ArtifactExtractionSpec,
    lint_findings: tuple[StructuralLintFinding, ...],
) -> str:
    body = extraction.body
    payload = {
        "extraction": {
            "body": None
            if body is None
            else {
                "content_blob_hash": body.content_blob_hash,
                "content_sha256": body.content_sha256,
                "content_size_bytes": body.content_size_bytes,
            },
            "created_at_ms": extraction.created_at_ms,
            "extracted_sha256": extraction.extracted_sha256,
            "extracted_size_bytes": extraction.extracted_size_bytes,
            "extractor_name": extraction.extractor_name,
            "extractor_version": extraction.extractor_version,
            "normalized_content_sha256": extraction.normalized_content_sha256,
            "normalizer_version": extraction.normalizer_version,
            "status": extraction.status,
            "warnings": json.loads(extraction.warnings_json),
        },
        "lint_findings": [
            {
                "code": finding.code,
                "column": finding.column,
                "line": finding.line,
                "severity": finding.severity,
            }
            for finding in lint_findings
        ],
        "raw_sha256": raw_sha256,
        "raw_size_bytes": raw_size_bytes,
        "schema_version": 1,
    }
    return _sha256(_canonical_json(payload).encode("utf-8"))


def prepare_publication_artifact(
    raw_bytes: bytes,
    *,
    extracted_markdown: str | None,
    extraction_failure_code: str | None,
    extractor_name: str,
    extractor_version: str,
    warnings: Sequence[str] = (),
    created_at_ms: int,
) -> PreparedPublicationArtifact:
    """Bind raw bytes to one ready or failed immutable extraction result.

    Exactly one of ``extracted_markdown`` and ``extraction_failure_code`` is
    required.  Extractors and model-assisted work, if any, must have completed
    before calling this pure function and before any database transaction.
    """

    if not isinstance(raw_bytes, bytes):
        raise PublicationSourcePreparationError("raw_bytes must be exact bytes.")
    if (
        isinstance(created_at_ms, bool)
        or not isinstance(created_at_ms, int)
        or created_at_ms < 0
    ):
        raise PublicationSourcePreparationError("created_at_ms is invalid.")
    extractor_name = _safe_text(extractor_name, field="extractor_name", maximum=120)
    extractor_version = _safe_text(
        extractor_version, field="extractor_version", maximum=120
    )
    warning_codes = _warnings(warnings)
    if (extracted_markdown is None) == (extraction_failure_code is None):
        raise PublicationSourcePreparationError(
            "Exactly one extraction outcome is required."
        )

    raw_sha256 = _sha256(raw_bytes)
    raw_size = len(raw_bytes)
    if extracted_markdown is None:
        failure_code = _safe_code(
            extraction_failure_code, field="extraction_failure_code"
        )
        warning_codes = tuple(sorted(set((*warning_codes, failure_code))))
        extraction = ArtifactExtractionSpec(
            status="failed",
            extracted_sha256=None,
            normalized_content_sha256=None,
            extracted_size_bytes=None,
            normalizer_version=None,
            extractor_name=extractor_name,
            extractor_version=extractor_version,
            warnings_json=_canonical_json(list(warning_codes)),
            body=None,
            created_at_ms=created_at_ms,
        )
        lint = (
            StructuralLintFinding("extraction_failed", "blocker", None, None),
        )
    else:
        if not isinstance(extracted_markdown, str):
            raise PublicationSourcePreparationError("Extracted Markdown is invalid.")
        exact_bytes = extracted_markdown.encode("utf-8")
        exact_sha256 = _sha256(exact_bytes)
        normalized_sha256 = _sha256(normalize_markdown_lf(extracted_markdown))
        extraction = ArtifactExtractionSpec(
            status="ready",
            extracted_sha256=exact_sha256,
            normalized_content_sha256=normalized_sha256,
            extracted_size_bytes=len(exact_bytes),
            normalizer_version=NORMALIZER_VERSION,
            extractor_name=extractor_name,
            extractor_version=extractor_version,
            warnings_json=_canonical_json(list(warning_codes)),
            body=PreparedMarkdownBody(
                content_md=extracted_markdown,
                content_blob_hash=None,
                content_size_bytes=len(exact_bytes),
                content_sha256=exact_sha256,
            ),
            created_at_ms=created_at_ms,
        )
        lint = lint_markdown_structure(extracted_markdown)

    preparation_sha256 = _preparation_hash(
        raw_sha256=raw_sha256,
        raw_size_bytes=raw_size,
        extraction=extraction,
        lint_findings=lint,
    )
    if _SHA_RE.fullmatch(preparation_sha256) is None:  # pragma: no cover
        raise AssertionError("The preparation hash is invalid.")
    return PreparedPublicationArtifact(
        raw_sha256=raw_sha256,
        raw_size_bytes=raw_size,
        extraction=extraction,
        lint_findings=lint,
        preparation_sha256=preparation_sha256,
        publishable=not any(
            finding.severity in {"blocker", "error"} for finding in lint
        ),
    )


def build_source_artifact_bundle(
    prepared: PreparedPublicationArtifact,
    *,
    ids: ArtifactIds,
    origin_type: Literal["upload", "notion", "chat", "manual"],
    origin_key: str | None,
    origin_revision_key: str | None,
    raw_blob: StagedVerifiedBlob,
    logical_name: str,
    original_filename: str | None,
    publisher_user_id: str,
    created_at_ms: int,
    notion_snapshot: AuthorizedNotionPageSnapshot | None = None,
) -> SourceArtifactBundle:
    """Bind pure preparation evidence to one physically staged raw blob.

    The helper emits exact, body-free metadata rather than accepting arbitrary
    caller JSON.  Repository validation remains authoritative for the physical
    row shapes and canonical foreign keys.
    """

    if not isinstance(prepared, PreparedPublicationArtifact):
        raise TypeError("A PreparedPublicationArtifact is required.")
    if not isinstance(raw_blob, StagedVerifiedBlob):
        raise TypeError("A StagedVerifiedBlob is required.")
    if (
        raw_blob.sha256 != prepared.raw_sha256
        or raw_blob.size_bytes != prepared.raw_size_bytes
    ):
        raise PublicationSourcePreparationError(
            "The staged raw blob does not match the prepared source bytes."
        )
    metadata: dict[str, object] = {
        "lint_findings": [
            {
                "code": finding.code,
                "column": finding.column,
                "line": finding.line,
                "severity": finding.severity,
            }
            for finding in prepared.lint_findings
        ],
        "publishable": prepared.publishable,
        "schema_version": 1,
        "preparation_sha256": prepared.preparation_sha256,
    }
    owner_snapshot = {
        "publisher_user_id": publisher_user_id,
        "schema_version": 1,
    }
    if origin_type == "notion":
        if not isinstance(notion_snapshot, AuthorizedNotionPageSnapshot):
            raise PublicationSourcePreparationError(
                "Notion source provenance is required."
            )
        try:
            notion_snapshot = _validate_notion_snapshot(
                notion_snapshot,
                actor_user_id=publisher_user_id,
                requested_page_id=notion_snapshot.page_id,
            )
        except PublicationPreviewServiceError as exc:
            raise PublicationSourcePreparationError(
                "Notion source provenance is invalid."
            ) from exc
        if (
            notion_snapshot.actor_user_id != publisher_user_id
            or origin_key
            != f"notion:{notion_snapshot.owner_user_id}:{notion_snapshot.page_id}"
            or origin_revision_key != notion_snapshot.source_snapshot_sha256
            or prepared.raw_sha256 != notion_snapshot.content_sha256
            or prepared.extraction.extracted_sha256
            != notion_snapshot.content_sha256
            or prepared.extraction.body is None
            or prepared.extraction.body.content_md != notion_snapshot.markdown
        ):
            raise PublicationSourcePreparationError(
                "Notion source provenance does not match the artifact identity."
            )
        metadata["notion_provenance"] = {
            "asset_manifest_sha256": notion_snapshot.asset_manifest_sha256,
            "owner_user_id": notion_snapshot.owner_user_id,
            "page_id": notion_snapshot.page_id,
            "revision": notion_snapshot.revision,
            "source_snapshot_sha256": notion_snapshot.source_snapshot_sha256,
            "updated_at": notion_snapshot.updated_at,
        }
    elif notion_snapshot is not None:
        raise PublicationSourcePreparationError(
            "Notion provenance is not valid for this source type."
        )
    return SourceArtifactBundle(
        ids=ids,
        origin_type=origin_type,
        origin_key=origin_key,
        origin_revision_key=origin_revision_key,
        raw_blob=raw_blob,
        logical_name=logical_name,
        original_filename=original_filename,
        publisher_user_id=publisher_user_id,
        source_owner_snapshot_json=_canonical_json(owner_snapshot),
        metadata_json=_canonical_json(metadata),
        metadata_schema_version=1,
        created_at_ms=created_at_ms,
        extraction=prepared.extraction,
    )


def build_pending_source_artifact_bundle(
    *,
    ids: ArtifactIds,
    origin_type: Literal["upload", "notion", "chat"],
    origin_key: str | None,
    origin_revision_key: str | None,
    raw_blob: StagedVerifiedBlob,
    logical_name: str,
    original_filename: str | None,
    publisher_user_id: str,
    created_at_ms: int,
) -> SourceArtifactBundle:
    """Build the exact body-free metadata for one queued raw source."""

    metadata = {
        "ingestion_state": "pending_extraction",
        "raw_sha256": raw_blob.sha256,
        "raw_size_bytes": raw_blob.size_bytes,
        "schema_version": 1,
    }
    return SourceArtifactBundle(
        ids=ids,
        origin_type=origin_type,
        origin_key=origin_key,
        origin_revision_key=origin_revision_key,
        raw_blob=raw_blob,
        logical_name=logical_name,
        original_filename=original_filename,
        publisher_user_id=publisher_user_id,
        source_owner_snapshot_json=_canonical_json(
            {"publisher_user_id": publisher_user_id, "schema_version": 1}
        ),
        metadata_json=_canonical_json(metadata),
        metadata_schema_version=1,
        created_at_ms=created_at_ms,
        extraction=None,
    )


def _preview_uuid(value: object, *, field_name: str) -> str:
    if not isinstance(value, str) or _UUID_RE.fullmatch(value) is None:
        raise PublicationPreviewValidationError(
            f"{field_name} must be a canonical lowercase UUID."
        )
    return value


def _preview_user(value: object) -> str:
    if not isinstance(value, str) or _USER_RE.fullmatch(value) is None:
        raise PublicationPreviewValidationError("actor_user_id is invalid.")
    return value


def _preview_epoch(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise PublicationPreviewValidationError("actor_scope_epoch is invalid.")
    return value


def _manual_markdown(value: object) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 2_000_000:
        raise PublicationPreviewValidationError("markdown is invalid.")
    if "\x00" in value:
        raise PublicationPreviewValidationError("markdown is invalid.")
    return value


def _manual_title(value: object, markdown: str) -> str:
    if value is not None:
        try:
            return _safe_text(value, field="title", maximum=500)
        except PublicationSourcePreparationError as exc:
            raise PublicationPreviewValidationError(str(exc)) from exc
    for line in markdown.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        candidate = line.strip()
        if not candidate:
            continue
        if candidate.startswith("#"):
            candidate = candidate.lstrip("#").strip()
        if candidate:
            return candidate[:500]
    return "Untitled"


def _preview_request_meta(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise PublicationPreviewValidationError("request_meta must be an object.")
    safe: dict[str, object] = {}
    for raw_key, raw_value in value.items():
        if not isinstance(raw_key, str):
            raise PublicationPreviewValidationError(
                "request_meta keys must be strings."
            )
        key = raw_key.strip().lower()
        if any(fragment in key for fragment in _SENSITIVE_META_FRAGMENTS):
            raise PublicationPreviewValidationError(
                "Sensitive request metadata cannot enter audit storage."
            )
        if key not in _SAFE_META_KEYS:
            continue
        if raw_value is not None and (
            not isinstance(raw_value, str) or len(raw_value) > 500
        ):
            raise PublicationPreviewValidationError(
                "request_meta values are invalid."
            )
        safe[key] = raw_value
    _canonical_json(safe)
    return safe


def _derived_uuid(namespace: UUID, *parts: str) -> str:
    return str(uuid5(namespace, "\x1f".join(parts)))


def publication_preview_id(actor_user_id: str, operation_id: str) -> str:
    return _derived_uuid(
        _PREVIEW_NAMESPACE,
        _preview_user(actor_user_id),
        _preview_uuid(operation_id, field_name="operation_id"),
    )


def publication_artifact_ids(
    actor_user_id: str, operation_id: str
) -> ArtifactIds:
    actor = _preview_user(actor_user_id)
    operation = _preview_uuid(operation_id, field_name="operation_id")
    return ArtifactIds(
        artifact_id=_derived_uuid(_ARTIFACT_NAMESPACE, actor, operation),
        raw_manifest_id=_derived_uuid(_RAW_MANIFEST_NAMESPACE, actor, operation),
        extraction_snapshot_id=_derived_uuid(
            _EXTRACTION_NAMESPACE, actor, operation
        ),
    )


def _manual_request_sha256(
    *,
    actor_user_id: str,
    actor_scope_epoch: int,
    operation_id: str,
    raw_sha256: str,
    title: str | None,
    target: HigherPublicationTargetDescriptor | None,
) -> str:
    payload: dict[str, object] = {
        "actor_scope_epoch": actor_scope_epoch,
        "actor_user_id": actor_user_id,
        "operation_id": operation_id,
        "operation_type": "publication_preview.create",
        "raw_sha256": raw_sha256,
        "schema_version": 1,
        "source_type": "manual",
        "title": title,
    }
    if target is not None:
        payload["target"] = target.to_mapping()
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _upload_bytes(value: object) -> bytes:
    if not isinstance(value, bytes):
        raise PublicationPreviewValidationError("content must be exact upload bytes.")
    return value


def _upload_text(value: object, *, field_name: str, maximum: int) -> str:
    try:
        result = _safe_text(value, field=field_name, maximum=maximum)
    except PublicationSourcePreparationError as exc:
        raise PublicationPreviewValidationError(str(exc)) from exc
    return result


def _upload_title(value: object, filename: str) -> str:
    if value is not None:
        return _upload_text(value, field_name="title", maximum=500)
    candidate = filename.rsplit(".", 1)[0].strip()
    return candidate[:500] if candidate else "Untitled"


def _upload_request_sha256(
    *,
    actor_user_id: str,
    actor_scope_epoch: int,
    operation_id: str,
    raw_sha256: str,
    raw_size_bytes: int,
    original_filename: str,
    media_type: str,
    title: str | None,
    target: HigherPublicationTargetDescriptor | None,
) -> str:
    payload: dict[str, object] = {
                "actor_scope_epoch": actor_scope_epoch,
                "actor_user_id": actor_user_id,
                "media_type": media_type,
                "operation_id": operation_id,
                "operation_type": "publication_preview.create",
                "original_filename": original_filename,
                "raw_sha256": raw_sha256,
                "raw_size_bytes": raw_size_bytes,
                "schema_version": 1,
                "source_type": "upload",
                "title": title,
    }
    if target is not None:
        payload["target"] = target.to_mapping()
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _optional_snapshot_sha256(value: object) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str) or _SHA_RE.fullmatch(value) is None:
        raise PublicationPreviewValidationError(
            "expected_source_snapshot_sha256 is invalid."
        )
    return value


def _source_key(value: object, *, field_name: str, maximum: int = 160) -> str:
    try:
        return _safe_text(value, field=field_name, maximum=maximum)
    except PublicationSourcePreparationError as exc:
        raise PublicationPreviewValidationError(str(exc)) from exc


def _chat_sequence(value: object, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise PublicationPreviewValidationError(f"{field_name} is invalid.")
    return value


def _chat_request_sha256(
    *,
    actor_user_id: str,
    actor_scope_epoch: int,
    operation_id: str,
    conversation_id: str,
    first_sequence_no: int,
    last_sequence_no: int,
    title: str | None,
    expected_source_snapshot_sha256: str | None,
    target: HigherPublicationTargetDescriptor | None,
) -> str:
    payload: dict[str, object] = {
                "actor_scope_epoch": actor_scope_epoch,
                "actor_user_id": actor_user_id,
                "conversation_id": conversation_id,
                "expected_source_snapshot_sha256": (
                    expected_source_snapshot_sha256
                ),
                "first_sequence_no": first_sequence_no,
                "last_sequence_no": last_sequence_no,
                "operation_id": operation_id,
                "operation_type": "publication_preview.create",
                "schema_version": 1,
                "source_type": "chat",
                "title": title,
    }
    if target is not None:
        payload["target"] = target.to_mapping()
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _notion_request_sha256(
    *,
    actor_user_id: str,
    actor_scope_epoch: int,
    operation_id: str,
    page_id: str,
    expected_source_snapshot_sha256: str | None,
    target: HigherPublicationTargetDescriptor | None,
) -> str:
    payload: dict[str, object] = {
                "actor_scope_epoch": actor_scope_epoch,
                "actor_user_id": actor_user_id,
                "expected_source_snapshot_sha256": (
                    expected_source_snapshot_sha256
                ),
                "operation_id": operation_id,
                "operation_type": "publication_preview.create",
                "page_id": page_id,
                "schema_version": 1,
                "source_type": "notion",
    }
    if target is not None:
        payload["target"] = target.to_mapping()
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _publication_target(
    value: HigherPublicationTargetDescriptor | None,
) -> HigherPublicationTargetDescriptor | None:
    if value is None:
        return None
    if not isinstance(value, HigherPublicationTargetDescriptor):
        raise PublicationPreviewValidationError(
            "target must be an explicit higher-space descriptor."
        )
    try:
        descriptor = HigherPublicationTargetDescriptor(
            space_id=_preview_uuid(value.space_id, field_name="target.space_id"),
            kind=value.kind,
            capability=value.capability,
            role_assignment_id=_preview_uuid(
                value.role_assignment_id,
                field_name="target.role_assignment_id",
            ),
            role_assignment_generation=_preview_epoch(
                value.role_assignment_generation
            ),
        )
        target_descriptor_sha256(descriptor)
    except (TypeError, ValueError) as exc:
        raise PublicationPreviewValidationError(
            "The explicit publication target is invalid."
        ) from exc
    return descriptor


def _validate_notion_snapshot(
    value: object,
    *,
    actor_user_id: str,
    requested_page_id: str,
) -> AuthorizedNotionPageSnapshot:
    if not isinstance(value, AuthorizedNotionPageSnapshot):
        raise PublicationPreviewStagingError(
            "The Notion adapter returned an invalid snapshot."
        )
    actor = _preview_user(value.actor_user_id)
    owner = _preview_user(value.owner_user_id)
    page_id = _source_key(value.page_id, field_name="snapshot.page_id")
    if actor != actor_user_id or page_id != requested_page_id:
        raise PublicationPreviewAuthorizationError(
            "The Notion snapshot authority does not match the request."
        )
    title = _source_key(value.title, field_name="snapshot.title", maximum=500)
    if not isinstance(value.markdown, str) or len(value.markdown) > 2_000_000:
        raise PublicationPreviewStagingError(
            "The Notion snapshot Markdown is invalid."
        )
    if "\x00" in value.markdown:
        raise PublicationPreviewStagingError(
            "The Notion snapshot Markdown is invalid."
        )
    for field_name, raw in (("revision", value.revision), ("updated_at", value.updated_at)):
        if raw is not None and (
            isinstance(raw, bool) or not isinstance(raw, int) or raw < 0
        ):
            raise PublicationPreviewStagingError(
                f"The Notion snapshot {field_name} is invalid."
            )
    content_sha = hashlib.sha256(value.markdown.encode("utf-8")).hexdigest()
    if (
        value.content_sha256 != content_sha
        or not isinstance(value.asset_manifest_sha256, str)
        or _SHA_RE.fullmatch(value.asset_manifest_sha256) is None
        or not isinstance(value.source_snapshot_sha256, str)
        or _SHA_RE.fullmatch(value.source_snapshot_sha256) is None
    ):
        raise PublicationPreviewStagingError(
            "The Notion snapshot hashes are invalid."
        )
    source_identity = {
        "asset_manifest_sha256": value.asset_manifest_sha256,
        "content_sha256": content_sha,
        "owner_user_id": owner,
        "page_id": page_id,
        "revision": value.revision,
        "schema_version": 1,
        "updated_at": value.updated_at,
    }
    expected_snapshot = hashlib.sha256(
        _canonical_json(source_identity).encode("utf-8")
    ).hexdigest()
    if value.source_snapshot_sha256 != expected_snapshot:
        raise PublicationPreviewStagingError(
            "The Notion snapshot identity is inconsistent."
        )
    return AuthorizedNotionPageSnapshot(
        actor,
        page_id,
        owner,
        title,
        value.revision,
        value.updated_at,
        value.markdown,
        content_sha,
        value.asset_manifest_sha256,
        expected_snapshot,
    )


def _utc_iso(value: int) -> str:
    return (
        datetime.fromtimestamp(value / 1000, tz=timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _queued_result(
    *,
    request_id: str,
    operation_id: str,
    preview_id: str,
    job_id: str,
    created_at_ms: int,
) -> QueuedPublicationPreviewResult:
    created = _utc_iso(created_at_ms)
    return QueuedPublicationPreviewResult(
        request_id=request_id,
        operation_id=operation_id,
        preview_id=preview_id,
        job_id=job_id,
        status="queued",
        status_url=f"/api/knowledge/v1/publication-previews/{preview_id}",
        expires_at=_utc_iso(created_at_ms + PREVIEW_TTL_MS),
        created_at=created,
        updated_at=created,
    )


def _queued_result_from_mapping(value: object) -> QueuedPublicationPreviewResult:
    if not isinstance(value, Mapping) or set(value) != {
        "created_at",
        "expires_at",
        "job_id",
        "operation_id",
        "preview_id",
        "request_id",
        "status",
        "status_url",
        "updated_at",
    }:
        raise PublicationPreviewIdempotencyError(
            "The stored queued preview result is invalid."
        )
    try:
        result = QueuedPublicationPreviewResult(
            request_id=_preview_uuid(value["request_id"], field_name="request_id"),
            operation_id=_preview_uuid(
                value["operation_id"], field_name="operation_id"
            ),
            preview_id=_preview_uuid(value["preview_id"], field_name="preview_id"),
            job_id=_preview_uuid(value["job_id"], field_name="job_id"),
            status=value["status"],  # type: ignore[arg-type]
            status_url=str(value["status_url"]),
            expires_at=str(value["expires_at"]),
            created_at=str(value["created_at"]),
            updated_at=str(value["updated_at"]),
        )
    except PublicationPreviewValidationError as exc:
        raise PublicationPreviewIdempotencyError(str(exc)) from exc
    if result.status not in {"queued", "extracting"} or result.to_mapping() != dict(value):
        raise PublicationPreviewIdempotencyError(
            "The stored queued preview result is invalid."
        )
    return result


def _preview_findings(
    prepared: PreparedPublicationArtifact,
) -> tuple[PreviewFinding, ...]:
    result: list[PreviewFinding] = []
    for finding in prepared.lint_findings:
        evidence = hashlib.sha256(
            _canonical_json(
                {
                    "code": finding.code,
                    "column": finding.column,
                    "line": finding.line,
                    "rule_version": PUBLICATION_PREVIEW_RULE_VERSION,
                    "schema_version": 1,
                    "severity": finding.severity,
                    "source_snapshot_sha256": prepared.extraction.extracted_sha256,
                }
            ).encode("utf-8")
        ).hexdigest()
        result.append(
            PreviewFinding(
                kind=finding.code,
                severity=finding.severity,
                risk_tier="T0",
                visibility_effect=(
                    "publication_blocked"
                    if finding.severity in {"blocker", "error"}
                    else "badge"
                    if finding.severity == "warning"
                    else "none"
                ),
                evidence_sha256=evidence,
            )
        )
    return tuple(
        sorted(
            result,
            key=lambda item: (
                item.kind,
                item.severity,
                item.risk_tier,
                item.visibility_effect,
                item.evidence_sha256,
            ),
        )
    )


def _freeze_staged_blob_observation(
    observation: object,
    *,
    expected_sha256: str,
    expected_size: int,
    expected_media_type: str,
    effective_at_ms: int,
) -> tuple[StagedVerifiedBlob, PublicationBlobLease]:
    """Copy exact physical evidence before a service write transaction opens."""

    lease = getattr(observation, "lease", None)
    closer = getattr(lease, "close", None)
    blob = getattr(observation, "blob", None)
    expected_path = (
        f"{expected_sha256[:2]}/{expected_sha256[2:4]}/{expected_sha256}"
    )
    valid = (
        isinstance(blob, StagedVerifiedBlob)
        and callable(closer)
        and isinstance(blob.sha256, str)
        and blob.sha256 == expected_sha256
        and type(blob.size_bytes) is int
        and blob.size_bytes == expected_size
        and isinstance(blob.media_type, str)
        and blob.media_type == expected_media_type
        and isinstance(blob.relative_path, str)
        and blob.relative_path == expected_path
        and type(blob.created_at_ms) is int
        and blob.created_at_ms == effective_at_ms
        and type(blob.verified_at_ms) is int
        and blob.verified_at_ms == effective_at_ms
    )
    if not valid:
        if callable(closer):
            closer()
        raise PublicationPreviewStagingError(
            "The blob stager returned invalid physical evidence."
        )
    return (
        StagedVerifiedBlob(
            sha256=blob.sha256,
            size_bytes=blob.size_bytes,
            media_type=blob.media_type,
            relative_path=blob.relative_path,
            created_at_ms=blob.created_at_ms,
            verified_at_ms=blob.verified_at_ms,
        ),
        lease,
    )


def _close_publication_leases(leases: Sequence[PublicationBlobLease]) -> None:
    first_error: Exception | None = None
    for lease in reversed(tuple(leases)):
        try:
            lease.close()
        except Exception as exc:  # pragma: no cover - defensive cleanup boundary
            if first_error is None:
                first_error = exc
    if first_error is not None:
        raise first_error


class CanonicalPublicationSourceLoader:
    """Read one pending CAS source with a retained no-follow verification lease."""

    __slots__ = ("_factory", "_verifier")

    def __init__(
        self,
        factory: ConnectionFactory,
        verifier: ExistingPublicationBlobVerifier,
    ) -> None:
        if not isinstance(factory, ConnectionFactory):
            raise TypeError(
                "CanonicalPublicationSourceLoader requires a ConnectionFactory."
            )
        if not callable(getattr(verifier, "verify_existing", None)):
            raise TypeError(
                "CanonicalPublicationSourceLoader requires a blob verifier."
            )
        self._factory = factory
        self._verifier = verifier

    def load(
        self,
        *,
        artifact_id: str,
        expected_sha256: str,
        expected_size: int,
        expected_media_type: str,
    ) -> LoadedPublicationSource:
        artifact_id = _preview_uuid(artifact_id, field_name="artifact_id")
        if not isinstance(expected_sha256, str) or _SHA_RE.fullmatch(expected_sha256) is None:
            raise PermanentJobExecutionError("The expected raw digest is invalid.")
        if (
            isinstance(expected_size, bool)
            or not isinstance(expected_size, int)
            or expected_size < 0
        ):
            raise PermanentJobExecutionError("The expected raw size is invalid.")
        expected_media_type = _upload_text(
            expected_media_type, field_name="media_type", maximum=255
        )
        connection = self._factory.open(read_only=True)
        try:
            with transaction(connection, TransactionMode.DEFERRED):
                row = connection.execute(
                    """SELECT a.raw_sha256,a.raw_size_bytes,a.media_type,
                              a.original_filename,a.metadata_json,
                              a.body_retention_status,b.content_sha256,
                              b.size_bytes,b.body_retention_status AS blob_status,
                              bb.content_sha256 AS body_sha256
                         FROM source_artifact a
                         JOIN artifact_blob b
                           ON b.artifact_id=a.id AND b.role='raw' AND b.ordinal=0
                         JOIN artifact_blob_body bb ON bb.artifact_blob_id=b.id
                        WHERE a.id=?""",
                    (artifact_id,),
                ).fetchone()
                if row is None:
                    raise JobExpectedStateConflictError(
                        "The queued source artifact is unavailable."
                    )
                try:
                    metadata = json.loads(str(row["metadata_json"]))
                except (json.JSONDecodeError, TypeError, ValueError) as exc:
                    raise JobExpectedStateConflictError(
                        "The queued source metadata is invalid."
                    ) from exc
                expected_metadata = {
                    "ingestion_state": "pending_extraction",
                    "raw_sha256": expected_sha256,
                    "raw_size_bytes": expected_size,
                    "schema_version": 1,
                }
                if (
                    row["raw_sha256"] != expected_sha256
                    or row["raw_size_bytes"] != expected_size
                    or row["media_type"] != expected_media_type
                    or row["content_sha256"] != expected_sha256
                    or row["body_sha256"] != expected_sha256
                    or row["size_bytes"] != expected_size
                    or row["body_retention_status"] != "present"
                    or row["blob_status"] != "present"
                    or metadata != expected_metadata
                    or _canonical_json(metadata) != row["metadata_json"]
                ):
                    raise JobExpectedStateConflictError(
                        "The queued source artifact changed before extraction."
                    )
                original_filename = row["original_filename"]
        finally:
            connection.close()
        try:
            observation, content = self._verifier.verify_existing(
                expected_sha256=expected_sha256,
                expected_size=expected_size,
                media_type=expected_media_type,
                normalized_content_sha256=None,
                normalizer_version=None,
            )
        except KnowledgeCoreError:
            raise
        except Exception as exc:
            raise RetryableJobExecutionError(
                "The queued source bytes could not be verified."
            ) from exc
        lease = getattr(observation, "lease", None)
        blob = getattr(observation, "blob", None)
        expected_relative_path = (
            f"{expected_sha256[:2]}/{expected_sha256[2:4]}/{expected_sha256}"
        )
        if (
            not isinstance(content, bytes)
            or hashlib.sha256(content).hexdigest() != expected_sha256
            or len(content) != expected_size
            or not isinstance(blob, StagedVerifiedBlob)
            or not isinstance(blob.sha256, str)
            or blob.sha256 != expected_sha256
            or type(blob.size_bytes) is not int
            or blob.size_bytes != expected_size
            or not isinstance(blob.media_type, str)
            or blob.media_type != expected_media_type
            or not isinstance(blob.relative_path, str)
            or blob.relative_path != expected_relative_path
            or type(blob.created_at_ms) is not int
            or blob.created_at_ms != 0
            or type(blob.verified_at_ms) is not int
            or blob.verified_at_ms != 0
            or not callable(getattr(lease, "close", None))
        ):
            closer = getattr(lease, "close", None)
            if callable(closer):
                closer()
            raise PermanentJobExecutionError(
                "The queued source verifier returned inconsistent evidence."
            )
        return LoadedPublicationSource(
            content=content,
            media_type=expected_media_type,
            original_filename=original_filename,
            lease=lease,
        )


class Utf8PublicationExtractor:
    """Frozen deterministic text extractor used when no richer adapter is configured."""

    __slots__ = ()

    def extract(
        self,
        content: bytes,
        *,
        media_type: str,
        original_filename: str | None,
    ) -> ExtractedPublicationContent:
        del original_filename
        base_media = media_type.split(";", 1)[0].strip().lower()
        if base_media not in {"text/markdown", "text/plain", "application/json"}:
            return ExtractedPublicationContent(
                markdown=None,
                failure_code="unsupported_media_type",
                extractor_name="utf8-text",
                extractor_version="1",
            )
        try:
            markdown = content.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            return ExtractedPublicationContent(
                markdown=None,
                failure_code="invalid_utf8",
                extractor_name="utf8-text",
                extractor_version="1",
            )
        return ExtractedPublicationContent(
            markdown=markdown,
            failure_code=None,
            extractor_name="utf8-text",
            extractor_version="1",
        )


class PublicationPreviewExtractionHandler:
    """Exact ``publication_preview.extract/v1`` durable-job adapter."""

    _handler_version = "v1"
    _rule_version = PUBLICATION_PREVIEW_RULE_VERSION
    _expected_schema_version = 1

    __slots__ = (
        "_asset_stager",
        "_artifacts",
        "_audit",
        "_extractor",
        "_factory",
        "_jobs",
        "_loader",
        "_outbox",
        "_previews",
        "_users",
    )

    def __init__(
        self,
        factory: ConnectionFactory,
        source_loader: PublicationSourceLoader,
        extractor: PublicationExtractor,
        *,
        asset_stager: PublicationBlobStager | None = None,
        artifacts: ArtifactRepository | None = None,
        previews: PublicationPreviewRepository | None = None,
        jobs: JobRepository | None = None,
        users: UserRepository | None = None,
        audit: AuditRepository | None = None,
        outbox: OutboxRepository | None = None,
    ) -> None:
        if not isinstance(factory, ConnectionFactory):
            raise TypeError(
                "PublicationPreviewExtractionHandler requires a ConnectionFactory."
            )
        if not callable(getattr(source_loader, "load", None)):
            raise TypeError("A publication source loader is required.")
        if not callable(getattr(extractor, "extract", None)):
            raise TypeError("A publication extractor is required.")
        if asset_stager is not None and not callable(
            getattr(asset_stager, "stage_and_verify", None)
        ):
            raise TypeError("asset_stager must provide stage_and_verify.")
        self._factory = factory
        self._loader = source_loader
        self._extractor = extractor
        self._asset_stager = asset_stager
        self._artifacts = artifacts or ArtifactRepository()
        self._previews = previews or PublicationPreviewRepository()
        self._jobs = jobs or JobRepository()
        self._users = users or UserRepository()
        self._audit = audit or AuditRepository()
        self._outbox = outbox or OutboxRepository()

    def registered_handler(self) -> RegisteredJobHandler:
        return RegisteredJobHandler.build(
            job_type="publication_preview.extract",
            handler_version=self._handler_version,
            actor_kind="user",
            required_capability=Capability.PUBLISH,
            validate_input=self.validate_input,
            prepare=self.prepare,
            revalidate_expected_state=self.revalidate_expected_state,
            commit_effect=self.commit_effect,
            converge_terminal=self.converge_terminal,
        )

    @classmethod
    def validate_input(
        cls, input_ref: Mapping[str, object], expected_state: Mapping[str, object]
    ) -> None:
        if set(input_ref) != {"artifact_id", "preview_id", "rule_version"}:
            raise JobExpectedStateConflictError(
                "The publication preview job input shape is invalid."
            )
        artifact_id = _preview_uuid(input_ref["artifact_id"], field_name="artifact_id")
        preview_id = _preview_uuid(input_ref["preview_id"], field_name="preview_id")
        if input_ref["rule_version"] != cls._rule_version:
            raise JobExpectedStateConflictError(
                "The publication preview rule version is unsupported."
            )
        expected_keys = {
            "artifact_id",
            "media_type",
            "preview_id",
            "raw_sha256",
            "raw_size_bytes",
            "schema_version",
            "title",
        }
        if frozenset(expected_state) not in {
            frozenset((*expected_keys, *optional))
            for optional in (
                (), ("_authorization_fence",),
                ("protected_source_kind",),
                ("_authorization_fence", "protected_source_kind"),
            )
        }:
            raise JobExpectedStateConflictError(
                "The publication preview expected-state shape is invalid."
            )
        if ("protected_source_kind" in expected_state
                and expected_state["protected_source_kind"]
                != ProtectedSourceKind.COMPANY_POLICY_PROTECTED.value):
            raise JobExpectedStateConflictError(
                "The durable protected source claim is invalid."
            )
        if (
            expected_state["artifact_id"] != artifact_id
            or expected_state["preview_id"] != preview_id
            or expected_state["schema_version"] != cls._expected_schema_version
            or not isinstance(expected_state["raw_sha256"], str)
            or _SHA_RE.fullmatch(expected_state["raw_sha256"]) is None
            or isinstance(expected_state["raw_size_bytes"], bool)
            or not isinstance(expected_state["raw_size_bytes"], int)
            or expected_state["raw_size_bytes"] < 0
        ):
            raise JobExpectedStateConflictError(
                "The publication preview expected-state binding is invalid."
            )
        _upload_text(
            expected_state["media_type"], field_name="media_type", maximum=255
        )
        _upload_text(expected_state["title"], field_name="title", maximum=500)

    def prepare(self, job: DurableJobRecord) -> PreparedJobEffect:
        self.validate_input(job.input_ref, job.expected_state)
        if job.actor_user_id is None or job.operation_id is None:
            raise PermanentJobExecutionError(
                "The publication preview job has no user operation identity."
            )
        self._mark_extracting(job)
        expected = job.expected_state
        policy_claim = expected.get("protected_source_kind")
        if policy_claim is not None and self._factory.migration_catalog.latest_version < 60:
            raise PermanentJobExecutionError(
                "The policy-protected source schema is not activated."
            )
        loaded = self._loader.load(
            artifact_id=str(expected["artifact_id"]),
            expected_sha256=str(expected["raw_sha256"]),
            expected_size=int(expected["raw_size_bytes"]),
            expected_media_type=str(expected["media_type"]),
        )
        leases: list[PublicationBlobLease] = [loaded.lease]
        try:
            visual_extract = getattr(self._extractor, "extract_for_visual_preview", None)
            use_visual_extract = False
            if callable(visual_extract):
                connection = self._factory.open(read_only=True)
                try:
                    with transaction(connection, TransactionMode.DEFERRED):
                        use_visual_extract = connection.execute(
                            """SELECT 1 FROM pdf_visual_workflow_request q
                               JOIN publication_preview p ON p.id=q.preview_id
                               WHERE q.preview_id=? AND q.parent_job_id=?
                                 AND p.durable_job_id=? AND p.actor_user_id=?
                                 AND p.artifact_id=?""",
                            (job.input_ref["preview_id"], job.id, job.id,
                             job.actor_user_id, expected["artifact_id"]),
                        ).fetchone() is not None
                finally:
                    connection.close()
            if use_visual_extract:
                outcome = visual_extract(
                    loaded.content, media_type=loaded.media_type,
                    original_filename=loaded.original_filename,
                    actor_user_id=job.actor_user_id,
                )
            else:
                actor_extract = getattr(self._extractor, "extract_for_actor", None)
                if callable(actor_extract):
                    actor_options = ({"expected_protected_kind":
                        ProtectedSourceKind.COMPANY_POLICY_PROTECTED}
                        if policy_claim is not None else {})
                    outcome = actor_extract(
                        loaded.content, media_type=loaded.media_type,
                        original_filename=loaded.original_filename,
                        actor_user_id=job.actor_user_id,
                        **actor_options,
                    )
                else:
                    outcome = self._extractor.extract(
                        loaded.content, media_type=loaded.media_type,
                        original_filename=loaded.original_filename,
                    )
            if not isinstance(outcome, ExtractedPublicationContent):
                raise PermanentJobExecutionError(
                    "The publication extractor returned an invalid result."
                )
            if (outcome.protected_provenance is not None
                    and (type(outcome.protected_provenance) is not ProtectedExtractionProvenance
                         or outcome.failure_code is not None
                         or outcome.markdown is None)):
                raise PermanentJobExecutionError(
                    "The protected extraction provenance is invalid."
                )
            prepared_at = self._database_time()
            prepared = prepare_publication_artifact(
                loaded.content,
                extracted_markdown=outcome.markdown,
                extraction_failure_code=outcome.failure_code,
                extractor_name=outcome.extractor_name,
                extractor_version=outcome.extractor_version,
                warnings=outcome.warnings,
                created_at_ms=prepared_at,
            )
            embedded_assets = self._stage_embedded_assets(
                outcome,
                artifact_id=str(expected["artifact_id"]),
                effective_at_ms=prepared_at,
                leases=leases,
            )
            payload = self._prepare_payload(
                loaded=loaded, outcome=outcome, prepared=prepared,
                embedded_assets=embedded_assets, title=str(expected["title"]),
            )
        except RetryableJobExecutionError:
            _close_publication_leases(leases)
            raise
        except Exception as exc:
            try:
                _close_publication_leases(leases)
            except Exception:
                pass
            if isinstance(exc, PermanentJobExecutionError):
                raise
            raise PermanentJobExecutionError(
                "The publication extractor result is invalid."
            ) from exc
        status = "ready" if prepared.extraction.status == "ready" else "failed"
        return PreparedJobEffect(
            result={
                "preview_id": str(job.input_ref["preview_id"]),
                "schema_version": self._expected_schema_version,
                "source_snapshot_sha256": prepared.extraction.extracted_sha256,
                "status": status,
            },
            payload=payload,
            cleanup=lambda: _close_publication_leases(leases),
        )

    def _prepare_payload(self, *, loaded, outcome, prepared, embedded_assets, title):
        """Trusted successor seam; called before the commit transaction."""
        return _PreparedPreviewExtraction(
            prepared=prepared, title=title, failure_code=outcome.failure_code,
            embedded_assets=embedded_assets,
            protected_provenance=outcome.protected_provenance,
        )

    def _stage_embedded_assets(
        self,
        outcome: ExtractedPublicationContent,
        *,
        artifact_id: str,
        effective_at_ms: int,
        leases: list[PublicationBlobLease],
    ) -> tuple[CanonicalEmbeddedAssetSpec, ...]:
        assets = outcome.assets
        if not isinstance(assets, tuple) or len(assets) > 64:
            raise PermanentJobExecutionError(
                "The publication extractor returned an invalid asset bundle."
            )
        if outcome.failure_code is not None and assets:
            raise PermanentJobExecutionError(
                "A failed extraction cannot return embedded assets."
            )
        if assets and self._asset_stager is None:
            raise PermanentJobExecutionError(
                "Embedded assets require the canonical blob stager."
            )
        total_size = 0
        prepared: list[CanonicalEmbeddedAssetSpec] = []
        for ordinal, candidate in enumerate(assets, start=1):
            if not isinstance(candidate, EmbeddedPublicationAsset):
                raise PermanentJobExecutionError(
                    "The publication extractor returned an invalid embedded asset."
                )
            content = candidate.content
            if not isinstance(content, bytes) or not content or len(content) > 16_777_216:
                raise PermanentJobExecutionError(
                    "An embedded asset exceeds the canonical size boundary."
                )
            total_size += len(content)
            if total_size > 67_108_864:
                raise PermanentJobExecutionError(
                    "The embedded asset bundle exceeds the canonical size boundary."
                )
            logical_name = _upload_text(
                candidate.logical_name,
                field_name="embedded_asset.logical_name",
                maximum=500,
            )
            media_type = _upload_text(
                candidate.media_type,
                field_name="embedded_asset.media_type",
                maximum=255,
            )
            sha256 = _sha256(content)
            assert self._asset_stager is not None
            observation = self._asset_stager.stage_and_verify(
                content,
                expected_sha256=sha256,
                media_type=media_type,
                effective_at_ms=effective_at_ms,
                normalized_content_sha256=None,
                normalizer_version=None,
            )
            blob, lease = _freeze_staged_blob_observation(
                observation,
                expected_sha256=sha256,
                expected_size=len(content),
                expected_media_type=media_type,
                effective_at_ms=effective_at_ms,
            )
            leases.append(lease)
            manifest_id = str(
                uuid5(
                    _EMBEDDED_ASSET_NAMESPACE,
                    f"{artifact_id}:{ordinal}:{sha256}:{logical_name}:{media_type}",
                )
            )
            prepared.append(
                CanonicalEmbeddedAssetSpec(
                    manifest_id=manifest_id,
                    blob=blob,
                    logical_name=logical_name,
                    ordinal=ordinal,
                )
            )
        return tuple(prepared)

    def _database_time(self) -> int:
        connection = self._factory.open(read_only=True)
        try:
            with transaction(connection, TransactionMode.DEFERRED):
                return db_now_ms(connection)
        finally:
            connection.close()

    def _mark_extracting(self, job: DurableJobRecord) -> None:
        connection = self._factory.open()
        try:
            with transaction(connection):
                current_job = self._jobs.require(connection, job.id)
                if current_job.state != "running" or current_job.attempt_count != job.attempt_count:
                    raise JobExpectedStateConflictError(
                        "The publication preview job is not running under this attempt."
                    )
                if job.actor_user_id is None:
                    raise JobExpectedStateConflictError(
                        "The publication preview job actor is unavailable."
                    )
                current = self._previews.require_for_actor(
                    connection,
                    preview_id=str(job.input_ref["preview_id"]),
                    actor_user_id=job.actor_user_id,
                )
                if (
                    current.artifact_id != job.input_ref["artifact_id"]
                    or current.durable_job_id != job.id
                    or current.rule_version != job.input_ref["rule_version"]
                    or current.status not in {"queued", "extracting"}
                ):
                    raise JobExpectedStateConflictError(
                        "The queued preview no longer matches its extraction job."
                    )
                updated = self._previews.mark_extracting(
                    connection,
                    preview_id=current.preview_id,
                    updated_at_ms=db_now_ms(connection),
                )
                self._append_worker_event(
                    connection,
                    job=current_job,
                    preview=updated,
                    outcome="extracting",
                    event_type="publication.preview.extracting",
                    now_ms=updated.updated_at_ms,
                )
        finally:
            connection.close()

    def revalidate_expected_state(
        self, connection: ManagedConnection, job: DurableJobRecord
    ) -> None:
        self.validate_input(job.input_ref, job.expected_state)
        if job.actor_user_id is None:
            raise JobExpectedStateConflictError(
                "The publication preview job actor is unavailable."
            )
        preview = self._previews.require_for_actor(
            connection,
            preview_id=str(job.input_ref["preview_id"]),
            actor_user_id=job.actor_user_id,
        )
        expected = job.expected_state
        source = connection.execute(
            """SELECT raw_sha256,raw_size_bytes,media_type,publisher_user_id,
                      body_retention_status
                 FROM source_artifact WHERE id=?""",
            (str(expected["artifact_id"]),),
        ).fetchone()
        extraction = connection.execute(
            "SELECT 1 FROM extraction_snapshot WHERE artifact_id=? LIMIT 1",
            (str(expected["artifact_id"]),),
        ).fetchone()
        if (
            preview.status != "extracting"
            or preview.artifact_id != expected["artifact_id"]
            or preview.durable_job_id != job.id
            or preview.rule_version != self._rule_version
            or source is None
            or source["raw_sha256"] != expected["raw_sha256"]
            or source["raw_size_bytes"] != expected["raw_size_bytes"]
            or source["media_type"] != expected["media_type"]
            or source["publisher_user_id"] != job.actor_user_id
            or source["body_retention_status"] != "present"
            or extraction is not None
        ):
            raise JobExpectedStateConflictError(
                "The publication preview expected state changed before commit."
            )

    def commit_effect(
        self,
        connection: ManagedConnection,
        job: DurableJobRecord,
        prepared: PreparedJobEffect,
    ) -> None:
        if not isinstance(prepared.payload, _PreparedPreviewExtraction):
            raise PermanentJobExecutionError(
                "The prepared publication preview payload is invalid."
            )
        if job.actor_user_id is None or job.operation_id is None or job.space_id is None:
            raise PermanentJobExecutionError(
                "The publication preview job binding is incomplete."
            )
        payload = prepared.payload
        expected = job.expected_state
        policy_claim = expected.get("protected_source_kind")
        policy_evidence = payload.protected_provenance
        if ((policy_claim is not None)
                != (policy_evidence is not None and policy_evidence.source_kind
                    is ProtectedSourceKind.COMPANY_POLICY_PROTECTED)):
            # The web's durable claim and the worker's provider extraction must
            # agree. A changed policy signal cannot silently become ordinary.
            if payload.prepared.extraction.status == "ready":
                raise PermanentJobExecutionError(
                    "The policy-protected source claim changed before commit."
                )
        if (
            payload.prepared.raw_sha256 != expected["raw_sha256"]
            or payload.prepared.raw_size_bytes != expected["raw_size_bytes"]
            or payload.title != expected["title"]
        ):
            raise PermanentJobExecutionError(
                "The prepared publication source differs from the durable input."
            )
        preview = self._previews.require_for_actor(
            connection,
            preview_id=str(job.input_ref["preview_id"]),
            actor_user_id=job.actor_user_id,
        )
        ids = publication_artifact_ids(job.actor_user_id, job.operation_id)
        if ids.artifact_id != preview.artifact_id:
            raise PermanentJobExecutionError(
                "The extraction identity differs from the queued artifact."
            )
        self._store_extraction(connection, ids=ids, payload=payload)
        self._store_protected_provenance(connection, ids=ids, payload=payload)
        result = self._queued_operation_result(connection, job, preview)
        now_ms = db_now_ms(connection)
        if payload.prepared.extraction.status == "ready":
            extracted_sha = payload.prepared.extraction.extracted_sha256
            extracted_size = payload.prepared.extraction.extracted_size_bytes
            if extracted_sha is None or extracted_size is None:
                raise PermanentJobExecutionError(
                    "The ready extraction has incomplete evidence."
                )
            warnings_value = json.loads(payload.prepared.extraction.warnings_json)
            if not isinstance(warnings_value, list) or any(
                not isinstance(value, str) for value in warnings_value
            ):
                raise PermanentJobExecutionError(
                    "The ready extraction warnings are invalid."
                )
            terminal = self._previews.complete_ready(
                connection,
                ReadyPreviewDraft(
                    preview_id=preview.preview_id,
                    request_id=result.request_id,
                    operation_id=result.operation_id,
                    actor_user_id=preview.actor_user_id,
                    artifact_id=preview.artifact_id,
                    extraction_snapshot_id=ids.extraction_snapshot_id,
                    durable_job_id=job.id,
                    source_snapshot_sha256=extracted_sha,
                    captured_part_id=preview.captured_part_id,
                    captured_scope_epoch=preview.captured_scope_epoch,
                    available_target_modes=preview.available_target_modes,  # type: ignore[arg-type]
                    rule_version=preview.rule_version,
                    title=payload.title,
                    size_bytes=extracted_size,
                    warnings=tuple(warnings_value),
                    deterministic_findings=_preview_findings(payload.prepared),
                    expires_at_ms=preview.expires_at_ms,
                    created_at_ms=preview.created_at_ms,
                    completed_at_ms=now_ms,
                ),
            )
            outcome = "ready"
            event_type = "publication.preview.ready"
        else:
            code = (payload.failure_code or "extraction_failed").upper()
            terminal = self._previews.fail(
                connection,
                preview_id=preview.preview_id,
                error={
                    "code": code,
                    "message": "The source could not be extracted safely.",
                },
                completed_at_ms=now_ms,
            )
            outcome = "failed"
            event_type = "publication.preview.failed"
        self._append_worker_event(
            connection,
            job=job,
            preview=terminal,
            outcome=outcome,
            event_type=event_type,
            now_ms=now_ms,
        )

    def _store_extraction(self, connection, *, ids, payload):
        """Caller transaction owns both extraction storage and terminal events."""
        self._artifacts.put_source_extraction(
            connection, artifact_id=ids.artifact_id,
            extraction_snapshot_id=ids.extraction_snapshot_id,
            extraction=payload.prepared.extraction,
            embedded_assets=payload.embedded_assets,
        )

    def _store_protected_provenance(self, connection, *, ids, payload) -> None:
        evidence = payload.protected_provenance
        if self._factory.migration_catalog.latest_version < 50:
            # The retained v49 runtime must keep serving until the explicit
            # v50 activation. No table exists there, so old receipts and
            # already-queued jobs continue their historical behavior.
            return
        protected_extractors = {
            "canonical-approved-owner-pdf",
            "canonical-authorized-protected-snapshot",
        }
        if (payload.prepared.extraction.status == "ready"
                and payload.prepared.extraction.extractor_name in protected_extractors
                and evidence is None):
            raise PermanentJobExecutionError(
                "A protected extraction has no retained provenance."
            )
        if evidence is None:
            return
        if (payload.prepared.extraction.status != "ready"
                or evidence.original_sha256 != payload.prepared.raw_sha256):
            raise PermanentJobExecutionError(
                "The protected source provenance differs from its raw artifact."
            )
        values = evidence.insert_values(
            extraction_snapshot_id=ids.extraction_snapshot_id,
            artifact_id=ids.artifact_id,
            created_at=payload.prepared.extraction.created_at_ms,
        )
        fields = tuple(values)
        connection.execute(
            "INSERT INTO protected_extraction_provenance(" + ",".join(fields)
            + ") VALUES(" + ",".join("?" for _ in fields) + ")",
            tuple(values[field] for field in fields),
        )

    def converge_terminal(
        self, connection: ManagedConnection, job: DurableJobRecord
    ) -> None:
        """Close the preview in the same transaction as a terminal job state."""

        self.validate_input(job.input_ref, job.expected_state)
        if job.actor_user_id is None or job.completed_at_ms is None:
            raise JobExpectedStateConflictError(
                "The terminal publication preview job binding is incomplete."
            )
        preview = self._previews.require_for_actor(
            connection,
            preview_id=str(job.input_ref["preview_id"]),
            actor_user_id=job.actor_user_id,
        )
        if (
            preview.artifact_id != job.input_ref["artifact_id"]
            or preview.durable_job_id != job.id
            or preview.rule_version != job.input_ref["rule_version"]
        ):
            raise JobExpectedStateConflictError(
                "The terminal job no longer identifies its preview."
            )
        if job.state == "cancelled":
            if preview.status == "cancelled":
                return
            terminal = self._previews.cancel(
                connection,
                preview_id=preview.preview_id,
                completed_at_ms=job.completed_at_ms,
            )
            outcome = "cancelled"
            event_type = "publication.preview.cancelled"
        elif job.state in {"failed", "dead"}:
            if preview.status == "failed":
                return
            error = job.error
            raw_code = None if error is None else error.get("code")
            code = (
                raw_code
                if isinstance(raw_code, str)
                and re.fullmatch(r"[A-Z][A-Z0-9_]{0,79}", raw_code)
                else "JOB_EXECUTION_ERROR"
            )
            terminal = self._previews.fail(
                connection,
                preview_id=preview.preview_id,
                error={
                    "code": code,
                    "message": "The source extraction job ended without a result.",
                },
                completed_at_ms=job.completed_at_ms,
            )
            outcome = "failed"
            event_type = "publication.preview.failed"
        else:
            raise JobExpectedStateConflictError(
                "The job is not terminal for preview convergence."
            )
        self._append_worker_event(
            connection,
            job=job,
            preview=terminal,
            outcome=outcome,
            event_type=event_type,
            now_ms=job.completed_at_ms,
        )

    def _queued_operation_result(
        self,
        connection: ManagedConnection,
        job: DurableJobRecord,
        preview: PublicationPreviewRecord,
    ) -> QueuedPublicationPreviewResult:
        if job.actor_user_id is None or job.operation_id is None:
            raise JobExpectedStateConflictError(
                "The publication preview operation binding is incomplete."
            )
        dedup_id = _derived_uuid(
            _DEDUP_NAMESPACE,
            job.actor_user_id,
            "publication_preview.create",
            job.operation_id,
        )
        operation = self._users.get_operation(connection, dedup_id)
        if (
            operation is None
            or operation.status != "committed"
            or operation.operation_type != "publication_preview.create"
        ):
            raise JobExpectedStateConflictError(
                "The publication preview operation result is unavailable."
            )
        result = _queued_result_from_mapping(operation.result)
        expected = _queued_result(
            request_id=result.request_id,
            operation_id=job.operation_id,
            preview_id=preview.preview_id,
            job_id=job.id,
            created_at_ms=preview.created_at_ms,
        )
        if result != expected:
            raise JobExpectedStateConflictError(
                "The publication preview operation result is bound incorrectly."
            )
        return result

    def _append_worker_event(
        self,
        connection: ManagedConnection,
        *,
        job: DurableJobRecord,
        preview: PublicationPreviewRecord,
        outcome: str,
        event_type: str,
        now_ms: int,
    ) -> None:
        if job.actor_user_id is None or job.operation_id is None or job.space_id is None:
            raise JobExpectedStateConflictError(
                "The publication preview event binding is incomplete."
            )
        actor = self._users.require(connection, job.actor_user_id)
        request_id = _derived_uuid(_WORKER_REQUEST_NAMESPACE, job.id, outcome)
        event_uuid = _derived_uuid(
            _AUDIT_NAMESPACE,
            job.actor_user_id,
            job.operation_id,
            outcome,
            str(preview.row_version),
        )
        self._audit.append(
            connection,
            AuditEventDraft(
                event_uuid=event_uuid,
                occurred_at_ms=now_ms,
                request_id=request_id,
                operation_id=job.operation_id,
                action="publication.preview.extract",
                outcome=outcome,
                actor_kind="user",
                actor_user_id=actor.id,
                actor_display_name=actor.display_name,
                actor_role_snapshot=actor.system_role,
                space_id=job.space_id,
                details={
                    "artifact_id": preview.artifact_id,
                    "job_id": job.id,
                    "preview_id": preview.preview_id,
                    "row_version": preview.row_version,
                    "schema_version": 1,
                    "source_snapshot_sha256": preview.source_snapshot_sha256,
                    "status": preview.status,
                },
            ),
        )
        self._outbox.append(
            connection,
            OutboxEventDraft(
                logical_key=(
                    f"publication-preview:{preview.preview_id}:"
                    f"{outcome}:{preview.row_version}"
                ),
                space_id=job.space_id,
                aggregate_type="publication_preview",
                aggregate_id=preview.preview_id,
                aggregate_version=preview.row_version,
                event_type=event_type,
                payload={
                    "job_id": job.id,
                    "preview_id": preview.preview_id,
                    "row_version": preview.row_version,
                    "schema_version": 1,
                    "source_snapshot_sha256": preview.source_snapshot_sha256,
                    "status": preview.status,
                },
                available_at_ms=now_ms,
            ),
        )


class PublicationPreviewService:
    """Service-owned synchronous manual-preview mutation.

    UTF-8 encoding, deterministic preparation, and immutable CAS staging happen
    before the write transaction.  Actor state, scope epoch, target capability,
    operation idempotency, artifact/snapshot persistence, preview state, audit,
    and outbox are revalidated and committed together.
    """

    _upload_handler_version = "v1"
    _upload_rule_version = PUBLICATION_PREVIEW_RULE_VERSION

    __slots__ = (
        "_artifacts",
        "_authority",
        "_audit",
        "_conversations",
        "_factory",
        "_jobs",
        "_notion_snapshots",
        "_wiki_upload",
        "_wiki_publication",
        "_outbox",
        "_previews",
        "_scope",
        "_stager",
        "_users",
    )

    def __init__(
        self,
        factory: ConnectionFactory,
        scope_resolver: ScopeResolver,
        stager: PublicationBlobStager,
        *,
        artifacts: ArtifactRepository | None = None,
        authority: HigherPublicationAuthorityRepository | None = None,
        previews: PublicationPreviewRepository | None = None,
        users: UserRepository | None = None,
        audit: AuditRepository | None = None,
        outbox: OutboxRepository | None = None,
        jobs: JobRepository | None = None,
        conversations: ConversationRepository | None = None,
        notion_snapshots: NotionPageSnapshotProvider | None = None,
        wiki_upload=None,
        wiki_publication: WikiPublicationSelector | None = None,
    ) -> None:
        if not isinstance(factory, ConnectionFactory):
            raise TypeError("PublicationPreviewService requires a ConnectionFactory.")
        if not isinstance(scope_resolver, ScopeResolver):
            raise TypeError("PublicationPreviewService requires a ScopeResolver.")
        if not callable(getattr(stager, "stage_and_verify", None)):
            raise TypeError("PublicationPreviewService requires a blob stager.")
        self._factory = factory
        self._scope = scope_resolver
        self._stager = stager
        self._artifacts = artifacts or ArtifactRepository()
        self._authority = authority or HigherPublicationAuthorityRepository()
        self._previews = previews or PublicationPreviewRepository()
        self._users = users or UserRepository()
        self._audit = audit or AuditRepository()
        self._outbox = outbox or OutboxRepository()
        self._jobs = jobs or JobRepository()
        self._conversations = conversations or ConversationRepository()
        if notion_snapshots is not None and not callable(
            getattr(notion_snapshots, "capture", None)
        ):
            raise TypeError("notion_snapshots must provide capture().")
        self._notion_snapshots = notion_snapshots
        if wiki_upload is not None and (
            not callable(getattr(wiki_upload, "register_upload", None))
            or wiki_upload.database_path != factory.database_path
        ):
            raise TypeError("Wiki upload requires a same-database registrar.")
        self._wiki_upload = wiki_upload
        if wiki_publication is not None and (
            not isinstance(wiki_publication, WikiPublicationSelector)
            or wiki_publication.database_path != factory.database_path
        ):
            raise TypeError("Wiki publication requires a same-database selector.")
        self._wiki_publication = wiki_publication or WikiPublicationSelector(factory)

    def create_manual_preview(
        self, command: CreateManualPreviewCommand
    ) -> PublicationPreviewRecord:
        if not isinstance(command, CreateManualPreviewCommand):
            raise TypeError("A CreateManualPreviewCommand is required.")
        request_id = _preview_uuid(command.request_id, field_name="request_id")
        operation_id = _preview_uuid(
            command.operation_id, field_name="operation_id"
        )
        actor_id = _preview_user(command.actor_user_id)
        epoch = _preview_epoch(command.actor_scope_epoch)
        markdown = _manual_markdown(command.markdown)
        title = _manual_title(command.title, markdown)
        target = _publication_target(command.target)
        request_meta = _preview_request_meta(command.request_meta)
        raw = markdown.encode("utf-8")
        raw_sha256 = hashlib.sha256(raw).hexdigest()
        request_sha256 = _manual_request_sha256(
            actor_user_id=actor_id,
            actor_scope_epoch=epoch,
            operation_id=operation_id,
            raw_sha256=raw_sha256,
            title=command.title,
            target=target,
        )
        dedup_id = _derived_uuid(
            _DEDUP_NAMESPACE, actor_id, "publication_preview.create", operation_id
        )
        preview_id = publication_preview_id(actor_id, operation_id)

        replay = self._read_replay(
            actor_id=actor_id,
            epoch=epoch,
            operation_id=operation_id,
            request_sha256=request_sha256,
            dedup_id=dedup_id,
            preview_id=preview_id,
            target=target,
        )
        if replay is not None:
            return replay

        prepared_at = self._database_time()
        prepared = prepare_publication_artifact(
            raw,
            extracted_markdown=markdown,
            extraction_failure_code=None,
            extractor_name="manual-markdown",
            extractor_version="1",
            created_at_ms=prepared_at,
        )
        normalized_sha = prepared.extraction.normalized_content_sha256
        if normalized_sha is None:  # pragma: no cover - ready preparation invariant
            raise PublicationPreviewServiceError(
                "The manual source has no normalized snapshot evidence."
            )
        try:
            observation = self._stager.stage_and_verify(
                raw,
                expected_sha256=prepared.raw_sha256,
                media_type="text/markdown; charset=utf-8",
                effective_at_ms=prepared_at,
                normalized_content_sha256=normalized_sha,
                normalizer_version=NORMALIZER_VERSION,
            )
        except Exception as exc:
            raise PublicationPreviewStagingError(
                "The source bytes could not be staged safely."
            ) from exc
        frozen_blob, lease = _freeze_staged_blob_observation(
            observation,
            expected_sha256=prepared.raw_sha256,
            expected_size=len(raw),
            expected_media_type="text/markdown; charset=utf-8",
            effective_at_ms=prepared_at,
        )
        try:
            bundle = build_source_artifact_bundle(
                prepared,
                ids=publication_artifact_ids(actor_id, operation_id),
                origin_type="manual",
                origin_key=f"manual:{preview_id}",
                origin_revision_key=prepared.raw_sha256,
                raw_blob=frozen_blob,
                logical_name="manual.md",
                original_filename=None,
                publisher_user_id=actor_id,
                created_at_ms=prepared_at,
            )
            return self._commit_ready_preview(
                bundle=bundle,
                prepared=prepared,
                title=title,
                source_type="manual",
                source_details={},
                source_revalidator=None,
                request_id=request_id,
                operation_id=operation_id,
                actor_id=actor_id,
                epoch=epoch,
                request_sha256=request_sha256,
                dedup_id=dedup_id,
                preview_id=preview_id,
                target=target,
                request_meta=request_meta,
            )
        finally:
            lease.close()

    def create_chat_preview(
        self, command: CreateChatPreviewCommand
    ) -> PublicationPreviewRecord:
        if not isinstance(command, CreateChatPreviewCommand):
            raise TypeError("A CreateChatPreviewCommand is required.")
        request_id = _preview_uuid(command.request_id, field_name="request_id")
        operation_id = _preview_uuid(
            command.operation_id, field_name="operation_id"
        )
        actor_id = _preview_user(command.actor_user_id)
        epoch = _preview_epoch(command.actor_scope_epoch)
        conversation_id = _preview_uuid(
            command.conversation_id, field_name="conversation_id"
        )
        first = _chat_sequence(
            command.first_sequence_no, field_name="first_sequence_no"
        )
        last = _chat_sequence(
            command.last_sequence_no, field_name="last_sequence_no"
        )
        if last < first or last - first + 1 > _MAX_CHAT_PROMOTION_MESSAGES:
            raise PublicationPreviewValidationError(
                "The conversation message range is invalid or too large."
            )
        requested_title = (
            None
            if command.title is None
            else _source_key(command.title, field_name="title", maximum=500)
        )
        expected_snapshot = _optional_snapshot_sha256(
            command.expected_source_snapshot_sha256
        )
        target = _publication_target(command.target)
        request_meta = _preview_request_meta(command.request_meta)
        request_sha256 = _chat_request_sha256(
            actor_user_id=actor_id,
            actor_scope_epoch=epoch,
            operation_id=operation_id,
            conversation_id=conversation_id,
            first_sequence_no=first,
            last_sequence_no=last,
            title=requested_title,
            expected_source_snapshot_sha256=expected_snapshot,
            target=target,
        )
        dedup_id = _derived_uuid(
            _DEDUP_NAMESPACE, actor_id, "publication_preview.create", operation_id
        )
        preview_id = publication_preview_id(actor_id, operation_id)
        replay = self._read_replay(
            actor_id=actor_id,
            epoch=epoch,
            operation_id=operation_id,
            request_sha256=request_sha256,
            dedup_id=dedup_id,
            preview_id=preview_id,
            target=target,
        )
        if replay is not None:
            return replay
        snapshot = self._capture_chat_snapshot(
            actor_id=actor_id,
            epoch=epoch,
            conversation_id=conversation_id,
            first_sequence_no=first,
            last_sequence_no=last,
            target=target,
        )
        if (
            expected_snapshot is not None
            and snapshot.source_snapshot_sha256 != expected_snapshot
        ):
            raise PublicationPreviewIdempotencyError(
                "The conversation snapshot changed after it was selected."
            )
        raw = snapshot.markdown.encode("utf-8")
        prepared_at = self._database_time()
        prepared = prepare_publication_artifact(
            raw,
            extracted_markdown=snapshot.markdown,
            extraction_failure_code=None,
            extractor_name="canonical-conversation",
            extractor_version="1",
            created_at_ms=prepared_at,
        )
        normalized_sha = prepared.extraction.normalized_content_sha256
        if normalized_sha is None:  # pragma: no cover
            raise PublicationPreviewServiceError(
                "The chat source has no normalized snapshot evidence."
            )
        try:
            observation = self._stager.stage_and_verify(
                raw,
                expected_sha256=prepared.raw_sha256,
                media_type="text/markdown; charset=utf-8",
                effective_at_ms=prepared_at,
                normalized_content_sha256=normalized_sha,
                normalizer_version=NORMALIZER_VERSION,
            )
        except Exception as exc:
            raise PublicationPreviewStagingError(
                "The chat snapshot could not be staged safely."
            ) from exc
        frozen_blob, lease = _freeze_staged_blob_observation(
            observation,
            expected_sha256=prepared.raw_sha256,
            expected_size=len(raw),
            expected_media_type="text/markdown; charset=utf-8",
            effective_at_ms=prepared_at,
        )
        try:
            bundle = build_source_artifact_bundle(
                prepared,
                ids=publication_artifact_ids(actor_id, operation_id),
                origin_type="chat",
                origin_key=f"chat:{conversation_id}:{first}-{last}",
                origin_revision_key=snapshot.source_snapshot_sha256,
                raw_blob=frozen_blob,
                logical_name="conversation.md",
                original_filename=None,
                publisher_user_id=actor_id,
                created_at_ms=prepared_at,
            )
            return self._commit_ready_preview(
                bundle=bundle,
                prepared=prepared,
                title=requested_title or snapshot.title,
                source_type="chat",
                source_details={
                    "conversation_id": conversation_id,
                    "conversation_space_id": snapshot.conversation_space_id,
                    "first_sequence_no": first,
                    "last_sequence_no": last,
                    "origin_source_snapshot_sha256": (
                        snapshot.source_snapshot_sha256
                    ),
                },
                source_revalidator=lambda connection: self._revalidate_chat_snapshot(
                    connection, actor_id=actor_id, expected=snapshot
                ),
                request_id=request_id,
                operation_id=operation_id,
                actor_id=actor_id,
                epoch=epoch,
                request_sha256=request_sha256,
                dedup_id=dedup_id,
                preview_id=preview_id,
                target=target,
                request_meta=request_meta,
            )
        finally:
            lease.close()

    def create_notion_preview(
        self, command: CreateNotionPreviewCommand
    ) -> PublicationPreviewRecord:
        if not isinstance(command, CreateNotionPreviewCommand):
            raise TypeError("A CreateNotionPreviewCommand is required.")
        request_id = _preview_uuid(command.request_id, field_name="request_id")
        operation_id = _preview_uuid(
            command.operation_id, field_name="operation_id"
        )
        actor_id = _preview_user(command.actor_user_id)
        epoch = _preview_epoch(command.actor_scope_epoch)
        page_id = _source_key(command.page_id, field_name="page_id")
        expected_snapshot = _optional_snapshot_sha256(
            command.expected_source_snapshot_sha256
        )
        target = _publication_target(command.target)
        request_meta = _preview_request_meta(command.request_meta)
        request_sha256 = _notion_request_sha256(
            actor_user_id=actor_id,
            actor_scope_epoch=epoch,
            operation_id=operation_id,
            page_id=page_id,
            expected_source_snapshot_sha256=expected_snapshot,
            target=target,
        )
        dedup_id = _derived_uuid(
            _DEDUP_NAMESPACE, actor_id, "publication_preview.create", operation_id
        )
        preview_id = publication_preview_id(actor_id, operation_id)
        replay = self._read_replay(
            actor_id=actor_id,
            epoch=epoch,
            operation_id=operation_id,
            request_sha256=request_sha256,
            dedup_id=dedup_id,
            preview_id=preview_id,
            target=target,
        )
        if replay is not None:
            return replay
        if self._notion_snapshots is None:
            raise PublicationPreviewServiceError(
                "A Notion snapshot provider is not configured."
            )
        try:
            captured = self._notion_snapshots.capture(
                actor_user_id=actor_id, page_id=page_id
            )
        except PermissionError as exc:
            raise PublicationPreviewAuthorizationError(
                "The Notion page is not accessible to this actor."
            ) from exc
        except Exception as exc:
            raise PublicationPreviewStagingError(
                "The Notion page snapshot could not be captured safely."
            ) from exc
        snapshot = _validate_notion_snapshot(
            captured, actor_user_id=actor_id, requested_page_id=page_id
        )
        if (
            expected_snapshot is not None
            and snapshot.source_snapshot_sha256 != expected_snapshot
        ):
            raise PublicationPreviewIdempotencyError(
                "The Notion source changed after it was selected."
            )
        raw = snapshot.markdown.encode("utf-8")
        prepared_at = self._database_time()
        prepared = prepare_publication_artifact(
            raw,
            extracted_markdown=snapshot.markdown,
            extraction_failure_code=None,
            extractor_name="notion-markdown-snapshot",
            extractor_version="1",
            created_at_ms=prepared_at,
        )
        normalized_sha = prepared.extraction.normalized_content_sha256
        if normalized_sha is None:  # pragma: no cover
            raise PublicationPreviewServiceError(
                "The Notion source has no normalized snapshot evidence."
            )
        try:
            observation = self._stager.stage_and_verify(
                raw,
                expected_sha256=prepared.raw_sha256,
                media_type="text/markdown; charset=utf-8",
                effective_at_ms=prepared_at,
                normalized_content_sha256=normalized_sha,
                normalizer_version=NORMALIZER_VERSION,
            )
        except Exception as exc:
            raise PublicationPreviewStagingError(
                "The Notion snapshot could not be staged safely."
            ) from exc
        frozen_blob, lease = _freeze_staged_blob_observation(
            observation,
            expected_sha256=prepared.raw_sha256,
            expected_size=len(raw),
            expected_media_type="text/markdown; charset=utf-8",
            effective_at_ms=prepared_at,
        )
        try:
            bundle = build_source_artifact_bundle(
                prepared,
                ids=publication_artifact_ids(actor_id, operation_id),
                origin_type="notion",
                origin_key=f"notion:{snapshot.owner_user_id}:{snapshot.page_id}",
                origin_revision_key=snapshot.source_snapshot_sha256,
                raw_blob=frozen_blob,
                logical_name="notion-page.md",
                original_filename=None,
                publisher_user_id=actor_id,
                created_at_ms=prepared_at,
                notion_snapshot=snapshot,
            )
            return self._commit_ready_preview(
                bundle=bundle,
                prepared=prepared,
                title=snapshot.title,
                source_type="notion",
                source_details={
                    "asset_manifest_sha256": snapshot.asset_manifest_sha256,
                    "notion_owner_user_id": snapshot.owner_user_id,
                    "notion_page_id": snapshot.page_id,
                    "notion_revision": snapshot.revision,
                    "notion_updated_at": snapshot.updated_at,
                    "origin_source_snapshot_sha256": (
                        snapshot.source_snapshot_sha256
                    ),
                },
                source_revalidator=None,
                request_id=request_id,
                operation_id=operation_id,
                actor_id=actor_id,
                epoch=epoch,
                request_sha256=request_sha256,
                dedup_id=dedup_id,
                preview_id=preview_id,
                target=target,
                request_meta=request_meta,
            )
        finally:
            lease.close()

    def create_upload_preview(
        self, command: CreateUploadPreviewCommand
    ) -> QueuedPublicationPreviewResult:
        if not isinstance(command, CreateUploadPreviewCommand):
            raise TypeError("A CreateUploadPreviewCommand is required.")
        request_id = _preview_uuid(command.request_id, field_name="request_id")
        operation_id = _preview_uuid(
            command.operation_id, field_name="operation_id"
        )
        actor_id = _preview_user(command.actor_user_id)
        epoch = _preview_epoch(command.actor_scope_epoch)
        content = _upload_bytes(command.content)
        filename = _upload_text(
            command.original_filename,
            field_name="original_filename",
            maximum=500,
        )
        media_type = _upload_text(
            command.media_type, field_name="media_type", maximum=255
        )
        title = _upload_title(command.title, filename)
        target = _publication_target(command.target)
        request_meta = _preview_request_meta(command.request_meta)
        raw_sha256 = hashlib.sha256(content).hexdigest()
        request_sha256 = self._upload_request_sha256(
            actor_user_id=actor_id,
            actor_scope_epoch=epoch,
            operation_id=operation_id,
            raw_sha256=raw_sha256,
            raw_size_bytes=len(content),
            original_filename=filename,
            media_type=media_type,
            title=command.title,
            target=target,
        )
        dedup_id = _derived_uuid(
            _DEDUP_NAMESPACE, actor_id, "publication_preview.create", operation_id
        )
        preview_id = publication_preview_id(actor_id, operation_id)
        replay = self._read_queued_replay(
            actor_id=actor_id,
            epoch=epoch,
            operation_id=operation_id,
            request_sha256=request_sha256,
            dedup_id=dedup_id,
            preview_id=preview_id,
            target=target,
        )
        if replay is not None:
            return replay

        prepared_at = self._database_time()
        try:
            observation = self._stager.stage_and_verify(
                content,
                expected_sha256=raw_sha256,
                media_type=media_type,
                effective_at_ms=prepared_at,
                normalized_content_sha256=None,
                normalizer_version=None,
            )
        except Exception as exc:
            raise PublicationPreviewStagingError(
                "The upload bytes could not be staged safely."
            ) from exc
        frozen_blob, lease = _freeze_staged_blob_observation(
            observation,
            expected_sha256=raw_sha256,
            expected_size=len(content),
            expected_media_type=media_type,
            effective_at_ms=prepared_at,
        )
        try:
            bundle = build_pending_source_artifact_bundle(
                ids=publication_artifact_ids(actor_id, operation_id),
                origin_type="upload",
                origin_key=f"upload:{preview_id}",
                origin_revision_key=raw_sha256,
                raw_blob=frozen_blob,
                logical_name=filename,
                original_filename=filename,
                publisher_user_id=actor_id,
                created_at_ms=prepared_at,
            )
            return self._commit_upload_preview(
                bundle=bundle,
                title=title,
                request_id=request_id,
                operation_id=operation_id,
                actor_id=actor_id,
                epoch=epoch,
                request_sha256=request_sha256,
                dedup_id=dedup_id,
                preview_id=preview_id,
                target=target,
                request_meta=request_meta,
            )
        finally:
            lease.close()

    def _upload_request_sha256(self, **fields) -> str:
        return _upload_request_sha256(**fields)

    def _upload_expected_state(self, state: dict[str, object]) -> dict[str, object]:
        return state

    def get_preview(self, query: GetPublicationPreviewQuery) -> dict[str, object]:
        """Return one actor-authorized durable preview status document.

        Ready-result evidence remains byte-frozen in the repository.  Polling
        overlays only the current lifecycle status/timestamps, while queued,
        failed, and pre-ready cancelled responses are reconstructed from the
        exact committed create-operation result.
        """

        actor_id, epoch, preview_id = self._status_query_fields(query)
        connection = self._factory.open(read_only=True)
        try:
            return self._read_preview_status(connection, actor_id, epoch, preview_id)
        finally:
            connection.close()

    @staticmethod
    def _status_query_fields(query):
        if not isinstance(query, GetPublicationPreviewQuery):
            raise TypeError("A GetPublicationPreviewQuery is required.")
        return (_preview_user(query.actor_user_id), _preview_epoch(query.actor_scope_epoch),
            _preview_uuid(query.preview_id, field_name="preview_id"))

    def _read_preview_status(self, connection, actor_id, epoch, preview_id):
        """Non-owning internal read; always opens a fresh authority snapshot."""
        with transaction(connection, TransactionMode.DEFERRED):
            actor = self._users.require(connection, actor_id)
            if actor.account_status != "active" or actor.scope_epoch != epoch:
                raise PublicationPreviewAuthorizationError(
                    "Current canonical actor state does not authorize preview status."
                )
            record = self._previews.require_for_actor(
                connection, preview_id=preview_id, actor_user_id=actor_id,
            )
            document = self._preview_status_document(connection, record)
            selection = None if self._wiki_publication is None else self._wiki_publication.select(connection, record)
            if selection is not None:
                document.update(selection.overlay(record))
            return document

    def cancel_preview(
        self, command: CancelPublicationPreviewCommand
    ) -> PublicationPreviewRecord:
        if not isinstance(command, CancelPublicationPreviewCommand):
            raise TypeError("A CancelPublicationPreviewCommand is required.")
        request_id = _preview_uuid(command.request_id, field_name="request_id")
        operation_id = _preview_uuid(
            command.operation_id, field_name="operation_id"
        )
        preview_id = _preview_uuid(command.preview_id, field_name="preview_id")
        actor_id = _preview_user(command.actor_user_id)
        epoch = _preview_epoch(command.actor_scope_epoch)
        request_meta = _preview_request_meta(command.request_meta)
        request_sha256 = hashlib.sha256(
            _canonical_json(
                {
                    "actor_scope_epoch": epoch,
                    "actor_user_id": actor_id,
                    "operation_id": operation_id,
                    "operation_type": "publication_preview.cancel",
                    "preview_id": preview_id,
                    "schema_version": 1,
                }
            ).encode("utf-8")
        ).hexdigest()
        dedup_id = _derived_uuid(
            _CANCEL_DEDUP_NAMESPACE, actor_id, operation_id
        )
        connection = self._factory.open()
        result: PublicationPreviewRecord | None = None
        try:
            with transaction(connection):
                actor = self._users.require(connection, actor_id)
                if actor.account_status != "active" or actor.scope_epoch != epoch:
                    raise PublicationPreviewAuthorizationError(
                        "Current canonical actor state does not authorize cancellation."
                    )
                current = self._previews.require_for_actor(
                    connection, preview_id=preview_id, actor_user_id=actor_id
                )
                now_ms = db_now_ms(connection)
                try:
                    begun = self._users.begin_operation(
                        connection,
                        actor_user_id=actor_id,
                        operation_type="publication_preview.cancel",
                        operation_id=operation_id,
                        request_sha256=request_sha256,
                        dedup_id=dedup_id,
                        now_ms=now_ms,
                    )
                except OperationIdempotencyConflictError as exc:
                    raise PublicationPreviewIdempotencyError(str(exc)) from exc
                if not begun.inserted:
                    if begun.operation.status != "committed":
                        raise PublicationPreviewIdempotencyError(
                            "The preview cancellation is already in progress."
                        )
                    result = self._previews.require_for_actor(
                        connection, preview_id=preview_id, actor_user_id=actor_id
                    )
                    stored = begun.operation.result
                    if (
                        result.status != "cancelled"
                        or not isinstance(stored, Mapping)
                        or stored.get("preview_id") != preview_id
                        or stored.get("operation_id") != operation_id
                        or stored.get("status") != "cancelled"
                    ):
                        raise PublicationPreviewIdempotencyError(
                            "The stored preview cancellation is invalid."
                        )
                    return result
                space_id = None
                if current.durable_job_id is not None:
                    cancelled = self._jobs.request_cancel(
                        connection,
                        job_id=current.durable_job_id,
                        now_ms=now_ms,
                    )
                    space_id = cancelled.job.space_id
                result = self._previews.cancel(
                    connection,
                    preview_id=preview_id,
                    completed_at_ms=now_ms,
                )
                response = {
                    "completed_at": _utc_iso(result.completed_at_ms or now_ms),
                    "job_id": result.durable_job_id,
                    "operation_id": operation_id,
                    "preview_id": preview_id,
                    "request_id": request_id,
                    "schema_version": 1,
                    "status": "cancelled",
                    "updated_at": _utc_iso(result.updated_at_ms),
                }
                self._audit.append(
                    connection,
                    AuditEventDraft(
                        event_uuid=_derived_uuid(
                            _AUDIT_NAMESPACE,
                            actor_id,
                            operation_id,
                            "cancelled",
                        ),
                        occurred_at_ms=now_ms,
                        request_id=request_id,
                        operation_id=operation_id,
                        action="publication.preview.cancel",
                        outcome="cancelled",
                        actor_kind="user",
                        actor_user_id=actor.id,
                        actor_display_name=actor.display_name,
                        actor_role_snapshot=actor.system_role,
                        space_id=space_id,
                        details={
                            "job_id": result.durable_job_id,
                            "preview_id": result.preview_id,
                            "row_version": result.row_version,
                            "schema_version": 1,
                        },
                        request_meta=request_meta,
                    ),
                )
                self._outbox.append(
                    connection,
                    OutboxEventDraft(
                        logical_key=(
                            f"publication-preview:{preview_id}:"
                            f"cancelled:{result.row_version}"
                        ),
                        space_id=space_id,
                        aggregate_type="publication_preview",
                        aggregate_id=preview_id,
                        aggregate_version=result.row_version,
                        event_type="publication.preview.cancelled",
                        payload={
                            "job_id": result.durable_job_id,
                            "preview_id": preview_id,
                            "row_version": result.row_version,
                            "schema_version": 1,
                            "status": "cancelled",
                        },
                        available_at_ms=now_ms,
                    ),
                )
                self._users.commit_operation(
                    connection,
                    dedup_id=dedup_id,
                    result=response,
                    http_status=200,
                    completed_at_ms=now_ms,
                )
        except (OperationIdempotencyConflictError, OperationInProgressError) as exc:
            raise PublicationPreviewIdempotencyError(str(exc)) from exc
        finally:
            connection.close()
        if result is None:
            raise PublicationPreviewServiceError(
                "The cancelled preview result is unavailable."
            )
        return result

    def expire_preview(self, preview_id: str) -> PublicationPreviewRecord:
        """Expire one ready preview using authoritative SQLite time.

        The state transition, system audit event, and outbox notification share
        one service-owned transaction.  An already-expired row is an exact
        no-op because the first transition committed all three effects.
        """

        preview_id = _preview_uuid(preview_id, field_name="preview_id")
        connection = self._factory.open()
        result: PublicationPreviewRecord | None = None
        try:
            with transaction(connection):
                current = self._previews.get(connection, preview_id)
                if current is None:
                    raise PublicationPreviewValidationError(
                        "The publication preview was not found."
                    )
                if current.status == "expired":
                    return current
                now_ms = db_now_ms(connection)
                result = self._previews.expire_ready(
                    connection, preview_id=preview_id, now_ms=now_ms
                )
                request_id = _derived_uuid(
                    _MAINTENANCE_NAMESPACE, preview_id, "expire", "request"
                )
                operation_id = _derived_uuid(
                    _MAINTENANCE_NAMESPACE, preview_id, "expire", "operation"
                )
                space_id = self._preview_space_id(connection, result)
                self._audit.append(
                    connection,
                    AuditEventDraft(
                        event_uuid=_derived_uuid(
                            _MAINTENANCE_NAMESPACE, preview_id, "expire", "audit"
                        ),
                        occurred_at_ms=now_ms,
                        request_id=request_id,
                        operation_id=operation_id,
                        action="publication.preview.expire",
                        outcome="expired",
                        actor_kind="system",
                        actor_user_id=None,
                        actor_display_name="Publication preview maintenance",
                        actor_role_snapshot="system",
                        space_id=space_id,
                        details={
                            "artifact_id": result.artifact_id,
                            "preview_id": preview_id,
                            "row_version": result.row_version,
                            "schema_version": 1,
                        },
                    ),
                )
                self._outbox.append(
                    connection,
                    OutboxEventDraft(
                        logical_key=(
                            f"publication-preview:{preview_id}:"
                            f"expired:{result.row_version}"
                        ),
                        space_id=space_id,
                        aggregate_type="publication_preview",
                        aggregate_id=preview_id,
                        aggregate_version=result.row_version,
                        event_type="publication.preview.expired",
                        payload={
                            "artifact_id": result.artifact_id,
                            "preview_id": preview_id,
                            "row_version": result.row_version,
                            "schema_version": 1,
                            "status": "expired",
                        },
                        available_at_ms=now_ms,
                    ),
                )
        finally:
            connection.close()
        if result is None:
            raise PublicationPreviewServiceError(
                "The expired preview result is unavailable."
            )
        return result

    def cleanup_orphan_preview_bodies(
        self, artifact_id: str
    ) -> OrphanBodyCleanupResult:
        """Purge eligible body links while retaining immutable source evidence."""

        artifact_id = _preview_uuid(artifact_id, field_name="artifact_id")
        connection = self._factory.open()
        result: OrphanBodyCleanupResult | None = None
        try:
            with transaction(connection):
                owner = connection.execute(
                    "SELECT publisher_user_id FROM source_artifact WHERE id=?",
                    (artifact_id,),
                ).fetchone()
                if owner is None:
                    raise PublicationPreviewValidationError(
                        "The source artifact was not found."
                    )
                now_ms = db_now_ms(connection)
                result = self._artifacts.cleanup_orphan_bodies(
                    connection, artifact_id=artifact_id, purged_at_ms=now_ms
                )
                if not result.changed:
                    return result
                space = connection.execute(
                    """SELECT id FROM knowledge_space
                         WHERE space_kind='personal' AND owner_user_id=?""",
                    (owner[0],),
                ).fetchone()
                space_id = None if space is None else str(space[0])
                request_id = _derived_uuid(
                    _MAINTENANCE_NAMESPACE, artifact_id, "cleanup", "request"
                )
                operation_id = _derived_uuid(
                    _MAINTENANCE_NAMESPACE, artifact_id, "cleanup", "operation"
                )
                self._audit.append(
                    connection,
                    AuditEventDraft(
                        event_uuid=_derived_uuid(
                            _MAINTENANCE_NAMESPACE, artifact_id, "cleanup", "audit"
                        ),
                        occurred_at_ms=now_ms,
                        request_id=request_id,
                        operation_id=operation_id,
                        action="publication.preview.body_cleanup",
                        outcome="purged",
                        actor_kind="system",
                        actor_user_id=None,
                        actor_display_name="Publication preview maintenance",
                        actor_role_snapshot="system",
                        space_id=space_id,
                        details={
                            "artifact_id": artifact_id,
                            "deleted_artifact_body_count": (
                                result.deleted_artifact_body_count
                            ),
                            "deleted_extraction_body_count": (
                                result.deleted_extraction_body_count
                            ),
                            "schema_version": 1,
                        },
                    ),
                )
                self._outbox.append(
                    connection,
                    OutboxEventDraft(
                        logical_key=f"source-artifact:{artifact_id}:body-purged:1",
                        space_id=space_id,
                        aggregate_type="source_artifact",
                        aggregate_id=artifact_id,
                        aggregate_version=1,
                        event_type="source_artifact.body_purged",
                        payload={
                            "artifact_id": artifact_id,
                            "deleted_artifact_body_count": (
                                result.deleted_artifact_body_count
                            ),
                            "deleted_extraction_body_count": (
                                result.deleted_extraction_body_count
                            ),
                            "reason": "orphan_ingestion_cleanup",
                            "schema_version": 1,
                        },
                        available_at_ms=now_ms,
                    ),
                )
        finally:
            connection.close()
        if result is None:
            raise PublicationPreviewServiceError(
                "The source cleanup result is unavailable."
            )
        return result

    @staticmethod
    def _preview_space_id(
        connection: ManagedConnection, preview: PublicationPreviewRecord
    ) -> str | None:
        row = connection.execute(
            "SELECT target_space_id FROM higher_publication_preview_authority "
            "WHERE preview_id=?",
            (preview.preview_id,),
        ).fetchone()
        if row is not None:
            return str(row[0])
        if preview.durable_job_id is not None:
            row = connection.execute(
                "SELECT space_id FROM durable_job WHERE id=?",
                (preview.durable_job_id,),
            ).fetchone()
            if row is not None:
                return str(row[0])
        row = connection.execute(
            """SELECT id FROM knowledge_space
                 WHERE space_kind='personal' AND owner_user_id=?""",
            (preview.actor_user_id,),
        ).fetchone()
        return None if row is None else str(row[0])

    def _database_time(self) -> int:
        connection = self._factory.open(read_only=True)
        try:
            with transaction(connection, TransactionMode.DEFERRED):
                return db_now_ms(connection)
        finally:
            connection.close()

    def _authorized_targets(
        self,
        connection,
        *,
        actor_id: str,
        epoch: int,
        target: HigherPublicationTargetDescriptor | None = None,
    ):
        if target is not None:
            try:
                resolution = self._scope.resolve_in_transaction(
                    connection,
                    SessionPrincipal(user_id=actor_id, captured_scope_epoch=epoch),
                    target.space_id,
                    Capability.PUBLISH,
                )
            except ScopeResolutionError as exc:
                raise PublicationPreviewAuthorizationError(
                    "Current canonical scope does not authorize the explicit "
                    "publication target."
                ) from exc
            plan = resolution.access_plan
            if (
                resolution.space.kind != target.kind
                or target.kind not in {"team", "responsibility"}
                or plan is None
                or plan.native_mutation_space_id != target.space_id
                or plan.role_assignment_id != target.role_assignment_id
                or plan.role_assignment_generation
                != target.role_assignment_generation
                or Capability.PUBLISH not in plan.native_capabilities
            ):
                raise PublicationPreviewAuthorizationError(
                    "The explicit publication role episode is stale or mismatched."
                )
            actor = self._users.require(connection, actor_id)
            if actor.scope_epoch != epoch or actor.account_status != "active":
                raise PublicationPreviewAuthorizationError(
                    "Current canonical actor state does not match the request."
                )
            return actor, resolution.space, ("explicit",), resolution
        try:
            selectable = self._scope.list_selectable_in_transaction(
                connection,
                SessionPrincipal(user_id=actor_id, captured_scope_epoch=epoch),
            )
        except ScopeResolutionError as exc:
            raise PublicationPreviewAuthorizationError(
                "Current canonical scope does not authorize publication preview."
            ) from exc
        personal = next(
            (item for item in selectable.items if item.kind == "personal"), None
        )
        if personal is None or Capability.PUBLISH not in personal.capabilities:
            raise PublicationPreviewAuthorizationError(
                "Personal publication is not currently enabled for this actor."
            )
        try:
            personal_resolution = self._scope.resolve_in_transaction(
                connection,
                SessionPrincipal(user_id=actor_id, captured_scope_epoch=epoch),
                personal.id,
                Capability.PUBLISH,
            )
        except ScopeResolutionError as exc:
            raise PublicationPreviewAuthorizationError(
                "Personal publication authorization changed during preview."
            ) from exc
        part = next(
            (
                item
                for item in selectable.items
                if item.kind == "part" and Capability.PUBLISH in item.capabilities
            ),
            None,
        )
        modes = ("personal", "part", "both") if part is not None else ("personal",)
        actor = self._users.require(connection, actor_id)
        if actor.scope_epoch != epoch or actor.account_status != "active":
            raise PublicationPreviewAuthorizationError(
                "Current canonical actor state does not match the request."
            )
        return actor, personal, modes, personal_resolution

    def _put_preview_authority(
        self,
        connection: ManagedConnection,
        *,
        preview_id: str,
        actor,
        resolution,
        target: HigherPublicationTargetDescriptor | None,
        created_at_ms: int,
    ) -> HigherPublicationPreviewAuthorityRecord | None:
        if target is None:
            return None
        plan = resolution.access_plan
        if plan is None:
            raise PublicationPreviewAuthorizationError(
                "The explicit publication access plan is unavailable."
            )
        try:
            return self._authority.put_preview(
                connection,
                HigherPublicationPreviewAuthorityDraft(
                    preview_id=preview_id,
                    actor_user_id=actor.id,
                    actor_scope_epoch=actor.scope_epoch,
                    actor_row_version=plan.actor_row_version,
                    descriptor=target,
                    corpus_closure_generation=plan.corpus_closure.generation,
                    corpus_closure_sha256=plan.corpus_closure.sha256,
                    organization_snapshot_version=(
                        plan.corpus_closure.organization_snapshot_version
                    ),
                    created_at_ms=created_at_ms,
                ),
            )
        except HigherPublicationAuthorityConflictError as exc:
            raise PublicationPreviewAuthorizationError(str(exc)) from exc
        except HigherPublicationAuthorityIntegrityError as exc:
            raise PublicationPreviewServiceError(str(exc)) from exc

    def _capture_chat_snapshot(
        self,
        *,
        actor_id: str,
        epoch: int,
        conversation_id: str,
        first_sequence_no: int,
        last_sequence_no: int,
        target: HigherPublicationTargetDescriptor | None,
    ) -> _ChatSourceSnapshot:
        connection = self._factory.open(read_only=True)
        try:
            with transaction(connection, TransactionMode.DEFERRED):
                self._authorized_targets(
                    connection, actor_id=actor_id, epoch=epoch, target=target
                )
                return self._chat_snapshot_in_transaction(
                    connection,
                    actor_id=actor_id,
                    conversation_id=conversation_id,
                    first_sequence_no=first_sequence_no,
                    last_sequence_no=last_sequence_no,
                )
        finally:
            connection.close()

    def _chat_snapshot_in_transaction(
        self,
        connection: ManagedConnection,
        *,
        actor_id: str,
        conversation_id: str,
        first_sequence_no: int,
        last_sequence_no: int,
    ) -> _ChatSourceSnapshot:
        try:
            conversation = self._conversations.require_accessible(
                connection,
                conversation_id=conversation_id,
                owner_user_id=actor_id,
            )
        except ConversationRepositoryError as exc:
            raise PublicationPreviewAuthorizationError(
                "The conversation is not accessible to this actor."
            ) from exc
        if conversation.status != "active":
            raise PublicationPreviewAuthorizationError(
                "Only an active accessible conversation can be promoted."
            )
        count = last_sequence_no - first_sequence_no + 1
        try:
            messages = self._conversations.list_messages(
                connection,
                conversation_id=conversation_id,
                owner_user_id=actor_id,
                after_sequence_no=first_sequence_no - 1,
                limit=count,
            )
        except ConversationRepositoryError as exc:
            raise PublicationPreviewAuthorizationError(
                "The conversation is not accessible to this actor."
            ) from exc
        expected_sequences = tuple(range(first_sequence_no, last_sequence_no + 1))
        if tuple(message.sequence_no for message in messages) != expected_sequences:
            raise PublicationPreviewValidationError(
                "The requested conversation message range is incomplete."
            )
        title = conversation.title.strip() or "Conversation"
        lines = [f"# {title}"]
        evidence: list[dict[str, object]] = []
        labels = {
            "assistant": "Assistant",
            "system": "System",
            "tool": "Tool",
            "user": "User",
        }
        for message in messages:
            content = message.content
            raw_content = content.get("content")
            lines.extend(("", f"## {labels[message.role]}", ""))
            if isinstance(raw_content, str):
                lines.append(raw_content)
            else:
                lines.extend(("```json", _canonical_json(content), "```"))
            evidence.append(
                {
                    "content_sha256": hashlib.sha256(
                        message.content_json.encode("utf-8")
                    ).hexdigest(),
                    "message_id": message.id,
                    "role": message.role,
                    "sequence_no": message.sequence_no,
                }
            )
        markdown = "\n".join(lines).rstrip() + "\n"
        snapshot_payload = {
            "conversation_id": conversation.id,
            "conversation_row_version": conversation.row_version,
            "conversation_space_id": conversation.space_id,
            "first_sequence_no": first_sequence_no,
            "last_sequence_no": last_sequence_no,
            "messages": evidence,
            "schema_version": 1,
        }
        snapshot_sha = hashlib.sha256(
            _canonical_json(snapshot_payload).encode("utf-8")
        ).hexdigest()
        return _ChatSourceSnapshot(
            conversation.id,
            conversation.space_id,
            conversation.row_version,
            first_sequence_no,
            last_sequence_no,
            title,
            markdown,
            snapshot_sha,
        )

    def _revalidate_chat_snapshot(
        self,
        connection: ManagedConnection,
        *,
        actor_id: str,
        expected: _ChatSourceSnapshot,
    ) -> None:
        observed = self._chat_snapshot_in_transaction(
            connection,
            actor_id=actor_id,
            conversation_id=expected.conversation_id,
            first_sequence_no=expected.first_sequence_no,
            last_sequence_no=expected.last_sequence_no,
        )
        if observed != expected:
            raise PublicationPreviewAuthorizationError(
                "The conversation snapshot changed before preview commit."
            )

    def _preview_status_document(
        self,
        connection: ManagedConnection,
        record: PublicationPreviewRecord,
    ) -> dict[str, object]:
        authority = self._authority.get_preview(connection, record.preview_id)

        def with_explicit_target(result: dict[str, object]) -> dict[str, object]:
            if authority is not None:
                result["available_targets"] = [authority.descriptor.to_mapping()]
            return result

        ready = record.ready_result
        if ready is not None:
            result = dict(ready)
            result["status"] = record.status
            result["updated_at"] = _utc_iso(record.updated_at_ms)
            if record.completed_at_ms is None:
                raise PublicationPreviewIdempotencyError(
                    "The ready preview has no completion time."
                )
            result["completed_at"] = _utc_iso(record.completed_at_ms)
            return with_explicit_target(result)

        job_id = record.durable_job_id
        if job_id is None:
            raise PublicationPreviewIdempotencyError(
                "The unready preview has no durable job binding."
            )
        job = self._jobs.require(connection, job_id)
        if (
            job.actor_user_id != record.actor_user_id
            or job.operation_id is None
            or job.id != job_id
        ):
            raise PublicationPreviewIdempotencyError(
                "The preview job binding is invalid."
            )
        dedup_id = _derived_uuid(
            _DEDUP_NAMESPACE,
            record.actor_user_id,
            "publication_preview.create",
            job.operation_id,
        )
        operation = self._users.get_operation(connection, dedup_id)
        if (
            operation is None
            or operation.status != "committed"
            or operation.operation_type != "publication_preview.create"
        ):
            raise PublicationPreviewIdempotencyError(
                "The preview create-operation result is unavailable."
            )
        queued = _queued_result_from_mapping(operation.result)
        expected = _queued_result(
            request_id=queued.request_id,
            operation_id=job.operation_id,
            preview_id=record.preview_id,
            job_id=job.id,
            created_at_ms=record.created_at_ms,
        )
        if queued != expected:
            raise PublicationPreviewIdempotencyError(
                "The preview create-operation result is bound incorrectly."
            )
        if record.status in {"queued", "extracting"}:
            result = queued.to_mapping()
            result["status"] = record.status
            result["updated_at"] = _utc_iso(record.updated_at_ms)
            return with_explicit_target(result)
        if record.completed_at_ms is None:
            raise PublicationPreviewIdempotencyError(
                "The terminal preview has no completion time."
            )
        common: dict[str, object] = {
            "completed_at": _utc_iso(record.completed_at_ms),
            "created_at": _utc_iso(record.created_at_ms),
            "expires_at": _utc_iso(record.expires_at_ms),
            "job_id": job.id,
            "operation_id": job.operation_id,
            "preview_id": record.preview_id,
            "request_id": queued.request_id,
            "status": record.status,
            "updated_at": _utc_iso(record.updated_at_ms),
        }
        if record.status == "failed":
            error = record.error
            if error is None:
                raise PublicationPreviewIdempotencyError(
                    "The failed preview has no error evidence."
                )
            common["error"] = error
            return with_explicit_target(common)
        if record.status == "cancelled":
            return with_explicit_target(common)
        raise PublicationPreviewIdempotencyError(
            "The unready preview lifecycle state is invalid."
        )

    def _read_replay(
        self,
        *,
        actor_id: str,
        epoch: int,
        operation_id: str,
        request_sha256: str,
        dedup_id: str,
        preview_id: str,
        target: HigherPublicationTargetDescriptor | None,
    ) -> PublicationPreviewRecord | None:
        connection = self._factory.open(read_only=True)
        try:
            with transaction(connection, TransactionMode.DEFERRED):
                self._authorized_targets(
                    connection, actor_id=actor_id, epoch=epoch, target=target
                )
                operation = self._users.get_operation(connection, dedup_id)
                if operation is None:
                    return None
                if (
                    operation.actor_user_id != actor_id
                    or operation.operation_type != "publication_preview.create"
                    or operation.operation_id != operation_id
                    or operation.request_sha256 != request_sha256
                ):
                    raise PublicationPreviewIdempotencyError(
                        "The operation ID is already bound to different input."
                    )
                if operation.status != "committed":
                    raise PublicationPreviewIdempotencyError(
                        "The publication preview operation is not replayable."
                    )
                record = self._previews.require_for_actor(
                    connection, preview_id=preview_id, actor_user_id=actor_id
                )
                if operation.result != record.ready_result:
                    raise PublicationPreviewIdempotencyError(
                        "The stored preview result differs from its operation result."
                    )
                authority = self._authority.get_preview(connection, preview_id)
                if (target is None) != (authority is None) or (
                    target is not None
                    and authority is not None
                    and authority.descriptor != target
                ):
                    raise PublicationPreviewIdempotencyError(
                        "The preview authority differs from the replay request."
                    )
                return record
        finally:
            connection.close()

    def _read_queued_replay(
        self,
        *,
        actor_id: str,
        epoch: int,
        operation_id: str,
        request_sha256: str,
        dedup_id: str,
        preview_id: str,
        target: HigherPublicationTargetDescriptor | None,
    ) -> QueuedPublicationPreviewResult | None:
        connection = self._factory.open(read_only=True)
        try:
            with transaction(connection, TransactionMode.DEFERRED):
                self._authorized_targets(
                    connection, actor_id=actor_id, epoch=epoch, target=target
                )
                operation = self._users.get_operation(connection, dedup_id)
                if operation is None:
                    return None
                if (
                    operation.actor_user_id != actor_id
                    or operation.operation_type != "publication_preview.create"
                    or operation.operation_id != operation_id
                    or operation.request_sha256 != request_sha256
                    or operation.status != "committed"
                ):
                    raise PublicationPreviewIdempotencyError(
                        "The operation ID is not an exact queued-preview replay."
                    )
                result = _queued_result_from_mapping(operation.result)
                record = self._previews.require_for_actor(
                    connection, preview_id=preview_id, actor_user_id=actor_id
                )
                expected = _queued_result(
                    request_id=result.request_id,
                    operation_id=operation_id,
                    preview_id=preview_id,
                    job_id=result.job_id,
                    created_at_ms=record.created_at_ms,
                )
                if (
                    result != expected
                    or record.durable_job_id != result.job_id
                    or record.captured_scope_epoch != epoch
                ):
                    raise PublicationPreviewIdempotencyError(
                        "The queued preview result is bound incorrectly."
                    )
                authority = self._authority.get_preview(connection, preview_id)
                if (target is None) != (authority is None) or (
                    target is not None
                    and authority is not None
                    and authority.descriptor != target
                ):
                    raise PublicationPreviewIdempotencyError(
                        "The queued preview authority differs from the replay request."
                    )
                return result
        finally:
            connection.close()

    def _commit_upload_preview(
        self,
        *,
        bundle: SourceArtifactBundle,
        title: str,
        request_id: str,
        operation_id: str,
        actor_id: str,
        epoch: int,
        request_sha256: str,
        dedup_id: str,
        preview_id: str,
        target: HigherPublicationTargetDescriptor | None,
        request_meta: Mapping[str, object],
    ) -> QueuedPublicationPreviewResult:
        connection = self._factory.open()
        result: QueuedPublicationPreviewResult | None = None
        try:
            with transaction(connection):
                actor, publication_space, modes, publication_resolution = (
                    self._authorized_targets(
                        connection,
                        actor_id=actor_id,
                        epoch=epoch,
                        target=target,
                    )
                )
                now_ms = db_now_ms(connection)
                try:
                    begun = self._users.begin_operation(
                        connection,
                        actor_user_id=actor_id,
                        operation_type="publication_preview.create",
                        operation_id=operation_id,
                        request_sha256=request_sha256,
                        dedup_id=dedup_id,
                        now_ms=now_ms,
                    )
                except OperationIdempotencyConflictError as exc:
                    raise PublicationPreviewIdempotencyError(str(exc)) from exc
                if not begun.inserted:
                    if begun.operation.status != "committed":
                        raise PublicationPreviewIdempotencyError(
                            "The publication preview operation is already in progress."
                        )
                    result = _queued_result_from_mapping(begun.operation.result)
                    record = self._previews.require_for_actor(
                        connection,
                        preview_id=preview_id,
                        actor_user_id=actor_id,
                    )
                    expected = _queued_result(
                        request_id=result.request_id,
                        operation_id=operation_id,
                        preview_id=preview_id,
                        job_id=result.job_id,
                        created_at_ms=record.created_at_ms,
                    )
                    if result != expected or record.durable_job_id != result.job_id:
                        raise PublicationPreviewIdempotencyError(
                            "The queued preview result is bound incorrectly."
                        )
                    return result

                self._artifacts.put_source_bundle(connection, bundle)
                self._put_preview_authority(
                    connection,
                    preview_id=preview_id,
                    actor=actor,
                    resolution=publication_resolution,
                    target=target,
                    created_at_ms=now_ms,
                )
                job_id = _derived_uuid(_JOB_NAMESPACE, actor_id, operation_id)
                job_dedup_id = _derived_uuid(
                    _JOB_DEDUP_NAMESPACE, actor_id, operation_id
                )
                job_input = {
                    "artifact_id": bundle.ids.artifact_id,
                    "preview_id": preview_id,
                    "rule_version": self._upload_rule_version,
                }
                job_input_sha = canonical_job_input_sha256(job_input)
                self._users.begin_operation(
                    connection,
                    actor_user_id=actor_id,
                    operation_type="job:publication_preview.extract",
                    operation_id=operation_id,
                    request_sha256=job_input_sha,
                    dedup_id=job_dedup_id,
                    now_ms=now_ms,
                )
                capabilities = tuple(
                    sorted(
                        capability.value
                        for capability in publication_space.capabilities
                    )
                )
                plan = publication_resolution.access_plan
                authorization_fence = (
                    None
                    if plan is None
                    else {
                        # Preview extraction does not mutate the corpus.  A
                        # different file from the same batch may be published
                        # while this job is extracting, so fencing extraction
                        # on the exact corpus generation would spuriously
                        # reject otherwise-authorized concurrent uploads.  The
                        # publication transaction still revalidates the
                        # current target authority before it commits.
                        "fence_mode": "mutation_authority",
                        "corpus_closure_generation": plan.corpus_closure.generation,
                        "corpus_closure_sha256": plan.corpus_closure.sha256,
                        "organization_snapshot_version": (
                            str(plan.corpus_closure.organization_snapshot_version)
                        ),
                        "native_mutation_space_id": plan.native_mutation_space_id,
                        "role_assignment_generation": plan.role_assignment_generation,
                        "role_assignment_id": plan.role_assignment_id,
                    }
                )
                job = self._jobs.enqueue(
                    connection,
                    JobEnqueueDraft(
                        job_id=job_id,
                        dedup_id=job_dedup_id,
                        operation_id=operation_id,
                        actor_kind="user",
                        actor_user_id=actor_id,
                        space_id=publication_space.id,
                        job_type="publication_preview.extract",
                        handler_version=self._upload_handler_version,
                        effect_key=f"publication-preview:{preview_id}:extract",
                        input_ref=job_input,
                        expected_state=self._upload_expected_state({
                            "_authorization_fence": authorization_fence,
                            "artifact_id": bundle.ids.artifact_id,
                            "media_type": bundle.raw_blob.media_type,
                            "preview_id": preview_id,
                            "raw_sha256": bundle.raw_blob.sha256,
                            "raw_size_bytes": bundle.raw_blob.size_bytes,
                            "schema_version": 1,
                            "title": title,
                        }),
                        authorization_snapshot={
                            "account_status": "active",
                            "actor_kind": "user",
                            "actor_user_id": actor_id,
                            "capabilities": list(capabilities),
                            "current_part_id": actor.current_part_id,
                            "schema_version": 1,
                            "scope_epoch": actor.scope_epoch,
                            "space_id": publication_space.id,
                            "system_role": actor.system_role,
                        },
                        authorization_scope_epoch=actor.scope_epoch,
                        authorization_part_id=actor.current_part_id,
                        authorization_capabilities=capabilities,
                        max_attempts=3,
                        cancellable=True,
                        available_at_ms=now_ms,
                    ),
                ).job
                preview = self._previews.insert_queued(
                    connection,
                    QueuedPreviewDraft(
                        preview_id=preview_id,
                        actor_user_id=actor_id,
                        artifact_id=bundle.ids.artifact_id,
                        durable_job_id=job.id,
                        captured_part_id=actor.current_part_id,
                        captured_scope_epoch=actor.scope_epoch,
                        available_target_modes=modes,
                        rule_version=self._upload_rule_version,
                        expires_at_ms=job.created_at_ms + PREVIEW_TTL_MS,
                        created_at_ms=job.created_at_ms,
                    ),
                )
                if self._wiki_upload is not None:
                    # Only the new operation registers. Exact legacy replay
                    # neither backfills a request nor adopts deployment drift.
                    self._wiki_upload.register_upload(
                        connection, preview_id=preview_id, parent=job
                    )
                result = _queued_result(
                    request_id=request_id,
                    operation_id=operation_id,
                    preview_id=preview_id,
                    job_id=job.id,
                    created_at_ms=preview.created_at_ms,
                )
                self._append_preview_event(
                    connection,
                    actor=actor,
                    publication_space_id=publication_space.id,
                    preview=preview,
                    request_id=request_id,
                    operation_id=operation_id,
                    request_meta=request_meta,
                    now_ms=now_ms,
                    outcome="queued",
                    event_type="publication.preview.queued",
                )
                self._users.commit_operation(
                    connection,
                    dedup_id=job_dedup_id,
                    result=result.to_mapping(),
                    http_status=202,
                    completed_at_ms=now_ms,
                )
                self._users.commit_operation(
                    connection,
                    dedup_id=dedup_id,
                    result=result.to_mapping(),
                    http_status=202,
                    completed_at_ms=now_ms,
                )
        except (OperationIdempotencyConflictError, OperationInProgressError) as exc:
            raise PublicationPreviewIdempotencyError(str(exc)) from exc
        finally:
            connection.close()
        if result is None:
            raise PublicationPreviewServiceError(
                "The queued preview result is unavailable."
            )
        return result

    def _append_preview_event(
        self,
        connection,
        *,
        actor,
        publication_space_id: str,
        preview: PublicationPreviewRecord,
        request_id: str,
        operation_id: str,
        request_meta: Mapping[str, object],
        now_ms: int,
        outcome: str,
        event_type: str,
    ) -> None:
        self._audit.append(
            connection,
            AuditEventDraft(
                event_uuid=_derived_uuid(
                    _AUDIT_NAMESPACE,
                    actor.id,
                    operation_id,
                    outcome,
                    str(preview.row_version),
                ),
                occurred_at_ms=now_ms,
                request_id=request_id,
                operation_id=operation_id,
                action="publication.preview.create",
                outcome=outcome,
                actor_kind="user",
                actor_user_id=actor.id,
                actor_display_name=actor.display_name,
                actor_role_snapshot=actor.system_role,
                space_id=publication_space_id,
                details={
                    "artifact_id": preview.artifact_id,
                    "available_target_modes": list(
                        preview.available_target_modes
                    ),
                    "job_id": preview.durable_job_id,
                    "preview_id": preview.preview_id,
                    "rule_version": preview.rule_version,
                    "schema_version": 1,
                    "source_snapshot_sha256": preview.source_snapshot_sha256,
                    "status": preview.status,
                },
                request_meta=request_meta,
            ),
        )
        self._outbox.append(
            connection,
            OutboxEventDraft(
                logical_key=(
                    f"publication-preview:{preview.preview_id}:"
                    f"{outcome}:{preview.row_version}"
                ),
                space_id=publication_space_id,
                aggregate_type="publication_preview",
                aggregate_id=preview.preview_id,
                aggregate_version=preview.row_version,
                event_type=event_type,
                payload={
                    "actor_user_id": actor.id,
                    "job_id": preview.durable_job_id,
                    "preview_id": preview.preview_id,
                    "row_version": preview.row_version,
                    "schema_version": 1,
                    "source_snapshot_sha256": preview.source_snapshot_sha256,
                    "status": preview.status,
                },
                available_at_ms=now_ms,
            ),
        )

    def _commit_ready_preview(
        self,
        *,
        bundle: SourceArtifactBundle,
        prepared: PreparedPublicationArtifact,
        title: str,
        source_type: Literal["manual", "chat", "notion"],
        source_details: Mapping[str, object],
        source_revalidator: Callable[[ManagedConnection], None] | None,
        request_id: str,
        operation_id: str,
        actor_id: str,
        epoch: int,
        request_sha256: str,
        dedup_id: str,
        preview_id: str,
        target: HigherPublicationTargetDescriptor | None,
        request_meta: Mapping[str, object],
    ) -> PublicationPreviewRecord:
        connection = self._factory.open()
        result: PublicationPreviewRecord | None = None
        try:
            with transaction(connection):
                actor, publication_space, modes, publication_resolution = (
                    self._authorized_targets(
                        connection,
                        actor_id=actor_id,
                        epoch=epoch,
                        target=target,
                    )
                )
                now_ms = db_now_ms(connection)
                try:
                    begun = self._users.begin_operation(
                        connection,
                        actor_user_id=actor_id,
                        operation_type="publication_preview.create",
                        operation_id=operation_id,
                        request_sha256=request_sha256,
                        dedup_id=dedup_id,
                        now_ms=now_ms,
                    )
                except OperationIdempotencyConflictError as exc:
                    raise PublicationPreviewIdempotencyError(str(exc)) from exc
                if not begun.inserted:
                    if begun.operation.status != "committed":
                        raise PublicationPreviewIdempotencyError(
                            "The publication preview operation is already in progress."
                        )
                    result = self._previews.require_for_actor(
                        connection, preview_id=preview_id, actor_user_id=actor_id
                    )
                    if begun.operation.result != result.ready_result:
                        raise PublicationPreviewIdempotencyError(
                            "The stored preview result differs from its operation result."
                        )
                    return result

                if source_revalidator is not None:
                    source_revalidator(connection)

                self._artifacts.put_source_bundle(connection, bundle)
                self._put_preview_authority(
                    connection,
                    preview_id=preview_id,
                    actor=actor,
                    resolution=publication_resolution,
                    target=target,
                    created_at_ms=now_ms,
                )
                extracted_sha = prepared.extraction.extracted_sha256
                extracted_size = prepared.extraction.extracted_size_bytes
                if extracted_sha is None or extracted_size is None:
                    raise PublicationPreviewServiceError(
                        "The source did not produce a ready snapshot."
                    )
                warnings_value = json.loads(prepared.extraction.warnings_json)
                if not isinstance(warnings_value, list) or any(
                    not isinstance(value, str) for value in warnings_value
                ):
                    raise PublicationPreviewServiceError(
                        "The prepared extraction warnings are invalid."
                    )
                result = self._previews.insert_ready(
                    connection,
                    ReadyPreviewDraft(
                        preview_id=preview_id,
                        request_id=request_id,
                        operation_id=operation_id,
                        actor_user_id=actor_id,
                        artifact_id=bundle.ids.artifact_id,
                        extraction_snapshot_id=bundle.ids.extraction_snapshot_id,
                        durable_job_id=None,
                        source_snapshot_sha256=extracted_sha,
                        captured_part_id=actor.current_part_id,
                        captured_scope_epoch=actor.scope_epoch,
                        available_target_modes=modes,
                        rule_version=PUBLICATION_PREVIEW_RULE_VERSION,
                        title=title,
                        size_bytes=extracted_size,
                        warnings=tuple(warnings_value),
                        deterministic_findings=_preview_findings(prepared),
                        expires_at_ms=now_ms + PREVIEW_TTL_MS,
                        created_at_ms=now_ms,
                        completed_at_ms=now_ms,
                    ),
                )
                ready_result = result.ready_result
                if ready_result is None:
                    raise PublicationPreviewServiceError(
                        "The ready preview result is unavailable."
                    )
                self._audit.append(
                    connection,
                    AuditEventDraft(
                        event_uuid=_derived_uuid(
                            _AUDIT_NAMESPACE, actor_id, operation_id, "ready"
                        ),
                        occurred_at_ms=now_ms,
                        request_id=request_id,
                        operation_id=operation_id,
                        action="publication.preview.create",
                        outcome="ready",
                        actor_kind="user",
                        actor_user_id=actor.id,
                        actor_display_name=actor.display_name,
                        actor_role_snapshot=actor.system_role,
                        space_id=publication_space.id,
                        details={
                            "artifact_id": bundle.ids.artifact_id,
                            "available_target_modes": list(modes),
                            "preview_id": preview_id,
                            "rule_version": PUBLICATION_PREVIEW_RULE_VERSION,
                            "schema_version": 1,
                            "source_snapshot_sha256": extracted_sha,
                            "source_type": source_type,
                            **dict(source_details),
                        },
                        request_meta=request_meta,
                    ),
                )
                self._outbox.append(
                    connection,
                    OutboxEventDraft(
                        logical_key=f"publication-preview:{preview_id}:ready:1",
                        space_id=publication_space.id,
                        aggregate_type="publication_preview",
                        aggregate_id=preview_id,
                        aggregate_version=result.row_version,
                        event_type="publication.preview.ready",
                        payload={
                            "actor_user_id": actor_id,
                            "preview_id": preview_id,
                            "row_version": result.row_version,
                            "schema_version": 1,
                            "source_snapshot_sha256": extracted_sha,
                        },
                        available_at_ms=now_ms,
                    ),
                )
                self._users.commit_operation(
                    connection,
                    dedup_id=dedup_id,
                    result=ready_result,
                    http_status=200,
                    completed_at_ms=now_ms,
                )
        except OperationInProgressError as exc:
            raise PublicationPreviewIdempotencyError(str(exc)) from exc
        finally:
            connection.close()
        if result is None:
            raise PublicationPreviewServiceError(
                "The publication preview result is unavailable."
            )
        return result


def _publication_result(
    commit: PublicationCommitResult,
    *,
    request_id: str,
    operation_id: str,
    target: HigherPublicationTargetDescriptor | None = None,
    body_source: Literal["authored_wiki", "extraction", "preserved_wiki", "native_wiki", "pptx_role_wiki", "pdf_wiki", "pdf_visual_wiki", "office_hybrid_wiki", "pdf_continuation_wiki"] | None = None,
) -> PublicationResult:
    return PublicationResult(
        request_id=request_id,
        operation_id=operation_id,
        publication_operation_id=commit.publication_operation_id,
        preview_id=commit.preview_id,
        source_artifact_id=commit.source_artifact_id,
        target_mode=commit.target_mode,
        lineage_group_id=commit.lineage_group_id,
        targets=tuple(target.to_mapping() for target in commit.targets),
        target=target,
        authored_snapshot_id=commit.authored_snapshot_id,
        reused_extraction_snapshot_id=commit.reused_extraction_snapshot_id,
        body_source=body_source,
        preserved_snapshot_id=commit.preserved_snapshot_id,
        native_snapshot_id=commit.native_snapshot_id,
        pptx_role_snapshot_id=commit.pptx_role_snapshot_id,
        pdf_snapshot_id=commit.pdf_snapshot_id,
        pdf_visual_snapshot_id=commit.pdf_visual_snapshot_id,
        office_hybrid_snapshot_id=commit.office_hybrid_snapshot_id,
        pdf_continuation_snapshot_id=commit.pdf_continuation_snapshot_id,
    )


def _stored_publication_result(value: object) -> PublicationResult:
    # Persisted result corruption is an exact-replay failure, not malformed
    # user input. Reuse the strict field validators without exposing their
    # request-validation error classification at this storage boundary.
    try:
        return _parse_stored_publication_result(value)
    except PublicationPreviewValidationError as exc:
        raise PublicationPreviewIdempotencyError(
            "The stored publication result is not canonical."
        ) from exc


def _parse_stored_publication_result(value: object) -> PublicationResult:
    if not isinstance(value, Mapping):
        raise PublicationPreviewIdempotencyError(
            "The stored publication result is not an object."
        )
    required = {
        "lineage_group_id",
        "operation_id",
        "preview_id",
        "publication_operation_id",
        "request_id",
        "schema_version",
        "source_artifact_id",
        "status",
        "target_mode",
        "targets",
    }
    target_mode = value.get("target_mode")
    if "body_source" in value:
        required.add("body_source")
        expected_kind = ("pdf_continuation_wiki" if value.get("pdf_continuation_snapshot_id") is not None else
            "office_hybrid_wiki" if value.get("office_hybrid_snapshot_id") is not None else
            "pdf_visual_wiki" if value.get("pdf_visual_snapshot_id") is not None else
            "pdf_wiki" if value.get("pdf_snapshot_id") is not None else
            "pptx_role_wiki" if value.get("pptx_role_snapshot_id") is not None else
            "native_wiki" if value.get("native_snapshot_id") is not None else
            "preserved_wiki" if value.get("preserved_snapshot_id") is not None else
            "authored_wiki" if value.get("authored_snapshot_id") is not None else "extraction")
        if value["body_source"] != expected_kind:
            raise PublicationPreviewIdempotencyError("The stored publication source kind is inconsistent.")
    if "pdf_continuation_snapshot_id" in value:
        required.add("pdf_continuation_snapshot_id")
        if (any(key in value for key in ("authored_snapshot_id", "preserved_snapshot_id", "native_snapshot_id",
                "pptx_role_snapshot_id", "pdf_snapshot_id", "pdf_visual_snapshot_id", "office_hybrid_snapshot_id",
                "reused_extraction_snapshot_id")) or value.get("body_source") != "pdf_continuation_wiki"):
            raise PublicationPreviewIdempotencyError("The stored continuation body selection is ambiguous.")
    if "authored_snapshot_id" in value:
        required.add("authored_snapshot_id")
    if "office_hybrid_snapshot_id" in value:
        required.add("office_hybrid_snapshot_id")
        if (any(key in value for key in ("authored_snapshot_id", "preserved_snapshot_id", "native_snapshot_id",
                "pptx_role_snapshot_id", "pdf_snapshot_id", "pdf_visual_snapshot_id", "reused_extraction_snapshot_id"))
                or value.get("body_source") != "office_hybrid_wiki"):
            raise PublicationPreviewIdempotencyError("The stored Office body selection is ambiguous.")
    if "pdf_visual_snapshot_id" in value:
        required.add("pdf_visual_snapshot_id")
        if (any(key in value for key in ("authored_snapshot_id", "preserved_snapshot_id", "native_snapshot_id",
                "pptx_role_snapshot_id", "pdf_snapshot_id", "reused_extraction_snapshot_id"))
                or value.get("body_source") != "pdf_visual_wiki"):
            raise PublicationPreviewIdempotencyError("The stored visual PDF body selection is ambiguous.")
    if "pdf_snapshot_id" in value:
        required.add("pdf_snapshot_id")
        if (any(key in value for key in ("authored_snapshot_id", "preserved_snapshot_id", "native_snapshot_id",
                "pptx_role_snapshot_id", "reused_extraction_snapshot_id")) or value.get("body_source") != "pdf_wiki"):
            raise PublicationPreviewIdempotencyError("The stored PDF body selection is ambiguous.")
    if "pptx_role_snapshot_id" in value:
        required.add("pptx_role_snapshot_id")
        if (any(key in value for key in ("authored_snapshot_id", "preserved_snapshot_id", "native_snapshot_id", "reused_extraction_snapshot_id"))
                or value.get("body_source") != "pptx_role_wiki"):
            raise PublicationPreviewIdempotencyError("The stored role body selection is ambiguous.")
    if "native_snapshot_id" in value:
        required.add("native_snapshot_id")
        if (any(key in value for key in ("authored_snapshot_id", "preserved_snapshot_id", "reused_extraction_snapshot_id"))
                or value.get("body_source") != "native_wiki"):
            raise PublicationPreviewIdempotencyError("The stored native body selection is ambiguous.")
    if "preserved_snapshot_id" in value:
        required.add("preserved_snapshot_id")
        if ("authored_snapshot_id" in value or "reused_extraction_snapshot_id" in value
                or value.get("body_source") != "preserved_wiki"):
            raise PublicationPreviewIdempotencyError("The stored body selection is ambiguous.")
    if "reused_extraction_snapshot_id" in value:
        required.add("reused_extraction_snapshot_id")
        if "authored_snapshot_id" in value:
            raise PublicationPreviewIdempotencyError("The stored body selection is ambiguous.")
    if target_mode == "explicit":
        required.add("target")
    if set(value) != required or value.get("schema_version") != 1 or value.get(
        "status"
    ) != "committed":
        raise PublicationPreviewIdempotencyError(
            "The stored publication result has an invalid shape."
        )
    targets = value.get("targets")
    if target_mode not in {"personal", "part", "both", "explicit"} or not isinstance(
        targets, list
    ):
        raise PublicationPreviewIdempotencyError(
            "The stored publication targets are invalid."
        )
    lineage = value.get("lineage_group_id")
    if lineage is not None:
        lineage = _preview_uuid(lineage, field_name="lineage_group_id")
    target: HigherPublicationTargetDescriptor | None = None
    if target_mode == "explicit":
        raw_target = value.get("target")
        if not isinstance(raw_target, Mapping) or set(raw_target) != {
            "capability",
            "kind",
            "role_assignment_generation",
            "role_assignment_id",
            "space_id",
        }:
            raise PublicationPreviewIdempotencyError(
                "The stored explicit publication target is invalid."
            )
        target = _publication_target(
            HigherPublicationTargetDescriptor(
                space_id=raw_target.get("space_id"),  # type: ignore[arg-type]
                kind=raw_target.get("kind"),  # type: ignore[arg-type]
                capability=raw_target.get("capability"),  # type: ignore[arg-type]
                role_assignment_id=raw_target.get("role_assignment_id"),  # type: ignore[arg-type]
                role_assignment_generation=raw_target.get(
                    "role_assignment_generation"
                ),  # type: ignore[arg-type]
            )
        )
    result = PublicationResult(
        request_id=_preview_uuid(value.get("request_id"), field_name="request_id"),
        operation_id=_preview_uuid(
            value.get("operation_id"), field_name="operation_id"
        ),
        publication_operation_id=_preview_uuid(
            value.get("publication_operation_id"),
            field_name="publication_operation_id",
        ),
        preview_id=_preview_uuid(value.get("preview_id"), field_name="preview_id"),
        source_artifact_id=_preview_uuid(
            value.get("source_artifact_id"), field_name="source_artifact_id"
        ),
        target_mode=target_mode,  # type: ignore[arg-type]
        lineage_group_id=lineage,
        targets=tuple(dict(item) if isinstance(item, Mapping) else {} for item in targets),
        target=target,
        authored_snapshot_id=(None if "authored_snapshot_id" not in value else
            _preview_uuid(value["authored_snapshot_id"], field_name="authored_snapshot_id")),
        reused_extraction_snapshot_id=(None if "reused_extraction_snapshot_id" not in value else
            _preview_uuid(value["reused_extraction_snapshot_id"], field_name="reused_extraction_snapshot_id")),
        body_source=value.get("body_source"),
        preserved_snapshot_id=(None if "preserved_snapshot_id" not in value else
            _preview_uuid(value["preserved_snapshot_id"], field_name="preserved_snapshot_id")),
        native_snapshot_id=(None if "native_snapshot_id" not in value else
            _preview_uuid(value["native_snapshot_id"], field_name="native_snapshot_id")),
        pptx_role_snapshot_id=(None if "pptx_role_snapshot_id" not in value else
            _preview_uuid(value["pptx_role_snapshot_id"], field_name="pptx_role_snapshot_id")),
        pdf_snapshot_id=(None if "pdf_snapshot_id" not in value else
            _preview_uuid(value["pdf_snapshot_id"], field_name="pdf_snapshot_id")),
        pdf_visual_snapshot_id=(None if "pdf_visual_snapshot_id" not in value else
            _preview_uuid(value["pdf_visual_snapshot_id"], field_name="pdf_visual_snapshot_id")),
        office_hybrid_snapshot_id=(None if "office_hybrid_snapshot_id" not in value else
            _preview_uuid(value["office_hybrid_snapshot_id"], field_name="office_hybrid_snapshot_id")),
        pdf_continuation_snapshot_id=(None if "pdf_continuation_snapshot_id" not in value else
            _preview_uuid(value["pdf_continuation_snapshot_id"], field_name="pdf_continuation_snapshot_id")),
    )
    if result.to_mapping() != dict(value):
        raise PublicationPreviewIdempotencyError(
            "The stored publication result is not canonical."
        )
    return result


@dataclass(frozen=True, slots=True)
class ProtectedPublicationEvidence:
    actor_user_id: str
    preview_id: str
    artifact_id: str
    extraction_snapshot_id: str
    original_filename: str
    original_sha256: str
    derivative_sha256: str | None
    source_kind: str
    opening_method: str


class PublicationService:
    """P4.3-P4.4 publication with one service-owned transaction."""

    __slots__ = (
        "_audit",
        "_authority",
        "_commits",
        "_factory",
        "_failpoint",
        "_wiki_publication",
        "_jobs",
        "_outbox",
        "_previews",
        "_protected_publication_authorizer",
        "_require_protected_publication_authorization",
        "_policy_protection_classifier",
        "_policy_source_loader",
        "_scope",
        "_users",
    )

    def __init__(
        self,
        factory: ConnectionFactory,
        scope_resolver: ScopeResolver,
        *,
        commits: PublicationCommitRepository | None = None,
        authority: HigherPublicationAuthorityRepository | None = None,
        previews: PublicationPreviewRepository | None = None,
        users: UserRepository | None = None,
        audit: AuditRepository | None = None,
        outbox: OutboxRepository | None = None,
        failpoint: Callable[[str], None] | None = None,
        wiki_publication: WikiPublicationSelector | None = None,
        protected_publication_authorizer: Callable[[ProtectedPublicationEvidence], bool] | None = None,
        require_protected_publication_authorization: bool = False,
        policy_protection_classifier: PolicyProtectedSourceClassifier | None = None,
        policy_source_loader: PublicationSourceLoader | None = None,
    ) -> None:
        if not isinstance(factory, ConnectionFactory):
            raise TypeError("PublicationService requires a ConnectionFactory.")
        if not isinstance(scope_resolver, ScopeResolver):
            raise TypeError("PublicationService requires a ScopeResolver.")
        if failpoint is not None and not callable(failpoint):
            raise TypeError("failpoint must be callable or None.")
        if protected_publication_authorizer is not None and not callable(protected_publication_authorizer):
            raise TypeError("Protected publication authorizer must be callable or None.")
        if type(require_protected_publication_authorization) is not bool:
            raise TypeError("Protected publication authorization policy must be boolean.")
        if (policy_protection_classifier is None) != (policy_source_loader is None):
            raise ValueError("Current policy publication checks require a classifier and source loader.")
        if (policy_protection_classifier is not None
                and not callable(getattr(policy_protection_classifier, "classify_for_actor", None))):
            raise TypeError("The current policy classifier is invalid.")
        if (policy_source_loader is not None
                and not callable(getattr(policy_source_loader, "load", None))):
            raise TypeError("The current policy source loader is invalid.")
        self._factory = factory
        self._scope = scope_resolver
        self._authority = authority or HigherPublicationAuthorityRepository()
        self._commits = commits or PublicationCommitRepository(
            authority=self._authority
        )
        self._previews = previews or PublicationPreviewRepository()
        self._users = users or UserRepository()
        self._audit = audit or AuditRepository()
        self._outbox = outbox or OutboxRepository()
        self._jobs = JobRepository()
        self._failpoint = failpoint
        self._protected_publication_authorizer = protected_publication_authorizer
        self._require_protected_publication_authorization = require_protected_publication_authorization
        self._policy_protection_classifier = policy_protection_classifier
        self._policy_source_loader = policy_source_loader
        if wiki_publication is not None and (
            not isinstance(wiki_publication, WikiPublicationSelector)
            or wiki_publication.database_path != factory.database_path
        ):
            raise TypeError("Wiki publication requires a same-database selector.")
        self._wiki_publication = wiki_publication or WikiPublicationSelector(factory)

    def _hit(self, name: str) -> None:
        if self._failpoint is not None:
            self._failpoint(name)

    def _authorize_current_source_policy(self, *, actor_id: str, preview_id: str,
            source_snapshot_sha256: str) -> None:
        """Reclassify a new upload at commit, including originally ordinary files.

        A protected-origin-only authority callback cannot detect an ordinary
        preview that gained account-bound protection after the worker ran.
        This read holds no SQLite transaction while calling the provider.
        """
        classifier = self._policy_protection_classifier
        loader = self._policy_source_loader
        if classifier is None or loader is None:
            return
        connection = self._factory.open(read_only=True)
        try:
            with transaction(connection, TransactionMode.DEFERRED):
                row = connection.execute(
                    """SELECT p.artifact_id,p.source_snapshot_sha256,
                              a.origin_type,a.original_filename,a.raw_sha256,
                              a.raw_size_bytes,a.media_type,
                              r.source_kind AS recorded_protection
                         FROM publication_preview p
                         JOIN source_artifact a ON a.id=p.artifact_id
                         LEFT JOIN protected_extraction_provenance r
                           ON r.artifact_id=a.id
                          AND r.extraction_snapshot_id=p.extraction_snapshot_id
                        WHERE p.id=? AND p.actor_user_id=?""",
                    (preview_id, actor_id),
                ).fetchone()
                if row is None or row["source_snapshot_sha256"] != source_snapshot_sha256:
                    raise PublicationPreviewAuthorizationError(
                        "The publication source protection binding is unavailable."
                    )
                if row["origin_type"] != "upload":
                    return
                artifact_id = row["artifact_id"]
                filename = row["original_filename"]
                raw_sha256 = row["raw_sha256"]
                raw_size = row["raw_size_bytes"]
                media_type = row["media_type"]
                recorded = row["recorded_protection"]
        finally:
            connection.close()
        if not isinstance(filename, str) or not filename:
            raise PublicationPreviewAuthorizationError(
                "The upload source filename is unavailable for current policy verification."
            )
        try:
            loaded = loader.load(
                artifact_id=artifact_id, expected_sha256=raw_sha256,
                expected_size=raw_size, expected_media_type=media_type,
            )
            try:
                if loaded.original_filename != filename:
                    raise ValueError("The upload source filename changed.")
                current = classify_protected_for_actor(
                    filename, loaded.content, actor_user_id=actor_id,
                    policy_classifier=classifier,
                )
            finally:
                loaded.lease.close()
        except Exception as exc:
            raise PublicationPreviewAuthorizationError(
                "The current source protection status could not be verified."
            ) from exc
        if (None if current is None else current.value) != recorded:
            raise PublicationPreviewAuthorizationError(
                "The source protection status changed before publication."
            )

    def _authorize_protected_publication(self, *, actor_id: str, preview_id: str,
            source_snapshot_sha256: str) -> None:
        """Check external rights for a new operation, after source preparation.

        This is a server-owned callback, never a client-supplied permission bit.
        An already committed idempotent replay does not publish a new body.
        External rights cannot be made atomic with SQLite; the company adapter
        must perform its own current, actor-bound check at this boundary.
        """
        if (self._protected_publication_authorizer is None
                and not self._require_protected_publication_authorization):
            return
        connection = self._factory.open(read_only=True)
        try:
            with transaction(connection, TransactionMode.DEFERRED):
                if connection.execute(
                    """SELECT 1 FROM sqlite_schema WHERE type='table'
                         AND name='protected_extraction_provenance'"""
                ).fetchone() is None:
                    legacy = connection.execute(
                        """SELECT e.extractor_name FROM publication_preview p
                             JOIN extraction_snapshot e ON e.id=p.extraction_snapshot_id
                            WHERE p.id=? AND p.actor_user_id=?""",
                        (preview_id, actor_id),
                    ).fetchone()
                    if legacy is None or legacy[0] in {
                        "canonical-approved-owner-pdf",
                        "canonical-authorized-protected-snapshot",
                    }:
                        raise PublicationPreviewAuthorizationError(
                            "The protected publication has no origin catalog."
                        )
                    return
                row = connection.execute(
                    """SELECT p.artifact_id,p.extraction_snapshot_id,p.source_snapshot_sha256,
                              a.original_filename,
                              a.raw_sha256,e.extractor_name,r.original_sha256 AS protected_sha256,
                              r.derivative_sha256,r.source_kind,r.opening_method
                         FROM publication_preview p
                         JOIN source_artifact a ON a.id=p.artifact_id
                         JOIN extraction_snapshot e ON e.id=p.extraction_snapshot_id
                         LEFT JOIN protected_extraction_provenance r
                           ON r.extraction_snapshot_id=e.id AND r.artifact_id=a.id
                        WHERE p.id=? AND p.actor_user_id=?""",
                    (preview_id, actor_id),
                ).fetchone()
                if row is None or row["source_snapshot_sha256"] != source_snapshot_sha256:
                    raise PublicationPreviewAuthorizationError(
                        "The protected publication source changed."
                    )
                if row["protected_sha256"] is None:
                    if row["extractor_name"] in {
                        "canonical-approved-owner-pdf",
                        "canonical-authorized-protected-snapshot",
                    }:
                        raise PublicationPreviewAuthorizationError(
                            "The protected publication has no origin record."
                        )
                    return
                if (row["protected_sha256"] != row["raw_sha256"]
                        or row["original_filename"] is None):
                    raise PublicationPreviewAuthorizationError(
                        "The protected publication origin is inconsistent."
                    )
                evidence = ProtectedPublicationEvidence(
                    actor_user_id=actor_id, preview_id=preview_id,
                    artifact_id=row["artifact_id"],
                    extraction_snapshot_id=row["extraction_snapshot_id"],
                    original_filename=row["original_filename"],
                    original_sha256=row["raw_sha256"],
                    derivative_sha256=row["derivative_sha256"],
                    source_kind=row["source_kind"],
                    opening_method=row["opening_method"],
                )
        finally:
            connection.close()
        authorizer = self._protected_publication_authorizer
        if authorizer is None:
            if self._require_protected_publication_authorization:
                raise PublicationPreviewAuthorizationError(
                    "A current protected-source permission check is required."
                )
            return
        try:
            authorized = authorizer(evidence) is True
        except Exception:
            authorized = False
        if not authorized:
            raise PublicationPreviewAuthorizationError(
                "Current protected-source permission does not allow publication."
            )

    @staticmethod
    def _request_sha256(
        *,
        actor_id: str,
        epoch: int,
        operation_id: str,
        preview_id: str,
        source_snapshot_sha256: str,
        target_mode: str,
        target: HigherPublicationTargetDescriptor | None,
        personal_space_id: str | None,
        authored_snapshot_id: str | None = None,
    ) -> str:
        payload: dict[str, object] = {
                    "actor_scope_epoch": epoch,
                    "actor_user_id": actor_id,
                    "operation_id": operation_id,
                    "operation_type": "publication",
                    "preview_id": preview_id,
                    "schema_version": 1,
                    "source_snapshot_sha256": source_snapshot_sha256,
                    "target_mode": target_mode,
        }
        if target is not None:
            payload["target"] = target.to_mapping()
        if personal_space_id is not None:
            payload["personal_space_id"] = personal_space_id
        if authored_snapshot_id is not None:
            payload["authored_snapshot_id"] = authored_snapshot_id
        return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()

    def _authored_preparation_targets(self, connection, *, actor_id, epoch, preview,
                                     target_mode, target, personal_space_id):
        """Authorize native targets before reading any other publisher's Wiki.

        The existing write-phase authority/preview fences still run independently.
        This read-phase resolution does not grant a reusable capability.
        """
        principal = SessionPrincipal(user_id=actor_id, captured_scope_epoch=epoch)
        try:
            if target_mode == "explicit":
                authority = self._authority.get_preview(connection, preview.preview_id)
                if authority is None or authority.actor_user_id != actor_id or authority.descriptor != target:
                    raise PublicationPreviewAuthorizationError("The authored preview target is not exact.")
                resolved = self._scope.resolve_in_transaction(connection, principal, target.space_id, Capability.PUBLISH)
                if (resolved.space.kind != target.kind or resolved.access_plan is None
                        or resolved.access_plan.native_mutation_space_id != target.space_id
                        or resolved.access_plan.role_assignment_id != target.role_assignment_id
                        or resolved.access_plan.role_assignment_generation != target.role_assignment_generation
                        or resolved.access_plan.actor_row_version != authority.actor_row_version
                        or resolved.access_plan.corpus_closure.organization_snapshot_version != authority.organization_snapshot_version):
                    raise PublicationPreviewAuthorizationError("The authored target authority is stale.")
                return (PublicationCommitTarget(target.space_id, target.kind),)
            selectable = self._scope.list_selectable_in_transaction(connection, principal)
            personal = next((row for row in selectable.items if row.kind == "personal"
                             and Capability.PUBLISH in row.capabilities), None)
            personal_id = None if personal is None else personal.id
            if personal_space_id is not None:
                resolved = self._scope.resolve_in_transaction(connection, principal, personal_space_id, Capability.PUBLISH)
                if resolved.space.kind != "personal":
                    raise PublicationPreviewAuthorizationError("The authored personal Wiki is not exact.")
                personal_id = resolved.space.id
            if personal_id is None:
                raise PublicationPreviewAuthorizationError("Personal publication is not currently authorized.")
            personal_target = PublicationCommitTarget(personal_id, "personal")
            if target_mode == "personal":
                return (personal_target,)
            part = next((row for row in selectable.items if row.kind == "part"
                         and Capability.PUBLISH in row.capabilities), None)
            actor = self._users.require(connection, actor_id)
            if part is None or actor.current_part_id is None or preview.captured_part_id != actor.current_part_id:
                raise PublicationPreviewAuthorizationError("The captured Part no longer authorizes publication.")
            part_target = PublicationCommitTarget(part.id, "part")
            return (part_target,) if target_mode == "part" else (personal_target, part_target)
        except ScopeResolutionError as exc:
            raise PublicationPreviewAuthorizationError("Current scope does not authorize authored publication.") from exc

    def _publication_preview_current(self, connection, preview, now_ms):
        return preview.status == "ready" and now_ms < preview.expires_at_ms

    def _freeze_new_publication_source(
        self,
        *,
        actor_id: str,
        epoch: int,
        operation_id: str,
        request_sha256: str,
        dedup_id: str,
        preview_id: str,
        source_snapshot_sha256: str,
        authored_snapshot_id: str | None = None,
        target_mode: str = "personal",
        target=None,
        personal_space_id: str | None = None,
    ):
        """Freeze a new operation's body outside the later write transaction."""

        connection = self._factory.open(read_only=True)
        try:
            with transaction(connection, TransactionMode.DEFERRED):
                actor = self._users.require(connection, actor_id)
                if actor.account_status != "active" or actor.scope_epoch != epoch:
                    raise PublicationPreviewAuthorizationError(
                        "Current canonical actor state does not authorize publication."
                    )
                existing = connection.execute(
                    """SELECT actor_user_id,operation_type,operation_id,
                              request_sha256,status
                         FROM operation_dedup WHERE id=?""",
                    (dedup_id,),
                ).fetchone()
                if existing is not None:
                    exact = (
                        existing["actor_user_id"] == actor_id
                        and existing["operation_type"] == "publication"
                        and existing["operation_id"] == operation_id
                        and existing["request_sha256"] == request_sha256
                    )
                    if exact and existing["status"] == "committed":
                        return None
                    raise PublicationPreviewIdempotencyError(
                        "The publication operation identity is already in use."
                    )
                preview = self._previews.require_for_actor(
                    connection,
                    preview_id=preview_id,
                    actor_user_id=actor_id,
                )
                selection = None
                if self._wiki_publication is not None:
                    selection = self._wiki_publication.select(connection, preview)
                    if selection is not None:
                        selected = selection.require_publishable()
                        if authored_snapshot_id is not None and authored_snapshot_id != selected:
                            raise PublicationPreviewValidationError("The internal Wiki selection differs from the server-selected source.")
                        authored_snapshot_id = selected
                if (
                    preview.status != "ready"
                    or preview.extraction_snapshot_id is None
                    or preview.source_snapshot_sha256 != source_snapshot_sha256
                    or preview.captured_scope_epoch != epoch
                    or (authored_snapshot_id is not None and target_mode not in preview.available_target_modes)
                    or not self._publication_preview_current(connection, preview, db_now_ms(connection))
                ):
                    raise PublicationPreviewAuthorizationError(
                        "The preview is not currently eligible for this publication."
                    )
                authored_options = {}
                if (authored_snapshot_id is not None or getattr(selection, "preserved_snapshot_id", None) is not None
                        or getattr(selection, "native_snapshot_id", None) is not None
                        or getattr(selection, "pptx_role_snapshot_id", None) is not None
                        or getattr(selection, "pdf_snapshot_id", None) is not None
                        or getattr(selection, "pdf_visual_snapshot_id", None) is not None
                        or getattr(selection, "office_hybrid_snapshot_id", None) is not None
                        or getattr(selection, "pdf_continuation_snapshot_id", None) is not None):
                    authored_options = {"authored_snapshot_id": authored_snapshot_id,
                        "authorized_targets": self._authored_preparation_targets(connection,
                            actor_id=actor_id, epoch=epoch, preview=preview, target_mode=target_mode,
                            target=target, personal_space_id=personal_space_id)}
                return self._commits.freeze_source(
                    connection,
                    actor_user_id=actor_id,
                    preview_id=preview_id,
                    artifact_id=preview.artifact_id,
                    extraction_snapshot_id=preview.extraction_snapshot_id,
                    source_snapshot_sha256=source_snapshot_sha256,
                    effective_at_ms=db_now_ms(connection),
                    **authored_options,
                )
        except PublicationCommitConflictError as exc:
            raise PublicationPreviewAuthorizationError(str(exc)) from exc
        except PublicationCommitIntegrityError as exc:
            raise PublicationPreviewServiceError(str(exc)) from exc
        finally:
            connection.close()

    def _resolve_explicit_reference_contexts(
        self,
        connection: ManagedConnection,
        *,
        actor_id: str,
        epoch: int,
        operation_id: str,
        source_target: PublicationTargetResult,
        references: tuple[ParsedExplicitReference, ...],
    ) -> list[dict[str, object]]:
        if not references:
            return []
        try:
            resolved = self._scope.resolve_in_transaction(
                connection,
                SessionPrincipal(user_id=actor_id, captured_scope_epoch=epoch),
                source_target.target_space_id,
                Capability.READ,
            )
        except ScopeResolutionError as exc:
            raise PublicationPreviewAuthorizationError(
                "An explicit reference is not available in the publication closure."
            ) from exc
        plan = resolved.access_plan
        if plan is None:
            raise PublicationPreviewAuthorizationError(
                "An explicit reference is not available in the publication closure."
            )
        source_ids = {
            descriptor.source_space_id
            for descriptor in plan.corpus_closure.sources
            if Capability.READ in descriptor.capabilities
        }
        overlay_ids = {
            (overlay.source_space_id, overlay.source_item_id, overlay.source_revision_id)
            for overlay in plan.corpus_closure.distribution_overlays
            if Capability.READ in overlay.capabilities
        }
        source_space = connection.execute(
            """SELECT space_kind,owner_org_unit_id
                 FROM knowledge_space WHERE id=? AND status='active'""",
            (source_target.target_space_id,),
        ).fetchone()
        if source_space is None:
            raise PublicationPreviewAuthorizationError(
                "An explicit reference is not available in the publication closure."
            )
        contexts: list[dict[str, object]] = []
        for reference in references:
            target = connection.execute(
                """SELECT i.space_id,i.current_revision_id,i.row_version,
                          i.lifecycle_status,i.curation_status,
                          s.owner_org_unit_id
                     FROM knowledge_item i JOIN knowledge_space s ON s.id=i.space_id
                    WHERE i.id=? AND s.status='active'""",
                (reference.target_item_id,),
            ).fetchone()
            if (
                target is None
                or reference.target_item_id == source_target.item_id
                or target["lifecycle_status"] != "active"
                or target["curation_status"] != "published"
                or target["current_revision_id"] is None
            ):
                raise PublicationPreviewAuthorizationError(
                    "An explicit reference is not available in the publication closure."
                )
            identity = (
                str(target["space_id"]),
                reference.target_item_id,
                str(target["current_revision_id"]),
            )
            if identity[0] not in source_ids and identity not in overlay_ids:
                raise PublicationPreviewAuthorizationError(
                    "An explicit reference is not available in the publication closure."
                )
            compatible = identity[0] == source_target.target_space_id
            if not compatible and source_space["owner_org_unit_id"] is not None:
                source_kind = str(source_space["space_kind"])
                if source_kind in {"team", "responsibility"}:
                    compatible = connection.execute(
                        """WITH RECURSIVE descendants(id) AS (
                             SELECT ? UNION ALL
                             SELECT o.id FROM org_unit o
                             JOIN descendants d ON o.parent_id=d.id
                           ) SELECT 1 FROM descendants WHERE id=? LIMIT 1""",
                        (
                            str(source_space["owner_org_unit_id"]),
                            target["owner_org_unit_id"],
                        ),
                    ).fetchone() is not None
                elif source_kind == "part":
                    compatible = connection.execute(
                        """WITH RECURSIVE ancestors(id) AS (
                             SELECT ? UNION ALL
                             SELECT o.parent_id FROM org_unit o
                             JOIN ancestors a ON o.id=a.id
                             WHERE o.parent_id IS NOT NULL
                           ) SELECT 1 FROM ancestors WHERE id=? LIMIT 1""",
                        (
                            str(source_space["owner_org_unit_id"]),
                            target["owner_org_unit_id"],
                        ),
                    ).fetchone() is not None
            if not compatible:
                raise PublicationPreviewAuthorizationError(
                    "An explicit reference is not available in the publication closure."
                )
            contexts.append(
                {
                    "actor_row_version": plan.actor_row_version,
                    "actor_scope_epoch": epoch,
                    "actor_user_id": actor_id,
                    "closure_generation": plan.corpus_closure.generation,
                    "closure_proof_sha256": plan.corpus_closure.sha256,
                    "operation_id": operation_id,
                    "ordinal": reference.ordinal,
                    "organization_snapshot_version": str(
                        plan.corpus_closure.organization_snapshot_version
                    ),
                    "source_space_id": source_target.target_space_id,
                    "target_item_id": reference.target_item_id,
                    "target_item_row_version": int(target["row_version"]),
                    "target_revision_id": str(target["current_revision_id"]),
                    "target_space_id": str(target["space_id"]),
                }
            )
        return contexts

    def publish(self, command: PublishPreviewCommand) -> PublicationResult:
        if not isinstance(command, PublishPreviewCommand):
            raise TypeError("A PublishPreviewCommand is required.")
        request_id = _preview_uuid(command.request_id, field_name="request_id")
        operation_id = _preview_uuid(
            command.operation_id, field_name="operation_id"
        )
        actor_id = _preview_user(command.actor_user_id)
        epoch = _preview_epoch(command.actor_scope_epoch)
        preview_id = _preview_uuid(command.preview_id, field_name="preview_id")
        expected_source_snapshot = _optional_snapshot_sha256(
            command.source_snapshot_sha256
        )
        if expected_source_snapshot is None:
            raise PublicationPreviewValidationError(
                "source_snapshot_sha256 is required."
            )
        if command.target_mode not in {"personal", "part", "both", "explicit"}:
            raise PublicationPreviewValidationError("target_mode is invalid.")
        target_mode = command.target_mode
        target = _publication_target(command.target)
        personal_space_id = (
            None
            if command.personal_space_id is None
            else _preview_uuid(
                command.personal_space_id, field_name="personal_space_id"
            )
        )
        if (target_mode == "explicit") != (target is not None):
            raise PublicationPreviewValidationError(
                "target is required exactly when target_mode is explicit."
            )
        if personal_space_id is not None and target_mode not in {"personal", "both"}:
            raise PublicationPreviewValidationError(
                "personal_space_id is valid only for personal or both publication."
            )
        request_meta = _preview_request_meta(command.request_meta)
        authored_snapshot_id = (None if command.authored_snapshot_id is None else
            _preview_uuid(command.authored_snapshot_id, field_name="authored_snapshot_id"))
        request_sha = self._request_sha256(
            actor_id=actor_id,
            epoch=epoch,
            operation_id=operation_id,
            preview_id=preview_id,
            source_snapshot_sha256=expected_source_snapshot,
            target_mode=target_mode,
            target=target,
            personal_space_id=personal_space_id,
            authored_snapshot_id=authored_snapshot_id,
        )
        dedup_id = _derived_uuid(
            _PUBLISH_DEDUP_NAMESPACE, actor_id, operation_id
        )
        frozen_source = self._freeze_new_publication_source(
            actor_id=actor_id,
            epoch=epoch,
            operation_id=operation_id,
            request_sha256=request_sha,
            dedup_id=dedup_id,
            preview_id=preview_id,
            source_snapshot_sha256=expected_source_snapshot,
            authored_snapshot_id=authored_snapshot_id,
            target_mode=target_mode,
            target=target,
            personal_space_id=personal_space_id,
        )
        if frozen_source is not None:
            self._authorize_current_source_policy(
                actor_id=actor_id, preview_id=preview_id,
                source_snapshot_sha256=expected_source_snapshot,
            )
            self._authorize_protected_publication(
                actor_id=actor_id, preview_id=preview_id,
                source_snapshot_sha256=expected_source_snapshot,
            )
        try:
            explicit_references = (
                ()
                if frozen_source is None
                else parse_explicit_item_references(
                    frozen_source.reference_source_md
                )
            )
        except ExplicitReferenceSyntaxError as exc:
            raise PublicationPreviewValidationError(str(exc)) from exc
        connection = self._factory.open()
        result: PublicationResult | None = None
        try:
            with transaction(connection):
                actor = self._users.require(connection, actor_id)
                if actor.account_status != "active" or actor.scope_epoch != epoch:
                    raise PublicationPreviewAuthorizationError(
                        "Current canonical actor state does not authorize publication."
                    )
                now_ms = db_now_ms(connection)
                try:
                    begun = self._users.begin_operation(
                        connection,
                        actor_user_id=actor_id,
                        operation_type="publication",
                        operation_id=operation_id,
                        request_sha256=request_sha,
                        dedup_id=dedup_id,
                        now_ms=now_ms,
                    )
                except OperationIdempotencyConflictError as exc:
                    raise PublicationPreviewIdempotencyError(str(exc)) from exc
                if not begun.inserted:
                    if begun.operation.status != "committed":
                        raise PublicationPreviewIdempotencyError(
                            "The publication operation is already in progress."
                        )
                    stored = _stored_publication_result(begun.operation.result)
                    replay_options = self._wiki_publication.replay_options(connection, actor_id=actor_id,
                        operation_id=operation_id, stored=stored, requested_authored_snapshot_id=authored_snapshot_id)
                    exact = self._commits.require_exact_replay(
                        connection,
                        actor_user_id=actor_id,
                        operation_id=operation_id,
                        dedup_id=dedup_id,
                        request_sha256=request_sha,
                        preview_id=preview_id,
                        **replay_options,
                    )
                    expected = _publication_result(
                        exact,
                        request_id=stored.request_id,
                        operation_id=operation_id,
                        target=target,
                        body_source=stored.body_source,
                    )
                    if stored != expected:
                        raise PublicationPreviewIdempotencyError(
                            "The stored publication result is inconsistent."
                        )
                    return stored

                preview = self._previews.require_for_actor(
                    connection,
                    preview_id=preview_id,
                    actor_user_id=actor_id,
                )
                if (
                    preview.status != "ready"
                    or preview.extraction_snapshot_id is None
                    or preview.source_snapshot_sha256 is None
                    or preview.source_snapshot_sha256 != expected_source_snapshot
                    or preview.captured_scope_epoch != epoch
                    or not self._publication_preview_current(connection, preview, now_ms)
                    or target_mode not in preview.available_target_modes
                ):
                    raise PublicationPreviewAuthorizationError(
                        "The preview is not currently eligible for this publication."
                    )
                higher_authority: HigherPublicationPreviewAuthorityRecord | None = None
                if target_mode == "explicit" and target is not None:
                    higher_authority = self._authority.get_preview(
                        connection, preview_id
                    )
                    if (
                        higher_authority is None
                        or higher_authority.actor_user_id != actor_id
                        or higher_authority.descriptor != target
                    ):
                        raise PublicationPreviewAuthorizationError(
                            "The preview is not bound to the explicit target."
                        )
                    try:
                        resolved = self._scope.resolve_in_transaction(
                            connection,
                            SessionPrincipal(
                                user_id=actor_id,
                                captured_scope_epoch=epoch,
                            ),
                            target.space_id,
                            Capability.PUBLISH,
                        )
                    except ScopeResolutionError as exc:
                        raise PublicationPreviewAuthorizationError(
                            "Current canonical scope no longer authorizes the "
                            "explicit publication target."
                        ) from exc
                    plan = resolved.access_plan
                    if (
                        plan is None
                        or resolved.space.kind != target.kind
                        or plan.native_mutation_space_id != target.space_id
                        or plan.role_assignment_id != target.role_assignment_id
                        or plan.role_assignment_generation
                        != target.role_assignment_generation
                        or plan.actor_row_version
                        != higher_authority.actor_row_version
                        or plan.corpus_closure.organization_snapshot_version
                        != higher_authority.organization_snapshot_version
                    ):
                        raise PublicationPreviewAuthorizationError(
                            "The explicit publication authority fence is stale."
                        )
                    targets = (
                        PublicationCommitTarget(target.space_id, target.kind),
                    )
                else:
                    try:
                        selectable = self._scope.list_selectable_in_transaction(
                            connection,
                            SessionPrincipal(
                                user_id=actor_id, captured_scope_epoch=epoch
                            ),
                        )
                    except ScopeResolutionError as exc:
                        raise PublicationPreviewAuthorizationError(
                            "Current canonical scope does not authorize publication."
                        ) from exc
                    personal = next(
                        (
                            item
                            for item in selectable.items
                            if item.kind == "personal"
                            and Capability.PUBLISH in item.capabilities
                        ),
                        None,
                    )
                    personal_target_id = None if personal is None else personal.id
                    if personal_space_id is not None:
                        try:
                            selected_personal = self._scope.resolve_in_transaction(
                                connection,
                                SessionPrincipal(
                                    user_id=actor_id,
                                    captured_scope_epoch=epoch,
                                ),
                                personal_space_id,
                                Capability.PUBLISH,
                            )
                        except ScopeResolutionError as exc:
                            raise PublicationPreviewAuthorizationError(
                                "The selected personal Wiki is not currently authorized."
                            ) from exc
                        if selected_personal.space.kind != "personal":
                            raise PublicationPreviewAuthorizationError(
                                "The selected personal Wiki is not currently authorized."
                            )
                        personal_target_id = selected_personal.space.id
                    part = next(
                        (
                            item
                            for item in selectable.items
                            if item.kind == "part"
                            and Capability.PUBLISH in item.capabilities
                        ),
                        None,
                    )
                    if personal_target_id is None:
                        raise PublicationPreviewAuthorizationError(
                            "Personal publication is not currently authorized."
                        )
                    if target_mode in {"part", "both"} and (
                        part is None
                        or actor.current_part_id is None
                        or preview.captured_part_id != actor.current_part_id
                    ):
                        raise PublicationPreviewAuthorizationError(
                            "The captured part no longer authorizes publication."
                        )
                    targets = (
                        (PublicationCommitTarget(personal_target_id, "personal"),)
                        if target_mode == "personal"
                        else (
                            (PublicationCommitTarget(part.id, "part"),)
                            if target_mode == "part" and part is not None
                            else (
                                PublicationCommitTarget(personal_target_id, "personal"),
                                PublicationCommitTarget(part.id, "part"),  # type: ignore[union-attr]
                            )
                        )
                    )
                try:
                    committed = self._commits.commit(
                        connection,
                        PublicationCommitDraft(
                            dedup_id=dedup_id,
                            actor_user_id=actor_id,
                            operation_id=operation_id,
                            request_sha256=request_sha,
                            preview_id=preview_id,
                            artifact_id=preview.artifact_id,
                            extraction_snapshot_id=preview.extraction_snapshot_id,
                            source_snapshot_sha256=preview.source_snapshot_sha256,
                            expected_preview_row_version=preview.row_version,
                            target_mode=target_mode,
                            targets=targets,
                            created_at_ms=now_ms,
                            higher_authority=higher_authority,
                            frozen_source=frozen_source,
                        ),
                        failpoint=self._hit,
                    )
                except PublicationUpdateProposalRequiredError as exc:
                    raise PublicationPreviewValidationError(str(exc)) from exc
                except PublicationCommitConflictError as exc:
                    raise PublicationPreviewIdempotencyError(str(exc)) from exc
                except PublicationCommitIntegrityError as exc:
                    raise PublicationPreviewServiceError(str(exc)) from exc

                for commit_target in committed.targets:
                    self._audit.append(
                        connection,
                        AuditEventDraft(
                            event_uuid=_derived_uuid(
                                _PUBLISH_AUDIT_NAMESPACE,
                                actor_id,
                                operation_id,
                                commit_target.target_space_id,
                            ),
                            occurred_at_ms=now_ms,
                            request_id=request_id,
                            operation_id=operation_id,
                            action=f"publication.{commit_target.outcome}",
                            outcome=commit_target.outcome,
                            actor_kind="user",
                            actor_user_id=actor.id,
                            actor_display_name=actor.display_name,
                            actor_role_snapshot=actor.system_role,
                            space_id=commit_target.target_space_id,
                            item_id=commit_target.item_id,
                            base_revision_id=(
                                None
                                if commit_target.outcome == "created"
                                else commit_target.resolved_revision_id
                            ),
                            new_revision_id=commit_target.created_revision_id,
                            details={
                                "finding_id": commit_target.curation_finding_id,
                                "proposal_id": commit_target.change_proposal_id,
                                "deterministic_rule_version": preview.rule_version,
                                "lineage_group_id": committed.lineage_group_id,
                                "preview_id": preview_id,
                                "publication_operation_id": (
                                    committed.publication_operation_id
                                ),
                                "schema_version": 1,
                                "source_artifact_id": committed.source_artifact_id,
                                "source_snapshot_sha256": (
                                    preview.source_snapshot_sha256
                                ),
                                "target_mode": target_mode,
                            },
                            request_meta=request_meta,
                        ),
                    )
                self._hit("publication.after_audit")
                for commit_target in committed.targets:
                    if commit_target.outcome == "created":
                        self._outbox.append(
                            connection,
                            OutboxEventDraft(
                                logical_key=(
                                    f"publication:{committed.publication_operation_id}:"
                                    f"{commit_target.target_space_id}:created"
                                ),
                                space_id=commit_target.target_space_id,
                                aggregate_type="knowledge_item",
                                aggregate_id=commit_target.item_id,
                                aggregate_version=1,
                                event_type="knowledge.item.provisional.created",
                                payload={
                                    "item_id": commit_target.item_id,
                                    "deterministic_rule_version": (
                                        preview.rule_version
                                    ),
                                    "preview_id": preview_id,
                                    "publication_operation_id": (
                                        committed.publication_operation_id
                                    ),
                                    "revision_id": commit_target.resolved_revision_id,
                                    "schema_version": 1,
                                    "space_id": commit_target.target_space_id,
                                },
                                available_at_ms=now_ms,
                            ),
                        )
                    elif commit_target.outcome == "update_proposed":
                        if (
                            commit_target.curation_finding_id is None
                            or commit_target.change_proposal_id is None
                        ):
                            raise PublicationPreviewServiceError(
                                "The update proposal finding is unavailable."
                            )
                        self._outbox.append(
                            connection,
                            OutboxEventDraft(
                                logical_key=(
                                    f"publication:{committed.publication_operation_id}:"
                                    f"{commit_target.target_space_id}:update-proposed"
                                ),
                                space_id=commit_target.target_space_id,
                                aggregate_type="curation_finding",
                                aggregate_id=commit_target.curation_finding_id,
                                aggregate_version=1,
                                event_type="curation.finding.pending",
                                payload={
                                    "deterministic_rule_version": (
                                        preview.rule_version
                                    ),
                                    "finding_id": commit_target.curation_finding_id,
                                    "item_id": commit_target.item_id,
                                    "preview_id": preview_id,
                                    "publication_operation_id": (
                                        committed.publication_operation_id
                                    ),
                                    "proposal_id": commit_target.change_proposal_id,
                                    "revision_id": (
                                        commit_target.resolved_revision_id
                                    ),
                                    "schema_version": 1,
                                    "space_id": commit_target.target_space_id,
                                },
                                available_at_ms=now_ms,
                            ),
                        )
                self._outbox.append(
                    connection,
                    OutboxEventDraft(
                        logical_key=(
                            f"publication:{committed.publication_operation_id}:committed"
                        ),
                        space_id=(
                            committed.targets[0].target_space_id
                            if len(committed.targets) == 1
                            else None
                        ),
                        aggregate_type="publication_operation",
                        aggregate_id=committed.publication_operation_id,
                        aggregate_version=1,
                        event_type="publication.committed",
                        payload={
                            "preview_id": preview_id,
                            "deterministic_rule_version": preview.rule_version,
                            "publication_operation_id": (
                                committed.publication_operation_id
                            ),
                            "schema_version": 1,
                            "source_artifact_id": committed.source_artifact_id,
                            "target_mode": target_mode,
                            "targets": [
                                target.to_mapping() for target in committed.targets
                            ],
                        },
                        available_at_ms=now_ms,
                    ),
                )
                for commit_target in committed.targets:
                    if commit_target.outcome != "created":
                        continue
                    curation_job_id = _derived_uuid(
                        _CURATION_DECISION_JOB_NAMESPACE,
                        commit_target.item_id,
                        commit_target.resolved_revision_id,
                    )
                    reference_contexts = self._resolve_explicit_reference_contexts(
                        connection,
                        actor_id=actor_id,
                        epoch=epoch,
                        operation_id=operation_id,
                        source_target=commit_target,
                        references=explicit_references,
                    )
                    self._jobs.enqueue(
                        connection,
                        JobEnqueueDraft(
                            job_id=curation_job_id,
                            dedup_id=None,
                            operation_id=None,
                            actor_kind="system",
                            actor_user_id=None,
                            space_id=commit_target.target_space_id,
                            job_type="curation.rescan",
                            handler_version="v1",
                            effect_key=(
                                f"curation-decision:{commit_target.item_id}:"
                                f"{commit_target.resolved_revision_id}"
                            ),
                            input_ref={
                                "item_id": commit_target.item_id,
                                "revision_id": commit_target.resolved_revision_id,
                                "rule_version": "curation-rescan-v1",
                            },
                            expected_state={
                                "actor_scope_epoch": epoch,
                                "actor_user_id": actor_id,
                                "operation_id": operation_id,
                                "explicit_references": reference_contexts,
                                "publication_operation_id": (
                                    committed.publication_operation_id
                                ),
                                "schema_version": 1,
                                "source_snapshot_sha256": (
                                    preview.source_snapshot_sha256
                                ),
                            },
                            authorization_snapshot={
                                "actor_kind": "system",
                                "schema_version": 1,
                            },
                            authorization_scope_epoch=None,
                            authorization_part_id=None,
                            authorization_capabilities=(),
                            max_attempts=8,
                            cancellable=False,
                            available_at_ms=now_ms,
                            created_at_ms=None,
                        ),
                    )
                self._hit("publication.after_outbox")
                result = _publication_result(
                    committed,
                    request_id=request_id,
                    operation_id=operation_id,
                    target=target,
                    body_source=("pdf_continuation_wiki" if committed.pdf_continuation_snapshot_id is not None else
                        "office_hybrid_wiki" if committed.office_hybrid_snapshot_id is not None else
                        "pdf_visual_wiki" if committed.pdf_visual_snapshot_id is not None else
                        "pdf_wiki" if committed.pdf_snapshot_id is not None else
                        "pptx_role_wiki" if committed.pptx_role_snapshot_id is not None else
                        "native_wiki" if committed.native_snapshot_id is not None else
                        "preserved_wiki" if committed.preserved_snapshot_id is not None else
                        "authored_wiki" if committed.authored_snapshot_id is not None else "extraction")
                        if self._wiki_publication.has_request(connection, preview_id)
                        else None,
                )
                self._users.commit_operation(
                    connection,
                    dedup_id=dedup_id,
                    result=result.to_mapping(),
                    http_status=200,
                    completed_at_ms=now_ms,
                )
                self._hit("publication.before_commit")
        except (OperationIdempotencyConflictError, OperationInProgressError) as exc:
            raise PublicationPreviewIdempotencyError(str(exc)) from exc
        finally:
            connection.close()
        if result is None:
            raise PublicationPreviewServiceError(
                "The publication result is unavailable."
            )
        self._hit("publication.after_commit_before_response")
        return result


class PublicationApiService:
    """One explicit P4.5 facade over preview and commit service boundaries.

    The facade owns no state or transaction.  It exists so a canonical route
    composition cannot accidentally bind only preview creation or only commit
    publication while presenting the complete v1 publication surface.
    """

    __slots__ = ("_commits", "_previews")

    def __init__(
        self,
        previews: PublicationPreviewService,
        commits: PublicationService,
    ) -> None:
        if not isinstance(previews, PublicationPreviewService):
            raise TypeError("previews must be PublicationPreviewService.")
        if not isinstance(commits, PublicationService):
            raise TypeError("commits must be PublicationService.")
        self._previews = previews
        self._commits = commits

    def create_manual_preview(
        self, command: CreateManualPreviewCommand
    ) -> PublicationPreviewRecord:
        return self._previews.create_manual_preview(command)

    def create_upload_preview(
        self, command: CreateUploadPreviewCommand
    ) -> QueuedPublicationPreviewResult:
        return self._previews.create_upload_preview(command)

    def create_chat_preview(
        self, command: CreateChatPreviewCommand
    ) -> PublicationPreviewRecord:
        return self._previews.create_chat_preview(command)

    def create_notion_preview(
        self, command: CreateNotionPreviewCommand
    ) -> PublicationPreviewRecord:
        return self._previews.create_notion_preview(command)

    def get_preview(self, query: GetPublicationPreviewQuery) -> dict[str, object]:
        return self._previews.get_preview(query)

    def status_read_coordinator(self, principal_resolver, *, server_read_binding=None):
        """Explicitly opt status GETs into a request-owned two-phase RO read."""
        from ..publication_status_read import PublicationStatusReadCoordinator
        return PublicationStatusReadCoordinator(self._previews, self, principal_resolver,
            server_read_binding=server_read_binding)

    def cancel_preview(
        self, command: CancelPublicationPreviewCommand
    ) -> PublicationPreviewRecord:
        return self._previews.cancel_preview(command)

    def publish(self, command: PublishPreviewCommand) -> PublicationResult:
        return self._commits.publish(command)

