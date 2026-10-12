"""P3.4 conversation-derived Agent context composition.

This module is deliberately inert: it registers no HTTP route and reads no
legacy knowledge, Second Brain, memory, project, workspace, or Notion store.
One public ``compose`` call derives the immutable knowledge space from an
already accepted canonical turn, authorizes explicit live bindings outside
SQLite, and then revalidates the complete turn/binding set before one bounded
canonical FTS/pin/context snapshot is returned.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, replace
import hashlib
import json
import re
import unicodedata
from typing import Callable, Iterator, Literal, Protocol
from uuid import UUID, uuid5

from ..conversation_binding import (
    ConversationBindingFence,
    ConversationBindingIntegrityError,
)
from ..answer_reading import EvidenceReadingCursor, ReadingSource
from ..db import ConnectionFactory, ManagedConnection, TransactionMode, db_now_ms, transaction
from ..errors import KnowledgeCoreError
from ..evidence_passages import allocate_evidence_bytes, select_evidence_passages
from ..repositories.context_bodies import (
    ContextBodyMetadata,
    ContextBodyRepository,
    ContextBodyUnavailableError,
)
from ..repositories.audit import AuditEventDraft, AuditRepository
from ..repositories.conversations import (
    ConversationContextBindingRecord,
    ConversationNotFoundError,
    ConversationRepository,
    ConversationRepositoryError,
    ConversationScopeError,
    ConversationTurnRecord,
    ConversationVersionConflictError,
)
from ..repositories.outbox import OutboxEventDraft, OutboxRepository
from ..repositories.management_reads import ManagementReadRepository
from ..repositories.personal_wikis import (
    PersonalWikiProfileRecord,
    PersonalWikiProfileRepository,
)
from ..repositories.retrieval import (
    CanonicalRetrievalSnapshot,
    RankedDocumentIdentity,
    RetrievalQueryIntent,
    RetrievalPinReference,
    RetrievalRepository,
)
from ..repositories.users import UserNotFoundError, UserRecord, UserRepository
from ..repositories.source_artifacts import SourceArtifactRepository
from ..scope import (
    AccessPlan,
    Capability,
    ScopeResolutionError,
    ScopeResolver,
    SessionPrincipal,
    SpaceSummary,
)


ContextAvailability = Literal["available", "unavailable"]
LiveUnavailableReason = Literal[
    "access_revoked", "resolver_unavailable", "source_unavailable"
]

_USER_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$", re.ASCII)
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.ASCII,
)
_SHA_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_AVAILABILITY_AUDIT_NAMESPACE = UUID("11c4656e-f513-4805-8b92-ebcbf8c56bc6")
_MAX_BINDINGS = 20
_MAX_CONTEXT_BYTES = 32_768
_MAX_TOTAL_CONTEXT_BYTES = 131_072
_HYBRID_MANDATORY_CAP = 4
_HYBRID_TOTAL_EXCERPT_BYTES = 32_768
_FULL_CORPUS_TOTAL_EXCERPT_BYTES = 98_304


class AgentContextError(KnowledgeCoreError):
    code = "AGENT_CONTEXT_ERROR"


class AgentContextNotFoundError(AgentContextError):
    code = "NOT_FOUND"
    http_status = 404


class AgentContextScopeStaleError(AgentContextError):
    code = "SCOPE_STALE"
    http_status = 409


class AgentContextStateConflictError(AgentContextError):
    code = "STATE_CONFLICT"
    http_status = 409


class AgentContextResolverUnavailableError(AgentContextError):
    code = "CONTEXT_RESOLVER_UNAVAILABLE"
    http_status = 503


class AgentContextIntegrityError(AgentContextError):
    code = "INTERNAL_ERROR"
    http_status = 500


class AgentContextWikiArchivedHistoryOnlyError(AgentContextError):
    code = "WIKI_ARCHIVED_HISTORY_ONLY"
    http_status = 409


class AgentContextCorpusClosureStaleError(AgentContextError):
    code = "CORPUS_CLOSURE_STALE"
    http_status = 409


@dataclass(frozen=True, slots=True)
class AgentTurnContextRequest:
    actor_user_id: str
    actor_scope_epoch: int
    conversation_id: str
    turn_id: str
    top_k: int = 8
    neighbor_limit: int = 16
    event_limit: int = 20
    pin_limit: int = 20
    as_of_ms: int | None = None


@dataclass(frozen=True, slots=True)
class AgentBoundContextResolveRequest:
    actor_user_id: str
    actor_scope_epoch: int
    conversation_id: str
    space_id: str
    binding_id: str
    binding_row_version: int
    binding_type: str
    binding_mode: str
    external_key: str
    source_artifact_id: str | None
    snapshot_sha256: str | None


@dataclass(frozen=True, slots=True)
class AgentBoundContextResolution:
    conversation_id: str
    space_id: str
    binding_id: str
    binding_row_version: int
    binding_type: str
    binding_mode: str
    external_key: str
    availability: ContextAvailability
    content_md: str | None
    content_sha256: str | None
    evidence_sha256: str | None
    unavailable_reason: LiveUnavailableReason | None


class AgentBoundContextResolver(Protocol):
    """Materialize one explicit binding without an open DB transaction.

    Snapshot mode loads the already-authorized immutable artifact/hash and must
    not reauthorize or silently replace it with a live source. Live mode
    reauthorizes the external key for this actor/conversation on every call.
    """

    def resolve(
        self, request: AgentBoundContextResolveRequest
    ) -> AgentBoundContextResolution: ...


@dataclass(frozen=True, slots=True)
class AgentBoundContext:
    binding_id: str
    binding_row_version: int
    binding_type: str
    binding_mode: str
    external_key: str
    display_name: str
    source_artifact_id: str | None
    snapshot_sha256: str | None
    availability: ContextAvailability
    content_md: str | None
    content_sha256: str | None
    evidence_sha256: str | None
    unavailable_reason: LiveUnavailableReason | None
    trust_boundary: Literal["untrusted_reference"] = "untrusted_reference"


@dataclass(frozen=True, slots=True)
class AgentConversationMessage:
    sequence_no: int
    role: Literal["system", "user", "assistant", "tool"]
    text: str


@dataclass(frozen=True, slots=True)
class AgentConversationSummary:
    covered_through_sequence_no: int
    summary_md: str
    summary_sha256: str


@dataclass(frozen=True, slots=True)
class AgentMandatoryContext:
    kind: Literal["explicit_context", "pin", "stable_reference"]
    identity: str
    title: str
    content_md: str
    content_sha256: str
    source_item_id: str | None = None
    source_revision_id: str | None = None
    target_item_id: str | None = None
    target_revision_id: str | None = None
    newer_revision_available: bool = False
    truncated: bool = False
    trust_boundary: Literal["untrusted_reference"] = "untrusted_reference"
    source_bytes: int = 0
    source_ranges: tuple[tuple[int, int], ...] = ()
    target_source_space_id: str | None = None
    source_artifacts: tuple[AgentSourceArtifact, ...] = ()


@dataclass(frozen=True, slots=True)
class AgentSourceArtifact:
    artifact_id: str
    source_revision_id: str
    original_filename: str
    media_type: str
    size_bytes: int
    content_sha256: str = ""


@dataclass(frozen=True, slots=True)
class AgentRankedContext:
    item_id: str
    revision_id: str
    source_space_id: str
    rank: int
    title: str
    excerpt_md: str
    relation_semantics: str
    truncated: bool
    selection_mode: Literal["ranked", "continuation", "representative"] = "ranked"
    source_artifacts: tuple[AgentSourceArtifact, ...] = ()
    trust_boundary: Literal["untrusted_reference"] = "untrusted_reference"
    source_bytes: int = 0
    source_sha256: str = ""
    source_ranges: tuple[tuple[int, int], ...] = ()
    body_in_mandatory: str | None = None


@dataclass(frozen=True, slots=True)
class AgentContextBudgetDisclosure:
    mandatory_eligible_count: int
    mandatory_selected_count: int
    mandatory_omitted_count: int
    ranked_selected_count: int
    byte_truncated_count: int
    excerpt_bytes: int
    supplemental_selected_count: int = 0
    mandatory_cap: int = _HYBRID_MANDATORY_CAP
    excerpt_budget_bytes: int = _HYBRID_TOTAL_EXCERPT_BYTES
    body_eligible_count: int = 0
    body_omitted_count: int = 0
    body_source_bytes: int = 0


@dataclass(frozen=True, slots=True)
class AgentTurnContextBundle:
    actor_user_id: str
    conversation_id: str
    turn_id: str
    operation_id: str
    space_id: str
    normalized_query: str
    retrieval: CanonicalRetrievalSnapshot
    pins: tuple[RetrievalPinReference, ...]
    explicit_contexts: tuple[AgentBoundContext, ...]
    recent_messages: tuple[AgentConversationMessage, ...]
    active_summary: AgentConversationSummary | None
    selected_view: SpaceSummary | None = None
    conversation_binding_sha256: str | None = None
    selected_view_space_id: str | None = None
    corpus_closure_generation: int | None = None
    corpus_closure_sha256: str | None = None
    mandatory_contexts: tuple[AgentMandatoryContext, ...] = ()
    ranked_contexts: tuple[AgentRankedContext, ...] = ()
    context_budget: AgentContextBudgetDisclosure | None = None
    retrieval_call_count: Literal[1] = 1
    legacy_knowledge_queried: Literal[False] = False
    legacy_second_brain_queried: Literal[False] = False
    private_memory_injected: Literal[False] = False
    default_project_context_injected: Literal[False] = False
    trust_boundary: Literal["untrusted_reference"] = "untrusted_reference"


@dataclass(frozen=True, slots=True)
class _TurnSnapshot:
    actor: UserRecord
    request_id: str
    conversation_id: str
    space_id: str
    query: str
    turn: ConversationTurnRecord
    bindings: tuple[ConversationContextBindingRecord, ...]
    user_message_sequence_no: int
    access_plan: AccessPlan
    binding_fence: ConversationBindingFence | None
    previous_retrieval_documents: tuple[RankedDocumentIdentity, ...]


@dataclass(frozen=True, slots=True)
class AgentTurnReadingSession:
    """Context and its invocation-owned reader, never a model/client payload."""

    context: AgentTurnContextBundle
    reader: EvidenceReadingCursor


def _user(value: object) -> str:
    if not isinstance(value, str) or _USER_RE.fullmatch(value) is None:
        raise ValueError("actor_user_id is invalid.")
    return value


def _uuid(value: object, field: str) -> str:
    if not isinstance(value, str) or _UUID_RE.fullmatch(value) is None:
        raise ValueError(f"{field} must be a canonical lowercase UUID.")
    return value


def _sha(value: object, field: str) -> str:
    if not isinstance(value, str) or _SHA_RE.fullmatch(value) is None:
        raise AgentContextIntegrityError(f"Resolved {field} is invalid.")
    return value


def _epoch(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError("actor_scope_epoch must be a positive integer.")
    return value


def _display_name(binding: ConversationContextBindingRecord) -> str:
    value = binding.display_metadata.get("display_name")
    if value is None:
        return {
            "workspace_file": "Workspace file",
            "project_context": "Project context",
            "notion_page": "Notion page",
            "source_artifact": "Source artifact",
        }[binding.binding_type]
    if not isinstance(value, str) or not 1 <= len(value) <= 200 or value != value.strip():
        raise AgentContextIntegrityError("Stored binding display name is invalid.")
    if any(unicodedata.category(character).startswith("C") for character in value):
        raise AgentContextIntegrityError("Stored binding display name is unsafe.")
    return value


def _safe_content(value: object) -> tuple[str, str, int]:
    if not isinstance(value, str):
        raise AgentContextIntegrityError("Resolved context content is invalid.")
    if any(
        unicodedata.category(character).startswith("C")
        and character not in {"\n", "\r", "\t"}
        for character in value
    ):
        raise AgentContextIntegrityError("Resolved context content has controls.")
    encoded = value.encode("utf-8")
    if not encoded or len(encoded) > _MAX_CONTEXT_BYTES:
        raise AgentContextIntegrityError(
            "Resolved context content exceeds the per-binding bound."
        )
    return value, hashlib.sha256(encoded).hexdigest(), len(encoded)


class AgentTurnContextService:
    """Compose one turn's canonical, conversation-derived context bundle."""

    __slots__ = (
        "_audit",
        "_bindings",
        "_conversation_wiki_binding",
        "_conversations",
        "_content_loader",
        "_factory",
        "_outbox",
        "_personal_wikis",
        "_postprocessor",
        "_query_expander",
        "_retrieval",
        "_scopes",
        "_users",
    )

    def __init__(
        self,
        factory: ConnectionFactory,
        *,
        scope_resolver: ScopeResolver,
        binding_resolver: AgentBoundContextResolver | None = None,
        conversations: ConversationRepository | None = None,
        retrieval: RetrievalRepository | None = None,
        users: UserRepository | None = None,
        audit: AuditRepository | None = None,
        outbox: OutboxRepository | None = None,
        conversation_wiki_binding: bool | None = None,
        personal_wikis: PersonalWikiProfileRepository | None = None,
        postprocessor: object | None = None,
        query_expander: object | None = None,
        content_loader: Callable[[str, int], bytes | str] | None = None,
    ) -> None:
        if not isinstance(factory, ConnectionFactory):
            raise TypeError("AgentTurnContextService requires a ConnectionFactory.")
        if not isinstance(scope_resolver, ScopeResolver):
            raise TypeError("AgentTurnContextService requires a ScopeResolver.")
        if binding_resolver is not None and not callable(
            getattr(binding_resolver, "resolve", None)
        ):
            raise TypeError("binding_resolver must provide resolve(request).")
        self._factory = factory
        if content_loader is not None and not callable(content_loader):
            raise TypeError("content_loader must be callable or None.")
        self._content_loader = content_loader
        self._scopes = scope_resolver
        self._bindings = binding_resolver
        self._conversations = conversations or ConversationRepository()
        inferred_binding = self._conversations.conversation_wiki_binding
        if conversation_wiki_binding is None:
            conversation_wiki_binding = inferred_binding
        if not isinstance(conversation_wiki_binding, bool):
            raise TypeError("conversation_wiki_binding must be Boolean or null.")
        if conversation_wiki_binding != inferred_binding:
            raise ValueError(
                "Agent context and conversation repository binding flags differ."
            )
        self._conversation_wiki_binding = conversation_wiki_binding
        self._personal_wikis = (
            personal_wikis or PersonalWikiProfileRepository()
        )
        self._retrieval = retrieval or RetrievalRepository()
        if postprocessor is not None and not callable(
            getattr(postprocessor, "process", None)
        ):
            raise TypeError("postprocessor must expose process() or be None.")
        self._postprocessor = postprocessor
        if query_expander is not None and not callable(
            getattr(query_expander, "expand", None)
        ):
            raise TypeError("query_expander must expose expand() or be None.")
        self._query_expander = query_expander
        self._users = users or UserRepository()
        self._audit = audit or AuditRepository()
        self._outbox = outbox or OutboxRepository()

    def compose(self, request: AgentTurnContextRequest) -> AgentTurnContextBundle:
        return self._compose(request)[0]

    @contextmanager
    def reading_session(self, request: AgentTurnContextRequest) -> Iterator[AgentTurnReadingSession]:
        """Compose once and permit fenced sequential reads for this invocation.

        No bundle, document IDs, range, or client cursor can reopen the reader.
        The accepted turn and original scope epoch are rechecked on each read.
        """
        context, reader = self._compose(request, include_reader=True)
        if reader is None:
            raise AgentContextStateConflictError("Canonical hybrid evidence is required for reading sessions.")
        try:
            yield AgentTurnReadingSession(context, reader)
        finally:
            reader.close()

    def _compose(
        self, request: AgentTurnContextRequest, *, include_reader: bool = False,
    ) -> tuple[AgentTurnContextBundle, EvidenceReadingCursor | None]:
        if not isinstance(request, AgentTurnContextRequest):
            raise TypeError("An AgentTurnContextRequest is required.")
        actor_id = _user(request.actor_user_id)
        epoch = _epoch(request.actor_scope_epoch)
        conversation_id = _uuid(request.conversation_id, "conversation_id")
        turn_id = _uuid(request.turn_id, "turn_id")
        initial = self._read_turn_snapshot(
            actor_id=actor_id,
            epoch=epoch,
            conversation_id=conversation_id,
            turn_id=turn_id,
        )
        search_query, retrieval_intent = self._query_plan(initial.query)
        resolutions = self._resolve_bindings(initial, epoch)
        needs_write = any(
            binding.binding_mode == "live"
            and (
                (
                    "active"
                    if resolution.availability == "available"
                    else "unavailable"
                )
                != binding.status
                or resolution.unavailable_reason != binding.status_reason
            )
            for binding, resolution in zip(initial.bindings, resolutions, strict=True)
        )
        connection = self._factory.open(read_only=not needs_write)
        try:
            mode = TransactionMode.IMMEDIATE if needs_write else TransactionMode.DEFERRED
            with transaction(connection, mode):
                current = self._validate_final_snapshot(
                    connection,
                    initial=initial,
                    actor_id=actor_id,
                    epoch=epoch,
                    conversation_id=conversation_id,
                    turn_id=turn_id,
                )
                contexts = self._record_and_build_contexts(
                    connection,
                    snapshot=current,
                    resolutions=resolutions,
                    request_id=current.request_id,
                )
                closure = current.access_plan.corpus_closure
                source_space_ids = tuple(
                    source.source_space_id for source in closure.sources
                )
                overlays = tuple(
                    {
                        "grant_id": overlay.grant_id,
                        "source_item_id": overlay.source_item_id,
                        "source_revision_id": overlay.source_revision_id,
                        "source_space_id": overlay.source_space_id,
                    }
                    for overlay in closure.distribution_overlays
                )
                retrieval = self._retrieval.retrieve_closure(
                    connection,
                    selected_space_id=current.space_id,
                    source_space_ids=source_space_ids,
                    overlays=overlays,
                    corpus_closure_sha256=closure.sha256,
                    query=current.query,
                    search_query=search_query,
                    top_k=request.top_k,
                    neighbor_limit=request.neighbor_limit,
                    event_limit=request.event_limit,
                    retrieval_intent=retrieval_intent,
                    continuation_documents=current.previous_retrieval_documents,
                )
                pins = self._retrieval.conversation_pins(
                    connection,
                    conversation_id=conversation_id,
                    space_id=current.space_id,
                    limit=request.pin_limit,
                )
                mandatory_candidates = self._hybrid_mandatory_candidates(
                    connection,
                    retrieval=retrieval,
                    explicit_contexts=contexts,
                    pins=pins,
                    source_space_ids=source_space_ids,
                    overlays=overlays,
                )
                recent_messages, active_summary = self._conversation_history(
                    connection,
                    snapshot=current,
                )
                binding_sha256 = (
                    None
                    if current.binding_fence is None
                    else current.binding_fence.cache_identity_sha256
                )
        except ConversationVersionConflictError as exc:
            raise AgentContextStateConflictError(
                "The conversation context changed during turn composition."
            ) from exc
        finally:
            connection.close()
        if self._postprocessor is not None:
            retrieval = self._postprocessor.process(
                retrieval,
                query=current.query,
                as_of_ms=request.as_of_ms,
            )
        mandatory_contexts, ranked_contexts, context_budget, body_metadata = (
            self._materialize_hybrid_context(
                snapshot=current,
                epoch=epoch,
                retrieval=retrieval,
                candidates=mandatory_candidates,
                evidence_query=f"{retrieval.normalized_query} {retrieval.effective_search_query or search_query}",
            )
        )
        ranked_contexts, mandatory_contexts = self._attach_source_artifacts(
            actor_id=actor_id,
            epoch=epoch,
            selected_space_id=current.space_id,
            expected_closure_sha256=current.access_plan.corpus_closure.sha256,
            ranked_contexts=ranked_contexts,
            mandatory_contexts=mandatory_contexts,
        )
        bundle = AgentTurnContextBundle(
            actor_user_id=actor_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            operation_id=current.turn.operation_id,
            space_id=current.space_id,
            normalized_query=retrieval.normalized_query,
            retrieval=retrieval,
            pins=pins,
            explicit_contexts=contexts,
            recent_messages=recent_messages,
            active_summary=active_summary,
            selected_view=current.access_plan.selected_view,
            conversation_binding_sha256=binding_sha256,
            selected_view_space_id=closure.selected_view_space_id,
            corpus_closure_generation=closure.generation,
            corpus_closure_sha256=closure.sha256,
            mandatory_contexts=mandatory_contexts,
            ranked_contexts=ranked_contexts,
            context_budget=context_budget,
        )
        reader = None
        if include_reader and retrieval.hybrid_explain is not None:
            reader = self._reading_cursor(
                snapshot=current, epoch=epoch, retrieval=retrieval,
                candidates=mandatory_candidates, metadata=body_metadata,
            )
        return bundle, reader

    def _reading_cursor(
        self, *, snapshot: _TurnSnapshot, epoch: int,
        retrieval: CanonicalRetrievalSnapshot, candidates: tuple[AgentMandatoryContext, ...],
        metadata: dict[tuple[str, str], ContextBodyMetadata],
    ) -> EvidenceReadingCursor:
        # Preserve the original four-document mandatory policy, before byte
        # allocation can omit a selected pin/reference. Explicit noncanonical
        # bindings stay in bundle.explicit_contexts; this reader is canonical.
        mandatory = self._select_mandatory_candidates(candidates)
        canonical = {(row.target_item_id, row.target_revision_id): row for row in mandatory
                     if row.kind in {"pin", "stable_reference"}}
        sources: dict[tuple[str, str], ReadingSource] = {}
        for key, row in canonical.items():
            sources[key] = ReadingSource(metadata[key], row.title, (row.kind,))
        for index, row in enumerate((*retrieval.ranked_documents, *retrieval.supplemental_documents)):
            key = (row.item_id, row.revision_id)
            roles = sources[key].selection_roles if key in sources else ()
            role = "ranked" if index < len(retrieval.ranked_documents) else row.selection_reason
            sources[key] = ReadingSource(metadata[key], row.title, tuple(dict.fromkeys((*roles, role))))

        def guard() -> None:
            current = self._body_metadata_snapshot(
                snapshot=snapshot, epoch=epoch, retrieval=retrieval, candidates=candidates,
            )
            if current != metadata:
                raise AgentContextStateConflictError("Evidence changed during the reading invocation.")

        def artifacts(rows: tuple[ReadingSource, ...]) -> dict[tuple[str, str], tuple[AgentSourceArtifact, ...]]:
            ranked = []
            references = []
            for index, row in enumerate(rows, 1):
                body = row.metadata
                key = (body.item_id, body.revision_id)
                candidate = canonical.get(key)
                if candidate is not None and candidate.kind == "stable_reference":
                    references.append(replace(candidate, target_source_space_id=body.source_space_id))
                else:
                    ranked.append(AgentRankedContext(
                        item_id=body.item_id, revision_id=body.revision_id,
                        source_space_id=body.source_space_id, rank=index, title=row.title,
                        excerpt_md="", relation_semantics="reading_source", truncated=False,
                    ))
            try:
                enriched, fixed = self._attach_source_artifacts(
                    actor_id=snapshot.actor.id, epoch=epoch, selected_space_id=snapshot.space_id,
                    expected_closure_sha256=snapshot.access_plan.corpus_closure.sha256,
                    ranked_contexts=tuple(ranked), mandatory_contexts=tuple(references),
                )
            except ScopeResolutionError as exc:
                raise AgentContextStateConflictError("Reading source authority changed.") from exc
            return {(row.item_id, row.revision_id): row.source_artifacts for row in enriched} | {
                (row.target_item_id, row.target_revision_id): row.source_artifacts for row in fixed
            }

        @contextmanager
        def bodies() -> Iterator[Callable[[ContextBodyMetadata], str]]:
            try:
                with self._context_body_reader() as loader:
                    yield loader
            except ContextBodyUnavailableError as exc:
                raise AgentContextStateConflictError("A reading source body is unavailable.") from exc

        return EvidenceReadingCursor(
            sources=tuple(sources.values()), body_reader=bodies,
            guard=guard, artifacts=artifacts,
        )

    def _attach_source_artifacts(
        self,
        *,
        actor_id: str,
        epoch: int,
        selected_space_id: str,
        expected_closure_sha256: str,
        ranked_contexts: tuple[AgentRankedContext, ...],
        mandatory_contexts: tuple[AgentMandatoryContext, ...],
    ) -> tuple[tuple[AgentRankedContext, ...], tuple[AgentMandatoryContext, ...]]:
        canonical_mandatory = tuple(row for row in mandatory_contexts
                                    if row.kind in {"pin", "stable_reference"})
        if not ranked_contexts and not canonical_mandatory:
            return ranked_contexts, mandatory_contexts
        management = ManagementReadRepository()
        artifacts = SourceArtifactRepository()
        connection = self._factory.open(read_only=True)
        try:
            with transaction(connection, TransactionMode.DEFERRED):
                scope = self._scopes.resolve_in_transaction(
                    connection,
                    SessionPrincipal(actor_id, epoch),
                    selected_space_id,
                    Capability.READ,
                )
                plan = scope.access_plan
                if (
                    plan is None
                    or plan.corpus_closure.sha256 != expected_closure_sha256
                ):
                    raise AgentContextStateConflictError(
                        "The corpus closure changed while source provenance was composed."
                    )
                closure = plan.corpus_closure
                source_spaces = tuple(
                    source.source_space_id for source in closure.sources
                )
                overlays = tuple(
                    {
                        "grant_id": overlay.grant_id,
                        "source_space_id": overlay.source_space_id,
                        "source_item_id": overlay.source_item_id,
                        "source_revision_id": overlay.source_revision_id,
                    }
                    for overlay in closure.distribution_overlays
                )
                references = tuple(row for row in canonical_mandatory
                                   if row.kind == "stable_reference")
                try:
                    current_identities = tuple(dict.fromkeys(
                        [(row.item_id, row.revision_id, row.source_space_id)
                         for row in ranked_contexts]
                        + [(row.target_item_id, row.target_revision_id, row.target_source_space_id)
                           for row in canonical_mandatory if row.kind == "pin"]
                    ))
                    if current_identities:
                        ContextBodyRepository().metadata(
                            connection, identities=current_identities,
                            source_space_ids=source_spaces, overlays=overlays,
                        )
                    reference_bodies = ContextBodyRepository().reference_metadata(
                        connection,
                        references=tuple((row.identity, row.source_revision_id,
                                          row.target_item_id, row.target_revision_id)
                                         for row in references),
                        source_space_ids=source_spaces, overlays=overlays,
                    )
                except ContextBodyUnavailableError as exc:
                    raise AgentContextStateConflictError(
                        "A fixed reference changed while source provenance was composed."
                    ) from exc
                for row in references:
                    body = reference_bodies[row.identity]
                    if (body.source_space_id != row.target_source_space_id
                            or body.content_sha256 != row.content_sha256):
                        raise AgentContextStateConflictError(
                            "Fixed-reference provenance no longer matches its body."
                        )
                # Revision is part of the key: an R1 reference must not acquire
                # originals first attached to the same item's current R2.
                by_identity: dict[tuple[str, str], tuple[AgentSourceArtifact, ...]] = {}
                identities = [
                    (row.item_id, row.revision_id, row.source_space_id, False)
                    for row in ranked_contexts
                ] + [
                    (row.target_item_id, row.target_revision_id,
                     row.target_source_space_id, row.kind == "stable_reference")
                    for row in canonical_mandatory
                ]
                for item_id, revision_id, source_space_id, historical in identities:
                    authorization = management.resolve_item_authorization(
                        connection,
                        selected_space_id=plan.selected_view.id,
                        item_id=item_id,
                        source_space_ids=source_spaces,
                        overlays=overlays,
                    )
                    if (
                        authorization is None
                        or authorization.source_space_id != source_space_id
                        or (not historical and authorization.source_revision_id != revision_id)
                    ):
                        raise AgentContextStateConflictError(
                            "A supplied document changed while source provenance was composed."
                        )
                    key = (item_id, revision_id)
                    if key in by_identity:
                        continue
                    by_identity[key] = tuple(
                        AgentSourceArtifact(
                            artifact_id=artifact.artifact_id,
                            source_revision_id=artifact.source_revision_id,
                            original_filename=artifact.original_filename,
                            media_type=artifact.media_type,
                            size_bytes=artifact.size_bytes,
                            content_sha256=artifact.content_sha256,
                        )
                        for artifact in artifacts.list_for_authorized_item(
                            connection,
                            item_id=item_id,
                            authorization=authorization,
                            as_of_revision_id=revision_id,
                        )
                    )
                return tuple(
                    replace(row, source_artifacts=by_identity[row.item_id, row.revision_id])
                    for row in ranked_contexts
                ), tuple(
                    replace(row, source_artifacts=by_identity[row.target_item_id, row.target_revision_id])
                    if row.kind in {"pin", "stable_reference"} else row
                    for row in mandatory_contexts
                )
        finally:
            connection.close()

    def _query_plan(self, query: str) -> tuple[str, RetrievalQueryIntent]:
        if self._query_expander is None:
            return query, "unknown"
        value = self._query_expander.expand(query)
        search_query = value if isinstance(value, str) and value.strip() else query
        intent: object = "unknown"
        intent_reader = getattr(self._query_expander, "intent_for", None)
        if callable(intent_reader):
            intent = intent_reader(query)
        if intent not in {
            "topic", "overview", "continuation", "exhaustive", "unknown"
        }:
            intent = "unknown"
        return search_query, intent  # type: ignore[return-value]

    @staticmethod
    def _clip_utf8(value: str, limit: int) -> tuple[str, bool, int]:
        encoded = value.encode("utf-8")
        if len(encoded) <= limit:
            return value, False, len(encoded)
        clipped = encoded[:limit].decode("utf-8", errors="ignore")
        return clipped, True, len(clipped.encode("utf-8"))

    def _hybrid_mandatory_candidates(
        self,
        connection,
        *,
        retrieval: CanonicalRetrievalSnapshot,
        explicit_contexts: tuple[AgentBoundContext, ...],
        pins: tuple[RetrievalPinReference, ...],
        source_space_ids: tuple[str, ...],
        overlays: tuple[dict[str, str], ...],
    ) -> tuple[AgentMandatoryContext, ...]:
        if retrieval.hybrid_explain is None:
            return ()
        candidates: list[AgentMandatoryContext] = []
        for context in explicit_contexts:
            if (
                context.availability != "available"
                or context.content_md is None
                or context.content_sha256 is None
            ):
                continue
            candidates.append(
                AgentMandatoryContext(
                    kind="explicit_context",
                    identity=context.binding_id,
                    title=context.display_name,
                    content_md=context.content_md,
                    content_sha256=context.content_sha256,
                )
            )
        for pin in pins:
            if (
                pin.availability != "available"
                or pin.revision_id is None
                or pin.title is None
                or pin.excerpt_md is None
                or pin.revision_content_sha256 is None
            ):
                continue
            candidates.append(
                AgentMandatoryContext(
                    kind="pin",
                    identity=pin.item_id,
                    title=pin.title,
                    content_md=pin.excerpt_md,
                    content_sha256=pin.revision_content_sha256,
                    target_item_id=pin.item_id,
                    target_revision_id=pin.revision_id,
                )
            )
        ranked_revision_ids = tuple(
            document.revision_id for document in retrieval.ranked_documents
        )
        if ranked_revision_ids:
            references = connection.execute(
                """WITH ranked(revision_id,rank) AS (
                     SELECT value,CAST(key AS INTEGER) FROM json_each(?)
                   ), authorized_sources(space_id) AS (
                     SELECT value FROM json_each(?)
                   ), authorized_overlays(
                     source_space_id,source_item_id,source_revision_id
                   ) AS (
                     SELECT json_extract(value,'$.source_space_id'),
                            json_extract(value,'$.source_item_id'),
                            json_extract(value,'$.source_revision_id')
                       FROM json_each(?)
                   )
                   SELECT ref.id,ref.source_item_id,ref.source_revision_id,
                          ref.target_item_id,ref.target_revision_id,
                          revision.title,body.content_sha256,
                          item.current_revision_id,ranked.rank,ref.ordinal
                     FROM ranked JOIN knowledge_explicit_reference AS ref
                       ON ref.source_revision_id=ranked.revision_id
                     JOIN knowledge_item AS item
                       ON item.id=ref.target_item_id
                      AND item.space_id=ref.target_space_id
                     JOIN knowledge_space AS space ON space.id=item.space_id
                     JOIN knowledge_revision AS revision
                       ON revision.id=ref.target_revision_id
                      AND revision.item_id=ref.target_item_id
                     JOIN knowledge_revision_body AS body
                       ON body.revision_id=revision.id
                      AND body.item_id=revision.item_id
                    WHERE ref.availability_status='available'
                      AND item.lifecycle_status='active'
                      AND item.curation_status='published'
                      AND space.status='active'
                      AND (body.content_md IS NOT NULL OR body.content_blob_hash IS NOT NULL)
                      AND (
                        EXISTS (SELECT 1 FROM authorized_sources source
                                 WHERE source.space_id=ref.target_space_id)
                        OR EXISTS (
                          SELECT 1 FROM authorized_overlays overlay
                           WHERE overlay.source_space_id=ref.target_space_id
                             AND overlay.source_item_id=ref.target_item_id
                             AND overlay.source_revision_id=ref.target_revision_id
                        )
                      )
                    ORDER BY ranked.rank,ref.ordinal,ref.id
                    LIMIT 41""",
                (
                    json.dumps(ranked_revision_ids, separators=(",", ":")),
                    json.dumps(source_space_ids, separators=(",", ":")),
                    json.dumps(overlays, sort_keys=True, separators=(",", ":")),
                ),
            ).fetchall()
            for row in references:
                candidates.append(
                    AgentMandatoryContext(
                        kind="stable_reference",
                        identity=_uuid(row["id"], "reference id"),
                        title=str(row["title"]),
                        content_md="",
                        content_sha256=str(row["content_sha256"]),
                        source_item_id=_uuid(
                            row["source_item_id"], "reference source item"
                        ),
                        source_revision_id=_uuid(
                            row["source_revision_id"], "reference source revision"
                        ),
                        target_item_id=_uuid(
                            row["target_item_id"], "reference target item"
                        ),
                        target_revision_id=_uuid(
                            row["target_revision_id"], "reference target revision"
                        ),
                        newer_revision_available=(
                            row["current_revision_id"] != row["target_revision_id"]
                        ),
                    )
                )

        return tuple(candidates)

    @staticmethod
    def _select_mandatory_candidates(
        candidates: tuple[AgentMandatoryContext, ...],
    ) -> tuple[AgentMandatoryContext, ...]:
        selected: list[AgentMandatoryContext] = []
        identities: set[tuple[str | None, str | None]] = set()
        for candidate in candidates:
            if len(selected) == _HYBRID_MANDATORY_CAP:
                break
            if candidate.kind in {"pin", "stable_reference"}:
                identity = (candidate.target_item_id, candidate.target_revision_id)
                if identity in identities:
                    continue
                identities.add(identity)
            selected.append(candidate)
        return tuple(selected)

    def _body_metadata_snapshot(
        self,
        *,
        snapshot: _TurnSnapshot,
        epoch: int,
        retrieval: CanonicalRetrievalSnapshot,
        candidates: tuple[AgentMandatoryContext, ...],
    ) -> dict[tuple[str, str], ContextBodyMetadata]:
        repository = ContextBodyRepository()
        connection = self._factory.open(read_only=True)
        try:
            with transaction(connection, TransactionMode.DEFERRED):
                current = self._validate_final_snapshot(
                    connection, initial=snapshot, actor_id=snapshot.actor.id,
                    epoch=epoch, conversation_id=snapshot.conversation_id,
                    turn_id=snapshot.turn.id,
                )
                closure = current.access_plan.corpus_closure
                if closure.sha256 != snapshot.access_plan.corpus_closure.sha256:
                    raise AgentContextStateConflictError(
                        "The corpus closure changed before body materialization."
                    )
                documents = (*retrieval.ranked_documents, *retrieval.supplemental_documents)
                pin_identities = tuple(
                    (row.target_item_id, row.target_revision_id, current.space_id)
                    for row in self._select_mandatory_candidates(candidates) if row.kind == "pin"
                )
                metadata = repository.metadata(
                    connection,
                    identities=tuple((row.item_id, row.revision_id, row.source_space_id)
                                     for row in documents) + pin_identities,
                    source_space_ids=tuple(source.source_space_id for source in closure.sources),
                    overlays=tuple({
                        "source_space_id": overlay.source_space_id,
                        "source_item_id": overlay.source_item_id,
                        "source_revision_id": overlay.source_revision_id,
                    } for overlay in closure.distribution_overlays),
                )
                references = repository.reference_metadata(
                    connection,
                    references=tuple(
                        (row.identity, row.source_revision_id,
                         row.target_item_id, row.target_revision_id)
                        for row in self._select_mandatory_candidates(candidates)
                        if row.kind == "stable_reference"
                    ),
                    source_space_ids=tuple(source.source_space_id for source in closure.sources),
                    overlays=tuple({
                        "source_space_id": overlay.source_space_id,
                        "source_item_id": overlay.source_item_id,
                        "source_revision_id": overlay.source_revision_id,
                    } for overlay in closure.distribution_overlays),
                )
                metadata.update({(record.item_id, record.revision_id): record
                                 for record in references.values()})
                return metadata
        except ContextBodyUnavailableError as exc:
            raise AgentContextStateConflictError(
                "A selected document changed before body materialization."
            ) from exc
        finally:
            connection.close()

    @contextmanager
    def _context_body_reader(self) -> Iterator[Callable[[ContextBodyMetadata], str]]:
        """One invocation-local connection, but no snapshot spans body reads.

        Reopening the database per tiny body repeats path/schema verification.
        Reuse only the read-only handle, never a transaction or cached body.
        CAS work stays outside SQLite transactions and the surrounding metadata
        fences still reauthorize the exact selected revisions before delivery.
        """
        repository = ContextBodyRepository()
        connection: ManagedConnection | None = None

        def read(record: ContextBodyMetadata) -> str:
            nonlocal connection
            if record.content_blob_hash is not None:
                if self._content_loader is None:
                    raise ContextBodyUnavailableError("Canonical blob body loading is not configured.")
                try:
                    payload = self._content_loader(record.content_blob_hash, record.size_bytes)
                except Exception as exc:
                    raise ContextBodyUnavailableError("The canonical body loader failed.") from exc
                return repository.validate_content(record, payload)
            if connection is None:
                connection = self._factory.open(read_only=True)
            with transaction(connection, TransactionMode.DEFERRED):
                return repository.read(connection, record)

        try:
            yield read
        finally:
            if connection is not None:
                connection.close()

    def _materialize_hybrid_context(
        self,
        *,
        snapshot: _TurnSnapshot,
        epoch: int,
        retrieval: CanonicalRetrievalSnapshot,
        candidates: tuple[AgentMandatoryContext, ...],
        evidence_query: str,
    ) -> tuple[
        tuple[AgentMandatoryContext, ...], tuple[AgentRankedContext, ...],
        AgentContextBudgetDisclosure | None,
        dict[tuple[str, str], ContextBodyMetadata],
    ]:
        if retrieval.hybrid_explain is None:
            return (), (), None, {}
        inputs = dict(snapshot=snapshot, epoch=epoch, retrieval=retrieval, candidates=candidates)
        initial = self._body_metadata_snapshot(**inputs)
        try:
            # No SQLite snapshot is open during CAS/file I/O. Materialize only
            # allocated sources, one at a time, and fence again before use.
            with self._context_body_reader() as body_loader:
                result = self._bounded_hybrid_context(
                    retrieval=retrieval, candidates=candidates, body_metadata=initial,
                    body_loader=body_loader,
                    evidence_query=evidence_query,
                )
        except ContextBodyUnavailableError as exc:
            raise AgentContextStateConflictError("A selected canonical body is unavailable.") from exc
        confirmed = self._body_metadata_snapshot(**inputs)
        if confirmed != initial:
            raise AgentContextStateConflictError("Canonical body authority changed during materialization.")
        return *result, confirmed

    def _bounded_hybrid_context(
        self,
        *,
        retrieval: CanonicalRetrievalSnapshot,
        candidates: tuple[AgentMandatoryContext, ...],
        body_metadata: dict[tuple[str, str], ContextBodyMetadata],
        body_loader: Callable[[ContextBodyMetadata], str],
        evidence_query: str | None = None,
    ) -> tuple[
        tuple[AgentMandatoryContext, ...],
        tuple[AgentRankedContext, ...],
        AgentContextBudgetDisclosure | None,
    ]:
        if retrieval.hybrid_explain is None:
            return (), (), None
        excerpt_budget = (
            _FULL_CORPUS_TOTAL_EXCERPT_BYTES
            if retrieval.hybrid_explain.final_cap is None
            else _HYBRID_TOTAL_EXCERPT_BYTES
        )
        mandatory_candidates = self._select_mandatory_candidates(candidates)
        mandatory_keys = {(row.target_item_id, row.target_revision_id)
                          for row in mandatory_candidates
                          if row.kind in {"pin", "stable_reference"}}
        mandatory_sizes = tuple(
            body_metadata[row.target_item_id, row.target_revision_id].size_bytes
            if row.kind in {"pin", "stable_reference"}
            else len(row.content_md.encode("utf-8"))
            for row in mandatory_candidates
        )
        documents = (*retrieval.ranked_documents, *retrieval.supplemental_documents)
        sizes = tuple(body_metadata[row.item_id, row.revision_id].size_bytes
                      for row in documents)
        # Reserve bytes once across all lanes. A UTF-8 remainder from the one
        # overflow source is not reassigned to a later lower-priority source.
        document_sizes = tuple(0 if (row.item_id, row.revision_id) in mandatory_keys else size
                               for row, size in zip(documents, sizes, strict=True))
        allocations = allocate_evidence_bytes((*mandatory_sizes, *document_sizes), excerpt_budget)
        selected_mandatory: list[AgentMandatoryContext] = []
        excerpt_bytes = 0
        truncated_count = 0
        for candidate, allocation in zip(
            mandatory_candidates, allocations[:len(mandatory_candidates)], strict=True,
        ):
            if not allocation:
                continue
            content = candidate.content_md
            if candidate.kind in {"pin", "stable_reference"}:
                content = body_loader(body_metadata[
                    candidate.target_item_id, candidate.target_revision_id
                ])
            evidence = select_evidence_passages(
                content, allocation,
                query=evidence_query or retrieval.normalized_query,
            )
            if evidence.source_sha256 != candidate.content_sha256:
                raise AgentContextIntegrityError("Mandatory source integrity changed.")
            if not evidence.text:
                continue
            selected_mandatory.append(
                replace(candidate, content_md=evidence.text, truncated=evidence.truncated,
                        source_bytes=evidence.source_bytes, source_ranges=evidence.ranges,
                        target_source_space_id=(
                            body_metadata[candidate.target_item_id, candidate.target_revision_id].source_space_id
                            if candidate.kind in {"pin", "stable_reference"} else None))
            )
            excerpt_bytes += len(evidence.text.encode("utf-8"))
            truncated_count += int(evidence.truncated)

        ranked_contexts: list[AgentRankedContext] = []
        document_allocations = allocations[len(mandatory_candidates):]
        shared_mandatory = {
            (row.target_item_id, row.target_revision_id): row for row in selected_mandatory
            if row.kind in {"pin", "stable_reference"}
        }
        ranked_selected_count = 0
        for index, (document, allocation) in enumerate(zip(documents, document_allocations, strict=True)):
            shared = shared_mandatory.get((document.item_id, document.revision_id))
            if not allocation and shared is None:
                continue
            is_ranked = index < len(retrieval.ranked_documents)
            if (not is_ranked and retrieval.hybrid_explain.final_cap is not None
                    and len(ranked_contexts) >= retrieval.hybrid_explain.final_cap):
                break
            metadata = body_metadata[document.item_id, document.revision_id]
            # One source body exists transiently here, never in the query cache.
            if shared is None:
                evidence = select_evidence_passages(
                    body_loader(metadata), allocation,
                    query=evidence_query or retrieval.normalized_query,
                )
            if shared is None and not evidence.text:
                # A final one/two-byte remainder may not hold one Korean code
                # point. Count that body as omitted, not as supplied evidence.
                continue
            ranked_contexts.append(
                AgentRankedContext(
                    item_id=document.item_id,
                    revision_id=document.revision_id,
                    source_space_id=document.source_space_id,
                    rank=document.rank if is_ranked else len(ranked_contexts) + 1,
                    title=document.title,
                    excerpt_md=shared.content_md if shared is not None else evidence.text,
                    relation_semantics=(document.relation_semantics if is_ranked
                                        else "related_context"),
                    truncated=shared.truncated if shared is not None else evidence.truncated,
                    selection_mode="ranked" if is_ranked else document.selection_reason,
                    source_bytes=shared.source_bytes if shared is not None else evidence.source_bytes,
                    source_sha256=shared.content_sha256 if shared is not None else evidence.source_sha256,
                    source_ranges=shared.source_ranges if shared is not None else evidence.ranges,
                    body_in_mandatory=None if shared is None else shared.identity,
                )
            )
            ranked_selected_count += int(is_ranked)
            if shared is None:
                excerpt_bytes += len(evidence.text.encode("utf-8"))
                truncated_count += int(evidence.truncated)
        if excerpt_bytes > excerpt_budget:
            raise AgentContextIntegrityError("The hybrid excerpt budget overflowed.")
        disclosure = AgentContextBudgetDisclosure(
            mandatory_eligible_count=len(candidates),
            mandatory_selected_count=len(selected_mandatory),
            mandatory_omitted_count=max(0, len(candidates) - len(selected_mandatory)),
            ranked_selected_count=ranked_selected_count,
            byte_truncated_count=truncated_count,
            excerpt_bytes=excerpt_bytes,
            supplemental_selected_count=(
                len(ranked_contexts) - ranked_selected_count
            ),
            excerpt_budget_bytes=excerpt_budget,
            body_eligible_count=len(documents),
            body_omitted_count=len(documents) - len(ranked_contexts),
            body_source_bytes=sum(sizes),
        )
        return tuple(selected_mandatory), tuple(ranked_contexts), disclosure

    def _read_turn_snapshot(
        self,
        *,
        actor_id: str,
        epoch: int,
        conversation_id: str,
        turn_id: str,
    ) -> _TurnSnapshot:
        connection = self._factory.open(read_only=True)
        try:
            with transaction(connection, TransactionMode.DEFERRED):
                return self._snapshot_in_transaction(
                    connection,
                    actor_id=actor_id,
                    epoch=epoch,
                    conversation_id=conversation_id,
                    turn_id=turn_id,
                )
        finally:
            connection.close()

    def _snapshot_in_transaction(
        self,
        connection,
        *,
        actor_id: str,
        epoch: int,
        conversation_id: str,
        turn_id: str,
    ) -> _TurnSnapshot:
        try:
            actor = self._users.require(connection, actor_id)
        except UserNotFoundError as exc:
            raise AgentContextNotFoundError("The canonical actor was not found.") from exc
        if actor.account_status != "active" or actor.scope_epoch != epoch:
            raise AgentContextScopeStaleError("The authenticated scope is stale.")
        try:
            if self._conversation_wiki_binding:
                conversation = self._conversations.require_owned(
                    connection,
                    conversation_id=conversation_id,
                    owner_user_id=actor_id,
                )
                access_status = self._conversations.access_status(
                    connection,
                    conversation_id=conversation_id,
                    owner_user_id=actor_id,
                )
                if access_status == "wiki_archived":
                    raise AgentContextWikiArchivedHistoryOnlyError(
                        "An archived personal Wiki is available as history only."
                    )
                if access_status != "accessible":
                    raise ConversationScopeError(
                        f"The conversation scope is not accessible: {access_status}."
                    )
            else:
                conversation = self._conversations.require_accessible(
                    connection,
                    conversation_id=conversation_id,
                    owner_user_id=actor_id,
                )
        except ConversationNotFoundError as exc:
            raise AgentContextNotFoundError("The conversation was not found.") from exc
        except ConversationScopeError as exc:
            raise AgentContextScopeStaleError(
                "The conversation scope is no longer accessible."
            ) from exc
        if conversation.status != "active":
            raise AgentContextStateConflictError(
                "An archived conversation cannot compose a turn context."
            )
        try:
            scope = self._scopes.resolve_in_transaction(
                connection,
                SessionPrincipal(actor_id, epoch),
                conversation.space_id,
                Capability.READ,
            )
        except ScopeResolutionError as exc:
            raise AgentContextScopeStaleError(
                "The conversation knowledge scope is no longer readable."
            ) from exc
        turn = self._conversations.get_turn(connection, turn_id)
        if (
            turn is None
            or turn.conversation_id != conversation_id
            or turn.actor_user_id != actor_id
        ):
            raise AgentContextNotFoundError("The accepted conversation turn was not found.")
        if turn.state not in {"accepted", "running"}:
            raise AgentContextStateConflictError(
                "Only an accepted or running turn can compose context."
            )
        accepted_events = self._conversations.list_turn_events(
            connection,
            turn_id=turn.id,
            conversation_id=conversation_id,
            after_sequence_no=0,
            limit=1,
        )
        if (
            len(accepted_events) != 1
            or accepted_events[0].sequence_no != 1
            or accepted_events[0].event_type != "accepted"
        ):
            raise AgentContextIntegrityError(
                "The accepted turn event is unavailable."
            )
        accepted_payload = accepted_events[0].payload
        try:
            accepted_request_id = _uuid(
                accepted_payload.get("request_id"), "accepted request_id"
            )
        except ValueError as exc:
            raise AgentContextIntegrityError(
                "The accepted turn request binding is invalid."
            ) from exc
        if accepted_payload.get("operation_id") != turn.operation_id:
            raise AgentContextIntegrityError(
                "The accepted turn operation binding is invalid."
            )
        if scope.access_plan is None:
            raise AgentContextIntegrityError(
                "The canonical conversation access plan is unavailable."
            )
        binding_fence: ConversationBindingFence | None = None
        if self._conversation_wiki_binding:
            try:
                binding_fence = ConversationBindingFence.from_mapping(
                    accepted_payload.get("conversation_binding")
                )
            except (
                ConversationBindingIntegrityError,
                TypeError,
                ValueError,
            ) as exc:
                raise AgentContextCorpusClosureStaleError(
                    "The accepted turn binding checkpoint is invalid."
                ) from exc
            if (
                accepted_payload.get("conversation_binding_sha256")
                != binding_fence.cache_identity_sha256
            ):
                raise AgentContextCorpusClosureStaleError(
                    "The accepted turn binding digest is invalid."
                )
            personal_wiki: PersonalWikiProfileRecord | None = None
            if scope.access_plan.selected_view.kind == "personal":
                personal_wiki = self._personal_wikis.get_by_space_id(
                    connection, scope.access_plan.selected_view.id
                )
            if not binding_fence.matches(
                scope.access_plan,
                personal_wiki=personal_wiki,
            ):
                raise AgentContextCorpusClosureStaleError(
                    "The accepted turn corpus closure is stale."
                )
        message = self._conversations.get_message(connection, turn.user_message_id)
        if (
            message is None
            or message.conversation_id != conversation_id
            or message.role != "user"
        ):
            raise AgentContextIntegrityError(
                "The accepted turn user message is unavailable."
            )
        try:
            message_payload = message.content
        except (ConversationRepositoryError, TypeError, ValueError) as exc:
            raise AgentContextIntegrityError(
                "The accepted turn user message is invalid."
            ) from exc
        if set(message_payload) != {"content", "role"} or message_payload.get(
            "role"
        ) != "user":
            raise AgentContextIntegrityError(
                "The accepted turn user message envelope is invalid."
            )
        user_content = message_payload.get("content")
        if not isinstance(user_content, dict):
            raise AgentContextIntegrityError(
                "The accepted turn user content is invalid."
            )
        query = user_content.get("text")
        if not isinstance(query, str):
            raise AgentContextIntegrityError(
                "The accepted turn has no canonical retrieval text."
            )
        bindings = self._conversations.list_context_bindings(
            connection,
            conversation_id=conversation_id,
            owner_user_id=actor_id,
            include_detached=False,
            limit=_MAX_BINDINGS + 1,
        )
        if len(bindings) > _MAX_BINDINGS:
            raise AgentContextStateConflictError(
                f"A turn may use at most {_MAX_BINDINGS} explicit bindings."
            )
        previous_retrieval_documents = self._previous_retrieval_documents(
            connection,
            conversation_id=conversation_id,
            before_sequence_no=message.sequence_no,
            selected_view_space_id=scope.access_plan.selected_view.id,
            corpus_closure_sha256=scope.access_plan.corpus_closure.sha256,
        )
        return _TurnSnapshot(
            actor,
            accepted_request_id,
            conversation_id,
            scope.space.id,
            query,
            turn,
            bindings,
            message.sequence_no,
            scope.access_plan,
            binding_fence,
            previous_retrieval_documents,
        )

    @staticmethod
    def _previous_retrieval_documents(
        connection,
        *,
        conversation_id: str,
        before_sequence_no: int,
        selected_view_space_id: str,
        corpus_closure_sha256: str,
    ) -> tuple[RankedDocumentIdentity, ...]:
        row = connection.execute(
            """SELECT turn.final_response_json
                 FROM conversation_turn AS turn
                 JOIN conversation_message AS message
                   ON message.id=turn.user_message_id
                WHERE turn.conversation_id=? AND turn.state='completed'
                  AND turn.final_response_json IS NOT NULL
                  AND message.sequence_no<?
                ORDER BY message.sequence_no DESC,turn.id DESC
                LIMIT 1""",
            (conversation_id, before_sequence_no),
        ).fetchone()
        if row is None:
            return ()
        try:
            response = json.loads(row["final_response_json"])
        except (json.JSONDecodeError, TypeError) as exc:
            raise AgentContextIntegrityError(
                "The previous turn response is invalid."
            ) from exc
        if not isinstance(response, dict):
            raise AgentContextIntegrityError(
                "The previous turn response is invalid."
            )
        trace = response.get("retrieval_trace")
        if trace is None:
            return ()
        if not isinstance(trace, dict) or set(trace) != {
            "schema_version",
            "selected_view_space_id",
            "corpus_closure_sha256",
            "documents",
        }:
            return ()
        if trace.get("schema_version") != 1:
            return ()
        if (
            trace.get("selected_view_space_id") != selected_view_space_id
            or trace.get("corpus_closure_sha256") != corpus_closure_sha256
        ):
            return ()
        documents = trace.get("documents")
        if not isinstance(documents, list) or len(documents) > 10:
            return ()
        result: list[RankedDocumentIdentity] = []
        seen: set[str] = set()
        for value in documents:
            if not isinstance(value, dict) or set(value) != {
                "item_id", "revision_id", "source_space_id"
            }:
                return ()
            try:
                identity = RankedDocumentIdentity(
                    item_id=_uuid(value.get("item_id"), "previous item_id"),
                    revision_id=_uuid(
                        value.get("revision_id"), "previous revision_id"
                    ),
                    source_space_id=_uuid(
                        value.get("source_space_id"), "previous source_space_id"
                    ),
                )
            except ValueError:
                return ()
            if identity.item_id in seen:
                return ()
            seen.add(identity.item_id)
            result.append(identity)
        return tuple(result)

    def _conversation_history(
        self,
        connection,
        *,
        snapshot: _TurnSnapshot,
    ) -> tuple[
        tuple[AgentConversationMessage, ...],
        AgentConversationSummary | None,
    ]:
        summary = self._conversations.get_active_summary(
            connection,
            conversation_id=snapshot.conversation_id,
            owner_user_id=snapshot.actor.id,
        )
        covered = 0 if summary is None else summary.covered_through_sequence_no
        after = max(covered, snapshot.user_message_sequence_no - 24)
        records = self._conversations.list_messages(
            connection,
            conversation_id=snapshot.conversation_id,
            owner_user_id=snapshot.actor.id,
            after_sequence_no=after,
            limit=25,
        )
        if not records or records[-1].sequence_no != snapshot.user_message_sequence_no:
            raise AgentContextIntegrityError(
                "The accepted turn is outside the bounded conversation history."
            )
        messages: list[AgentConversationMessage] = []
        for record in records:
            try:
                envelope = record.content
            except (ConversationRepositoryError, TypeError, ValueError) as exc:
                raise AgentContextIntegrityError(
                    "A recent conversation message is invalid."
                ) from exc
            content = envelope.get("content")
            if (
                envelope.get("role") != record.role
                or record.role not in {"system", "user", "assistant", "tool"}
                or not isinstance(content, dict)
            ):
                raise AgentContextIntegrityError(
                    "A recent conversation message envelope is invalid."
                )
            text = content.get("text")
            if not isinstance(text, str) or not text or len(text) > 32_000:
                raise AgentContextIntegrityError(
                    "A recent conversation message has no bounded text."
                )
            messages.append(
                AgentConversationMessage(
                    sequence_no=record.sequence_no,
                    role=record.role,
                    text=text,
                )
            )
        active_summary = None
        if summary is not None:
            active_summary = AgentConversationSummary(
                covered_through_sequence_no=summary.covered_through_sequence_no,
                summary_md=summary.summary_md,
                summary_sha256=summary.summary_sha256,
            )
        return tuple(messages), active_summary

    def _resolve_bindings(
        self, snapshot: _TurnSnapshot, epoch: int
    ) -> tuple[AgentBoundContextResolution, ...]:
        result: list[AgentBoundContextResolution] = []
        total_bytes = 0
        for binding in snapshot.bindings:
            request = AgentBoundContextResolveRequest(
                actor_user_id=snapshot.actor.id,
                actor_scope_epoch=epoch,
                conversation_id=snapshot.conversation_id,
                space_id=snapshot.space_id,
                binding_id=binding.id,
                binding_row_version=binding.row_version,
                binding_type=binding.binding_type,
                binding_mode=binding.binding_mode,
                external_key=binding.external_key,
                source_artifact_id=binding.source_artifact_id,
                snapshot_sha256=binding.snapshot_sha256,
            )
            resolver = self._bindings
            if resolver is None:
                if binding.binding_mode == "snapshot":
                    raise AgentContextResolverUnavailableError(
                        "An immutable context materializer is not configured."
                    )
                resolution = self._unavailable_resolution(
                    request, "resolver_unavailable"
                )
            else:
                try:
                    resolution = resolver.resolve(request)
                except Exception as exc:
                    if binding.binding_mode == "snapshot":
                        raise AgentContextResolverUnavailableError(
                            "The immutable context could not be materialized."
                        ) from exc
                    resolution = self._unavailable_resolution(
                        request, "resolver_unavailable"
                    )
            resolution, size = self._validate_resolution(request, resolution)
            total_bytes += size
            if total_bytes > _MAX_TOTAL_CONTEXT_BYTES:
                raise AgentContextIntegrityError(
                    "Resolved context exceeds the per-turn byte bound."
                )
            result.append(resolution)
        return tuple(result)

    @staticmethod
    def _unavailable_resolution(
        request: AgentBoundContextResolveRequest,
        reason: LiveUnavailableReason,
    ) -> AgentBoundContextResolution:
        return AgentBoundContextResolution(
            conversation_id=request.conversation_id,
            space_id=request.space_id,
            binding_id=request.binding_id,
            binding_row_version=request.binding_row_version,
            binding_type=request.binding_type,
            binding_mode=request.binding_mode,
            external_key=request.external_key,
            availability="unavailable",
            content_md=None,
            content_sha256=None,
            evidence_sha256=None,
            unavailable_reason=reason,
        )

    @staticmethod
    def _validate_resolution(
        request: AgentBoundContextResolveRequest,
        value: object,
    ) -> tuple[AgentBoundContextResolution, int]:
        if not isinstance(value, AgentBoundContextResolution):
            raise AgentContextIntegrityError(
                "The context resolver returned an invalid result."
            )
        if (
            value.conversation_id != request.conversation_id
            or value.space_id != request.space_id
            or value.binding_id != request.binding_id
            or value.binding_row_version != request.binding_row_version
            or value.binding_type != request.binding_type
            or value.binding_mode != request.binding_mode
            or value.external_key != request.external_key
        ):
            raise AgentContextIntegrityError(
                "The context resolver changed binding identity or scope."
            )
        if value.availability == "unavailable":
            if (
                request.binding_mode != "live"
                or value.content_md is not None
                or value.content_sha256 is not None
                or value.evidence_sha256 is not None
                or value.unavailable_reason
                not in {"access_revoked", "resolver_unavailable", "source_unavailable"}
            ):
                raise AgentContextIntegrityError(
                    "The unavailable context result is invalid."
                )
            return value, 0
        if value.availability != "available" or value.unavailable_reason is not None:
            raise AgentContextIntegrityError("The context availability is invalid.")
        content, observed_sha, size = _safe_content(value.content_md)
        content_sha = _sha(value.content_sha256, "content SHA-256")
        evidence_sha = _sha(value.evidence_sha256, "evidence SHA-256")
        if content_sha != observed_sha:
            raise AgentContextIntegrityError(
                "Resolved context bytes do not match their SHA-256."
            )
        if evidence_sha != content_sha:
            raise AgentContextIntegrityError(
                "Resolved context evidence is not bound to the returned bytes."
            )
        if (
            request.binding_mode == "snapshot"
            and evidence_sha != request.snapshot_sha256
        ):
            raise AgentContextIntegrityError(
                "Resolved snapshot evidence changed after binding."
            )
        return (
            AgentBoundContextResolution(
                conversation_id=value.conversation_id,
                space_id=value.space_id,
                binding_id=value.binding_id,
                binding_row_version=value.binding_row_version,
                binding_type=value.binding_type,
                binding_mode=value.binding_mode,
                external_key=value.external_key,
                availability="available",
                content_md=content,
                content_sha256=content_sha,
                evidence_sha256=evidence_sha,
                unavailable_reason=None,
            ),
            size,
        )

    def _validate_final_snapshot(
        self,
        connection,
        *,
        initial: _TurnSnapshot,
        actor_id: str,
        epoch: int,
        conversation_id: str,
        turn_id: str,
    ) -> _TurnSnapshot:
        current = self._snapshot_in_transaction(
            connection,
            actor_id=actor_id,
            epoch=epoch,
            conversation_id=conversation_id,
            turn_id=turn_id,
        )
        stable_turn_fields = (
            "id",
            "conversation_id",
            "actor_user_id",
            "operation_id",
            "request_sha256",
            "user_message_id",
        )
        if (
            current.space_id != initial.space_id
            or current.access_plan != initial.access_plan
            or current.request_id != initial.request_id
            or current.query != initial.query
            or current.bindings != initial.bindings
            or current.previous_retrieval_documents
            != initial.previous_retrieval_documents
            or any(
                getattr(current.turn, field) != getattr(initial.turn, field)
                for field in stable_turn_fields
            )
        ):
            raise AgentContextStateConflictError(
                "The conversation context changed during external resolution."
            )
        return current

    def _record_and_build_contexts(
        self,
        connection,
        *,
        snapshot: _TurnSnapshot,
        resolutions: tuple[AgentBoundContextResolution, ...],
        request_id: str,
    ) -> tuple[AgentBoundContext, ...]:
        now_ms = db_now_ms(connection)
        output: list[AgentBoundContext] = []
        for binding, resolution in zip(
            snapshot.bindings, resolutions, strict=True
        ):
            current = binding
            if binding.binding_mode == "live":
                available = resolution.availability == "available"
                updated = self._conversations.set_live_context_binding_availability(
                    connection,
                    binding_id=binding.id,
                    conversation_id=snapshot.conversation_id,
                    owner_user_id=snapshot.actor.id,
                    expected_row_version=binding.row_version,
                    available=available,
                    unavailable_reason=resolution.unavailable_reason,
                    changed_at_ms=now_ms,
                )
                current = updated.binding
                if updated.changed:
                    self._append_availability_events(
                        connection,
                        actor=snapshot.actor,
                        turn=snapshot.turn,
                        binding=current,
                        request_id=request_id,
                        space_id=snapshot.space_id,
                        now_ms=now_ms,
                    )
            output.append(
                AgentBoundContext(
                    binding_id=binding.id,
                    binding_row_version=current.row_version,
                    binding_type=binding.binding_type,
                    binding_mode=binding.binding_mode,
                    external_key=binding.external_key,
                    display_name=_display_name(binding),
                    source_artifact_id=binding.source_artifact_id,
                    snapshot_sha256=binding.snapshot_sha256,
                    availability=resolution.availability,
                    content_md=resolution.content_md,
                    content_sha256=resolution.content_sha256,
                    evidence_sha256=resolution.evidence_sha256,
                    unavailable_reason=resolution.unavailable_reason,
                )
            )
        return tuple(output)

    def _append_availability_events(
        self,
        connection,
        *,
        actor: UserRecord,
        turn: ConversationTurnRecord,
        binding: ConversationContextBindingRecord,
        request_id: str,
        space_id: str,
        now_ms: int,
    ) -> None:
        event_uuid = str(
            uuid5(
                _AVAILABILITY_AUDIT_NAMESPACE,
                f"{binding.id}:{binding.row_version}:{binding.status}",
            )
        )
        self._audit.append(
            connection,
            AuditEventDraft(
                event_uuid=event_uuid,
                occurred_at_ms=now_ms,
                request_id=request_id,
                operation_id=turn.operation_id,
                action="conversation.context.availability",
                outcome=binding.status,
                actor_kind="user",
                actor_user_id=actor.id,
                actor_display_name=actor.display_name,
                actor_role_snapshot=actor.system_role,
                space_id=space_id,
                reason=binding.status_reason,
                details={
                    "binding_id": binding.id,
                    "binding_mode": binding.binding_mode,
                    "binding_type": binding.binding_type,
                    "conversation_id": binding.conversation_id,
                    "row_version": binding.row_version,
                    "schema_version": 1,
                    "status": binding.status,
                    "turn_id": turn.id,
                },
            ),
        )
        self._outbox.append(
            connection,
            OutboxEventDraft(
                logical_key=(
                    f"conversation-context:{binding.id}:availability:"
                    f"{binding.row_version}"
                ),
                space_id=space_id,
                aggregate_type="conversation_context_binding",
                aggregate_id=binding.id,
                aggregate_version=binding.row_version,
                event_type="conversation.context.availability_changed",
                payload={
                    "binding_id": binding.id,
                    "conversation_id": binding.conversation_id,
                    "row_version": binding.row_version,
                    "schema_version": 1,
                    "status": binding.status,
                    "turn_id": turn.id,
                },
                available_at_ms=now_ms,
            ),
        )


__all__ = [
    "AgentBoundContext",
    "AgentBoundContextResolveRequest",
    "AgentBoundContextResolution",
    "AgentBoundContextResolver",
    "AgentContextError",
    "AgentContextIntegrityError",
    "AgentContextCorpusClosureStaleError",
    "AgentContextNotFoundError",
    "AgentContextResolverUnavailableError",
    "AgentContextScopeStaleError",
    "AgentContextStateConflictError",
    "AgentContextWikiArchivedHistoryOnlyError",
    "AgentConversationMessage",
    "AgentConversationSummary",
    "AgentRankedContext",
    "AgentSourceArtifact",
    "AgentTurnContextBundle",
    "AgentTurnContextRequest",
    "AgentTurnContextService",
]
