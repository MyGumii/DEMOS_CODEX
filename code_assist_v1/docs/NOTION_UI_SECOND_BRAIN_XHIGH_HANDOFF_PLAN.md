# Notion UI × Second Brain — xhigh 구현 인계 및 전체 개발 계획

기준 시각: 2026-08-14 Asia/Seoul  
계획 작성 프로필: `gpt-5.6-sol` ultra planning/review  
권장 구현·리뷰 프로필: `gpt-5.6-sol` xhigh  
상태: UI-first 제품 범위와 실행 순서를 재정의한 권위 인계본. 구현 완료, 운영 적용, live migration 또는 전체 Notion 동등성 선언이 아니다.

최신 UI-first checkpoint: `docs/notion_parity/checkpoints/ui-resp-001-local-viewport-green-reference-pending-20260815.json`  
Core reference runbook: `docs/notion_parity/CORE_FREE_WEB_UI_CHARACTERIZATION_RUNBOOK.md`
H1 implementation design: `docs/notion_parity/CORE_FREE_WEB_UI_HARNESS_DESIGN.md`

Current authoritative completion audit (supersedes the older execution note
immediately below): the isolated local side now covers all 25 required Core
scenarios. Twenty-four have real-Chrome functional evidence and
`UI-PERF-001` has a measurement-only real-Chrome baseline. The H9 isolated
pilot/recovery foundation is green. Reference characterization,
differential-passed evidence, promotions, an approved performance budget, and
authorized live rollout remain zero or pending. The machine-readable source of
truth is
`docs/notion_parity/core_free_web_ui/completion_matrix.v1.json`; a passing
completion-matrix verifier means the incomplete snapshot is truthful, not that
Core parity is complete.

Current execution note (2026-08-15): isolated real-Chrome `UI-SHELL-001`, `UI-SHELL-003`, `UI-TREE-001` through `UI-TREE-003`, the full local H4 set `UI-EDITOR-001` through `UI-EDITOR-006`, H5 `UI-DB-001` through `UI-DB-003`, all adopted local H6 slices `UI-SEARCH-001`/`UI-SEARCH-002`/`UI-COLLAB-001`/`UI-SHARE-001`, H7 `UI-SB-001` through `UI-SB-003`, and H8 `UI-RESP-001` are local-green. The H3 slices prove scoped workspace navigation, coherent page hierarchy, accessible tree behavior, persisted create/move, and bounded duplicate -> Trash -> restore with zero permanent-delete requests or Second Brain mutation. H4 covers exact title/block save and reload, slash-command transformation, selection/clipboard/undo/redo, Korean composition, main/full/side-peek persistence and focus, and network-failure/stale-revision draft recovery. H5 covers keyboard-addressable typed database rows, deterministic filter/sort/group state, and table/board/list/gallery/calendar switching over unchanged canonical rows with active-view and focus restoration. H6 covers quick search, current-page find, page links, scoped backlinks, comment lifecycle, and a read-only share dialog with stable trigger focus and zero access/public-link mutation. H7 covers publication preview cancel, approve/replay, and a synthetic session attachment `POST -> DELETE` with current-access-filtered reads, stable session-context focus, zero external requests, and zero publication delta. `UI-RESP-001` now repeats shell, saved editor, search overlay, and share inspector behavior at 1440×900, 1280×800, 980×800, and 720×800 with zero global horizontal overflow, reachable dismissal controls, stable focus restoration, zero external requests, and zero Second Brain delta. The commands remain workspace-bound and default-off and do not enable locator resolver cutover. `REF-FREE-A/B` reference capture and the first differential remain pending; no reference or parity promotion is claimed. The next local slice is the remaining H8 `UI-A11Y-001` full-surface accessibility gate, followed by measured `UI-PERF-001`.

이 문서는 현재 실행 우선순위에 한해 다음 문서의 오래된 “다음 단계” 문장을 supersede한다.

- `docs/NOTION_PARITY_MASTER_PLAN.md`
- `docs/NOTION_PARITY_ROADMAP.md`
- `docs/NOTION_PARITY_XHIGH_HANDOFF_PLAN.md`
- `docs/notion_parity/NP2A_WORKSPACE_SUBSTRATE.md`
- `docs/notion_parity/NP2C_PAGE_TREE_LIFECYCLE_DESIGN.md`
- `docs/notion_parity/NP2C_OFFICIAL_ARCHIVE_DESIGN.md`
- `docs/notion_parity/NP2C_ARCHIVE_LIVE_CHARACTERIZATION_RUNBOOK.md`

기존 문서의 schema lineage, checksum, 안전 불변식, 과거 checkpoint와 검증 결과는 계속 유효하다. 이 문서는 그것을 삭제하거나 약화하지 않고, **유료 Archive 연구를 전체 제품의 임계경로에서 분리하고 Core Web UI·interaction 개발을 전면으로 이동**한다.

---

## 0. 한 문장 제품 계약

우리가 만드는 것은 Notion의 결제·요금제·Enterprise 운영 체계를 복제한 SaaS가 아니다.

우리가 만드는 것은 **Notion에 익숙한 사용자가 거의 다시 배우지 않고 사용할 수 있는 Core Web UI와 상호작용을 가지며, 기존 Second Brain의 publication·session·context·thought 흐름과 안전하게 연결되는 독립 제품**이다.

따라서 구현 우선순위는 다음과 같다.

1. 화면 위계, 공간 배치, 상태 표현, 키보드, 초점, caret, drag/drop, 메뉴, 전환과 오류 회복;
2. 그 UI가 거짓 상태를 보여주지 않도록 하는 저장·권한·재시도·충돌·projection 로직;
3. Second Brain 연결의 snapshot/revision/permission 불변식;
4. 데이터 안전, 접근성, 성능과 운영 가능성;
5. 사용자가 실제로 채택하는 경우에만 조건부 유료·Enterprise 기능.

Notion 내부 billing, entitlement, 조직 관리, 사설 API, 내부 프로토콜 또는 bit-for-bit AI 결과는 Core 범위가 아니다.

---

## 1. 이 재정의가 필요한 이유

기존 계획은 안전한 backend substrate를 매우 깊게 구축했지만, 최근 우선순위가 Business/Enterprise 전용 beta Archive의 live characterization에 직렬화되었다. 공식 Archive 연구에는 그 조건이 맞지만, 사용자가 요구한 Core UI 제품에는 맞지 않는다.

현재 사실은 다음과 같다.

- 사용자는 Chrome에 로그인되어 있다.
- 사용자는 별도의 비어 있는 Free 합성 workspace 두 개를 만들었다.
- 개인 콘텐츠가 있는 기존 workspace는 관찰·fixture·evidence에서 제외해야 한다.
- Business/Enterprise workspace나 30일 trial은 현재 Core UI 개발에 필요하지 않다.
- 현재 local UI는 이미 넓은 기능 진입점을 가진 단일 SPA이지만, 실제 브라우저와 공식 제품을 같은 action으로 대조한 증거는 없다.
- 현재 backend는 workspace schema `2026081317`, frozen checksum `sha256:6cb890ccc026435715a1c9171b4d1fd0f74ae6896f63d3cdf66a27b6eec5b61b`, actual schema hash `sha256:650a031ff1f7d1794bde06d9f9479794d0c89fa2faba8e07762badfe3c500aad`, 179 objects까지의 격리 substrate를 가진다.
- 이 schema/migration 기반은 유용하지만 운영 DB에는 적용되지 않았고 Core UI의 시각·interaction 완료를 의미하지 않는다.

공식 Archive 설계와 승인 harness는 폐기하지 않는다. 그것은 `Optional Paid Archive` 연구 트랙의 준비 자산으로 휴면 보존한다.

---

## 2. 권위 순서

xhigh는 서로 다른 문장이 충돌할 때 다음 순서로 판단한다.

1. 현재 사용자의 명시적 제품 의도와 데이터 접근 지시;
2. `code_assist_v1/AGENTS.md`의 안전·호환·운영 금지 규칙;
3. 이 문서;
4. 이 문서가 지정하는 최신 UI-first checkpoint;
5. `NOTION_PARITY_MASTER_PLAN.md`와 `NOTION_PARITY_ROADMAP.md`의 불변 계약;
6. NP2A/NP2B/NP2C 상세 설계와 frozen migration 계약;
7. 과거 checkpoint와 historical handoff.

낮은 순위의 문서가 Business/Enterprise Archive를 “전체 다음 단계”라고 쓰면 다음과 같이 해석한다.

