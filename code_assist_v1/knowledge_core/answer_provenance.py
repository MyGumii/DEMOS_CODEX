"""Opt-in answer experiment: quote occurrence and structure, never entailment.

Pure transformation of the existing turn-bound citation catalog. No retrieval,
file/DB access, model review, semantic approval, or default activation.
"""
from dataclasses import replace
from hashlib import sha256
from html import escape
import json
import re

from markdown_it import MarkdownIt

from .answer_citations import CitationCatalog, CitationValidationError, render_citations

PROVENANCE_PROMPT_VERSION = "canonical-second-brain-turn-experimental-provenance-v1"
PROVENANCE_POLICY = """[Optional Claim Provenance Experiment]
Replace only the preceding final-answer presentation instructions with this
JSON contract. All private-data authority, evidence, general knowledge,
reasoning, uncertainty and external-tool policies still apply. Return one JSON
object, no code fence, wrapper, analysis, or text outside that object.
Required root fields: schema_version (integer 1), chapters (nonempty array),
summary_ids (nonempty array of distinct existing claim IDs).
Each chapter has exactly title (one-line text) and claims (nonempty array).
Each claim has exactly id, kind, text, basis, evidence.
id is a unique C followed by 1 to 7 digits. kind is one of wiki_fact, inference,
proposal, general, uncertain. text contains the final explanation, not internal
deliberation. Keep each independently assessable claim's conditions and scope.
Ordinary Markdown lists, tables, code and inline formatting are allowed in text;
headings, horizontal rules, raw HTML and citation markers are server-owned.
wiki_fact describes a statement actually in the supplied Wiki body, not a title
or inference. It requires nonempty evidence and an empty basis string.
inference is a calculation or derived conclusion, not a source quotation;
basis must briefly identify its inputs/assumptions or calculation, not private
chain-of-thought. It may cite the Wiki statements used as inputs.
proposal is optional advice not stated by the Wiki; general is ordinary knowledge
not attributed to the Wiki. Both require empty evidence and empty basis strings.
uncertain preserves what is unsettled; basis briefly explains the missing or
conditional premise. It may cite relevant supplied evidence but never turns an
unknown premise into a fact. Do not turn this contract into a closed-book refusal.
evidence is an array; each entry has exactly handle and quote. Copy the handle
from this turn's citation_sources and quote a nonempty exact passage of at most
480 Unicode characters from that same supplied document body. Never combine
different passages or infer missing text. Multiple quotes per source are allowed
without duplicate handle/quote pairs. A quote proves occurrence, not entailment:
the claim still must accurately preserve its source's meaning and qualifications.
Do not insert Wiki markers or guess knowledge API/download URLs in any text field.
External sources may be linked normally in text and must remain distinct from
Wiki evidence; do not claim external verification without an actual tool result.
Use summary_ids to select the most important existing claims in summary order.
The server will repeat those exact claims and their types/grounds under the final
전체 요약 heading; do not generate an additional paraphrase or new summary claim.
The server renders readable chapters, each claim's evidence, separators and the
final summary. Do not mislabel advice or general knowledge to obtain a Wiki link.
No extra fields. Stay within the existing output budget without hiding relevant
conditions, exceptions, or uncertainty. This is one answer, not a review request."""

_LABELS = dict(wiki_fact="Wiki 기반 설명", inference="추론·계산", proposal="일반 제안",
    general="일반 지식", uncertain="미확인·조건부")
_CLAIM_KEYS = {"id", "kind", "text", "basis", "evidence"}


class ProvenanceValidationError(CitationValidationError):
    pass


def _invalid():
    raise ProvenanceValidationError("Invalid experimental answer provenance.")


def _object(value, keys):
    if type(value) is not dict or set(value) != keys:
        _invalid()
    return value


def _array(value, *, nonempty=False):
    if type(value) is not list or (nonempty and not value):
        _invalid()
    return value


def _text(value, *, blank=False):
    if (type(value) is not str or (not blank and not value.strip())
            or any((ord(char) < 32 and char not in "\n\r\t") or 0xD800 <= ord(char) <= 0xDFFF
                   for char in value)):
        _invalid()
    return value


