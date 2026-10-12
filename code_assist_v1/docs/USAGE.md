# Second Brain Agent 사용자 가이드

Second Brain Agent는 대화, 문서, 코드 파일, 개인 지식 저장소, Notion 스타일 문서 작업공간을 함께 사용하는 지식 기반 작업 에이전트입니다.

이 문서는 일반 사용자가 실제 화면에서 사용하는 기능만 설명합니다. 개발용 실행 옵션이나 내부 브릿지 옵션은 다루지 않습니다.

## 1. 접속과 계정

| 서비스 | 기본 주소 | 역할 |
|---|---|---|
| Second Brain Agent | `http://localhost:10011` | 채팅, 지식 검색, 문서 업로드, 세션 관리 |
| Notion Workspace | `http://localhost:10012` | 개인 문서 작성, 공유 페이지, 채팅/지식 연결 |

두 서비스는 같은 로그인 계정을 사용합니다. `localhost:10011`로 로그인했다면 `localhost:10012`도 같은 호스트로 접속해야 쿠키가 공유됩니다.

계정별 데이터는 분리됩니다.

```text
code_assist_v1/user_data/<user>/...
code_assist_v1/knowledge/<user>/wiki/...
```

다른 사용자의 채팅, Notion 문서, Second Brain source는 기본적으로 보이지 않습니다. Notion에서 명시적으로 공유한 페이지만 게스트 워크스페이스로 보입니다.

처음 사용하는 계정은 로그인 후 필요한 경우 비밀번호를 변경하고, Notion Workspace에서 `Second Brain 템플릿 만들기`를 눌러 기본 페이지 세트를 만들면 됩니다.

## 2. 전체 구조

Second Brain Agent는 네 가지 컨텍스트를 합쳐 답변합니다.

| 컨텍스트 | 설명 | 사용 시점 |
|---|---|---|
| 대화 기록 | 현재 세션의 최근 메시지와 compact summary | 항상 |
| Second Brain | 승인된 source markdown, index, graph, log | Second Brain 토글 ON |
| Workspace 파일 | 현재 세션에 올린 코드/문서 파일 | 파일 첨부 시 |
| Notion 페이지 | 특정 세션에 연결한 Notion 페이지 markdown | Notion에서 Chat에 추가 시 |

응답 생성 중에는 `응답 준비 중`, `GGUF 모델 확인 중`, `API 모델 응답 대기 중`, `내부 처리 중` 같은 상태가 표시됩니다. 실제 chain-of-thought는 노출하지 않고, 진행 상태만 보여줍니다.

## 3. 채팅 사용

1. 모델 드롭다운에서 사용할 모델을 선택합니다. 잘 모르면 기본값을 사용합니다.
2. 필요한 경우 스킬, Second Brain, Workspace 파일을 켭니다.
3. 입력창에 질문을 씁니다.
4. `Enter`로 전송합니다. 줄바꿈은 `Shift + Enter`입니다.
5. 응답을 멈추려면 `Esc` 또는 중단 버튼을 사용합니다.

예시:

- `첨부한 PDF를 기준으로 핵심 내용을 정리해줘.`
- `Second Brain을 참고해서 Graph Attention Network 수식을 설명해줘.`
- `워크스페이스에 올린 코드에서 에러 원인을 찾아줘.`
- `현재 대화에서 결정사항과 할 일을 정리해줘.`

수식은 기본적으로 LaTeX 원문을 그대로 던지지 않고, 읽기 쉬운 Unicode/일반 표기로 표현합니다. 수식 덩어리는 하이라이트되어 표시되고, 긴 식은 줄바꿈되어 읽기 쉽게 렌더링됩니다.

## 4. 세션 관리

좌측 `세션` 탭에서 과거 대화를 열거나 삭제할 수 있습니다. 새 대화를 시작하려면 상단 `+ 세션`을 누릅니다.

입력창 명령:

| 명령 | 동작 |
|---|---|
| `/status` | 현재 모델, context window, 추정 입력 토큰, 남은 context, compact 권장 상태를 표시합니다. |
| `/compact` | 오래된 대화를 요약하고, 이후 호출에는 summary + 최근 메시지만 보냅니다. 원본 transcript는 유지됩니다. |
| `/clear` | 현재 화면과 대화 context를 비우고 새 세션 상태로 시작합니다. 저장된 과거 세션 삭제와는 다릅니다. |

### 세션 명령 상세

`/status`는 지금 세션이 모델 context를 얼마나 쓰고 있는지 확인할 때 사용합니다. 모델명, context window, 추정 토큰, 남은 토큰, compact 권장 상태, compact summary 사용 여부를 보여줍니다. 답변이 길어지거나 파일/Notion/Second Brain context를 많이 붙인 상태에서 먼저 확인하면 좋습니다.

`/compact`는 대화가 길어졌을 때 오래된 메시지를 요약본으로 압축합니다. 이후 요청에는 `compact summary + 최근 메시지`가 들어가므로 context window를 아낄 수 있습니다. 원본 세션 기록을 삭제하는 기능은 아니며, 오래된 대화의 세부 문장보다 결정사항과 진행 맥락을 유지하는 데 목적이 있습니다.

`/clear`는 현재 화면과 모델에 전달되는 대화 context를 비우고 새 대화 상태로 돌아갑니다. 저장된 과거 세션, Second Brain 지식, Workspace 파일, 장기기억을 삭제하지 않습니다. 주제가 완전히 바뀌었거나 이전 대화가 답변에 섞이면 안 될 때 사용합니다.

자동 compact는 모든 모델에 공통 적용됩니다.

| 기준 | 동작 |
|---|---|
| context 65% 이상 | 경고 상태 |
| context 75% 이상 | 자동 compact 후보 |
| context 85% 이상 | 강제 compact |

## 5. Agent Harness와 장기기억

Second Brain Agent는 세 가지 기억 계층을 분리해서 사용합니다.

| 계층 | 역할 | 저장 위치 |
|---|---|---|
| Second Brain | 공식 지식, 승인 source, 업무 규칙, 문서 근거 | `code_assist_v1/knowledge/<user>/wiki/` |
| 장기기억 | 사용자 선호, 프로젝트 관습, 반복되는 작업 방식 | `code_assist_v1/user_data/<user>/memory/` |
| Workspace 임시 인덱스 | 현재 세션/프로젝트에 올린 임시 문서 검색 | `code_assist_v1/user_data/<user>/projects/` |