- Archive 트랙 안에서는 다음 단계다.
- Core Free Web UI 트랙의 선행조건은 아니다.
- Archive가 `unknown` 또는 미관찰이어도 Core UI gate는 실패하지 않는다.

---

## 3. 지원 범위 분모

### 3.1 Core Free Web — 현재 제품 분모

첫 고정 profile은 다음과 같다.

| 축 | 값 |
|---|---|
| 공식 reference | Notion Free workspace의 정상 공개 UI |
| 플랫폼 | Chrome desktop web |
| locale | `ko-KR` |
| 역할 | workspace owner |
| 데이터 | 합성 테스트 데이터만 |
| workspace | 사용자가 새로 만든 Free workspace A/B |
| local target | `code_assist_v1`의 Notion형 SPA |
| 통합 | 기존 Second Brain publication/session/context |

Core 분모에는 다음이 들어간다.

- workspace shell, switcher, home, topbar, sidebar;
- page tree와 breadcrumb;
- create, rename, nest, reorder, move, duplicate, Delete/Trash, restore;
- title, icon, cover, page header, full page와 peek;
- 기본 block/rich text, slash/inline menu, selection, clipboard, drag, undo/redo, Korean IME;
- 일반 database property/view UI;
- quick search, page find, backlinks와 page link;
- Free에서 실제로 노출되는 기본 comment/share/public surface;
- Second Brain preview/approve/cancel, attach/detach, template, chat/thought import;
- desktop responsive range, keyboard, focus-visible, a11y와 성능.

### 3.2 Extension — Core 뒤에 채택 가능한 범위

- guest/member 협업과 realtime presence;
- mobile web interaction;
- 고급 database view/property/form/dashboard;
- richer comments/inbox/reminders;
- 외부 public share의 추가 옵션;
- broader locale/browser profile.

Extension이 미완료여도 Core 완료 판정은 실패하지 않는다. 다만 사용자가 해당 기능을 채택하면 별도의 scenario 분모와 gate를 만든다.

### 3.3 Conditional paid/moving — Core를 막지 않는 별도 트랙

- 공식 Archive/unarchive beta;
- Business/Enterprise plan 전환과 plan-loss 관찰;
- Enterprise Content Search, SAML, SCIM, DLP, eDiscovery, compliance/audit;
- 유료 Sites/custom domain/analytics;
- 유료 automation/AI credit 동작;
- native desktop/mobile/offline;
- moving beta/alpha 기능.

Notion의 plan 이름은 reference 환경 metadata다. 독립 제품은 자체 capability/feature flag를 사용하며 Notion billing·upgrade·downgrade를 복제하지 않는다.

### 3.4 Excluded

- 개인 workspace의 콘텐츠, 이름, 페이지, 이미지, 파일 또는 metadata;
- Notion private endpoint, raw network payload, cookie/token, scraping;
- Notion 상표·로고·독점 asset의 복제;
- Notion billing/licensing 내부 로직;
- 공급자 고유 AI 문장의 bit-for-bit 복제;
- 이 계획에서 채택하지 않은 Enterprise 운영 제품군.

---

## 4. 합성 reference workspace 계약

사용자가 만든 두 Free workspace는 repository와 evidence에서 실제 이름이나 ID를 쓰지 않고 다음 alias로만 부른다.

- `REF-FREE-A`: 주 fixture workspace;
- `REF-FREE-B`: workspace 전환과 격리 검증용.

### 4.1 허용 동작

합성 workspace 안에서는 다음 synthetic 작업을 수행할 수 있다.

- `UI-PARITY-<run-id>` prefix의 page/database 생성;
- 합성 제목·본문·block·property 편집;
- nesting, reorder, move, duplicate;
- Delete/Trash와 restore;
- workspace A/B 전환;
- visible Free UI의 메뉴·keyboard·focus 관찰.

### 4.2 별도 확인이 필요한 동작

다음은 synthetic workspace라도 실행 시점의 별도 확인 없이는 하지 않는다.

- plan trial 시작, 결제, upgrade/downgrade;
- 실제 사용자 invite, email/message 발송;
- 외부 integration/OAuth 연결;
- public web 게시 또는 외부 접근 가능한 link 생성;
- 테스트 범위를 넘는 permanent delete;
- 실제 외부 사람·조직에 영향을 주는 mutation.

### 4.3 절대 금지

- 개인 콘텐츠 workspace를 클릭하거나 열기;
- 개인 page를 검색·capture·복사·fixture화하기;
- workspace switcher에 보이는 개인 이름을 raw evidence에 남기기;
- browser cookie, token, raw DOM dump, raw network log를 repository에 저장하기;
- 기존 `images/notion*.PNG`를 새 baseline으로 재사용하기. 일부 파일에는 개인 정보가 있으므로 historical/private reference로만 취급한다.

### 4.4 evidence redaction

모든 reference capture는 다음을 보장한다.

- workspace/page/user 식별자는 alias 또는 deterministic placeholder로 치환;
- 개인 workspace가 보일 수 있는 switcher 영역은 crop 또는 redaction;
- synthetic content 외의 텍스트가 발견되면 artifact를 폐기;
- 원본 screenshot은 승인된 repository 밖 임시 evidence root에 두고, repository에는 redacted derivative와 hash/manifest만 저장;
- cookie, URL query token, filesystem path, email, public token, raw exception을 기록하지 않음.

---

## 5. 현재 구현 스냅샷

### 5.1 UI surface

현재 UI는 Flask가 정적 SPA를 제공하는 구조다.

- `notion_static/index.html`: shell, sidebar, topbar, page/editor, drawers, overlays;
- `notion_static/notion.css`: 약 4,600줄의 layout/style/responsive rules;
- `notion_static/notion.js`: 약 9,400줄의 runtime, state, API, editor와 database rendering;
- `notion_static/notion_editor_core.js`: block catalog와 순수 editor helpers;
- `notion_static/notion_publication.js`: Second Brain publication helper.

이미 존재하는 주요 surface:

- workspace switcher, Home, Inbox, AI, sidebar resize/collapse;
- Favorites/Recents/Teamspaces/Shared/Private와 nested page tree;
- breadcrumb, icon/cover/title, share/comments/files/history drawers;
- search, page find, Trash, block/slash/mention/inline menus;
- side peek, database row navigation;
- 기본/고급/media/database block catalog;
- table/board/list/gallery/calendar/timeline/chart/dashboard/form/feed/map renderer;
- comments, reactions, history, sharing/public, import/export, files;
- light/dark와 일부 responsive CSS.

### 5.2 현재 UI의 중요한 한계

- canonical workspace navigation은 default-off다.
- workspace 선택과 실제 page 목록 scope가 완전히 결속되지 않았다.
- 활성 UI는 여전히 legacy per-user page discovery를 폭넓게 사용한다.
- main editor는 `contenteditable`, side peek은 별도 textarea 계열이어서 command 의미가 다르다.
- center peek은 아직 없다.
- Korean IME composition/beforeinput에 대한 실제 브라우저 증거가 없다.
- rich text가 `execCommand`에 의존하는 구간이 있다.
- database model은 canonical data-source engine이 아니라 block-local state가 중심이다.
- realtime cursor/presence/convergence는 없다.
- focus-visible, reduced motion, forced colors와 tree semantics가 완결되지 않았다.
- responsive CSS는 존재하지만 Notion mobile interaction을 의미하지 않는다.

### 5.3 테스트 증거의 한계

현재 JavaScript UI 테스트는 주로 Node `vm`, source probe와 inert DOM stub이다. 다음은 검증하지 못한다.

- 실제 Chromium layout/paint;
- focus/caret/selection;
- Korean IME;
- pointer drag/drop와 clipboard;
- screenshot geometry;
- accessibility tree/screen reader;
- responsive viewport interaction;
- official reference와 동일 action replay.

따라서 “코드와 stub 테스트가 존재한다”는 `exists` 또는 부분 `contract-tested` 증거일 뿐 `live-characterized`나 `differential-passed`가 아니다.

### 5.4 Backend substrate의 의미

현재 1303→1317 schema/migration, atomic save, CAS, idempotency, audit/outbox, locator, quarantine, incarnation, navigation, page-tree/lifecycle foundation은 다음 이유로 계속 보존한다.

- UI가 stale/권한 없는/부분 commit 상태를 성공처럼 보여주지 않게 한다.
- retry와 crash 뒤에도 사용자 상태가 수렴하게 한다.
- tenant metadata 누출과 directory scan을 제거할 수 있게 한다.
- Second Brain에 승인되지 않은 snapshot이 publish되지 않게 한다.

