"""Stored-response replay: no live provider, database or semantic-quality claim."""
from dataclasses import asdict, replace
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys

import pytest

from code_assist_v1.knowledge_core.wiki_authoring import (
    AuthoringError, AuthoringIncompleteError, AuthoringModelPolicy,
    AuthoringWorkLimitError, WikiChunkAuthor,
)
from code_assist_v1.knowledge_core.wiki_synthesis import (
    ReductionReceipt, SynthesisPolicy, WikiReductionAuthor, reduction_receipt,
    resume_synthesis, synthesize_wiki,
)
from code_assist_v1.llm_wire import chat_payload, encode_chat_payload, request_body_receipt
from code_assist_v1.tests.knowledge_spaces.unit.test_wiki_synthesis import _case, _response, _META


pytestmark = [pytest.mark.unit, pytest.mark.scenario("UPLOAD-005")]


def _multi():
    plan, maps = _case("여러 작성자가 남긴 독립 조건과 마지막 예외를 보존한다. " * 130)
    policy = SynthesisPolicy(input_bytes=7000, boundary_context_characters=20)
    return plan, maps, policy


def _complete(progress, plan, policy):
    author = WikiReductionAuthor(runner=lambda task, payload, **kwargs: (_response(payload), _META))
    return [
        reduction_receipt(author.author(plan, batch, wave=progress.stage.wave, policy=policy),
                          round_number=batch.round_number)
        for batch in progress.pending_batches
    ]


def _finish(plan, maps, policy, receipts=(), **kwargs):
    receipts = list(receipts)
    for _ in range(policy.max_rounds + 1):
        progress = resume_synthesis(plan, maps, receipts, policy=policy, **kwargs)
        if progress.wiki is not None:
            return progress.wiki, receipts
        receipts.extend(_complete(progress, plan, policy))
    pytest.fail("the bounded fixture did not finish")


def test_partial_wave_returns_only_missing_jobs_and_preserves_v4_request_identity(monkeypatch):
    plan, maps, policy = _multi()
    initial = resume_synthesis(plan, maps, policy=policy)
    assert initial.wiki is None and len(initial.pending_batches) > 1
    assert initial.pending_batches == initial.stage.batches
    receipts = _complete(initial, plan, policy)
    one = receipts[-1]
    monkeypatch.setattr(WikiReductionAuthor, "author",
                        lambda *a, **kw: pytest.fail("replay called a provider"))
    resumed = resume_synthesis(plan, tuple(reversed(maps)), [one], policy=policy)
    assert resumed.wiki is None
    assert resumed.pending_batches == initial.pending_batches[:-1]
    assert resumed.stage.wave.wave_id == initial.stage.wave.wave_id
    assert resumed.stage.reserved_work == initial.stage.reserved_work
    assert [row.batch_id for row in resumed.completed_batches] == [one.batch_id]
    next_wave = resume_synthesis(plan, maps, tuple(reversed(receipts)), policy=policy)
    assert next_wave.wiki is None
    assert next_wave.stage.wave.round_number == 2
    assert next_wave.stage.wave.predecessor_id == initial.stage.wave.wave_id
    assert next_wave.stage.wave.map_completion_id == initial.stage.wave.map_completion_id


def test_complete_response_replay_matches_offline_wiki_and_manifest_byte_for_byte():
    plan, maps, policy = _multi()
    offline = synthesize_wiki(plan, maps, policy=policy,
        runner=lambda task, payload, **kwargs: (_response(payload), _META))
    wiki, receipts = _finish(plan, maps, policy)
    assert wiki == offline
    terminal = resume_synthesis(plan, maps, tuple(reversed(receipts)), policy=policy)
    assert terminal.wiki == wiki and terminal.stage is None
    assert terminal.pending_batches == terminal.completed_batches == ()
    manifest = json.loads(wiki.manifest_json)
    assert manifest["synthesis"]["all_input_cards_accounted"]
    assert not manifest["synthesis"]["compression_verified"]
    assert not manifest["semantic_support_verified"]


