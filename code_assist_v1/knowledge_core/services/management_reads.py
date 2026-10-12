"""Read-only, one-space management views for the disabled P3.7 API gate."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from difflib import SequenceMatcher
from hashlib import sha256
import json
import re

from ..db import ConnectionFactory, TransactionMode, db_now_ms, transaction
from ..repositories.item_history import (
    ItemHistoryIntegrityError,
    ItemHistoryRepository,
    ManagementItemDetailRecord,
    ManagementRevisionRecord,
    RevisionBodyReference,
)
from ..repositories.management_reads import (
    ManagementActivityRecord,
    ManagementFindingRecord,
    ManagementGraph,
    ManagementItemAuthorization,
    ManagementItemRecord,
    ManagementProjectionStatus,
    ManagementReadRepository,
)
from ..repositories.semantic_graph_contracts import (
    ExplicitReferenceRecord,
    SemanticGraphContractRepository,
)
from ..scope import (
    AccessPlan,
    Capability,
    ScopeNotFoundError,
    ScopeResolver,
    SessionPrincipal,
)


_USER_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$", re.ASCII)
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.ASCII,
)
_MAX_INT = 9_223_372_036_854_775_807
_MAX_DIFF_BODY_CHARS = 2_000_000
_MAX_DIFF_LINES = 20_000
_MAX_DIFF_OUTPUT_CHARS = 2_000_000


@dataclass(frozen=True, slots=True)
class SpaceManagementReadRequest:
    actor_user_id: str
    actor_scope_epoch: int
    space_id: str


@dataclass(frozen=True, slots=True)
class SpaceManagementPageRequest:
    actor_user_id: str
    actor_scope_epoch: int
    space_id: str
    before_updated_at_ms: int | None = None
    before_id: str | None = None
    limit: int = 50


@dataclass(frozen=True, slots=True)
class ManagementItemPage:
    space_id: str
    items: tuple[ManagementItemRecord, ...]
    has_more: bool
    corpus_closure_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class ManagementFindingPage:
    space_id: str
    items: tuple[ManagementFindingRecord, ...]
    has_more: bool
    corpus_closure_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class ManagementActivityPage:
    space_id: str
    items: tuple[ManagementActivityRecord, ...]
    has_more: bool
    corpus_closure_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class ItemManagementReadRequest:
    actor_user_id: str
    actor_scope_epoch: int
    space_id: str
    item_id: str


@dataclass(frozen=True, slots=True)
class ItemRevisionPageRequest:
    actor_user_id: str
    actor_scope_epoch: int
    space_id: str
    item_id: str
    before_revision_no: int | None = None
    before_revision_id: str | None = None
    limit: int = 50


@dataclass(frozen=True, slots=True)
class ItemRevisionReadRequest:
    actor_user_id: str
    actor_scope_epoch: int
    space_id: str
    item_id: str
    revision_id: str


@dataclass(frozen=True, slots=True)
class ItemRevisionCompareRequest:
    actor_user_id: str
    actor_scope_epoch: int
    space_id: str
    item_id: str
    from_revision_id: str
    to_revision_id: str


@dataclass(frozen=True, slots=True)
class ManagementItemDetail:
    record: ManagementItemDetailRecord
    markdown: str | None
    capabilities: tuple[str, ...]
    content_age_seconds: int
    review_age_seconds: int | None
    inactive_duration_seconds: int | None
    selected_view_space_id: str | None = None
    source_space_id: str | None = None
    inherited: bool = False
    origin: str = "native"
    overlay_grant_id: str | None = None
    corpus_closure_sha256: str | None = None
    references: tuple["ManagementExplicitReference", ...] = ()


@dataclass(frozen=True, slots=True)
class ManagementExplicitReferenceTarget:
    item_id: str
    observed_revision_id: str
    current_revision_id: str
    title: str
    source_space_id: str
    origin: str
    inherited: bool
    newer_revision_available: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "current_revision_id": self.current_revision_id,
            "inherited": self.inherited,
            "item_id": self.item_id,
            "newer_revision_available": self.newer_revision_available,
            "observed_revision_id": self.observed_revision_id,
            "origin": self.origin,
            "source_space_id": self.source_space_id,
            "title": self.title,
        }


@dataclass(frozen=True, slots=True)
class ManagementExplicitReference:
    id: str
    ordinal: int
    syntax_version: str
    availability_status: str
    unavailable_reason: str | None
    target: ManagementExplicitReferenceTarget | None

    def as_dict(self) -> dict[str, object]:
        return {
            "availability_status": self.availability_status,
            "id": self.id,
            "ordinal": self.ordinal,
            "syntax_version": self.syntax_version,
            "target": None if self.target is None else self.target.as_dict(),
            "unavailable_reason": self.unavailable_reason,
        }


@dataclass(frozen=True, slots=True)
class ManagementRevisionPage:
    space_id: str
    item_id: str
    items: tuple[ManagementRevisionRecord, ...]
    has_more: bool
    selected_view_space_id: str | None = None
    source_space_id: str | None = None
    inherited: bool = False
    origin: str = "native"
    overlay_grant_id: str | None = None
    corpus_closure_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class ManagementRevisionDetail:
    space_id: str
    revision: ManagementRevisionRecord
    markdown: str | None
    selected_view_space_id: str | None = None
    source_space_id: str | None = None
    inherited: bool = False
    origin: str = "native"
    overlay_grant_id: str | None = None
    corpus_closure_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class ManagementDiffHunk:
    from_start: int
    from_count: int
    to_start: int
    to_count: int
    lines: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "from_start": self.from_start,
            "from_count": self.from_count,
            "to_start": self.to_start,
            "to_count": self.to_count,
            "lines": list(self.lines),
        }


@dataclass(frozen=True, slots=True)
class ManagementRevisionComparison:
    space_id: str
    item_id: str
    from_revision_id: str
    to_revision_id: str
    diff_format: str
    hunks: tuple[ManagementDiffHunk, ...]
    diff_sha256: str
    selected_view_space_id: str | None = None
    source_space_id: str | None = None
    inherited: bool = False
    origin: str = "native"
    overlay_grant_id: str | None = None
    corpus_closure_sha256: str | None = None


class ManagementReadBodyUnavailableError(ItemHistoryIntegrityError):
    code = "REVISION_BODY_UNAVAILABLE"
    http_status = 409
    retryable = False


class ManagementReadStateConflictError(ItemHistoryIntegrityError):
    code = "STATE_CONFLICT"
    http_status = 409
    retryable = True


class ManagementDiffTooLargeError(ItemHistoryIntegrityError):
    code = "DIFF_TOO_LARGE"
    http_status = 413
    retryable = False


def _user(value: object) -> str:
    if not isinstance(value, str) or _USER_RE.fullmatch(value) is None:
        raise ValueError("actor_user_id is invalid.")
    return value


def _uuid(value: object, field: str) -> str:
    if not isinstance(value, str) or _UUID_RE.fullmatch(value) is None:
        raise ValueError(f"{field} must be a canonical lowercase UUID.")
    return value


def _positive(value: object, field: str, maximum: int = _MAX_INT) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 1 <= value <= maximum
    ):
        raise ValueError(f"{field} is invalid.")
    return value


def _cursor(
    before_updated_at_ms: object,
    before_id: object,
) -> tuple[int | None, str | None]:
    if (before_updated_at_ms is None) != (before_id is None):
        raise ValueError("Both cursor fields must be supplied together.")
    if before_updated_at_ms is None:
        return None, None
    if (
        isinstance(before_updated_at_ms, bool)
        or not isinstance(before_updated_at_ms, int)
        or not 0 <= before_updated_at_ms <= _MAX_INT
    ):
        raise ValueError("before_updated_at_ms is invalid.")
    return before_updated_at_ms, _uuid(before_id, "before_id")


def _age_seconds(now_ms: int, timestamp_ms: int, field: str) -> int:
    if timestamp_ms > now_ms:
        raise ItemHistoryIntegrityError(f"Stored {field} is in the future.")
    return (now_ms - timestamp_ms) // 1000


def _materialize_body(
    body: RevisionBodyReference | None,
    loader: Callable[[str, int], bytes | str] | None,
) -> str | None:
    if body is None:
        return None
    if body.content_md is not None:
        return body.content_md
    if body.content_blob_hash is None or loader is None:
        raise ManagementReadBodyUnavailableError(
            "Blob-backed revision content is not available to this reader."
        )
    try:
        payload = loader(body.content_blob_hash, body.content_size_bytes)
    except Exception as exc:
        raise ManagementReadBodyUnavailableError(
            "The revision content loader failed."
        ) from exc
    if isinstance(payload, str):
        markdown = payload
        encoded = payload.encode("utf-8")
    elif isinstance(payload, bytes):
        encoded = bytes(payload)
        try:
            markdown = encoded.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise ManagementReadBodyUnavailableError(
                "The revision content is not valid UTF-8."
            ) from exc
    else:
        raise ManagementReadBodyUnavailableError(
            "The revision content loader returned an invalid value."
        )
    if (
        len(encoded) != body.content_size_bytes
        or sha256(encoded).hexdigest() != body.content_sha256
        or body.content_blob_hash != body.content_sha256
    ):
        raise ManagementReadBodyUnavailableError(
            "The revision content does not match canonical metadata."
        )
    return markdown


def _diff_hunks(from_markdown: str, to_markdown: str) -> tuple[ManagementDiffHunk, ...]:
    if (
        len(from_markdown) > _MAX_DIFF_BODY_CHARS
        or len(to_markdown) > _MAX_DIFF_BODY_CHARS
    ):
        raise ManagementDiffTooLargeError("A revision is too large for safe comparison.")
    before = from_markdown.splitlines(keepends=True)
    after = to_markdown.splitlines(keepends=True)
    if len(before) > _MAX_DIFF_LINES or len(after) > _MAX_DIFF_LINES:
        raise ManagementDiffTooLargeError("The revision has too many lines to compare safely.")
    matcher = SequenceMatcher(None, before, after, autojunk=False)
    hunks: list[ManagementDiffHunk] = []
    output_chars = 0
    for group in matcher.get_grouped_opcodes(n=3):
        first = group[0]
        last = group[-1]
        lines: list[str] = []
        for tag, i1, i2, j1, j2 in group:
            if tag == "equal":
                lines.extend(f" {line}" for line in before[i1:i2])
            elif tag == "delete":
                lines.extend(f"-{line}" for line in before[i1:i2])
            elif tag == "insert":
                lines.extend(f"+{line}" for line in after[j1:j2])
            elif tag == "replace":
                lines.extend(f"-{line}" for line in before[i1:i2])
                lines.extend(f"+{line}" for line in after[j1:j2])
            else:  # pragma: no cover - guarded by difflib's public contract
                raise ItemHistoryIntegrityError("The diff engine returned an invalid opcode.")
        output_chars += sum(len(line) for line in lines)
        if output_chars > _MAX_DIFF_OUTPUT_CHARS:
            raise ManagementDiffTooLargeError("The safe diff output limit was exceeded.")
        hunks.append(
            ManagementDiffHunk(
                from_start=first[1] + 1,
                from_count=last[2] - first[1],
                to_start=first[3] + 1,
                to_count=last[4] - first[3],
                lines=tuple(lines),
            )
        )
    return tuple(hunks)


def _diff_sha256(hunks: tuple[ManagementDiffHunk, ...]) -> str:
    encoded = json.dumps(
        [hunk.as_dict() for hunk in hunks],
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def _closure_inputs(
    plan: AccessPlan,
) -> tuple[tuple[str, ...], tuple[dict[str, str], ...]]:
    return (
        tuple(source.source_space_id for source in plan.corpus_closure.sources),
        tuple(
            {
                "grant_id": overlay.grant_id,
                "source_space_id": overlay.source_space_id,
                "source_item_id": overlay.source_item_id,
                "source_revision_id": overlay.source_revision_id,
            }
            for overlay in plan.corpus_closure.distribution_overlays
        ),
    )


def _native_capabilities(plan: AccessPlan) -> tuple[str, ...]:
    return tuple(capability.value for capability in plan.selected_view.capabilities)


def _item_not_found() -> ScopeNotFoundError:
    return ScopeNotFoundError("The requested knowledge resource was not found.")


class CanonicalManagementReadService:
    """Resolve authority and read one canonical snapshot without side effects."""

    __slots__ = (
        "_content_loader",
        "_factory",
        "_history",
        "_reads",
        "_scopes",
        "_semantic",
    )

    def __init__(
        self,
        factory: ConnectionFactory,
        *,
        scope_resolver: ScopeResolver,
        reads: ManagementReadRepository | None = None,
        history: ItemHistoryRepository | None = None,
        semantic: SemanticGraphContractRepository | None = None,
        content_loader: Callable[[str, int], bytes | str] | None = None,
    ) -> None:
        if not isinstance(factory, ConnectionFactory):
            raise TypeError("CanonicalManagementReadService requires a ConnectionFactory.")
        if not isinstance(scope_resolver, ScopeResolver):
            raise TypeError("CanonicalManagementReadService requires a ScopeResolver.")
        if content_loader is not None and not callable(content_loader):
            raise TypeError("content_loader must be callable.")
        self._factory = factory
        self._scopes = scope_resolver
        self._reads = reads or ManagementReadRepository()
        self._history = history or ItemHistoryRepository()
        self._semantic = semantic or SemanticGraphContractRepository()
        self._content_loader = content_loader

    def list_items(self, request: SpaceManagementPageRequest) -> ManagementItemPage:
        actor, epoch, space, before_time, before_id, limit = self._page_request(request)
        connection = self._factory.open(read_only=True)
        try:
            with transaction(connection, TransactionMode.DEFERRED):
                scope = self._scopes.resolve_in_transaction(
                    connection,
                    SessionPrincipal(actor, epoch),
                    space,
                    Capability.READ,
                )
                if scope.access_plan is None:
                    raise ItemHistoryIntegrityError(
                        "The canonical access plan is unavailable."
                    )
                plan = scope.access_plan
                sources, overlays = _closure_inputs(plan)
                rows = self._reads.list_items_closure(
                    connection,
                    selected_space_id=plan.selected_view.id,
                    source_space_ids=sources,
                    overlays=overlays,
                    before_updated_at_ms=before_time,
                    before_item_id=before_id,
                    limit=limit + 1,
                )
                native_capabilities = _native_capabilities(plan)
                rows = tuple(
                    replace(
                        row,
                        capabilities=(
                            native_capabilities
                            if row.origin == "native"
                            else ("read",)
                        ),
                    )
                    for row in rows
                )
                return ManagementItemPage(
                    space_id=plan.selected_view.id,
                    items=rows[:limit],
                    has_more=len(rows) > limit,
                    corpus_closure_sha256=plan.corpus_closure.sha256,
                )
        finally:
            connection.close()

    def get_item(self, request: ItemManagementReadRequest) -> ManagementItemDetail:
        actor, epoch, space, item = self._item_request(request)
        scope_capabilities: tuple[Capability, ...]
        authorization = None
        corpus_closure_sha256: str | None = None
        connection = self._factory.open(read_only=True)
        try:
            with transaction(connection, TransactionMode.DEFERRED):
                scope = self._scopes.resolve_in_transaction(
                    connection,
                    SessionPrincipal(actor, epoch),
                    space,
                    Capability.READ,
                )
                if scope.access_plan is None:
                    raise ItemHistoryIntegrityError(
                        "The canonical access plan is unavailable."
                    )
                plan = scope.access_plan
                sources, overlays = _closure_inputs(plan)
                authorization = self._reads.resolve_item_authorization(
                    connection,
                    selected_space_id=plan.selected_view.id,
                    item_id=item,
                    source_space_ids=sources,
                    overlays=overlays,
                )
                if authorization is None:
                    raise _item_not_found()
                now = db_now_ms(connection)
                record = self._history.get_item(
                    connection,
                    space_id=authorization.source_space_id,
                    item_id=item,
                    now_ms=now,
                )
                scope_capabilities = (
                    plan.selected_view.capabilities
                    if authorization.origin == "native"
                    else (Capability.READ,)
                )
                corpus_closure_sha256 = plan.corpus_closure.sha256
                references = self._item_references(
                    connection,
                    plan=plan,
                    authorization=authorization,
                    source_item=record,
                )
        finally:
            connection.close()
        if authorization is None:  # pragma: no cover - transaction invariant
            raise ItemHistoryIntegrityError("Item authorization was not retained.")
        markdown = _materialize_body(
            record.anchor_revision.body, self._content_loader
        )
        if record.anchor_revision.body is not None and (
            record.anchor_revision.body.content_blob_hash is not None
        ):
            record, scope_capabilities, now, authorization, corpus_closure_sha256, references = (
                self._revalidate_item(
                    actor=actor,
                    epoch=epoch,
                    space=space,
                    item=item,
                    expected_revision=record.anchor_revision,
                    expected_authorization=authorization,
                )
            )
        content_age = _age_seconds(
            now, record.anchor_revision.created_at_ms, "revision creation time"
        )
        review_age = (
            None
            if record.last_reviewed_at_ms is None
            else _age_seconds(now, record.last_reviewed_at_ms, "last review time")
        )
        inactive_duration = (
            None
            if record.deactivation is None
            else _age_seconds(
                now, record.deactivation.deactivated_at_ms, "deactivation time"
            )
        )
        return ManagementItemDetail(
            record=record,
            markdown=markdown,
            capabilities=self._item_capabilities(
                scope_capabilities, record.lifecycle_status
            ),
            content_age_seconds=content_age,
            review_age_seconds=review_age,
            inactive_duration_seconds=inactive_duration,
            selected_view_space_id=authorization.selected_view_space_id,
            source_space_id=authorization.source_space_id,
            inherited=authorization.inherited,
            origin=authorization.origin,
            overlay_grant_id=authorization.overlay_grant_id,
            corpus_closure_sha256=corpus_closure_sha256,
            references=references,
        )

    def list_revisions(
        self, request: ItemRevisionPageRequest
    ) -> ManagementRevisionPage:
        actor, epoch, space, item, before_no, before_id, limit = (
            self._revision_page_request(request)
        )
        connection = self._factory.open(read_only=True)
        try:
            with transaction(connection, TransactionMode.DEFERRED):
                scope = self._scopes.resolve_in_transaction(
                    connection,
                    SessionPrincipal(actor, epoch),
                    space,
                    Capability.READ,
                )
                if scope.access_plan is None:
                    raise ItemHistoryIntegrityError(
                        "The canonical access plan is unavailable."
                    )
                plan = scope.access_plan
                sources, overlays = _closure_inputs(plan)
                authorization = self._reads.resolve_item_authorization(
                    connection,
                    selected_space_id=plan.selected_view.id,
                    item_id=item,
                    source_space_ids=sources,
                    overlays=overlays,
                )
                if authorization is None:
                    raise _item_not_found()
                rows = self._history.list_revisions(
                    connection,
                    space_id=authorization.source_space_id,
                    item_id=item,
                    before_revision_no=before_no,
                    before_revision_id=before_id,
                    limit=limit + 1,
                )
                return ManagementRevisionPage(
                    space_id=plan.selected_view.id,
                    item_id=item,
                    items=rows[:limit],
                    has_more=len(rows) > limit,
                    selected_view_space_id=plan.selected_view.id,
                    source_space_id=authorization.source_space_id,
                    inherited=authorization.inherited,
                    origin=authorization.origin,
                    overlay_grant_id=authorization.overlay_grant_id,
                    corpus_closure_sha256=plan.corpus_closure.sha256,
                )
        finally:
            connection.close()

    def get_revision(
        self, request: ItemRevisionReadRequest
    ) -> ManagementRevisionDetail:
        actor, epoch, space, item, revision_id = self._revision_request(request)
        revision, authorization, closure_sha = self._revision_snapshot(
            actor=actor,
            epoch=epoch,
            space=space,
            item=item,
            revision_id=revision_id,
        )
        markdown = _materialize_body(revision.body, self._content_loader)
        if revision.body is not None and revision.body.content_blob_hash is not None:
            verified, verified_authorization, verified_sha = self._revision_snapshot(
                actor=actor,
                epoch=epoch,
                space=space,
                item=item,
                revision_id=revision_id,
            )
            if (
                verified != revision
                or verified_authorization != authorization
                or verified_sha != closure_sha
            ):
                raise ManagementReadStateConflictError(
                    "The revision changed while its content was loaded."
                )
            revision = verified
        return ManagementRevisionDetail(
            space_id=space,
            revision=revision,
            markdown=markdown,
            selected_view_space_id=authorization.selected_view_space_id,
            source_space_id=authorization.source_space_id,
            inherited=authorization.inherited,
            origin=authorization.origin,
            overlay_grant_id=authorization.overlay_grant_id,
            corpus_closure_sha256=closure_sha,
        )

    def compare_revisions(
        self, request: ItemRevisionCompareRequest
    ) -> ManagementRevisionComparison:
        actor, epoch, space, item, from_id, to_id = self._compare_request(request)
        before, after, authorization, closure_sha = self._revision_pair_snapshot(
            actor=actor,
            epoch=epoch,
            space=space,
            item=item,
            from_revision_id=from_id,
            to_revision_id=to_id,
        )
        from_markdown = _materialize_body(before.body, self._content_loader)
        to_markdown = _materialize_body(after.body, self._content_loader)
        if from_markdown is None or to_markdown is None:
            raise ManagementReadBodyUnavailableError(
                "Both revision bodies are required for comparison."
            )
        if any(
            revision.body is not None and revision.body.content_blob_hash is not None
            for revision in (before, after)
        ):
            verified_before, verified_after, verified_authorization, verified_sha = (
                self._revision_pair_snapshot(
                actor=actor,
                epoch=epoch,
                space=space,
                item=item,
                from_revision_id=from_id,
                to_revision_id=to_id,
            )
            )
            if (
                (verified_before, verified_after) != (before, after)
                or verified_authorization != authorization
                or verified_sha != closure_sha
            ):
                raise ManagementReadStateConflictError(
                    "A revision changed while comparison content was loaded."
                )
        hunks = _diff_hunks(from_markdown, to_markdown)
        return ManagementRevisionComparison(
            space_id=space,
            item_id=item,
            from_revision_id=from_id,
            to_revision_id=to_id,
            diff_format="unified-v1",
            hunks=hunks,
            diff_sha256=_diff_sha256(hunks),
            selected_view_space_id=authorization.selected_view_space_id,
            source_space_id=authorization.source_space_id,
            inherited=authorization.inherited,
            origin=authorization.origin,
            overlay_grant_id=authorization.overlay_grant_id,
            corpus_closure_sha256=closure_sha,
        )

    def graph(self, request: SpaceManagementReadRequest) -> ManagementGraph:
        actor, epoch, space = self._read_request(request)
        connection = self._factory.open(read_only=True)
        try:
            with transaction(connection, TransactionMode.DEFERRED):
                scope = self._scopes.resolve_in_transaction(
                    connection,
                    SessionPrincipal(actor, epoch),
                    space,
                    Capability.READ,
                )
                if scope.access_plan is None:
                    raise ItemHistoryIntegrityError(
                        "The canonical access plan is unavailable."
                    )
                plan = scope.access_plan
                sources, overlays = _closure_inputs(plan)
                graph = self._reads.graph_closure(
                    connection,
                    selected_space_id=plan.selected_view.id,
                    source_space_ids=sources,
                    overlays=overlays,
                    closure_generation=plan.corpus_closure.generation,
                    corpus_closure_sha256=plan.corpus_closure.sha256,
                    include_aggregate_edges=(
                        self._scopes.flags.aggregate_graph_reads
                    ),
                )
                native_capabilities = _native_capabilities(plan)
                return replace(
                    graph,
                    nodes=tuple(
                        replace(
                            node,
                            capabilities=(
                                native_capabilities
                                if node.origin == "native"
                                else ("read",)
                            ),
                        )
                        for node in graph.nodes
                    ),
                    edges=tuple(
                        replace(
                            edge,
                            capabilities=(
                                native_capabilities
                                if not edge.inherited
                                else ("read",)
                            ),
                        )
                        for edge in graph.edges
                    ),
                )
        finally:
            connection.close()

    def projection_status(
        self, request: SpaceManagementReadRequest
    ) -> ManagementProjectionStatus:
        actor, epoch, space = self._read_request(request)
        connection = self._factory.open(read_only=True)
        try:
            with transaction(connection, TransactionMode.DEFERRED):
                scope = self._scopes.resolve_in_transaction(
                    connection,
                    SessionPrincipal(actor, epoch),
                    space,
                    Capability.READ,
                )
                return self._reads.projection_status(
                    connection,
                    space_id=scope.space.id,
                )
        finally:
            connection.close()

    def list_findings(
        self, request: SpaceManagementPageRequest
    ) -> ManagementFindingPage:
        actor, epoch, space, before_time, before_id, limit = self._page_request(request)
        connection = self._factory.open(read_only=True)
        try:
            with transaction(connection, TransactionMode.DEFERRED):
                scope = self._scopes.resolve_in_transaction(
                    connection,
                    SessionPrincipal(actor, epoch),
                    space,
                    Capability.READ,
                )
                if scope.access_plan is None:
                    raise ItemHistoryIntegrityError(
                        "The canonical access plan is unavailable."
                    )
                plan = scope.access_plan
                sources, overlays = _closure_inputs(plan)
                rows = self._reads.list_findings_closure(
                    connection,
                    selected_space_id=plan.selected_view.id,
                    source_space_ids=sources,
                    overlays=overlays,
                    before_updated_at_ms=before_time,
                    before_finding_id=before_id,
                    limit=limit + 1,
                )
                native_capabilities = _native_capabilities(plan)
                rows = tuple(
                    replace(
                        row,
                        capabilities=(
                            native_capabilities
                            if row.origin == "native"
                            else ("read",)
                        ),
                    )
                    for row in rows
                )
                return ManagementFindingPage(
                    space_id=plan.selected_view.id,
                    items=rows[:limit],
                    has_more=len(rows) > limit,
                    corpus_closure_sha256=plan.corpus_closure.sha256,
                )
        finally:
            connection.close()

    def list_activity(
        self, request: SpaceManagementPageRequest
    ) -> ManagementActivityPage:
        actor, epoch, space, before_time, before_id, limit = self._page_request(request)
        connection = self._factory.open(read_only=True)
        try:
            with transaction(connection, TransactionMode.DEFERRED):
                scope = self._scopes.resolve_in_transaction(
                    connection,
                    SessionPrincipal(actor, epoch),
                    space,
                    Capability.READ,
                )
                if scope.access_plan is None:
                    raise ItemHistoryIntegrityError(
                        "The canonical access plan is unavailable."
                    )
                plan = scope.access_plan
                sources, overlays = _closure_inputs(plan)
                rows = self._reads.list_activity_closure(
                    connection,
                    selected_space_id=plan.selected_view.id,
                    source_space_ids=sources,
                    overlays=overlays,
                    before_occurred_at_ms=before_time,
                    before_event_uuid=before_id,
                    limit=limit + 1,
                )
                native_capabilities = _native_capabilities(plan)
                rows = tuple(
                    replace(
                        row,
                        capabilities=(
                            native_capabilities
                            if row.origin == "native"
                            else ("read",)
                        ),
                    )
                    for row in rows
                )
                return ManagementActivityPage(
                    space_id=plan.selected_view.id,
                    items=rows[:limit],
                    has_more=len(rows) > limit,
                    corpus_closure_sha256=plan.corpus_closure.sha256,
                )
        finally:
            connection.close()

    def _revalidate_item(
        self,
        *,
        actor: str,
        epoch: int,
        space: str,
        item: str,
        expected_revision: ManagementRevisionRecord,
        expected_authorization: ManagementItemAuthorization,
    ) -> tuple[
        ManagementItemDetailRecord,
        tuple[Capability, ...],
        int,
        ManagementItemAuthorization,
        str,
        tuple[ManagementExplicitReference, ...],
    ]:
        connection = self._factory.open(read_only=True)
        try:
            with transaction(connection, TransactionMode.DEFERRED):
                scope = self._scopes.resolve_in_transaction(
                    connection,
                    SessionPrincipal(actor, epoch),
                    space,
                    Capability.READ,
                )
                if scope.access_plan is None:
                    raise ItemHistoryIntegrityError(
                        "The canonical access plan is unavailable."
                    )
                plan = scope.access_plan
                sources, overlays = _closure_inputs(plan)
                authorization = self._reads.resolve_item_authorization(
                    connection,
                    selected_space_id=plan.selected_view.id,
                    item_id=item,
                    source_space_ids=sources,
                    overlays=overlays,
                )
                if authorization is None:
                    raise _item_not_found()
                if authorization != expected_authorization:
                    raise ManagementReadStateConflictError(
                        "The item authorization changed while its content was loaded."
                    )
                now = db_now_ms(connection)
                record = self._history.get_item(
                    connection,
                    space_id=authorization.source_space_id,
                    item_id=item,
                    now_ms=now,
                )
                if record.anchor_revision != expected_revision:
                    raise ManagementReadStateConflictError(
                        "The item changed while its content was loaded."
                    )
                capabilities = (
                    plan.selected_view.capabilities
                    if authorization.origin == "native"
                    else (Capability.READ,)
                )
                return (
                    record,
                    capabilities,
                    now,
                    authorization,
                    plan.corpus_closure.sha256,
                    self._item_references(
                        connection,
                        plan=plan,
                        authorization=authorization,
                        source_item=record,
                    ),
                )
        finally:
            connection.close()

    def _item_references(
        self,
        connection: ManagedConnection,
        *,
        plan: AccessPlan,
        authorization: ManagementItemAuthorization,
        source_item: ManagementItemDetailRecord,
    ) -> tuple[ManagementExplicitReference, ...]:
        rows = self._semantic.list_references(
            connection,
            source_revision_id=source_item.anchor_revision.id,
            limit=1000,
        )
        sources, overlays = _closure_inputs(plan)
        now = db_now_ms(connection)
        result: list[ManagementExplicitReference] = []
        for row in rows:
            if (
                not isinstance(row, ExplicitReferenceRecord)
                or row.source_space_id != authorization.source_space_id
                or row.source_item_id != source_item.id
                or row.source_revision_id != source_item.anchor_revision.id
            ):
                raise ItemHistoryIntegrityError(
                    "An explicit reference escaped its source revision."
                )
            target = None
            availability = str(row.availability_status.value)
            unavailable_reason = row.unavailable_reason
            if availability == "available":
                target_authorization = self._reads.resolve_item_authorization(
                    connection,
                    selected_space_id=plan.selected_view.id,
                    item_id=row.target_item_id,
                    source_space_ids=sources,
                    overlays=overlays,
                )
                if target_authorization is not None:
                    target_item = self._history.get_item(
                        connection,
                        space_id=target_authorization.source_space_id,
                        item_id=row.target_item_id,
                        now_ms=now,
                    )
                    if (
                        target_item.lifecycle_status == "active"
                        and target_item.curation_status == "published"
                    ):
                        target = ManagementExplicitReferenceTarget(
                            item_id=row.target_item_id,
                            observed_revision_id=row.target_revision_id,
                            current_revision_id=target_item.current_revision_id,
                            title=target_item.anchor_revision.title,
                            source_space_id=target_authorization.source_space_id,
                            origin=target_authorization.origin,
                            inherited=target_authorization.inherited,
                            newer_revision_available=(
                                target_item.current_revision_id
                                != row.target_revision_id
                            ),
                        )
                if target is None:
                    availability = "unavailable"
                    unavailable_reason = "target_unavailable"
            result.append(
                ManagementExplicitReference(
                    id=row.id,
                    ordinal=row.ordinal,
                    syntax_version=row.syntax_version,
                    availability_status=availability,
                    unavailable_reason=unavailable_reason,
                    target=target,
                )
            )
        return tuple(result)

    def _revision_snapshot(
        self,
        *,
        actor: str,
        epoch: int,
        space: str,
        item: str,
        revision_id: str,
    ) -> tuple[ManagementRevisionRecord, ManagementItemAuthorization, str]:
        connection = self._factory.open(read_only=True)
        try:
            with transaction(connection, TransactionMode.DEFERRED):
                scope = self._scopes.resolve_in_transaction(
                    connection,
                    SessionPrincipal(actor, epoch),
                    space,
                    Capability.READ,
                )
                if scope.access_plan is None:
                    raise ItemHistoryIntegrityError(
                        "The canonical access plan is unavailable."
                    )
                plan = scope.access_plan
                sources, overlays = _closure_inputs(plan)
                authorization = self._reads.resolve_item_authorization(
                    connection,
                    selected_space_id=plan.selected_view.id,
                    item_id=item,
                    source_space_ids=sources,
                    overlays=overlays,
                )
                if authorization is None:
                    raise _item_not_found()
                revision = self._history.get_revision(
                    connection,
                    space_id=authorization.source_space_id,
                    item_id=item,
                    revision_id=revision_id,
                )
                return revision, authorization, plan.corpus_closure.sha256
        finally:
            connection.close()

    def _revision_pair_snapshot(
        self,
        *,
        actor: str,
        epoch: int,
        space: str,
        item: str,
        from_revision_id: str,
        to_revision_id: str,
    ) -> tuple[
        ManagementRevisionRecord,
        ManagementRevisionRecord,
        ManagementItemAuthorization,
        str,
    ]:
        connection = self._factory.open(read_only=True)
        try:
            with transaction(connection, TransactionMode.DEFERRED):
                scope = self._scopes.resolve_in_transaction(
                    connection,
                    SessionPrincipal(actor, epoch),
                    space,
                    Capability.READ,
                )
                if scope.access_plan is None:
                    raise ItemHistoryIntegrityError(
                        "The canonical access plan is unavailable."
                    )
                plan = scope.access_plan
                sources, overlays = _closure_inputs(plan)
                authorization = self._reads.resolve_item_authorization(
                    connection,
                    selected_space_id=plan.selected_view.id,
                    item_id=item,
                    source_space_ids=sources,
                    overlays=overlays,
                )
                if authorization is None:
                    raise _item_not_found()
                before = self._history.get_revision(
                    connection,
                    space_id=authorization.source_space_id,
                    item_id=item,
                    revision_id=from_revision_id,
                )
                after = self._history.get_revision(
                    connection,
                    space_id=authorization.source_space_id,
                    item_id=item,
                    revision_id=to_revision_id,
                )
                return (
                    before,
                    after,
                    authorization,
                    plan.corpus_closure.sha256,
                )
        finally:
            connection.close()

    @staticmethod
    def _item_capabilities(
        capabilities: tuple[Capability, ...], lifecycle_status: str
    ) -> tuple[str, ...]:
        allowed: set[Capability] = {Capability.READ}
        available = set(capabilities)
        if lifecycle_status == "active":
            allowed.update(
                {
                    Capability.EDIT,
                    Capability.DEACTIVATE,
                    Capability.CURATE,
                    Capability.FORCE_RELEASE,
                }
            )
        elif lifecycle_status == "inactive":
            allowed.add(Capability.RESTORE)
        return tuple(
            capability.value
            for capability in Capability
            if capability in allowed and capability in available
        )

    @staticmethod
    def _item_request(
        request: ItemManagementReadRequest,
    ) -> tuple[str, int, str, str]:
        if not isinstance(request, ItemManagementReadRequest):
            raise TypeError("An ItemManagementReadRequest is required.")
        return (
            _user(request.actor_user_id),
            _positive(request.actor_scope_epoch, "actor_scope_epoch"),
            _uuid(request.space_id, "space_id"),
            _uuid(request.item_id, "item_id"),
        )

    @staticmethod
    def _revision_page_request(
        request: ItemRevisionPageRequest,
    ) -> tuple[str, int, str, str, int | None, str | None, int]:
        if not isinstance(request, ItemRevisionPageRequest):
            raise TypeError("An ItemRevisionPageRequest is required.")
        if (request.before_revision_no is None) != (
            request.before_revision_id is None
        ):
            raise ValueError("Both revision cursor fields must be supplied together.")
        before_no = (
            None
            if request.before_revision_no is None
            else _positive(request.before_revision_no, "before_revision_no")
        )
        before_id = (
            None
            if request.before_revision_id is None
            else _uuid(request.before_revision_id, "before_revision_id")
        )
        return (
            _user(request.actor_user_id),
            _positive(request.actor_scope_epoch, "actor_scope_epoch"),
            _uuid(request.space_id, "space_id"),
            _uuid(request.item_id, "item_id"),
            before_no,
            before_id,
            _positive(request.limit, "limit", 100),
        )

    @staticmethod
    def _revision_request(
        request: ItemRevisionReadRequest,
    ) -> tuple[str, int, str, str, str]:
        if not isinstance(request, ItemRevisionReadRequest):
            raise TypeError("An ItemRevisionReadRequest is required.")
        return (
            _user(request.actor_user_id),
            _positive(request.actor_scope_epoch, "actor_scope_epoch"),
            _uuid(request.space_id, "space_id"),
            _uuid(request.item_id, "item_id"),
            _uuid(request.revision_id, "revision_id"),
        )

    @staticmethod
    def _compare_request(
        request: ItemRevisionCompareRequest,
    ) -> tuple[str, int, str, str, str, str]:
        if not isinstance(request, ItemRevisionCompareRequest):
            raise TypeError("An ItemRevisionCompareRequest is required.")
        return (
            _user(request.actor_user_id),
            _positive(request.actor_scope_epoch, "actor_scope_epoch"),
            _uuid(request.space_id, "space_id"),
            _uuid(request.item_id, "item_id"),
            _uuid(request.from_revision_id, "from_revision_id"),
            _uuid(request.to_revision_id, "to_revision_id"),
        )

    @staticmethod
    def _read_request(
        request: SpaceManagementReadRequest,
    ) -> tuple[str, int, str]:
        if not isinstance(request, SpaceManagementReadRequest):
            raise TypeError("A SpaceManagementReadRequest is required.")
        return (
            _user(request.actor_user_id),
            _positive(request.actor_scope_epoch, "actor_scope_epoch"),
            _uuid(request.space_id, "space_id"),
        )

    @staticmethod
    def _page_request(
        request: SpaceManagementPageRequest,
    ) -> tuple[str, int, str, int | None, str | None, int]:
        if not isinstance(request, SpaceManagementPageRequest):
            raise TypeError("A SpaceManagementPageRequest is required.")
        before_time, before_id = _cursor(
            request.before_updated_at_ms,
            request.before_id,
        )
        return (
            _user(request.actor_user_id),
            _positive(request.actor_scope_epoch, "actor_scope_epoch"),
            _uuid(request.space_id, "space_id"),
            before_time,
            before_id,
            _positive(request.limit, "limit", 100),
        )


__all__ = [
    "CanonicalManagementReadService",
    "ItemManagementReadRequest",
    "ItemRevisionCompareRequest",
    "ItemRevisionPageRequest",
    "ItemRevisionReadRequest",
    "ManagementActivityPage",
    "ManagementDiffHunk",
    "ManagementDiffTooLargeError",
    "ManagementFindingPage",
    "ManagementItemDetail",
    "ManagementItemPage",
    "ManagementReadBodyUnavailableError",
    "ManagementReadStateConflictError",
    "ManagementRevisionComparison",
    "ManagementRevisionDetail",
    "ManagementRevisionPage",
    "SpaceManagementPageRequest",
    "SpaceManagementReadRequest",
]