def _pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            _invalid()
        value[key] = item
    return value


def _markdown(value, *, blank=False):
    value = _text(value, blank=blank)
    tokens = MarkdownIt("commonmark").enable(["table", "strikethrough"]).parse(value)
    def inspect(rows):
        for token in rows:
            if token.type in {"heading_open", "hr", "html_block", "html_inline"}:
                _invalid()
            if token.children:
                inspect(token.children)
    inspect(tokens)
    # Markers in a code example are inert in the default Markdown renderer.
    # Here every authoritative marker must come from structured evidence.
    if re.search(r"\[\[wiki\s*:", value, re.I):
        _invalid()
    return value


def _literal(value):
    # Keep source line boundaries. Escaping is presentation only; quote matching
    # and the trace always retain exact unnormalized source text.
    return re.sub(r"([\\`*_{}\[\]()#!|~+\-$])", r"\\\1", escape(value, quote=True))


def contiguous_quote_catalog(payload: dict, catalog: CitationCatalog) -> CitationCatalog:
    """Exclude synthetic joins between known source ranges, without new reads.

    The input body is unchanged. Legacy unannotated excerpts retain occurrence-
    only authority; this never claims their source offsets were verified.
    """
    annotated = {}
    sources = {source.handle: source for source in catalog.sources}
    for lane in ("retrieval_candidates", "wiki_body_evidence", "pins", "semantic_neighbors", "mandatory_contexts"):
        for row in payload.get(lane, []):
            handle = row.get("citation_handle")
            if handle not in sources:
                continue
            text = row.get("content_md") if lane == "mandatory_contexts" else row.get("excerpt_md")
            if text is None:  # Shared body belongs to the mandatory entry.
                continue
            coverage = row.get("body_coverage")
            if coverage is None:
                continue
            if (type(text) is not str or text not in sources[handle].passages
                    or type(coverage) is not dict or coverage.get("range_unit") != "unicode_code_points"
                    or type(coverage.get("complete")) is not bool):
                _invalid()
            ranges = _array(coverage.get("character_ranges"), nonempty=True)
            parts, offset, previous_end = [], 0, -1
            for pair in ranges:
                if (type(pair) is not list or len(pair) != 2
                        or any(type(value) is not int for value in pair)):
                    _invalid()
                start, end = pair
                if start < 0 or end <= start or start <= previous_end:
                    _invalid()
                if parts:
                    if text[offset:offset + 2] != "\n\n":
                        _invalid()
                    offset += 2  # Exact separator in select_evidence_passages.
                part = text[offset:offset + end - start]
                if len(part) != end - start:
                    _invalid()
                parts.append(part)
                offset += end - start
                previous_end = end
            if offset != len(text) or (coverage["complete"] and ranges != [[0, len(text)]]):
                _invalid()
            key = handle, text
            if key in annotated and annotated[key] != tuple(parts):
                _invalid()
            annotated[key] = tuple(parts)
    return replace(catalog, sources=tuple(replace(source,
        passages=tuple(part for text in source.passages
            for part in annotated.get((source.handle, text), (text,)))) for source in catalog.sources))


def _claim_markdown(claim):
    lines = [f"**{_LABELS[claim['kind']]}**", "", claim["text"]]
    if claim["basis"]:
        lines.extend(["", "입력·가정 또는 미확인 사유: " + claim["basis"]])
    for row in claim["evidence"]:
        # A source may itself document HTML, Wiki markers or protected routes.
        # Preserve that text as inert quoted code, never as a new live link.
        width = max([2, *[len(run) for run in re.findall(r"`+", row["quote"])]]) + 1
        fence = "`" * width
        lines.extend(["", "> 근거 구절:", ">", "> " + fence + "text",
            *["> " + line for line in row["quote"].splitlines()], "> " + fence, "",
            f"근거 문서: [[wiki:{row['handle']}]]"])
    if not claim["evidence"]:
        lines.extend(["", "근거 문서: " + _LABELS[claim["kind"]] + " (Wiki 근거 미사용)"])
    return "\n".join(lines)


