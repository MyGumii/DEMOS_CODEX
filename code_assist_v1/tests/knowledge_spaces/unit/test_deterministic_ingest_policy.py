from __future__ import annotations

from dataclasses import replace
import json
from uuid import UUID, uuid5

import pytest

from code_assist_v1.knowledge_core.services.curation import (
    DETERMINISTIC_INGEST_RULE_VERSION,
    DeterministicCorpusConflictError,
    build_deterministic_corpus_metrics,
    build_deterministic_fingerprint,
    build_deterministic_lint_metrics,
    classify_deterministic_ingest,
    lint_markdown_deterministic,
)
from code_assist_v1.knowledge_core.services.publication import (
    PUBLICATION_PREVIEW_RULE_VERSION,
    lint_markdown_structure,
)


pytestmark = pytest.mark.unit
_NAMESPACE = UUID("169a47f7-78c4-48a0-8531-f3d0532ecfb5")


def _uuid(label: str) -> str:
    return str(uuid5(_NAMESPACE, label))


def _sha(character: str) -> str:
    return character * 64


def _observed(
    markdown: str,
    *,
    space: str = "space-a",
    item: str = "item-a",
    source_sha: str = _sha("a"),
):
    return build_deterministic_fingerprint(
        markdown,
        space_id=_uuid(space),
        source_identity_sha256=source_sha,
        item_id=_uuid(item),
        revision_id=_uuid(f"revision:{item}"),
    )


def _proposed(
    markdown: str,
    *,
    space: str = "space-a",
    source_sha: str = _sha("b"),
):
    return build_deterministic_fingerprint(
        markdown,
        space_id=_uuid(space),
        source_identity_sha256=source_sha,
    )


@pytest.mark.scenario("CUR-010")
def test_versioned_t0_lint_preserves_blockers_and_adds_bounded_warnings() -> None:
    assert PUBLICATION_PREVIEW_RULE_VERSION == DETERMINISTIC_INGEST_RULE_VERSION
    legacy = (
        (" \r\n\t", "empty_markdown", "blocker"),
        ("safe\x01unsafe", "unsafe_control_character", "error"),
        ("---\ntitle: open", "unterminated_front_matter", "blocker"),
        ("# Title\n```python\nprint(1)", "unterminated_code_fence", "error"),
    )
    for markdown, code, severity in legacy:
        finding = next(
            value for value in lint_markdown_deterministic(markdown) if value.code == code
        )
        assert finding.severity == severity

    warnings = lint_markdown_deterministic(
        "# First\n### Jump\n# Second\n" + ("x" * 4_097) + "\n"
    )
    assert {(value.code, value.severity) for value in warnings} == {
        ("duplicate_h1", "warning"),
        ("heading_level_jump", "warning"),
        ("line_too_long", "warning"),
    }
    ignored = lint_markdown_deterministic(
        "---\ntitle: '# not a heading'\n---\n# Real\n```md\n# fenced\n```\n"
    )
    assert ignored == ()
    assert tuple(
        (value.code, value.severity, value.line, value.column)
        for value in lint_markdown_structure("# First\n### Jump\n")
    ) == (("heading_level_jump", "warning", 2, 1),)


def test_fingerprint_binds_exact_bytes_but_normalizes_only_line_endings() -> None:
    crlf = _proposed("# Title\r\nbody\r\n")
    lf = _proposed("# Title\nbody\n")
    assert crlf.raw_content_sha256 != lf.raw_content_sha256
    assert crlf.content_size_bytes != lf.content_size_bytes
    assert crlf.normalized_content_sha256 == lf.normalized_content_sha256
    assert crlf.normalizer_version == "markdown_lf_v1"


@pytest.mark.scenario("CUR-001")
def test_same_space_byte_exact_repeat_allows_only_provenance_not_merge() -> None:
    proposed = _proposed("# Exact\nbody\n", source_sha=_sha("b"))
    existing = _observed("# Exact\nbody\n", source_sha=_sha("a"))
    result = classify_deterministic_ingest(proposed, (existing,))
    assert result.disposition == "exact_repeat"
    assert result.automatic_effect == "provenance"
    assert (result.matched_item_id, result.matched_revision_id) == (
        existing.item_id,
        existing.revision_id,
    )
    assert "merge" not in json.dumps(result.as_evidence(), sort_keys=True)


@pytest.mark.scenario("CUR-001")
def test_same_source_exact_is_no_op_and_changed_source_is_review_candidate() -> None:
    source = _sha("c")
    existing = _observed("# Current\n", source_sha=source)
    same = classify_deterministic_ingest(
        _proposed("# Current\n", source_sha=source), (existing,)
    )
    changed = classify_deterministic_ingest(
        _proposed("# Changed\n", source_sha=source), (existing,)
    )
    assert (same.disposition, same.automatic_effect) == ("no_op", "none")
    assert (changed.disposition, changed.automatic_effect) == (
        "source_update_candidate",
        "none",
    )
    assert changed.matched_item_id == existing.item_id


