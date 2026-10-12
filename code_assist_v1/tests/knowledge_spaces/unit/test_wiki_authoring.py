from dataclasses import replace
import hashlib
import json
from uuid import uuid4

import pytest

from code_assist_v1.llm_wire import chat_payload, encode_chat_payload, request_body_receipt

from code_assist_v1.knowledge_core.wiki_authoring import (
    AUTHORING_POLICY_VERSION, AUTHORING_PROMPT, ExtractionSource, AuthoringPlan,
    AuthoringSourceError, AuthoringOutputError, AuthoringModelError,
    AuthoringIncompleteError, WikiChunkAuthor, plan_authoring,
    AuthoringModelPolicy, AuthoringWorkPolicy, AuthoringWorkLimitError,
    validate_chunk_output, assemble_wiki,
)


pytestmark = [pytest.mark.unit, pytest.mark.scenario("UPLOAD-005")]
_META = {"model_kind": "hynix_api", "model_id": "fixture"}


def _meta(kwargs, **values):
    # Fake wire evidence for pure unit tests; actual adapter bytes are tested
    # separately in test_internal_llm_request_budget.py.
    payload = chat_payload("fixture-wire", kwargs["messages"], max_tokens=kwargs["max_tokens"], temperature=kwargs["temperature"])
    return {**_META, **values, "request_transport": request_body_receipt(payload, encode_chat_payload(payload))}


def _source(text, *, snapshot=None):
    return ExtractionSource(snapshot or str(uuid4()), hashlib.sha256(text.encode()).hexdigest(), text)


def _response(chunk_id, quote, *, statement="원문에 기재된 조건입니다."):
    return json.dumps({"schema_version": 1, "chunk_id": chunk_id,
                       "sections": [{"heading": "적용 조건", "claims": [{"text": statement, "quotes": [quote]}]}],
                       "warnings": []}, ensure_ascii=False)


