# 본문 제목 1개인 페이지의 오른쪽 인덱스 복구

2026-09-12 사용자 보고: 현재 페이지의 오른쪽 Notion형 인덱스가 보이지 않는다.

## 확인 및 수정

- 인덱스 코드/스크립트 로딩 항목은 삭제되지 않았다. Computer Use의 읽기 전용 DOM
  확인에서 해당 페이지는 표시 가능한 최상위 heading2 1개, 인덱스 버튼 1개,
  `#pageOutline.hidden=true`였다. 관찰한 Chrome의 viewport 1920px, 본문 폭 1635px로
  좁은 화면 조건이 아니라 기존 `headings.length < 2` 조건에 해당했다.
- `notion_static/notion_page_outline.js`에서 제목이 0개일 때만 제목 수로 숨기도록 변경했다.
  제목이 1개인 긴 페이지에서도 섹션 위치 표시와 해당 제목으로 이동할 수 있다.
- 빈 제목/숨겨진 제목/들여쓴 제목 제외, 좁은 화면 숨김, hover 확장, 클릭 후 마우스가
  벗어나면 축소, 키보드 탐색 및 현재 섹션 추적은 유지한다. 본문이나 AI 노트 안의
  문장을 임의의 제목으로 추가하거나 실제 사용자 페이지 내용을 변경하지 않는다.
- index.html의 outline JS 캐시 키만 `20260912-single-heading1`로 갱신했다.
  서버 재시작은 필요 없고 페이지 새로고침으로 반영된다. 녹음/미저장 내용을 보호하기
  위해 실제 사용자 탭은 강제로 새로고침하지 않았다.

## 검증

- `node --check notion_static/notion_page_outline.js` 통과.
- 격리 브라우저 `test_ui_editor_page_outline_tracks_headings_navigation_and_edits`:
  **1 passed / 16.50초**. 다중 제목/한 제목/빈 제목/다시 입력/제목 없음,
  hover·클릭·축소·키보드 이동·좁은 화면을 확인했다.
- OS Temp의 합성 페이지 fixture만 변경했다. 실제 DB/노트/권한/음성은 변경하지 않았다.
- outline JS SHA-256: `7833dbc8cbaa7b0466f813985d68931c056040caab45a74f3942dc98605c5e42`.
- AGENTS.md SHA-256은 기존 `629fe4f841b6aa3ba4ea7b3dd987540e5a3bbcc0bcaf06c2d15f5367e881b763`로
  유지한다. notion.js, SBE hot contract, 기존 immutable receipt는 변경하지 않았다.
  전체 제품/Z0 완료를 선언하는 증거가 아니다.
