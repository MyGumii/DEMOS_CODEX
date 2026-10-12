from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from uuid import UUID

import pytest

from code_assist_v1.knowledge_core.answer_citations import (
    CitationCatalog, CitationValidationError, prepare_citations, render_citations,
)


pytestmark = [pytest.mark.unit, pytest.mark.scenario("RANK-014", "RANK-017")]


def _id(number):
    return str(UUID(int=number))


def _source(number, *, space=10, revision=None, body="본문 조건과 예외\n", filename="policy.pdf"):
    item, rev = _id(number), _id(revision or number + 1000)
    return (
        {"item_id": item, "revision_id": rev, "source_space_id": _id(space),
         "title": f"지식 {number}", "excerpt_md": body},
        {"item_id": item, "revision_id": rev, "source_space_id": _id(space),
         "title": f"지식 {number}", "artifacts": [
             {"artifact_id": _id(number + 2000), "source_revision_id": rev,
              "original_filename": filename, "content_sha256": sha256(str(number).encode()).hexdigest(),
              "download_url": "/api/knowledge/v1/server-only"}
         ]},
    )


def _prepare(payload, *, turn=1, view=10):
    return prepare_citations(payload, turn_id=_id(turn), selected_view_space_id=_id(view))


def _marker(source):
    return f"[[wiki:{source.handle}]]"


def test_actual_body_catalog_excludes_manifest_omitted_reranker_history_and_empty_rows():
    supplied, source = _source(100)
    excluded, other_source = _source(101)
    payload = {
        "retrieval_candidates": [supplied, {**excluded, "excerpt_md": ""}],
        "wiki_body_evidence": [{**excluded, "excerpt_md": "  "}],
        "source_documents": [source, other_source],
        "wiki_manifest": {"entries": [excluded]},
        "reranker_candidates": [excluded],
        "recent_messages": [{"text": str(excluded)}],
        "active_summary": str(excluded),
        "explicit_contexts": [{"content_md": "External snapshot, no canonical identity"}],
        "pins": [{**excluded, "availability": "unavailable"}],
    }
    before = deepcopy(payload)
    model, catalog = _prepare(payload)
    assert payload == before
    assert len(catalog.sources) == len(model["source_documents"]) == 1
    assert catalog.sources[0].item_id == supplied["item_id"]
    assert catalog.sources[0].passages == (supplied["excerpt_md"],)
    assert "download_url" not in model["source_documents"][0]["artifacts"][0]
    assert model["retrieval_candidates"][0]["citation_handle"] == catalog.sources[0].handle
    assert "citation_handle" not in model["wiki_manifest"]["entries"][0]
    assert model["citation_sources"][0]["original_filenames"] == ["policy.pdf"]


@pytest.mark.parametrize("view", [10, 11, 12, 13], ids=["personal", "part", "team", "responsibility"])
def test_server_links_preserve_selected_view_and_multiple_authors_same_filename(view):
    first, first_source = _source(100, space=10)
    second, second_source = _source(101, space=11)
    third, third_source = _source(102, space=12)
    _, catalog = _prepare({"retrieval_candidates": [first, second, third],
                           "source_documents": [first_source, second_source, third_source]}, view=view)
    a, b, _ = catalog.sources
    answer = f"## 종합\n\n작성자별 조건 비교.\n\n근거 문서: {_marker(b)}, {_marker(a)}, {_marker(b)}\n\n---\n\n## 전체 요약\n요약."
    rendered, trace = render_citations(answer, catalog)
    for source in (a, b):
        expected = (f"/api/knowledge/v1/spaces/{_id(view)}/items/{source.item_id}"
                    f"/source-artifacts/{source.artifacts[0].artifact_id}/download")
        assert rendered.count(expected) == 1
    assert "[[wiki:" not in rendered
    assert rendered.count("policy.pdf") == 2
    assert [row["item_id"] for row in trace["sources"]] == [b.item_id, a.item_id]
    assert [row["source_space_id"] for row in trace["sources"]] == [_id(11), _id(10)]
    assert trace["claim_support"] == "not_semantically_verified"
    assert trace["occurrences"] == [{"line": 5, "handles": [b.handle, a.handle]}]