@pytest.mark.parametrize("text", [
    "한글과 English🙂\n" * 4_000 + "\n부록 예외: 센서 교체 직후에는 적용하지 않는다.",
    '"\\\x01\r\n' * 10_000 + "최종 수치 0.75%",
    "A" * 100_000, "🙂" * 20_000,
], ids=["long-korean", "escaped-controls", "long-ascii-line", "emoji"])
def test_complete_partition_preserves_every_code_point_and_serialized_request_bound(text):
    source = _source(text)
    plan = plan_authoring(source)
    assert "".join(chunk.text for chunk in plan.chunks) == text
    assert plan.chunks[0].start == 0 and plan.chunks[-1].end == len(text)
    assert len({chunk.chunk_id for chunk in plan.chunks}) == len(plan.chunks)
    calls = []
    def runner(task, payload, **kwargs):
        assert task == "canonical_wiki_chunk_authoring"
        assert len(json.dumps(kwargs["messages"], ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()) <= 65_536
        assert len(payload["source"].encode()) <= 24_576
        assert payload["source"] == text[slice(*payload["source_range"])]
        calls.append(payload["chunk_id"])
        return _response(payload["chunk_id"], payload["source"]), _meta(kwargs)
    author = WikiChunkAuthor(runner=runner)
    for chunk in plan.chunks:
        assert chunk.text == text[chunk.start:chunk.end]
        assert chunk.content_sha256 == hashlib.sha256(chunk.text.encode()).hexdigest()
        author.author(plan, chunk.ordinal)
    assert calls == [chunk.chunk_id for chunk in plan.chunks if chunk.text.strip()]


@pytest.mark.parametrize("budget", [4, 17, 128, 4_096, 24_576])
def test_requested_chunk_bound_is_not_a_document_truncation(budget):
    text = "한글🙂\n문장 끝.\n" * 30 + "\n마지막 0.50% 조건."
    plan = plan_authoring(_source(text), chunk_bytes=budget)
    assert "".join(row.text for row in plan.chunks) == text
    assert all(0 < len(row.text.encode()) <= budget for row in plan.chunks)
    assert plan == plan_authoring(plan.source, chunk_bytes=budget)


@pytest.mark.parametrize("budget", [0, 3, -1, True, 4.0, 24_577])
def test_invalid_chunk_budget_is_rejected(budget):
    with pytest.raises(AuthoringSourceError):
        plan_authoring(_source("본문"), chunk_bytes=budget)


def test_source_and_plan_forgery_are_rejected_before_model_calls():
    source = _source("원본 기준\n최종 예외")
    with pytest.raises(AuthoringSourceError):
        replace(source, markdown="다른 원문")
    with pytest.raises(AuthoringSourceError):
        replace(source, snapshot_id="wrong")
    plan = plan_authoring(source, chunk_bytes=16)
    with pytest.raises(AuthoringSourceError):
        replace(plan, chunks=plan.chunks[:-1])
    with pytest.raises(AuthoringSourceError):
        replace(plan, chunks=(replace(plan.chunks[0], start=1), *plan.chunks[1:]))
    with pytest.raises(AuthoringSourceError):
        AuthoringPlan(source, 16, tuple(reversed(plan.chunks)))


@pytest.mark.parametrize("bad", ["", " \n\t", "\ud800"])
def test_empty_or_non_utf8_source_cannot_be_authored(bad):
    with pytest.raises(AuthoringSourceError):
        ExtractionSource(str(uuid4()), "a" * 64, bad)


def test_same_contents_from_different_upload_snapshots_do_not_share_handles():
    first = plan_authoring(_source("동일 파일명과 동일 본문입니다."))
    other = plan_authoring(_source(first.source.markdown))
    assert first.source.content_sha256 == other.source.content_sha256
    assert first.chunks[0].chunk_id != other.chunks[0].chunk_id
    with pytest.raises(AuthoringOutputError):
        validate_chunk_output(_response(first.chunks[0].chunk_id, first.source.markdown), other.chunks[0])


@pytest.mark.parametrize("invalid", [
    "not-json", "[]", '{"schema_version":1,"schema_version":1}',
    '{"schema_version":NaN}', "\ud800", "x" * 65_537,
], ids=["not-json", "array", "duplicate", "nan", "surrogate", "oversized"])
def test_untrusted_provider_output_is_strictly_bounded_and_shaped(invalid):
    chunk = plan_authoring(_source("0.75% 미만, 센서 교체는 제외.")).chunks[0]
    with pytest.raises(AuthoringOutputError):
        validate_chunk_output(invalid, chunk)


@pytest.mark.parametrize("mutation", ["quote", "id", "extra", "empty_claim", "empty_section", "bool_version", "duplicate_key"])
def test_invalid_quotes_and_contract_fields_fail_validation(mutation):
    chunk = plan_authoring(_source("수치 기준은 0.75% 미만입니다.")).chunks[0]
    value = json.loads(_response(chunk.chunk_id, chunk.text))
    if mutation == "quote":
        value["sections"][0]["claims"][0]["quotes"] = ["수치 기준은 0.50% 미만입니다."]
    elif mutation == "id":
        value["chunk_id"] = "C1_other"
    elif mutation == "extra":
        value["verified"] = True
    elif mutation == "empty_claim":
        value["sections"][0]["claims"] = []
    elif mutation == "empty_section":
        value["sections"] = []
    elif mutation == "bool_version":
        value["schema_version"] = True
    encoded = json.dumps(value, ensure_ascii=False)
    if mutation == "duplicate_key":
        encoded = encoded.replace('"schema_version": 1', '"schema_version": 1,"schema_version": 1')
    with pytest.raises(AuthoringOutputError):
        validate_chunk_output(encoded, chunk)


def test_quote_locations_are_global_exact_and_unambiguous():
    source = _source("가" * 100 + "\n마지막 예외는 2027년부터 적용한다.")
    plan = plan_authoring(source, chunk_bytes=128)
    chunk = plan.chunks[-1]
    quote = "마지막 예외는 2027년부터 적용한다."
    sections, _ = validate_chunk_output(_response(chunk.chunk_id, quote), chunk)
    reference = sections[0].claims[0].quotes[0]
    assert reference.ranges == ((source.markdown.index(quote), len(source.markdown)),)
    for start, end in reference.ranges:
        assert source.markdown[start:end] == quote
    repeated = plan_authoring(_source("조건 A와 조건 A는 맥락이 다르다.")).chunks[0]
    with pytest.raises(AuthoringOutputError, match="unambiguous"):
        validate_chunk_output(_response(repeated.chunk_id, "조건 A"), repeated)


def test_one_request_per_chunk_uses_company_compatible_runner_without_tools_or_topic_examples():
    source = _source("기본 한계는 0.75% 미만이다. 단, 센서 교체 후에는 0.50% 미만을 적용한다.")
    plan = plan_authoring(source, model_policy=AuthoringModelPolicy(model_ids=("compatible-fixture",)))
    calls = []
    def runner(task, payload, **kwargs):
        calls.append((task, payload, kwargs))
        good = _response(payload["chunk_id"], payload["source"])
        assert kwargs["validate_output"](good) == ""
        assert kwargs["validate_output"](_response("stale", payload["source"]))
        return good, _meta(kwargs, model_id="compatible-fixture", secret="do-not-record")
    authored = WikiChunkAuthor(runner=runner, provider_order=("hynix_api",), model_ids=("compatible-fixture",)).author(plan, 1)
    assert len(calls) == 1
    task, payload, kwargs = calls[0]
    assert kwargs["provider_order"] == ["hynix_api"] and kwargs["model_ids"] == ["compatible-fixture"]
    assert kwargs["api_timeout_s"] == kwargs["timeout_s"] == 300
    assert kwargs["messages"][0]["content"] == AUTHORING_PROMPT
    assert "0.75" not in AUTHORING_PROMPT and "센서" not in AUTHORING_PROMPT
    assert "secret" not in dict(authored.model)
    wiki = assemble_wiki(plan, (authored,))
    # Exact quotes belong to the separate ledger; do not inflate visible Wiki
    # prose by appending the whole extraction again.
    assert "0.50%" not in wiki.markdown
    assert "0.50%" in wiki.manifest_json
    manifest = json.loads(wiki.manifest_json)
    assert manifest["all_chunks_processed"] is True
    assert manifest["semantic_support_verified"] is False
    assert manifest["key_fact_recall"] is None
    assert manifest["source_sha256"] == source.content_sha256
    assert manifest["rendered_markdown_sha256"] == hashlib.sha256(wiki.markdown.encode()).hexdigest()
    assert manifest["rendered_markdown_bytes"] == len(wiki.markdown.encode())
    assert source.markdown == payload["source"]  # authoring never mutates extraction


def test_incomplete_duplicate_foreign_or_tampered_results_cannot_form_a_wiki():
    plan = plan_authoring(_source("Alpha 1.\nBeta 2.\nGamma 3.\nDelta 4."), chunk_bytes=16)
    author = WikiChunkAuthor(runner=lambda task, payload, **kw: (_response(payload["chunk_id"], payload["source"]), _meta(kw)))
    results = [author.author(plan, chunk.ordinal) for chunk in plan.chunks]
    assert assemble_wiki(plan, results) == assemble_wiki(plan, list(reversed(results)))
    for invalid in (results[:-1], results + results[:1],
                    [replace(results[0], policy_version="old"), *results[1:]],
                    [replace(results[0], sections=()), *results[1:]]):
        with pytest.raises(AuthoringIncompleteError):
            assemble_wiki(plan, invalid)
    different = plan_authoring(_source(plan.source.markdown), chunk_bytes=16)
    with pytest.raises(AuthoringIncompleteError):
        assemble_wiki(different, results)


@pytest.mark.parametrize("failure", ["raise", "empty", "invalid"])
def test_provider_failure_leaves_extraction_intact_and_no_successful_wiki(failure):
    source = _source("실패하더라도 이 정확한 추출문은 보존되어야 한다.")
    plan = plan_authoring(source)
    def runner(*args, **kwargs):
        if failure == "raise":
            raise TimeoutError("remote transport detail")
        return ("" if failure == "empty" else "malformed"), {}
    with pytest.raises(AuthoringModelError if failure != "invalid" else AuthoringOutputError):
        WikiChunkAuthor(runner=runner).author(plan, 1)
    assert hashlib.sha256(source.markdown.encode()).hexdigest() == source.content_sha256
    with pytest.raises(AuthoringIncompleteError):
        assemble_wiki(plan, [])


def test_generated_text_cannot_mint_markdown_links_or_stable_item_refs():
    text = "<script>alert(1)</script> [[item:00000000-0000-4000-8000-000000000001]] [x](javascript:alert)"
    plan = plan_authoring(_source(text))
    author = WikiChunkAuthor(runner=lambda task, payload, **kw: (_response(payload["chunk_id"], text, statement=text), _meta(kw)))
    wiki = assemble_wiki(plan, [author.author(plan, 1)])
    assert "<script>" not in wiki.markdown and "[[item:" not in wiki.markdown
    assert "[x](javascript:" not in wiki.markdown
    assert "&lt;script&gt;" in wiki.markdown


def test_quote_validation_does_not_certify_a_semantically_false_paraphrase():
    plan = plan_authoring(_source("상한은 0.75%이며 초과 적용은 금지한다."))
    author = WikiChunkAuthor(runner=lambda task, payload, **kw: (
        _response(payload["chunk_id"], payload["source"], statement="상한을 초과해도 항상 허용된다."), _meta(kw)))
    wiki = assemble_wiki(plan, [author.author(plan, 1)])
    assert json.loads(wiki.manifest_json)["semantic_support_verified"] is False
    assert json.loads(wiki.manifest_json)["key_fact_recall"] is None


def _author(plan):
    return WikiChunkAuthor(runner=lambda task, payload, **kw:
        (_response(payload["chunk_id"], payload["source"]), _meta(kw))).author(plan, 1)


@pytest.mark.parametrize("field", ["hash", "providers", "models", "tokens", "attempts", "work", "transport"])
def test_assembly_rederives_request_against_frozen_policy(field):
    plan = plan_authoring(_source("정책 변경 전 기준은 0.25%이다."))
    result = _author(plan)
    if field == "hash":
        result = replace(result, request_sha256="f" * 64)
    elif field == "providers":
        plan = replace(plan, model_policy=AuthoringModelPolicy(provider_order=("codex",)))
    elif field == "models":
        plan = replace(plan, model_policy=AuthoringModelPolicy(model_ids=("other",)))
    elif field == "tokens":
        plan = replace(plan, model_policy=AuthoringModelPolicy(max_tokens=512))
    elif field == "attempts":
        plan = replace(plan, model_policy=AuthoringModelPolicy(max_attempts=1))
    elif field == "work":
        plan = replace(plan, work_policy=AuthoringWorkPolicy(markdown_bytes=1_024))
    else:
        result = replace(result, request_transport_json='{"secret":"not-persistable"}')
    with pytest.raises(AuthoringIncompleteError):
        assemble_wiki(plan, [result])


@pytest.mark.parametrize("metadata", [
    (), (("model_kind", "hynix_api"),),
    (("model_kind", "hynix_api"), ("model_id", "fixture"), ("secret", "not-persistable")),
    (("model_kind", "hynix_api"), ("model_id", "fixture"), ("model_id", "second")),
    (("model_kind", "hynix_api"), ("model_id", "")),
    (("model_kind", "hynix_api"), ("model_id", "\ud800")),
    (("model_kind", "hynix_api"), ("model_id", "a" * 161)),
    (("model_kind", "codex"), ("model_id", "codex-local")),
], ids=["empty", "missing-id", "unknown", "duplicate", "blank", "unicode", "long", "wrong-lane"])
def test_forged_model_receipt_is_not_saved(metadata):
    plan = plan_authoring(_source("출처와 생성 모델은 별도로 기록한다."))
    result = replace(_author(plan), model=metadata)
    with pytest.raises(AuthoringIncompleteError):
        assemble_wiki(plan, [result])


@pytest.mark.parametrize("field", ["source_bytes", "model_attempts", "logical_input_bytes", "response_bytes"])
def test_complete_source_work_is_reserved_before_any_call(field):
    source = _source("긴 원문을 중간에 생략해서는 안 된다.\n" * 100)
    policy = replace(AuthoringWorkPolicy(), **{field: 1})
    with pytest.raises(AuthoringWorkLimitError):
        plan_authoring(source, work_policy=policy)
    assert source.content_sha256 == hashlib.sha256(source.markdown.encode()).hexdigest()


@pytest.mark.parametrize("field", ["markdown_bytes", "manifest_bytes"])
def test_assembly_enforces_final_artifact_budget_without_partial_wiki(field):
    plan = plan_authoring(_source("원문은 실패하더라도 남아 있어야 한다."),
                          work_policy=replace(AuthoringWorkPolicy(), **{field: 256}))
    with pytest.raises(AuthoringWorkLimitError):
        assemble_wiki(plan, [_author(plan)])


def test_caller_cannot_silently_switch_production_to_codex():
    plan = plan_authoring(_source("운영 기본 경로를 유지한다."))
    author = WikiChunkAuthor(runner=lambda *args, **kw: pytest.fail("must not call"),
                             provider_order=("codex",))
    with pytest.raises(AuthoringSourceError):
        author.author(plan, 1)


def test_mid_line_boundaries_are_disclosed_even_when_model_omits_warnings():
    plan = plan_authoring(_source("수치 조건과 예외를 포함하는 매우 긴 단일 문장이다." * 4), chunk_bytes=32)
    author = WikiChunkAuthor(runner=lambda task, payload, **kw:
        (_response(payload["chunk_id"], payload["source"]), _meta(kw)))
    wiki = assemble_wiki(plan, [author.author(plan, row.ordinal) for row in plan.chunks])
    assert "구간 경계 주의" in wiki.markdown
    manifest = json.loads(wiki.manifest_json)
    assert manifest["chunks"][0]["boundary"]["ends_mid_line"]
    assert manifest["chunks"][-1]["boundary"]["starts_mid_line"]
    assert manifest["semantic_support_verified"] is False


@pytest.mark.parametrize("mutation", ["missing", "fake-hash", "fake-size", "changed-model", "other-message"])
def test_shape_valid_api_receipt_must_match_exact_current_request(mutation):
    plan = plan_authoring(_source("현재 문서의 수치와 예외가 들어 있는 정확한 요청."))
    result = _author(plan)
    receipt = json.loads(result.request_transport_json)
    if mutation == "missing":
        receipt = {}
    elif mutation == "fake-hash":
        receipt["body_sha256"] = "f" * 64
    elif mutation == "fake-size":
        receipt["body_bytes"] = 1
    elif mutation == "changed-model":
        receipt["model"] = "not-the-transmitted-model"
    else:
        payload = chat_payload(receipt["model"], [{"role": "user", "content": "another request"}],
            max_tokens=plan.model_policy.max_tokens, temperature=0.1)
        receipt = request_body_receipt(payload, encode_chat_payload(payload))
    forged = replace(result, request_transport_json=json.dumps(receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    with pytest.raises(AuthoringIncompleteError, match="transport"):
        assemble_wiki(plan, [forged])


def test_model_warnings_are_retained_in_structured_manifest():
    plan = plan_authoring(_source("부분적인 관측이며 추가 검증이 필요하다."))
    def runner(task, payload, **kwargs):
        value = json.loads(_response(payload["chunk_id"], payload["source"]))
        value["warnings"] = ["관측의 불확실성을 보존해야 한다."]
        return json.dumps(value, ensure_ascii=False), _meta(kwargs)
    result = WikiChunkAuthor(runner=runner).author(plan, 1)
    wiki = assemble_wiki(plan, [result])
    assert json.loads(wiki.manifest_json)["chunks"][0]["warnings"] == list(result.warnings)


def test_frozen_logical_to_wire_model_mapping_rejects_recomputed_alias_receipt():
    plan = plan_authoring(_source("모델 경로와 원문은 별도로 동결한다."),
        model_policy=AuthoringModelPolicy(model_ids=("fixture",), wire_models=(("fixture", "fixture-wire"),)))
    result = _author(plan)
    from code_assist_v1.knowledge_core.wiki_authoring import authoring_request_descriptor
    descriptor = authoring_request_descriptor(plan, result.chunk)
    payload = chat_payload("different-wire-alias", descriptor["messages"],
        max_tokens=plan.model_policy.max_tokens, temperature=0.1)
    receipt = request_body_receipt(payload, encode_chat_payload(payload))
    forged = replace(result, request_transport_json=json.dumps(receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    with pytest.raises(AuthoringIncompleteError, match="transport"):
        assemble_wiki(plan, [forged])
    assert json.loads(assemble_wiki(plan, [result]).manifest_json)["wire_model_binding"] == "frozen_route"


@pytest.mark.parametrize("value", ["대상 '확인' 값과 \"보류\" 상태.", "O'Brien & 조건 <미정>."])
def test_plain_prose_quotes_render_as_text_without_numeric_entity_artifacts(value):
    from markdown_it import MarkdownIt
    from code_assist_v1.knowledge_core.wiki_authoring import _literal
    parser = MarkdownIt("commonmark")
    tokens = parser.parseInline(_literal(value))[0].children
    assert all(token.type == "text" for token in tokens)
    assert "".join(token.content for token in tokens) == value
    assert "&\\#x27;" not in _literal(value)