장기기억은 공식 지식이 아닙니다. Second Brain source, 스펙, 원문 근거와 충돌하면 Second Brain이 우선합니다.

### 장기기억

장기기억은 Mem0와 비슷하게 동작합니다.

1. 사용자의 입력에서 저장 신호를 찾습니다.
2. 저장할 만한 문장이면 `preference`, `project`, `decision`, `lesson` 같은 유형으로 분류합니다.
3. 계정별 장기기억 파일에 JSONL 형태로 저장합니다.
4. 다음 질문을 받을 때 현재 질문과 관련 있는 기억만 검색합니다.
5. 검색된 기억은 낮은 우선순위 context로 답변에 들어갑니다.
6. Second Brain source, 스펙, 원문 근거와 충돌하면 장기기억보다 공식 지식이 우선합니다.

즉, 장기기억은 모든 대화를 통째로 외우는 기능이 아닙니다. 반복해서 쓸 사용자 선호, 프로젝트 관습, 결정사항, 오류 대응 규칙만 작게 저장하고, 질문과 관련 있을 때만 꺼내 쓰는 방식입니다.

저장 위치:

```text
code_assist_v1/user_data/<user>/memory/
  memories.jsonl
  events.jsonl
```

`memories.jsonl`에는 현재 사용할 수 있는 기억이 저장되고, `events.jsonl`에는 저장/수정/삭제 같은 이력이 남습니다.

입력창의 `기억` 버튼은 이번 답변에 장기기억을 참고할지 결정합니다. 끄더라도 저장된 기억이 삭제되지는 않습니다.

#### 어떻게 기억시키나

장기기억에 저장하고 싶은 내용은 평소처럼 말하되, 저장 의도가 드러나게 쓰는 것이 가장 안정적입니다.

| 입력 방식 | 예시 | 결과 |
|---|---|---|
| 명시 저장 | `기억해. 나는 답변을 짧고 실무적으로 받는 걸 선호해.` | 사용자 선호로 저장됩니다. |
| 앞으로의 규칙 | `앞으로 논문 설명은 수식, 변수 정의, 직관을 같이 정리해줘.` | 반복 답변 규칙으로 저장됩니다. |
| 항상 적용 | `항상 포트는 10011을 먼저 확인해.` | 프로젝트/작업 관습으로 저장될 수 있습니다. |
| 프로젝트 규칙 | `이 프로젝트에서는 Notion 초안은 확정 후 Second Brain으로 보낸다.` | 프로젝트 기억으로 저장됩니다. |
| 결정사항 | `오늘 결정: 문서 업로드 후 sources를 정본으로 사용한다.` | 결정 기억으로 저장됩니다. |
| 교훈/주의 | `다음부터 WinError 2가 나면 실행 파일 경로를 먼저 확인한다.` | 반복 오류 대응 기억으로 저장됩니다. |

저장 신호로 인식되는 대표 표현은 `기억해`, `remember`, `저장해`, `앞으로`, `항상`, `기본적으로`입니다. 질문이나 일회성 명령은 기본적으로 저장하지 않습니다.

#### 어떻게 사용되나

장기기억은 답변 전에 현재 질문과 관련 있는 항목만 검색되어 context에 들어갑니다. 예를 들어 “PDF 논문 설명해줘”라고 물었을 때, 과거에 “논문 설명은 수식과 변수 정의를 같이 정리해줘”라고 저장했다면 그 기억이 함께 들어갈 수 있습니다.

응답 하단 메타에 `기억=1`처럼 표시되면 이번 답변에 1개의 장기기억이 참고된 것입니다. `기억+active`처럼 보이면 새 기억이 저장된 것입니다.

장기기억은 다음처럼 활용하는 것이 좋습니다.

- 답변 말투와 길이 선호 유지
- 자주 쓰는 포트, 실행 명령, 프로젝트 구조 기억
- 반복되는 오류 대응 순서 기억
- 팀/프로젝트에서 정한 결정사항 기억
- 문서 설명, 코드 리뷰, 테스트 방식 같은 반복 작업 규칙 유지

좋은 기억 후보:

- `나는 답변을 한국어로 짧게 받는 걸 선호해.`
- `이 프로젝트에서는 포트를 10011로 사용한다.`
- `앞으로 PDF 논문 설명은 수식과 변수 정의를 같이 정리해줘.`
- `WinError 2가 나면 실행 파일 경로를 먼저 확인한다.`

저장하지 않는 것이 좋은 내용:

- `이 파일을 지금만 고쳐줘.`
- `이 오류가 뭔지 알려줘.`
- `이건 임시 테스트야.`
- `방금 말한 UI 색상은 이번 턴에서만 봐줘.`

기억하지 않게 하려면 `이건 기억하지마`, `저장하지마`처럼 명시하면 됩니다. 잘못된 기억은 `이전 규칙은 취소하고 앞으로는 ...`처럼 새 규칙으로 덮어쓰는 방식이 안전합니다.

장기기억과 Second Brain은 용도가 다릅니다. “Graph Attention Network의 수식 설명”처럼 문서 근거가 필요한 지식은 Second Brain source로 저장하는 것이 맞고, “나는 수식을 Unicode와 변수 정의로 설명받고 싶다”처럼 사용자 선호는 장기기억에 맞습니다.

### Agent Harness

Agent Harness는 사용자가 직접 다루는 버튼이 아니라, 매 요청마다 실행되는 내부 계획 레이어입니다.

Harness는 질문을 보고 다음 모드를 조합합니다.

| 모드 | 의미 |
|---|---|
| `coding` | 코드 작성, 수정, 리뷰, 디버깅 |
| `second_brain` | Second Brain source/index/graph 검색 필요 |
| `spec_driven` | Workspace 문서나 스펙 기반 작업 |
| `memory_aware` | 사용자/프로젝트 장기기억 참고 |

Harness는 context를 다음 순서로 정리합니다.

1. 현재 채팅
2. Workspace 파일과 임시 인덱스
3. 고정되거나 검색된 Second Brain source
4. graph 관계와 source trace
5. 사용자/프로젝트 장기기억
6. 활성 스킬

