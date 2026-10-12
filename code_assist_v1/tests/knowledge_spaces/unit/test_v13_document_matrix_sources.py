from __future__ import annotations

import hashlib
import json

import pytest

from code_assist_v1.document_extractors import extract_document
from code_assist_v1.tools.validate_v13_document_upload_matrix import (
    _ALL_USERS,
    _PART_LEADERS,
    _PART_USERS,
    _TEAM_LEADERS,
    _TOPICS,
    _TWO_USERS_PER_PART,
    _body,
    _graph_audit,
    _pick_related_publication,
    _projected_graph_audit,
    _write_source,
)


pytestmark = pytest.mark.unit


def test_validation_accounts_cover_members_and_each_direct_leadership_level() -> None:
    assert len(_TWO_USERS_PER_PART) == 14
    assert len(_PART_USERS) == 15
    assert len(_PART_LEADERS) == 7
    assert _PART_LEADERS[0] == "admin"
    assert len(_TEAM_LEADERS) == 3
    assert len(_ALL_USERS) == 19


@pytest.mark.parametrize("suffix", (".docx", ".pptx", ".pdf"))
def test_generated_validation_document_round_trips_through_native_extractor(
    tmp_path,
    suffix: str,
) -> None:
    marker = "V13-GENERATED-ROUNDTRIP-01"
    path = tmp_path / f"validation{suffix}"
    _write_source(
        path,
        "Generated validation source",
        f"Related recovery protocol. Unique evidence marker: {marker}",
    )

    result = extract_document(path.name, path.read_bytes(), allow_configured_converters=False)
    first_bytes = path.read_bytes()
    _write_source(
        path,
        "Generated validation source",
        f"Related recovery protocol. Unique evidence marker: {marker}",
    )

    assert marker in result.markdown
    assert result.markdown.strip()
    assert path.stat().st_size > 50
    assert path.read_bytes() == first_bytes


def test_part_graph_audit_distinguishes_read_only_overlay_nodes_from_cross_space_edges() -> None:
    part_space = "part-space"
    team_space = "team-space"

    class _Api:
        user_id = "part-member"

        @staticmethod
        def get(path: str, label: str):
            assert path.endswith(f"/{part_space}/graph")
            assert label == f"graph-audit:{part_space}"
            return {
                "nodes": [
                    {"source_space_id": part_space},
                    {"source_space_id": part_space},
                    {
                        "source_space_id": team_space,
                        "inherited": True,
                        "origin": "overlay",
                    },
                ],
                "edges": [
                    {
                        "source_item_id": "related-a",
                        "target_item_id": "related-b",
                        "source_space_id": part_space,
                        "target_space_id": part_space,
                        "home_space_id": part_space,
                        "edge_scope": "local",
                    }
                ],
            }

    result = _graph_audit(
        _Api(),
        part_space,
        {
            "uploads": [
                {
                    "relation": "related",
                    "results": [
                        {"item_id": "related-a", "space_id": part_space},
                        {"item_id": "related-b", "space_id": part_space},
                    ],
                }
            ]
        },
    )

    assert result["source_space_ids"] == [part_space, team_space]
    assert result["related_edge_count"] == 1
    assert result["foreign_endpoint_edge_count"] == 0
    assert result["nonlocal_edge_count"] == 0


def test_query_evidence_uses_substantive_facts_and_can_select_a_peer_publication() -> None:
    topic = _TOPICS["industrial_ct"]
    markdown = _body(
        bridge_topic_key=None,
        marker="UNPREDICTABLE-EVIDENCE-7319",
        topic_key="industrial_ct",
        user_id="peer-a",
        ordinal=6,
    )
    report = {
        "accounts": {
            "peer-a": {
                "uploads": [
                    {
                        "action": "document-06",
                        "marker": "UNPREDICTABLE-EVIDENCE-7319",
                        "relation": "related",
                        "results": [{"item_id": "item-a", "space_id": "part-03"}],
                    }
                ]
            },
            "peer-b": {"uploads": []},
        }
    }

    owner, upload, publication = _pick_related_publication(
        report,
        source_space_ids={"part-03"},
        excluded_owners={"peer-b"},
    )

    assert topic.control_rule in markdown
    assert all(term in markdown for term in topic.expected_terms)
    assert "answer-key" not in markdown.casefold()
    assert "few-shot" not in markdown.casefold()
    assert owner == "peer-a"
    assert upload["marker"] == "UNPREDICTABLE-EVIDENCE-7319"
    assert publication == {"item_id": "item-a", "space_id": "part-03"}


