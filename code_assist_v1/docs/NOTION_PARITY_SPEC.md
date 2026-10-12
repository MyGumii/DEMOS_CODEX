# Notion 동작 대조 명세

마지막 코드 검증: 2026-08-12

이 문서는 `code_assist_v1`의 Notion Workspace가 따라야 할 사용자 행동과 현재 검증 상태를 기록한다. Notion 클라우드 서비스는 계속 변경되므로, 공식 도움말을 기준선으로 삼고 로그인된 실제 서비스와 로컬 앱의 반복 조작 결과를 최종 판정으로 사용한다.

## 공식 기준 문서

- [Writing & editing basics](https://www.notion.com/help/guides/writing-and-editing-basics)
- [What is a block?](https://www.notion.com/help/what-is-a-block)
- [Keyboard shortcuts](https://www.notion.com/help/keyboard-shortcuts)
- [Create a subpage](https://www.notion.com/help/create-a-subpage)
- [Navigate with the sidebar](https://www.notion.com/help/navigate-with-the-sidebar)
- [Search](https://www.notion.com/en-gb/help/search)
- [Columns, headings & dividers](https://www.notion.com/help/columns-headings-and-dividers)
- [Synced blocks](https://www.notion.com/help/synced-blocks)
- [Keep pages tidy with tabs — 2026-03-30 release](https://www.notion.com/releases/2026-03-30)
- [Introduction to databases](https://www.notion.com/help/intro-to-databases)
- [Views, filters & sorts](https://www.notion.com/help/views-filters-and-sorts)
- [Database properties](https://www.notion.com/help/database-properties)
- [Database views](https://www.notion.com/help/category/database-views/all)
- [Dashboards](https://www.notion.com/help/dashboards)
- [Forms](https://www.notion.com/help/forms)
- [Feeds](https://www.notion.com/help/feeds)
- [Maps](https://www.notion.com/help/maps)
- [Comments, mentions & reminders](https://www.notion.com/help/comments-mentions-and-reminders)
- [Reminders](https://www.notion.com/help/reminders)
- [Duplicate, delete & restore](https://www.notion.com/help/duplicate-delete-and-restore-content)
- [Duplicate public pages](https://www.notion.com/en-gb/help/duplicate-public-pages)
- [Import data](https://www.notion.com/help/import-data-into-notion)
- [Notion AI FAQs](https://www.notion.com/help/notion-ai-faqs)

## 기능 대조표

`자동 검증`은 저장소 테스트로 재현되는 범위이고, `실서비스 대조`는 로그인된 Notion과 로컬 앱을 같은 순서로 직접 조작한 결과다.

| 영역 | 기대 행동 | 구현 | 자동 검증 | 실서비스 대조 |
|---|---|---:|---:|---:|
| 페이지 | 생성, 이름 변경, 복제, 하위 페이지, 이동, 즐겨찾기, 잠금 | 완료 | 완료 | 대기 |
| 저장 일관성 | 메타데이터·블록 원자 저장, revision CAS, 멱등 재시도, 충돌 초안 보존 | 1차 완료 | 완료 | 대기 |
| 사이드바 | 트리 접기/펼치기, 최근/공유/개인 영역, 크기 조절 | 완료 | 완료 | 대기 |
| 검색 | 제목·본문 검색, 키보드 이동, 미리보기, 페이지 열기 | 완료 | 완료 | 대기 |
| 휴지통 | 보관, 복원, 영구 삭제 | 완료 | 완료 | 대기 |
| 편집기 | 블록 삽입/전환/삭제/복제/이동, 다중 선택, undo/redo | 완료 | 완료 | 대기 |
| 기본 블록 | 텍스트, 제목, 목록, 할 일, 토글, 인용, 콜아웃, 구분선 | 완료 | 완료 | 대기 |
| 고급 블록 | 코드, 수식, 미디어, 파일, 북마크, 임베드, 목차, 경로, 버튼 | 완료 | 완료 | 대기 |
| 레이아웃 | 다단 컬럼, 간단한 표, 동기화 블록, 페이지 탭 | 완료 | 완료 | 대기 |
| 인라인 서식 | 굵게, 기울임, 밑줄, 취소선, 코드, 링크, 색 | 완료 | 완료 | 대기 |
| 댓글 | 페이지/블록 스레드, 답글, 반응, 해결/재열기, 필터 | 완료 | 완료 | 대기 |
| 멘션·알림 | 사용자/페이지 멘션, 미리 알림, Inbox | 완료 | 완료 | 대기 |
| 공유 | 사용자별 권한, 공개 읽기 링크, 복제 허용/차단, 공개 페이지 트리 복제 | 완료 | 완료 | 대기 |
| 데이터베이스 보기 | 테이블, 보드, 타임라인, 캘린더, 목록, 갤러리, 차트 | 완료 | 완료 | 대기 |
| 신규 보기 | 대시보드, 양식, 피드, 지도 | 완료 | 완료 | 대기 |
| 보기 설정 | 이름 있는 복수 보기, 보기별 필터·정렬·속성 표시 | 완료 | 완료 | 대기 |
| 데이터 속성 | 기본 속성, 파일, 사용자, 관계형, 롤업, 수식, 장소 | 완료 | 완료 | 대기 |
| 양식 제출 | 필수값/형식 검증, 행과 하위 페이지 생성, 공개 익명 제출 | 완료 | 완료 | 대기 |
| 가져오기 | Markdown, CSV 및 CSV 속성 추론 | 완료 | 완료 | 대기 |
| 내보내기 | Markdown, HTML, 데이터베이스 CSV, 인쇄/PDF | 완료 | 완료 | 대기 |
| 표시 | 페이지 글꼴·폭·글자 크기, 라이트/다크 모드 | 완료 | 완료 | 대기 |
| 페이지 기록 | 버전 목록과 이전 버전 복원 | 완료 | 완료 | 대기 |
| Notion AI | 페이지 문맥 질의와 블록 쓰기 작업, 고정 제공자 라우팅 | 완료 | 완료 | 대기 |

## AI 제공자 계약

| 실행 방식 | 허용 제공자 | 모델 | 실패 시 |
|---|---|---|---|
| 기본 | Hynix API만 | `gemma4-31b` | 결정론적 로컬 추출 fallback |
| `--codex` | Codex만 | `codex-local` | 결정론적 로컬 추출 fallback |

클라이언트가 보낸 모델명은 이 계약을 변경하지 못한다. 기본 모드는 현재 동작하지 않는 Hynix API를 최대 5초 시도한 뒤 fallback하며, 실패 후 60초 회로 차단 시간에는 즉시 fallback한다. `--codex` 모드에서 Hynix API로 우회하거나 기본 모드에서 Codex로 우회하지 않는다.

## 자동 검증

저장소 루트 `G:\AI_Agent\DEMOS_CODEX`에서 다음 검증을 통과해야 한다.

```powershell
.\venv\Scripts\python.exe -m pytest .\code_assist_v1\tests -q

Set-Location .\code_assist_v1
node --check notion_static\notion.js
node --check notion_static\notion_editor_core.js
node tests\test_notion_editor_core.js
node tests\test_notion_ui_runtime.js
node tests\test_notion_atomic_save.js
..\venv\Scripts\python.exe -m py_compile app_notion.py internal_llm.py notion_store.py routes_notion.py outlook_integration.py routes_outlook.py
```

2026-08-02 결과: Python 31개 테스트, JavaScript 편집기 코어 및 UI 런타임 테스트, Python/JavaScript 구문 검사가 모두 통과했다. Classic Outlook 실환경 bridge는 로그인된 프로필의 상태와 읽지 않은 메일 1건의 필수 필드 수신까지 확인했으며 메일 내용은 검증 출력에 노출하지 않았다.

2026-08-12 원자 저장 1차 결과: 신규 DB에서 페이지·블록을 하나의 트랜잭션으로 저장하고, 단조 증가 `revision`, CAS 충돌, 사용자별 `operation_id` 재시도를 검증했다. 메인 편집기와 side peek은 `409` 시 자동 덮어쓰기를 중단하고 현재 초안을 보존한다. 기존 DB는 일반 요청으로 스키마를 바꾸지 않으며 `capabilities.atomic_save=false`인 동안 종전 PATCH + PUT 경로를 유지한다. 운영 DB 마이그레이션은 실행하지 않았다.

최종 현재 소스 검증은 Python `495 passed + 5 subtests`, Notion JavaScript 구문·편집기·UI·원자 저장 테스트 전부 통과, code-callsite inventory `655 = 395 + 260`(미분류 0), 문서 계약 감사 오류 0이다. 모든 DB 테스와 마이그레이션 시험은 pytest 격리 임시 경로에서만 수행했다.

### 원자 저장 마이그레이션 경계

`tools/migrate_notion_atomic_save.py`는 기본이 읽기 전용 dry-run이다. 적용은 정확한 사용자 ID와 DB 경로, 백업·복구 확인, 현재 checksum, `--apply`가 모두 제공된 배포 작업에서만 실행된다. 앱 시작·일반 연결·웹 라우트는 이 마이그레이션을 호출하지 않는다.

## 실서비스 대조 절차

최종 완료 판정 전에는 인앱 브라우저에 로그인된 실제 Notion 탭과 최신 코드로 재시작한 로컬 `http://localhost:10012` 탭을 함께 열고 다음 순서로 비교한다.

1. 동일한 페이지/하위 페이지 트리를 만들고 사이드바 이동·검색·복제·휴지통 복원을 비교한다.
2. 슬래시 메뉴의 각 지원 블록을 만들고 Enter, Backspace, Tab, drag/drop, 블록 전환과 다중 선택을 비교한다.
3. 인라인 서식, 링크, 멘션, 댓글 스레드, 알림과 Inbox 흐름을 비교한다.
4. 한 데이터베이스에 모든 지원 속성을 만들고 각 보기에서 동일한 행을 편집한다.
5. 보기별 필터·정렬·속성 표시를 바꾼 뒤 다른 보기로 왕복하여 상태 보존을 확인한다.
6. 보드 이동, 캘린더/타임라인 날짜 범위, 차트 집계, 대시보드 위젯, 피드 정렬, 지도 장소를 비교한다.
7. 내부 사용자 권한과 공개 페이지를 비교하고, 로그인하지 않은 창에서 공개 양식을 제출한다.
8. Markdown/CSV 가져오기와 Markdown/HTML/CSV/PDF 내보내기를 비교한다.
9. 기본 실행과 `--codex` 실행을 각각 재시작해 `/healthz`와 AI 응답 제공자/fallback을 확인한다.

## 아직 동일하다고 판정할 수 없는 범위

현재 인앱 브라우저에 열린 탭이 없어 위 실서비스 대조 절차는 실행 대기 상태다. 따라서 자동 검증을 통과했더라도 “Notion의 모든 행동과 완벽히 동일”하다고 최종 판정하지 않는다.

또한 아래 SaaS 범위는 별도 제품 수준 구현 없이는 동등하지 않다.

- 실시간 다중 사용자 CRDT 편집, 커서와 presence, 오프라인 충돌 병합
- Notion 계정·요금제·조직·감사 로그·SCIM/SAML 같은 엔터프라이즈 관리
- Slack, Google Drive, GitHub 등 외부 연결과 자동화 생태계
- 공개 사이트의 사용자 도메인, 검색 최적화, 분석, 전체 사이트 관리
- Notion이 서버에서 제공하는 독점 AI 모델 및 웹/워크스페이스 커넥터

이 항목까지 요구 범위에 포함한다면 현재 작업은 완료가 아니라 제품 로드맵 수준의 미완료 상태로 유지한다.