@pytest.mark.parametrize("mutation", [
    "duplicate", "unknown-batch", "request", "round", "response-hash",
    "response-cards", "model-kind", "model-object", "transport", "response-unicode",
    "model-unicode", "transport-unicode", "oversize-response",
])
def test_changed_or_malformed_stored_receipt_is_not_silently_reused(mutation):
    plan, maps, policy = _multi()
    progress = resume_synthesis(plan, maps, policy=policy)
    receipt = _complete(progress, plan, policy)[0]
    if mutation == "duplicate":
        receipts = [receipt, receipt]
    else:
        changes = {
            "unknown-batch": {"batch_id": "B" + "0" * 24},
            "request": {"request_sha256": "0" * 64},
            "round": {"round_number": 2},
            "response-hash": {"response_sha256": "0" * 64},
            "response-cards": {"response": "{}", "response_sha256": sha256(b"{}").hexdigest()},
            "model-kind": {"model_json": '[["model_kind","hynix_api"],["model_id","fixture"]]'},
            "model-object": {"model_json": '{"model_id":"fixture","model_kind":"codex"}'},
            "transport": {"transport_json": '{"unrecognized":1}'},
            "response-unicode": {"response": "\ud800"},
            "model-unicode": {"model_json": "\ud800"},
            "transport-unicode": {"transport_json": "\ud800"},
            "oversize-response": {"response": "x" * 65_537},
        }[mutation]
        receipts = [replace(receipt, **changes)]
    with pytest.raises(AuthoringError):
        resume_synthesis(plan, maps, receipts, policy=policy)


def test_a_later_response_cannot_mask_a_missing_predecessor_or_unknown_extra():
    plan, maps, policy = _multi()
    _, receipts = _finish(plan, maps, policy)
    assert any(row.round_number > 1 for row in receipts)
    with pytest.raises(AuthoringIncompleteError, match="incomplete predecessor"):
        resume_synthesis(plan, maps, receipts[1:], policy=policy)
    with pytest.raises(AuthoringIncompleteError, match="follow the completed"):
        resume_synthesis(plan, maps, [*receipts, replace(receipts[-1], batch_id="B" + "f" * 24)],
                         policy=policy)


def test_single_map_shortcut_requires_all_maps_and_rejects_spurious_reduction():
    plan, maps = _case("원문 전체에 조건과 예외가 있다.", chunk_bytes=1024)
    state = resume_synthesis(plan, maps)
    assert state.wiki is not None and state.stage is None
    assert json.loads(state.wiki.manifest_json)["synthesis"]["model_calls"] == 0
    with pytest.raises(AuthoringIncompleteError):
        resume_synthesis(plan, [])
    other_plan, other_maps, policy = _multi()
    extra = _complete(resume_synthesis(other_plan, other_maps, policy=policy), other_plan, policy)[0]
    with pytest.raises(AuthoringIncompleteError, match="follow the completed"):
        resume_synthesis(plan, maps, [extra])


@pytest.mark.parametrize("context", ["page-boundary", "map-warning"])
def test_one_source_chunk_with_unresolved_context_is_not_the_zero_reduction_shortcut(context):
    text = "조건을 구분해서 " + ("\n[Page 2]\n" if context == "page-boundary" else "") + "적용한다."
    plan, maps = _case(text, chunk_bytes=4096)
    if context == "map-warning":
        def runner(task, payload, **kwargs):
            return json.dumps({
                "schema_version": 1, "chunk_id": payload["chunk_id"],
                "sections": [{"heading": "조건", "claims": [
                    {"text": "검증 원문 조건.", "quotes": [payload["source"]]}]}],
                "warnings": ["실제 승인 주체가 원문에 명시되지 않았다."]}, ensure_ascii=False), _META
        maps = [WikiChunkAuthor(runner=runner).author(plan, 1)]
    assert len(plan.chunks) == 1
    pending = resume_synthesis(plan, maps)
    assert pending.wiki is None and len(pending.pending_batches) == 1
    assert any(card.kind != "claim" for card in pending.stage.wave)


