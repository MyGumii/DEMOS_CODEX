"""Map/reduce integrity tests; fake prose is never semantic-quality evidence."""
from dataclasses import replace
from dataclasses import asdict
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

import pytest

from code_assist_v1.knowledge_core.wiki_authoring import (
    AuthoringIncompleteError, AuthoringOutputError, AuthoringWorkLimitError,
    AuthoringModelPolicy, AuthoringWorkPolicy, ExtractionSource, WikiChunkAuthor, plan_authoring,
)
from code_assist_v1.knowledge_core.wiki_synthesis import (
    SynthesisPolicy, SYNTHESIS_PROMPT, source_cards, plan_reduction_batches,
    WikiReductionAuthor, validate_reduction, synthesize_wiki, advance_synthesis_wave,
    _card, _make_batch,
)


pytestmark = [pytest.mark.unit, pytest.mark.scenario("UPLOAD-005")]
_META = {"model_kind": "codex", "model_id": "fixture"}


def _case(text, *, chunk_bytes=256, work_policy=None):
    source = ExtractionSource(str(uuid4()), sha256(text.encode()).hexdigest(), text)
    plan = plan_authoring(source, chunk_bytes=chunk_bytes, work_policy=work_policy,
        model_policy=AuthoringModelPolicy(provider_order=("codex",), model_ids=("fixture",)))
    def runner(task, payload, **kwargs):
        value = {"schema_version": 1, "chunk_id": payload["chunk_id"], "sections": [{
            "heading": "원문 요지", "claims": [{"text": "해당 원문 구간의 제안이다.", "quotes": [payload["source"]]}]}], "warnings": []}
        return json.dumps(value, ensure_ascii=False), _META
    author = WikiChunkAuthor(runner=runner)
    return plan, [author.author(plan, chunk.ordinal) for chunk in plan.chunks]


def _response(payload, *, text="조건과 예외를 함께 정리한 검증용 통합문이다."):
    return json.dumps({"schema_version": 3, "batch_id": payload["batch_id"],
        "sections": [{"heading": "통합 항목", "claims": [{"text": text,
            "source_ids": [card["id"] for card in payload["cards"] if card["kind"] != "qualification"],
            "context_quotes": []}]}], "unresolved_ids": [],
        "context_dispositions": [{"source_id": card["id"],
            "disposition": "integrated" if card["kind"] == "boundary" else "resolved",
            "reason": "검증 fixture의 명시적 문맥 처리 기록."}
            for card in payload["cards"] if card["kind"] != "claim"]}, ensure_ascii=False)


def test_small_complete_source_needs_no_extra_reduction_call():
    plan, maps = _case("# 짧은 원문\n조건과 예외를 함께 기록한다.", chunk_bytes=1024)
    wiki = synthesize_wiki(plan, maps, runner=lambda *a, **kw: pytest.fail("unnecessary reduction"))
    manifest = json.loads(wiki.manifest_json)
    assert manifest["synthesis"]["model_calls"] == 0
    assert not manifest["synthesis"]["compression_verified"]
    assert not manifest["semantic_support_verified"]


def test_boundary_context_is_exact_and_all_map_cards_are_accounted():
    source = "조건과 예외가 여러 구간을 넘어 이어진다. " * 30 + "최종 단서."
    plan, maps = _case(source)
    calls = []
    def runner(task, payload, **kwargs):
        calls.append(payload)
        assert task == "canonical_wiki_synthesis"
        assert "최종 단서" not in SYNTHESIS_PROMPT  # no question/fact-specific prompt
        assert kwargs["messages"][0]["content"] == SYNTHESIS_PROMPT
        for passage in payload["source_passages"]:
            start, end = passage["range"]
            assert passage["text"] == source[start:end]
        response = _response(payload)
        assert kwargs["validate_output"](response) == ""
        return response, _META
    wiki = synthesize_wiki(plan, maps, runner=runner)
    manifest = json.loads(wiki.manifest_json)
    assert calls and any(card["kind"] == "boundary" for card in calls[0]["cards"])
    synthesis = manifest["synthesis"]
    assert synthesis["all_input_cards_accounted"] and not synthesis["compression_verified"]
    final = synthesis["final_cards"][0]
    assert set(synthesis["rounds"][0]["input_card_ids"]) == set(final["parent_ids"] + final["context_ids"])
    assert final["evidence_ranges"] == [[0, len(source)]]
    assert manifest["rendered_markdown_sha256"] == sha256(wiki.markdown.encode()).hexdigest()
    assert manifest["source_sha256"] == plan.source.content_sha256