def test_substantive_body_can_bridge_an_aggregate_topic_without_answer_keys() -> None:
    markdown = _body(
        bridge_topic_key="amhs_oht",
        marker="TRACE-ONLY-42",
        topic_key="cold_chain",
        user_id="team-leader",
        ordinal=8,
    )

    assert _TOPICS["cold_chain"].control_rule in markdown
    assert _TOPICS["amhs_oht"].control_rule in markdown
    assert "TRACE-ONLY-42" in markdown
    assert "answer-key" not in markdown.casefold()


def test_graph_audit_accepts_related_items_connected_through_peer_cluster() -> None:
    space_id = "part-03"
    current = {
        "uploads": [
            {
                "relation": "related",
                "results": [
                    {"item_id": "current-a", "space_id": space_id},
                    {"item_id": "current-b", "space_id": space_id},
                ],
            }
        ]
    }
    peer = {
        "uploads": [
            {
                "relation": "related",
                "results": [
                    {"item_id": "peer-a", "space_id": space_id},
                    {"item_id": "peer-b", "space_id": space_id},
                ],
            }
        ]
    }

    class _Api:
        user_id = "current"

        @staticmethod
        def get(path: str, label: str):
            return {
                "nodes": [{"source_space_id": space_id}] * 4,
                "edges": [
                    {
                        "source_item_id": "current-a",
                        "target_item_id": "peer-a",
                        "source_space_id": space_id,
                        "target_space_id": space_id,
                        "home_space_id": space_id,
                        "edge_scope": "local",
                    },
                    {
                        "source_item_id": "current-b",
                        "target_item_id": "peer-b",
                        "source_space_id": space_id,
                        "target_space_id": space_id,
                        "home_space_id": space_id,
                        "edge_scope": "local",
                    },
                ],
            }

    result = _graph_audit(
        _Api(),
        space_id,
        current,
        {"accounts": {"current": current, "peer": peer}},
    )

    assert result["related_edge_count"] == 0
    assert result["related_incident_item_count"] == 2
    assert result["isolated_cross_edge_count"] == 0


def test_projected_graph_audit_binds_current_generation_and_file_checksum(tmp_path) -> None:
    space_id = "part-03"
    generation = 4
    graph = {
        "schema_version": 1,
        "space_id": space_id,
        "generation": generation,
        "nodes": [{"item_id": "related-a"}, {"item_id": "related-b"}],
        "edges": [
            {
                "edge_id": "edge-1",
                "source_item_id": "related-a",
                "target_item_id": "related-b",
            }
        ],
    }
    graph_bytes = (
        json.dumps(graph, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    projection_root = tmp_path / "projections" / space_id
    graph_path = projection_root / "generations" / str(generation) / "graph" / "graph.json"
    graph_path.parent.mkdir(parents=True)
    graph_path.write_bytes(graph_bytes)
    manifest = {
        "space_id": space_id,
        "generation": generation,
        "entries": [
            {
                "relative_path": "graph/graph.json",
                "sha256": hashlib.sha256(graph_bytes).hexdigest(),
                "size_bytes": len(graph_bytes),
            }
        ],
    }
    projection_root.joinpath("manifest.json").write_text(
        json.dumps(manifest, sort_keys=True), encoding="utf-8"
    )

    entry = {
        "uploads": [
            {
                "relation": "related",
                "results": [
                    {"item_id": "related-a", "space_id": space_id},
                    {"item_id": "related-b", "space_id": space_id},
                ],
            },
            {
                "relation": "isolated",
                "results": [{"item_id": "isolated", "space_id": space_id}],
            },
        ]
    }
    result = _projected_graph_audit(
        tmp_path,
        space_id=space_id,
        entry=entry,
        report={"accounts": {"member": entry}},
        built_generation=generation,
    )

    assert result["generation"] == generation
    assert result["related_edge_count"] == 1
    assert result["isolated_cross_edge_count"] == 0
