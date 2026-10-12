"""Exact, bounded neighboring-source context; no inferred semantic relations.

The current chunk owns writing. Immediate neighbors are reference context only.
Quotes name a supplied chunk; the server, not the model, resolves source offsets.
Core-quote presence proves source ownership, never entailment or completeness.
"""
from .wiki_authoring import (
    AuthoredClaim, AuthoredSection, AuthoringOutputError, AuthoringSourceError,
    SourceQuote, _array, _hash, _json, _shape, _text,
)

SOURCE_CONTEXT_VERSION = "wiki-source-neighbors-v1"


def neighbors(plan, core):
    chunks = plan.source_plan.chunks
    if (type(core.ordinal) is not int or not 1 <= core.ordinal <= len(chunks)
            or chunks[core.ordinal - 1] != core):
        raise AuthoringSourceError("Context requires the exact owned source chunk.")
    return tuple(chunks[index] for index in (core.ordinal - 2, core.ordinal)
        if 0 <= index < len(chunks))


def context_records(plan, core, *, include_text=False):
    return _records(neighbors(plan, core), include_text=include_text)


def _records(parts, *, include_text=False):
    rows = []
    for part in parts:
        row = {"chunk_id": part.chunk_id, "ordinal": part.ordinal,
            "source_range": [part.start, part.end], "source_sha256": part.content_sha256}
        if include_text:
            row["text"] = part.text
        rows.append(row)
    return rows


def validate_boundary_output(value, plan, core):
    """Input must already be bounded, strict JSON; preserve literal quotations."""
    row = _shape(value, {"schema_version", "chunk_id", "sections", "warnings"})
    if type(row["schema_version"]) is not int or row["schema_version"] != 1 or row["chunk_id"] != core.chunk_id:
        raise AuthoringOutputError("Authoring response has a stale chunk identity.")
    permitted = {part.chunk_id: part for part in (core, *neighbors(plan, core))}
    sections, cache, work = [], {}, 0
    for raw_section in _array(row["sections"]):
        section = _shape(raw_section, {"heading", "claims"})
        claims = []
        for raw_claim in _array(section["claims"]):
            claim = _shape(raw_claim, {"text", "quotes"})
            quotes, owns_source = [], False
            for raw_quote in _array(claim["quotes"]):
                raw_quote = _shape(raw_quote, {"chunk_id", "text"})
                chunk_id, text = _text(raw_quote["chunk_id"]), _text(raw_quote["text"])
                part = permitted.get(chunk_id)
                if part is None:
                    raise AuthoringOutputError("Quote names an unavailable source chunk.")
                key = (chunk_id, text)
                if key not in cache:
                    work += len(part.text)
                    if work > 8_388_608:
                        raise AuthoringOutputError("Quote verification exceeds its per-call work bound.")
                    offset = part.text.find(text)
                    if offset < 0 or part.text.find(text, offset + 1) >= 0:
                        raise AuthoringOutputError("An exact, unambiguous source quote is required.")
                    cache[key] = SourceQuote(text, ((part.start + offset, part.start + offset + len(text)),))
                if cache[key] not in quotes:
                    quotes.append(cache[key])
                owns_source |= chunk_id == core.chunk_id
            if not owns_source:
                raise AuthoringOutputError("Every claim requires a quote from the owned source chunk.")
            claims.append(AuthoredClaim(_text(claim["text"]), tuple(quotes)))
        sections.append(AuthoredSection(_text(section["heading"]), tuple(claims)))
    return tuple(sections), tuple(_text(item) for item in _array(row["warnings"], nonempty=False))


def validate_context_manifest(value, chunks, source_text):
    """Recheck publication metadata against stored same-run chunks and source.

No DB/model calls. Existing published-item reuse has its separate lineage fence.
This validates exact text/ownership, not the meaning of generated claim prose.
"""
    if (value.get("source_context_version") != SOURCE_CONTEXT_VERSION
            or not isinstance(source_text, str) or not chunks
            or value.get("source_sha256") != _hash(source_text)
            or not isinstance(value.get("chunks"), list) or len(value["chunks"]) != len(chunks)):
        raise AuthoringOutputError("Invalid boundary context manifest.")
    start = 0
    for index, (core, row) in enumerate(zip(chunks, value["chunks"])):
        if (core.ordinal != index + 1 or core.start != start or not core.start < core.end <= len(source_text)
                or core.text != source_text[core.start:core.end] or core.content_sha256 != _hash(core.text)
                or not isinstance(row, dict) or row.get("chunk_id") != core.chunk_id
                or _json(row.get("source_range")) != _json([core.start, core.end])):
            raise AuthoringOutputError("Boundary manifest differs from stored source chunks.")
        start = core.end
        adjacent = tuple(chunks[i] for i in (index - 1, index + 1) if 0 <= i < len(chunks))
        if _json(row.get("source_context")) != _json(_records(adjacent)):
            raise AuthoringOutputError("Boundary manifest has inconsistent neighbor context.")
        allowed = {c.chunk_id: c for c in (core, *adjacent)}
        facts, locations = row.get("facts"), row.get("source_locations")
        if not isinstance(facts, list) or not facts or not isinstance(locations, list) or len(facts) != len(locations):
            raise AuthoringOutputError("Invalid boundary fact locations.")
        cache, work = set(), 0
        for fact, location in zip(facts, locations):
            if (not isinstance(fact, dict) or not isinstance(location, dict)
                    or fact.get("fact_id") != location.get("fact_id")
                    or not isinstance(fact.get("quotes"), list) or not fact["quotes"]
                    or not isinstance(fact.get("source_ranges"), list)
                    or not isinstance(location.get("quotes"), list)
                    or len(fact["quotes"]) != len(fact["source_ranges"])
                    or len(fact["quotes"]) != len(location["quotes"])):
                raise AuthoringOutputError("Boundary quote mapping is incomplete.")
            owns_source = False
            for text, span, quote in zip(fact["quotes"], fact["source_ranges"], location["quotes"]):
                if not isinstance(quote, dict) or not isinstance(quote.get("source_chunk_id"), str):
                    raise AuthoringOutputError("Missing boundary source chunk identity.")
                part = allowed.get(quote["source_chunk_id"])
                if (part is None or not isinstance(span, list) or len(span) != 2
                        or any(type(n) is not int for n in span)
                        or not part.start <= span[0] < span[1] <= part.end
                        or _json(quote.get("source_ranges")) != _json([span])
                        or not isinstance(text, str) or not text.strip()
                        or source_text[span[0]:span[1]] != text or quote.get("quote_sha256") != _hash(text)):
                    raise AuthoringOutputError("Boundary quote is not bound to exact source text.")
                key = (part.chunk_id, text)
                if key not in cache:
                    work += len(part.text)
                    if work > 8_388_608 or part.text.find(text, span[0] - part.start + 1) >= 0 or part.text.find(text) != span[0] - part.start:
                        raise AuthoringOutputError("Boundary quote is ambiguous or over budget.")
                    cache.add(key)
                owns_source |= part.chunk_id == core.chunk_id
            if not owns_source:
                raise AuthoringOutputError("Boundary fact lacks its owned source quote.")
    if start != len(source_text):
        raise AuthoringOutputError("Boundary manifest omits a source suffix.")
