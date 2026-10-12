"""The single framework-light composition root used by every web host."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from .domain import FeatureFlags, HostKind
from .errors import (
    KnowledgeCoreHostConflictError,
    KnowledgeCoreServiceUnboundError,
)
from .feature_flags import read_feature_flags


EXTENSION_KEY = "code_assist_v1.knowledge_core"
_BLUEPRINT_ATTRIBUTE = "_code_assist_knowledge_core"
_MISSING = object()


@dataclass(frozen=True, slots=True)
class HostProfile:
    kind: HostKind
    intended_surfaces: frozenset[str]


HOST_PROFILES: dict[HostKind, HostProfile] = {
    HostKind.CODE: HostProfile(
        HostKind.CODE,
        frozenset({"admin_api", "knowledge_api", "publication_adapter"}),
    ),
    HostKind.NOTION: HostProfile(
        HostKind.NOTION,
        frozenset({"publication_adapter"}),
    ),
    HostKind.BLUEPRINT: HostProfile(
        HostKind.BLUEPRINT,
        frozenset({"knowledge_api", "publication_adapter"}),
    ),
}


@dataclass(frozen=True, slots=True)
class ServiceRegistry:
    """Stable service slots; P1.1 intentionally leaves every slot unbound."""

    agent_context: Any | None = None
    organization: Any | None = None
    publication: Any | None = None
    editing: Any | None = None
    lifecycle: Any | None = None
    retrieval: Any | None = None
    curation: Any | None = None
    jobs: Any | None = None
    conversations: Any | None = None
    management_reads: Any | None = None
    aggregate_graph: Any | None = None
    scope_selection: Any | None = None
    personal_wikis: Any | None = None
    personal_wiki_search: Any | None = None
    higher_authority: Any | None = None
    spaces: Any | None = None
    purge: Any | None = None
    temporal: Any | None = None
    semantic_edge_correction: Any | None = None
    hybrid_ranking: Any | None = None
    source_artifacts: Any | None = None

    def require(self, name: str) -> Any:
        if name not in self.__dataclass_fields__:
            raise KnowledgeCoreServiceUnboundError(
                "The requested Knowledge Core service slot is unknown."
            )
        value = getattr(self, name)
        if value is None:
            raise KnowledgeCoreServiceUnboundError(
                "The requested Knowledge Core service is not bound in this phase."
            )
        return value


@dataclass(frozen=True, slots=True)
class CanonicalRouteBindings:
    """Non-service adapters required to activate the canonical P3.7 routes."""

    turn_executor: Any
    conversation_summarizer: Any
    principal_resolver: Callable[[], object]
    publication_status_reader: Any = None

    def __post_init__(self) -> None:
        if not callable(getattr(self.turn_executor, "complete", None)):
            raise TypeError("turn_executor must expose complete().")
        if not callable(getattr(self.conversation_summarizer, "summarize", None)):
            raise TypeError("conversation_summarizer must expose summarize().")
        if not callable(self.principal_resolver):
            raise TypeError("principal_resolver must be callable.")


@dataclass(frozen=True, slots=True)
class PublicationRouteBindings:
    """Authentication adapter for the standalone P4.5 publication surface."""

    principal_resolver: Callable[[], object]
    publication_status_reader: Any = None

    def __post_init__(self) -> None:
        if not callable(self.principal_resolver):
            raise TypeError("principal_resolver must be callable.")


@dataclass(frozen=True, slots=True)
class KnowledgeCoreContainer:
    contract_version: int
    host: HostProfile
    flags: FeatureFlags
    services: ServiceRegistry = field(default_factory=ServiceRegistry)
    canonical_route_bindings: CanonicalRouteBindings | None = None
    publication_route_bindings: PublicationRouteBindings | None = None
    registered_route_groups: tuple[str, ...] = ()

    @property
    def serves_traffic(self) -> bool:
        return bool(self.registered_route_groups)


def compose_knowledge_core(
    host_kind: HostKind | str,
    *,
    environ: Mapping[str, str] | None = None,
    services: ServiceRegistry | None = None,
    canonical_route_bindings: CanonicalRouteBindings | None = None,
    publication_route_bindings: PublicationRouteBindings | None = None,
) -> KnowledgeCoreContainer:
    """Build a side-effect-free container; no DB, route, worker, or file opens."""

    try:
        normalized_host = HostKind(host_kind)
    except (TypeError, ValueError) as exc:
        raise KnowledgeCoreHostConflictError(
            "The Knowledge Core host kind is unknown."
        ) from exc
    if services is not None and not isinstance(services, ServiceRegistry):
        raise TypeError("services must be a ServiceRegistry.")
    if canonical_route_bindings is not None and not isinstance(
        canonical_route_bindings, CanonicalRouteBindings
    ):
        raise TypeError("canonical_route_bindings must be CanonicalRouteBindings.")
    if publication_route_bindings is not None and not isinstance(
        publication_route_bindings, PublicationRouteBindings
    ):
        raise TypeError("publication_route_bindings must be PublicationRouteBindings.")
    return KnowledgeCoreContainer(
        contract_version=1,
        host=HOST_PROFILES[normalized_host],
        flags=read_feature_flags(environ),
        services=services or ServiceRegistry(),
        canonical_route_bindings=canonical_route_bindings,
        publication_route_bindings=publication_route_bindings,
    )


def _existing_container(target: Any) -> KnowledgeCoreContainer | None:
    extensions = getattr(target, "extensions", None)
    if isinstance(extensions, dict):
        value = extensions.get(EXTENSION_KEY, _MISSING)
    else:
        value = getattr(target, _BLUEPRINT_ATTRIBUTE, _MISSING)
    if value is _MISSING:
        return None
    if not isinstance(value, KnowledgeCoreContainer):
        raise KnowledgeCoreHostConflictError(
            "The route target contains an invalid Knowledge Core extension value."
        )
    return value


def _store_container(target: Any, container: KnowledgeCoreContainer) -> None:
    extensions = getattr(target, "extensions", None)
    if isinstance(extensions, dict):
        extensions[EXTENSION_KEY] = container
    else:
        setattr(target, _BLUEPRINT_ATTRIBUTE, container)


def register_knowledge_core(
    target: Any,
    host_kind: HostKind | str,
    *,
    environ: Mapping[str, str] | None = None,
    services: ServiceRegistry | None = None,
    canonical_route_bindings: CanonicalRouteBindings | None = None,
    publication_route_bindings: PublicationRouteBindings | None = None,
) -> KnowledgeCoreContainer:
    """Attach exactly one container to a Flask app or Blueprint-like target."""

    if not callable(getattr(target, "route", None)):
        raise KnowledgeCoreHostConflictError(
            "Knowledge Core requires a Flask app or Blueprint-like route target."
        )
    candidate = compose_knowledge_core(
        host_kind,
        environ=environ,
        services=services,
        canonical_route_bindings=canonical_route_bindings,
        publication_route_bindings=publication_route_bindings,
    )
    existing = _existing_container(target)
    if existing is not None:
        if (
            existing.host.kind != candidate.host.kind
            or existing.flags != candidate.flags
            or existing.services != candidate.services
            or existing.canonical_route_bindings != candidate.canonical_route_bindings
            or existing.publication_route_bindings
            != candidate.publication_route_bindings
        ):
            raise KnowledgeCoreHostConflictError(
                "The route target already has an incompatible Knowledge Core container."
            )
        return existing

    from .routes import register_routes

    route_groups = register_routes(target, candidate)
    if not isinstance(route_groups, tuple) or any(
        not isinstance(group, str) or not group for group in route_groups
    ):
        raise KnowledgeCoreHostConflictError(
            "Knowledge Core route registration returned an invalid group manifest."
        )
    if len(set(route_groups)) != len(route_groups):
        raise KnowledgeCoreHostConflictError(
            "Knowledge Core route registration returned duplicate route groups."
        )
    # Route closures must capture the same container that the host exposes via
    # get_knowledge_core().  The manifest is the sole field finalized after
    # route registration; all authorization/service dependencies were already
    # frozen on candidate before any route adapter was invoked.
    object.__setattr__(candidate, "registered_route_groups", route_groups)
    _store_container(target, candidate)
    return candidate


def get_knowledge_core(target: Any) -> KnowledgeCoreContainer:
    """Return a host container or fail without constructing hidden global state."""

    container = _existing_container(target)
    if container is None:
        raise KnowledgeCoreHostConflictError(
            "The route target has no registered Knowledge Core container."
        )
    return container


__all__ = [
    "CanonicalRouteBindings",
    "EXTENSION_KEY",
    "HOST_PROFILES",
    "HostProfile",
    "KnowledgeCoreContainer",
    "PublicationRouteBindings",
    "ServiceRegistry",
    "compose_knowledge_core",
    "get_knowledge_core",
    "register_knowledge_core",
]
