from __future__ import annotations

import pytest

from code_assist_v1.knowledge_core.services.explicit_references import (
    ExplicitReferenceSyntaxError,
    ITEM_REFERENCE_SYNTAX_VERSION,
    parse_explicit_item_references,
)


pytestmark = pytest.mark.unit

_FIRST = "11111111-1111-4111-8111-111111111111"
_SECOND = "22222222-2222-4222-8222-222222222222"


def test_exact_stable_id_syntax_preserves_source_order_and_duplicates() -> None:
    parsed = parse_explicit_item_references(
        f"Title [[item:{_SECOND}]] then [[item:{_FIRST}]] and [[item:{_SECOND}]]."
    )
    assert ITEM_REFERENCE_SYNTAX_VERSION == "item-ref-v1"
    assert [(row.ordinal, row.target_item_id) for row in parsed] == [
        (1, _SECOND),
        (2, _FIRST),
        (3, _SECOND),
    ]


@pytest.mark.parametrize(
    "value",
    [
        f"[[item:{_FIRST}|injected title]]",
        "[[item:AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA]]",
        f"[[item:{_FIRST}",
        "[[item:project-roadmap]]",
        "[[item:\n11111111-1111-4111-8111-111111111111]]",
    ],
)
def test_malformed_or_extended_item_syntax_fails_closed(value: str) -> None:
    with pytest.raises(ExplicitReferenceSyntaxError):
        parse_explicit_item_references(value)


def test_title_only_wiki_syntax_is_not_an_item_reference() -> None:
    assert parse_explicit_item_references("[[Project Roadmap]]") == ()
