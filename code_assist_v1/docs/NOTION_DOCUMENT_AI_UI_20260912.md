# 문서 AI 채팅·선택 편집 UI — 2026-09-12

## 요청 및 범위

실제 Notion AI 기능 조사 후 사용자가 문서 AI 개발/UI 추가와 왼쪽 최하단
`Second Brain 템플릿 만들기` 항목 제거를 요청했다. 이전에 권장한 첫 단계인
현재 페이지 요약·질문·초안·할 일 추출, 선택 문장 수정·번역을 구현했다.
Second Brain 지식 검색, 다른 페이지 검색, DB Autofill, 자율 문서 조작은 이번 범위가 아니다.
이 문서는 전체 Internal Collaborative Notion/Z0 완료 또는 SBE 동결/게이트 변경 증거가 아니다.

## 사용자 UI

- 왼쪽 내비게이션에 `✦ AI 채팅` 추가. 페이지 탐색 목록은 유지하고 오른쪽 문서 AI 패널을 연다.
- `페이지 요약`, `할 일 추출`, `초안 작성`, 자유 질문 및 Ctrl+Enter 요청.
- 글을 드래그 선택한 뒤 인라인 `✦ AI`, 또는 블록 메뉴 `AI로 다듬기`로 선택 편집 진입.
  글 다듬기/맞춤법/짧게/길게/업무 문체/번역 제공. 번역 언어는 한국어·영어·일본어
  제안과 자유 입력을 함께 제공하며 세 언어로 제한하지 않는다.
- 결과를 먼저 보여주고 사용자가 `선택한 글 바꾸기`, `아래에 삽입`, `페이지 끝에 삽입`을
  눌렀을 때만 기존 편집 저장 계약으로 반영한다. 선택 밖 글/서식과 블록 ID를 보존한다.
- 다시 시도/버리기/대화 비우기. 대화는 이 탭 메모리에만 최대 3쌍을 유지하고 페이지 이동 시 비운다.
  진행 중 비우기는 늦은 응답을 무시하며, 이미 서버에 전달된 모델 요청 자체를 중단한다는 보장은 없다.
- AI 출력은 textContent로 렌더링해 HTML/script를 실행하지 않는다.
- 왼쪽 footer의 템플릿 버튼과 이벤트만 제거. 기존 템플릿/페이지 데이터와 홈의 별도 템플릿 기능은 삭제하지 않는다.

## 모델·권한·데이터 계약

- 새 경로 `/api/notion/document-ai/capabilities`, `/api/notion/document-ai/ask`.
  레거시 `/api/notion/ai/ask`와 해당 internal-product 차단 정책은 유지한다.
- `notion_meeting_providers.text_models()` 재사용: Hynix Gemma4 → Qwen3.6 →
  명시적 Codex 실행 argument + CLI 연결/인증이 모두 있을 때만 Codex fallback.
  직접 선택은 현재 노출된 모델 하나만 호출한다. 조건 불충족 시 Codex 선택 항목 자체가 없다.
  공유 SBE routing/bridge와 전역 모델 설정은 변경하지 않는다.
- Codex 요청은 호출별 OS Temp 빈 cwd/read-only/ephemeral/low override를 사용하고
  원본 cfg는 수정하지 않는다. 문서 내용은 명령이 아닌 데이터로 전달하며 도구 실행을 지시하지 않는다.
- 서버가 인증 사용자와 page ID로 현재 저장된 제목/블록 본문만 읽는다.
  다른 페이지, 임의 props, 첨부파일 본문, 호스트 비교 DB는 컨텍스트에 포함하지 않는다.
- canonical IAM 활성 시 기존 canonical 권한 경로만 사용하고 workspace ID를 검사한다.
  비활성 환경은 기존 legacy page read 권한을 사용한다. canonical 실패에서 legacy로 우회하지 않는다.
- 읽기 권한 사용자는 읽기용 요약/질문 가능. UI 삽입/교체는 잠금 및 기존 편집 권한을 재검사한다.
- 모델 호출 전/후마다 접근 권한과 서버 본문 fingerprint 재검사. 권한 회수/원문 변경이면
  응답을 버리며 모델 fallback으로 오인하지 않는다. 클라이언트도 페이지·블록 ID·본문·props
  스냅샷과 요청 세대를 검사해 늦은 응답 및 변경된 원문에 대한 적용을 차단한다.