def test_pdf_page_break_context_triggers_reduction_even_for_one_map_chunk():
    source = "기존 조건을 대조군으로 다시 포함\n\n[Page 2]\n보고서\n2\n하여 회차 간 차이를 확인한다."
    plan, maps = _case(source, chunk_bytes=4096)
    cards, _ = source_cards(plan, maps)
    boundary = [card for card in cards if card.kind == "boundary"]
    assert boundary and boundary[0].context_ranges == ((0, len(source)),)
    assert boundary[0].evidence_ranges == ()
    calls = []
    wiki = synthesize_wiki(plan, maps, runner=lambda task, payload, **kw:
        (calls.append(payload) or _response(payload), _META))
    assert len(calls) == 1 and json.loads(wiki.manifest_json)["synthesis"]["model_calls"] == 1


@pytest.mark.parametrize("mutation", ["omitted", "unknown", "duplicate", "no-support", "wrong-batch", "unresolved"])
def test_reduction_never_silently_drops_or_rebinds_input_cards(mutation):
    plan, maps = _case("원문 내용이 이어진다. " * 30)
    cards, _ = source_cards(plan, maps)
    batch = plan_reduction_batches(plan, cards, 1)[0]
    value = json.loads(_response(json.loads(batch.payload_json)))
    claim = value["sections"][0]["claims"][0]
    if mutation == "omitted":
        claim["source_ids"].pop()
    elif mutation == "unknown":
        claim["source_ids"].append("unknown-id")
    elif mutation == "duplicate":
        claim["source_ids"].append(claim["source_ids"][0])
    elif mutation == "no-support":
        claim["source_ids"] = []
    elif mutation == "wrong-batch":
        value["batch_id"] = "stale-batch"
    else:
        value["unresolved_ids"] = [claim["source_ids"][0]]
    with pytest.raises((AuthoringOutputError, AuthoringIncompleteError)):
        validate_reduction(json.dumps(value), batch)