응답 완료 메타에는 사용된 project context, memory, harness mode가 표시될 수 있습니다. 예를 들어 `project=2`, `기억=1`, `mode=coding+second_brain`처럼 보이면 어떤 보조 context가 들어갔는지 알 수 있습니다.

## 6. 모델

모델은 API 모델과 로컬 GGUF 모델을 함께 사용할 수 있습니다.

| 구분 | 설명 |
|---|---|
| API 모델 | 서버 설정에 등록된 사내 API 모델입니다. |
| GGUF 모델 | `code_assist_v1/MODEL_GGUF/`에 `.gguf` 파일을 넣으면 자동 인식됩니다. |

GGUF는 기본적으로 128000 context를 목표로 로드합니다. 메모리나 GPU 상황에 따라 `65536 → 32768 → 8192` 순서로 자동 fallback합니다. 실제 context는 `/status`에서 확인할 수 있습니다.

출력 한도에 도달하면 답변 하단에 안내가 표시됩니다. 이 경우 질문을 나누거나 `/compact` 후 다시 질문하세요.

## 7. Second Brain 지식

Second Brain은 장기적으로 재사용할 지식을 저장하는 canonical knowledge 저장소입니다.

현재 기준 정본은 `sources/`입니다.

```text
knowledge/<user>/wiki/
  index.md
  log.md
  sources/
  assets/
  graph/
    graph.json
    backlinks.json
    pending_diffs.json
  health/
  wiki_index.sqlite
```

| 파일 | 의미 |
|---|---|
| `sources/` | 업로드, 대화 지식화, Notion publish 결과가 저장되는 source markdown |
| `assets/` | PDF/PPTX/DOCX 등에서 추출한 이미지, 슬라이드, 페이지 asset |
| `index.md` | LLM이 먼저 읽는 지식 지도 |
| `log.md` | import, graph update, 품질 점검 이력 |
| `graph.json` | source markdown 간 승인된 관계 |
| `pending_diffs.json` | source 품질, lint, out-of-date 점검 항목 |

`pages/`는 legacy 호환용이며 새 지식 흐름에서는 사용하지 않습니다.

### 지식 등록 방법

| 방법 | 절차 |
|---|---|
| 문서 업로드 | `지식` 탭 → 자료 업로드 |
| 복수 문서 업로드 | 여러 파일을 한 번에 선택하면 각 파일이 단건 업로드와 같은 절차로 순차 처리됩니다. |
| 대화 지식화 | `지식` 탭 → 대화 지식화 → 제목 입력 |
| Notion publish | Notion 페이지 상단 `Second Brain` → preview 확인 → 승인 |

지원 파일:

- `.md`, `.txt`, source code
- `.pdf`
- `.pptx`
- `.docx`
- `.xlsx`
- `.ipynb`

PDF, PPTX, DOCX, XLSX, Notebook은 텍스트와 이미지 asset을 추출한 뒤 source markdown으로 변환됩니다. PPTX는 슬라이드별 이미지 asset을 만들고, PDF는 텍스트 추출이 약할 때 페이지 이미지를 asset으로 저장합니다.

### 업로드 후 처리

1. 원문 또는 변환된 markdown이 `sources/`에 저장됩니다.
2. 이미지와 페이지 asset은 `assets/`에 저장되고 source markdown에 링크됩니다.
3. 기존 wiki에서 관련 후보를 검색합니다.
4. 내부 LLM curator가 후보를 검토해 의미 있는 graph edge만 제안합니다.
5. 충분히 강한 edge만 `graph.json`과 `backlinks.json`에 저장됩니다.
6. `index.md`와 `log.md`가 갱신됩니다.
7. 품질 점검이 필요한 항목만 지식 점검에 남습니다.

내부 LLM 우선순위는 `local GGUF → 사내 API`입니다. 둘 다 실패해도 source 저장과 검색 인덱싱은 계속됩니다. 이 경우 의미 graph는 보수적으로 처리됩니다.

### Graph 연결 기준

Graph는 단어가 조금 겹친다는 이유만으로 연결하지 않습니다.

저장되는 edge는 다음 조건을 만족해야 합니다.

- 실제 같은 주제, 출처, 보완, 확장, 충돌 관계가 있어야 합니다.
- `reason`이 구체적이어야 합니다.
- confidence가 충분해야 합니다.
- `keyword overlap`, `tag overlap`, `term overlap` 같은 약한 이유는 자동 저장하지 않습니다.

예시:

| edge type | 의미 |
|---|---|
| `extends` | 같은 주제를 확장 |
| `supports` | 기존 지식의 근거 보강 |
| `contradicts` | 기존 지식과 충돌 |
| `related` | 함께 참고할 의미 관계 |

지식 목록 최상단의 `Index`, `Log` 버튼은 일반 문서와 분리된 관리 문서입니다.

## 8. 대화 지식화

대화 중 재사용 가능한 지식이 생기면 `대화 지식화`를 사용합니다.

좋은 후보:

- 개념 설명
- 수식 정리
- 업무 규칙
- 반복 절차
- 의사결정과 근거
- 검증된 코드 패턴

좋지 않은 후보:

- 단순 UI 취향
- 임시 디버깅 로그
- 아직 검증되지 않은 추측
- 이번 세션에서만 필요한 요청

대화 지식화는 현재 세션을 읽고 재사용 가능한 내용만 source markdown으로 정리합니다. source에는 `conversation_derived` 성격이 남으므로, 중요한 업무 지식은 가능하면 원문 문서나 공식 근거와 함께 보강하세요.

## 9. Workspace 파일

Workspace는 현재 세션에서 참고할 파일을 올리는 곳입니다.

| 동작 | 설명 |
|---|---|
| 파일 업로드 | 입력창 `파일` 버튼 또는 우측 Workspace의 `+` |
| 다중 업로드 | 여러 파일을 선택하면 모두 첨부됩니다. |
| 파일 클릭 | 미리보기 |
| 첨부 토글 | 현재 질문 context에 포함할지 선택 |
| 임시 인덱스 | 문서형 파일은 현재 세션 검색용으로 자동 인덱싱됩니다. |

Workspace는 Second Brain이 아닙니다. 현재 작업에 필요한 임시 자료는 Workspace에 두고, 장기 지식만 Second Brain으로 승격하세요.