@pytest.mark.scenario("CUR-002")
def test_normalized_only_and_cross_space_exact_never_auto_deduplicate() -> None:
    normalized = _observed("# Similar\n", item="normalized")
    cross_space = _observed(
        "# Similar\r\n", space="space-b", item="foreign", source_sha=_sha("d")
    )
    proposed = _proposed("# Similar\r\n")
    result = classify_deterministic_ingest(proposed, (cross_space, normalized))
    assert result.disposition == "normalized_candidate"
    assert result.automatic_effect == "none"
    assert result.normalized_candidate_item_ids == (normalized.item_id,)
    assert result.cross_space_exact_item_ids == (cross_space.item_id,)
    assert result.matched_item_id is None

    cross_only = classify_deterministic_ingest(proposed, (cross_space,))
    assert (cross_only.disposition, cross_only.automatic_effect) == ("new", "none")
    assert cross_only.cross_space_exact_item_ids == (cross_space.item_id,)


def test_corrupt_multiple_exact_or_source_matches_fail_closed() -> None:
    proposed = _proposed("# Exact\n")
    first = _observed("# Exact\n", item="first")
    second = _observed("# Exact\n", item="second", source_sha=_sha("e"))
    with pytest.raises(
        DeterministicCorpusConflictError,
        match="More than one exact current body",
    ):
        classify_deterministic_ingest(proposed, (first, second))

    shared_source = _sha("f")
    source_first = _observed("# One\n", item="source-first", source_sha=shared_source)
    source_second = _observed(
        "# Two\n", item="source-second", source_sha=shared_source
    )
    with pytest.raises(
        DeterministicCorpusConflictError,
        match="source identity resolves to multiple",
    ):
        classify_deterministic_ingest(
            _proposed("# Three\n", source_sha=shared_source),
            (source_second, source_first),
        )


@pytest.mark.scenario("CUR-010")
def test_corpus_metrics_are_order_independent_body_free_and_never_count_merges() -> None:
    exact = classify_deterministic_ingest(
        _proposed("# A\n", source_sha=_sha("b")),
        (_observed("# A\n", item="exact", source_sha=_sha("a")),),
    )
    normalized = classify_deterministic_ingest(
        _proposed("# B\r\n", source_sha=_sha("d")),
        (_observed("# B\n", item="normalized", source_sha=_sha("c")),),
    )
    novel = classify_deterministic_ingest(
        _proposed("secret-body-sentinel", source_sha=_sha("e")), ()
    )
    first = build_deterministic_corpus_metrics((exact, normalized, novel))
    second = build_deterministic_corpus_metrics((novel, exact, normalized))
    assert first == second
    assert first.as_dict() == {
        "automatic_merge_count": 0,
        "automatic_provenance_count": 1,
        "cross_space_exact_candidate_count": 0,
        "disposition_counts": {
            "exact_repeat": 1,
            "new": 1,
            "normalized_candidate": 1,
        },
        "evidence_sha256": first.evidence_sha256,
        "normalized_candidate_count": 1,
        "rule_version": DETERMINISTIC_INGEST_RULE_VERSION,
        "total_inputs": 3,
    }
    assert "secret-body-sentinel" not in json.dumps(first.as_dict(), sort_keys=True)
    with pytest.raises(
        DeterministicCorpusConflictError,
        match="input fingerprint is duplicated",
    ):
        build_deterministic_corpus_metrics((exact, exact))


@pytest.mark.scenario("CUR-010")
def test_lint_corpus_metrics_are_order_independent_and_hash_only() -> None:
    clean_sha = _sha("1")
    blocked_sha = _sha("2")
    warning_sha = _sha("3")
    clean = lint_markdown_deterministic("# Clean\n")
    blocked = lint_markdown_deterministic("---\nunclosed")
    warning = lint_markdown_deterministic("# One\n# Two\n")
    first = build_deterministic_lint_metrics(
        {clean_sha: clean, blocked_sha: blocked, warning_sha: warning}
    )
    second = build_deterministic_lint_metrics(
        {warning_sha: warning, clean_sha: clean, blocked_sha: blocked}
    )
    assert first == second
    assert first.as_dict() == {
        "evidence_sha256": first.evidence_sha256,
        "finding_code_counts": {
            "duplicate_h1": 1,
            "unterminated_front_matter": 1,
        },
        "inputs_with_blockers_or_errors": 1,
        "rule_version": DETERMINISTIC_INGEST_RULE_VERSION,
        "severity_counts": {"blocker": 1, "warning": 1},
        "total_inputs": 3,
    }


def test_corpus_metrics_reject_forged_public_decision_dtos() -> None:
    exact = classify_deterministic_ingest(
        _proposed("# Exact\n", source_sha=_sha("b")),
        (_observed("# Exact\n", item="exact", source_sha=_sha("a")),),
    )
    with pytest.raises(ValueError, match="automatic effect exceeds"):
        build_deterministic_corpus_metrics(
            (replace(exact, automatic_effect="none"),)  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="evidence hash does not bind"):
        build_deterministic_corpus_metrics(
            (replace(exact, evidence_sha256=_sha("0")),)
        )