def test_multi_round_reduction_preserves_transitive_source_ranges_and_byte_bounds():
    source = "각 구간의 조건과 예외를 보존한다.\n" * 600
    plan, maps = _case(source, chunk_bytes=512)
    policy = SynthesisPolicy(input_bytes=7000, boundary_context_characters=32)
    observed = []
    def runner(task, payload, **kwargs):
        observed.append(payload)
        wire_messages = json.dumps(kwargs["messages"], ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        assert len(wire_messages) <= policy.input_bytes
        if payload["round_number"] > 1:
            assert payload["source_passages"] == []  # do not falsely claim another full-source read
        return _response(payload), _META
    wiki = synthesize_wiki(plan, maps, runner=runner, policy=policy)
    summary = json.loads(wiki.manifest_json)["synthesis"]
    assert max(row["round_number"] for row in observed) >= 2
    assert summary["final_cards"][0]["evidence_ranges"] == [[0, len(source)]]
    assert summary["model_calls"] == len(observed)


def test_cumulative_map_and_reduce_budget_exhaustion_fails_before_reduction_call():
    source = "원문 조건이 이어진다.\n" * 40
    plan, maps = _case(source)
    constrained = replace(plan.work_policy, model_attempts=len(plan.chunks) * plan.model_policy.max_attempts)
    plan, maps = _case(source, work_policy=constrained)
    with pytest.raises(AuthoringWorkLimitError, match="cumulative"):
        synthesize_wiki(plan, maps, runner=lambda *a, **kw: pytest.fail("no extra budget"))


def test_oversized_claim_context_is_not_prefix_truncated():
    plan, maps = _case("원문 " * 100, chunk_bytes=256)
    policy = SynthesisPolicy(input_bytes=100)
    cards, _ = source_cards(plan, maps, policy=policy)
    with pytest.raises(AuthoringWorkLimitError, match="complete claim"):
        plan_reduction_batches(plan, cards, 1, policy=policy)


def test_forged_batch_is_rejected_before_model_call():
    plan, maps = _case("원문 경계 " * 50)
    cards, _ = source_cards(plan, maps)
    batch = plan_reduction_batches(plan, cards, 1)[0]
    with pytest.raises(AuthoringIncompleteError, match="frozen"):
        WikiReductionAuthor(runner=lambda *a, **kw: pytest.fail("must not call")).author(
            plan, replace(batch, payload_json="{}"), wave=cards)


def test_synthesized_text_cannot_create_active_links_or_html():
    plan, maps = _case("원문 경계 " * 50)
    hostile = '<script>alert(1)</script> [링크](javascript:alert) [[item:00000000-0000-4000-8000-000000000001]]'
    wiki = synthesize_wiki(plan, maps, runner=lambda task, payload, **kw: (_response(payload, text=hostile), _META))
    assert "<script>" not in wiki.markdown and "[[item:" not in wiki.markdown and "[링크](javascript:" not in wiki.markdown
    assert "&lt;script&gt;" in wiki.markdown
    assert not json.loads(wiki.manifest_json)["semantic_support_verified"]


def _narrow_evidence_case(source="Known fact.\nBoundary exception is deferred.\nOther context."):
    plan, _ = _case(source, chunk_bytes=4096)
    fact = _card(plan.source.snapshot_id, "fact", "Fact", "Known fact.", [(0, 11)])
    context = _card(plan.source.snapshot_id, "boundary", "Context", "Read adjacent context.",
                    [(0, len(source))], "boundary")
    batch = _make_batch(plan, (fact, context), 1, SynthesisPolicy())
    return plan, fact, context, batch


def test_context_range_is_never_promoted_to_exact_evidence():
    plan, fact, context, batch = _narrow_evidence_case()
    reduced = validate_reduction(_response(json.loads(batch.payload_json)), batch)[0]
    assert reduced.evidence_ranges == fact.evidence_ranges
    assert reduced.context_ranges == context.context_ranges
    assert reduced.parent_ids == (fact.card_id,)
    assert reduced.context_ids == (context.card_id,)


@pytest.mark.parametrize("kind", ["boundary", "qualification"])
def test_context_only_fabrication_is_rejected(kind):
    plan, fact, context, batch = _narrow_evidence_case()
    context = replace(context, kind=kind)
    batch = _make_batch(plan, (context,), 1, SynthesisPolicy())
    value = json.loads(_response(json.loads(batch.payload_json)))
    value["sections"][0]["claims"][0]["source_ids"] = [context.card_id]
    with pytest.raises(AuthoringOutputError, match="Context-only"):
        validate_reduction(json.dumps(value), batch)


def test_new_boundary_fact_requires_a_narrow_exact_quote_receipt():
    plan, fact, context, batch = _narrow_evidence_case()
    value = json.loads(_response(json.loads(batch.payload_json)))
    quote = "Boundary exception is deferred."
    value["sections"][0]["claims"].append({"text": "A deferred exception.",
        "source_ids": [context.card_id], "context_quotes": [{"source_id": context.card_id, "text": quote}]})
    reduced = validate_reduction(json.dumps(value), batch)[1]
    start = plan.source.markdown.index(quote)
    assert reduced.evidence_ranges == ((start, start + len(quote)),)
    assert reduced.parent_ids == () and reduced.context_ids == (context.card_id,)
    assert reduced.context_quote_receipts[0][1:] == (start, start + len(quote), sha256(quote.encode()).hexdigest())


@pytest.mark.parametrize("source,quote", [
    ("Known fact.\nSame sentence. Same sentence.", "Same sentence."),
    ("Known fact.\n한글\r\n예외 🙂", "한글\n예외 🙂"),
    ("Known fact.\n한글 예외 🙂", "한글 예외 🙃"),
])
def test_ambiguous_or_normalized_boundary_quotes_are_rejected(source, quote):
    plan, fact, context, batch = _narrow_evidence_case(source)
    value = json.loads(_response(json.loads(batch.payload_json)))
    value["sections"][0]["claims"][0]["context_quotes"] = [{"source_id": context.card_id, "text": quote}]
    with pytest.raises(AuthoringOutputError, match="exactly and unambiguously"):
        validate_reduction(json.dumps(value), batch)


@pytest.mark.parametrize("input_bytes", [7000, 10_000, 14_000])
def test_dense_page_markers_remain_atomic_and_context_batches_have_neighboring_facts(input_bytes):
    source = "\n".join(f"[Page {i}]\n" + f"구간 {i}의 별도 조건을 보존한다. " * 5 for i in range(1, 21))
    plan, maps = _case(source, chunk_bytes=256)
    policy = SynthesisPolicy(input_bytes=input_bytes, boundary_context_characters=100)
    cards, _ = source_cards(plan, maps, policy=policy)
    boundaries = [card for card in cards if card.kind == "boundary"]
    assert len(boundaries) >= 19
    assert all(not card.evidence_ranges and len(card.context_ranges) == 1 and
               card.context_ranges[0][1] - card.context_ranges[0][0] <= 200 for card in boundaries)
    batches = plan_reduction_batches(plan, cards, 1, policy=policy)
    assert len(batches) > 1
    assert all(any(card.kind == "claim" for card in batch.cards) for batch in batches)
    assert {card.card_id for batch in batches for card in batch.cards} == {card.card_id for card in cards}
    assert Counter(card.card_id for batch in batches for card in batch.cards) == Counter(card.card_id for card in cards)
    assert any(batch.helper_cards for batch in batches)
    assert all(not ({card.card_id for card in batch.helper_cards} &
                    {card.card_id for card in batch.cards}) for batch in batches)
    assert all(len(batch.messages_json.encode()) <= policy.input_bytes for batch in batches)


def test_neighbor_helpers_cannot_be_claimed_as_owned_evidence():
    plan, maps = _case("구간 조건을 보존한다. " * 200, chunk_bytes=256)
    policy = SynthesisPolicy(input_bytes=7000, boundary_context_characters=50)
    cards, _ = source_cards(plan, maps, policy=policy)
    batches = plan_reduction_batches(plan, cards, 1, policy=policy)
    batch = next(batch for batch in batches if batch.helper_cards)
    value = json.loads(_response(json.loads(batch.payload_json)))
    value["sections"][0]["claims"][0]["source_ids"].append(batch.helper_cards[0].card_id)
    with pytest.raises(AuthoringOutputError, match="supporting card"):
        validate_reduction(json.dumps(value), batch, policy=policy)
    assert Counter(card.card_id for batch in batches for card in batch.cards) == Counter(card.card_id for card in cards)


def test_planning_work_exhaustion_is_explicit_not_a_partial_prefix():
    plan, maps = _case("조건과 예외를 기록한다. " * 20)
    policy = SynthesisPolicy(planning_serialized_bytes=1)
    cards, _ = source_cards(plan, maps, policy=policy)
    with pytest.raises(AuthoringWorkLimitError, match="planning serialization"):
        plan_reduction_batches(plan, cards, 1, policy=policy)


def test_disjoint_boundary_quotes_do_not_promote_intervening_text():
    plan, fact, context, batch = _narrow_evidence_case(
        "Known fact.\r\nCondition A.\r\n[Page 2]\r\nUnrelated header.\r\nException B.")
    value = json.loads(_response(json.loads(batch.payload_json)))
    fragments = ["Condition A.", "Exception B."]
    value["sections"][0]["claims"].append({"text": "Condition with exception.",
        "source_ids": [context.card_id], "context_quotes": [
            {"source_id": context.card_id, "text": fragment} for fragment in fragments]})
    card = validate_reduction(json.dumps(value), batch)[1]
    assert card.evidence_ranges == tuple((plan.source.markdown.index(fragment),
        plan.source.markdown.index(fragment) + len(fragment)) for fragment in fragments)
    assert len(card.context_quote_receipts) == 2


def test_same_boundary_quote_has_one_identity_across_overlapping_windows():
    plan, fact, context, _ = _narrow_evidence_case()
    second = _card(plan.source.snapshot_id, "other-window", "Context", "Neighbor context.",
        [(11, len(plan.source.markdown))], "boundary")
    batch = _make_batch(plan, (fact, context, second), 1, SynthesisPolicy())
    value = json.loads(_response(json.loads(batch.payload_json)))
    quote = "Boundary exception is deferred."
    value["sections"][0]["claims"][0]["context_quotes"] = [
        {"source_id": card.card_id, "text": quote} for card in (context, second)]
    card = validate_reduction(json.dumps(value), batch)[0]
    assert len(card.context_quote_receipts) == 1
    assert card.evidence_ranges == ((0, 11), (12, 12 + len(quote)))


def test_quote_outside_its_named_context_is_not_accepted_from_another_passage():
    plan, fact, context, _ = _narrow_evidence_case()
    narrow = _card(plan.source.snapshot_id, "narrow", "Context", "Narrow context.", [(0, 11)], "boundary")
    batch = _make_batch(plan, (fact, context, narrow), 1, SynthesisPolicy())
    value = json.loads(_response(json.loads(batch.payload_json)))
    value["sections"][0]["claims"][0]["context_quotes"] = [
        {"source_id": narrow.card_id, "text": "Boundary exception is deferred."}]
    with pytest.raises(AuthoringOutputError, match="exactly and unambiguously"):
        validate_reduction(json.dumps(value), batch)


def test_no_progress_stops_before_another_nonterminal_wave_but_terminal_echo_is_valid():
    plan, maps = _case("여러 구간의 조건을 그대로 보존한다. " * 1800, chunk_bytes=512)
    policy = SynthesisPolicy(input_bytes=7000, boundary_context_characters=10)
    observed = []
    def echo(task, payload, **kwargs):
        observed.append(payload["round_number"])
        facts = [card for card in payload["cards"] if card["kind"] == "claim"]
        contexts = [card["id"] for card in payload["cards"] if card["kind"] != "claim"]
        value = {"schema_version": 3, "batch_id": payload["batch_id"], "sections": [
            {"heading": card["heading"], "claims": [{"text": card["text"],
             "source_ids": [card["id"], *(contexts if ordinal == 0 else [])], "context_quotes": []}]}
            for ordinal, card in enumerate(facts)], "unresolved_ids": [],
            "context_dispositions": [{"source_id": key, "disposition": "integrated", "reason": "fixture context"}
                                     for key in contexts]}
        return json.dumps(value, ensure_ascii=False), _META
    with pytest.raises(AuthoringWorkLimitError, match="no progress"):
        synthesize_wiki(plan, maps, runner=echo, policy=policy)
    assert max(observed) == 2
    small_plan, small_maps = _case("작은 문서의 상충 조건을 임의로 없애지 않는다. " * 10)
    result = synthesize_wiki(small_plan, small_maps, runner=echo)
    assert json.loads(result.manifest_json)["synthesis"]["model_calls"] == 1


def test_whole_map_completion_is_distinct_from_partial_batch_and_original_passages():
    plan, maps = _case("각 구간은 독립 조건을 보유한다. " * 250, chunk_bytes=512)
    policy = SynthesisPolicy(input_bytes=10_000, boundary_context_characters=30)
    cards, _ = source_cards(plan, maps, policy=policy)
    batches = plan_reduction_batches(plan, cards, 1, policy=policy)
    assert len(batches) > 1
    for batch in batches:
        coverage = json.loads(batch.payload_json)["document_coverage"]
        assert coverage["map_processing"] == "complete_document"
        assert coverage["map_chunks_verified"] == coverage["map_chunks_total"] == len(plan.chunks)
        assert coverage["batch_card_coverage"] == "partial_wave"
        assert coverage["batch_card_count"] == len(batch.cards) < coverage["wave_card_count"] == len(cards)
        assert coverage["original_text_in_this_call"] == "selected_exact_passages"
        assert not coverage["semantic_source_coverage_verified"]
        assert coverage["map_completion_id"] == cards.map_completion_id
        assert coverage["wave_id"] == cards.wave_id
    author = WikiReductionAuthor(runner=lambda task, payload, **kw: (_response(payload), _META))
    completed = [author.author(plan, batch, wave=cards, policy=policy) for batch in batches]
    next_wave = advance_synthesis_wave(plan, cards, batches, completed, policy=policy)
    later = plan_reduction_batches(plan, next_wave, 2, policy=policy)[0]
    coverage = json.loads(later.payload_json)["document_coverage"]
    assert coverage["batch_card_coverage"] == "complete_wave"
    assert coverage["original_text_in_this_call"] == "not_reread"
    with pytest.raises(AuthoringIncompleteError, match="frozen"):
        WikiReductionAuthor(runner=lambda *a, **kw: pytest.fail("no call")).author(
            plan, replace(batches[0], wave_card_count=9999), wave=cards, policy=policy)


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "unknown", "empty-reason", "claim-id", "invalid-state"])
def test_context_dispositions_are_required_exactly_once_and_owned(mutation):
    plan, fact, context, batch = _narrow_evidence_case()
    value = json.loads(_response(json.loads(batch.payload_json)))
    dispositions = value["context_dispositions"]
    if mutation == "missing":
        dispositions.clear()
    elif mutation == "duplicate":
        dispositions.append(dict(dispositions[0]))
    elif mutation == "unknown":
        dispositions[0]["source_id"] = "not-owned"
    elif mutation == "empty-reason":
        dispositions[0]["reason"] = " "
    elif mutation == "claim-id":
        dispositions[0]["source_id"] = fact.card_id
    else:
        dispositions[0]["disposition"] = []
    with pytest.raises((AuthoringOutputError, AuthoringIncompleteError)):
        validate_reduction(json.dumps(value), batch)