## 10. 스킬

스킬은 LLM에게 붙이는 작업 매뉴얼입니다. 특정 업무를 할 때 매번 같은 절차, 검증 기준, 출력 형식을 반복해서 설명하지 않도록 만드는 기능입니다.

### 스킬 선택

| 방법 | 설명 |
|---|---|
| 좌측 `스킬` 탭 | 사용할 스킬을 클릭하면 활성화되고 입력창 위에 칩으로 표시됩니다. 다시 클릭하거나 칩의 `x`를 누르면 비활성화됩니다. |
| 입력창 `/` | 입력창 맨 앞에서 `/`를 입력하면 세션 명령과 스킬 자동완성이 뜹니다. `Tab`, `↑↓`, `Enter`로 선택할 수 있습니다. |
| `/skill-id 요청` | 특정 스킬을 이번 요청에만 명시 적용합니다. 예: `/backend-development 이 API 구조 리뷰해줘` |
| `Auto` | 질문 내용을 보고 관련 스킬을 자동 선택합니다. 명시 선택한 스킬이 있으면 함께 적용됩니다. |

`/status`, `/compact`, `/clear`는 세션 명령입니다. 스킬 ID와 같은 방식으로 자동완성에 뜨지만, 모델 답변을 부르는 것이 아니라 현재 세션 상태를 제어합니다.

### 스킬이 유용한 경우

- 특정 언어/프레임워크 방식으로 코드 작성
- 테스트 우선 개발
- 코드 리뷰
- API 설계
- 문서 작성
- 디버깅 절차 고정
- 특정 업무 로그 분석, 보고서 작성, 반복 검증 절차

### Skill Creator

새 스킬은 `스킬` 탭의 `+`, 입력창의 `Skill+`, 또는 `/skill-creator 만들고 싶은 스킬 설명`으로 만들 수 있습니다. `skill-creater`, `skillcreator`, `create-skill`, `new-skill`도 같은 별칭으로 처리됩니다.

Skill Creator는 다음 정보를 바탕으로 초안을 만듭니다.

- 사용자가 입력한 스킬 목적
- 현재 대화 내용
- 고정된 Second Brain 노드
- 자동 선택 설명, 태그, 도메인, 서비스, 필요한 도구
- `when_to_use`, `when_not_to_use`

생성된 스킬은 현재 계정의 사용자 스킬로 저장됩니다.

```text
code_assist_v1/user_data/<user>/skills/<skill_id>/SKILL.md
```

공용 스킬은 모든 계정에서 읽을 수 있는 기본 스킬입니다.

```text
code_assist_v1/skills/<skill_id>/SKILL.md
```

공용 스킬을 수정하면 원본을 직접 바꾸기보다 현재 계정의 사용자 사본으로 덮어쓰는 흐름이 안전합니다. 삭제는 사용자 스킬에 대해서만 사용하는 것을 권장합니다.

### 좋은 스킬 작성 기준

스킬은 길게 쓰는 것보다 “언제 켜지고, 무엇을 보고, 어떤 순서로, 어떻게 검증하는지”가 명확해야 합니다.

권장 구성:

```markdown
---
name: backend-api-review
description: Use when reviewing backend API design, routing, validation, error handling, and tests.
metadata:
  tags: [backend, api, review]
  when_to_use: Backend API 구조, validation, error response, test coverage를 검토할 때
  when_not_to_use: 단순 UI 문구 수정이나 일반 설명 요청
  tool_dependencies: [rg, shell]
  allow_implicit_invocation: true
---

# Backend API Review

## Workflow

1. 요청 범위와 관련 파일을 확인한다.
2. route, schema, validation, error handling, auth 경계를 본다.
3. 테스트 누락과 회귀 위험을 우선 찾는다.
4. 수정이 필요하면 가장 작은 변경으로 제안한다.
5. 실행 가능한 검증 명령을 제시한다.
```

`description`과 `when_to_use`는 Auto Skill이 스킬을 고르는 핵심 단서입니다. 너무 일반적인 설명보다 “어떤 요청에서 켜야 하는지”를 구체적으로 쓰세요.

## 11. Notion Workspace

Notion Workspace는 10012 포트에서 실행되는 개인 문서 작업공간입니다.

```text
http://localhost:10012
```

실행 모드에 따라 Notion AI 제공자가 고정됩니다. 이미 10012 포트에서 실행 중인 프로세스의 모드는 실행 중에 바뀌지 않으므로, 모드를 바꾸려면 해당 서비스를 다시 시작해야 합니다.

```powershell
# Hynix API Gemma 4 모드(기본)
python -m code_assist_v1.app_notion --port 10012

# Codex 모드
python -m code_assist_v1.app_notion --port 10012 --codex

# Second Brain Agent와 함께 실행
.\code_assist_v1\run_standalone.ps1
.\code_assist_v1\run_standalone.ps1 -Codex
```

### 기본 원칙

- Second Brain Agent 계정과 로그인 상태를 공유합니다.
- 각 계정은 자기 Notion DB를 가집니다.
- 페이지는 기본적으로 비공개입니다.
- 다른 사용자에게 공유한 페이지만 게스트 워크스페이스로 보입니다.
- 공유받은 페이지가 없으면 다른 계정의 게스트 워크스페이스는 표시되지 않습니다.

### 왼쪽 사이드바

| 영역 | 설명 |
|---|---|
| 최근 항목 | 최근 수정한 페이지 |
| 공유된 페이지 | 다른 사용자가 나에게 공유한 페이지 |
| 개인 페이지 | 내가 소유한 페이지 |
| 도움말 | Notion Workspace 사용법 |
| 휴지통 | 삭제한 페이지 복원 또는 영구 삭제 |
| Second Brain Agent | 10011 채팅 화면으로 이동 |

사이드바는 페이지가 많아져도 내부 스크롤로 접근할 수 있습니다.

워크스페이스 이름에 마우스를 올리면 현재 워크스페이스와 공유받은 게스트 워크스페이스가 표시됩니다. 업그레이드, 계정 추가, 새 워크스페이스 같은 외부 Notion 기능은 제공하지 않습니다.

### 페이지 만들기와 이동

