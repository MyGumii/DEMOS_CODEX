"""Deterministic, revision-bound exact body spans for semantic graph evidence.

Single line breaks are retained, including PDF soft wrapping inside a word.
The model selects an opaque ID; it never rewrites quotes or supplies offsets.
"""
from dataclasses import dataclass
from hashlib import sha256
from collections.abc import Mapping
import json
import re

EVIDENCE_BODY_CHARS = 16_384
EVIDENCE_UNIT_CHARS = 512
EVIDENCE_UNIT_OVERLAP_CHARS = 256
EVIDENCE_UNIT_LIMIT = 64
EVIDENCE_SPAN_BYTES = 8_192
_BREAK = re.compile(r"\r?\n[ \t]*\r?\n|(?<=[.!?。])\s+")


@dataclass(frozen=True, slots=True)
class SemanticEvidenceUnit:
    evidence_unit_id: str
    start: int
    end: int
    text: str
    kind: str


def evidence_units(*, side: str, item_id: str, revision_id: str, content: str) -> tuple[SemanticEvidenceUnit, ...]:
    if side not in {"source", "target"}:
        raise ValueError("Evidence side is invalid.")
    body = content[:EVIDENCE_BODY_CHARS]
    units = []
    hard_boundaries = []
    def append(start, stop, kind):
        while start < stop and body[start].isspace():
            start += 1
        while stop > start and body[stop - 1].isspace():
            stop -= 1
        text = body[start:stop]
        if text and any(character.isalnum() for character in text):
            binding = json.dumps([side, item_id, revision_id, start, stop, text, kind], ensure_ascii=True, separators=(",", ":"))
            unit_id = ("src_" if side == "source" else "tgt_") + sha256(binding.encode()).hexdigest()[:20]
            units.append(SemanticEvidenceUnit(unit_id, start, stop, text, kind))
    cursor = 0
    begins_fragment = False
    while cursor < len(body):
        limit = min(cursor + EVIDENCE_UNIT_CHARS, len(body))
        end = limit
        if limit < len(body):
            # Primary spans start where the previous complete sentence/block
            # ended. Soft newlines inside words are never primary boundaries.
            boundaries = [match.end() for match in _BREAK.finditer(body, cursor + 256, limit)]
            if boundaries:
                end = boundaries[-1]
            else:
                hard_boundaries.append(end)
        ends_fragment = end in hard_boundaries or end == len(body) == EVIDENCE_BODY_CHARS
        append(cursor, end, "window" if begins_fragment or ends_fragment else "block")
        if end == len(body):
            break
        cursor = end
        begins_fragment = ends_fragment
    # Hard windows require explicit bridge excerpts. Their starts need not be
    # sentence boundaries, so disclose their kind rather than implying a whole
    # claim. Each hard 512-char span leaves room in the64-unit budget for one.
    for boundary in hard_boundaries:
        start = max(0, boundary - EVIDENCE_UNIT_OVERLAP_CHARS)
        stop = min(len(body), boundary + EVIDENCE_UNIT_OVERLAP_CHARS)
        append(start, stop, "bridge")
    units.sort(key=lambda unit: (unit.start, unit.end, unit.kind))
    if len(units) > EVIDENCE_UNIT_LIMIT or len({unit.evidence_unit_id for unit in units}) != len(units):
        raise ValueError("Evidence unit bounds are invalid.")
    return tuple(units)


def evidence_span_units(*, side: str, item_id: str, revision_id: str, content: str) -> tuple[SemanticEvidenceUnit, ...]:
    """Ordered, non-overlapping endpoints for a complete evidence interval.

    v5 units remain unchanged for historical response replay. v6 does not need
    duplicate bridge text: an interval includes every original character between
    its endpoint units, so conditions across windows are not omitted.
    """
    return tuple(unit for unit in evidence_units(
        side=side, item_id=item_id, revision_id=revision_id, content=content
    ) if unit.kind != "bridge")


def resolve_evidence_span(*, side: str, item_id: str, revision_id: str,
                          content: str, selection: object) -> tuple[int, int] | None:
    """Resolve allowlisted endpoints without rewriting, skipping or inferring text."""
    if not isinstance(selection, Mapping) or set(selection) != {"start_unit_id", "end_unit_id"}:
        return None
    if any(not isinstance(value, str) for value in selection.values()):
        return None
    units = evidence_span_units(side=side, item_id=item_id, revision_id=revision_id, content=content)
    indexed = {unit.evidence_unit_id: (index, unit) for index, unit in enumerate(units)}
    first = indexed.get(selection["start_unit_id"])
    last = indexed.get(selection["end_unit_id"])
    if first is None or last is None or first[0] > last[0]:
        return None
    start, end = first[1].start, last[1].end
    excerpt = content[start:end]
    # Preserve the original quote contract's byte bound, not a character count.
    if not excerpt or len(excerpt.encode("utf-8")) > EVIDENCE_SPAN_BYTES:
        return None
    if any(content[unit.start:unit.end] != unit.text for unit in units[first[0]:last[0] + 1]):
        return None
    return start, end
