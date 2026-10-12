# Notion 사이드바·12초 음성·조직 Wiki 선택 후속 변경

2026-09-13. 기존 `NOTION_AI_HOME_20260913.md` 이후의 사용자 요청에 대한
Notion consumer 변경이며 SBE rollout, schema, AGENTS 또는 완료 receipt 변경은 아니다.

## 변경

1. `sidebarChatNavBtn`은 HTML부터 hidden으로 시작한다. 현재 workspace 정책이
   확인된 뒤 허용된 경우에만 표시하므로 legacy 홈 옆 채팅 아이콘이 새로고침 때
   잠깐 나타났다가 사라지지 않는다. canonical 채팅 아이콘 정책 자체는 유지한다.
2. 중복된 상단 `AI 채팅` 행과 이벤트 연결을 제거했다. 하단 `새 채팅`, Ctrl+O,
   `/ai`, 기존 문서 AI는 유지한다.
3. 일반 홈의 맨 아래 `Second Brain 연결` 카드와 그 안의 템플릿 생성 버튼을
   제거했다. 기존 사용자 페이지·템플릿·Second Brain 자료는 삭제하지 않는다.
4. local Whisper 음성 구간은 기본 **12초**, 선택값은 **12초 / 10초**다.
   과거 저장값 2/4/8초 및 유효하지 않은 값은 다음 녹음에서 12초로 해석한다.
   로드만으로 저장 내용을 수정하거나 기존 전사·번역을 다시 가공하지 않는다.
   녹음 중에는 간격을 변경할 수 없으며, 완료/중단 시 남은 짧은 구간도 처리한다.
   자동 번역 체크 순서와는 독립적이다. 12초는 오디오 수집 길이이지 번역 결과가
   정확히 12초마다 도착한다는 보장이 아니다. 전사/모델 지연은 추가된다.
5. AI 홈의 Wiki 선택을 개인 / 파트 / 팀 / 담당 그룹과 조직 경로로 표시한다.
   대화당 선택된 Wiki/view는 정확히 하나이며 바꾸면 새 대화를 시작한다.

## Wiki 권한 및 연결 경계

새 `notion_ai_chat_knowledge.ChatKnowledgeReader`는 AI 채팅에만 적용한다.
AI 노트의 host-only 비교 수집은 기존 개인 Wiki 전용 `KnowledgeReader`를 유지한다.

- 기존 `KnowledgeBridge.organization_views/view/search_view/get_view` 읽기 API를 사용한다.
- 현재 계정, 현재 소속, 실제 선택한 view의 서버 소유 CorpusClosure를 재검증한다.
- personal 소유자 검사, Part/higher read feature flag를 우회하지 않는다.
- 팀/담당에서 상속된 문서는 실제 physical source-space ID를 보존한다.
- 명시적 페이지 최대 4개와 Wiki 검색 결과의 합계는 UTF-8 **96 KiB** 이하다.
  canonical ranked documents에 어댑터가 추가적인 문서 수 상한을 두지 않는다.
- 응답 전후 및 과거 대화 재열람/다운로드 시 revision/source/closure를 재검증한다.
  closure hash는 암호화된 호스트 대화 snapshot에만 저장하고 공개 응답에서 제거한다.
- 서비스가 없는 canonical 호스트는 legacy 파일 검색으로 넘어가지 않는다.
- core 미연결 legacy 호스트는 본인의 `내 Second Brain · 파일 Wiki`만 표시하고
  조직 Wiki가 아직 연결되지 않았다는 설명을 표시한다. 비권한 조직을 가짜 메뉴로
  노출하거나 disabled 메뉴로 나열하지 않는다.

### 사내 호스트 연결 handoff

현재 Notion launcher는 legacy 개인 파일 Wiki 연결 방식이다. 검사 시 10421 listener는
없었다. 개발 중인 다른 호스트의 catalog/계정/플래그를 가져오거나 실행하지 않았다.

조직 Wiki 실제 사용에는 Notion 호스트가 승인된 공용 Knowledge Core catalog와
canonical service registry에 이미 연결되어 있어야 한다. 필요한 읽기 의존성은
source_artifacts, personal_wikis, retrieval, management_reads이며, 읽기 플래그는
core/personal/multi_wiki, Part용 part_reads, 팀·담당용 higher_spaces다.
**이 문서는 플래그 활성화 승인이 아니다.** 기존 SBE packet 및 rollout dependency
gate를 통과한 호스트 구성 담당자가 연결해야 한다. 경로를 브라우저에서 임의 입력하거나
10421 개발 DB를 복사하는 방식으로 우회하지 않는다. 연결 후에는 현재 로그인 계정이
접근 가능한 view가 자동으로 목록에 나온다. 쓰기/게시 권한은 이 기능에 필요하지 않다.

