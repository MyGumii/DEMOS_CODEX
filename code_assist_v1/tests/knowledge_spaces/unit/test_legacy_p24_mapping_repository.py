from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path

import pytest

from code_assist_v1.knowledge_core.db import transaction
from code_assist_v1.knowledge_core.repositories.graph import (
    GraphRepository,
    LegacyCandidateFinding,
    LegacyGraphEdge,
    legacy_candidate_finding_id,
    legacy_graph_edge_id,
)
from code_assist_v1.knowledge_core.repositories.migration import (
    migration_mapping_id_for_record,
    migration_run_id_for_plan,
    migration_source_locator,
)
from code_assist_v1.knowledge_core.repositories.migration_p24 import (
    LegacyP24GraphDetails,
    LegacyP24GraphTarget,
    LegacyP24MappingConflict,
    LegacyP24MappingRepository,
    LegacyP24RelationshipDetails,
    LegacyP24RelationshipRepository,
    LegacyP24RelationshipTarget,
)
from code_assist_v1.tests.knowledge_spaces.unit.test_legacy_graph_repository import (
    _targets,
)


pytestmark = pytest.mark.unit

_NOW = 1_786_582_923_000


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _dependencies(factory, fixture):
    connection = factory.open()
    try:
        rows = connection.execute(
            """SELECT map.id,map.item_id,map.revision_id,map.resolved_owner_user_id,
                      map.details_json,item.space_id
               FROM legacy_migration_map AS map
               JOIN knowledge_item AS item ON item.id=map.item_id
               WHERE json_extract(map.details_json,'$.plan_sha256')=?
                 AND json_extract(map.details_json,'$.import_mode')='personal_item'
               ORDER BY map.id""",
            (fixture.plan_sha256,),
        ).fetchall()
        assert len(rows) == 2
        result = []
        for row in rows:
            details = json.loads(row["details_json"])
            result.append(
                {
                    "mapping_id": row["id"],
                    "record_id": details["inventory_record_id"],
                    "item_id": row["item_id"],
                    "revision_id": row["revision_id"],
                    "owner": row["resolved_owner_user_id"],
                    "space_id": row["space_id"],
                    "manifest": details["inventory_manifest_sha256"],
                    "root_binding": details["root_binding_sha256"],
                }
            )
        return tuple(result)
    finally:
        connection.close()


def _details(
    fixture,
    dependency,
    *,
    record_id: str,
    parent_id: str,
    path_key: str,
    ordinal: int,
    mode: str,
    endpoints: tuple[str, ...],
    relationships: tuple[str, ...],
    source_sha: str,
    effect_evidence_json: str,
) -> LegacyP24GraphDetails:
    return LegacyP24GraphDetails(
        plan_sha256=fixture.plan_sha256,
        inventory_manifest_sha256=dependency["manifest"],
        root_binding_sha256=dependency["root_binding"],
        inventory_record_id=record_id,
        inventory_record_sha256=_sha(record_id + ":inventory"),
        parent_inventory_record_id=parent_id,
        root_id="knowledge_000",
        path_key_sha256=_sha(path_key),
        category={
            "graph_node": "knowledge.wiki_graph_nodes",
            "graph_edge": "knowledge.wiki_graph_edges",
            "finding": "knowledge.wiki_candidates",
        }[mode],
        record_kind="logical_record",
        source_ordinal=ordinal,
        import_mode=mode,
        result_disposition="created",
        target_space_id=dependency["space_id"],
        source_record_sha256=source_sha,
        endpoint_inventory_record_ids=endpoints,
        relationship_ids=tuple(sorted(relationships)),
        effect_evidence_sha256=hashlib.sha256(
            effect_evidence_json.encode("utf-8")
        ).hexdigest(),
    )