그러나 이 substrate의 깊이를 UI 완성률로 환산하지 않는다. 운영 migration, resolver cutover, continuous consumer와 broad flag enable은 계속 별도 승인 대상이다.

### 5.5 Second Brain은 후속 부가기능이 아니다

현재 다음 흐름은 이미 구현되어 있으며 모든 UI slice의 hard regression이다.

- dirty save → preview → 명시 승인 → source hash 재검증 → publish;
- preview cancel/error 시 knowledge mutation 0;
- session page attach/detach와 context 구성 시 권한 재검증;
- chat full/summary/decisions와 thought import;
- Second Brain template 생성;
- Notion page revision과 Knowledge revision 분리.

UI가 더 Notion처럼 보이더라도 이 계약을 깨면 slice는 실패다.

---

## 6. 진행률을 보고하는 방법

이 계획에서는 “전체 Notion의 몇 %”를 사용하지 않는다. Enterprise/native/AI까지 포함한 거대 분모와 Core UI 분모를 섞으면 숫자가 의미를 잃기 때문이다.

현재 보고는 다음 네 축으로 나눈다.

| 축 | 현재 판정 |
|---|---|
| 안전한 저장·호환 substrate | advanced, 격리 contract evidence 다수 |
| Core UI surface inventory | broad but inconsistent |
| 실제 브라우저 UI 검증 | established locally for the UI1 journey, H3 shell/tree, full H4 editor, and H5 UI-DB-001/UI-DB-002; reference pending |
| 공식 Free reference differential | 0 scenarios passed |

H0에서 Core scenario denominator를 machine-readable하게 고정한 뒤에만 다음처럼 count를 보고한다.

- `required_core_scenarios`;
- `exists_count`;
- `contract_tested_count`;
- `live_characterized_count`;
- `differential_passed_count`;
- `blocked_count`와 이유;
- conditional track은 별도 count.

UI completion은 scenario 수와 gate로만 판단하고 schema version, 코드 줄 수 또는 테스트 파일 존재로 대신하지 않는다.

---

## 7. 전체 실행 단계 H0~H9

### H0 — Scope rebase와 안전 snapshot

목표:

- Core Free Web, extension, conditional, excluded 분모를 고정한다.
- 사용자 합성 workspace A/B와 개인 workspace 금지 경계를 기록한다.
- 현재 파일 hash, owner, goal 상태와 historical checkpoint를 고정한다.
- 오래된 Archive next-gate 문장을 optional lane으로 재분류한다.

산출물:

- 이 문서;
- Core Free Web characterization runbook;
- UI-first scope checkpoint;
- 후속 machine-readable UI scenario overlay와 strict verifier 계획.

Gate:

- `AGENTS.md` 무변경;
- live local data 접근 0;
- 운영 migration/flag 변경 0;
- Core/conditional 분모가 명시됨;
- 기존 Archive checkpoint가 historical optional로 보존됨;
- “전체 10%” 같은 추측치가 최신 권위 문서에 사용되지 않음.

현재 상태: H0 documentation rebase와 machine-readable scenario overlay는 완료되었다. H1 local real-browser proof와 redacted evidence machinery는 수립되었고, signed-in `REF-FREE-A/B` characterization과 첫 differential만 남아 있다.

### H1 — 실행 가능한 실제 브라우저 evidence harness

구현 세부 계약은 `docs/notion_parity/CORE_FREE_WEB_UI_HARNESS_DESIGN.md`가 고정한다. xhigh는 historical catalog나 기존 evidence verifier를 덮어쓰지 않고 Core Free Web 전용 overlay와 verifier를 새 파일로 추가한다. 기존 comparator의 `2px` geometry tolerance와 `500ms` latency budget은 historical 정책이며, 첫 synthetic reference/local pair가 승인되기 전에는 Core UI 합격 기준으로 승격하지 않는다.

목표:

- official Free reference와 isolated local app에 같은 action grammar를 적용한다.
- visible state, keyboard/focus, a11y, semantic visual, geometry와 recovery를 비교한다.
- artifact redaction, hash, provenance와 repeatability를 제공한다.

필수 구성:

1. Core scenario manifest와 strict verifier;
2. synthetic local auth/workspace/page fixtures;
3. action vocabulary: click, key, type, wait-for-visible, assert-focus, assert-state, screenshot;
4. reference observation manifest;
5. local replay manifest;
6. normalized state/focus/a11y/geometry capture;
7. redacted artifact writer와 SHA-256 manifest;
8. deterministic comparator integration;
9. failure taxonomy: product-diff, fixture, connection, redaction, host-dependent.

현 저장소에는 Playwright/Selenium/Puppeteer 기반 persistent runner가 확인되지 않았다. 첫 proof는 사용 가능한 in-app browser control로 수행할 수 있다. 새 automation dependency가 필요하면 다음을 먼저 기록한다.

- 왜 기존 도구로 부족한가;
- package/version/license;
- Windows install/runtime 영향;
- CI와 local 재현 방식;
- artifact 보안 경계;
- 제거/rollback 방법.

Gate:

- 실제 Chromium에서 synthetic local smoke 1회 이상 반복 가능;
- inert DOM stub을 browser evidence로 오인하지 않음;
- official/local 양쪽 action log가 같은 scenario ID에 결속됨;
- personal content, raw DOM/network/cookie/token 0;
- repository 밖 raw evidence와 repository 안 redacted manifest가 명확히 분리됨.

### H2 — Free reference characterization

목표:

- `REF-FREE-A/B`의 정상 UI에서 Core scenario를 synthetic content로 관찰한다.
- 관찰되지 않은 항목은 추정하지 않고 `unknown`으로 남긴다.

우선 관찰 순서:

1. workspace switcher와 shell;
2. sidebar section, collapse/resize, nested tree;
3. home, empty page, page header;
4. create/rename/nest/reorder/move/duplicate/Trash/restore;
5. title/body save/reload;
6. slash/inline menu, keyboard, focus와 Korean IME;
7. search overlay/preview/open;
8. table/board/calendar 기본 전환;
9. Free에서 노출되는 basic share/public UI;
10. desktop viewport 변형.

Gate:

- Core scenario마다 dated Free/Chrome/ko-KR observation 또는 explicit `unknown`;
- 실제 workspace/page/user identifier가 evidence에 없음;
- 개인 workspace click/open 0;
- trial/plan upgrade/invite/public mutation 0 unless separately approved.

### H3 — Desktop shell와 workspace-scoped navigation

목표:

- local shell의 정보 위계와 navigation interaction을 reference에 맞춘다.
- workspace A/B 전환 시 page/search/favorite/breadcrumb가 정확한 workspace scope를 사용한다.

사용자-visible scope:

- workspace switcher;
- sidebar sections와 nested page tree;
- resize/collapse/restore;
- Favorites/Recents/Shared/Private;
- home, search, new page affordance;
- topbar, breadcrumb, active/hover/loading/empty/error state;
- keyboard tree navigation과 focus restoration.

재사용:

- existing default-off workspace navigation capability;
- current navigation route/store contract;
- existing legacy UI fallback.

금지:

- UI 편의를 위해 directory scan을 새로 추가;
- workspace scope가 없는데 client filter만으로 숨기기;
- migration/flag를 request startup에서 실행;
- dirty draft가 flush되지 않았는데 workspace 전환.

Gate:

- workspace 전환 후 타 workspace metadata 누출 0;
- main/peek dirty draft flush 실패 시 전환 중단;
- keyboard/focus/geometry differential 통과;
- feature flag off에서 기존 route/UI 회귀 0;
- Second Brain controls와 publication regression 통과.

### H4 — Page canvas와 editor interaction fidelity

목표:

- main/full/side peek가 같은 block command model과 저장 의미를 사용한다.
- Notion에 익숙한 keyboard/caret/selection 흐름을 제공한다.

초기 분모:

- page title, icon, cover;
- paragraph, H1–H3, todo, bullet, numbered, toggle;
- quote, callout, divider, code, equation;
- link, bookmark, image, file, child page, columns, simple table;
- slash menu와 inline toolbar;
- Enter, Backspace, Tab, Shift+Tab, Escape;
- multi-select, drag, clipboard, undo/redo;
- Korean IME composition;
- autosave, reload, revision conflict;
- main/full/side peek consistency.

Gate:

- block round-trip loss 0;
- Korean IME duplicate/loss/premature split 0;
- paste sanitize와 unsupported-content degraded state 명시;
- save conflict에서 dirty draft 보존;
- page revision과 Second Brain preview snapshot revision/hash 일치;
- keyboard/focus/a11y와 target viewport geometry 통과.