def test_resolved_chunk_warning_is_not_rewritten_as_a_factual_claim():
    plan, fact, context, _ = _narrow_evidence_case()
    warning = _card(plan.source.snapshot_id, "warning", "Map warning", "Only this chunk was supplied.",
        [(0, len(plan.source.markdown))], "qualification")
    batch = _make_batch(plan, (fact, warning), 1, SynthesisPolicy())
    value = json.loads(_response(json.loads(batch.payload_json)))
    cards = validate_reduction(json.dumps(value), batch)
    assert len(cards) == 1 and cards[0].kind == "claim"
    assert warning.card_id not in cards[0].parent_ids and not cards[0].context_ids
    value["sections"][0]["claims"][0]["source_ids"].append(warning.card_id)
    with pytest.raises(AuthoringOutputError, match="qualifications"):
        validate_reduction(json.dumps(value), batch)


def test_partially_recovered_boundary_preserves_unresolved_residual_separately():
    plan, fact, context, batch = _narrow_evidence_case()
    value = json.loads(_response(json.loads(batch.payload_json)))
    value["context_dispositions"][0].update(disposition="retained", reason="The approval authority is absent from the source.")
    cards = validate_reduction(json.dumps(value), batch)
    assert len(cards) == 2 and cards[0].kind == "claim" and cards[1].kind == "qualification"
    assert cards[1].evidence_ranges == () and cards[1].context_ids == (context.card_id,)
    assert cards[1].context_ranges == context.context_ranges
    later = _make_batch(plan, cards, 2, SynthesisPolicy())
    response = json.loads(_response(json.loads(later.payload_json)))
    response["context_dispositions"][0].update(disposition="retained", reason=cards[1].text)
    final = validate_reduction(json.dumps(response), later)
    assert final[1].kind == "qualification" and final[1].context_ids == (cards[1].card_id,)


