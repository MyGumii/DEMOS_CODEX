# Core Free Web UI characterization runbook

상태: UI-first reference 관찰 절차. live 실행·public 게시·plan 변경은 아직 수행하지 않았다.  
기준일: 2026-08-14 Asia/Seoul  
권위 계획: `docs/NOTION_UI_SECOND_BRAIN_XHIGH_HANDOFF_PLAN.md`

## 1. 목적

이 runbook은 사용자가 만든 두 개의 비어 있는 Notion Free workspace에서 합성 콘텐츠만 사용해 Core Web UI의 관찰 가능한 동작을 기록하는 절차다. 결과는 local `code_assist_v1` UI를 구현·검증하기 위한 reference이며 Notion의 private API, billing, Enterprise 운영 로직 또는 개인 콘텐츠를 수집하기 위한 절차가 아니다.

이 절차는 `NP2C_ARCHIVE_LIVE_CHARACTERIZATION_RUNBOOK.md`와 독립이다. Business/Enterprise Archive 연구가 미승인·미실행이어도 이 Core runbook은 진행할 수 있다.

## 2. 승인 범위

현재 사용자 지시로 허용된 범위:

- 로그인된 Chrome session 사용;
- 새 Free synthetic workspace 두 개의 정상 공개 UI 관찰;
- 합성 page/database 생성·편집·move·duplicate·Trash·restore;
- local isolated fixture와 비교;
- redacted screenshot/state/focus/geometry evidence 생성.

현재 지시만으로 허용되지 않는 범위:

- 30일 Business trial 시작 또는 결제/plan 변경;
- Enterprise tenant 생성;
- 실제 사람 invite/message;
- OAuth/external integration;
- public web 게시;
- 개인 workspace/page 열기;
- raw network/cookie/token/private endpoint 수집;
- 운영 local data migration/feature flag enable.

## 3. Reference alias와 금지 workspace

| Alias | 역할 |
|---|---|
| `REF-FREE-A` | 주 synthetic fixture |
| `REF-FREE-B` | workspace switch/isolation |
| `PERSONAL-FORBIDDEN` | 개인 콘텐츠 workspace; 열기·검색·capture 금지 |

실제 workspace 이름과 ID는 repository, log, screenshot filename 또는 manifest에 넣지 않는다.

## 4. 실행 전 점검

다음 항목을 순서대로 확인한다.

1. `AGENTS.md`와 UI-first plan을 읽었다.
2. `get_goal`과 active agent/file ownership을 확인했다.
3. browser control이 현재 로그인 session에 연결된다.
4. 화면에서 `REF-FREE-A/B`를 육안으로 식별할 수 있다.
5. 개인 workspace는 클릭하지 않는다.
6. evidence root는 repository와 live local data root 밖의 승인된 임시 경로다.
7. raw artifact 보존 기간과 cleanup 시점을 기록했다.
8. local fixture는 pytest/trusted OS temp 또는 별도 synthetic sentinel root다.
9. public/invite/plan mutation을 포함하지 않는다.

하나라도 만족하지 않으면 브라우저 action을 시작하지 않는다.

## 5. Synthetic fixture

모든 이름은 `UI-PARITY-<run-id>-<kind>` 패턴을 사용한다.

### 5.1 REF-FREE-A

- root page `...-ROOT`;
- child `...-CHILD-A`;
- child `...-CHILD-B`;
- editor page `...-EDITOR`;
- database `...-DB`;
- Trash/restore page `...-TRASH`.

본문에는 개인정보처럼 보일 수 있는 문자열을 넣지 않는다. 예:

- 제목: `한글 입력 기준 페이지`;
- 본문: `합성 문장 하나`, `합성 문장 둘`;
- property: `상태=진행 중`, `날짜=2030-01-02`;
- 사람/이메일/file/public token은 사용하지 않음.

### 5.2 REF-FREE-B

- root page `...-WORKSPACE-B` 하나;
- A와 겹치지 않는 synthetic title;
- workspace isolation 확인에 필요한 최소 fixture만 유지.

## 6. Action 기록 규칙

각 action은 다음 필드를 가진다.

- `action_id`;
- `scenario_id`;
- `surface`;
- `verb`;
- `target_alias`;
- `input_class`;
- `expected_visible_state`;
- `expected_focus`;
- `capture_after`;
- `mutation_class`;
- `recovery_action`.

허용 verb 예:

- `click`;
- `press`;
- `type_synthetic`;
- `wait_visible`;
- `assert_visible`;
- `assert_hidden`;
- `assert_focus`;
- `assert_text_synthetic`;
- `capture_region`;
- `reload`;
- `switch_workspace_alias`.

Raw DOM selector, private internal ID, network request 또는 token은 reference contract에 넣지 않는다. Local automation은 안정적인 accessible role/name과 제품 소유 `data-testid`를 사용할 수 있지만 reference UI에는 private selector를 요구하지 않는다.

## 7. 첫 characterization sequence

### C0 — connection/read-only smoke

