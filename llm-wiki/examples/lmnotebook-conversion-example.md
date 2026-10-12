---
title: "Quarterly Process Risk Review Summary"
source_type: "lmnotebook_markdown"
origin_type: "pptx"
source_date: "2026-07-01"
converted_at: "2026-07-07"
domain: "process risk management"
project: "example project"
sensitivity: "internal"
confidence: "medium"
topics:
  - "process risk"
  - "review workflow"
  - "mitigation status"
entities:
  - "Example Process"
  - "Risk Review Board"
  - "Mitigation Tracker"
aliases:
  - "RRB"
  - "risk tracker"
related_candidates:
  - target: "previous-process-risk-review.md"
    edge_type: "supersedes"
    reason: "This review updates the previous mitigation status and review date"
  - target: "risk-mitigation-workflow.md"
    edge_type: "requires"
    reason: "The action items depend on the mitigation workflow definition"
---

# Quarterly Process Risk Review Summary

> TL;DR: 이 예시는 실제 회사 정보가 없는 샘플입니다. 문서는 분기별 공정 리스크 상태, 완화 조치 진행률, 후속 검토 항목을 정리합니다.

## Source Context

- 원본 유형: PPTX
- 문서 기준일: 2026-07-01
- 작성/사용 목적: 분기별 리스크 상태 공유와 후속 조치 정리
- 주요 독자: 리스크 검토 담당자, 프로젝트 리더
- 변환 신뢰도: medium
- source_location 기준: slide number

## Executive Summary

- 주요 리스크는 세 가지 범주로 정리되며, 두 항목은 완화 조치가 진행 중입니다. (source: slide 3)
- 후속 조치는 담당자 지정, 완료 기준 확인, 다음 리뷰 일정 확정으로 나뉩니다. (source: slide 5)
- 일부 수치는 표 이미지에서 추출되어 사람이 원본과 대조해야 합니다. (source: slide 4)

## Core Knowledge

### Key Claims

- [C1] 현재 리뷰는 이전 리뷰 대비 완화 조치 상태를 갱신합니다. (source: slide 2)
- [C2] 미완료 항목은 다음 리뷰 전까지 owner와 완료 기준을 확정해야 합니다. (source: slide 5)
- [C3] 표 기반 수치는 OCR 또는 변환 과정에서 오류 가능성이 있습니다. (source: slide 4)

### Requirements And Constraints

- 요구사항: 미완료 항목별 owner와 완료 기준을 다음 리뷰 전까지 확정해야 합니다.
- 제약 조건: 표 이미지 기반 수치는 원본과 대조해야 합니다.
- 전제 조건: 이전 리뷰와 현재 리뷰의 리스크 분류 체계가 동일해야 비교가 가능합니다.

### Definitions

- Risk Review Board: 리스크 상태를 검토하고 후속 조치를 결정하는 그룹입니다.
- Mitigation Tracker: 완화 조치의 owner, 상태, 완료 기준을 추적하는 문서입니다.

## Detailed Knowledge

### Review Scope

- 핵심 내용: 문서는 분기별 공정 리스크 상태를 리뷰하고 후속 조치를 정리합니다.
- 배경: 이전 리뷰에서 남은 미완료 항목의 상태를 갱신하는 목적입니다.
- 세부 설명: 리스크 항목은 상태, owner, 완료 기준 기준으로 정리됩니다.
- 조건/예외: 이전 리뷰와 분류 체계가 다르면 직접 비교하지 않습니다.
- 관련 entity: Example Process, Risk Review Board, Mitigation Tracker
- 근거 위치: slide 2, slide 3

### Mitigation Follow-up

- 핵심 내용: 미완료 완화 조치는 owner 지정과 완료 기준 정의가 필요합니다.
- 배경: 완료 기준이 없으면 다음 리뷰에서 상태 판단이 어렵습니다.
- 세부 설명: 각 항목은 담당자, 목표일, 완료 판정 기준으로 관리됩니다.
- 조건/예외: 긴급 리스크는 일반 리뷰 주기와 별도로 escalated review가 필요할 수 있습니다.
- 관련 entity: Mitigation Tracker
- 근거 위치: slide 5

## Decisions And Recommendations

- 결정 사항: 다음 리뷰 전까지 미완료 항목별 owner를 지정합니다.
- 권고 사항: 표 이미지에서 추출한 수치는 원본 PPTX와 대조합니다.
- 적용 조건: 동일 리스크 분류 체계를 쓰는 리뷰에 적용합니다.
- 예외 조건: 새 분류 체계가 도입된 경우 이전 리뷰와 직접 비교하지 않습니다.

## Procedures Or Workflows

1. 현재 리스크 항목을 분류합니다.
2. 이전 리뷰 대비 상태 변화를 확인합니다.
3. 미완료 항목의 owner와 완료 기준을 지정합니다.
4. 다음 리뷰 일정과 확인 항목을 기록합니다.

## Entities And Terms

| Name | Type | Meaning In This Document | Notes |
|---|---|---|---|
| Example Process | process | 리뷰 대상 공정 | 샘플 이름 |
| Risk Review Board | team | 리스크 검토 의사결정 그룹 | RRB로도 표기 |
| Mitigation Tracker | document | 완화 조치 상태 추적 문서 | 관련 후보 |

## Evidence And Source Details

| Item | Value | Source Location | Confidence |
|---|---:|---|---|
| Open risk items | 3 | slide 3 | medium |
| Pending mitigation items | 2 | slide 4 | low |

## Risks And Constraints

- 리스크: 표 이미지에서 추출된 수치의 정확도가 낮을 수 있습니다.
- 제약: 이전 리뷰와 분류 체계가 다르면 단순 비교가 어렵습니다.
- 의존성: 완화 조치 workflow와 owner 지정 기준이 필요합니다.
- 정책/운영상 주의: 실제 문서에서는 owner, 완료 기준, 다음 리뷰 일정이 원문과 일치하는지 확인해야 합니다.

## Link Hints For Graph

- supports:
  - "risk-mitigation-workflow.md": 후속 조치 필요성을 뒷받침합니다.
- requires:
  - "risk-mitigation-workflow.md": owner와 완료 기준 정의가 필요합니다.
- supersedes:
  - "previous-process-risk-review.md": 이전 리뷰의 상태 정보를 갱신합니다.
- contradicts:
  - 없음
- references:
  - "mitigation-tracker.md": 완화 조치 상태 추적 문서를 참조합니다.
- related:
  - "review-calendar.md": 다음 리뷰 일정과 관련됩니다.

## Source Trace

- 근거가 강한 부분: slide 2, slide 3의 항목 구조
- 근거가 약한 부분: slide 4의 표 이미지 수치
- 표/그림/OCR/첨부 누락 가능성: 있음
- LMNotebook 답변에서 확인하지 못한 부분: 원본 표의 세부 주석

## Open Questions

- 이전 리뷰의 분류 체계가 현재 리뷰와 동일한가?
- slide 4의 표 수치가 원본과 일치하는가?

## Extraction Notes

- 원문 구조가 깨진 부분: 표 이미지 기반 수치
- 생략한 섹션 또는 낮은 신뢰도로 정리한 부분: slide 4 표의 세부 주석
- Markdown 업로드 전에 사람이 확인해야 할 부분: Evidence And Source Details 표
