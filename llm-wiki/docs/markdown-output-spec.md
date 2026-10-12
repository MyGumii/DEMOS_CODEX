# Markdown Output Spec

이 문서는 LMNotebook이 생성해야 하는 Second Brain 업로드용 Markdown의 구조를 정의합니다.

## 왜 구조가 필요한가

지식 에이전트는 Markdown의 제목, 본문, 태그성 단어, graph relation을 함께 사용합니다. 따라서 긴 요약문 하나보다, 반복 가능한 필드와 섹션을 가진 Markdown이 검색과 연결에 더 유리합니다.

## 필수 frontmatter

모든 변환 결과는 YAML frontmatter로 시작합니다.

```yaml
---
title: "문서 제목"
source_type: "lmnotebook_markdown"
origin_type: "pptx"
source_date: "YYYY-MM-DD 또는 unknown"
converted_at: "YYYY-MM-DD 또는 unknown"
domain: "업무 도메인 또는 unknown"
project: "프로젝트/업무명 또는 unknown"
sensitivity: "internal"
confidence: "high|medium|low"
topics:
  - "핵심 주제"
entities:
  - "주요 entity"
aliases:
  - "약어"
related_candidates:
  - target: "연결 후보 문서명 또는 unknown"
    edge_type: "supports|requires|supersedes|contradicts|references|related"
    reason: "연결 근거"
---
```

### 필드 설명

`title`은 그래프 노드 라벨처럼 쓰일 수 있으므로 문서의 실제 의미가 드러나야 합니다.

`source_type`은 이 파일이 LMNotebook 변환 결과임을 표시합니다.

`origin_type`은 원본 문서 유형입니다. PPTX, DOCX, PDF, TXT, image, source_code, code_snippet, config, log, unknown 중 하나를 권장합니다.

`source_date`는 문서의 작성일이 아니라, 문서 내용이 유효한 기준일을 우선합니다.

`domain`은 업무 영역입니다. 너무 넓은 이름보다 검색에 도움이 되는 구체적 이름을 씁니다.

`topics`는 검색 키워드입니다. 3-8개 정도가 적당합니다.

`entities`는 그래프 연결 후보입니다. 시스템, 제품, 공정, 조직, 지표, 정책, 문서명을 넣습니다.

`aliases`는 약어, 영문명, 다른 표기입니다.

`related_candidates`는 LLM이 graph edge를 만들 때 참고할 후보입니다.

## 필수 섹션

### Source Context

문서의 출처와 변환 상태를 적습니다. 이 섹션은 나중에 답변의 신뢰도를 판단하는 데 중요합니다.

### Executive Summary

3-7개의 bullet로 핵심을 정리합니다. 가능하면 source location을 붙입니다.

### Core Knowledge

문서에서 확인되는 핵심 주장, 요구사항, 제약, 정의를 구조화합니다. 나중에 다른 문서와 충돌하거나 지지되는 대상을 찾기 쉽습니다.

### Detailed Knowledge

원문 문서의 주요 섹션, 슬라이드, 페이지, 표, 그림 단위로 세부 내용을 보존합니다. 핵심 요약만 남기면 답변이 얕아지므로, 배경, 조건, 예외, 단계, 사례, 근거를 함께 적습니다.

### Source Code And Snippets

문서에 source code, config, script, command, log, code snippet이 있고, 그 내용이 문서 이해, 운영, 장애 분석, 재사용, 구현 변경에 중요할 때만 작성합니다. 대부분의 업무 문서는 이 섹션이 없어도 됩니다.

시스템 엔지니어링 문서에서는 파일 경로, 함수명, 클래스명, 설정 key, command, API endpoint가 검색과 graph 연결의 핵심 anchor가 될 수 있습니다. 단순 예시, 짧은 로그, 부가적인 명령은 별도 섹션을 만들지 말고 `Detailed Knowledge` 또는 `Evidence And Source Details`에 짧게 반영합니다.

이 섹션에는 다음을 포함합니다.

- Code Reference Map: 코드 항목, 언어/유형, 경로 또는 식별자, 역할, 출처 위치
- Important Snippets: 원문에 나온 핵심 코드/명령/설정
- Code Behavior Notes: entrypoint, inputs, outputs, dependencies, config/env, 외부 시스템, side effects, failure modes
- Code Questions: 실행 환경, 버전, 관련 파일처럼 추가 확인이 필요한 부분

### Decisions And Recommendations

의사결정, 권고, 적용 조건, 예외 조건을 분리합니다. 문서에 없으면 없다고 명시합니다.

### Procedures Or Workflows

절차, 승인 단계, 운영 흐름, 분석 순서를 단계로 정리합니다.

### Entities And Terms

용어와 entity를 표로 정리합니다. 같은 entity가 여러 문서에 반복되면 graph 연결 품질이 좋아집니다.

### Evidence And Source Details

표, 수치, 일정, 비용, 리스크, 비교 기준, 인용, 그림 설명 등 답변 근거로 쓸 수 있는 세부 자료를 정리합니다. 논문이나 실험 문서에 한정하지 않습니다.

### Risks And Constraints

문서가 말하는 제한 조건, 전제, 정책/운영상 주의점을 정리합니다.

### Link Hints For Graph

문서 간 연결 후보를 edge type별로 적습니다. 확실하지 않으면 억지로 만들지 않습니다.

### Source Trace

근거가 강한 부분, 약한 부분, OCR/표/그림 누락 가능성을 적습니다.

### Open Questions

문서만으로 답할 수 없는 질문을 남깁니다.

### Extraction Notes

표 깨짐, 그림 누락, 원문 구조 손실, 사람이 확인해야 할 부분을 적습니다.

## 품질 기준

- H1 제목이 있어야 합니다.
- 문서에서 확인되지 않은 사실은 본문 claim에 넣지 않습니다.
- 같은 개념은 한 문서 안에서 같은 이름으로 씁니다.
- 내부 프로젝트명, 시스템명, 문서명, 약어는 원문 표기로 보존합니다.
- 약어는 `aliases`와 `Entities And Terms`에 모두 반영합니다.
- 중요한 코드/설정/명령이 있으면 파일 경로, 함수명, 클래스명, config key, command, API endpoint를 보존합니다.
- 숫자와 날짜는 source location을 붙입니다.
- graph hint는 연결 근거가 있는 경우만 작성합니다.
- 핵심 요약에 들어가지 않은 세부 내용은 `Detailed Knowledge`에 남깁니다.
