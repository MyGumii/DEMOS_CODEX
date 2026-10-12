"""Answer-only token admission over already authorized evidence.

Never searches another space, rewrites a Wiki, generates a summary, or changes
the lexical candidate set. Tables/fences are indivisible source blocks. Native
llama.cpp counting is optional and is restricted to the configured loopback
origin; other OpenAI-compatible providers use a labeled conservative estimate.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import math
import re
from urllib.parse import urlsplit

from markdown_it import MarkdownIt

from .answer_citations import prepare_citations
from .answer_input import answer_messages, factor_source_documents
from .evidence_passages import allocate_evidence_bytes


class AnswerTokenBudgetError(ValueError):
    pass


def model_message_counter(model_ids):
    """Use only the chosen model's existing auth/URL, without provider fallback."""
    from code_assist_v1.internal_llm import _merged_config, _candidate_configs
    if not model_ids:
        candidates = _candidate_configs("hynix_api")
        if len(candidates) != 1:
            raise AnswerTokenBudgetError("Token admission requires one unambiguous configured API model.")
        model_ids = (candidates[0][0],)
    if len(model_ids) != 1:
        raise AnswerTokenBudgetError("Token admission requires one explicit model.")
    from code_assist_v1.config import resolve_api_token
    import requests
    cfg = _merged_config(model_ids[0])
    url = urlsplit(str(cfg.get("url") or ""))
    if url.username or url.password or url.query or url.fragment:
        raise AnswerTokenBudgetError("Invalid configured token-count origin.")
    local = url.scheme in {"http", "https"} and url.hostname in {"127.0.0.1", "localhost", "::1"}
    if not local:
        def estimate(messages):
            return sum(len(str(row["content"]).encode("utf8")) + 32 for row in messages) + 512
        return estimate, "conservative_utf8_estimate"
    origin = f"{url.scheme}://{url.netloc}"
    token, _ = resolve_api_token(cfg)
    headers = {"Authorization": "Bearer " + token} if token else {}
    session = requests.Session()
    session.trust_env = False
    def post(route, body):
        response = session.post(origin + route, json=body, headers=headers, timeout=(3, 10),
                                allow_redirects=False, verify=cfg.get("tls_ca_bundle") or cfg.get("tls_verify", True))
        if response.status_code != 200 or len(response.content) > 4_194_304:
            raise AnswerTokenBudgetError("Native token counting is unavailable; no oversized model request was sent.")
        return response.json()
    cache = {}
    def count(messages):
        # The same model/template as the actual chat endpoint, no inference.
        import json
        key = json.dumps(messages, ensure_ascii=False, separators=(",", ":"))
        if key not in cache:
            prompt = post("/apply-template", {"messages": messages}).get("prompt")
            if not isinstance(prompt, str):
                raise AnswerTokenBudgetError("Native template returned no prompt.")
            tokens = post("/tokenize", {"content": prompt, "add_special": False,
                                        "parse_special": True}).get("tokens")
            if not isinstance(tokens, list) or any(type(value) is not int for value in tokens):
                raise AnswerTokenBudgetError("Native tokenizer returned invalid tokens.")
            cache[key] = len(tokens)
        return cache[key]
    return count, "llama_cpp_native_chat_template"


def _terms(value):
    words = set(re.findall(r"[^\W_]+", value.casefold()))
    grams = {word[i:i + 2] for word in words if re.fullmatch(r"[가-힣]+", word)
             for i in range(len(word) - 1)}
    return words | grams


@dataclass(frozen=True)
class Block:
    start: int
    end: int
    headings: tuple[tuple[int, int], ...]


def _blocks(text):
    lines = text.splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    tokens = MarkdownIt("commonmark").enable("table").parse(text)
    blocks, headings = [], []
    for token in tokens:
        if token.level != 0 or token.map is None or (token.nesting == -1):
            continue
        start, end = (offsets[index] for index in token.map)
        if token.type == "heading_open":
            depth = int(token.tag[1:])
            headings = [(d, a, b) for d, a, b in headings if d < depth]
            headings.append((depth, start, end))
            continue
        blocks.append(Block(start, end, tuple((a, b) for _, a, b in headings)))
    if not blocks and text:
        blocks.append(Block(0, len(text), ()))
    return blocks


def _merge(ranges):
    merged = []
    for start, end in sorted(set(ranges)):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return merged


def select_blocks(text, budget, query):
    """Return exact source ranges; never cut a table cell, formula or fence."""
    if len(text.encode("utf8")) <= budget:
        return text, [(0, len(text))]
    if budget <= 0:
        return "", []
    blocks = _blocks(text)
    terms = _terms(query)
    matched = [_terms(text[b.start:b.end] + " " + " ".join(text[a:z] for a, z in b.headings)) & terms
               for b in blocks]
    frequency = {term: sum(term in hit for hit in matched) for term in terms}
    scores = [sum(math.log1p(len(blocks) / frequency[t]) for t in sorted(hit)) for hit in matched]
    # Query-centered text first; nearby qualifications outrank distant zero hits.
    order = sorted(range(len(blocks)), key=lambda i: (-scores[i],
                   -max(scores[max(0, i-1):i] + scores[i+1:i+2] + [0]),
                   min(i, len(blocks)-1-i), i))
    ranges = []
    for index in order:
        block = blocks[index]
        candidate = _merge([*ranges, *block.headings, (block.start, block.end)])
        value = "\n\n".join(text[a:b] for a, b in candidate)
        if len(value.encode("utf8")) <= budget:
            ranges = candidate
    return "\n\n".join(text[a:b] for a, b in ranges), ranges