def test_retained_warnings_render_separately_and_disposition_history_is_preserved():
    plan, maps = _case("구간에 실제 미확정 조건이 있다. " * 35)
    def runner(task, payload, **kwargs):
        value = json.loads(_response(payload))
        for disposition in value["context_dispositions"]:
            disposition.update(disposition="retained", reason="검증용 실제 미확정 조건.")
        return json.dumps(value), _META
    wiki = synthesize_wiki(plan, maps, runner=runner)
    manifest = json.loads(wiki.manifest_json)
    assert "## 작성 시 확인할 사항" in wiki.markdown
    assert manifest["synthesis"]["all_contexts_dispositioned"]
    assert manifest["synthesis"]["rounds"][0]["context_dispositions"]
    assert not manifest["semantic_support_verified"]


def test_self_consistent_one_card_subset_is_not_verified_whole_document_coverage():
    plan, maps = _case("여러 문맥의 독립 조건이다. " * 60)
    wave, _ = source_cards(plan, maps)
    assert len(wave) > 1
    unprepared = _make_batch(plan, (wave[0],), 1, SynthesisPolicy())
    coverage = json.loads(unprepared.payload_json)["document_coverage"]
    assert coverage["map_processing"] == coverage["batch_card_coverage"] == "unverified"
    assert coverage["map_chunks_verified"] == coverage["wave_card_count"] == 0
    assert coverage["map_completion_id"] is coverage["wave_id"] is None
    author = WikiReductionAuthor(runner=lambda *a, **kw: pytest.fail("unverified subset sent"))
    with pytest.raises(AuthoringIncompleteError, match="frozen"):
        author.author(plan, unprepared)
    with pytest.raises(AuthoringIncompleteError, match="frozen"):
        author.author(plan, unprepared, wave=wave)
    with pytest.raises(AuthoringIncompleteError, match="frozen"):
        plan_reduction_batches(plan, wave.cards[:1], 1)
    partial = _make_batch(plan, (wave[0],), 1, SynthesisPolicy(), wave=wave)
    assert json.loads(partial.payload_json)["document_coverage"]["batch_card_coverage"] == "partial_wave"