def test_shared_pin_and_historical_reference_have_distinct_revision_identity_and_real_bodies():
    row, metadata = _source(100, revision=1100)
    old_row, old_metadata = _source(100, revision=1099, body="과거 조건")
    old_metadata["artifacts"] = []
    pin = {"kind": "pin", "identity": row["item_id"], "title": row["title"],
           "target_item_id": row["item_id"], "target_revision_id": row["revision_id"],
           "source_space_id": row["source_space_id"], "content_md": row["excerpt_md"]}
    stable = {**pin, "kind": "stable_reference", "identity": _id(300),
              "target_revision_id": old_row["revision_id"], "content_md": old_row["excerpt_md"]}
    shared = {key: value for key, value in row.items() if key != "excerpt_md"}
    shared["mandatory_context_identity"] = pin["identity"]
    model, catalog = _prepare({"retrieval_candidates": [shared],
                               "mandatory_contexts": [pin, stable],
                               "source_documents": [metadata, old_metadata]})
    assert len(catalog.sources) == 2
    assert catalog.sources[0].passages == (row["excerpt_md"],)
    assert catalog.sources[1].revision_id == old_row["revision_id"]
    assert catalog.sources[1].artifacts == ()
    assert model["retrieval_candidates"][0]["citation_handle"] == model["mandatory_contexts"][0]["citation_handle"]
    rendered, trace = render_citations(f"근거 문서: {_marker(catalog.sources[1])}", catalog)
    assert "원본 링크 미제공" in rendered and "download" not in rendered
    assert trace["sources"][0]["revision_id"] == old_row["revision_id"]


@pytest.mark.parametrize("problem", ["missing", "different_item", "different_revision", "explicit_context"])
def test_shared_body_cannot_bind_to_a_different_identity(problem):
    row, metadata = _source(100)
    shared = {**row, "mandatory_context_identity": "shared"}
    mandatory = {"kind": "pin", "identity": "shared", "target_item_id": row["item_id"],
                 "target_revision_id": row["revision_id"], "content_md": "Actual body"}
    if problem == "different_item":
        mandatory["target_item_id"] = _id(999)
    if problem == "different_revision":
        mandatory["target_revision_id"] = _id(999)
    if problem == "explicit_context":
        mandatory["kind"] = "explicit_context"
    with pytest.raises(CitationValidationError):
        _prepare({"retrieval_candidates": [shared], "source_documents": [metadata],
                  "mandatory_contexts": [] if problem == "missing" else [mandatory]})


def test_multiple_originals_retain_their_own_source_revision_and_digest():
    row, metadata = _source(100)
    first = metadata["artifacts"][0]
    first["source_revision_id"] = _id(1099)
    second = {**first, "artifact_id": _id(2101), "original_filename": "report.docx",
              "source_revision_id": row["revision_id"], "content_sha256": "b" * 64}
    metadata["artifacts"] = [first, second, first]
    _, catalog = _prepare({"retrieval_candidates": [row], "source_documents": [metadata]})
    rendered, trace = render_citations(f"근거 문서: {_marker(catalog.sources[0])}", catalog)
    assert rendered.count("/download") == 2
    assert [a["source_revision_id"] for a in trace["sources"][0]["artifacts"]] == [_id(1099), row["revision_id"]]
    assert trace["sources"][0]["artifacts"][1]["content_sha256"] == "b" * 64
    assert trace["sources"][0]["delivered_body_sha256"] == [sha256(row["excerpt_md"].encode()).hexdigest()]