def _source_ranges(coverage, local_ranges, old_text):
    """Compose passage offsets through existing synthetic two-LF separators."""
    original = coverage.get("character_ranges") or [[0, len(old_text)]]
    result, position = [], 0
    for start, end in original:
        width = end - start
        for a, b in local_ranges:
            left, right = max(a, position), min(b, position + width)
            if left < right:
                result.append((start + left - position, start + right - position))
        position += width + 2
    return [list(pair) for pair in _merge(result)]


def _body_rows(payload):
    rows = []
    # Mandatory context keeps its existing priority, not an arbitrary top-N cap.
    for lane, field in (("mandatory_contexts", "content_md"), ("retrieval_candidates", "excerpt_md"),
                        ("wiki_body_evidence", "excerpt_md"), ("pins", "excerpt_md"),
                        ("semantic_neighbors", "excerpt_md")):
        for row in payload.get(lane, []):
            if isinstance(row.get(field), str) and row[field].strip():
                rows.append((lane, row, field))
    return rows


def _narrow(original, body_budget):
    value = deepcopy(original)
    rows = _body_rows(value)
    sizes = tuple(len(row[field].encode("utf8")) for _, row, field in rows)
    allocations = allocate_evidence_bytes(sizes, body_budget)
    omitted, truncated = 0, 0
    for (lane, row, field), allocation in zip(rows, allocations):
        before = row[field]
        selected, ranges = select_blocks(before, allocation, str(value.get("query", "")))
        row[field] = selected
        coverage = row.get("body_coverage")
        if isinstance(coverage, dict) and selected != before:
            coverage["character_ranges"] = _source_ranges(coverage, ranges, before)
            coverage["complete"] = False
        omitted += int(not selected)
        truncated += int(selected != before)
    available_mandatory = {row["identity"] for row in value.get("mandatory_contexts", []) if row.get("content_md")}
    for lane, field in (("mandatory_contexts", "content_md"), ("retrieval_candidates", "excerpt_md"),
                        ("wiki_body_evidence", "excerpt_md"), ("pins", "excerpt_md"),
                        ("semantic_neighbors", "excerpt_md")):
        value[lane] = [row for row in value.get(lane, []) if row.get(field)
                       or row.get("mandatory_context_identity") in available_mandatory]
    remaining = _body_rows(value)
    keys = {(row.get("item_id") or row.get("target_item_id") or row.get("neighbor_item_id"),
             row.get("revision_id") or row.get("target_revision_id") or row.get("neighbor_revision_id"))
            for _, row, _ in remaining}
    value["source_documents"] = [row for row in value.get("source_documents", [])
                                 if (row["item_id"], row["revision_id"]) in keys]
    status = value.setdefault("wiki_evidence_status", {})
    status.update(retrieved_body_excerpt_count=len(remaining),
                  retrieved_body_excerpt_bytes=sum(len(row[field].encode("utf8")) for _, row, field in remaining),
                  some_body_excerpts_truncated=bool(truncated) or status.get("some_body_excerpts_truncated", False),
                  coverage_truncated=bool(truncated) or status.get("coverage_truncated", False))
    value["answer_token_selection"] = {"policy": "exact-markdown-blocks-v1",
        "body_budget_bytes": body_budget, "omitted_body_count": omitted,
        "narrowed_body_count": truncated, "stored_wiki_changed": False}
    return value


def fit_answer_payload(original, *, system, turn_id, view_id, count_messages,
                       context_limit, output_tokens=3072, safety_tokens=512):
    """Measure the complete final payload with citations/template, not text/4."""
    if context_limit <= output_tokens + safety_tokens:
        raise AnswerTokenBudgetError("No input budget remains after output reservation.")
    available = context_limit - output_tokens - safety_tokens
    def measure(value):
        prepared, _ = prepare_citations(value, turn_id=turn_id, selected_view_space_id=view_id)
        prepared = factor_source_documents(prepared)
        return count_messages(answer_messages(prepared, system=system))
    full_tokens = measure(original)
    if full_tokens <= available:
        return original, {"input_tokens": full_tokens, "provider_context_limit_tokens": context_limit,
                          "reserved_output_tokens": output_tokens, "safety_tokens": safety_tokens,
                          "selection_applied": False}
    baseline = deepcopy(original)
    # Preserve the current question. Discard old history only if its metadata
    # alone prevents any evidence; never prefix-clip a user question.
    while measure(_narrow(baseline, 0)) >= available and baseline.get("recent_messages"):
        baseline["recent_messages"] = baseline["recent_messages"][1:]
    empty_tokens = measure(_narrow(baseline, 0))
    if empty_tokens >= available:
        raise AnswerTokenBudgetError("Question and mandatory metadata alone exceed the model input budget.")
    total = sum(len(row[field].encode("utf8")) for _, row, field in _body_rows(baseline))
    body_budget = int(total * max(0.05, (available-empty_tokens)/(full_tokens-empty_tokens)) * .92)
    for _ in range(12):
        candidate = _narrow(baseline, body_budget)
        tokens = measure(candidate)
        if tokens <= available:
            if _body_rows(original) and not _body_rows(candidate):
                raise AnswerTokenBudgetError("No complete evidence block fits; refusing a title-only answer.")
            return candidate, {"input_tokens": tokens, "provider_context_limit_tokens": context_limit,
                              "original_input_tokens": full_tokens, "reserved_output_tokens": output_tokens,
                              "safety_tokens": safety_tokens, "selection_applied": True,
                              **candidate["answer_token_selection"]}
        body_budget = int(body_budget * .8)
    raise AnswerTokenBudgetError("Could not admit a complete model request within its input budget.")