| 동작 | 방법 |
|---|---|
| 새 페이지 | 왼쪽 상단 새 페이지 버튼 |
| 하위 페이지 추가 | 페이지 행 오른쪽 `+` |
| 페이지 메뉴 | 페이지 행 오른쪽 `...` 또는 상단 `...` |
| 위치 이동 | 페이지를 드래그해서 위/아래 또는 다른 페이지 안으로 drop |
| 최상위 이동 | 개인 페이지 목록의 빈 영역으로 drag/drop |
| 접기/펼치기 | 하위 페이지가 있는 페이지의 화살표 |

자기 자신의 하위 페이지 안으로 이동하는 순환 구조는 자동 차단됩니다.

### 페이지 메뉴

`...` 메뉴에서 다음 작업을 할 수 있습니다.

- 즐겨찾기 추가/제거
- 링크 복사
- 복제
- 이름 바꾸기
- 옮기기
- 휴지통으로 이동
- Second Brain으로 보내기
- 새 탭에서 열기
- 사이드 보기에서 열기

### 블록 편집

페이지 본문은 블록 단위로 편집합니다.

| 컨트롤 | 동작 |
|---|---|
| `+` | 새 블록 추가 |
| `::` | 전환, 색, 블록 링크 복사, 복제, 이동, 삭제, AI 질문 |
| `/` | 빈 블록에서 블록 메뉴 열기 |
| 하이퍼링크 | 텍스트 블록에서 URL pill 추가 |
| 이미지/파일 | hover 시 댓글, 캡션, 다운로드, AI 질문, 더보기 |

텍스트, 제목, 할 일, 글머리표/번호 목록, 토글, 인용, 콜아웃, 구분선, 코드, 수식, 이미지, 동영상, 오디오, 파일, 북마크, 임베드, 페이지 링크, 하위 페이지, 목차, 경로, 간단한 표, 버튼, 동기화 블록, 탭, 다단 컬럼을 만들 수 있습니다. 탭은 `/tabs`로 만들고 탭별 블록을 선택·추가·이름 변경·삭제할 수 있습니다. 굵게/기울임/밑줄/취소선/인라인 코드/링크/텍스트 색 같은 인라인 서식과 블록 다중 선택·복사·복제·이동·변환을 지원합니다.

### 데이터베이스

한 데이터베이스에 이름이 있는 보기를 여러 개 만들고 보기별 필터, 정렬, 속성 표시 여부를 따로 저장할 수 있습니다.

| 보기 | 주요 동작 |
|---|---|
| 테이블 | 셀 편집, 속성 추가/수정, 행을 페이지로 열기 |
| 보드 | 그룹별 카드, 카드 drag/drop, 다중 선택 그룹 |
| 타임라인 | 날짜 범위 표시, 날짜 속성 선택 |
| 캘린더 | 시작일/종료일 범위 표시, 날짜 속성 선택 |
| 목록·갤러리 | 압축 목록 또는 카드 배치 |
| 차트 | 속성별 집계와 막대 시각화 |
| 대시보드 | 최대 12개 위젯, 4열 배치, 보기/편집 모드 |
| 양식 | 데이터베이스 속성 기반 질문, 필수값 검증, 공개 응답 수집 |
| 피드 | 날짜 속성 기준 역순 카드 피드 |
| 지도 | 장소 속성 핀과 OpenStreetMap 검색 링크 |

속성은 제목, 텍스트, 숫자, 선택, 상태, 다중 선택, 날짜, 수식, 관계형, 롤업, 사용자, 파일, 체크박스, URL, 이메일, 전화번호, 생성/수정 시간과 사용자, 버튼, ID, 장소를 지원합니다. 관계형 속성은 대상 데이터베이스와 단일/다중 연결을 선택할 수 있고, 롤업은 원본 표시·개수·고유 개수·합계·평균·최솟값·최댓값·최초/최종 날짜·체크 비율을 계산합니다.

양식 제출은 새 데이터베이스 행과 하위 페이지를 원자적으로 생성합니다. 공개 페이지의 양식은 로그인하지 않은 사용자도 제출할 수 있지만 사용자·관계형 같은 비공개 속성은 공개 입력에서 제외됩니다.

### 댓글, 멘션과 알림

페이지 및 블록 댓글 스레드, 답글, 해결/재열기, 삭제, 필터, 반응을 지원합니다. `@` 멘션과 미리 알림은 Inbox에 모이며 날짜 속성 알림도 저장됩니다.

### Microsoft Outlook 연결

기본 연결 방식은 Microsoft Graph single-tenant OAuth입니다. 홈의 `Microsoft Outlook` 카드나 워크스페이스 메뉴의 `Outlook 연결`에서 회사 Microsoft 계정으로 로그인하면, 웹서버에 접속한 PC와 관계없이 로그인한 사용자 본인의 받은편지함을 연결할 수 있습니다. 기본값은 꺼짐이며 OAuth 동의가 끝난 뒤에만 동기화가 시작됩니다.

- 새 메일을 설정 주기(1·5·15·30분)마다 확인해 `Outlook Mail` 아래에 메일별 페이지를 만듭니다.
- 각 페이지에는 발신자·수신 시각·읽음 상태·첨부파일 메타데이터, 핵심 요약, 예상되는 행동 체크리스트, 우선순위와 회신 필요 예상을 저장합니다.
- 메일 본문은 읽기 전용으로 가져오고 첨부파일은 이름·크기·형식 같은 메타데이터만 저장합니다. Outlook의 읽음 상태를 바꾸거나 메일을 수정·발송하지 않습니다.
- 위임 권한은 `Mail.Read` 하나만 요청합니다. `Mail.ReadWrite`, `Mail.Send`, 애플리케이션 권한은 요청하지 않습니다.
- Graph delta cursor로 받은편지함의 변경분만 확인하며, 만료된 cursor는 최근 동기화 시각의 제한된 구간부터 자동 복구합니다.
- 매일 기본 오전 9시 이후 첫 실행 때 미열람 메일을 우선순위화해 브리핑 페이지와 Notion Inbox 알림을 만듭니다. Notion이 열려 있고 권한을 허용한 경우 브라우저 알림도 표시합니다.
- 오전 9시에 앱이 꺼져 있었다면 그날 앱이 다시 실행된 뒤 첫 스케줄 확인 때 누락된 브리핑을 만듭니다.
- `Outlook 연결 끄기`를 누르면 이후 동기화와 브리핑을 중단하고 서버에 암호화 저장된 사용자 토큰을 삭제합니다. 이미 만든 Notion 페이지와 Microsoft Tenant에 기록된 사용자 동의는 자동 삭제하지 않습니다.
- 같은 서버 PC에 설치된 Classic Outlook을 사용해야 할 때만 설정의 `이 서버의 Classic Outlook · 로컬`을 선택할 수 있습니다.

