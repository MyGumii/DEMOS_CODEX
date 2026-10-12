# 자동 번역 표시 지연 교정 및 요약 직접 편집

2026-09-12 사용자 요청: 자동 번역 체크를 해제해야 누적 결과가 표시되는 문제를
수정하고, 생성된 요약도 사용자가 직접 편집할 수 있게 한다.

## 원인과 수정

- 번역 controller는 모델 결과를 검증하고 편집본에 넣은 뒤, 공유 페이지 저장 queue가
  끝나야 화면을 갱신했다. 녹음/자동 저장 중 저장이 지연되면 결과가 이미 도착했어도
  표시가 늦어졌다. 체크 해제는 번역을 새로 요청한 것이 아니라 stop의 UI 갱신으로
  기존 편집본을 드러낸 것이었다. 지연된 persist promise로 이 순서를 재현했다.
- 이제 검증된 결과를 편집본에 넣자마자 표시하고 `화면에 반영됨 · 저장 중`으로
  안내한다. 저장 실패 시 결과를 화면에 남기고 실패를 알리며 자동 번역을 중지한다.
  재시도는 남아 있는 편집본부터 저장하므로 같은 구간을 모델에 다시 보내지 않는다.
- 저장 대기 중 중지한 경우 늦은 저장 성공 메시지가 중지 상태를 덮어쓰지 않는다.
  모델 응답의 구간·언어 쌍·권한·설정 재검증과 취소된 응답 폐기는 유지한다.

## 요약 편집 계약

- 편집 권한이 있고 기존 `result.summary`가 문자열이면 요약 탭에 직접 편집 가능한
  textarea를 제공한다. 별도 팝업 없이 수정하며 12,000자 한도와 HTML escaping을 적용한다.
- 기존 페이지 저장 adapter를 사용한다. 입력 후 약 1초 유휴, 포커스 이탈 또는
  Ctrl+S에서 저장 상태를 확인한다. 실패 시 새로고침하지 말고 Ctrl+S로 재시도하도록 안내한다.
- `result.summary`만 교체하고 원문·메모·결정 사항·할 일·AI 생성 근거 정보는 보존한다.
  `summary_manually_edited`, `summary_edited_at`으로 수동 편집을 기록한다.
  Markdown 본문과 요약 다운로드에 수정 내용이 반영된다.
- 요약을 빈 문자열로 수정해 저장해도 편집 칸과 결정 사항·할 일은 유지된다.
  기존 `요약 비우기`는 요약 결과와 수동 편집 표식을 함께 제거한다.
- 직접 수정한 요약은 회의 중/종료 시 자동 요약이 덮어쓰지 않는다. 사용자가 명시적으로
  `요약 다시 생성`을 눌렀을 때만 교체 확인을 받으며 취소하면 기존 수정본을 유지한다.
  승인 후 정상 AI 결과를 반영하면 수동 편집 표식을 해제한다.
- AI 요청 당시 요약과 응답 시 요약이 다르면 늦은 결과를 폐기한다. AI 처리 중에는
  편집을 잠그고, 다른 보기에서 변경된 요약도 덮어쓰지 않는다.
- 읽기 전용 페이지에는 편집 칸이 없으며 이벤트 처리 시에도 권한을 재확인한다.
  Second Brain 호스트 전용 비교 수집 모드에는 요약을 새로 노출하지 않는다.
- 원문은 녹음/미전사 음성이 있는 동안 계속 보호한다. 비우기 역시 진행 중 작업이
  끝나야 가능하다. 이 요청은 진행 중 녹음을 강제로 중단하거나 기존 내용을 삭제하지 않는다.

## 변경 범위와 적용

- 제품: `notion_static/notion_ai_notes.js`, `notion_static/notion_notes_translation.js`,
  `notion_static/index.html`의 두 JS 캐시 키 `20260912-edit1`.
- 회귀: 두 Node 테스트, `tests/browser/test_notion_ai_notes_edit.py`,
  `tests/browser/test_notion_notes_translation.py`.
- 정적 파일 변경이므로 서버 재시작은 필요 없다. 녹음 중이면 사용자가 회의를 종료하고
  남은 전사/저장이 끝난 뒤 페이지를 새로고침해야 새 스크립트가 적용된다.
- 실제 사용자 페이지/DB/녹음 상태는 변경하지 않았다. 10011, 10322, 10323의
  기존 서버는 계속 실행 중임을 포트 조회로 확인했다.
- AGENTS.md, notion.js, SBE hot contract, 공유 routing/bridge, 모델 우선순위,
  기존 immutable receipt는 변경하지 않는다. 전체 제품/Z0 완료 선언과 무관하다.

## 검증

- Node: AI notes core/편집/보존/권한 표시, translation controller의 저장 대기 중 표시,
  저장 실패/재시도/중지, 기존 구간/취소/통역 설정, meeting audio lifecycle 모두 통과.
  제품 JS 두 파일의 `node --check` 통과.
- 서버: `tests/test_notion_ai_notes.py tests/test_notion_notes_translation.py`
  **66 passed / 64.46초**.
- 첫 신규 브라우저 검증: 5 passed, 1 failed / 141.75초. 실패는 전사 textarea의
  현재 value 대신 초기 textContent를 확인한 테스트 오류였다. 실제 합성 전사는
  누적되었으며 테스트를 현재 편집본 상태 확인으로 교정했다. 후속 결과는 아래에 기록한다.
- 모든 브라우저 검증은 OS Temp의 격리 DB/합성 문장/모의 모델/합성 오실레이터를 사용한다.
  실제 사용자의 음성이나 문서를 모델에 전송하지 않는다. 이 증거는 실제 STT/LLM의
  언어 품질 또는 응답 지연을 보증하지 않는다.

- 최종 브라우저 묶음: 편집/일반 AI 노트/비우기/호스트 비교/통역의 다섯 파일을 실행하여
  **24 passed, 1 error / 440.82초**. error는 비우기 잠금 테스트의 본문 실행 전,
  로그인 후 준비 상태를 기다리는 fixture에서 Playwright driver 연결이 끊긴 경우였다
  (`Connection closed while reading from the driver`). 제품이나 fixture 코드를 변경하지
  않고 해당 항목을 단독 재실행했다.

- 단독 재실행: **1 passed / 16.19초**. 최종 제품 코드에서 브라우저 **25개 고유
  시나리오**를 검증했다. 앞선 신규 테스트/재실행 횟수를 추가 시나리오로 중복 합산하지 않는다.
- 이번 실행의 OS Temp 루트 `code-assist-pytest-4q46h25n`, `code-assist-pytest-wkpe3lvm`,
  `code-assist-pytest-qtrd5kis`, `code-assist-pytest-w2yo_tgv`는 fixture 정리 후 부재를 확인했다.

제품 파일 SHA-256 (검증 대상):

```text
C500E48C554A79E817AC4DFD735E114C36A2A9EB7ACEA7CB65AEBDE67B1F2C7A  notion_static/notion_ai_notes.js
21DCFA63548A158D695A8DED151A70B6F0065723170990329E42049707369B33  notion_static/notion_notes_translation.js
6C7CC0687D99DF7546858B94D87D578998827A9F76C7E147FA79F78667BD346B  notion_static/index.html
629FE4F841B6AA3BA4EA7B3DD987540E5A3BBCC0BCAF06C2D15F5367E881B763  AGENTS.md (변경 없음)
```