def _put_record(connection, fixture, target: LegacyP24GraphTarget):
    details = target.details
    return LegacyP24MappingRepository().put_graph_imported(
        connection,
        mapping_id=migration_mapping_id_for_record(
            fixture.plan_sha256, details.inventory_record_id
        ),
        migration_run_id=migration_run_id_for_plan(fixture.plan_sha256),
        legacy_kind=details.category,
        normalized_path=migration_source_locator(
            root_id=details.root_id,
            path_key=target.source_path_key,
            record_kind="logical_record",
            source_ordinal=details.source_ordinal,
        ),
        source_sha256=details.source_record_sha256,
        target=target,
        now_ms=_NOW,
    )


def _put_relationship(
    connection,
    fixture,
    *,
    relationship_id: str,
    relationship_type: str,
    source_record_id: str | None,
    from_record_id: str | None,
    to_record_id: str,
    effect_kind: str,
    edge_id: str | None = None,
    finding_id: str | None = None,
):
    def mapping(record_id: str | None) -> str | None:
        return (
            migration_mapping_id_for_record(fixture.plan_sha256, record_id)
            if record_id is not None
            else None
        )

    return LegacyP24RelationshipRepository().put_imported(
        connection,
        LegacyP24RelationshipTarget(
            relationship_type=relationship_type,
            source_mapping_id=mapping(source_record_id),
            from_mapping_id=mapping(from_record_id),
            to_mapping_id=mapping(to_record_id),
            resolved_owner_user_id="admin",
            target_space_id=_dependency_space(connection, fixture, to_record_id),
            effect_kind=effect_kind,
            edge_id=edge_id,
            finding_id=finding_id,
            item_id=None,
            details=LegacyP24RelationshipDetails(
                plan_sha256=fixture.plan_sha256,
                relationship_id=relationship_id,
                inventory_manifest_sha256=_dependency_binding(
                    connection, fixture, to_record_id, "inventory_manifest_sha256"
                ),
                root_binding_sha256=_dependency_binding(
                    connection, fixture, to_record_id, "root_binding_sha256"
                ),
                inventory_relationship_sha256=_sha(
                    relationship_id + ":source"
                ),
                source_inventory_record_id=source_record_id,
                from_inventory_record_id=from_record_id,
                to_inventory_record_id=to_record_id,
                effect_mapping_id=mapping(
                    source_record_id
                    if source_record_id is not None
                    else from_record_id
                ),
            ),
        ),
        now_ms=_NOW,
    )


def _dependency_space(connection, fixture, record_id: str) -> str:
    row = connection.execute(
        """SELECT json_extract(details_json,'$.target_space_id')
           FROM legacy_migration_map
           WHERE json_extract(details_json,'$.plan_sha256')=?
             AND json_extract(details_json,'$.inventory_record_id')=?""",
        (fixture.plan_sha256, record_id),
    ).fetchone()
    assert row is not None
    return str(row[0])


def _dependency_binding(
    connection, fixture, record_id: str, field: str
) -> str:
    assert field in {"inventory_manifest_sha256", "root_binding_sha256"}
    row = connection.execute(
        f"""SELECT json_extract(details_json,'$.{field}')
            FROM legacy_migration_map
            WHERE json_extract(details_json,'$.plan_sha256')=?
              AND json_extract(details_json,'$.inventory_record_id')=?""",
        (fixture.plan_sha256, record_id),
    ).fetchone()
    assert row is not None
    return str(row[0])