1. 로그인된 Notion 화면이 보이는지 확인한다.
2. switcher를 열되 개인 workspace를 클릭하지 않는다.
3. `REF-FREE-A/B`가 존재함을 육안 확인한다.
4. screenshot을 만들지 않거나, switcher의 다른 workspace label을 모두 redaction한다.
5. 어떠한 page mutation도 하지 않는다.

Gate: browser connection과 workspace boundary만 확인. 실패는 product diff가 아니라 environment/connection failure다.

### C1 — shell/sidebar

1. `REF-FREE-A`를 연다.
2. Home, topbar와 sidebar 기본 상태를 기록한다.
3. sidebar collapse와 restore;
4. resize가 보이면 최소/중간/최대 근처 geometry 기록;
5. Search를 열고 Escape;
6. 원래 control로 focus가 돌아오는지 기록;
7. `REF-FREE-B`로 전환하고 workspace-specific visible state 기록;
8. A로 복귀.

### C2 — page tree/lifecycle

1. synthetic root/children 생성;
2. expand/collapse;
3. rename;
4. nest/reorder/move;
5. duplicate;
6. Trash;
7. restore;
8. breadcrumb와 active tree row 변화 기록.

Official Archive는 실행하지 않는다. 이 sequence의 `Trash`는 Free Core의 Delete/Recycle Bin 의미다.

### C3 — editor golden journey

1. 새 page 생성;
2. Korean title 입력;
3. paragraph 두 개 입력;
4. slash menu open/dismiss;
5. heading/todo/list 변환;
6. save/autosave indicator와 reload;
7. caret, selection, undo/redo;
8. Korean IME composition 중 중복/유실 여부 기록.

### C4 — database/search

1. simple table/database 생성;
2. text/select/date property;
3. row page open/close;
4. table/board/calendar가 Free에서 보이는 범위만 전환;
5. filter/sort 기본 flow;
6. global search에서 synthetic title 검색;
7. preview/open과 keyboard 이동 기록.

### C5 — share visible-state inspection

1. share dialog를 열어 visible Free options만 기록;
2. 실제 invite, public publish 또는 link 생성은 하지 않는다;
3. Escape/close와 focus return 기록.

실제 public 상태 비교가 필요하면 별도 action approval을 받는다.

## 8. Local golden journey

Local app은 격리 synthetic data root에서 다음을 수행한다.

1. home;
2. sidebar collapse/restore;
3. search open/Escape/focus return;
4. new page;
5. Korean title+paragraph;
6. save/reload;
7. Second Brain preview;
8. cancel → knowledge mutation 0;
9. preview again;
10. approve → exact one immutable synthetic publication.

Reference/local 공통 구간은 differential한다. Second Brain 구간은 local hard contract로 판정한다.

## 9. Capture와 redaction

각 scenario는 다음 capture를 독립적으로 남긴다.

- visible state summary;
- focused accessible element;
- keyboard result;
- semantic regions와 bounding boxes;
- accessible role/name/state summary;
- screenshot crop;
- error/recovery result;
- action duration.

다음은 저장하지 않는다.

- full raw DOM;
- raw accessibility tree에 포함된 개인 label;
- cookies/local storage/token;
- network request/response payload;
- absolute local path;
- actual workspace/page/user ID;
- non-synthetic content.

Redaction 뒤 별도 reviewer가 synthetic allowlist만 남았는지 확인한다. 실패한 raw artifact는 acceptance에 사용하지 않고 삭제 대상으로 표시한다.

## 10. Differential 판정

Exact 비교:

- visible/hidden state;
- action result;
- keyboard/focus transition;
- error/recovery semantics;
- workspace isolation;
- Second Brain snapshot/revision contract;
- accessible role/name/state where product terminology is intentionally aligned.

Tolerance 비교:

- region geometry;
- spacing/size ratio;
- responsive breakpoint behavior;
- visual hierarchy and affordance.

Pixel-identical brand/logo/color copying은 목표가 아니다. 독립 visual tokens를 사용하면서 Notion에 익숙한 사용자가 같은 위치·gesture·state model을 예측할 수 있는지를 평가한다.

## 11. Cleanup

1. synthetic fixture 목록을 alias로 확인한다.
2. public/invite/external side effect가 0인지 확인한다.
3. 필요한 fixture는 다음 repeatability run까지 유지하고 retention을 기록한다.
4. 제거할 fixture는 Trash로 이동한다.
5. permanent delete가 필요하면 별도 승인 후 synthetic prefix exact target만 처리한다.
6. raw evidence cleanup과 redacted derivative hash를 기록한다.

## 12. Stop 조건

- 개인 workspace/page가 열림;
- synthetic allowlist 밖 텍스트가 capture됨;
- reference workspace를 확실히 식별할 수 없음;
- browser control이 다른 profile/session에 연결됨;
- trial/plan/invite/public/external mutation이 요구됨;
- redaction verifier 실패;
- local test가 live data root를 가리킴;
- source/runtime hash drift;
- 사용자 또는 agent ownership 충돌.

Archive/Business/Enterprise가 Free에서 보이지 않는 것은 stop 조건이 아니다. 해당 scenario를 `conditional` 또는 `unknown`으로 기록하고 Core sequence를 계속한다.