Outlook 요약의 AI 라우팅은 Notion AI와 같습니다. 기본 실행은 Hynix API의 `gemma4-31b`만 5초간 시도한 뒤 로컬 추출 요약으로 fallback하고, `--codex` 실행은 Codex만 사용한 뒤 로컬 추출 요약으로 fallback합니다.

#### Single-tenant Graph 서버 설정

1. 실행 환경에 Graph 패키지를 설치합니다.

   ```powershell
   & .\venv\Scripts\python.exe -m pip install -r .\code_assist_v1\requirements-outlook-graph.txt
   ```

2. Microsoft Entra 관리 센터의 **App registrations**에서 앱을 만들고 지원 계정 유형을 **이 조직 디렉터리의 계정만**으로 선택합니다.
3. **Authentication**에 플랫폼 `Web`과 정확한 callback URI를 등록합니다. 원격 서비스는 반드시 HTTPS를 사용합니다.

   ```text
   https://notion.example.com/api/notion/outlook/oauth/callback
   ```

4. **API permissions**에는 Microsoft Graph의 delegated `Mail.Read`만 남깁니다. Tenant의 사용자 동의 정책이 이를 허용하지 않으면 관리자가 대신 동의해야 합니다.
5. client secret을 만든 뒤 아래 값을 웹서버 프로세스 환경에 설정합니다. 실제 secret 파일과 토큰 암호화 키 파일은 서비스 계정만 읽을 수 있도록 OS ACL을 제한합니다. [`outlook_graph.env.example`](../outlook_graph.env.example)은 이름 확인용이며 자동으로 로드되지 않습니다.

   ```powershell
   $env:CODE_ASSIST_MS_TENANT_ID = "00000000-0000-0000-0000-000000000000"
   $env:CODE_ASSIST_MS_CLIENT_ID = "00000000-0000-0000-0000-000000000000"
   $env:CODE_ASSIST_MS_CLIENT_SECRET_FILE = "G:\secure\notion-graph-client-secret.txt"
   $env:CODE_ASSIST_MS_REDIRECT_URI = "https://notion.example.com/api/notion/outlook/oauth/callback"
   $env:CODE_ASSIST_MS_TOKEN_KEY_FILE = "G:\secure\notion-outlook-token.key"
   ```

   Fernet 키 파일은 최초 1회 다음과 같이 만들 수 있습니다.

   ```powershell
   & .\venv\Scripts\python.exe -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" | Set-Content -NoNewline G:\secure\notion-outlook-token.key
   ```

6. 환경을 적용해 서버를 재시작합니다. Redirect URI가 HTTPS이면 세션 쿠키도 자동으로 `Secure`가 됩니다. TLS 종료 reverse proxy를 통해 서비스하고 callback URI가 외부 주소와 정확히 일치해야 합니다.

서버는 Tenant별 authority를 사용하고 로그인 결과의 `tid`도 다시 검사합니다. OAuth state/PKCE flow와 MSAL token cache는 사용자별 Fernet 암호문으로 저장되며 API 상태 응답에는 client secret, access token, refresh token을 포함하지 않습니다. client secret 만료 전 교체와 암호화 키 백업은 운영자가 관리해야 합니다.

### 가져오기와 내보내기

- Markdown과 CSV를 가져올 수 있습니다. CSV는 열 형식을 추론해 데이터베이스와 행별 하위 페이지를 만듭니다.
- 페이지는 Markdown 또는 HTML로, 데이터베이스는 CSV로 내보낼 수 있습니다.
- 인쇄 메뉴는 브라우저의 PDF 저장 대화상자를 엽니다.

### 표시 설정

페이지별 글꼴, 작은 텍스트, 전체 너비, 잠금을 제공하며 워크스페이스의 라이트/다크 모드는 브라우저에 유지됩니다.

### 사이드 보기

페이지나 표/캘린더 항목은 오른쪽 사이드 보기로 열 수 있습니다. 사이드 보기에서도 제목과 블록을 수정할 수 있고, 필요하면 전체 페이지로 열 수 있습니다.

### 공유

공유 패널에서 내부 사용자에게 권한을 줄 수 있습니다.

| 권한 | 의미 |
|---|---|
| `view` | 읽기 |
| `comment` | 댓글 중심 |
| `edit` | 편집 |

웹 공개 링크를 켜면 읽기 전용 공개 페이지로 공유할 수 있습니다. 공개 링크는 편집 권한을 포함하지 않습니다. `템플릿으로 복제 허용`을 켜면 로그인 사용자가 공개 페이지 상단 `복제`를 눌러 모든 하위 페이지와 첨부파일을 자기 개인 영역에 복사한 뒤 편집할 수 있습니다.

### Chat에 추가

Notion 페이지를 특정 채팅 세션에 일시적으로 연결할 수 있습니다.

1. Notion 페이지 상단 `Chat에 추가`를 누릅니다.
2. 연결할 최근 세션을 선택합니다.
3. 이후 해당 세션의 답변에는 페이지 markdown이 context로 들어갑니다.

이 기능은 지식 저장이 아니라 현재 세션 context 연결입니다.

### Second Brain으로 보내기

Notion 페이지를 장기 지식으로 저장하려면 `Second Brain` 버튼을 사용합니다.

1. 페이지 상단 `Second Brain` 클릭
2. 업로드될 markdown preview 확인
3. 승인
4. source markdown으로 저장
5. index/log/graph 갱신

초안은 Notion에 두고, 확정된 내용만 Second Brain으로 보내는 흐름을 권장합니다.

### 기본 템플릿

처음 사용하는 계정은 `Second Brain 템플릿 만들기`로 다음 페이지를 만들 수 있습니다.

- `Second Brain Home`
- `Inbox`
- `Projects`
- `Areas`
- `Resources`
- `Archive`
- `Sources`
- `Chat Captures`
- `Decisions`
- `Review`