- JSON 문서 컨텍스트 80,000자, 선택 글 16,000자, 질문 4,000자, 답변 16,000자 한도.
  긴 페이지는 조용히 자르지 않고 필요한 부분을 선택하라고 안내한다.
  DB 내부 행/자식 페이지/첨부 본문은 자동 확장하지 않는다.
- 동시 모델 요청 슬롯 2개. 실패 시 가짜 추출 요약을 성공으로 반환하지 않고 재시도 가능한 오류를 표시한다.
- 기존 회의 노트의 일반/Second Brain 연동 비교 요약 두 개는 계속 호스트 전용이다.
  문서 AI 결과는 별도의 사용자 요청 결과이며 기존 비교 결과를 공개하는 경로가 아니다.

## 변경 파일

- 추가: `routes_notion_document_ai.py`, `notion_static/notion_document_ai.js`,
  `notion_static/notion_document_ai.css`, 서버 `test_notion_document_ai.py`, 브라우저 `test_notion_document_ai_ui.py`.
- 수정: `app_notion.py` 새 route 등록, `notion_static/index.html` 진입점/패널/footer/cache,
  `notion_static/notion.js` 편집기 adapter·인라인/블록 메뉴·탐색 lifecycle 연결.
- AGENTS.md SHA-256: `629fe4f841b6aa3ba4ea7b3dd987540e5a3bbcc0bcaf06c2d15f5367e881b763` 변경 없음.
  SBE hot contract, DB/schema, 공유 model routing/bridge, 기존 immutable receipt는 수정하지 않았다.

## 검증

- 새 서버 테스트 최초 27 passed / 15.99초.
- 문서 AI + AI 노트 + 번역 서버 회귀: **94 passed / 60.27초**.
- 문서 AI 브라우저 최초 5 pass/1 테스트 mock callback 인자 오류; lambda wrapper로 fixture만 교정.
- 문서 AI 6개 + 수식/오른쪽 인덱스 2개 + 요약 직접 수정 1개 + 번역 즉시 표시/저장 실패 2개:
  **11 passed / 128.29초**.
- 최종 연결 오류 처리/캐시 키/첨부 메뉴 질문 연결 후 새 서버+브라우저 통합 재검증:
  **33 passed / 80.19초**. 통합 수집에서 발견한 테스트 모듈명 충돌을 브라우저 파일의
  고유 이름 `test_notion_document_ai_ui.py`로 교정했다. 두 JS 파일 `node --check` 통과.
- 최초/회귀 run의 OS Temp 디렉터리 `62lmoq3j`, `b1fiyd1w`, `xesmp8_g`, `fr6l9qkf`
  (`code-assist-pytest-` 접두사 포함)는 테스트 종료 후 부재를 확인했다.
- 합성 문서와 격리된 OS Temp fixture만 사용. 실제 사용자 페이지/Notion DB/오디오를 테스트에 사용하지 않았다.
  모델은 테스트 더블이며 사내 Hynix 실망/실제 Codex 추론 품질·지연 검증을 의미하지 않는다.
- `G:/AI_Agent/DEMOS_CODEX/.tmp/notion-document-ai-20260913/document-ai-summary.png`를 직접 열어
  왼쪽 AI 버튼, 오른쪽 패널, 결과 액션과 footer 제거를 시각 검토했다. 경로의 20260913은 아티팩트
  생성 때 선택한 식별자이며 실제 검증일은 2026-09-12이다. 이미지에는 합성 테스트 내용만 있다.

## 실행 반영

현재 10322 Notion / 10323 local STT는 이전 실행기 PID 26312 트리로 가동 중이다.
10011 Second Brain은 PID 6524로 별도 가동 중이며 재시작 대상이 아니다.
사용자에게 녹음·전사·번역 진행 여부를 질문했다. 응답 확인 전에는 서버를 재시작하거나
사용자의 열린 페이지를 새로고침하지 않았다. 새 Python route는 서버 재시작 후 사용 가능하다.
프런트 cache key는 `20260912-document-ai1`. 실행 반영을 완료하면 이 절을 갱신한다.