def test_final_allowed_round_can_finish_but_cannot_schedule_another_round():
    plan, maps = _case("원문의 연결 조건을 보존한다. " * 20)
    policy = SynthesisPolicy(max_rounds=1)
    initial = resume_synthesis(plan, maps, policy=policy)
    assert len(initial.pending_batches) == 1
    receipts = _complete(initial, plan, policy)
    done = resume_synthesis(plan, maps, receipts, policy=policy)
    assert done.wiki is not None
    assert json.loads(done.wiki.manifest_json)["synthesis"]["rounds"][0]["round_number"] == 1
    large_plan, large_maps, large_policy = _multi()
    large_policy = replace(large_policy, max_rounds=1)
    pending = resume_synthesis(large_plan, large_maps, policy=large_policy)
    assert len(pending.pending_batches) > 1
    with pytest.raises(AuthoringWorkLimitError, match="did not converge"):
        resume_synthesis(large_plan, large_maps, _complete(pending, large_plan, large_policy),
                         policy=large_policy)


@pytest.mark.parametrize("field", ["model_attempts", "logical_input_bytes", "response_bytes"])
def test_durable_retry_product_reserves_all_planned_work_before_first_pending_job(field):
    plan, maps, policy = _multi()
    regular = resume_synthesis(plan, maps, policy=policy)
    doubled = resume_synthesis(plan, maps, policy=policy,
                              map_durable_attempts=2, reduction_durable_attempts=2)
    assert doubled.stage.reserved_work == tuple(value * 2 for value in regular.stage.reserved_work)
    assert doubled.stage.batches == regular.stage.batches  # unchanged v4 provider requests
    index = ("model_attempts", "logical_input_bytes", "response_bytes").index(field)
    limited = replace(plan, work_policy=replace(plan.work_policy, **{field: regular.stage.reserved_work[index]}))
    # Changing frozen work policy also changes map requests. Do not reuse stale maps.
    source = limited.source.markdown
    limited, limited_maps = _case(source, work_policy=limited.work_policy)
    with pytest.raises(AuthoringWorkLimitError, match="cumulative"):
        resume_synthesis(limited, limited_maps, policy=policy,
                         map_durable_attempts=2, reduction_durable_attempts=2)


@pytest.mark.parametrize("value", [0, -1, True, 1.5])
def test_invalid_attempt_multiplier_is_rejected_even_on_single_map_shortcut(value):
    plan, maps = _case("모든 조건을 읽은 짧은 문서.", chunk_bytes=1024)
    with pytest.raises(AuthoringWorkLimitError, match="positive integers"):
        resume_synthesis(plan, maps, reduction_durable_attempts=value)


def test_replay_accounts_for_durable_attempts_without_double_charging_on_restart():
    plan, maps, policy = _multi()
    regular, _ = _finish(plan, maps, policy)
    doubled, receipts = _finish(plan, maps, policy,
                               map_durable_attempts=2, reduction_durable_attempts=2)
    first = json.loads(regular.manifest_json)["synthesis"]
    second = json.loads(doubled.manifest_json)["synthesis"]
    for key in ("reserved_model_attempts", "reserved_logical_input_bytes", "reserved_response_bytes"):
        assert second[key] == 2 * first[key]
    assert second["model_calls"] == first["model_calls"]
    assert second["execution"]["map_durable_attempts"] == 2
    assert resume_synthesis(plan, maps, receipts, policy=policy, map_durable_attempts=2,
                            reduction_durable_attempts=2).wiki == doubled


