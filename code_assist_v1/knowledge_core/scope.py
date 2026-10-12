"""Canonical request-scope and capability resolution.

The resolver is deliberately framework and configuration independent.  A host
passes an immutable rollout snapshot and an authenticated ``SessionPrincipal``;
the latter contains no role, part, or capability claims.  Every authoritative
fact is reread from the canonical database inside a transaction owned by the
caller.

P1.7 exposes only an actor's personal space and current-part space.  It never
creates a missing space, grants an administrator an arbitrary Agent scope, or
exposes the schema-supported team/responsibility space kinds.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from enum import StrEnum
from hashlib import sha256
import json
import re
from typing import Literal

from .db import ManagedConnection
from .domain import FeatureFlags
from .errors import KnowledgeCoreError, KnowledgeCoreTransactionError
from .repositories.organization import (
    LeaderAssignmentRecord,
    MembershipRecord,
    OrganizationRepository,
    OrgUnitRecord,
)
from .repositories.affiliations import (
    OrgAffiliationRepository,
    OrgHomeAffiliationRecord,
)
from .repositories.higher_spaces import (
    HigherScopeRoleAssignmentRecord,
    HigherSpaceRepository,
    KnowledgeDistributionGrantRecord,
)
from .repositories.personal_wikis import (
    PersonalWikiProfileRecord,
    PersonalWikiProfileRepository,
)
from .repositories.spaces import SpaceRecord, SpaceRepository
from .repositories.users import UserRecord, UserRepository


SpaceKind = Literal["personal", "part", "team", "responsibility"]
SystemRole = Literal["admin", "user"]

_USER_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$", re.ASCII)
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.ASCII,
)


class Capability(StrEnum):
    """Canonical capability vocabulary and stable display order."""

    READ = "read"
    PUBLISH = "publish"
    EDIT = "edit"
    DEACTIVATE = "deactivate"
    RESTORE = "restore"
    CURATE = "curate"
    TEMPORAL_MUTATE = "temporal_mutate"
    LOCAL_EDGE_CORRECT = "local_edge_correct"
    AGGREGATE_EDGE_CORRECT = "aggregate_edge_correct"
    DISTRIBUTION_GRANT = "distribution_grant"
    DISTRIBUTION_REVOKE = "distribution_revoke"
    FORCE_RELEASE = "force_release"
    PURGE_ELIGIBLE = "purge_eligible"


_CAPABILITY_ORDER = tuple(Capability)
_ORDINARY_MUTATIONS = (
    Capability.PUBLISH,
    Capability.EDIT,
    Capability.DEACTIVATE,
    Capability.RESTORE,
    Capability.CURATE,
    Capability.TEMPORAL_MUTATE,
    Capability.LOCAL_EDGE_CORRECT,
)
_HIGHER_NATIVE_MUTATIONS = (
    *_ORDINARY_MUTATIONS,
    Capability.LOCAL_EDGE_CORRECT,
    Capability.AGGREGATE_EDGE_CORRECT,
    Capability.DISTRIBUTION_GRANT,
    Capability.DISTRIBUTION_REVOKE,
    Capability.FORCE_RELEASE,
)
_MAX_CLOSURE_SOURCES = 4096
_MAX_CLOSURE_OVERLAYS = 4096


class ScopeResolutionError(KnowledgeCoreError):
    """Base class for stable, framework-neutral scope failures."""

    code = "INTERNAL_ERROR"
    http_status = 500
    retryable = False

    def as_error(self) -> dict[str, object]:
        return {
            "code": self.code,
            "message": str(self),
            "retryable": self.retryable,
        }


class ScopeValidationError(ScopeResolutionError):
    code = "VALIDATION_ERROR"
    http_status = 400


class ScopeAuthenticationRequiredError(ScopeResolutionError):
    code = "AUTHENTICATION_REQUIRED"
    http_status = 401


class ScopeStaleError(ScopeResolutionError):
    code = "SCOPE_STALE"
    http_status = 409


class ScopeNotFoundError(ScopeResolutionError):
    code = "NOT_FOUND"
    http_status = 404


class ScopeForbiddenError(ScopeResolutionError):
    code = "FORBIDDEN"
    http_status = 403


class ScopeIntegrityError(ScopeResolutionError):
    code = "INTERNAL_ERROR"


@dataclass(frozen=True, slots=True)
class SessionPrincipal:
    """The complete trusted input copied from an authenticated session."""

    user_id: str
    captured_scope_epoch: int


@dataclass(frozen=True, slots=True)
class SpaceSummary:
    id: str
    kind: SpaceKind
    name: str
    org_path: tuple[str, ...]
    capabilities: tuple[Capability, ...]


@dataclass(frozen=True, slots=True)
class CorpusSourceDescriptor:
    source_space_id: str
    kind: SpaceKind
    origin: Literal["native", "inherited"]
    inherited: bool
    capabilities: tuple[Capability, ...]
    space_generation: int
    space_row_version: int


@dataclass(frozen=True, slots=True)
class DistributionOverlayDescriptor:
    grant_id: str
    source_space_id: str
    source_item_id: str
    source_revision_id: str
    target_org_unit_id: str
    target_scope_kind: Literal["org_unit", "native_space"]
    target_scope_id: str
    capabilities: tuple[Capability, ...]
    grant_row_version: int


@dataclass(frozen=True, slots=True)
class CorpusClosure:
    selected_view_space_id: str
    sources: tuple[CorpusSourceDescriptor, ...]
    distribution_overlays: tuple[DistributionOverlayDescriptor, ...]
    organization_snapshot_version: int
    generation: int
    sha256: str


@dataclass(frozen=True, slots=True)
class AccessPlan:
    actor_user_id: str
    system_role: SystemRole
    scope_epoch: int
    selected_view: SpaceSummary
    corpus_closure: CorpusClosure
    native_mutation_space_id: str | None
    native_capabilities: tuple[Capability, ...]
    role_assignment_id: str | None
    role_assignment_generation: int | None
    actor_row_version: int


@dataclass(frozen=True, slots=True)
class SelectableSpacesResult:
    actor_user_id: str
    scope_epoch: int
    items: tuple[SpaceSummary, ...]


@dataclass(frozen=True, slots=True)
class ResolvedScope:
    actor_user_id: str
    system_role: SystemRole
    current_part_id: str | None
    scope_epoch: int
    space: SpaceSummary
    is_part_leader: bool
    entitled_capabilities: tuple[Capability, ...]
    effective_capabilities: tuple[Capability, ...]
    actor_row_version: int
    space_row_version: int
    access_plan: AccessPlan | None = None


@dataclass(frozen=True, slots=True)
class _ActorSnapshot:
    user: UserRecord
    affiliation: OrgHomeAffiliationRecord | None
    membership: MembershipRecord | None
    part: OrgUnitRecord | None
    team: OrgUnitRecord | None
    responsibility: OrgUnitRecord | None
    leader: LeaderAssignmentRecord | None
    team_role: HigherScopeRoleAssignmentRecord | None
    responsibility_role: HigherScopeRoleAssignmentRecord | None

    @property
    def is_part_leader(self) -> bool:
        return self.leader is not None and self.leader.user_id == self.user.id

    @property
    def part_path(self) -> tuple[str, ...]:
        if self.part is None:
            return ()
        if self.team is None or self.responsibility is None:
            raise ScopeIntegrityError("The canonical organization path is invalid.")
        return (self.responsibility.name, self.team.name, self.part.name)

    @property
    def team_path(self) -> tuple[str, ...]:
        if self.team is None or self.responsibility is None:
            return ()
        return (self.responsibility.name, self.team.name)

    @property
    def responsibility_path(self) -> tuple[str, ...]:
        if self.responsibility is None:
            return ()
        return (self.responsibility.name,)


def _require_active_transaction(connection: ManagedConnection) -> ManagedConnection:
    if not isinstance(connection, ManagedConnection):
        raise TypeError("ScopeResolver requires a ManagedConnection.")
    if not connection.in_transaction:
        raise KnowledgeCoreTransactionError(
            "Scope resolution requires an active caller-owned transaction."
        )
    return connection


def _principal(value: object) -> SessionPrincipal:
    if not isinstance(value, SessionPrincipal):
        raise ScopeValidationError("A canonical session principal is required.")
    if not isinstance(value.user_id, str) or _USER_ID_RE.fullmatch(value.user_id) is None:
        raise ScopeValidationError("The session user ID is invalid.")
    epoch = value.captured_scope_epoch
    if isinstance(epoch, bool) or not isinstance(epoch, int) or epoch < 1:
        raise ScopeValidationError("The captured scope epoch is invalid.")
    return value


def _space_id(value: object) -> str:
    if not isinstance(value, str) or _UUID_RE.fullmatch(value) is None:
        raise ScopeValidationError("space_id must be a canonical lowercase UUID.")
    return value


def _required_capability(value: Capability | str) -> Capability:
    if value is None:
        raise ScopeValidationError("A required capability must be supplied.")
    try:
        return Capability(value)
    except (TypeError, ValueError) as exc:
        raise ScopeValidationError("The required capability is invalid.") from exc


def _validate_rollout_snapshot(value: object) -> FeatureFlags:
    if not isinstance(value, FeatureFlags):
        raise TypeError("ScopeResolver requires an immutable FeatureFlags snapshot.")
    for descriptor in fields(FeatureFlags):
        if type(getattr(value, descriptor.name)) is not bool:
            raise ValueError("Every rollout flag must be a Boolean.")
    if value.traffic_or_worker_enabled and not value.core_enabled:
        raise ValueError("Knowledge Core rollout flags require core_enabled=True.")
    if value.personal_writes and not value.personal_reads:
        raise ValueError("Personal writes require personal reads in the same snapshot.")
    if value.part_writes and not value.part_reads:
        raise ValueError("Part writes require part reads in the same snapshot.")
    if value.higher_spaces and not value.part_reads:
        raise ValueError("Higher spaces require Part reads in the same snapshot.")
    if value.aggregate_graph_reads and not value.higher_spaces:
        raise ValueError("Aggregate graph reads require higher-space reads.")
    if value.auto_semantic_edges_aggregate and not all(
        (value.auto_semantic_edges_local, value.aggregate_graph_reads)
    ):
        raise ValueError(
            "Aggregate automatic semantic edges require local automatic edges "
            "and aggregate graph reads."
        )
    if value.team_writes and not value.higher_spaces:
        raise ValueError("Team writes require higher-space reads.")
    if value.responsibility_writes and not value.higher_spaces:
        raise ValueError("Responsibility writes require higher-space reads.")
    if value.conversation_wiki_binding and not value.multi_wiki_reads:
        raise ValueError("Conversation-Wiki binding requires multi-Wiki reads.")
    return value


def _ordered_capabilities(values: set[Capability]) -> tuple[Capability, ...]:
    return tuple(capability for capability in _CAPABILITY_ORDER if capability in values)


class ScopeResolver:
    """Resolve canonical selected views and their server-owned corpus closure."""

    __slots__ = (
        "_flags",
        "_affiliations",
        "_higher",
        "_organizations",
        "_personal_wikis",
        "_spaces",
        "_users",
    )

    def __init__(self, flags: FeatureFlags = FeatureFlags()) -> None:
        self._flags = _validate_rollout_snapshot(flags)
        self._affiliations = OrgAffiliationRepository()
        self._users = UserRepository()
        self._organizations = OrganizationRepository()
        self._higher = HigherSpaceRepository()
        self._personal_wikis = PersonalWikiProfileRepository()
        self._spaces = SpaceRepository()

    @property
    def flags(self) -> FeatureFlags:
        return self._flags

    def resolve_in_transaction(
        self,
        connection: ManagedConnection,
        principal: SessionPrincipal,
        space_id: str,
        required_capability: Capability | str,
    ) -> ResolvedScope:
        """Resolve one requested space from the transaction's canonical snapshot.

        Missing, foreign, former-part, higher-level, and unavailable spaces use
        one intentionally undisclosed ``NOT_FOUND`` result.  An actor who owns
        the active scope but whose requested capability is disabled by rollout
        or space state receives ``FORBIDDEN``.
        """

        connection = _require_active_transaction(connection)
        principal = _principal(principal)
        space_id = _space_id(space_id)
        required = _required_capability(required_capability)

        actor = self._load_actor_snapshot(connection, principal)
        requested = self._spaces.get_by_id(connection, space_id)
        if requested is None:
            raise self._undisclosed()

        plan = self._build_access_plan(connection, actor=actor, selected=requested)
        if required not in plan.selected_view.capabilities:
            raise ScopeForbiddenError(
                "The requested capability is not effective for this scope."
            )
        entitled = self._entitled_capabilities(
            actor, plan.selected_view.kind, requested
        )
        return ResolvedScope(
            actor_user_id=actor.user.id,
            system_role=actor.user.system_role,
            current_part_id=actor.user.current_part_id,
            scope_epoch=actor.user.scope_epoch,
            space=plan.selected_view,
            is_part_leader=(
                plan.selected_view.kind == "part" and actor.is_part_leader
            ),
            entitled_capabilities=entitled,
            effective_capabilities=plan.selected_view.capabilities,
            actor_row_version=actor.user.row_version,
            space_row_version=requested.row_version,
            access_plan=plan,
        )

    def resolve_access_plan_in_transaction(
        self,
        connection: ManagedConnection,
        principal: SessionPrincipal,
        selected_view_space_id: str,
        required_capability: Capability | str = Capability.READ,
    ) -> AccessPlan:
        """Return the typed SBE-2.3 plan without trusting client closure input."""

        resolved = self.resolve_in_transaction(
            connection,
            principal,
            selected_view_space_id,
            required_capability,
        )
        if resolved.access_plan is None:
            raise ScopeIntegrityError("The canonical access plan is unavailable.")
        return resolved.access_plan

    def list_selectable_in_transaction(
        self,
        connection: ManagedConnection,
        principal: SessionPrincipal,
    ) -> SelectableSpacesResult:
        """List selectable personal, Part, Team, then Responsibility views."""

        connection = _require_active_transaction(connection)
        principal = _principal(principal)
        actor = self._load_actor_snapshot(connection, principal)
        summaries: list[SpaceSummary] = []

        personal = self._spaces.get_personal(connection, actor.user.id)
        if personal is None:
            raise ScopeIntegrityError(
                "The canonical active user has no provisioned personal space."
            )
        personal_summary = self._selectable_summary(actor, personal, "personal")
        if personal_summary is not None:
            if self._flags.multi_wiki_reads:
                profile = self._active_personal_wiki(
                    connection, actor=actor, selected=personal
                )
                personal_summary = SpaceSummary(
                    id=personal_summary.id,
                    kind=personal_summary.kind,
                    name=profile.display_name,
                    org_path=personal_summary.org_path,
                    capabilities=personal_summary.capabilities,
                )
            summaries.append(personal_summary)

        if actor.part is not None:
            part_space = self._spaces.get_part(connection, actor.part.id)
            part_summary = self._selectable_summary(actor, part_space, "part")
            if part_summary is not None:
                summaries.append(part_summary)

        if self._flags.higher_spaces and actor.team is not None:
            team_space = self._spaces.get_org(connection, actor.team.id)
            team_summary = self._selectable_summary(actor, team_space, "team")
            if team_summary is not None:
                summaries.append(team_summary)

        if self._flags.higher_spaces and actor.responsibility is not None:
            responsibility_space = self._spaces.get_org(
                connection, actor.responsibility.id
            )
            responsibility_summary = self._selectable_summary(
                actor, responsibility_space, "responsibility"
            )
            if responsibility_summary is not None:
                summaries.append(responsibility_summary)

        return SelectableSpacesResult(
            actor_user_id=actor.user.id,
            scope_epoch=actor.user.scope_epoch,
            items=tuple(summaries),
        )

    def _load_actor_snapshot(
        self,
        connection: ManagedConnection,
        principal: SessionPrincipal,
    ) -> _ActorSnapshot:
        user = self._users.get(connection, principal.user_id)
        if user is None or user.account_status != "active":
            raise ScopeAuthenticationRequiredError(
                "An active authenticated account is required."
            )
        if user.scope_epoch != principal.captured_scope_epoch:
            raise ScopeStaleError("The authenticated scope epoch is stale.")

        membership = self._organizations.get_current_membership(connection, user.id)
        affiliation = (
            self._affiliations.get_current(connection, user.id)
            if self._flags.higher_spaces
            else None
        )
        if user.current_part_id is None:
            if membership is not None:
                raise ScopeIntegrityError(
                    "The canonical user and membership scope disagree."
                )
            if affiliation is None:
                return _ActorSnapshot(
                    user=user,
                    affiliation=None,
                    membership=None,
                    part=None,
                    team=None,
                    responsibility=None,
                    leader=None,
                    team_role=None,
                    responsibility_role=None,
                )
            home = self._organizations.get_unit(connection, affiliation.org_unit_id)
            if (
                home is None
                or home.status != "active"
                or home.kind != affiliation.home_kind
                or affiliation.home_kind == "part"
            ):
                raise ScopeIntegrityError(
                    "The canonical direct organization affiliation is invalid."
                )
            part = None
            team = home if home.kind == "team" else None
            responsibility = home if home.kind == "responsibility" else None
            if team is not None:
                if team.parent_id is None:
                    raise ScopeIntegrityError(
                        "The canonical Team affiliation path is invalid."
                    )
                responsibility = self._organizations.get_unit(
                    connection, team.parent_id
                )
                if (
                    responsibility is None
                    or responsibility.kind != "responsibility"
                    or responsibility.status != "active"
                    or responsibility.parent_id is not None
                ):
                    raise ScopeIntegrityError(
                        "The canonical Team affiliation path is invalid."
                    )
            team_role = None
            responsibility_role = None
            if self._flags.higher_spaces:
                if team is not None:
                    team_role = self._higher.get_open_role(
                        connection, org_unit_id=team.id, role="team_leader"
                    )
                    if team_role is None or team_role.user_id != user.id:
                        raise ScopeIntegrityError(
                            "The direct Team affiliation has no exact leader episode."
                        )
                if responsibility is not None:
                    responsibility_role = self._higher.get_open_role(
                        connection,
                        org_unit_id=responsibility.id,
                        role="responsibility_executive",
                    )
                    if home.kind == "responsibility" and (
                        responsibility_role is None
                        or responsibility_role.user_id != user.id
                    ):
                        raise ScopeIntegrityError(
                            "The direct Responsibility affiliation has no exact executive episode."
                        )
            return _ActorSnapshot(
                user=user,
                affiliation=affiliation,
                membership=None,
                part=part,
                team=team,
                responsibility=responsibility,
                leader=None,
                team_role=team_role,
                responsibility_role=responsibility_role,
            )

        if membership is None or membership.part_id != user.current_part_id:
            raise ScopeIntegrityError("The canonical user and membership scope disagree.")
        if self._flags.higher_spaces and (
            affiliation is None
            or affiliation.home_kind != "part"
            or affiliation.org_unit_id != user.current_part_id
            or affiliation.id != membership.id
        ):
            raise ScopeIntegrityError(
                "The canonical Part affiliation and membership disagree."
            )

        part = self._organizations.get_unit(connection, user.current_part_id)
        if part is None or part.kind != "part" or part.status != "active":
            raise ScopeIntegrityError("The canonical current part is invalid.")
        if part.parent_id is None:
            raise ScopeIntegrityError("The canonical current-part path is invalid.")
        team = self._organizations.get_unit(connection, part.parent_id)
        if (
            team is None
            or team.kind != "team"
            or team.status != "active"
            or team.parent_id is None
        ):
            raise ScopeIntegrityError("The canonical current-part path is invalid.")
        responsibility = self._organizations.get_unit(connection, team.parent_id)
        if (
            responsibility is None
            or responsibility.kind != "responsibility"
            or responsibility.status != "active"
            or responsibility.parent_id is not None
        ):
            raise ScopeIntegrityError("The canonical current-part path is invalid.")

        leader = self._organizations.get_active_part_leader(connection, part.id)
        if leader is not None and leader.part_id != part.id:
            raise ScopeIntegrityError("The canonical part leader is invalid.")
        team_role = None
        responsibility_role = None
        if self._flags.higher_spaces:
            team_role = self._higher.get_open_role(
                connection, org_unit_id=team.id, role="team_leader"
            )
            responsibility_role = self._higher.get_open_role(
                connection,
                org_unit_id=responsibility.id,
                role="responsibility_executive",
            )
        return _ActorSnapshot(
            user=user,
            affiliation=affiliation,
            membership=membership,
            part=part,
            team=team,
            responsibility=responsibility,
            leader=leader,
            team_role=team_role,
            responsibility_role=responsibility_role,
        )

    @staticmethod
    def _undisclosed() -> ScopeNotFoundError:
        return ScopeNotFoundError("The requested knowledge space was not found.")

    def _authorize_requested_space(
        self,
        actor: _ActorSnapshot,
        space: SpaceRecord,
    ) -> tuple[SpaceKind, str, tuple[str, ...]] | None:
        if space.status != "active":
            return None
        if space.space_kind == "personal":
            if space.owner_user_id != actor.user.id:
                return None
            return ("personal", "내 지식", ())
        if space.space_kind == "part":
            if actor.part is None or space.owner_org_unit_id != actor.part.id:
                return None
            if space.write_enabled and actor.leader is None:
                raise ScopeIntegrityError(
                    "A write-enabled part space has no active leader."
                )
            return ("part", actor.part.name, actor.part_path)
        if not self._flags.higher_spaces:
            return None
        if space.space_kind == "team":
            if actor.team is None or space.owner_org_unit_id != actor.team.id:
                return None
            if space.write_enabled and actor.team_role is None:
                raise ScopeIntegrityError(
                    "A write-enabled Team space has no open leader episode."
                )
            return ("team", actor.team.name, actor.team_path)
        if space.space_kind == "responsibility":
            if (
                actor.responsibility is None
                or space.owner_org_unit_id != actor.responsibility.id
            ):
                return None
            if space.write_enabled and actor.responsibility_role is None:
                raise ScopeIntegrityError(
                    "A write-enabled Responsibility space has no open executive episode."
                )
            return (
                "responsibility",
                actor.responsibility.name,
                actor.responsibility_path,
            )
        return None

    def _build_access_plan(
        self,
        connection: ManagedConnection,
        *,
        actor: _ActorSnapshot,
        selected: SpaceRecord,
    ) -> AccessPlan:
        personal_wiki: PersonalWikiProfileRecord | None = None
        if selected.space_kind == "personal" and self._flags.multi_wiki_reads:
            personal_wiki = self._active_personal_wiki(
                connection, actor=actor, selected=selected
            )
        authorized = self._authorize_requested_space(actor, selected)
        if authorized is None:
            raise self._undisclosed()
        kind, name, path = authorized
        if personal_wiki is not None:
            name = personal_wiki.display_name
        entitled = self._entitled_capabilities(actor, kind, selected)
        effective = self._effective_capabilities(
            actor=actor,
            kind=kind,
            space=selected,
            entitled=entitled,
        )
        if Capability.READ not in effective:
            raise ScopeForbiddenError(
                "The selected view is not enabled for canonical reads."
            )
        selected_summary = SpaceSummary(
            id=selected.id,
            kind=kind,
            name=name,
            org_path=path,
            capabilities=effective,
        )
        source_spaces, org_rows = self._closure_source_spaces(
            connection, actor=actor, selected=selected, kind=kind
        )
        role = self._native_role(actor, kind)
        source_descriptors = tuple(
            CorpusSourceDescriptor(
                source_space_id=source.id,
                kind=source.space_kind,
                origin="native" if source.id == selected.id else "inherited",
                inherited=source.id != selected.id,
                capabilities=(
                    effective if source.id == selected.id else (Capability.READ,)
                ),
                space_generation=source.generation,
                space_row_version=source.row_version,
            )
            for source in source_spaces
        )
        overlays = self._closure_overlays(
            connection, actor=actor, selected=selected, kind=kind
        )
        organization_snapshot_version = max(
            (
                row.unit.updated_at_ms * 1_000_000 + row.unit.row_version
                for row in org_rows
            ),
            default=1,
        )
        generation = max(
            1,
            *(source.space_generation for source in source_descriptors),
            *(overlay.grant_row_version for overlay in overlays),
            *(row.unit.row_version for row in org_rows),
            0 if role is None else role.assignment_generation,
            0 if personal_wiki is None else personal_wiki.lifecycle_generation,
            0 if actor.affiliation is None else actor.affiliation.row_version,
        )
        closure_payload = {
            "actor_user_id": actor.user.id,
            "distribution_overlays": [
                {
                    "grant_id": overlay.grant_id,
                    "grant_row_version": overlay.grant_row_version,
                    "source_item_id": overlay.source_item_id,
                    "source_revision_id": overlay.source_revision_id,
                    "source_space_id": overlay.source_space_id,
                    "target_org_unit_id": overlay.target_org_unit_id,
                    "target_scope_id": overlay.target_scope_id,
                    "target_scope_kind": overlay.target_scope_kind,
                }
                for overlay in overlays
            ],
            "organization": [
                {
                    "id": row.unit.id,
                    "kind": row.unit.kind,
                    "parent_id": row.unit.parent_id,
                    "row_version": row.unit.row_version,
                    "status": row.unit.status,
                }
                for row in org_rows
            ],
            "role": (
                None
                if role is None
                else {
                    "assignment_generation": role.assignment_generation,
                    "id": role.id,
                    "row_version": role.row_version,
                    "user_id": role.user_id,
                }
            ),
            "schema_version": 1,
            "scope_epoch": actor.user.scope_epoch,
            "selected_view_space_id": selected.id,
            "sources": [
                {
                    "generation": source.space_generation,
                    "kind": source.kind,
                    "row_version": source.space_row_version,
                    "source_space_id": source.source_space_id,
                }
                for source in source_descriptors
            ],
        }
        if actor.affiliation is not None:
            closure_payload["home_affiliation"] = {
                "home_kind": actor.affiliation.home_kind,
                "id": actor.affiliation.id,
                "org_unit_id": actor.affiliation.org_unit_id,
                "row_version": actor.affiliation.row_version,
            }
        if personal_wiki is not None:
            closure_payload["personal_wiki"] = {
                "lifecycle_generation": personal_wiki.lifecycle_generation,
                "lifecycle_status": personal_wiki.lifecycle_status,
                "space_id": personal_wiki.space_id,
            }
        closure_sha = sha256(
            json.dumps(
                closure_payload,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        mutation_capabilities = tuple(
            capability for capability in effective if capability is not Capability.READ
        )
        closure = CorpusClosure(
            selected_view_space_id=selected.id,
            sources=source_descriptors,
            distribution_overlays=overlays,
            organization_snapshot_version=organization_snapshot_version,
            generation=generation,
            sha256=closure_sha,
        )
        return AccessPlan(
            actor_user_id=actor.user.id,
            system_role=actor.user.system_role,
            scope_epoch=actor.user.scope_epoch,
            selected_view=selected_summary,
            corpus_closure=closure,
            native_mutation_space_id=(selected.id if mutation_capabilities else None),
            native_capabilities=mutation_capabilities,
            role_assignment_id=None if role is None else role.id,
            role_assignment_generation=(
                None if role is None else role.assignment_generation
            ),
            actor_row_version=actor.user.row_version,
        )

    def _active_personal_wiki(
        self,
        connection: ManagedConnection,
        *,
        actor: _ActorSnapshot,
        selected: SpaceRecord,
    ) -> PersonalWikiProfileRecord:
        profile = self._personal_wikis.get_by_space_id(connection, selected.id)
        if (
            profile is None
            or profile.owner_user_id != actor.user.id
            or profile.space_id != selected.id
            or profile.lifecycle_status != "active"
        ):
            raise self._undisclosed()
        return profile

    def _closure_source_spaces(
        self,
        connection: ManagedConnection,
        *,
        actor: _ActorSnapshot,
        selected: SpaceRecord,
        kind: SpaceKind,
    ) -> tuple[tuple[SpaceRecord, ...], tuple[object, ...]]:
        if kind == "personal":
            return (selected,), ()
        root = {
            "part": actor.responsibility,
            "team": actor.team,
            "responsibility": actor.responsibility,
        }[kind]
        if root is None:
            raise ScopeIntegrityError("The selected organization path is unavailable.")
        if kind == "part":
            rows = self._organizations.list_subtree(
                connection, root_id=root.id, include_inactive=False
            )
            path_ids = {
                unit.id
                for unit in (actor.responsibility, actor.team, actor.part)
                if unit is not None
            }
            org_rows = tuple(row for row in rows if row.unit.id in path_ids)
            return (selected,), org_rows

        rows = self._organizations.list_subtree(
            connection, root_id=root.id, include_inactive=False
        )
        allowed_kinds = (
            {"team", "part"}
            if kind == "team"
            else {"responsibility", "team", "part"}
        )
        org_rows = tuple(row for row in rows if row.unit.kind in allowed_kinds)
        if len(org_rows) > _MAX_CLOSURE_SOURCES:
            raise ScopeIntegrityError("The organization closure exceeds its bound.")
        sources: list[SpaceRecord] = []
        for row in org_rows:
            source = self._spaces.get_org(connection, row.unit.id)
            if (
                source is None
                or source.status != "active"
                or source.space_kind != row.unit.kind
            ):
                raise ScopeIntegrityError(
                    "An active organization unit has no exact active native space."
                )
            sources.append(source)
        sources.sort(
            key=lambda source: (
                0 if source.id == selected.id else 1,
                {"responsibility": 0, "team": 1, "part": 2}[source.space_kind],
                source.id,
            )
        )
        if not sources or sources[0].id != selected.id:
            raise ScopeIntegrityError("The selected native source is absent from closure.")
        return tuple(sources), org_rows

    def _closure_overlays(
        self,
        connection: ManagedConnection,
        *,
        actor: _ActorSnapshot,
        selected: SpaceRecord,
        kind: SpaceKind,
    ) -> tuple[DistributionOverlayDescriptor, ...]:
        if kind not in {"part", "team"} or selected.owner_org_unit_id is None:
            return ()
        records = self._higher.list_active_distributions_for_target(
            connection,
            target_org_unit_id=selected.owner_org_unit_id,
            target_space_id=selected.id,
            limit=_MAX_CLOSURE_OVERLAYS + 1,
        )
        if len(records) > _MAX_CLOSURE_OVERLAYS:
            raise ScopeIntegrityError("The distribution overlay closure exceeds its bound.")
        expected_ancestors = (
            {
                unit.id
                for unit in (actor.team, actor.responsibility)
                if unit is not None
            }
            if kind == "part"
            else {
                actor.responsibility.id
                if actor.responsibility is not None
                else ""
            }
        )
        result: list[DistributionOverlayDescriptor] = []
        for record in records:
            source = self._spaces.get_by_id(connection, record.source_space_id)
            if (
                source is None
                or source.owner_org_unit_id not in expected_ancestors
                or source.space_kind not in {"team", "responsibility"}
                or (kind == "team" and source.space_kind != "responsibility")
            ):
                continue
            result.append(self._overlay_descriptor(record))
        return tuple(result)

    @staticmethod
    def _overlay_descriptor(
        record: KnowledgeDistributionGrantRecord,
    ) -> DistributionOverlayDescriptor:
        return DistributionOverlayDescriptor(
            grant_id=record.id,
            source_space_id=record.source_space_id,
            source_item_id=record.source_item_id,
            source_revision_id=record.source_revision_id,
            target_org_unit_id=record.target_org_unit_id,
            target_scope_kind=record.target_scope_kind,
            target_scope_id=record.target_scope_id,
            capabilities=(Capability.READ,),
            grant_row_version=record.row_version,
        )

    @staticmethod
    def _native_role(
        actor: _ActorSnapshot, kind: SpaceKind
    ) -> HigherScopeRoleAssignmentRecord | None:
        role = (
            actor.team_role
            if kind == "team"
            else actor.responsibility_role if kind == "responsibility" else None
        )
        return None if role is None or role.user_id != actor.user.id else role

    def _selectable_summary(
        self,
        actor: _ActorSnapshot,
        space: SpaceRecord | None,
        expected_kind: SpaceKind,
    ) -> SpaceSummary | None:
        if space is None:
            return None
        authorized = self._authorize_requested_space(actor, space)
        if authorized is None or authorized[0] != expected_kind:
            return None
        kind, name, path = authorized
        entitled = self._entitled_capabilities(actor, kind, space)
        effective = self._effective_capabilities(
            actor=actor,
            kind=kind,
            space=space,
            entitled=entitled,
        )
        if Capability.READ not in effective:
            return None
        return SpaceSummary(
            id=space.id,
            kind=kind,
            name=name,
            org_path=path,
            capabilities=effective,
        )

    @staticmethod
    def _entitled_capabilities(
        actor: _ActorSnapshot,
        kind: SpaceKind,
        space: SpaceRecord,
    ) -> tuple[Capability, ...]:
        values = {Capability.READ}
        if space.status == "active" and space.write_enabled:
            if kind in {"personal", "part"}:
                values.update(_ORDINARY_MUTATIONS)
                if kind == "part" and (
                    actor.is_part_leader
                    or actor.user.is_admin
                ):
                    values.add(Capability.FORCE_RELEASE)
            else:
                role = ScopeResolver._native_role(actor, kind)
                if role is not None:
                    values.update(_HIGHER_NATIVE_MUTATIONS)
        return _ordered_capabilities(values)

    def _effective_capabilities(
        self,
        *,
        actor: _ActorSnapshot,
        kind: SpaceKind,
        space: SpaceRecord,
        entitled: tuple[Capability, ...],
    ) -> tuple[Capability, ...]:
        if space.status != "active" or not self._flags.core_enabled:
            return ()

        if kind == "personal":
            read_enabled = self._flags.personal_reads
            mutation_enabled = self._flags.personal_writes and space.write_enabled
        elif kind == "part":
            read_enabled = self._flags.part_reads
            mutation_enabled = self._flags.part_writes and space.write_enabled
        elif kind == "team":
            read_enabled = self._flags.higher_spaces
            mutation_enabled = self._flags.team_writes and space.write_enabled
        else:
            read_enabled = self._flags.higher_spaces
            mutation_enabled = (
                self._flags.responsibility_writes and space.write_enabled
            )

        if not read_enabled:
            return ()
        values = {Capability.READ}
        if mutation_enabled:
            if kind in {"personal", "part"}:
                values.update(_ORDINARY_MUTATIONS)
                if kind == "part" and (
                    actor.is_part_leader
                    or (actor.user.is_admin and not self._flags.higher_spaces)
                ):
                    values.add(Capability.FORCE_RELEASE)
            else:
                values.update(_HIGHER_NATIVE_MUTATIONS)
        values.intersection_update(entitled)
        return _ordered_capabilities(values)


__all__ = [
    "AccessPlan",
    "Capability",
    "CorpusClosure",
    "CorpusSourceDescriptor",
    "DistributionOverlayDescriptor",
    "ResolvedScope",
    "ScopeAuthenticationRequiredError",
    "ScopeForbiddenError",
    "ScopeIntegrityError",
    "ScopeNotFoundError",
    "ScopeResolutionError",
    "ScopeResolver",
    "ScopeStaleError",
    "ScopeValidationError",
    "SelectableSpacesResult",
    "SessionPrincipal",
    "SpaceKind",
    "SpaceSummary",
    "SystemRole",
]