## 12. Notion AI

Notion Workspace의 AI 패널은 현재 페이지와 관련 Notion 페이지를 context로 사용해 질문에 답합니다.

예시:

- `이 페이지의 핵심 결정사항만 정리해줘.`
- `이 회의록에서 할 일을 뽑아줘.`
- `이 페이지를 Second Brain에 올리기 전에 정리할 점을 알려줘.`

Notion AI의 제공자는 프런트엔드에서 보낸 모델명으로 바꿀 수 없고 서버 실행 옵션으로만 결정됩니다.

- 기본 실행: `hynix_api`의 `gemma4-31b`만 호출합니다. 현재 API가 5초 안에 응답하지 않거나 실패하면 결정론적 로컬 추출 응답으로 fallback하며, 최근 실패 후 60초 동안은 지연 없이 즉시 fallback합니다.
- `--codex` 실행: Codex만 호출합니다. Codex가 실패하면 같은 결정론적 로컬 fallback을 사용하며 Hynix API로 우회하지 않습니다.

AI 패널 질문뿐 아니라 요약, 개선, 번역, 설명, 이어 쓰기, 액션 아이템 추출 등 블록 AI 작업에도 같은 라우팅 규칙이 적용됩니다.

## 13. 외부 도구에서 Markdown 만들어 가져오기

Google NotebookLM 같은 외부 도구에서 원문을 먼저 읽히고, Second Brain Agent에 올릴 Markdown을 만들 수 있습니다. 이때 목표는 “보기 좋은 요약”이 아니라, Second Brain의 source markdown, index, graph 판단에 잘 맞는 구조화 문서입니다.

NotebookLM이 `canonical source markdown` 같은 내부 용어를 완전히 이해할 필요는 없습니다. 중요한 것은 아래 양식처럼 원문 근거, 핵심 주장, 용어, 수식, 표/그림, 출처 단서를 분리해서 Markdown으로 출력하게 만드는 것입니다.

권장 원칙:

- 원문 근거를 최대한 유지합니다.
- 제목, 요약, 핵심 주장, 용어, 수식, 표, 출처를 분리합니다.
- 문서 간 연결 후보는 원문에서 명시된 선행 연구, 비교 대상, 참조 문서, 데이터셋, 표준, 방법론이 있을 때만 적습니다.
- 이미지나 표는 누락하지 말고 “원문 figure/table 참조”로 남깁니다.
- 추측과 원문 사실을 구분합니다.

NotebookLM에 사용할 수 있는 프롬프트:

```text
너는 원문 문서를 구조화된 Markdown 지식 문서로 정제하는 문서 정제자다.
이 Markdown은 Second Brain Agent에 업로드할 canonical source로 사용된다.

목표:
- 원문 문서의 핵심 지식, 근거, 용어, 수식, 표, 결정사항을 Second Brain Agent가 검색하고 graph 관계를 판단하기 좋은 Markdown으로 변환한다.
- 단순 요약이 아니라, 나중에 다른 문서와 연결될 수 있는 source markdown을 만든다.
- 원문에 없는 사실은 추가하지 않는다.

출력 형식:

# <문서의 명확한 제목>

---
source_type: external_markdown
original_title: <원문 제목>
original_author_or_org: <알 수 있으면 작성, 모르면 unknown>
original_date: <알 수 있으면 YYYY-MM-DD, 모르면 unknown>
created_for: Second Brain Agent import
verification_status: source_derived
---

## 1. Executive Summary
- 문서 전체를 5~8개 bullet로 요약한다.
- 각 bullet은 원문에서 확인 가능한 내용만 쓴다.

## 2. Key Concepts
| Concept | Definition | Source Evidence |
|---|---|---|
| 핵심 용어 | 원문 기반 정의 | 페이지/섹션/문단 단서 |

## 3. Core Claims
| Claim | Evidence | Caveat |
|---|---|---|
| 주장 | 원문 근거 | 조건, 한계, 예외 |

## 4. Methods / Process / Architecture
- 절차, 알고리즘, 시스템 구조, workflow가 있으면 단계별로 정리한다.
- 입력, 처리, 출력, 의존성을 분리한다.

## 5. Equations and Formal Definitions
- 수식은 Unicode/일반 표기로 읽기 쉽게 작성한다.
- 각 변수의 의미와 단위를 바로 아래에 설명한다.
- 원문 LaTeX가 중요하면 별도 줄에 `Original notation:`으로 보존한다.

예:
αᵢⱼ =
softmaxⱼ(eᵢⱼ)
=
exp(eᵢⱼ) / Σₖ∈Nᵢ exp(eᵢₖ)

Variables:
- αᵢⱼ: node i가 neighbor j에 주는 attention weight
- eᵢⱼ: normalization 전 attention score
- Nᵢ: node i의 neighbor set

## 6. Tables, Figures, and Assets
- 원문 figure/table을 빠뜨리지 말고 설명한다.
- 이미지를 직접 첨부할 수 없으면 `Figure <번호>: <내용과 의미>` 형식으로 남긴다.
- 표는 가능한 Markdown table로 재구성한다.

## 7. Decisions / Rules / Reusable Knowledge
- 업무 규칙, 설계 결정, 반복 사용 가능한 패턴을 bullet로 정리한다.
- 임시 의견과 확정 사실을 구분한다.

## 8. Related Topics and Graph Hints
다음은 Second Brain Agent가 graph 후보를 판단할 때 참고할 힌트다.
단일 문서만 보고 알 수 있는 범위에서만 작성한다.
원문에 선행 연구, 비교 대상, 참조 문서, 데이터셋, 표준, 방법론, 충돌 주장 등이 명시된 경우만 적는다.
확실하지 않으면 `No reliable graph hint from this single source.`라고 쓴다.

| Related Topic | Possible Relation | Reason |
|---|---|---|
| 관련 주제/문서명 | extends/supports/contradicts/related 중 하나 | 원문 기반 이유 |

## 9. Open Questions
- 원문만으로 확인되지 않는 부분
- 추가 문서가 필요한 부분
- 모호한 용어

## 10. Source Trace
- 가능한 경우 페이지, 섹션, 슬라이드 번호, 표 번호, figure 번호를 남긴다.
- 정확한 페이지 번호를 모르면 원문 내 heading이나 주변 문구를 남긴다.

작성 규칙:
- 원문에 없는 내용을 만들지 마라.
- 광고성 표현이나 중복 문장은 제거하라.
- 너무 짧은 bullet만 나열하지 말고, 재사용 가능한 지식 단위로 정리하라.
- 숫자, 날짜, 모델명, 논문명, 변수명은 원문과 다르게 바꾸지 마라.
- 한국어로 작성하되, 고유명사와 기술 용어는 원문 표기를 함께 유지하라.
- 최종 출력은 Markdown 본문만 제공하라.
```