def test_openai_compatible_receipts_replay_exact_wire_and_never_fallback_to_codex():
    plan, _ = _case("회사 모델도 모든 원문의 조건과 예외를 보존한다. " * 30)
    plan = replace(plan, model_policy=AuthoringModelPolicy(provider_order=("hynix_api",),
        model_ids=("fixture",), wire_models=(("fixture", "private-wire-model"),)))
    calls = []
    def runner(task, payload, **kwargs):
        calls.append(task)
        assert kwargs["provider_order"] == ["hynix_api"] and kwargs["model_ids"] == ["fixture"]
        wire_payload = chat_payload("private-wire-model", kwargs["messages"],
                                    max_tokens=kwargs["max_tokens"], temperature=0.1)
        meta = {"model_kind": "hynix_api", "model_id": "fixture", "model_used": "private-wire-model",
                "request_transport": request_body_receipt(wire_payload, encode_chat_payload(wire_payload))}
        response = _response(payload) if task == "canonical_wiki_synthesis" else json.dumps({
            "schema_version": 1, "chunk_id": payload["chunk_id"], "sections": [{
                "heading": "원문", "claims": [{"text": "검증용 원문 요지.", "quotes": [payload["source"]]}]}],
            "warnings": []}, ensure_ascii=False)
        return response, meta
    maps = [WikiChunkAuthor(runner=runner).author(plan, chunk.ordinal) for chunk in plan.chunks]
    initial = resume_synthesis(plan, maps)
    assert len(initial.pending_batches) == 1
    batch = initial.pending_batches[0]
    result = WikiReductionAuthor(runner=runner).author(plan, batch, wave=initial.stage.wave)
    receipt = reduction_receipt(result, round_number=1)
    count = len(calls)
    done = resume_synthesis(plan, maps, [receipt])
    assert done.wiki is not None and len(calls) == count
    assert json.loads(done.wiki.manifest_json)["synthesis"]["rounds"][0]["model"]["model_kind"] == "hynix_api"
    with pytest.raises(AuthoringIncompleteError, match="transport"):
        resume_synthesis(plan, maps, [replace(receipt, transport_json="{}")])


def test_fresh_interpreter_uses_only_stored_maps_and_ordered_model_transport_receipts(tmp_path):
    plan, maps, policy = _multi()
    wiki, receipts = _finish(plan, maps, policy)
    # Sorted outer JSON must not sort the inner model pair-array. v4 wave IDs
    # include the original pair order, unlike semantic mapping equality.
    assert json.loads(receipts[0].model_json)[0][0] == "model_kind"
    data = {"source_id": plan.source.snapshot_id, "source_hash": plan.source.content_sha256,
        "source": plan.source.markdown, "chunk_bytes": plan.chunk_bytes, "policy": asdict(policy),
        "maps": [{"ordinal": row.chunk.ordinal, "response": row.response,
                  "request": row.request_sha256, "model": row.model,
                  "transport": row.request_transport_json} for row in maps],
        "receipts": [asdict(row) for row in receipts]}
    path = tmp_path / "stored-responses.json"
    encoded = json.dumps(data, ensure_ascii=False, sort_keys=True).encode()
    path.write_bytes(encoded)
    script = r'''
import json, sys
from pathlib import Path
from code_assist_v1.knowledge_core.wiki_authoring import (
    AuthoredChunk, AuthoringModelPolicy, ExtractionSource, plan_authoring, validate_chunk_output)
from code_assist_v1.knowledge_core.wiki_synthesis import (
    ReductionReceipt, SynthesisPolicy, WikiReductionAuthor, resume_synthesis)
data = json.loads(Path(sys.argv[1]).read_bytes())
plan = plan_authoring(ExtractionSource(data["source_id"], data["source_hash"], data["source"]),
    chunk_bytes=data["chunk_bytes"],
    model_policy=AuthoringModelPolicy(provider_order=("codex",), model_ids=("fixture",)))
maps = []
for row in data["maps"]:
    chunk = plan.chunks[row["ordinal"] - 1]
    sections, warnings = validate_chunk_output(row["response"], chunk)
    maps.append(AuthoredChunk(chunk, row["request"], row["response"], sections, warnings,
        tuple(tuple(pair) for pair in row["model"]), request_transport_json=row["transport"]))
WikiReductionAuthor.author = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("provider invoked"))
receipts = [ReductionReceipt(**row) for row in data["receipts"]]
result = resume_synthesis(plan, maps, receipts, policy=SynthesisPolicy(**data["policy"]))
print(json.dumps({"markdown": result.wiki.markdown, "manifest": result.wiki.manifest_json}, ensure_ascii=True))
'''
    process = subprocess.run([sys.executable, "-E", "-B", "-c", script, str(path)],
        cwd=Path(__file__).resolve().parents[4], capture_output=True, text=True,
        encoding="utf-8", timeout=30, check=False)
    assert process.returncode == 0, process.stderr
    result = json.loads(process.stdout.splitlines()[-1])
    assert result == {"markdown": wiki.markdown, "manifest": wiki.manifest_json}
    assert path.read_bytes() == encoded