### H5 — Database UX

목표:

- UI-only fake state가 아니라 reload 가능한 structured data/view state를 제공한다.

순서:

1. row page와 typed property editor;
2. table view;
3. filter/sort/group;
4. board/list/gallery/calendar;
5. view-local state와 saved view;
6. side/center/full peek transition;
7. advanced views와 formula/relation/rollup은 extension backlog.

Gate:

- deterministic corpus가 reload 뒤 동일;
- 한 view에서 편집한 row가 다른 view와 일치;
- property validation/error recovery;
- keyboard navigation와 focus;
- no cross-workspace leakage;
- Second Brain context가 stale database state를 publish하지 않음.

### H6 — Discovery, comments와 sharing

목표:

- Free owner Core 범위의 discovery/collaboration UI를 닫는다.

범위:

- quick search, title/body search, preview/open;
- in-page find;
- backlinks/page links;
- comments/thread/reaction 기본 flow;
- Inbox 기본 state;
- Free에서 노출되는 share/public state.

조건:

- public mutation, invite, external message는 별도 action approval;
- realtime presence/cursor는 extension;
- Enterprise role/IAM은 conditional;
- server authorization을 client-side concealment로 대체하지 않음.

Gate:

- inaccessible metadata/title/count leakage 0;
- loading/empty/error/revoke/retry state;
- keyboard dismissal과 focus restoration;
- public renderer와 signed/file access regression;
- Second Brain context permission revalidation 통과.

### H7 — Second Brain product journeys

Second Brain hard smoke는 H3~H6에서 계속 실행한다. H7은 깊은 사용자 journey를 닫는다.

필수 journey:

1. dirty page save → preview → cancel → mutation 0;
2. dirty page save → preview → approve → exactly one immutable publication;
3. source drift between preview/approve → fail closed;
4. page attach/detach → current session context;
5. revoked page → context에서 즉시 제외;
6. template creation;
7. chat full/summary/decisions → Notion;
8. thought → Notion;
9. overview와 backlinks;
10. Notion revision과 Knowledge revision 독립성.

Gate:

- route/response compatibility 유지;
- authorization 재검증;
- LLM/network/OCR는 transaction 밖;
- raw user content가 test log/evidence에 없음;
- Knowledge Core/part-space schema나 운영 data를 건드리지 않음.

### H8 — Responsive, accessibility와 performance polish

초기 viewport:

- 1440×900;
- 1280×800;
- 980px boundary;
- 720px narrow web boundary.

검증:

- 100%/125%/200% zoom;
- keyboard-only journey;
- focus-visible와 focus trap/return;
- accessible name/role/state;
- contrast, reduced motion, forced colors;
- horizontal overflow;
- long title, deep tree, large page/database;
- load/edit/save/search latency와 busy/retry count.

성능 budget은 추측으로 고정하지 않는다. H1 harness로 현재 local p50/p95를 측정하고, 첫 approved baseline에서 target과 regression threshold를 정한다.

### H9 — Pilot, release와 conditional packs

Core pilot 순서:

1. isolated local fixture;
2. internal synthetic user;
3. 명시 opt-in workspace;
4. canary;
5. broader rollout.

필수:

- default-off feature flag;
- readiness와 kill switch;
- backup/restore/reconciliation;
- telemetry와 support runbook;
- Second Brain hard gate;
- live data migration 별도 승인;
- legal/brand/accessibility review.

Optional Paid Archive는 H0에서 별도 branch로 유지하며, 사용자가 그 기능을 실제 채택할 때만 Business trial/tenant와 기존 NP2C-5a harness를 사용한다. 이 branch는 Core H3~H8을 막지 않는다.

---

## 8. 의존성 DAG와 병렬화

```text
H0 scope/snapshot
 └─> H1 browser/evidence harness
      ├─> H2 Free reference characterization
      └─> local UI inventory/contract baseline

H2 + local baseline
 └─> H3 shell/navigation
      └─> H4 page/editor
           ├─> H5 database UX
           └─> H6 discovery/comments/share

Second Brain hard smoke ─────────────> H3, H4, H5, H6
H3 + H4 + deep SB journeys ──────────> H7
H4 + H5 + H6 + H7 ──────────────────> H8
H8 ──────────────────────────────────> H9 Core pilot

Optional Archive/B-E ────────────────> 별도 conditional branch
```

병렬화 가능한 작업:

- H2 reference capture와 local read-only UI inventory;
- shell visual token 조사와 navigation contract test;
- editor action catalog와 accessibility audit;
- screenshot redaction harness와 comparator hardening.

병렬화하면 안 되는 작업:

- 같은 `notion.js`, `notion.css`, `index.html` 동시 편집;
- schema/frozen migration과 UI를 한 소유권 없이 동시에 변경;
- reference observation과 local 구현 차이를 한 agent가 근거 없이 동시에 정의;
- live mutation과 evidence acceptance를 같은 승인 없이 수행.

---

## 9. Core scenario seed catalog

H1에서 아래 seed를 machine-readable overlay로 옮긴다. ID는 안정적으로 유지한다.

| ID | 사용자 여정 | 첫 phase |
|---|---|---|
| `UI-SHELL-001` | workspace A/B switch | H3 |
| `UI-SHELL-002` | sidebar collapse/resize/restore | H3 |
| `UI-SHELL-003` | home/topbar/breadcrumb | H3 |
| `UI-TREE-001` | nested page expand/collapse | H3 |
| `UI-TREE-002` | create/rename/nest/reorder/move | H3 |
| `UI-TREE-003` | duplicate/Trash/restore | H3 |
| `UI-EDITOR-001` | title+paragraph save/reload | H4 |
| `UI-EDITOR-002` | slash menu and block transform | H4 |
| `UI-EDITOR-003` | keyboard/caret/selection | H4 |
| `UI-EDITOR-004` | Korean IME composition | H4 |
| `UI-EDITOR-005` | main/full/side peek parity | H4 |
| `UI-EDITOR-006` | conflict/retry/draft recovery | H4 |
| `UI-DB-001` | table row/property round-trip | H5 |
| `UI-DB-002` | filter/sort/group and reload | H5 |
| `UI-DB-003` | table/board/list/gallery/calendar | H5 |
| `UI-SEARCH-001` | quick search preview/open | H6 |
| `UI-SEARCH-002` | in-page find and backlinks | H6 |
| `UI-COLLAB-001` | comment thread basic flow | H6 |
| `UI-SHARE-001` | Free basic share/public visible states | H6 |
| `UI-SB-001` | preview cancel = no mutation | H7/hard smoke |
| `UI-SB-002` | preview approve = one snapshot | H7/hard smoke |
| `UI-SB-003` | session attach/detach and revoke | H7/hard smoke |
| `UI-A11Y-001` | keyboard/focus/roles/names | H8 |
| `UI-RESP-001` | desktop/narrow viewport | H8 |
| `UI-PERF-001` | measured shell/editor/search latency | H8 |

각 scenario의 상태는 다음 중 하나다.

- `planned`;
- `exists`;
- `contract-tested`;
- `live-characterized`;
- `differential-passed`;
- `unknown`;
- `blocked`;
- `conditional`;
- `excluded`.

`unknown`은 실패가 아니지만 완료도 아니다. Conditional scenario는 Core 분모에 넣지 않는다.

---

## 10. 첫 구현 slice — UI1-FREE-DESKTOP-GOLDEN-JOURNEY

### 10.1 목표

한 개의 synthetic golden journey를 official Free reference와 isolated local app에서 실제 브라우저로 재생하고, 이후 UI 수정의 기준을 만든다.

여정:

```text
home
→ sidebar collapse/restore
→ search open/Escape/focus return
→ new page
→ Korean title + paragraph
→ save/reload
→ Second Brain preview
→ cancel and verify mutation 0
→ preview again
→ approve and verify one immutable synthetic publication
```

Official reference에는 Second Brain 동작이 없으므로 다음처럼 비교한다.

- `home → edit → save/reload`까지는 official/local differential;
- Second Brain preview/approve는 local product hard contract;
- 둘을 하나의 local golden journey에서 이어 사용자 흐름의 단절을 검증한다.

### 10.2 구현 전 단계

1. `AGENTS.md`, 이 문서, 최신 checkpoint를 완독한다.
2. `get_goal`을 확인하고 기존 goal을 교체하지 않는다.
3. active agent/file ownership을 확인한다.
4. 후보 파일의 size/mtime/SHA-256을 기록한다.
5. 기존 Node/Python focused baseline을 serial로 실행한다.
6. browser control이 official/local 모두에서 작동하는지 read-only smoke로 확인한다.
7. synthetic reference fixture와 local temp fixture를 준비한다.
8. 개인 workspace가 열리지 않았는지 육안 확인한다.