def test_p24_node_edge_finding_and_relationship_checkpoints_replay_exactly(
    tmp_path: Path, isolated_knowledge_layout
) -> None:
    factory, fixture, _ = _targets(tmp_path, isolated_knowledge_layout)
    first, second = _dependencies(factory, fixture)
    parent_graph = "rec_" + "a" * 24
    parent_candidates = "rec_" + "b" * 24
    node_id = "rec_" + "1" * 24
    edge_record_id = "rec_" + "2" * 24
    finding_record_id = "rec_" + "3" * 24
    node_relation = "rel_" + "1" * 24
    edge_relation = "rel_" + "2" * 24
    candidate_source = "rel_" + "3" * 24
    candidate_target = "rel_" + "4" * 24
    edge_id = legacy_graph_edge_id(fixture.plan_sha256, edge_record_id)
    finding_id = legacy_candidate_finding_id(
        fixture.plan_sha256, finding_record_id
    )
    node_evidence = _canonical(
        {"inventory_record_sha256": _sha(node_id + ":inventory")}
    )
    edge_evidence = _canonical({"source_record_sha256": _sha("edge")})
    finding_evidence = _canonical(
        {"source_record_sha256": _sha("candidate")}
    )
    node_target = LegacyP24GraphTarget(
        resolved_owner_user_id="admin",
        item_id=first["item_id"],
        revision_id=first["revision_id"],
        edge_id=None,
        finding_id=None,
        details=_details(
            fixture,
            first,
            record_id=node_id,
            parent_id=parent_graph,
            path_key="admin/wiki/graph.json",
            ordinal=1,
            mode="graph_node",
            endpoints=(first["record_id"],),
            relationships=(node_relation,),
            source_sha=_sha("node"),
            effect_evidence_json=node_evidence,
        ),
        source_path_key="admin/wiki/graph.json",
        effect_evidence_json=node_evidence,
    )
    edge_target = LegacyP24GraphTarget(
        resolved_owner_user_id="admin",
        item_id=None,
        revision_id=None,
        edge_id=edge_id,
        finding_id=None,
        details=_details(
            fixture,
            first,
            record_id=edge_record_id,
            parent_id=parent_graph,
            path_key="admin/wiki/graph.json",
            ordinal=2,
            mode="graph_edge",
            endpoints=(first["record_id"], second["record_id"]),
            relationships=(edge_relation,),
            source_sha=_sha("edge"),
            effect_evidence_json=edge_evidence,
        ),
        source_path_key="admin/wiki/graph.json",
        effect_evidence_json=edge_evidence,
    )
    finding_target = LegacyP24GraphTarget(
        resolved_owner_user_id="admin",
        item_id=None,
        revision_id=None,
        edge_id=None,
        finding_id=finding_id,
        details=_details(
            fixture,
            first,
            record_id=finding_record_id,
            parent_id=parent_candidates,
            path_key="admin/wiki/pending_diffs.jsonl",
            ordinal=1,
            mode="finding",
            endpoints=(first["record_id"], second["record_id"]),
            relationships=(candidate_source, candidate_target),
            source_sha=_sha("candidate"),
            effect_evidence_json=finding_evidence,
        ),
        source_path_key="admin/wiki/pending_diffs.jsonl",
        effect_evidence_json=finding_evidence,
    )
    connection = factory.open()
    try:
        graph = GraphRepository()
        with transaction(connection):
            graph.put_legacy_edge(
                connection,
                LegacyGraphEdge(
                    plan_sha256=fixture.plan_sha256,
                    inventory_record_id=edge_record_id,
                    edge_id=edge_id,
                    owner_user_id="admin",
                    space_id=first["space_id"],
                    source_item_id=first["item_id"],
                    source_revision_id=first["revision_id"],
                    target_item_id=second["item_id"],
                    target_revision_id=second["revision_id"],
                    edge_type="related",
                    evidence_json=edge_evidence,
                    created_at_ms=_NOW,
                ),
            )
            graph.put_legacy_candidate(
                connection,
                LegacyCandidateFinding(
                    plan_sha256=fixture.plan_sha256,
                    inventory_record_id=finding_record_id,
                    finding_id=finding_id,
                    owner_user_id="admin",
                    space_id=first["space_id"],
                    source_item_id=first["item_id"],
                    source_revision_id=first["revision_id"],
                    target_item_id=second["item_id"],
                    target_revision_id=second["revision_id"],
                    candidate_kind="graph_candidate",
                    evidence_json=finding_evidence,
                    proposed_action_json='{"legacy_action":"add_edge"}',
                    created_at_ms=_NOW,
                ),
            )
            assert _put_record(connection, fixture, node_target)[1] is True
            assert _put_record(connection, fixture, edge_target)[1] is True
            assert _put_record(connection, fixture, finding_target)[1] is True
            assert _put_relationship(
                connection,
                fixture,
                relationship_id=node_relation,
                relationship_type="graph_node_source",
                source_record_id=None,
                from_record_id=node_id,
                to_record_id=first["record_id"],
                effect_kind="dependency",
            )[1] is True
            assert _put_relationship(
                connection,
                fixture,
                relationship_id=edge_relation,
                relationship_type="related",
                source_record_id=edge_record_id,
                from_record_id=first["record_id"],
                to_record_id=second["record_id"],
                effect_kind="edge",
                edge_id=edge_id,
            )[1] is True
            assert _put_relationship(
                connection,
                fixture,
                relationship_id=candidate_source,
                relationship_type="candidate_source",
                source_record_id=None,
                from_record_id=finding_record_id,
                to_record_id=first["record_id"],
                effect_kind="finding",
                finding_id=finding_id,
            )[1] is True
            assert _put_relationship(
                connection,
                fixture,
                relationship_id=candidate_target,
                relationship_type="candidate_target",
                source_record_id=None,
                from_record_id=finding_record_id,
                to_record_id=second["record_id"],
                effect_kind="finding",
                finding_id=finding_id,
            )[1] is True
        with transaction(connection):
            assert _put_record(connection, fixture, node_target)[1] is False
            assert _put_record(connection, fixture, edge_target)[1] is False
            assert _put_record(connection, fixture, finding_target)[1] is False
            assert _put_relationship(
                connection,
                fixture,
                relationship_id=edge_relation,
                relationship_type="related",
                source_record_id=edge_record_id,
                from_record_id=first["record_id"],
                to_record_id=second["record_id"],
                effect_kind="edge",
                edge_id=edge_id,
            )[1] is False
        assert connection.execute(
            "SELECT count(*) FROM legacy_migration_map WHERE json_extract(details_json,'$.schema_version')=2"
        ).fetchone()[0] == 3
        assert connection.execute(
            "SELECT count(*) FROM legacy_migration_p24_relationship_map"
        ).fetchone()[0] == 4
    finally:
        connection.close()