@pytest.mark.parametrize("mutation", ["subset", "same-count-replacement", "order", "map", "predecessor", "receipt", "hash", "seal"])
def test_prepared_wave_card_identity_and_ancestry_cannot_be_rebound(mutation):
    plan, maps = _case("독립적으로 보존할 조건과 예외다. " * 50)
    wave, _ = source_cards(plan, maps)
    batch = plan_reduction_batches(plan, wave, 1)[0]
    if mutation == "subset":
        bad = replace(wave, cards=wave.cards[:1])
    elif mutation == "same-count-replacement":
        bad = replace(wave, cards=(replace(wave[0], text="changed fact"), *wave.cards[1:]))
    elif mutation == "order":
        bad = replace(wave, cards=tuple(reversed(wave.cards)))
    elif mutation == "map":
        bad = replace(wave, map_completion_id="M" + "0" * 64)
    elif mutation == "predecessor":
        bad = replace(wave, predecessor_id="W" + "0" * 64)
    elif mutation == "receipt":
        bad = replace(wave, completion_receipts=(("Bfake", "request", "response", "receipt"),))
    elif mutation == "hash":
        bad = replace(wave, wave_id="W" + "0" * 64)
    else:
        bad = replace(wave, preparation_seal="0" * 64)
    with pytest.raises(AuthoringIncompleteError, match="frozen"):
        WikiReductionAuthor(runner=lambda *a, **kw: pytest.fail("tampered wave sent")).author(
            plan, batch, wave=bad)