def render_provenance(raw: str, catalog: CitationCatalog) -> tuple[str, dict]:
    """Validate exact structure/occurrence and render with no new factual text."""
    try:
        if type(raw) is not str or not raw.strip() or len(raw) > 32_000:
            _invalid()
        raw_bytes = raw.encode("utf-8")
        if type(catalog) is not CitationCatalog:
            _invalid()
        by_handle = {source.handle: source for source in catalog.sources}
        if len(by_handle) != len(catalog.sources):
            _invalid()
        value = json.loads(raw, object_pairs_hook=_pairs, parse_constant=lambda _: _invalid())
        _object(value, {"schema_version", "chapters", "summary_ids"})
        if type(value["schema_version"]) is not int or value["schema_version"] != 1:
            _invalid()
        claims, trace_claims, sections = {}, [], []
        for chapter in _array(value["chapters"], nonempty=True):
            _object(chapter, {"title", "claims"})
            title = _text(chapter["title"])
            if len(title) > 240 or "\n" in title or "\r" in title or title.strip() == "전체 요약":
                _invalid()
            section = ["## " + _literal(title)]
            for claim in _array(chapter["claims"], nonempty=True):
                _object(claim, _CLAIM_KEYS)
                identity = _text(claim["id"])
                if not re.fullmatch(r"C[0-9]{1,7}", identity) or identity in claims:
                    _invalid()
                kind = _text(claim["kind"])
                if kind not in _LABELS:
                    _invalid()
                _markdown(claim["text"])
                _markdown(claim["basis"], blank=True)
                evidence = _array(claim["evidence"])
                if (kind == "wiki_fact" and not evidence
                        or kind in {"general", "proposal"} and evidence
                        or kind in {"inference", "uncertain"} and not claim["basis"].strip()
                        or kind in {"wiki_fact", "general", "proposal"} and claim["basis"]):
                    _invalid()
                used, trace_evidence = set(), []
                for item in evidence:
                    _object(item, {"handle", "quote"})
                    handle, quote = _text(item["handle"]), _text(item["quote"])
                    pair = handle, quote
                    if (handle not in by_handle or len(quote) > 480 or pair in used
                            or not any(quote in passage for passage in by_handle[handle].passages)):
                        _invalid()
                    used.add(pair)
                    source = by_handle[handle]
                    trace_evidence.append(dict(handle=handle, quote=quote,
                        quote_sha256=sha256(quote.encode()).hexdigest(),
                        item_id=source.item_id, revision_id=source.revision_id))
                claims[identity] = claim
                trace_claims.append(dict(id=identity, kind=kind,
                    text_sha256=sha256(claim["text"].encode()).hexdigest(),
                    basis_sha256=sha256(claim["basis"].encode()).hexdigest(), evidence=trace_evidence))
                section.append(_claim_markdown(claim))
            sections.append("\n\n".join(section))
        summary = _array(value["summary_ids"], nonempty=True)
        if any(type(identity) is not str or identity not in claims for identity in summary):
            _invalid()
        if len(set(summary)) != len(summary):
            _invalid()
        sections.append("## 전체 요약\n\n" + "\n\n".join(_claim_markdown(claims[key]) for key in summary))
        markdown = "\n\n---\n\n".join(sections)
        # Keep the existing final-link, active-content and pre/post-render size
        # validation. Return markers so the executor uses its original renderer.
        render_citations(markdown, catalog)
        encoded = markdown.encode("utf-8")
        return markdown, dict(schema_version=1, protocol_version=PROVENANCE_PROMPT_VERSION,
            claim_support="not_semantically_verified", quote_check="exact_delivered_passage_occurrence",
            raw_response=raw, raw_response_sha256=sha256(raw_bytes).hexdigest(), raw_response_bytes=len(raw_bytes),
            rendered_markdown_sha256=sha256(encoded).hexdigest(), rendered_markdown_bytes=len(encoded),
            claims=trace_claims, summary_ids=list(summary), summary_strategy="reuse_selected_claims")
    except ProvenanceValidationError:
        raise
    except (ValueError, TypeError, RecursionError, UnicodeError) as exc:
        raise ProvenanceValidationError("Invalid experimental answer provenance.") from exc
