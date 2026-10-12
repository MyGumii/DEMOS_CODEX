from __future__ import annotations

from dataclasses import replace

import pytest

from code_assist_v1.knowledge_core.db import transaction
from code_assist_v1.knowledge_core.domain import (
    AggregateEdgeStatus,
    ExplicitReferenceAvailability,
    SemanticEdgeDirection,
    SemanticIntentProvenanceKind,
    SemanticIntentTargetKind,
    SemanticRelationType,
)
from code_assist_v1.knowledge_core.repositories.semantic_graph_contracts import (
    SemanticEdgeIntentRecord,
    SemanticGraphContractConflictError,
    SemanticGraphContractRepository,
    derive_semantic_lifecycle_vector,
)
from code_assist_v1.tests.knowledge_spaces.unit.test_migration_v10_semantic_graph_contracts import (
    _audit_outbox,
    _insert_aggregate,
    _migrate_current,
    _published_item,
    _uuid,
    _v9_hierarchy,
)


pytestmark = pytest.mark.unit


def _running_baseline_seed(connection, *, ids: dict[str, str], revision_id: str) -> tuple[str, int]:
    audit_id, outbox_id, _operation_id = _audit_outbox(
        connection,
        label="repository-seed",
        actor="admin",
        space_id=ids["root_space"],
        item_id=None,
        revision_id=None,
        timestamp=1000,
    )
    seed_id = _uuid("v10:repository:seed")
    connection.execute(
        """INSERT INTO semantic_edge_intent_seed_run(
             id,effect_key,cause_kind,scope_kind,scope_id,
             detector_generation,previous_detector_generation,
             old_ancestor_space_id,new_ancestor_space_id,
             organization_event_audit_id,detector_rollout_audit_id,
             lifecycle_restore_audit_id,snapshot_domain_high_water,
             snapshot_audit_high_water,cursor_updated_at,cursor_revision_id,
             state,fence,scanned_count,intent_count,result_sha256,error_json,
             created_audit_event_id,created_outbox_event_id,created_at,
             updated_at,completed_at,row_version
           ) VALUES (?,'seed:repository','baseline_backfill','global',NULL,
                     1,NULL,NULL,NULL,NULL,NULL,NULL,1000,?,0,NULL,'pending',1,
                     0,0,NULL,NULL,?,?,1001,1001,NULL,1)""",
        (seed_id, audit_id, audit_id, outbox_id),
    )
    connection.execute(
        """UPDATE semantic_edge_intent_seed_run
              SET state='running',cursor_updated_at=1002,cursor_revision_id=?,
                  updated_at=1002,row_version=2 WHERE id=?""",
        (revision_id, seed_id),
    )
    return seed_id, audit_id


@pytest.mark.scenario("AUTOEDGE-003")
def test_repository_derives_lifecycle_and_converges_exact_intent_replay(
    isolated_knowledge_layout,
) -> None:
    _v9, ids = _v9_hierarchy(isolated_knowledge_layout)
    factory, _ = _migrate_current(isolated_knowledge_layout)
    repository = SemanticGraphContractRepository()
    connection = factory.open()
    try:
        with transaction(connection):
            item_id, revision_id = _published_item(
                connection,
                label="repository-intent",
                space_id=ids["part_space"],
                actor="leader",
                timestamp=990,
            )
            vector = derive_semantic_lifecycle_vector(
                connection,
                source_space_id=ids["part_space"],
                source_item_id=item_id,
            )
            assert vector.item_lifecycle_generation == 1
            assert vector.wiki_lifecycle_generation == 0
            assert vector.latest_episode_id is None

            personal_space_id = connection.execute(
                """SELECT id FROM knowledge_space
                     WHERE owner_user_id='leader' AND space_kind='personal'"""
            ).fetchone()[0]
            personal_item_id, _personal_revision_id = _published_item(
                connection,
                label="repository-personal-vector",
                space_id=personal_space_id,
                actor="leader",
                timestamp=991,
            )
            personal_vector = derive_semantic_lifecycle_vector(
                connection,
                source_space_id=personal_space_id,
                source_item_id=personal_item_id,
            )
            assert personal_vector.item_lifecycle_generation == 1
            assert personal_vector.wiki_lifecycle_generation == 1

            seed_id, audit_id = _running_baseline_seed(
                connection, ids=ids, revision_id=revision_id
            )
            body_sha = connection.execute(
                """SELECT normalized_content_sha256 FROM knowledge_revision
                     WHERE id=?""",
                (revision_id,),
            ).fetchone()[0]
            source_generation, organization_generation = tuple(
                connection.execute(
                    """SELECT s.generation,o.row_version FROM knowledge_space s
                         JOIN org_unit o ON o.id=s.owner_org_unit_id
                        WHERE s.id=?""",
                    (ids["part_space"],),
                ).fetchone()
            )
            record = SemanticEdgeIntentRecord(
                id=_uuid("v10:repository:intent"),
                effect_key="intent:repository:exact-replay",
                source_space_id=ids["part_space"],
                source_item_id=item_id,
                source_revision_id=revision_id,
                source_body_sha256=body_sha,
                target_kind=SemanticIntentTargetKind.LOCAL,
                target_space_id=ids["part_space"],
                target_org_unit_id=ids["part"],
                detector_generation=1,
                source_revision_row_version=1,
                source_space_generation=source_generation,
                target_space_generation=source_generation,
                organization_generation=organization_generation,
                closure_proof_sha256="1" * 64,
                item_lifecycle_generation=vector.item_lifecycle_generation,
                wiki_lifecycle_generation=vector.wiki_lifecycle_generation,
                latest_episode_id=vector.latest_episode_id,
                latest_episode_no=vector.latest_episode_no,
                latest_episode_restored_at=vector.latest_episode_restored_at,
                provenance_kind=SemanticIntentProvenanceKind.BASELINE_BACKFILL,
                publication_transition_id=None,
                publication_audit_event_id=None,
                publication_outbox_event_id=None,
                seed_run_id=seed_id,
                seed_snapshot_high_water=1000,
                seed_cursor_updated_at=1002,
                seed_cursor_revision_id=revision_id,
                organization_event_audit_id=None,
                detector_rollout_audit_id=None,
                lifecycle_restore_audit_id=None,
                created_at=1003,
            )
            assert repository.append_intent(connection, record) == record
            assert repository.append_intent(connection, record) == record
            assert repository.get_intent(connection, record.id) == record
            assert repository.get_seed_run(connection, seed_id).created_audit_event_id == audit_id  # type: ignore[union-attr]
            assert repository.list_intents_after(connection, limit=1) == (record,)
            with pytest.raises(SemanticGraphContractConflictError):
                repository.append_intent(
                    connection,
                    replace(record, id=_uuid("v10:repository:intent:collision")),
                )
            with pytest.raises(ValueError):
                repository.list_intents_after(connection, limit=201)
    finally:
        connection.close()


