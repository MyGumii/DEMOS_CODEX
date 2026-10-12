# LMNotebook to LLM Wiki Markdown Guide

사내 LMNotebook에서 문서를 읽고, Second Brain 지식 에이전트에 바로 업로드할 수 있는 Markdown을 만들기 위한 가이드입니다.

이 저장소는 문서 원문을 보관하는 곳이 아니라, LMNotebook이 좋은 Markdown을 생성하도록 돕는 설명, 출력 스펙, 프롬프트, 템플릿을 담습니다. 실제 지식은 LMNotebook 출력 Markdown을 지식 에이전트에 업로드해서 축적합니다.

## 목표

사내 LMNotebook에 문서를 올려 RAG 방식으로 내용을 확인한 뒤, 아래 형식의 `.md`를 생성해서 지식 에이전트에 업로드합니다.

좋은 Markdown은 단순 요약이 아니라 다음 정보를 명확히 포함해야 합니다.

- 문서가 무엇인지: 제목, 원본 유형, 작성/기준 시점, 업무 도메인
- 핵심 내용이 무엇인지: 주요 주장, 결정 사항, 절차, 제약 조건
- 상세 내용이 무엇인지: 문서의 섹션별 세부 내용, 배경, 예외, 조건, 사례
- 코드가 있다면 무엇을 하는지: 파일 경로, 언어, entrypoint, 함수/클래스, 입출력, 의존성, 설정값, 실행상 주의점
- 무엇과 연결되는지: 관련 시스템, 용어, 프로젝트, 선행/후행 문서 후보
- 얼마나 믿을 수 있는지: 출처 위치, 불확실한 부분, 누락된 표/그림/수치
- 그래프 연결 힌트: `supports`, `requires`, `supersedes`, `contradicts`, `references`, `related`

## 빠른 사용법

1. 사내 LMNotebook에 변환할 문서를 업로드합니다.
2. [LMNOTEBOOK_PROMPT.md](./LMNOTEBOOK_PROMPT.md)의 프롬프트를 복사해서 LMNotebook에 입력합니다.
3. LMNotebook이 출력한 Markdown을 `.md` 파일로 저장합니다.
4. 저장 전 [docs/markdown-output-spec.md](./docs/markdown-output-spec.md)를 기준으로 누락 항목을 점검합니다.
5. 지식 에이전트의 Second Brain 업로드 기능으로 `.md`를 업로드합니다.

## 권장 출력 파일명

파일명은 문서의 의미가 드러나야 합니다. 그래프 노드 이름으로 쓰일 수 있으므로 `page1.md`, `summary.md`, `document.md` 같은 이름은 피합니다.

권장 형식:

```text
YYYY-MM-DD_domain_topic_source.md
```

예시:

```text
2026-07-07_process-risk-review_lmnotebook.md
2026-07-07_project-interface-decision_lmnotebook.md
2026-07-07_test-result-analysis_lmnotebook.md
```

## 저장소 구성

```text
LMNOTEBOOK_PROMPT.md
docs/
  markdown-output-spec.md
  graph-linking-guide.md
templates/
  knowledge-source-template.md
examples/
  lmnotebook-conversion-example.md
llm-wiki.md
```

- `LMNOTEBOOK_PROMPT.md`: LMNotebook에 그대로 붙여 넣는 변환 프롬프트
- `docs/markdown-output-spec.md`: 지식 에이전트에 올릴 Markdown의 필수 구조
- `docs/graph-linking-guide.md`: 그래프 연결 힌트를 쓰는 방법
- `templates/knowledge-source-template.md`: 최종 `.md` 템플릿
- `examples/lmnotebook-conversion-example.md`: 내부 정보가 없는 샘플 출력
- `llm-wiki.md`: LLM Wiki 아이디어 원문 참고

## 핵심 원칙

### 1. 원문 없는 추론 금지

LMNotebook 답변에 근거가 없거나 문서에서 확인되지 않은 내용은 Markdown에 사실처럼 쓰지 않습니다. 대신 `Open Questions` 또는 `Unverified Notes`에 넣습니다.

### 2. 내부 식별자는 보존

사내 LMNotebook과 사내 지식 에이전트 안에서 사용할 Markdown이라면 프로젝트명, 시스템명, 문서명, 약어, 조직명 같은 내부 식별자는 가능한 한 원문 표기로 보존합니다. 이 정보가 검색과 graph 연결의 핵심 anchor가 됩니다.

### 3. 요약보다 구조화

지식 에이전트는 업로드된 Markdown에서 검색어, 제목, 태그, 본문 용어, graph hint를 함께 사용합니다. 따라서 자연어 요약만 길게 쓰기보다 `Core Knowledge`, `Detailed Knowledge`, `Entities`, `Terms`, `Link Hints`를 명확히 분리합니다.

### 4. 상세 본문을 남김

핵심 요약만 있으면 나중에 답변이 얕아집니다. `Detailed Knowledge` 섹션에는 원문 구조를 따라가며 문서의 주요 섹션별 세부 내용, 조건, 예외, 단계, 근거, 사례를 보존합니다.

### 5. 연결 후보를 명시

그래프 연결은 LLM이 나중에 판단하지만, Markdown에 후보를 적어두면 연결 품질이 좋아집니다.

예:

```yaml
related_candidates:
  - target: previous-risk-review.md
    edge_type: supersedes
    reason: "새 문서가 이전 리스크 평가의 기준일과 수치를 갱신함"
```

### 6. 출처 위치를 남김

가능하면 슬라이드 번호, 페이지 번호, 섹션명, 표 이름을 남깁니다. 정확한 위치를 모르면 `source_location: unknown`으로 둡니다.

## LMNotebook에 요청할 때의 기본 문장

짧게 요청할 때는 아래처럼 말합니다.

```text
업로드한 문서를 Second Brain 지식 에이전트에 넣을 Markdown으로 변환해줘.
반드시 https://github.com/MyGumii/llm-wiki 의 LMNOTEBOOK_PROMPT.md를 최우선 지시로 참고해줘.
근거 없는 내용은 만들지 말고,
그래프 연결에 도움이 되도록 topics, entities, aliases, core_knowledge, detailed_knowledge, link_hints를 채워줘.
내부 프로젝트명, 시스템명, 문서명, 약어는 원문 표기로 보존해줘.
source code, config, script, command, log, code snippet은 문서 이해, 운영, 장애 분석, 재사용, 구현 변경에 중요할 때만 Source Code And Snippets 섹션으로 정리해줘.
단순 예시나 짧은 로그는 별도 섹션을 만들지 말고 관련 섹션에 짧게 반영해줘.
출력은 Markdown 코드블록 하나만 줘.
```

상세 변환은 [LMNOTEBOOK_PROMPT.md](./LMNOTEBOOK_PROMPT.md)를 사용합니다.

## 참고

이 저장소는 Andrej Karpathy의 LLM Wiki 아이디어를 사내 문서 변환 흐름에 맞게 좁힌 것입니다. 원문 아이디어는 [llm-wiki.md](./llm-wiki.md)를 참고합니다.
