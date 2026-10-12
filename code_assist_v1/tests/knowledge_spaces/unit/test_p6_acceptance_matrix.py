from __future__ import annotations

from pathlib import Path
import re


_SCENARIO = re.compile(
    r'@pytest\.mark\.scenario\("((?:CUR|PROP|MERGE)-[0-9]{3})"\)'
)
_EXPECTED = {
    *(f"CUR-{value:03d}" for value in range(1, 12)),
    *(f"PROP-{value:03d}" for value in range(1, 6)),
    *(f"MERGE-{value:03d}" for value in range(1, 5)),
}
_OWNED_TESTS = (
    "test_canonical_retrieval.py",
    "test_curation_shadow_metrics.py",
    "test_deterministic_ingest_policy.py",
    "test_incremental_candidate_retrieval.py",
    "test_publication_service.py",
    "test_reviewed_semantic_edge_service.py",
    "test_transactional_merge_service.py",
    "test_transactional_proposal_service.py",
    "test_versioned_finding_service.py",
)


def test_p6_acceptance_scenarios_have_explicit_permanent_test_owners() -> None:
    root = Path(__file__).resolve().parent
    owners: dict[str, set[str]] = {}
    for name in _OWNED_TESTS:
        text = (root / name).read_text(encoding="utf-8")
        for scenario_id in _SCENARIO.findall(text):
            owners.setdefault(scenario_id, set()).add(name)

    assert set(owners) == _EXPECTED
    assert all(owners[scenario_id] for scenario_id in sorted(_EXPECTED))