## 검증 기록

- Python AI 홈 + 새 Wiki reader: **43 passed**.
- Python AI 노트 + 번역 + 비공개 비교 회귀: **86 passed**.
- Node: AI notes, meeting audio, notes translation 테스트 모두 통과.
- `node --check`: 메인 Notion, AI 홈 JS 문법 검사 통과.
- 실제 canonical 서비스를 격리된 임시 catalog에 연결하여 개인/파트/팀/담당 목록,
  팀 하위 문서 검색, physical source 보존, 무관한 Part/Team 거부를 확인했다.
  가짜 모델/UI 응답 테스트와 구분되는 실제 canonical 읽기 검증이다.
- 첫 브라우저 묶음: 25 passed / 2 failed. 실패 중 하나는 초기 HTML 검사 도구의
  중복 route 해제였고, 다른 하나는 12초 구간 3개를 25초 안에 기다리던 테스트였다.
  첫 문제는 앱 스크립트 차단/정상 reload로 분리해 수정 후 단독 통과했다.
  두 번째는 3 × 12초를 수용하는 timeout으로 교정했다. 후속 결과는 아래에 기록한다.
- 교정 후 초기 HTML/홈 정리, 조직 Wiki 선택, 느린 번역 중 전사 지속 시나리오
  **3 passed (71.63초)**. 기존 25 passed와 중복을 제외한 총 **27개 브라우저
  시나리오 통과**. 12초 구간 첫 3초에는 전사 요청이 없고, 00:12 타임스탬프,
  10초 선택 저장/복원, 한↔일 양방향 순차 처리도 검증했다.
- 합성 UI 캡처 위치: `.tmp/notion-ui-audio-wiki-20260913/`.
  `home-cleaned-sidebar.png`, `ai-chat-organization-wiki.png`를 직접 열어 확인했다.
- 실제 사용자 페이지/음성/Wiki를 테스트 입력으로 사용하거나 모델에 전송하지 않았다.
- AGENTS SHA-256 유지:
  `629fe4f841b6aa3ba4ea7b3dd987540e5a3bbcc0bcaf06c2d15f5367e881b763`.

## 실행 중 서버 적용 상태

수정 전 확인된 listener: 10011 PID 30616, 10322 PID 4864, 10323 PID 24816.
사용자에게 녹음·전사·번역 중이 아닌지 재시작 확인을 요청했다. 확인 전에는
실행 중 서비스를 종료하지 않는다. 정적 화면/12초 변경은 저장 후 새로고침하면 읽히지만,
새 Wiki reader의 Python 코드 적용은 Notion 프로세스 재시작이 필요하다.
기존 combined launcher로 다시 띄울 경우 10322/10323만 대상으로 하며 10011 및
다른 SBE 개발 서버는 변경하지 않는다. 기존 `--comparisons --codex` 조건을 보존한다.
본 변경 작성/검증 종료 시점까지 재시작 답변을 받지 않아 실제 서버는 그대로 유지했다.

### 사용자 승인 후 적용 — 2026-09-13 21:08 KST

사용자의 명시적 `재시작해` 요청 후 이전 launcher PID 17060의 경로·인자 및
10322/10323 하위 프로세스를 다시 확인하고 해당 tree만 종료했다.
두 포트가 비워진 것을 확인한 뒤 기존 launcher를 `--comparisons --codex`로
숨김 창에서 시작했다. 오디오 인증 토큰은 launcher 메모리/자식 환경에서만 새로 발급했다.

- 새 launcher PID: 26680.
- 10322 Notion listener PID: 25100, `/healthz` HTTP 200.
- 10323 Whisper listener PID: 39592, loopback 유지. launcher가 인증된 준비 상태를
  확인한 뒤 Notion을 시작했고 `Local STT connected`를 기록했다.
  별도 무인증 `/healthz`는 의도된 HTTP 401로 접근 보호를 확인했다.
- 10011 PID 30616 유지, `/healthz` HTTP 200. 재시작하거나 설정을 바꾸지 않았다.
- 로그: `.tmp/notion-ui-audio-wiki-runtime-20260913-210819/`.
- 사용자 페이지/전사/번역 내용을 수정·삭제하거나 테스트 음성을 생성하지 않았다.
  열린 페이지는 사용자가 새로고침하면 최신 UI/12초 기본 설정을 읽는다.
- 위의 조직 Wiki 호스트 연결 전제는 그대로다. 재시작이 SBE 조직 읽기 rollout을
  활성화한 것은 아니다.

전체 Internal Collaborative Notion / SBE / Z0 goal 완료를 선언하지 않는다.
