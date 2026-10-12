"""Pure shared-byte allocation and extractive (never generated) evidence.

Ranges use half-open Unicode code-point offsets into the immutable source,
not UTF-16 units, bytes or grapheme clusters. Only the join
separator is synthetic. Internal segment size is not a document-size limit.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import re


@dataclass(frozen=True, slots=True)
class EvidencePassages:
    text: str
    source_bytes: int
    source_sha256: str
    ranges: tuple[tuple[int, int], ...]
    truncated: bool


def allocate_evidence_bytes(sizes: tuple[int, ...], budget: int) -> tuple[int, ...]:
    """Whole bodies in caller priority order, then one partial body and omissions.

    No fixed document maximum. Omitted documents must be disclosed and handled
    by a subsequent reading pass, not silently called complete corpus coverage.
    """
    if budget < 0 or any(size < 0 for size in sizes):
        raise ValueError("Evidence sizes and budget must be nonnegative.")
    allocations: list[int] = []
    remaining = budget
    for size in sizes:
        allocation = min(size, remaining)
        allocations.append(allocation)
        remaining -= allocation
    return tuple(allocations)


def allocate_fair_evidence_bytes(sizes: tuple[int, ...], budget: int) -> tuple[int, ...]:
    """Max-min fair integer allocation; short sources release unused allowance."""
    if budget < 0 or any(size < 0 for size in sizes):
        raise ValueError("Evidence sizes and budget must be nonnegative.")
    allocations = [0] * len(sizes)
    remaining = budget
    ordered = sorted(range(len(sizes)), key=lambda index: (sizes[index], index))
    for position, index in enumerate(ordered):
        count = len(ordered) - position
        share, extra = divmod(remaining, count)
        if sizes[index] <= share:
            allocations[index] = sizes[index]
            remaining -= sizes[index]
            continue
        for offset, pending in enumerate(ordered[position:]):
            allocations[pending] = share + int(offset < extra)
        break
    return tuple(allocations)


def evidence_text_bytes(text: str, *, json_escaped: bool = False) -> int:
    """Text-content bytes; JSON mode excludes the surrounding string quotes."""
    if json_escaped:
        return len(json.dumps(text, ensure_ascii=False).encode("utf-8")) - 2
    return len(text.encode("utf-8"))


def _clip_text(text: str, budget: int, *, json_escaped: bool) -> str:
    if not json_escaped:
        return text.encode("utf-8")[:budget].decode("utf-8", errors="ignore")
    # Search code-point boundaries, never a serialized escape or UTF-8 byte.
    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        if evidence_text_bytes(text[:middle], json_escaped=True) <= budget:
            low = middle
        else:
            high = middle - 1
    return text[:low]


def _terms(text: str) -> set[str]:
    # General cross-inflection Korean overlap, not question-specific keywords.
    words = set(re.findall(r"[^\W_]+", text.casefold(), re.UNICODE))
    grams = {
        word[index:index + 2]
        for word in words if re.fullmatch(r"[가-힣]+", word)
        for index in range(len(word) - 1)
    }
    return words | grams


def _segments(text: str) -> list[tuple[int, int]]:
    result: list[tuple[int, int]] = []
    start = 0
    while start < len(text):
        end = min(start + 1_200, len(text))
        if end < len(text):
            # Prefer paragraph/line/sentence boundaries. A very long line is
            # still searched in full rather than treating its prefix as the doc.
            boundary = max(text.rfind(sep, start + 300, end)
                           for sep in ("\n\n", "\n", ". ", "。"))
            if boundary >= 0:
                end = boundary + 1
        result.append((start, end))
        start = end
    return result


def select_evidence_passages(
    text: str, budget: int, *, query: str, json_escaped: bool = False,
) -> EvidencePassages:
    """Exact source passages under raw UTF-8 or JSON text-content byte budget.

    Hash, source_bytes and ranges always describe the original unescaped source.
    """
    if budget < 0:
        raise ValueError("Evidence budget must be nonnegative.")
    encoded = text.encode("utf-8")
    source_bytes = len(encoded)
    digest = hashlib.sha256(encoded).hexdigest()
    # Do not keep a second complete byte copy throughout passage scoring.
    del encoded
    wire_size = evidence_text_bytes(text, json_escaped=True) if json_escaped else source_bytes
    if wire_size <= budget:
        return EvidencePassages(text, source_bytes, digest, ((0, len(text)),), False)
    if budget == 0:
        return EvidencePassages("", source_bytes, digest, (), bool(text))
    segments = _segments(text)
    query_terms = _terms(query)
    matched = [_terms(text[start:end]) & query_terms for start, end in segments]
    frequencies = {term: sum(term in terms for terms in matched) for term in query_terms}
    scores = [sum(math.log1p(len(segments) / frequencies[term]) for term in sorted(terms))
              for terms in matched]
    # Spread ties over the document instead of repeatedly selecting its prefix.
    spread = sorted(range(len(segments)), key=lambda i: (min(i, len(segments) - 1 - i), i))
    tie_order = {index: rank for rank, index in enumerate(spread)}
    order = sorted(range(len(segments)), key=lambda i: (-scores[i], tie_order[i]))
    # Keep immediately adjacent qualifications near each matching segment when
    # possible, while retaining other direct query hits ahead of zero-hit text.
    if any(scores):
        neighbor_scores = [max(scores[max(0, i - 1):i] + scores[i + 1:i + 2] + [0])
                           for i in range(len(segments))]
        order.sort(key=lambda i: (-scores[i], -neighbor_scores[i], tie_order[i]))
    ranges: list[tuple[int, int]] = []
    remaining = budget
    for index in order:
        start, end = segments[index]
        separator_bytes = (4 if json_escaped else 2) if ranges else 0
        available = remaining - separator_bytes
        if available <= 0:
            break
        fragment = text[start:end]
        clipped = _clip_text(fragment, available, json_escaped=json_escaped)
        if not clipped:
            continue
        ranges.append((start, start + len(clipped)))
        remaining -= separator_bytes + evidence_text_bytes(clipped, json_escaped=json_escaped)
    ranges.sort()
    merged: list[tuple[int, int]] = []
    for start, end in ranges:
        if merged and merged[-1][1] == start:
            merged[-1] = (merged[-1][0], end)
        else:
            merged.append((start, end))
    selected = "\n\n".join(text[start:end] for start, end in merged)
    return EvidencePassages(selected, source_bytes, digest, tuple(merged), True)
