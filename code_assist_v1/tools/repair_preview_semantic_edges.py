"""Repair fixture-judged semantic edges in one isolated preview owner's Wikis.

This operator tool is deliberately marker-bound.  It never targets a live data
    root, uses the canonical edit/curation/job services for every mutation, and
re-evaluates the selected personal Wikis with the same model policy as the
preview worker.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any
from uuid import UUID, uuid5


_NAMESPACE = UUID("6c62bdcb-cdce-47fc-963c-390b8231d433")
_DISALLOWED_PREVIEW_PROVIDERS = ("local-deterministic-preview",)
_REPAIR_REASON = (
    "Re-evaluate semantic relations after removing the fixture-only preview judge."
)


def _id(*parts: object) -> str:
    return str(uuid5(_NAMESPACE, "\x1f".join(str(value) for value in parts)))


def repair(
    root: Path,
    *,
    owner_user_id: str,
    port: int,
    codex_validation: bool,
) -> dict[str, Any]:
    from code_assist_v1.knowledge_core.scope import ScopeResolver, SessionPrincipal
    from code_assist_v1.knowledge_core.services.automatic_semantic_edges import (
        AutomaticSemanticEdgeHandler,
        InternalLLMSemanticJudge,
    )
    from code_assist_v1.knowledge_core.services.curation_worker import (
        CanonicalCurationRescanHandler,
    )
    from code_assist_v1.knowledge_core.services.editing import (
        AcquireLeaseCommand,
        ImmutableItemEditService,
        LeaseHmacKeyRing,
        SaveItemCommand,
    )
    from code_assist_v1.knowledge_core.services.semantic_edge_correction import (
        SemanticEdgeCorrectionService,
        SuppressSemanticEdgeCommand,
    )
    from code_assist_v1.knowledge_core.services.semantic_edge_seeding import (
        SemanticIntentMaterializer,
    )
    from code_assist_v1.knowledge_core.worker import DurableWorkKernel, HandlerRegistry
    from code_assist_v1.tools.full_second_brain_preview import (
        _canonical_factory,
        _configure_environment,
        _load_marker,
        _preview_feature_flags,
    )
    from code_assist_v1.tools.full_second_brain_preview_workers import (
        preview_semantic_provider_policy,
    )

    resolved = root.resolve(strict=True)
    marker = _load_marker(resolved)
    if Path(str(marker.get("data_root", ""))).resolve(strict=True) != resolved:
        raise RuntimeError("The preview marker does not bind this data root.")
    _configure_environment(resolved, port=port, codex=codex_validation)
    factory = _canonical_factory(resolved)
    connection = factory.open(read_only=True)
    try:
        user = connection.execute(
            "SELECT id,scope_epoch FROM app_user WHERE id=? AND account_status='active'",
            (owner_user_id,),
        ).fetchone()
        if user is None:
            raise RuntimeError("The requested active owner is unavailable.")
        wiki_rows = connection.execute(
            """SELECT profile.space_id,profile.display_name
                 FROM personal_wiki_profile AS profile
                 JOIN knowledge_space AS space ON space.id=profile.space_id
                WHERE profile.owner_user_id=? AND profile.lifecycle_status='active'
                  AND space.status='active'
                ORDER BY profile.is_default DESC,profile.name_key,profile.space_id""",
            (owner_user_id,),
        ).fetchall()
        wiki_space_ids = tuple(str(row["space_id"]) for row in wiki_rows)
        if not wiki_space_ids:
            raise RuntimeError("The owner has no active personal Wiki.")
        placeholders = ",".join("?" for _ in wiki_space_ids)
        fixture_edges = connection.execute(
            f"""SELECT id,space_id FROM knowledge_edge
                  WHERE space_id IN ({placeholders}) AND origin='semantic_auto'
                    AND model_provider IN ({','.join('?' for _ in _DISALLOWED_PREVIEW_PROVIDERS)})
                  ORDER BY space_id,id""",
            (*wiki_space_ids, *_DISALLOWED_PREVIEW_PROVIDERS),
        ).fetchall()
        item_rows = connection.execute(
            f"""SELECT item.id,item.space_id,item.current_revision_id,item.row_version,
                       revision.change_reason,body.content_md
                  FROM knowledge_item AS item
                  JOIN knowledge_revision AS revision
                    ON revision.id=item.current_revision_id AND revision.item_id=item.id
                  JOIN knowledge_revision_body AS body
                    ON body.item_id=item.id AND body.revision_id=item.current_revision_id
                 WHERE item.space_id IN ({placeholders})
                   AND item.lifecycle_status='active'
                   AND item.curation_status='published'
                 ORDER BY item.space_id,item.id""",
            wiki_space_ids,
        ).fetchall()
        disallowed_local_edges = connection.execute(
            f"""SELECT edge.id,edge.space_id AS scope_space_id,edge.row_version,
                       space.space_kind,profile.owner_user_id
                  FROM knowledge_edge AS edge
                  JOIN knowledge_space AS space ON space.id=edge.space_id
                  LEFT JOIN personal_wiki_profile AS profile
                    ON profile.space_id=edge.space_id
                 WHERE edge.origin='semantic_auto' AND edge.edge_status='active'
                   AND edge.model_provider IN ({','.join('?' for _ in _DISALLOWED_PREVIEW_PROVIDERS)})
                 ORDER BY edge.space_id,edge.id""",
            _DISALLOWED_PREVIEW_PROVIDERS,
        ).fetchall()
        disallowed_aggregate_edges = connection.execute(
            f"""SELECT edge.id,edge.home_space_id AS scope_space_id,edge.row_version,
                       space.space_kind,NULL AS owner_user_id
                  FROM aggregate_knowledge_edge AS edge
                  JOIN knowledge_space AS space ON space.id=edge.home_space_id
                 WHERE edge.origin='semantic_auto'
                   AND edge.edge_status IN ('active','needs_revalidation')
                   AND edge.model_provider IN ({','.join('?' for _ in _DISALLOWED_PREVIEW_PROVIDERS)})
                 ORDER BY edge.home_space_id,edge.id""",
            _DISALLOWED_PREVIEW_PROVIDERS,
        ).fetchall()
    finally:
        connection.close()

    flags = _preview_feature_flags()
    scope_resolver = ScopeResolver(flags)
    principal = SessionPrincipal(owner_user_id, int(user["scope_epoch"]))
    corrections = SemanticEdgeCorrectionService(factory, scope_resolver)
    suppressed_fixture_edges = 0
    for edge_kind, rows in (
        ("aggregate", disallowed_aggregate_edges),
        ("local", disallowed_local_edges),
    ):
        for row in rows:
            edge_id = str(row["id"])
            selected_space_id = str(row["scope_space_id"])
            space_kind = str(row["space_kind"])
            break_glass = space_kind != "personal"
            correction_principal = principal
            if not break_glass:
                edge_owner = row["owner_user_id"]
                if not isinstance(edge_owner, str) or not edge_owner:
                    raise RuntimeError("A personal fixture edge has no canonical owner.")
                owner_connection = factory.open(read_only=True)
                try:
                    owner_row = owner_connection.execute(
                        "SELECT scope_epoch FROM app_user WHERE id=? AND account_status='active'",
                        (edge_owner,),
                    ).fetchone()
                finally:
                    owner_connection.close()
                if owner_row is None:
                    raise RuntimeError("A personal fixture-edge owner is unavailable.")
                correction_principal = SessionPrincipal(
                    edge_owner, int(owner_row["scope_epoch"])
                )
            current_connection = factory.open(read_only=True)
            try:
                table = (
                    "knowledge_edge"
                    if edge_kind == "local"
                    else "aggregate_knowledge_edge"
                )
                current = current_connection.execute(
                    f"SELECT row_version,edge_status FROM {table} WHERE id=?",
                    (edge_id,),
                ).fetchone()
            finally:
                current_connection.close()
            if current is None or str(current["edge_status"]) not in {
                "active",
                "needs_revalidation",
            }:
                continue
            corrections.suppress(
                SuppressSemanticEdgeCommand(
                    principal=correction_principal,
                    selected_space_id=selected_space_id,
                    edge_id=edge_id,
                    edge_kind=edge_kind,
                    reason=(
                        "Remove an edge created by the retired fixture-only "
                        "preview judge."
                    ),
                    operation_id=_id("retire-disallowed", edge_kind, edge_id),
                    request_id=_id(
                        "retire-disallowed-request", edge_kind, edge_id
                    ),
                    break_glass=break_glass,
                    confirmation=(
                        "SUPPRESS ORGANIZATIONAL SEMANTIC EDGE"
                        if break_glass
                        else None
                    ),
                    expected_row_version=(
                        int(current["row_version"]) if break_glass else None
                    ),
                )
            )
            suppressed_fixture_edges += 1
    provider = preview_semantic_provider_policy(
        codex_validation=codex_validation
    )
    semantic_handler = AutomaticSemanticEdgeHandler(
        factory,
        InternalLLMSemanticJudge(
            provider_order=provider.provider_order,
            model_ids=provider.model_ids,
        ),
        model_provider=provider.model_provider,
        model_name=provider.model_name,
    )
    curation_handler = CanonicalCurationRescanHandler(
        factory,
        scope_resolver=scope_resolver,
    )
    curation_registered = curation_handler.registered_handler()
    semantic_registered = semantic_handler.registered_handlers()
    kernel = DurableWorkKernel(
        factory,
        HandlerRegistry((curation_registered, *semantic_registered)),
        scope_resolver=scope_resolver,
    )

    editing = ImmutableItemEditService(
        factory,
        key_ring=LeaseHmacKeyRing(
            active_key_id="preview-semantic-repair-v1",
            keys={"preview-semantic-repair-v1": bytes(range(32))},
        ),
        scope_resolver=scope_resolver,
    )
    revised_items = 0
    reused_repair_revisions = 0
    for row in item_rows:
        if str(row["change_reason"]) == _REPAIR_REASON:
            reused_repair_revisions += 1
            continue
        item_id = str(row["id"])
        space_id = str(row["space_id"])
        base_revision_id = str(row["current_revision_id"])
        editor_session_id = _id("editor", item_id, base_revision_id)
        raw_token = "A" * 43
        lease = editing.acquire(
            AcquireLeaseCommand(
                principal=principal,
                space_id=space_id,
                item_id=item_id,
                editor_session_id=editor_session_id,
                operation_id=_id("lease", item_id, base_revision_id),
                request_id=_id("lease-request", item_id, base_revision_id),
                raw_token=raw_token,
            )
        )
        editing.save(
            SaveItemCommand(
                principal=principal,
                space_id=space_id,
                item_id=item_id,
                editor_session_id=editor_session_id,
                lease_id=lease.lease_id,
                fencing_generation=lease.fencing_generation,
                expected_item_row_version=int(row["row_version"]),
                expected_revision_id=base_revision_id,
                operation_id=_id("save", item_id, base_revision_id),
                request_id=_id("save-request", item_id, base_revision_id),
                markdown=str(row["content_md"]),
                reason=_REPAIR_REASON,
                raw_token=raw_token,
            )
        )
        revised_items += 1

    curation_jobs = 0
    kernel.recover_expired()
    for _ in range(2000):
        claim = kernel.claim_next(
            claim_owner=f"preview-semantic-repair-curation-{os.getpid()}",
            lease_ms=900_000,
            handler_keys=(curation_registered.key,),
        )
        if claim is None:
            break
        kernel.run_claim(claim)
        curation_jobs += 1
    else:
        raise RuntimeError("Semantic repair curation jobs did not drain.")

    materializer = SemanticIntentMaterializer(factory, kernel, flags)
    materialized_jobs = 0
    for _ in range(1000):
        batch = materializer.run_batch(limit=100)
        materialized_jobs += batch.inserted_count
        if batch.selected_count == 0:
            break
    else:
        raise RuntimeError("Semantic repair job materialization did not converge.")

    handler_keys = tuple(value.key for value in semantic_registered)
    processed_jobs = 0
    kernel.recover_expired()
    for _ in range(2000):
        claim = kernel.claim_next(
            claim_owner=f"preview-semantic-repair-{os.getpid()}",
            lease_ms=900_000,
            handler_keys=handler_keys,
        )
        if claim is None:
            break
        kernel.run_claim(claim)
        processed_jobs += 1
    else:
        raise RuntimeError("Semantic repair jobs did not drain.")

    connection = factory.open(read_only=True)
    try:
        placeholders = ",".join("?" for _ in wiki_space_ids)
        edge_rows = connection.execute(
            f"""SELECT model_provider,model_name,count(*) AS edge_count
                  FROM knowledge_edge
                 WHERE space_id IN ({placeholders}) AND origin='semantic_auto'
                   AND edge_status='active'
                 GROUP BY model_provider,model_name
                 ORDER BY model_provider,model_name""",
            wiki_space_ids,
        ).fetchall()
        node_rows = connection.execute(
            f"""SELECT space_id,count(*) AS item_count FROM knowledge_item
                 WHERE space_id IN ({placeholders}) AND lifecycle_status='active'
                   AND curation_status='published'
                 GROUP BY space_id ORDER BY space_id""",
            wiki_space_ids,
        ).fetchall()
    finally:
        connection.close()
    return {
        "curation_jobs": curation_jobs,
        "edge_counts": [dict(row) for row in edge_rows],
        "item_counts": [dict(row) for row in node_rows],
        "materialized_jobs": materialized_jobs,
        "model_mode": provider.mode,
        "owner_user_id": owner_user_id,
        "processed_jobs": processed_jobs,
        "retired_fixture_edges": len(fixture_edges),
        "suppressed_fixture_edges": suppressed_fixture_edges,
        "revised_items": revised_items,
        "reused_repair_revisions": reused_repair_revisions,
        "status": "complete",
        "wiki_space_ids": list(wiki_space_ids),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--owner", default="admin")
    parser.add_argument("--port", type=int, default=10221)
    parser.add_argument("--codex", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    print(
        json.dumps(
            repair(
                args.data_root,
                owner_user_id=args.owner,
                port=args.port,
                codex_validation=args.codex,
            ),
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
