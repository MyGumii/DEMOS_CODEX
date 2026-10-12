"""Authorized listing and verified download of original Wiki source files."""

from __future__ import annotations

from dataclasses import dataclass
import re

from ..db import ConnectionFactory, TransactionMode, transaction
from ..errors import KnowledgeCoreError
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

    __slots__ = ("_artifacts", "_factory", "_management", "_scopes", "_stager")

    def __init__(
        self,
        factory: ConnectionFactory,
        *,
        scope_resolver: ScopeResolver,
        stager: object,
        management_reads: ManagementReadRepository | None = None,
        source_artifacts: SourceArtifactRepository | None = None,
    ) -> None:
        if not isinstance(factory, ConnectionFactory):
            raise TypeError("SourceArtifactService requires a ConnectionFactory.")
        if not isinstance(scope_resolver, ScopeResolver):
            raise TypeError("SourceArtifactService requires a ScopeResolver.")
        if not callable(getattr(stager, "verify_existing", None)):
            raise TypeError("stager must provide verify_existing().")
        self._factory = factory
        self._scopes = scope_resolver
        self._stager = stager
        self._management = management_reads or ManagementReadRepository()
        self._artifacts = source_artifacts or SourceArtifactRepository()

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
        return self._snapshot(
            principal=principal, selected_space_id=selected, item_id=item
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
    "SourceArtifactNotFoundError",
    "SourceArtifactService",
    "SourceArtifactServiceError",
    "SourceArtifactStateConflictError",
]
