from __future__ import annotations

import hashlib

import pytest

from code_assist_v1.knowledge_core.evidence_passages import (
    allocate_evidence_bytes, select_evidence_passages,
)

pytestmark = [pytest.mark.unit, pytest.mark.scenario("RANK-009", "RANK-011")]


@pytest.mark.parametrize("size", [4095, 4096, 4097, 12_288, 24_576, 32_768, 98_303, 98_304])
def test_whole_korean_body_has_no_four_kib_cutoff(size):
    body = "한" * (size // 3) + "x" * (size % 3)
    result = select_evidence_passages(body, 98_304, query="질문")
    assert result.text == body
    assert result.ranges == ((0, len(body)),)
    assert not result.truncated
    assert result.source_bytes == size
    assert result.source_sha256 == hashlib.sha256(body.encode()).hexdigest()


def test_forty_documents_have_exact_shared_budget_and_explicit_zero_allocations():
    assert allocate_evidence_bytes((3_072,) * 40, 98_304) == (3_072,) * 32 + (0,) * 8
    assert allocate_evidence_bytes((98_305, 10), 98_304) == (98_304, 0)
    assert allocate_evidence_bytes((12_288, 24_576, 32_768), 98_304) == (12_288, 24_576, 32_768)


def test_late_korean_fact_preserves_numbers_conditions_and_exact_ranges():
    fact = ("복구 승인 조건: 오차가 0.75% 미만이고 3영업일 동안 유지되어야 한다. "
            "비용은 1,275,000원이다. 센서 교체 시에는 이 조건을 적용하지 않는다. "
            "시행일은 2026-09-08 09:30 KST이며 이전 1.25% 기준보다 엄격하다.")
    body = "# 운영 매뉴얼\n\n" + ("식당 예약과 식재료 목록만을 정리한 별도 배경.\n\n" * 3_000)
    body += "\n\n" + fact + "\n"
    result = select_evidence_passages(body, 8_192, query="복구 승인 조건 오차 센서 시행일 비용")
    assert fact in result.text
    assert result.truncated
    assert len(result.text.encode()) <= 8_192
    assert result.text == "\n\n".join(body[start:end] for start, end in result.ranges)
    assert result == select_evidence_passages(body, 8_192, query="복구 승인 조건 오차 센서 시행일 비용")


@pytest.mark.parametrize("budget", [0, 1, 2, 3, 4096, 98_304])
def test_unicode_budget_never_splits_a_character_or_overflows(budget):
    body = "가나다. 🧪 검증\n\n" * 10_000
    result = select_evidence_passages(body, budget, query="검증")
    assert len(result.text.encode()) <= budget
    assert "\ufffd" not in result.text
    assert result.truncated


def test_source_ranges_are_code_points_even_after_emoji_and_combining_marks():
    body = ("🧪e\u0301 배경 정리.\n\n" * 1_000) + "\n복구 승인 조건은 0.75% 미만."
    result = select_evidence_passages(body, 3_000, query="복구 승인 조건")
    assert "복구 승인 조건은 0.75% 미만." in result.text
    assert result.text == "\n\n".join(body[start:end] for start, end in result.ranges)
    assert result.ranges[-1][1] == len(body)