@pytest.mark.parametrize("problem", ["space", "duplicate_source", "artifact", "digest", "uuid"])
def test_inconsistent_metadata_is_rejected(problem):
    row, metadata = _source(100)
    sources = [metadata]
    if problem == "space":
        metadata["source_space_id"] = _id(999)
    elif problem == "duplicate_source":
        sources.append({**metadata, "title": "Different title"})
    elif problem == "artifact":
        metadata["artifacts"].append({**metadata["artifacts"][0], "original_filename": "other.pdf"})
    elif problem == "digest":
        metadata["artifacts"][0]["content_sha256"] = "wrong"
    else:
        row["item_id"] = "not-an-id"
    with pytest.raises(CitationValidationError):
        _prepare({"retrieval_candidates": [row], "source_documents": sources})


def test_turn_bound_handles_cannot_be_rebound_by_another_turn_or_view_context():
    row, metadata = _source(100)
    payload = {"retrieval_candidates": [row], "source_documents": [metadata]}
    _, first = _prepare(payload, turn=1)
    _, repeated = _prepare(payload, turn=1)
    _, second = _prepare(payload, turn=2)
    _, other_view = _prepare(payload, turn=1, view=11)
    assert first == repeated
    assert first.sources[0].handle != second.sources[0].handle
    with pytest.raises(CitationValidationError):
        render_citations(f"근거 문서: {_marker(first.sources[0])}", second)
    with pytest.raises(CitationValidationError):
        render_citations(f"근거 문서: {_marker(first.sources[0])}", other_view)


@pytest.mark.parametrize("answer", [
    "근거 문서: [[wiki:missing]]", "근거 문서: [[wiki:missing]", "본문 [[wiki:missing]]",
    "근거 문서: <a>[[wiki:missing]]</a>", "근거 문서: [[wiki:missing]] extra",
    "근거 문서: [[WIKI:missing]]", r"근거 문서: [[wiki\:missing]]",
    "근거 문서: &#91;&#91;wiki&#58;missing]]",
])
def test_unknown_or_malformed_markers_fail_closed(answer):
    with pytest.raises(CitationValidationError):
        render_citations(answer, CitationCatalog(_id(10), ()))


@pytest.mark.parametrize("answer", [
    "[file](/api/knowledge/v1/spaces/x)", "[file](https://host/api/knowledge/v1/spaces/x)",
    '<a href="/api/knowledge/v1/spaces/x">file</a>',
    '<a href="/api&#47;knowledge&#47;v1/spaces/x">file</a>',
    '[file](/%61pi/knowledge/v1/spaces/x)', '[file](/api/knowledge/./v1/spaces/x)',
    '[file](/api/temp/../knowledge/v1/spaces/x)',
    '[file](/api/knowledge%25252fv1/spaces/x)',
    '<a href="`/api/knowledge/v1/spaces/x`">file</a>',
    '[file](`/api/knowledge/v1/spaces/x`)',
    '<a href="/a&#9;pi/knowledge/v1/spaces/x">file</a>',
    '<a href="/api/\nknowledge/v1/spaces/x">file</a>',
    '<a href="/api/\nknowledge/./v1/spaces/x">file</a>',
    '[source]: /api/knowledge/v1/spaces/x',
    '```bad`fence\n[file](/api/knowledge/v1/spaces/x)\n```',
    r'\`[file](/api/knowledge/v1/spaces/x)\`',
])
def test_model_authored_internal_links_are_rejected_even_with_encoding_or_literal_disguise(answer):
    with pytest.raises(CitationValidationError):
        render_citations(answer, CitationCatalog(_id(10), ()))


@pytest.mark.parametrize("answer", [
    "일반 지식으로 답변합니다.\n근거 문서: 일반 지식\n\n## 전체 요약\n요약.",
    "외부 자료 [원문](https://example.org/research)와 비교했습니다.",
    "Use `GET /api/knowledge/v1/spaces/example` in the shell.",
    "```powershell\nInvoke-WebRequest /api/knowledge/v1/spaces/example\n```\n일반 설명",
    "~~~text\n근거 문서: [[wiki:example]]\n~~~\n후속 설명",
    "Example: `[[wiki:source]]`, not a real citation.",
    "외부 [자료](https://example.org) 옆 `GET /api/knowledge/v1/spaces/x` 예시",
    "``Literal ` inside /api/knowledge/v1/spaces/example``",
    "    GET /api/knowledge/v1/spaces/example\n    [[wiki:example]]\n",
    "Example `GET\n/api/knowledge/v1/spaces/example` code.",
    "- Code example:\n\n      GET /api/knowledge/v1/spaces/example\n",
    "> ```text\n> /api/knowledge/v1/spaces/example\n> ```",
])
def test_general_web_shell_and_literal_examples_are_preserved(answer):
    rendered, trace = render_citations(answer, CitationCatalog(_id(10), ()))
    assert rendered == answer
    assert trace["status"] == "no_wiki_body"
    assert trace["sources"] == trace["occurrences"] == []


