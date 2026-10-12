"""Frozen stable-ID reference syntax for canonical Markdown revisions."""

from __future__ import annotations

from dataclasses import dataclass
import re


ITEM_REFERENCE_SYNTAX_VERSION = "item-ref-v1"
_TOKEN_RE = re.compile(
    r"\[\[item:([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\]\]",
    re.ASCII,
)
_WRAPPED_RE = re.compile(r"\[\[([^\]\r\n]{0,2000})\]\]", re.ASCII)
_MAX_REFERENCES = 1000


class ExplicitReferenceSyntaxError(ValueError):
    """Body-free rejection for malformed or ambiguous item syntax."""

    code = "EXPLICIT_REFERENCE_SYNTAX_INVALID"


@dataclass(frozen=True, slots=True)
class ParsedExplicitReference:
    ordinal: int
    target_item_id: str
    start_offset: int
    end_offset: int


def parse_explicit_item_references(
    markdown: str,
) -> tuple[ParsedExplicitReference, ...]:
    if not isinstance(markdown, str) or "\x00" in markdown:
        raise ExplicitReferenceSyntaxError("Canonical Markdown is invalid.")

    exact_spans: dict[tuple[int, int], str] = {
        match.span(): match.group(1) for match in _TOKEN_RE.finditer(markdown)
    }
    for wrapped in _WRAPPED_RE.finditer(markdown):
        inner = wrapped.group(1)
        if inner.startswith("item:") and wrapped.span() not in exact_spans:
            raise ExplicitReferenceSyntaxError(
                "An item reference is malformed or uses an unsupported extension."
            )
    # Unterminated/line-breaking item markers must not degrade into plain text.
    for marker in re.finditer(r"\[\[item:", markdown, re.ASCII):
        if not any(start == marker.start() for start, _end in exact_spans):
            raise ExplicitReferenceSyntaxError("An item reference is unterminated.")
    if len(exact_spans) > _MAX_REFERENCES:
        raise ExplicitReferenceSyntaxError(
            "A revision exceeds the explicit-reference boundary."
        )
    return tuple(
        ParsedExplicitReference(ordinal, target, start, end)
        for ordinal, ((start, end), target) in enumerate(
            sorted(exact_spans.items()), start=1
        )
    )


__all__ = [
    "ExplicitReferenceSyntaxError",
    "ITEM_REFERENCE_SYNTAX_VERSION",
    "ParsedExplicitReference",
    "parse_explicit_item_references",
]
