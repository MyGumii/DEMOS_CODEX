"""Authorized listing and verified download of original Wiki source files."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Callable

from ..db import ConnectionFactory, TransactionMode, transaction
from ..errors import KnowledgeCoreError
from ..protected_sources import (
    PolicyProtectedSourceClassifier, classify_protected_for_actor,
)
from ..repositories.management_reads import ManagementReadRepository
from ..repositories.source_artifacts import (
    ItemSourceArtifactRecord,
    SourceArtifactRepository,
)
from ..scope import Capability, ScopeResolver, SessionPrincipal


_USER_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$", re.ASCII)
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.ASCII,
)


class SourceArtifactServiceError(KnowledgeCoreError):
    code = "INTERNAL_ERROR"
    http_status = 500
    retryable = False


class SourceArtifactNotFoundError(SourceArtifactServiceError):
    code = "NOT_FOUND"
    http_status = 404


class SourceArtifactStateConflictError(SourceArtifactServiceError):
    code = "STATE_CONFLICT"
    http_status = 409
    retryable = True


class ProtectedOriginalDownloadForbiddenError(SourceArtifactServiceError):
    code = "FORBIDDEN"
    http_status = 403


@dataclass(frozen=True, slots=True)
class ItemSourceArtifactRequest:
    principal: SessionPrincipal
    selected_space_id: str
    item_id: str


@dataclass(frozen=True, slots=True)
class ItemSourceArtifactDownloadRequest:
    principal: SessionPrincipal
    selected_space_id: str
    item_id: str
    artifact_id: str


@dataclass(frozen=True, slots=True)
class ItemSourceArtifactPage:
    selected_space_id: str
    item_id: str
    corpus_closure_sha256: str
    items: tuple[ItemSourceArtifactRecord, ...]
    download_permitted_artifact_ids: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class ItemSourceArtifactDownload:
    selected_space_id: str
    item_id: str
    artifact_id: str
    original_filename: str
    media_type: str
    size_bytes: int
    content_sha256: str
    content: bytes


@dataclass(frozen=True, slots=True)
class ProtectedOriginalDownloadEvidence:
    actor_user_id: str
    selected_space_id: str
    item_id: str
    source_revision_id: str
    artifact_id: str
    original_sha256: str
    source_kind: str
    media_type: str


def _principal(value: object) -> SessionPrincipal:
    if not isinstance(value, SessionPrincipal):
        raise ValueError("A canonical session principal is required.")
    if not isinstance(value.user_id, str) or _USER_RE.fullmatch(value.user_id) is None:
        raise ValueError("The session user ID is invalid.")
    if (
        isinstance(value.captured_scope_epoch, bool)
        or not isinstance(value.captured_scope_epoch, int)
        or value.captured_scope_epoch < 1
    ):
        raise ValueError("The captured scope epoch is invalid.")
    return value


def _uuid(value: object, field: str) -> str:
    if not isinstance(value, str) or _UUID_RE.fullmatch(value) is None:
        raise ValueError(f"{field} must be a canonical lowercase UUID.")
    return value


class SourceArtifactService:
    """Keep authorization in SQLite and immutable byte verification outside it."""

    __slots__ = ("_artifacts", "_factory", "_management", "_policy_protection_classifier", "_protected_download_authorizer", "_scopes", "_stager")

    def __init__(
        self,
        factory: ConnectionFactory,
        *,
        scope_resolver: ScopeResolver,
        stager: object,
        management_reads: ManagementReadRepository | None = None,
        source_artifacts: SourceArtifactRepository | None = None,
        protected_download_authorizer: Callable[[ProtectedOriginalDownloadEvidence], bool] | None = None,
        policy_protection_classifier: PolicyProtectedSourceClassifier | None = None,
    ) -> None:
        if not isinstance(factory, ConnectionFactory):
            raise TypeError("SourceArtifactService requires a ConnectionFactory.")
        if not isinstance(scope_resolver, ScopeResolver):
            raise TypeError("SourceArtifactService requires a ScopeResolver.")
        if not callable(getattr(stager, "verify_existing", None)):
            raise TypeError("stager must provide verify_existing().")
        if protected_download_authorizer is not None and not callable(protected_download_authorizer):
            raise TypeError("protected_download_authorizer must be callable.")
        if (policy_protection_classifier is not None
                and not callable(getattr(policy_protection_classifier, "classify_for_actor", None))):
            raise TypeError("policy_protection_classifier must supply classify_for_actor().")
        self._factory = factory
        self._scopes = scope_resolver
        self._stager = stager
        self._management = management_reads or ManagementReadRepository()
        self._artifacts = source_artifacts or SourceArtifactRepository()
        self._protected_download_authorizer = protected_download_authorizer
        self._policy_protection_classifier = policy_protection_classifier

    def _authorize_protected_original(
        self,
        *,
        principal: SessionPrincipal,
        selected_space_id: str,
        artifact: ItemSourceArtifactRecord,
    ) -> None:
        if artifact.protected_source_kind is None:
            return
        authorizer = self._protected_download_authorizer
        if authorizer is None:
            raise ProtectedOriginalDownloadForbiddenError(
                "The protected original source cannot be downloaded."
            )
        evidence = ProtectedOriginalDownloadEvidence(
            actor_user_id=principal.user_id,
            selected_space_id=selected_space_id,
            item_id=artifact.item_id,
            source_revision_id=artifact.source_revision_id,
            artifact_id=artifact.artifact_id,
            original_sha256=artifact.content_sha256,
            source_kind=artifact.protected_source_kind,
            media_type=artifact.media_type,
        )
        try:
            allowed = authorizer(evidence)
        except Exception:
            allowed = False
        if allowed is not True:
            raise ProtectedOriginalDownloadForbiddenError(
                "The protected original source cannot be downloaded."
            )

    def _snapshot(
        self,
        *,
        principal: SessionPrincipal,
        selected_space_id: str,
        item_id: str,
    ) -> ItemSourceArtifactPage:
        connection = self._factory.open(read_only=True)
        try:
            with transaction(connection, TransactionMode.DEFERRED):
                scope = self._scopes.resolve_in_transaction(
                    connection, principal, selected_space_id, Capability.READ
                )
                plan = scope.access_plan
                if plan is None:
                    raise SourceArtifactServiceError(
                        "The canonical access plan is unavailable."
                    )
                closure = plan.corpus_closure
                authorization = self._management.resolve_item_authorization(
                    connection,
                    selected_space_id=plan.selected_view.id,
                    item_id=item_id,
                    source_space_ids=tuple(
                        source.source_space_id for source in closure.sources
                    ),
                    overlays=tuple(
                        {
                            "grant_id": overlay.grant_id,
                            "source_space_id": overlay.source_space_id,
                            "source_item_id": overlay.source_item_id,
                            "source_revision_id": overlay.source_revision_id,
                        }
                        for overlay in closure.distribution_overlays
                    ),
                )
                if authorization is None:
                    raise SourceArtifactNotFoundError(
                        "The requested knowledge resource was not found."
                    )
                items = self._artifacts.list_for_authorized_item(
                    connection, item_id=item_id, authorization=authorization
                )
                return ItemSourceArtifactPage(
                    selected_space_id=plan.selected_view.id,
                    item_id=item_id,
                    corpus_closure_sha256=closure.sha256,
                    items=items,
                )
        finally:
            connection.close()

    def list_for_item(
        self, request: ItemSourceArtifactRequest
    ) -> ItemSourceArtifactPage:
        if not isinstance(request, ItemSourceArtifactRequest):
            raise TypeError("An ItemSourceArtifactRequest is required.")
        principal = _principal(request.principal)
        selected = _uuid(request.selected_space_id, "selected_space_id")
        item = _uuid(request.item_id, "item_id")
        page = self._snapshot(
            principal=principal, selected_space_id=selected, item_id=item
        )
        # List-time rights are advisory for the UI; download() rechecks the
        # same actor after reading and verifying the immutable original.
        permitted: set[str] = set()
        for artifact in page.items:
            if artifact.protected_source_kind is None:
                # An older link or ordinary source has no policy provenance.
                # Its bytes are classified only in download(), so the list
                # must not claim that a download has already been authorized.
                continue
            try:
                self._authorize_protected_original(
                    principal=principal, selected_space_id=selected,
                    artifact=artifact,
                )
            except ProtectedOriginalDownloadForbiddenError:
                continue
            permitted.add(artifact.artifact_id)
        return ItemSourceArtifactPage(
            selected_space_id=page.selected_space_id,
            item_id=page.item_id,
            corpus_closure_sha256=page.corpus_closure_sha256,
            items=page.items,
            download_permitted_artifact_ids=frozenset(permitted),
        )

    def download(
        self, request: ItemSourceArtifactDownloadRequest
    ) -> ItemSourceArtifactDownload:
        if not isinstance(request, ItemSourceArtifactDownloadRequest):
            raise TypeError("An ItemSourceArtifactDownloadRequest is required.")
        principal = _principal(request.principal)
        selected = _uuid(request.selected_space_id, "selected_space_id")
        item = _uuid(request.item_id, "item_id")
        artifact_id = _uuid(request.artifact_id, "artifact_id")
        initial = self._snapshot(
            principal=principal, selected_space_id=selected, item_id=item
        )
        artifact = next(
            (row for row in initial.items if row.artifact_id == artifact_id), None
        )
        if artifact is None:
            raise SourceArtifactNotFoundError(
                "The requested original source file was not found."
            )
        self._authorize_protected_original(
            principal=principal, selected_space_id=selected, artifact=artifact
        )
        try:
            observation, content = self._stager.verify_existing(
                expected_sha256=artifact.content_sha256,
                expected_size=artifact.size_bytes,
                media_type=artifact.media_type,
                normalized_content_sha256=None,
                normalizer_version=None,
            )
        except Exception as exc:
            raise SourceArtifactServiceError(
                "The original source file failed immutable-storage verification."
            ) from exc
        try:
            # Older original-only links may lack protected provenance. Recheck
            # both container and installed policy signals against the bytes
            # before any such original can leave the server.
            if artifact.protected_source_kind is None:
                try:
                    unrecorded_protection = classify_protected_for_actor(
                        artifact.original_filename, content,
                        actor_user_id=principal.user_id,
                        policy_classifier=self._policy_protection_classifier,
                    ) is not None
                except Exception:
                    unrecorded_protection = True
                if unrecorded_protection:
                    raise ProtectedOriginalDownloadForbiddenError(
                        "The protected original source cannot be downloaded."
                    )
            verified = self._snapshot(
                principal=principal, selected_space_id=selected, item_id=item
            )
            current = next(
                (row for row in verified.items if row.artifact_id == artifact_id),
                None,
            )
            if current != artifact:
                raise SourceArtifactStateConflictError(
                    "The original source authorization changed during download."
                )
            self._authorize_protected_original(
                principal=principal, selected_space_id=selected, artifact=current
            )
            if not isinstance(content, bytes) or len(content) != artifact.size_bytes:
                raise SourceArtifactServiceError(
                    "The verified original source bytes are invalid."
                )
            return ItemSourceArtifactDownload(
                selected_space_id=selected,
                item_id=item,
                artifact_id=artifact.artifact_id,
                original_filename=artifact.original_filename,
                media_type=artifact.media_type,
                size_bytes=artifact.size_bytes,
                content_sha256=artifact.content_sha256,
                content=content,
            )
        finally:
            lease = getattr(observation, "lease", None)
            close = getattr(lease, "close", None)
            if callable(close):
                close()


__all__ = [
    "ItemSourceArtifactDownload",
    "ItemSourceArtifactDownloadRequest",
    "ItemSourceArtifactPage",
    "ItemSourceArtifactRequest",
    "ProtectedOriginalDownloadEvidence",
    "ProtectedOriginalDownloadForbiddenError",
    "SourceArtifactNotFoundError",
    "SourceArtifactService",
    "SourceArtifactServiceError",
    "SourceArtifactStateConflictError",
]