def test_valid_link_is_not_semantic_support_and_uncited_answer_remains_uncited():
    row, metadata = _source(100, body="양식 어류의 사료 전환율")
    _, catalog = _prepare({"retrieval_candidates": [row], "source_documents": [metadata]})
    _, trace = render_citations(f"태양은 얼음으로 되어 있다.\n근거 문서: {_marker(catalog.sources[0])}", catalog)
    assert trace["status"] == "resolved"
    assert trace["claim_support"] == "not_semantically_verified"
    answer = "사료 전환율에 대해 설명합니다."
    rendered, trace = render_citations(answer, catalog)
    assert rendered == answer
    assert trace["status"] == "uncited" and trace["sources"] == []


def test_link_labels_are_escaped_crlf_preserved_and_rendered_expansion_is_bounded():
    row, metadata = _source(100, filename='file[1](x)<img>.pdf')
    row["title"] = metadata["title"] = 'Doc [x](javascript:alert) <b> * #'
    _, catalog = _prepare({"retrieval_candidates": [row], "source_documents": [metadata]})
    line = f"근거 문서: {_marker(catalog.sources[0])}\r\n"
    rendered, _ = render_citations(line, catalog)
    assert rendered.endswith("\r\n")
    assert "<img>" not in rendered and "<b>" not in rendered
    assert r"file\[1\]\(x\)&lt;img&gt;.pdf" in rendered
    assert r"Doc \[x\]\(javascript:alert\)" in rendered
    with pytest.raises(CitationValidationError):
        render_citations(line * 250, catalog)


def test_citation_marker_mixed_with_inline_code_is_not_silently_discarded():
    row, metadata = _source(100)
    _, catalog = _prepare({"retrieval_candidates": [row], "source_documents": [metadata]})
    with pytest.raises(CitationValidationError):
        render_citations(f"근거 문서: {_marker(catalog.sources[0])} `extra code`", catalog)


@pytest.mark.parametrize("answer", [
    '<a href="#" onclick="location.href=\'/api/\'+\'knowledge/v1/x\'">source</a>',
    '<a href="javascript:location.href=\'/api/\'+\'knowledge/v1/x\'">source</a>',
    '<a href="java&#x09;script:alert(1)">source</a>',
    '[source](javascript:alert%281%29)',
    '<img src="x" onerror="alert(1)">', '<iframe src="https://example.org"></iframe>',
    '<script>location.href="/api/"+"knowledge/v1/x"</script>',
    '<div style="background:url(x)">source</div>',
    '<svg><a href="https://example.org">source</a></svg>',
    '<a/href=javascript:alert(1)>source</a>', '<img/src=x onerror=alert(1)>',
    '[source](http://[invalid)',
])
def test_model_output_cannot_execute_code_to_construct_a_different_link(answer):
    with pytest.raises(CitationValidationError):
        render_citations(answer, CitationCatalog(_id(10), ()))


@pytest.mark.parametrize("answer", [
    '<b>설명</b>과 <a href="https://example.org/paper">외부 원문</a>',
    '```html\n<script>alert(1)</script>\n```',
    '`[example](javascript:alert(1))` is an example, not a live link.',
    '<img src="https://example.org/plot.png" alt="그래프">',
])
def test_passive_html_and_code_examples_are_not_disabled(answer):
    assert render_citations(answer, CitationCatalog(_id(10), ()))[0] == answer