@pytest.mark.scenario("AGRAPH-001")
@pytest.mark.scenario("REF-001")
def test_repository_maps_aggregate_and_reference_ledgers_without_conflation(
    isolated_knowledge_layout,
) -> None:
    _v9, ids = _v9_hierarchy(isolated_knowledge_layout)
    factory, _ = _migrate_current(isolated_knowledge_layout)
    repository = SemanticGraphContractRepository()
    connection = factory.open()
    try:
        with transaction(connection):
            source_item, source_revision = _published_item(
                connection,
                label="repository-aggregate-source",
                space_id=ids["part_space"],
                actor="leader",
                timestamp=1100,
            )
            target_item, target_revision = _published_item(
                connection,
                label="repository-aggregate-target",
                space_id=ids["part2_space"],
                actor="foreign",
                timestamp=1101,
            )
            audit_id, outbox_id, operation_id = _audit_outbox(
                connection,
                label="repository-aggregate",
                actor="leader",
                space_id=ids["root_space"],
                item_id=source_item,
                revision_id=source_revision,
                timestamp=1102,
            )
            edge_id = _insert_aggregate(
                connection,
                label="repository",
                home_space_id=ids["root_space"],
                home_org_unit_id=ids["root"],
                source_space_id=ids["part_space"],
                source_item_id=source_item,
                source_revision_id=source_revision,
                target_space_id=ids["part2_space"],
                target_item_id=target_item,
                target_revision_id=target_revision,
                actor="leader",
                audit_id=audit_id,
                outbox_id=outbox_id,
                operation_id=operation_id,
                semantic_key="2" * 64,
            )
            edge = repository.get_aggregate_edge(connection, edge_id)
            assert edge is not None
            assert edge.edge_type is SemanticRelationType.RELATED
            assert edge.direction is SemanticEdgeDirection.SYMMETRIC
            assert edge.edge_status is AggregateEdgeStatus.ACTIVE
            assert repository.list_aggregate_edges(
                connection, home_space_id=ids["root_space"]
            ) == (edge,)

            reference_target, reference_target_revision = _published_item(
                connection,
                label="repository-reference-target",
                space_id=ids["part_space"],
                actor="leader",
                timestamp=1103,
            )
            reference_audit, reference_outbox, reference_operation = _audit_outbox(
                connection,
                label="repository-reference",
                actor="leader",
                space_id=ids["part_space"],
                item_id=source_item,
                revision_id=source_revision,
                timestamp=1104,
            )
            reference_id = _uuid("v10:repository:reference")
            connection.execute(
                """INSERT INTO knowledge_explicit_reference(
                     id,source_space_id,source_item_id,source_revision_id,ordinal,
                     syntax_version,target_space_id,target_item_id,
                     target_revision_id,closure_proof_sha256,actor_user_id,
                     operation_id,effect_key,audit_event_id,outbox_event_id,
                     availability_status,unavailable_reason,created_at,updated_at,
                     row_version
                   ) VALUES (?,?,?,?,1,'item-ref-v1',?,?,?,?,?,?,?, ?,?,
                             'available',NULL,1105,1105,1)""",
                (
                    reference_id,
                    ids["part_space"],
                    source_item,
                    source_revision,
                    ids["part_space"],
                    reference_target,
                    reference_target_revision,
                    "3" * 64,
                    "leader",
                    reference_operation,
                    "reference:repository",
                    reference_audit,
                    reference_outbox,
                ),
            )
            reference = repository.get_reference(connection, reference_id)
            assert reference is not None
            assert (
                reference.availability_status
                is ExplicitReferenceAvailability.AVAILABLE
            )
            assert repository.list_references(
                connection, source_revision_id=source_revision
            ) == (reference,)
            assert connection.execute(
                "SELECT count(*) FROM knowledge_edge WHERE id=?", (reference_id,)
            ).fetchone()[0] == 0
            assert connection.execute(
                "SELECT count(*) FROM aggregate_knowledge_edge WHERE id=?",
                (reference_id,),
            ).fetchone()[0] == 0
    finally:
        connection.close()