def _completed_wave_case():
    policy = SynthesisPolicy(input_bytes=7000, boundary_context_characters=20)
    plan, maps = _case("여러 사용자의 별도 조건을 모두 보존한다. " * 130, chunk_bytes=256)
    wave, _ = source_cards(plan, maps, policy=policy)
    batches = plan_reduction_batches(plan, wave, 1, policy=policy)
    assert len(batches) > 1
    author = WikiReductionAuthor(runner=lambda task, payload, **kw: (_response(payload), _META))
    completed = tuple(author.author(plan, batch, wave=wave, policy=policy) for batch in batches)
    return plan, maps, policy, wave, batches, completed


@pytest.mark.parametrize("mutation", ["missing-result", "duplicate-result", "wrong-id", "wrong-request",
    "changed-card", "changed-disposition", "wrong-provider", "missing-batch", "duplicate-batch"])
def test_advancing_requires_every_exact_predecessor_response(mutation):
    plan, maps, policy, wave, batches, completed = _completed_wave_case()
    if mutation == "missing-result":
        completed = completed[:-1]
    elif mutation == "duplicate-result":
        completed = (*completed, completed[0])
    elif mutation == "wrong-id":
        completed = (replace(completed[0], batch_id="wrong-batch"), *completed[1:])
    elif mutation == "wrong-request":
        completed = (replace(completed[0], request_sha256="0" * 64), *completed[1:])
    elif mutation == "changed-card":
        first = completed[0]
        completed = (replace(first, cards=(replace(first.cards[0], text="lost condition"),)), *completed[1:])
    elif mutation == "changed-disposition":
        completed = (replace(completed[0], context_dispositions_json="[]"), *completed[1:])
    elif mutation == "wrong-provider":
        completed = (replace(completed[0], model=(("model_kind", "hynix_api"), ("model_id", "fixture"))), *completed[1:])
    elif mutation == "missing-batch":
        batches = batches[:-1]
    else:
        batches = (*batches, batches[0])
    with pytest.raises((AuthoringIncompleteError, AuthoringOutputError)):
        advance_synthesis_wave(plan, wave, batches, completed, policy=policy)


def test_complete_map_and_response_replay_reconstructs_the_same_ordered_wave():
    plan, maps, policy, wave, batches, completed = _completed_wave_case()
    replay, _ = source_cards(plan, tuple(reversed(maps)), policy=policy)
    assert replay == wave
    next_wave = advance_synthesis_wave(plan, wave, batches, completed, policy=policy)
    replay_next = advance_synthesis_wave(plan, replay, batches, tuple(reversed(completed)), policy=policy)
    assert next_wave == replay_next
    assert next_wave.map_completion_id == wave.map_completion_id
    assert next_wave.predecessor_id == wave.wave_id
    assert next_wave.wave_id != wave.wave_id
    assert [receipt[0] for receipt in next_wave.completion_receipts] == [batch.batch_id for batch in batches]
    for batch in plan_reduction_batches(plan, next_wave, 2, policy=policy):
        coverage = json.loads(batch.payload_json)["document_coverage"]
        assert coverage["map_completion_id"] == wave.map_completion_id
        assert coverage["wave_id"] == next_wave.wave_id
        assert coverage["original_text_in_this_call"] == "not_reread"
    with pytest.raises(AuthoringIncompleteError, match="Every source chunk"):
        source_cards(plan, maps[:-1], policy=policy)


def test_preparation_cannot_be_reused_after_process_key_changes_without_reconstruction(monkeypatch):
    import code_assist_v1.knowledge_core.wiki_synthesis as synthesis
    plan, maps = _case("동일 원문과 완료 응답을 다시 검증한다. " * 40)
    wave, _ = source_cards(plan, maps)
    monkeypatch.setattr(synthesis, "_PREPARATION_KEY", b"different-runtime-key")
    with pytest.raises(AuthoringIncompleteError, match="frozen"):
        plan_reduction_batches(plan, wave, 1)
    rebuilt, _ = source_cards(plan, maps)
    assert rebuilt.wave_id == wave.wave_id
    assert rebuilt.preparation_seal != wave.preparation_seal
    assert plan_reduction_batches(plan, rebuilt, 1)