def test_p24_record_conflict_does_not_create_a_second_mapping(
    tmp_path: Path, isolated_knowledge_layout
) -> None:
    factory, fixture, _ = _targets(tmp_path, isolated_knowledge_layout)
    dependency = _dependencies(factory, fixture)[0]
    record_id = "rec_" + "9" * 24
    evidence = _canonical(
        {"inventory_record_sha256": _sha(record_id + ":inventory")}
    )
    target = LegacyP24GraphTarget(
        resolved_owner_user_id="admin",
        item_id=dependency["item_id"],
        revision_id=dependency["revision_id"],
        edge_id=None,
        finding_id=None,
        details=_details(
            fixture,
            dependency,
            record_id=record_id,
            parent_id="rec_" + "8" * 24,
            path_key="admin/wiki/graph.json",
            ordinal=1,
            mode="graph_node",
            endpoints=(dependency["record_id"],),
            relationships=("rel_" + "9" * 24,),
            source_sha=_sha("node-conflict"),
            effect_evidence_json=evidence,
        ),
        source_path_key="admin/wiki/graph.json",
        effect_evidence_json=evidence,
    )
    connection = factory.open()
    try:
        with transaction(connection):
            assert _put_record(connection, fixture, target)[1] is True
        changed = replace(
            target,
            details=replace(target.details, effect_evidence_sha256="f" * 64),
        )
        with pytest.raises((LegacyP24MappingConflict, ValueError)):
            with transaction(connection):
                _put_record(connection, fixture, changed)
        assert connection.execute(
            "SELECT count(*) FROM legacy_migration_map WHERE json_extract(details_json,'$.schema_version')=2"
        ).fetchone()[0] == 1
    finally:
        connection.close()