### 10.3 첫 slice 파일 소유권

새 파일 우선:

- Core UI scenario overlay;
- strict verifier/test;
- browser action/capture adapter;
- synthetic fixture builder;
- evidence manifest/redaction test;
- golden journey browser test.

기존 production UI 파일은 reference/local baseline이 고정된 뒤 필요한 최소 영역만 소유한다.

- `notion_static/index.html`;
- `notion_static/notion.css`;
- `notion_static/notion.js`;
- `tests/test_notion_ui_runtime.js`;
- `tests/test_notion_navigation_ui.js`;
- `tests/test_notion_publication_ui.js`.

첫 slice에서 기본적으로 수정하지 않는 파일:

- `notion_workspace_store.py`와 모든 frozen migration;
- `notion_store.py`, `routes_notion.py`, `routes_notion_workspace.py`;
- `app_code.py`, `auth.py`, `config.py`;
- `knowledge_core/**`, `docs/knowledge_spaces/**`, `tests/knowledge_spaces/**`;
- `AGENTS.md`;
- live data paths.

여정이 API gap을 발견하면 즉시 gap report를 작성하고 소유권을 다시 승인받는다. UI 수정 권한을 route/schema 변경 권한으로 확대 해석하지 않는다.

### 10.4 Acceptance

- real browser에서 실행되며 inert DOM stub만으로 통과하지 않음;
- deterministic synthetic page와 isolated local data root;
- shell/sidebar/topbar의 visual hierarchy와 active/hover/focus state capture;
- create/edit/reload exact content;
- horizontal overflow 0;
- Escape 뒤 focus가 원래 control로 복원;
- dirty page가 preview 전에 저장됨;
- cancel 뒤 knowledge mutation 0;
- approve 뒤 trusted OS temp의 isolated synthetic Second Brain fixture에만 exact one immutable publication과 matching source hash;
- live `knowledge/` 또는 Knowledge Spaces canonical store write 0;
- auth/route/publication 기존 tests green;
- official/local artifact redacted/hash-bound;
- reference와 다른 점이 있으면 숨기지 않고 diff ledger에 기록.

초기 visual tolerance는 임의 숫자로 만들지 않는다. 첫 approved synthetic reference/local pair에서 semantic region과 geometry metric을 측정한 뒤 reviewer가 threshold를 고정한다. state/focus/keyboard/security assertions는 exact다.

### 10.5 완료 후 다음 slice

Golden journey 증거가 확보되면 H3 `UI-SHELL-001/002/003`의 가장 큰 사용자-visible diff 하나를 고른다. 전면 `notion.js` refactor는 금지하고 behavior lock 뒤 cohesive region만 변경한다.

---

## 11. 이후 권장 vertical slices

| 순서 | Slice | 핵심 결과 |
|---:|---|---|
| 1 | `UI1-FREE-DESKTOP-GOLDEN-JOURNEY` | 실제 browser/evidence foundation |
| 2 | `UI2-WORKSPACE-SCOPED-SHELL` | A/B page scope, sidebar, breadcrumb, focus |
| 3 | `UI3-PAGE-TREE-INTERACTIONS` | expand/create/rename/nest/reorder/move |
| 4 | `UI4-EDITOR-COMMAND-CORE` | main/peek command convergence |
| 5 | `UI5-KOREAN-IME-CARET` | composition/caret/selection/clipboard |
| 6 | `UI6-SAVE-CONFLICT-RECOVERY` | autosave/reload/conflict/draft |
| 7 | `UI7-DATABASE-TABLE-PROPERTIES` | typed row/property/table |
| 8 | `UI8-DATABASE-VIEWS` | board/list/gallery/calendar/filter/sort |
| 9 | `UI9-SEARCH-BACKLINKS` | search/find/backlink navigation |
| 10 | `UI10-COMMENTS-SHARE` | Free basic collaboration/public states |
| 11 | `UI11-SB-DEEP-JOURNEYS` | publication/session/template/thought |
| 12 | `UI12-A11Y-RESPONSIVE-PERF` | release-quality polish |

각 slice는 사용자-visible 결과 하나, 필요한 backend contract, focused regression, 실제 browser evidence와 checkpoint를 함께 낸다. UI, API, schema를 무조건 모두 바꾸는 것이 아니다. 변화가 필요 없는 층은 hash unchanged와 회귀 test로 증명한다.

---

## 12. xhigh 실행 harness

### 12.1 새 턴 bootstrap

xhigh는 첫 턴 또는 compaction/resume 뒤 다음 순서를 그대로 따른다.

1. `code_assist_v1/AGENTS.md` 전체 읽기;
2. `get_goal` 확인;
3. 이 문서 전체 읽기;
4. 최신 UI-first checkpoint 읽기;
5. current agent/file ownership 확인;
6. checkpoint의 모든 input hash 재계산;
7. drift 분류;
8. 현재 slice, non-goal, acceptance ID와 stop condition을 commentary로 선언;
9. baseline test/evidence 실행;
10. 한 cohesive slice만 구현.

기존 goal이 `blocked`로 보이지만 사용자가 최신 메시지에서 재개를 지시한 경우, 그 불일치를 checkpoint에 기록한다. goal을 새로 만들거나 기존 goal을 완료 처리하지 않는다.

### 12.2 Drift 분류

- owned file drift: 즉시 stop, rebase/ownership 재확인;
- direct UI/SB dependency drift: read-only audit와 targeted baseline 재실행;
- unrelated Archive historical drift: 기록하되 Core UI를 자동 중단하지 않음;
- protected file drift: 읽기 전용으로 기록, 되돌리거나 덮어쓰지 않음.

### 12.3 작업 계획 형식

각 slice plan은 최소 다음을 포함한다.

- 사용자-visible outcome;
- reference scenario IDs;
- non-goals;
- existing behavior to preserve;
- owned/add/read-only/protected files;
- exact data fixture;
- browser action sequence;
- code change hypothesis;
- acceptance assertions;
- negative/recovery cases;
- test ladder;
- evidence artifacts;
- rollback;
- stop conditions;
- next exact command.

### 12.4 구현 규율

- action/DOM state를 먼저 측정하고 추측으로 CSS를 조정하지 않는다.
- `notion.js`와 `notion.css`의 전면 rewrite를 하지 않는다.
- 새 state가 server/canonical source와 모순되면 UI-only cache로 숨기지 않는다.
- backend 변경이 필요하면 transaction/IAM/idempotency/Second Brain 불변식을 유지한다.
- schema 변경은 필요한 slice에서만 forward-only migrator와 frozen checksum을 함께 낸다.
- request/startup migration은 금지한다.
- live flag enable과 운영 data apply는 금지한다.
- test를 통과시키기 위해 보호 경계나 redaction을 완화하지 않는다.

### 12.5 agent 병렬화

Ultra planning은 독립 감사를 병렬화할 수 있다. xhigh 구현은 다음 소유권을 권장한다.

- implementer 한 명: 해당 slice production files;
- reviewer 한 명: read-only P0/P1/interaction audit;
- evidence/test agent 한 명: 독립 test/evidence, production edit 금지.

같은 SPA 파일을 둘 이상의 agent가 편집하지 않는다. reviewer가 지적한 수정은 implementer에게 메시지로 전달하고, 파일 소유권을 이동하지 않는다.

### 12.6 한 번의 handoff

xhigh는 ultra가 이미 확정한 제품 범위, Archive 재분류와 H0~H9를 다시 설계하지 않는다. 새 evidence가 계약 충돌을 증명할 때만 decision record를 제안한다. 완료 시 다음 agent에게 다음 네 가지만 명확히 넘긴다.

- 무엇이 사용자-visible하게 바뀌었는가;
- 어떤 acceptance/evidence가 통과했는가;
- 무엇이 아직 unknown/blocked인가;
- 다음 exact slice와 command가 무엇인가.

---

## 13. 테스트 ladder

모든 명령은 repository root `G:\AI_Agent\DEMOS_CODEX`에서 serial로 실행한다. `--basetemp`를 직접 지정하지 않는다. `tests/conftest.py`가 격리한 trusted OS temp를 사용한다.

### L0 — pre-edit snapshot

- intended file size/mtime/SHA-256;
- goal/agent ownership;
- JSON/schema/document verifier;
- live/protected path 접근 0 확인.

### L1 — Node focused

