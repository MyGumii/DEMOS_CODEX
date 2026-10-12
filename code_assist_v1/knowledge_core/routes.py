"""Thin, dependency-injected Knowledge Core route composition boundary."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from .errors import KnowledgeCoreNotReadyError

if TYPE_CHECKING:
    from .composition import KnowledgeCoreContainer


def register_routes(
    target: Any, container: "KnowledgeCoreContainer"
) -> tuple[str, ...]:
    if not callable(getattr(target, "route", None)):
        raise TypeError("route target is missing route()")
    read_routes = container.flags.personal_reads or container.flags.part_reads
    publication_routes = container.flags.personal_writes
    if not read_routes and not publication_routes:
        route_mutation_enabled = any(
            (
                container.flags.personal_writes,
                container.flags.multi_wiki_reads,
                container.flags.multi_wiki_writes,
                container.flags.conversation_wiki_binding,
                container.flags.part_reads,
                container.flags.part_writes,
                container.flags.higher_spaces,
                container.flags.team_writes,
                container.flags.responsibility_writes,
                container.flags.auto_semantic_edges_local,
            )
        )
        if route_mutation_enabled:
            raise KnowledgeCoreNotReadyError(
                "Knowledge Core mutation/worker routes are not available."
            )
        return ()
    if read_routes and "knowledge_api" not in container.host.intended_surfaces:
        raise KnowledgeCoreNotReadyError(
            "This host cannot serve the canonical knowledge API."
        )
    if (
        container.flags.higher_spaces
        and "admin_api" not in container.host.intended_surfaces
    ):
        raise KnowledgeCoreNotReadyError(
            "This host cannot serve the higher-authority administration API."
        )
    if publication_routes and "publication_adapter" not in container.host.intended_surfaces:
        raise KnowledgeCoreNotReadyError(
            "This host cannot serve the canonical publication API."
        )
    services = container.services
    from .canonical_routes import register_canonical_routes, register_publication_routes

    if read_routes:
        bindings = container.canonical_route_bindings
        if bindings is None:
            raise KnowledgeCoreNotReadyError(
                "Canonical read routes require explicit P3.7 route bindings."
            )
        publication_service = services.require("publication") if publication_routes else None
        item_edit_service = (
            services.require("editing")
            if (container.flags.personal_writes or container.flags.part_writes)
            else None
        )
        item_lifecycle_service = (
            services.require("lifecycle")
            if (container.flags.personal_writes or container.flags.part_writes)
            else None
        )
        item_purge_service = services.purge
        curation_service = (
            services.curation
            if (container.flags.personal_writes or container.flags.part_writes)
            else None
        )
        job_service = services.jobs
        job_mutations_enabled = job_service is not None and bool(
            container.flags.personal_writes or container.flags.part_writes
        )
        register_canonical_routes(
            target,
            scope_selection_service=services.require("scope_selection"),
            conversation_service=services.require("conversations"),
            retrieval_service=services.require("retrieval"),
            management_read_service=services.require("management_reads"),
            agent_context_service=services.require("agent_context"),
            turn_executor=bindings.turn_executor,
            conversation_summarizer=bindings.conversation_summarizer,
            principal_resolver=bindings.principal_resolver,
            publication_service=publication_service,
            item_edit_service=item_edit_service,
            item_lifecycle_service=item_lifecycle_service,
            item_purge_service=item_purge_service,
            curation_service=curation_service,
            job_service=job_service,
            job_mutations_enabled=job_mutations_enabled,
            source_artifact_service=services.source_artifacts,
        )
        groups = ["canonical_knowledge_v1"]
        if container.flags.multi_wiki_reads:
            from .personal_wiki_routes import register_personal_wiki_routes

            register_personal_wiki_routes(
                target,
                service=services.require("personal_wikis"),
                search_service=(
                    services.require("personal_wiki_search")
                    if container.flags.hybrid_rerank
                    else None
                ),
                principal_resolver=bindings.principal_resolver,
                writes_enabled=container.flags.multi_wiki_writes,
            )
            groups.append("personal_wiki_v2_reads")
            if container.flags.hybrid_rerank:
                groups.append("personal_wiki_v2_management_search")
            if container.flags.multi_wiki_writes:
                groups.append("personal_wiki_v2_writes")
        if container.flags.higher_spaces:
            from .higher_space_routes import register_higher_space_routes

            register_higher_space_routes(
                target,
                service=services.require("higher_authority"),
                principal_resolver=bindings.principal_resolver,
            )
            groups.append("higher_authority_v2")
        if services.temporal is not None:
            from .temporal_routes import register_temporal_routes

            register_temporal_routes(
                target,
                service=services.temporal,
                principal_resolver=bindings.principal_resolver,
            )
            groups.append("temporal_v2")
        if container.flags.auto_semantic_edges_local:
            from .semantic_edge_routes import register_semantic_edge_routes

            register_semantic_edge_routes(
                target,
                service=services.require("semantic_edge_correction"),
                principal_resolver=bindings.principal_resolver,
            )
            groups.append("semantic_edge_correction_v2")
        if publication_service is not None:
            groups.append("canonical_publication_v1")
        if item_edit_service is not None:
            groups.append("canonical_editing_v1")
        if item_lifecycle_service is not None:
            groups.append("canonical_lifecycle_v1")
        if item_purge_service is not None:
            groups.append("canonical_purge_v1")
        if curation_service is not None:
            groups.append("canonical_curation_v1")
        if job_service is not None:
            groups.append("canonical_jobs_v1")
        return tuple(groups)

    publication_bindings = container.publication_route_bindings
    if publication_bindings is None:
        raise KnowledgeCoreNotReadyError(
            "Standalone publication routes require explicit P4.5 route bindings."
        )
    register_publication_routes(
        target,
        scope_selection_service=services.require("scope_selection"),
        publication_service=services.require("publication"),
        principal_resolver=publication_bindings.principal_resolver,
    )
    return ("canonical_publication_v1",)


__all__ = ["register_routes"]