이렇게 만든 `.md` 파일을 Second Brain Agent의 지식 업로드에 올리면, Agent는 다시 source 저장, index/log 갱신, graph 후보 검색, 내부 LLM curator 판단을 수행합니다. 외부 도구가 만든 관련 주제 표는 확정 graph가 아니라 graph 판단을 돕는 힌트로만 취급됩니다.

단일 문서만 NotebookLM에 넣는 경우, 기존 Second Brain 전체 지식과의 관계를 NotebookLM이 알 수 없습니다. 따라서 Related Topics는 “이 문서 내부에서 명시적으로 드러난 관계”만 적고, 나머지 실제 graph 연결은 Second Brain Agent의 local GGUF 또는 사내 API curator가 기존 source들과 비교해 다시 판단합니다.

## 14. 권장 작업 흐름

### 문서를 읽고 질문하기

1. Workspace에 문서를 업로드합니다.
2. 필요한 경우 Second Brain 토글을 켭니다.
3. 질문합니다.
4. 답변에서 재사용할 내용은 대화 지식화 또는 Notion으로 정리합니다.

### 지식을 만들기

1. PDF/PPTX/DOCX/Markdown을 `지식` 탭에 업로드합니다.
2. job 진행 상태를 확인합니다.
3. Index/Log를 확인합니다.
4. 지식 점검 항목이 있으면 처리합니다.
5. Second Brain ON으로 질문해 반영 여부를 확인합니다.

### Notion에서 정리 후 지식화

1. Notion에 초안 페이지를 작성합니다.
2. 이미지, 파일, 표, 캘린더, 하위 페이지를 정리합니다.
3. 필요한 사용자에게 공유합니다.
4. 특정 세션에만 쓸 때는 `Chat에 추가`를 사용합니다.
5. 장기 지식으로 확정되면 `Second Brain`으로 보냅니다.

## 15. 계정별 적용

모든 UI 기능과 backend 처리는 계정 공통 코드로 동작합니다. admin에서 검증한 Notion 메뉴, 하위 페이지, drag/drop, 공유, Second Brain publish, 문서 업로드, 세션 명령은 다른 계정에서도 같은 방식으로 적용됩니다.

다만 데이터는 계정별로 분리됩니다.

- admin의 페이지는 user01에게 자동으로 보이지 않습니다.
- user01이 처음 접속했을 때 페이지가 없다면 정상입니다.
- user01은 자기 계정에서 `Second Brain 템플릿 만들기`를 눌러 자기 Notion 페이지를 생성해야 합니다.
- admin이 user01에게 페이지를 공유하면 user01의 `공유된 페이지`와 workspace popover에 게스트 워크스페이스가 표시됩니다.

## 16. 문제 해결

| 증상 | 확인할 것 |
|---|---|
| 페이지 목록이 비어 보임 | 브라우저 캐시가 오래된 JS를 잡고 있을 수 있습니다. `Ctrl + F5`로 강력 새로고침하세요. |
| Notion 페이지가 실제로 없는 계정 | 처음 사용하는 계정은 `Second Brain 템플릿 만들기`를 눌러 기본 페이지를 만듭니다. |
| 공유 워크스페이스가 보이지 않음 | 실제 공유받은 페이지가 있을 때만 게스트 워크스페이스가 표시됩니다. |
| 문서 업로드가 멈춘 것처럼 보임 | 지식 탭의 job 진행 상태를 확인하세요. PDF/PPTX/DOCX는 추출과 변환에 시간이 걸립니다. |
| GGUF 답변이 끝에서 잘림 | 출력 한도입니다. 질문을 나누거나 `/compact` 후 다시 요청하세요. |
| graph가 이상하게 연결됨 | `reason`과 `confidence`를 확인하세요. 약한 keyword overlap 관계는 자동 저장하지 않는 것이 원칙입니다. |
| Second Brain 토글을 켰는데 답변에 반영되지 않음 | `sources/`, `index.md`, 검색어, 최근 log를 확인하세요. 업로드 직후에도 같은 세션에서 검색되어야 합니다. |
| 장기기억이 반영되지 않음 | 입력창의 `기억` 버튼이 켜져 있는지 확인하세요. 장기기억은 공식 지식보다 낮은 우선순위입니다. |
| 임시 자료가 장기 지식처럼 답변됨 | Workspace 파일인지 Second Brain source인지 확인하세요. 현재 세션용 자료는 Workspace에 두고, 공식 지식만 Second Brain에 올리세요. |

## 17. 단축키

| 키 | 동작 |
|---|---|
| `Enter` | 메시지 전송 |
| `Shift + Enter` | 줄바꿈 |
| `Esc` | 스트리밍 중단 |
| `Ctrl + K` | Notion 검색 |
| `Ctrl/Cmd + F` | 현재 Notion 페이지 안에서 찾기 |
| `Ctrl + S` | Notion 페이지 수동 저장 |
| `Ctrl/Cmd + N` | 새 Notion 페이지 |
| `Ctrl/Cmd + [` / `]` | 페이지 뒤로 / 앞으로 |
| `Ctrl/Cmd + Shift + R` | 현재 페이지 이름 바꾸기 |
| `Ctrl/Cmd + D` | 현재 페이지 또는 선택 블록 복제 |
| `Ctrl/Cmd + Shift + L` | 라이트/다크 모드 전환 |
| `Ctrl/Cmd + \\` | 사이드바 접기/펼치기 |
| `Tab` / `Shift + Tab` | 블록 들여쓰기 / 내어쓰기 |
| 사이드 보기에서 `Ctrl + K` / `Ctrl + J` | 이전 / 다음 데이터베이스 페이지 |
| `Ctrl + F5` | 브라우저 강력 새로고침 |