```powershell
node code_assist_v1\tests\test_notion_editor_core.js
node code_assist_v1\tests\test_notion_ui_runtime.js
node code_assist_v1\tests\test_notion_navigation_ui.js
node code_assist_v1\tests\test_notion_publication_ui.js
node code_assist_v1\tests\test_notion_atomic_save.js
node code_assist_v1\tests\test_auth_csrf_frontend.js
```

### L2 — Python focused

```powershell
venv\Scripts\python.exe -m pytest `
  code_assist_v1\tests\test_notion_routes.py `
  code_assist_v1\tests\test_notion_atomic_save_compat.py `
  code_assist_v1\tests\test_notion_publication_adapter.py -q
```

### L3 — workspace/auth adjacent

```powershell
venv\Scripts\python.exe -m pytest `
  code_assist_v1\tests\test_notion_workspace_routes.py `
  code_assist_v1\tests\test_notion_workspace_navigation_routes.py `
  code_assist_v1\tests\test_notion_workspace_auth_isolation.py `
  code_assist_v1\tests\test_notion_workspace_pages.py -q
```

### L4 — real browser golden journey

- isolated local fixture;
- official synthetic reference;
- same action IDs;
- screenshot/state/focus/a11y/geometry capture;
- redaction/hash verification;
- repeatability.

### L5 — Second Brain hard regression

관련 existing route/publication/session tests를 먼저 실행한다. Knowledge Spaces regression은 해당 fixture가 OS temp에 격리되고 shared Knowledge files를 수정하지 않는다는 것을 확인한 뒤에만 실행한다.

### L6 — differential/a11y/viewport

- normalized state exact comparison;
- keyboard/focus exact comparison;
- a11y role/name/state;
- approved visual/geometry tolerance;
- 1440/1280/980/720 viewport.

### L7 — broader compatibility

- relevant Notion route/store/UI suites;
- public renderer/share/file;
- app import/host isolation;
- Second Brain publication/session/chat characterization.

Windows `0xC0000409`, empty-output timeout 또는 test process leak는 원 출력을 기록한다. 같은 wrapper를 반복 루프하지 않고 canonical interpreter로 serial 1회 재시도한다. 반복되면 host-dependent gate로 중단하며 pass나 product failure로 바꾸지 않는다.

---

## 14. Evidence와 checkpoint 계약

### 14.1 Evidence 상태

```text
exists
→ contract-tested
→ live-characterized
→ differential-passed
```

각 단계는 누적이다. Stub test는 browser proof를 대신하지 못하고 screenshot 하나는 keyboard/state proof를 대신하지 못한다.

### 14.2 Artifact 최소 필드

- scenario ID;
- reference/local;
- date/timezone;
- plan/role/platform/browser/version/locale;
- synthetic fixture alias;
- action log hash;
- normalized state hash;
- focus/a11y/geometry summary;
- screenshot hash;
- redaction report;
- reviewer;
- result/unknown/conflict;
- source/build hashes.

### 14.3 Checkpoint v2

각 slice는 unique JSON checkpoint를 만든다.

필수 구조:

```json
{
  "contract": "notion_ui_second_brain_checkpoint_v2",
  "recorded_at": "RFC3339",
  "run_id": "opaque-synthetic-id",
  "model": {"planning": "ultra", "implementation": "xhigh"},
  "parent_checkpoint": {"path": "...", "sha256": "...", "semantic_status": "historical"},
  "goal": {"thread_id": "...", "tool_status": "...", "user_resume_observed": true},
  "scope": {
    "primary_profile": "free-owner-chrome-desktop-ko-KR",
    "required_scenarios": [],
    "extension": [],
    "conditional": [],
    "excluded": []
  },
  "slice": {"id": "...", "status": "...", "acceptance_ids": []},
  "ownership": {"owned": [], "read_dependencies": [], "protected": []},
  "runtime": {
    "isolated_data_root": true,
    "synthetic_only": true,
    "live_local_data_accessed": false,
    "operational_migration_applied": false,
    "feature_flags_changed": false
  },
  "evidence": {"reference": [], "local": [], "differential": [], "second_brain": []},
  "commands": [],
  "tests": [],
  "claims": {
    "core_ui_complete": false,
    "interaction_parity_complete": false,
    "second_brain_regression_green": false,
    "archive_business_complete": false,
    "overall_notion_parity_complete": false
  },
  "remaining": [],
  "next_exact_command": "..."
}
```

### 14.4 VCS 사실

현재 workspace의 `.git`은 유효한 Git metadata repository로 인식되지 않는다. 따라서:

- `git init` 금지;
- commit/branch/landed 주장 금지;
- 다른 사용자의 변경을 reset/revert/cleanup 금지;
- 현재 인계는 path/size/mtime/SHA-256과 test evidence로만 고정.

Git provenance가 복구된 뒤에만 explicit owned paths를 stage하고 cached diff를 검토한다. `git add -A`와 `commit -a`는 사용하지 않는다.

---

## 15. Stop/no-go 조건

다음 중 하나면 즉시 중단하고 상태를 보고한다.

- 개인 또는 production 콘텐츠가 열리거나 capture됨;
- live `user_data`, `knowledge`, `.runtime`, session/workspace DB 접근이 필요함;
- isolated data root가 없음;
- owned file이 다른 agent에 의해 drift함;
- `AGENTS.md`, app/auth/config/Knowledge/Second Brain 보호 파일 수정이 필요하지만 ownership이 없음;
- mutation semantics나 권한 경계가 계획보다 크게 변함;
- auth/CSRF/Origin/zero-leak 또는 Second Brain publication regression;
- evidence를 안전하게 redaction할 수 없음;
- purchase/trial/upgrade/invite/external message/public posting이 필요하지만 별도 승인이 없음;
- schema/checksum/frozen migration 계약 불일치;
- request/startup migration, live flag, continuous consumer, resolver cutover가 필요함;
- 반복되는 host-dependent test failure;
- goal/ownership 충돌.

다음은 Core 작업 중단 사유가 아니다.

- Business/Enterprise workspace 부재;
- 공식 Archive를 Free에서 관찰할 수 없음;
- native/mobile/Enterprise scenario가 conditional/unknown;
- unrelated historical Archive file의 drift. 단, 기록은 한다.

---

## 16. 완료 정의

### 16.1 Slice 완료

- 사용자-visible outcome이 실제 browser에서 동작;
- acceptance IDs 전부 통과;
- negative/recovery/security/a11y 포함;
- existing behavior와 Second Brain regression green;
- evidence/redaction/hash/checkpoint 완료;
- known gaps와 다음 slice 명시;
- 운영 적용 없음.

### 16.2 Core Free Web 완료

- 채택된 Core scenario가 모두 `differential-passed`;
- conditional/extension/excluded가 분리되어 사용자 문서에 표시;
- 개인 데이터 evidence 0;
- Second Brain hard journeys green;
- responsive/a11y/performance budget 통과;
- canary/rollback/support 준비;
- 사용자가 승인한 Core 범위에 unresolved P0/P1 없음.

### 16.3 사용하면 안 되는 주장

다음은 별도의 전체 증거가 없으면 말하지 않는다.

- “Notion 완벽 복제”;
- “전체 Notion parity 완료”;
- “Business/Enterprise 동일”;
- “모바일/오프라인 동일”;
- “실시간 협업 동일”;
- “운영 migration 완료”.

허용되는 주장은 고정 profile과 scenario를 붙인다. 예:

> Free owner / Chrome desktop / ko-KR의 `UI-EDITOR-001`이 2026-08-XX reference build와 differential-passed다.

---

## 17. xhigh의 다음 턴 정확한 순서

2026-08-15 continuation override: 아래 bootstrap 목록은 이미 수행된 historical
순서이며 다시 구현하지 않는다. 현재 checkpoint를 재해시한 뒤, signed-in C0가
안전하게 복구되면 `REF-FREE-A/B` reference를 먼저 수집한다. 복구되지 않은
현재 실행에서는 H5 `UI-DB-003`, 전체 H6, H7 `UI-SB-001`부터
`UI-SB-003`, H8 `UI-RESP-001`까지 local-green이다. `UI-RESP-001`은 1440×900, 1280×800,
980×800, 720×800의 격리 Chrome 2회 반복, 전역 가로 overflow 0,
검색/공유 dismissal과 focus return, Second Brain delta 0으로 local-green이다.
다음 local slice는 `UI-A11Y-001`이며, 이후 `UI-PERF-001`을 측정한다. 각
slice는 기존 Second Brain hard regression과 identifier-free checkpoint를 유지한다.

1. `AGENTS.md`를 읽고 hash를 기록한다.
2. `get_goal`을 확인한다.
3. 이 문서, H1 implementation design과 최신 UI-first checkpoint를 읽는다.
4. agent/file ownership을 확인한다.
5. UI/SB files를 read-only inventory하고 hash를 기록한다.
6. 현재 알려진 `register_knowledge_core` subprocess stub drift를 단일 테스트로 재현하고, 소유권이 명확할 때만 test-only 호환 수정을 적용한다. `app_code.py`와 Knowledge Core는 수정하지 않는다.
7. L1→L3 baseline을 serial 실행한다.
8. Core UI overlay/verifier와 import-side-effect-free action/evidence/redaction library를 새 파일로 구현한다.
9. browser control connection을 read-only로 확인한다.
10. `REF-FREE-A/B` alias와 개인 workspace 금지 경계를 commentary로 재확인한다.
11. `UI1-FREE-DESKTOP-GOLDEN-JOURNEY` local/reference evidence를 수집한다.
12. 실제 reference/local diff가 나온 뒤에만 production UI의 첫 cohesive correction을 선택한다.
13. L1→L7, evidence redaction과 checkpoint를 완료한다.
14. 다음 slice를 한 문장 outcome과 exact command로 넘긴다.

이 순서는 xhigh가 ultra의 계획 작업을 다시 반복하지 않으면서도, 현재 shared tree의 동시 변경과 안전 경계를 재확인하게 만든다.

---

## 18. 현재 인계 요약

- Core 목표는 UI·interaction parity + Second Brain integration이다.
- Notion SaaS billing/Enterprise business logic 복제는 목표가 아니다.
- Free synthetic workspace 두 개면 시작에 충분하다.
- 개인 workspace는 절대 reference/fixture로 사용하지 않는다.
- Business trial은 official Archive optional pack을 실제 채택할 때만 검토한다.
- Backend safety substrate는 보존하되 UI 임계경로를 막지 않는다.
- local 실제 browser evidence는 수립되었고, signed-in Free reference와 differential evidence가 가장 큰 현재 공백이다.
- H3 shell/tree, H4 editor, H5 database, H6 search/comment/share, H7 Second Brain hard journeys와 H8 `UI-RESP-001`은 local-green이며, 다음 local slice는 `UI-A11Y-001`이다.
- Second Brain은 모든 UI slice의 hard regression이다.
- 운영 data, migration, flag, consumer, resolver cutover는 여전히 미승인이다.
- `.git`이 유효하지 않으므로 hash/evidence/checkpoint로만 인계한다.

---

## 19. 2026-08-15 H8 accessibility continuation

`UI-A11Y-001` is now isolated-local real-Chrome green. The adopted keyboard
journey covers 36 visible native/composite tab targets and requires exact DOM
tab order, an accessible name/role/state for every target, and a visible focus
indicator for every target. Search and the canonical Second Brain preview
have forward/reverse focus traps. Search, share, and an unlocked icon-picker
surface restore the exact trigger on Escape. The intentionally locked Second
Brain preview exits through its explicit keyboard Cancel action and restores
the publication trigger.

The product correction is limited to stable accessible names, adopted
`:focus-visible` styling, visible-modal focus trapping, focus-restoring Escape
for unlocked modals, reduced-motion behavior, and forced-colors focus. Routes,
schema, migrations, auth, runtime flags, resolver, Knowledge Core, and Second
Brain production services remain unchanged.

Current evidence at this boundary:

- focused repeated Chrome journey: `1 passed, 20 deselected`;
- complete isolated Chrome suite: `21 passed`;
- six Node UI/editor/navigation/publication/atomic-save/CSRF contracts: pass;
- Notion route/storage/publication: `25 passed`;
- Second Brain hard regression: `5 passed`;
- plan/evidence/redaction/reference guards: `90 passed`;
- accessibility report: 36/36 visible focus indicators, two modal focus
  traps, zero external requests, zero Second Brain item/revision delta;
- scenario denominator: 25 required, zero promotions.

The exact result is recorded in
`docs/notion_parity/core_free_web_ui/UI_A11Y_001_LOCAL_ACCESSIBILITY_RESULT_20260815.md`.
`UI-A11Y-001` remains `planned` until a safe redacted `REF-FREE-A/B`
observation and reviewed differential exist. The next local slice is
`UI-PERF-001`, using real-Chrome repeatability and measurement-only latency
reporting; no threshold may be invented before calibration. Signed-in
reference characterization remains higher priority whenever the in-app Chrome
control surface can be initialized safely without opening personal content.

---

## 20. 2026-08-15 H8 performance continuation

`UI-PERF-001` now has an isolated-local measurement baseline. Two independent
OS-temporary roots each run three fresh Chrome contexts over the same
pre-created synthetic page. The measured actions are login-to-shell,
existing-page open, editor input acknowledgement, save, reload/restore, search
open, search result, and Second Brain preview readiness.

The six-sample p50 values are 638.066ms, 78.676ms, 43.598ms, 1380.734ms,
238.975ms, 85.672ms, 14.632ms, and 904.201ms respectively. These are
precalibration measurements, not budgets. The complete current-source Chrome
suite plus the performance journey is `22 passed`; Node, route/storage, and
Second Brain regression ladders are green. The identifier-free report records
zero external requests and zero Second Brain item/revision delta.

The exact result is recorded in
`docs/notion_parity/core_free_web_ui/UI_PERF_001_LOCAL_MEASUREMENT_RESULT_20260815.md`.
`UI-PERF-001` remains `planned`; no scenario is promoted without a reviewed
reference pair or explicitly approved local budget. The next phase is H9:
isolated synthetic pilot, rollback/recovery rehearsal, and a requirement-level
completion audit. Reference characterization remains an unresolved gate.

The 90-test evidence/reference-guard bundle was green before the independent
performance test was added. Two identical post-performance executions ended
with Windows host status `0xC0000409` before useful output, so that gate is
recorded as host-dependent and was not looped further. The compact verifier is
green with 25 required scenarios and zero promotions.

---

## 21. 2026-08-15 H9 isolated pilot/recovery continuation

The offline H9 foundation is now green without registering a production flag,
route, worker, migration, or resolver. A side-effect-free pilot module requires
explicit opt-in, exact readiness, synthetic-only confirmation, and a disengaged
kill switch before path inspection. Backup and restore are copy-on-write into
new trusted temporary roots; the changed canary root is retained.

The real-Chrome rehearsal saves a baseline page, proves preview cancellation
has zero publication delta, snapshots a quiesced 15-file tree, exercises
kill-switch denial, applies a canary edit and exactly one Second Brain
publication, then restores another root and verifies the baseline body plus
zero publication items/revisions. Unit/import and browser contracts are
`8 passed`.

Result and operations documents:

- `docs/notion_parity/core_free_web_ui/UI_H9_ISOLATED_PILOT_RECOVERY_RESULT_20260815.md`;
- `docs/notion_parity/core_free_web_ui/CORE_FREE_WEB_UI_PILOT_RUNBOOK.md`.

This is not H9 or Core completion. The production default-off flag/readiness
endpoint/kill switch, live canary, operational backup/restore, formal
legal/brand/accessibility approval, approved performance budget, and all
`REF-FREE-A/B` differentials remain pending or unauthorized. The next highest
value action is safe signed-in reference characterization. Until browser
control is available, only read-only completion auditing and host-gate
diagnosis may proceed; local feature work must not manufacture parity claims.

---

## 22. 2026-08-15 requirement-level completion audit

`docs/notion_parity/core_free_web_ui/completion_matrix.v1.json` binds the exact
25-scenario denominator, current scenario plan, latest H9 checkpoint, and each
local evidence document by SHA-256. Its recomputed snapshot is:

- 25 required Core scenarios;
- 24 `local_real_browser_green` scenarios;
- one `local_measurement_green` performance scenario;
- zero reference-characterized scenarios;
- zero differential-passed scenarios; and
- zero promoted scenarios.

`tools/verify_notion_core_free_ui_completion.py` is a read-only,
import-side-effect-free fail-closed verifier for that snapshot. It rejects
duplicate/non-finite JSON, denominator or phase drift, forged counts, unsafe
paths, evidence/hash drift, invented reference/differential promotion, and
false completion claims. A green verifier result is deliberately reported as
`completion_state=incomplete`.

The next implementation agent must therefore treat the local H3-H8 behavior
and isolated H9 recovery foundation as established inputs, not as parity
completion. The next product-changing slice may start only after a synthetic
`REF-FREE-A/B` observation produces a concrete differential. Until a safe
signed-in browser transport exists, only bounded read-only host diagnosis and
evidence integrity work are authorized; personal workspaces remain forbidden.

---

## 23. 2026-08-20 manual reference intake continuation

The signed-in Chrome control path still fails before browser or tab selection.
No Notion workspace was opened. To avoid making the automation transport a
single point of failure, the repository now includes a human-observed reference
intake that preserves the same safety and evidence boundary:

- `tools/notion_parity/ui_reference_intake.py`;
- `docs/notion_parity/core_free_web_ui/CORE_FREE_WEB_UI_MANUAL_REFERENCE_INTAKE_RUNBOOK.md`;
- `tests/test_notion_ui_reference_intake.py`.

The tool derives an identifier-free worksheet from the verified scenario
plan, evaluates an explicit C0 observation, emits a deliberately incomplete
submission draft, and accepts a completed submission only when every declared
capture point, action hash, source-build hash, redaction rule, and operator
safety attestation is exact. It refuses to read a draft/submission without an
explicit synthetic-only confirmation. Its receipt is non-promoting and
path-free.

This is an alternative transport for real human observation, not synthetic
reference evidence by itself. The first allowed use is the read-only
`UI-SHELL-002` collapse/restore reference. Personal content, raw browser
artifacts, invite/public/plan actions, and Notion execution of Second Brain
actions remain forbidden. The full adjacent intake/C0/evidence/plan/completion
bundle is 82 green.

---

## 24. 2026-08-22 owner-approved R1–R4 continuation

The Chrome transport is operational for the two synthetic Free workspaces.
The owner approved the redacted summaries for `UI-SHELL-001`,
`UI-EDITOR-001`, `UI-EDITOR-002`, `UI-EDITOR-005`, `UI-DB-001` through
`UI-DB-003`, `UI-SEARCH-001`, `UI-SEARCH-002`, `UI-COLLAB-001`, and
`UI-SHARE-001`. The owner also approved the local-only evidence policy for
`UI-SB-001` through `UI-SB-003`. Those approvals authorize current-source
revalidation and bounded product corrections; they do not authorize reference
promotion or completion-matrix mutation.

R1 through R3 are now `implemented_current_source_green_review_pending`:

- the first workspace visit focuses the workspace sidebar group while return
  navigation restores the workspace switcher;
- page entry distinguishes page-canvas, page-editor-group, page-title, and
  preserve-focus policies without stealing the active text caret;
- search exposes listbox/option semantics and restores or preserves focus by
  exit path;
- the share surface starts at its first input and restores the trigger on
  Escape;
- side peek is a labelled region, begins at its title, and preserves the
  ambient focus context when promoted to a full page;
- `UI-SB-001` cancel remains mutation-zero, `UI-SB-002` commits exactly one
  immutable synthetic publication and replays idempotently, and `UI-SB-003`
  attaches/detaches only while current authorization holds.

R4 used one read-only native Tab cycle in `REF-FREE-A` to establish a concrete
accessibility differential. Official Notion begins keyboard traversal with a
focus-visible `콘텐츠로 건너뛰기` link. The local shell now provides the same
first-entry control and a programmatically focusable native `<main>` target.
No reference screenshot, raw DOM, network payload, cookie, storage, personal
content, or external side effect was persisted. The redacted observation is
bound by
`checkpoints/ui-a11y-001-reference-native-tab-skip-link-green-20260822.json`;
owner review of that new summary remains required before any promotion claim.

`UI-PERF-001` also gained five warm search-dialog samples in the same synthetic
Chrome tab: `901, 317, 329, 322, 328 ms` from click to visible workspace search
combobox. The median is `328 ms`, but these values mix browser-control overhead
and cache state. They are observation-only and do not establish a release SLO,
percentile, or parity claim. An independent create measurement, a safely
observable reference release binding, and an approved product budget remain
open.

`UI-EDITOR-003` now has a trusted plaintext paste observation without reading
the OS clipboard or clipboard history. A known synthetic string was written
only to the browser-session clipboard. Paste created exactly one plaintext
block, undo reduced the marker count to zero, and redo restored it to one. The
synthetic marker was removed and a reload proved the original `REF-FREE-A`
textbox shape with zero residual markers. The redacted summary still requires
owner review before reference-category promotion.

Current machine truth remains deliberately incomplete:

- required scenarios: `25`;
- local real-browser green: `24`;
- local measurement green: `1`;
- reference characterized and differential-passed: `5`;
- promoted: `0`.

The next evidence slices, in order, are:

1. obtain owner review of the redacted `UI-A11Y-001` native-Tab summary and
   `UI-EDITOR-003` session-clipboard summary;
2. finish `UI-EDITOR-004` native Korean composition observation;
3. finish `UI-EDITOR-006` safe failed-save/stale-revision reference evidence;
4. finish official `UI-RESP-001` viewport measurements;
5. complete the remaining `UI-PERF-001` identity/create/budget gates.

Do not change the completion matrix merely because local tests are green.
Open another product-code slice only after a new synthetic reference
observation proves a concrete product difference. The exact integrity commands
for every resume are:

```powershell
venv\Scripts\python.exe -m code_assist_v1.tools.verify_notion_core_free_ui_review_gate --compact
venv\Scripts\python.exe -m code_assist_v1.tools.verify_notion_core_free_ui_completion --compact
```

---

## 25. 2026-08-22 approval reaffirmation and independent create harness

The owner reaffirmed the exact 11-scenario reference-summary approval and the
three-scenario local-only Second Brain evidence policy recorded in
`owner_approval_receipt.20260822.json`. This reaffirmation does not extend to
`UI-A11Y-001`, `UI-EDITOR-003`, or native Korean IME evidence and does not
authorize completion-matrix mutation.

The local `UI-PERF-001` harness now measures `create_page_ready_ms` as an
independent action. Before the action it records the complete synthetic page
identity set. Readiness requires the title to receive focus, the creation
status to appear, the tree count to increase by exactly one, exactly one new
identity to exist, and that identity to be the single active tree row. A
deterministic synthetic title is committed before the rest of the sample.
Both preparation and timed journeys require the explicit application boot
signal `body[data-notion-ready='true']`, avoiding a pre-listener click race.

The final isolated run covered two temporary roots and three samples per root
and passed (`1 passed in 68.63s`). The two preceding failures are retained in
the checkpoint because they exposed the missing boot guard; neither touched
live or personal data. The evidence is bound by
`checkpoints/ui-perf-001-local-independent-create-harness-green-20260822.json`.

This closes only the local independent-create measurement ambiguity. It does
not establish an official Notion create sample, a safe reference release
identity, a product budget, an SLO, or differential parity. The Browser
extension transport reported zero connected tabs when the reference follow-up
was attempted. A later read-only Windows Computer Use inventory proved that
Chrome itself had two open windows and exactly one synthetic window titled
`REF-FREE-A | Notion - Chrome`; therefore the earlier zero count must not be
interpreted as the browser or tab being closed. Computer Use stopped before
capturing or changing page state because it could not determine the browser URL
with enough confidence for its policy gate. No URL or workspace was guessed and
no reference mutation was performed. Resume the measurement after the existing
synthetic tab is connected to the Browser extension transport; reopening the
tab is not required if it is already visible in Chrome.

---

## 26. 2026-08-22 reference independent-create identity checkpoint

The existing external Chrome tab was recovered without reopening it by using
the Browser `user.openTabs()` discovery and claiming the single exact
`REF-FREE-A | Notion` result. Other tabs were not opened or inspected.

The reference New-page control first exposes a type chooser (`Page`, `Chat`,
`AI note`, and `Database`). The complete Page choice did create a distinct
page: durable reobservation proved a different in-memory URL identity, exactly
one additional sidebar tree item, and exactly one committed synthetic title
`UI-PERF-001-REFERENCE-CREATE`. No raw URL or page identifier was persisted.

The page-type click returned a pointer-dispatch timeout after the SPA replaced
the clicked target. Because the result was proved only by the subsequent
durable observation, no click-to-ready duration from this attempt is accepted
as a performance sample. It closes the reference identity ambiguity only; the
timing, release binding, and reviewed budget gates remain open.

The created synthetic page is intentionally still present while action-time
confirmation for a cloud Trash operation is pending. Resume only after the
owner supplies `UI-PERF-001-REFERENCE-CREATE 휴지통 이동 승인`; then move that
one page to Trash, prove the original tree count and zero title residual, and
record a cleanup-complete successor checkpoint. The current non-promoting
state is bound by
`checkpoints/ui-perf-001-reference-independent-create-pending-cleanup-20260822.json`.
