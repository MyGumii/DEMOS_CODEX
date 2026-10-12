"""P3.6 read-only scope-selector and immutable conversation-badge model.

The selector deliberately separates a management screen's requested space
from the space permanently captured by an existing conversation.  Both views
are resolved from one read-only database snapshot.  Starting a conversation is
still the responsibility of :class:`ConversationService`; there is no command
here that can mutate a conversation's ``space_id``.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import re
from typing import Iterator

from ..db import ConnectionFactory, ManagedConnection, TransactionMode, transaction
from ..errors import KnowledgeCoreError
from ..repositories.conversations import (
    ConversationAccessStatus,
    ConversationNotFoundError,
    ConversationRecord,
    ConversationRepository,
)
from ..repositories.organization import OrganizationRepository
from ..repositories.personal_wikis import (
    PersonalWikiProfileRecord,
    PersonalWikiProfileRepository,
)
from ..repositories.spaces import SpaceRecord, SpaceRepository
from ..scope import (
    Capability,
    ScopeResolutionError,
    ScopeResolver,
    SessionPrincipal,
    SpaceSummary,
)


_USER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$", re.ASCII)
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.ASCII,
)


class ScopeSelectionError(KnowledgeCoreError):
    code = "SCOPE_SELECTION_ERROR"


class ScopeSelectionNotFoundError(ScopeSelectionError):
    code = "NOT_FOUND"
    http_status = 404


class ScopeSelectionScopeError(ScopeSelectionError):
    code = "SCOPE_STALE"
    http_status = 409


class ScopeSelectionIntegrityError(ScopeSelectionError):
    code = "INTERNAL_ERROR"
    http_status = 500


@dataclass(frozen=True, slots=True)
class ScopeSelectionRequest:
    actor_user_id: str
    actor_scope_epoch: int
    selected_space_id: str | None = None
    conversation_id: str | None = None


@dataclass(frozen=True, slots=True)
class HigherNativeAuthority:
    native_space_id: str
    role_assignment_id: str
    role_assignment_generation: int
    capabilities: tuple[str, ...]
    publication_target: dict[str, object] | None

    def as_dict(self) -> dict[str, object]:
        return {
            "capabilities": list(self.capabilities),
            "native_space_id": self.native_space_id,
            "publication_target": self.publication_target,
            "role_assignment_generation": self.role_assignment_generation,
            "role_assignment_id": self.role_assignment_id,
        }


@dataclass(frozen=True, slots=True)
class ScopeSelectionOption:
    id: str
    kind: str
    name: str
    org_path: tuple[str, ...]
    capabilities: tuple[str, ...]
    selected: bool
    history_only: bool = False
    successor_binding: bool = False
    native_authority: HigherNativeAuthority | None = None

    def as_dict(self) -> dict[str, object]:
        value: dict[str, object] = {
            "capabilities": list(self.capabilities),
            "id": self.id,
            "kind": self.kind,
            "name": self.name,
            "org_path": list(self.org_path),
            "selected": self.selected,
        }
        if self.successor_binding:
            value["history_only"] = self.history_only
        if self.native_authority is not None:
            value["native_authority"] = self.native_authority.as_dict()
        return value


@dataclass(frozen=True, slots=True)
class ConversationSpaceBadge:
    conversation_id: str
    conversation_status: str
    conversation_title: str
    conversation_row_version: int
    space_id: str
    space_kind: str
    space_name: str
    org_path: tuple[str, ...]
    access_status: ConversationAccessStatus
    accessible: bool
    history_only: bool = False
    wiki_lifecycle_generation: int | None = None
    successor_binding: bool = False
    immutable: bool = True

    def as_dict(self) -> dict[str, object]:
        value: dict[str, object] = {
            "access_status": self.access_status,
            "accessible": self.accessible,
            "conversation_id": self.conversation_id,
            "conversation_row_version": self.conversation_row_version,
            "conversation_status": self.conversation_status,
            "conversation_title": self.conversation_title,
            "immutable": self.immutable,
            "org_path": list(self.org_path),
            "space_id": self.space_id,
            "space_kind": self.space_kind,
            "space_name": self.space_name,
        }
        if self.successor_binding:
            value.update(
                {
                    "history_only": self.history_only,
                    "selected_view_space_id": self.space_id,
                    "wiki_lifecycle_generation": self.wiki_lifecycle_generation,
                }
            )
        return value


@dataclass(frozen=True, slots=True)
class ScopeSelectionSnapshot:
    actor_user_id: str
    scope_epoch: int
    selected_space_id: str
    spaces: tuple[ScopeSelectionOption, ...]
    conversation_badge: ConversationSpaceBadge | None

    def as_dict(self) -> dict[str, object]:
        return {
            "actor_user_id": self.actor_user_id,
            "conversation_badge": (
                None
                if self.conversation_badge is None
                else self.conversation_badge.as_dict()
            ),
            "scope_epoch": self.scope_epoch,
            "selected_space_id": self.selected_space_id,
            "spaces": [space.as_dict() for space in self.spaces],
        }


def _actor(value: object) -> str:
    if not isinstance(value, str) or _USER_RE.fullmatch(value) is None:
        raise ValueError("actor_user_id is invalid.")
    return value


def _positive(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{field} must be a positive integer.")
    return value


def _optional_uuid(value: object, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or _UUID_RE.fullmatch(value) is None:
        raise ValueError(f"{field} must be a canonical UUID or null.")
    return value


def _option(
    summary: SpaceSummary,
    selected_space_id: str,
    *,
    history_only: bool = False,
    successor_binding: bool = False,
    native_authority: HigherNativeAuthority | None = None,
) -> ScopeSelectionOption:
    return ScopeSelectionOption(
        id=summary.id,
        kind=summary.kind,
        name=summary.name,
        org_path=summary.org_path,
        capabilities=tuple(capability.value for capability in summary.capabilities),
        selected=summary.id == selected_space_id,
        history_only=history_only,
        successor_binding=successor_binding,
        native_authority=native_authority,
    )


class ScopeSelectionService:
    """Build one bounded selector snapshot without changing UI or DB state."""

    __slots__ = (
        "_conversations",
        "_factory",
        "_organizations",
        "_personal_wikis",
        "_scopes",
        "_spaces",
    )

    def __init__(
        self,
        factory: ConnectionFactory,
        *,
        scope_resolver: ScopeResolver,
        conversations: ConversationRepository | None = None,
        spaces: SpaceRepository | None = None,
        organizations: OrganizationRepository | None = None,
        personal_wikis: PersonalWikiProfileRepository | None = None,
    ) -> None:
        if not isinstance(factory, ConnectionFactory):
            raise TypeError("ScopeSelectionService requires a ConnectionFactory.")
        if not isinstance(scope_resolver, ScopeResolver):
            raise TypeError("ScopeSelectionService requires a ScopeResolver.")
        self._factory = factory
        self._scopes = scope_resolver
        self._conversations = conversations or ConversationRepository()
        self._spaces = spaces or SpaceRepository()
        self._organizations = organizations or OrganizationRepository()
        self._personal_wikis = personal_wikis or PersonalWikiProfileRepository()

    @contextmanager
    def _snapshot(self) -> Iterator[ManagedConnection]:
        connection = self._factory.open(read_only=True)
        try:
            with transaction(connection, TransactionMode.DEFERRED):
                yield connection
        finally:
            connection.close()

    def read(self, request: ScopeSelectionRequest) -> ScopeSelectionSnapshot:
        if not isinstance(request, ScopeSelectionRequest):
            raise TypeError("A ScopeSelectionRequest is required.")
        actor = _actor(request.actor_user_id)
        epoch = _positive(request.actor_scope_epoch, "actor_scope_epoch")
        selected_request = _optional_uuid(
            request.selected_space_id, "selected_space_id"
        )
        conversation_id = _optional_uuid(request.conversation_id, "conversation_id")

        with self._snapshot() as connection:
            try:
                selectable = self._scopes.list_selectable_in_transaction(
                    connection,
                    SessionPrincipal(actor, epoch),
                )
            except ScopeResolutionError as exc:
                raise ScopeSelectionScopeError(
                    "The authenticated scope selector is stale or unavailable."
                ) from exc
            if selectable.actor_user_id != actor or selectable.scope_epoch != epoch:
                raise ScopeSelectionIntegrityError(
                    "The scope selector actor binding is inconsistent."
                )
            summaries = list(selectable.items)
            available = {space.id: space for space in summaries}
            personal = next(
                (space for space in selectable.items if space.kind == "personal"),
                None,
            )
            if personal is None:
                raise ScopeSelectionIntegrityError(
                    "The authenticated actor has no selectable personal space."
                )

            conversation: ConversationRecord | None = None
            badge: ConversationSpaceBadge | None = None
            if conversation_id is not None:
                try:
                    conversation = self._conversations.require_owned(
                        connection,
                        conversation_id=conversation_id,
                        owner_user_id=actor,
                    )
                    access_status = self._conversations.access_status(
                        connection,
                        conversation_id=conversation.id,
                        owner_user_id=actor,
                    )
                except ConversationNotFoundError as exc:
                    raise ScopeSelectionNotFoundError(
                        "The owner-bound conversation was not found."
                    ) from exc
                badge = self._conversation_badge(
                    connection,
                    conversation,
                    access_status,
                )

            binding_enabled = self._conversations.conversation_wiki_binding
            if binding_enabled and conversation is not None:
                # Existing sessions are rebound only by creating a new
                # conversation. Browser/dropdown state is never authority.
                selected = conversation.space_id
                if selected not in available:
                    exact = self._exact_conversation_summary(
                        connection,
                        actor=actor,
                        epoch=epoch,
                        conversation=conversation,
                        badge=badge,
                    )
                    summaries.append(exact)
                    available[exact.id] = exact
            elif binding_enabled and selected_request is not None:
                selected = selected_request
                if selected not in available:
                    exact = self._exact_active_summary(
                        connection,
                        actor=actor,
                        epoch=epoch,
                        space_id=selected,
                    )
                    summaries.append(exact)
                    available[exact.id] = exact
            elif selected_request is not None:
                if selected_request not in available:
                    raise ScopeSelectionNotFoundError(
                        "The selected knowledge space was not found."
                    )
                selected = selected_request
            elif conversation is not None and conversation.space_id in available:
                selected = conversation.space_id
            else:
                selected = personal.id

            options_list: list[ScopeSelectionOption] = []
            for space in summaries:
                authority = self._higher_native_authority(
                    connection,
                    actor=actor,
                    epoch=epoch,
                    summary=space,
                )
                options_list.append(
                    _option(
                        space,
                        selected,
                        history_only=(
                            binding_enabled
                            and badge is not None
                            and badge.space_id == space.id
                            and badge.history_only
                        ),
                        successor_binding=binding_enabled,
                        native_authority=authority,
                    )
                )
            options = tuple(options_list)
            if sum(option.selected for option in options) != 1:
                raise ScopeSelectionIntegrityError(
                    "The scope selector has no exact selected option."
                )
            return ScopeSelectionSnapshot(
                actor_user_id=actor,
                scope_epoch=epoch,
                selected_space_id=selected,
                spaces=options,
                conversation_badge=badge,
            )

    def _higher_native_authority(
        self,
        connection: ManagedConnection,
        *,
        actor: str,
        epoch: int,
        summary: SpaceSummary,
    ) -> HigherNativeAuthority | None:
        if summary.kind not in {"team", "responsibility"}:
            return None
        try:
            plan = self._scopes.resolve_access_plan_in_transaction(
                connection,
                SessionPrincipal(actor, epoch),
                summary.id,
            )
        except ScopeResolutionError as exc:
            raise ScopeSelectionScopeError(
                "The higher native authority changed during scope selection."
            ) from exc
        if (
            plan.role_assignment_id is None
            or plan.role_assignment_generation is None
        ):
            return None
        capabilities = tuple(
            capability.value for capability in plan.native_capabilities
        )
        publication_target = None
        if Capability.PUBLISH in plan.native_capabilities:
            publication_target = {
                "capability": "publish",
                "kind": summary.kind,
                "role_assignment_generation": plan.role_assignment_generation,
                "role_assignment_id": plan.role_assignment_id,
                "space_id": summary.id,
            }
        return HigherNativeAuthority(
            native_space_id=summary.id,
            role_assignment_id=plan.role_assignment_id,
            role_assignment_generation=plan.role_assignment_generation,
            capabilities=capabilities,
            publication_target=publication_target,
        )

    def _conversation_badge(
        self,
        connection: ManagedConnection,
        conversation: ConversationRecord,
        access_status: ConversationAccessStatus,
    ) -> ConversationSpaceBadge:
        space = self._spaces.get_by_id(connection, conversation.space_id)
        if space is None:
            raise ScopeSelectionIntegrityError(
                "The conversation's immutable space is missing."
            )
        name, path = self._space_label(connection, space)
        return ConversationSpaceBadge(
            conversation_id=conversation.id,
            conversation_status=conversation.status,
            conversation_title=conversation.title,
            conversation_row_version=conversation.row_version,
            space_id=space.id,
            space_kind=space.space_kind,
            space_name=name,
            org_path=path,
            access_status=access_status,
            accessible=access_status == "accessible",
            history_only=access_status == "wiki_archived",
            wiki_lifecycle_generation=self._wiki_generation(connection, space),
            successor_binding=self._conversations.conversation_wiki_binding,
        )

    def _exact_active_summary(
        self,
        connection: ManagedConnection,
        *,
        actor: str,
        epoch: int,
        space_id: str,
    ) -> SpaceSummary:
        try:
            plan = self._scopes.resolve_access_plan_in_transaction(
                connection,
                SessionPrincipal(actor, epoch),
                space_id,
            )
        except ScopeResolutionError as exc:
            raise ScopeSelectionNotFoundError(
                "The selected knowledge space was not found."
            ) from exc
        return plan.selected_view

    def _exact_conversation_summary(
        self,
        connection: ManagedConnection,
        *,
        actor: str,
        epoch: int,
        conversation: ConversationRecord,
        badge: ConversationSpaceBadge | None,
    ) -> SpaceSummary:
        if badge is None:
            raise ScopeSelectionIntegrityError(
                "The conversation badge is unavailable."
            )
        if badge.access_status == "accessible":
            return self._exact_active_summary(
                connection,
                actor=actor,
                epoch=epoch,
                space_id=conversation.space_id,
            )
        return SpaceSummary(
            id=badge.space_id,
            kind=badge.space_kind,  # type: ignore[arg-type]
            name=badge.space_name,
            org_path=badge.org_path,
            capabilities=(),
        )

    def _wiki_generation(
        self,
        connection: ManagedConnection,
        space: SpaceRecord,
    ) -> int | None:
        if (
            not self._conversations.conversation_wiki_binding
            or space.space_kind != "personal"
        ):
            return None
        profile = self._personal_wikis.get_by_space_id(connection, space.id)
        return None if profile is None else profile.lifecycle_generation

    def _space_label(
        self,
        connection: ManagedConnection,
        space: SpaceRecord,
    ) -> tuple[str, tuple[str, ...]]:
        if space.space_kind == "personal":
            if space.owner_user_id is None or space.owner_org_unit_id is not None:
                raise ScopeSelectionIntegrityError(
                    "The conversation personal-space owner is invalid."
                )
            profile: PersonalWikiProfileRecord | None = None
            if self._conversations.conversation_wiki_binding:
                profile = self._personal_wikis.get_by_space_id(connection, space.id)
                if (
                    profile is None
                    or profile.owner_user_id != space.owner_user_id
                ):
                    raise ScopeSelectionIntegrityError(
                        "The conversation personal-Wiki profile is invalid."
                    )
            return (
                "내 지식" if profile is None else profile.display_name,
                (),
            )
        if (
            space.space_kind not in {"part", "team", "responsibility"}
            or space.owner_org_unit_id is None
        ):
            raise ScopeSelectionIntegrityError(
                "The conversation space kind is unsupported."
            )
        unit = self._organizations.get_unit(connection, space.owner_org_unit_id)
        if unit is None or unit.kind != space.space_kind:
            raise ScopeSelectionIntegrityError(
                "The conversation organization-space path is invalid."
            )
        if space.space_kind == "responsibility":
            if unit.parent_id is not None:
                raise ScopeSelectionIntegrityError(
                    "The conversation responsibility-space path is invalid."
                )
            return unit.name, (unit.name,)
        if space.space_kind == "team":
            if unit.parent_id is None:
                raise ScopeSelectionIntegrityError(
                    "The conversation team-space path is invalid."
                )
            responsibility = self._organizations.get_unit(connection, unit.parent_id)
            if responsibility is None or responsibility.kind != "responsibility":
                raise ScopeSelectionIntegrityError(
                    "The conversation team-space path is invalid."
                )
            return unit.name, (responsibility.name, unit.name)
        part = unit
        if part is None or part.kind != "part" or part.parent_id is None:
            raise ScopeSelectionIntegrityError(
                "The conversation part-space path is invalid."
            )
        team = self._organizations.get_unit(connection, part.parent_id)
        if team is None or team.kind != "team" or team.parent_id is None:
            raise ScopeSelectionIntegrityError(
                "The conversation part-space path is invalid."
            )
        responsibility = self._organizations.get_unit(connection, team.parent_id)
        if (
            responsibility is None
            or responsibility.kind != "responsibility"
            or responsibility.parent_id is not None
        ):
            raise ScopeSelectionIntegrityError(
                "The conversation part-space path is invalid."
            )
        return part.name, (responsibility.name, team.name, part.name)


__all__ = [
    "HigherNativeAuthority",
    "ConversationSpaceBadge",
    "ScopeSelectionError",
    "ScopeSelectionIntegrityError",
    "ScopeSelectionNotFoundError",
    "ScopeSelectionOption",
    "ScopeSelectionRequest",
    "ScopeSelectionScopeError",
    "ScopeSelectionService",
    "ScopeSelectionSnapshot",
]