@pytest.mark.parametrize("mismatch", ["source-plan", "synthesis-policy", "model-policy"])
def test_wave_cannot_cross_source_or_policy_boundaries(mismatch):
    plan, maps = _case("특정 원문과 정책에 묶인 근거다. " * 40)
    wave, _ = source_cards(plan, maps)
    policy = SynthesisPolicy()
    batch = plan_reduction_batches(plan, wave, 1)[0]
    if mismatch == "source-plan":
        plan, _ = _case(plan.source.markdown)
    elif mismatch == "synthesis-policy":
        policy = replace(policy, input_bytes=policy.input_bytes - 1)
    else:
        plan = replace(plan, model_policy=replace(plan.model_policy, max_tokens=1024))
    with pytest.raises(AuthoringIncompleteError, match="frozen"):
        WikiReductionAuthor(runner=lambda *a, **kw: pytest.fail("cross-boundary model call")).author(
            plan, batch, wave=wave, policy=policy)


@pytest.mark.parametrize("missing", [None, "map", "reduction"])
def test_fresh_process_rebuilds_complete_fake_receipts_not_serialized_preparation(tmp_path, missing):
    plan, maps, policy, wave, batches, completed = _completed_wave_case()
    expected = advance_synthesis_wave(plan, wave, batches, completed, policy=policy)
    evidence = {"source_id": plan.source.snapshot_id, "source": plan.source.markdown,
        "source_sha256": plan.source.content_sha256, "chunk_bytes": plan.chunk_bytes,
        "policy": asdict(policy), "maps": [{"ordinal": row.chunk.ordinal,
            "chunk_id": row.chunk.chunk_id, "response": row.response} for row in maps],
        "reductions": [{"batch_id": row.batch_id, "response": row.response} for row in completed]}
    if missing:
        evidence["maps" if missing == "map" else "reductions"].pop()
    path = tmp_path / "fake-completed-responses.json"
    encoded = json.dumps(evidence, ensure_ascii=False).encode()
    path.write_bytes(encoded)
    # A fresh interpreter has a new private key. Only immutable source and fake
    # provider responses cross the process boundary; no prepared seal or wave
    # DTO does. This is not durable-job claiming or real-provider evidence.
    script = r'''
import json, sys
from pathlib import Path
from code_assist_v1.knowledge_core.wiki_authoring import (
    AuthoringError, AuthoringModelPolicy, ExtractionSource, WikiChunkAuthor, plan_authoring)
from code_assist_v1.knowledge_core.wiki_synthesis import (
    SynthesisPolicy, WikiReductionAuthor, source_cards, plan_reduction_batches, advance_synthesis_wave)
data = json.loads(Path(sys.argv[1]).read_bytes())
meta = {"model_kind": "codex", "model_id": "fixture"}
policy = SynthesisPolicy(**data["policy"])
plan = plan_authoring(ExtractionSource(data["source_id"], data["source_sha256"], data["source"]),
    chunk_bytes=data["chunk_bytes"],
    model_policy=AuthoringModelPolicy(provider_order=("codex",), model_ids=("fixture",)))
mapping = {row["chunk_id"]: row["response"] for row in data["maps"]}
reductions = {row["batch_id"]: row["response"] for row in data["reductions"]}
def replay_map(task, payload, **kwargs):
    assert task == "canonical_wiki_chunk_authoring"
    return mapping[payload["chunk_id"]], meta
def replay_reduce(task, payload, **kwargs):
    assert task == "canonical_wiki_synthesis"
    return reductions[payload["batch_id"]], meta
try:
    author = WikiChunkAuthor(runner=replay_map)
    maps = [author.author(plan, row["ordinal"]) for row in data["maps"]]
    wave, _ = source_cards(plan, maps, policy=policy)
    batches = plan_reduction_batches(plan, wave, 1, policy=policy)
    reducer = WikiReductionAuthor(runner=replay_reduce)
    completed = [reducer.author(plan, batch, wave=wave, policy=policy)
                 for batch in batches if batch.batch_id in reductions]
    following = advance_synthesis_wave(plan, wave, batches, completed, policy=policy)
    print(json.dumps({"status": "complete", "wave_id": following.wave_id,
        "map_completion_id": following.map_completion_id, "seal": following.preparation_seal}))
except AuthoringError as exc:
    print(json.dumps({"status": "rejected", "type": type(exc).__name__}))
'''
    process = subprocess.run([sys.executable, "-E", "-B", "-c", script, str(path)],
        cwd=Path(__file__).resolve().parents[4], capture_output=True, text=True,
        encoding="utf-8", timeout=30, check=False)
    assert process.returncode == 0, process.stderr
    result = json.loads(process.stdout.splitlines()[-1])
    assert path.read_bytes() == encoded
    if missing:
        assert result == {"status": "rejected", "type": "AuthoringIncompleteError"}
    else:
        assert result["status"] == "complete"
        assert result["wave_id"] == expected.wave_id
        assert result["map_completion_id"] == expected.map_completion_id
        assert result["seal"] != expected.preparation_seal
