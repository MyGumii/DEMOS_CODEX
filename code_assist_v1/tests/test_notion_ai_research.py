"""Multi-reference research chooses real quotations without relaxing validation."""
import json
from unittest.mock import patch

import pytest

from code_assist_v1 import notion_ai_chat as chat, notion_ai_chat_artifacts as artifacts, notion_store as ns
from test_notion_ai_home import setup, create
from test_notion_ai_stream import legacy_research_stream as send_stream, collect


def test_evidence_choices_remain_short_exact_substrings_of_each_authorized_excerpt():
    refs = [{"source_id": "Pa", "excerpt": "짧은 제목\n" + "αᵢⱼ는 이웃 노드의 중요도를 나타내며 합이 1인 attention 계수입니다.\n" * 8},
            {"source_id": "Pb", "excerpt": "B만의 이웃 집계 공식과 검증 결과를 설명하는 서로 다른 자료입니다.\n" + "수학 내용 " * 200},
            {"source_id": "Pc", "excerpt": "짧은 내용"}]
    options = artifacts.citation_options(refs)
    for ref in refs:
        own = [c for c in options if c["source_id"] == ref["source_id"]]
        assert 1 <= len(own) <= 3
        assert all(0 < len(c["quote"]) <= 180 and c["quote"] in ref["excerpt"] for c in own)
    schema = artifacts.response_schema("research", refs)
    choices = schema["properties"]["citations"]["items"]["anyOf"]
    assert {c["properties"]["source_id"]["const"] for c in choices} == {"Pa", "Pb", "Pc"}
    for choice in choices:
        sid = choice["properties"]["source_id"]["const"]
        assert choice["properties"]["quote"]["enum"] == [c["quote"] for c in options if c["source_id"] == sid]


@pytest.mark.parametrize("citation", [
    {"source_id": "Pa", "quote": "출처 B에만 들어 있는 별개의 원문입니다."},
    {"source_id": "Pforeign", "quote": "출처 A에만 들어 있는 원문입니다."},
    {"source_id": "Pa", "quote": "출처 A의 내용을 다시 풀어 쓴 문장입니다."},
])
def test_cross_source_foreign_and_paraphrased_quotes_still_fail(citation):
    refs = [{"source_id": "Pa", "title": "A", "excerpt": "출처 A에만 들어 있는 원문입니다."},
            {"source_id": "Pb", "title": "B", "excerpt": "출처 B에만 들어 있는 별개의 원문입니다."}]
    with pytest.raises(ValueError, match="unsupported citation"):
        chat.normalize_result({"answer": "조사 내용", "citations": [citation], "artifact": None}, refs, "research")


def test_default_research_request_retries_multi_page_quote_error_and_saves_only_verified_result(setup):
    http, service, _, first = setup
    pages = [first]
    for title in ("GAT 원리", "GAT 다중 헤드"):
        page = ns.create_page("owner", title=title)["page"]["id"]
        ns.replace_blocks("owner", page, [{"type": "paragraph", "content":
            title + ": 이 문서는 이웃별 attention 계수를 학습하는 합성 검증 자료입니다."}])
        pages.append(page)
    foreign = ns.create_page("stranger", title="다른 계정 GAT")["page"]["id"]
    ns.replace_blocks("stranger", foreign, [{"type": "paragraph", "content": "FOREIGN-DO-NOT-USE"}])
    record = create(http, page_ids=pages)
    attempts = []
    def upstream(lane, cfg, payload, *args):
        attempts.append(payload)
        assert len(payload["references"]) == 3
        assert "FOREIGN-DO-NOT-USE" not in json.dumps(payload)
        lookup = {r["source_id"]: r["excerpt"] for r in payload["references"]}
        options = payload["citation_options"]
        assert options and all(c["quote"] in lookup[c["source_id"]] for c in options)
        citations = [dict(options[-1])]
        if len(attempts) == 1:
            citations[0]["quote"] = "원문에 없는 재작성된 인용문"
        yield from json.dumps({"answer": "확인된 GAT 조사 결과", "citations": citations, "artifact": None})
    with patch.object(chat, "provider_chunks", side_effect=upstream):
        frames = collect(send_stream(http, record, mode="research", model_id="gemma4",
            message="선택한 자료에서 다음 주제를 조사하고 출처와 함께 정리해줘:"))
    assert len(attempts) == 2 and attempts[0]["citation_options"] == attempts[1]["citation_options"]
    assert any(f["type"] == "reset" for f in frames)
    assert frames[-1]["type"] == "done"
    saved = service.store("owner").get(record["id"])
    assert saved["revision"] == 1 and len(saved["messages"]) == 2
    assert saved["messages"][-1]["citations"][0]["quote"] == attempts[-1]["citation_options"][-1]["quote"]
