# NP-2B server-backed navigation state 설계

기준 시각: 2026-08-14 Asia/Seoul  
상태: **NP2B-3 UI contract-tested, default off; Recents/resolver/live migration disabled**
첫 vertical slice: sidebar preference + page favorites  
후속 slice: recents/view events, tree interaction, 실제 UI

## 1. 목적과 reference 경계

NP-2B는 workspace switcher, sidebar, Favorites, Recents와 page tree interaction을 server-backed
상태로 만든다. 첫 slice는 resolver cutover나 legacy source mutation 없이도 독립적으로 구현 가능한
사용자별 sidebar preference와 favorite locator를 canonical workspace catalog에 저장한다.

2026-08-13 기준 공식 도움말은 다음을 reference-only로 뒷받침한다.

- [Navigate Notion with the sidebar](https://www.notion.com/help/navigate-with-the-sidebar):
  Home에 Recents, Favorites, Teamspaces, Shared, Private 등이 있고 sidebar와 섹션을
  접고 펼치거나 너비를 바꿀 수 있으며 표시 개수·순서·숨김을 사용자화할 수 있다.
- [Intro to workspaces](https://www.notion.com/help/intro-to-workspaces): favorite한 page가
  Favorites 섹션에 나타나며 다시 제거할 수 있다.
- [Manage your Library](https://www.notion.com/help/manage-your-library): Recents와 Favorites가
  workspace content 탐색 섹션이며 bulk remove 같은 Library 작업이 존재한다.

공식 문서는 persistence 시점, multi-device conflict, exact ordering, retention, revoke 뒤 preference
보존 여부를 충분히 정의하지 않는다. 해당 세부는 live characterization 전까지 local contract로만
표현하며 Notion differential parity로 승격하지 않는다.

## 2. 현재 기반과 의존성

이미 존재하는 기반:

- workspace schema `2026081310`; frozen source `2026081309`
- current membership/teamspace authorization
- active page source identity `(workspace, owner, page)`
- exact operation replay와 request hash conflict
- immutable operation/audit/outbox transaction
- no-directory-scan page list overlay
- canonical attestation/readiness와 full rebind foundation

이 slice가 의존하지 않는 미완료 항목:

- H5e source v4/destructive acknowledgement
- continuous source consumer
- fail-closed resolver cutover
- legacy page create/move/archive mutation
- actual sidebar UI

따라서 schema/API는 구현할 수 있지만 UI activation과 resolver-dependent page content는 뒤로 둔다.

## 3. 범위 결정

### NP2B-0 — 이 설계와 machine contract — 완료

- current source/API/schema audit
- reference-only behavior와 unknown 분리
- workspace 1310 후보와 API/fault/privacy gate
- contract v3는 backend schema/store API와 protected route overlay가 구현됨으로 승격했다.
- `routes_enabled=true`, `ui_enabled=false`, `recents_write_enabled=false`,
  `resolver_cutover_enabled=false`는 유지한다.

### NP2B-1 — workspace 1310 navigation foundation — contract-tested

- user navigation state
- sidebar section preferences
- page favorites
- explicit 1309→1310 migrator/CLI
- store API와 focused tests
- NP2B-1 자체는 route/UI 미등록 backend checkpoint로 검증 완료
- frozen 1309→1310 explicit migrator와 dry-run 기본 CLI
- current-member read-time defaults, sidebar preference CAS, generation-stable favorite add/remove
- canonical operation/audit/outbox replay와 deep-health correlation

### NP2B-2 — protected workspace route overlay — contract-tested

- GET navigation snapshot
- PATCH sidebar preferences
- PUT/DELETE favorite
- current membership/CSRF/Origin/no-cache/strict JSON
- page title/body/path/token은 응답하지 않음

### NP2B-3 — UI integration

- server snapshot bootstrap
- optimistic local interaction + operation replay
- stale response fencing
- keyboard/focus/narrow viewport/a11y
- feature flag default off

구현/검증 상태: `implemented_contract_tested_default_off`. UI 코드와 route capability는 설치됐지만
enable 환경 변수의 기본값은 false이고 이 작업에서 실제 환경 flag를 켜지 않았다.

NP2B-3의 동결된 실행 계약은 다음과 같다.

- 서버가 `capabilities.navigation_ui=true`를 반환할 때만 canonical navigation UI를 사용한다.
- `CODE_ASSIST_NP2B_NAVIGATION_UI`는 정확히 `1`일 때만 enable 요청이며 기본값은 off다.
- `CODE_ASSIST_NP2B_NAVIGATION_UI_KILL_SWITCH`는 empty 또는 정확한 `0`만 비활성 상태이며 그 외 값은
  fail-closed로 UI를 끈다. 따라서 `1`과 잘못된 비영 값 모두 enable보다 우선한다.
- flag가 꺼져 있거나 catalog가 unavailable이면 기존 local sidebar/favorite 동작을 그대로 유지한다.
- flag가 켜진 뒤 받은 server snapshot은 local cache보다 우선한다.
- snapshot의 subject/workspace identity를 현재 사용자·선택 workspace와 exact 비교하고 duplicate workspace
  identity 또는 malformed canonical payload는 UI만 fail-closed로 끄되 legacy content boot는 막지 않는다.
- canonical workspace switcher에는 active member workspace만 표시하며 guest workspace는 제외한다.
- 이 slice는 navigation preference/favorite의 workspace만 전환한다. page content resolver는 legacy를 유지한다.
- workspace 전환 전 main/side-peek draft를 모두 flush하고 실패하면 전환하지 않는다.
- workspace/generation sequence로 늦은 응답을 버리고 navigation mutation은 한 번에 하나만 허용한다.
- network uncertainty에는 같은 operation ID로 한 번만 replay를 시도한다. offline queue는 만들지 않는다.
- mutation 실패 뒤 canonical reload가 성공하면 그 상태를 적용하고, reload도 실패하면 optimistic state를
  이전 snapshot으로 rollback한다.
- capability가 authoritative지만 eligible member workspace가 0개이면 local favorite cache를 다시 노출하지
  않는다. navigation GET이 membership 403/404로 실패하면 canonical favorite state를 fail-closed로 비운다.
- 활성화 후 mutation/workspace switch 직전 capability recheck가 실패해도 canonical favorite state를
  fail-closed로 비우며 offline queue 또는 stale local fallback을 만들지 않는다.
- section order/collapse/hidden 조작은 keyboard로 가능하고 rerender 뒤 같은 control로 focus를 복원한다.
- resize separator는 기존 Arrow/Home keyboard 계약을 유지하고 narrow viewport에서는 drag resize를 숨긴다.
- Recents write, tree lifecycle, directory scan, resolver cutover, live migration과 flag enable은 여전히 금지한다.

### NP2B-4 — recents/view events

view event는 빈도가 높고 every-mutation audit/outbox invariant와 retention/privacy 비용이 크다.
아래 결정 전에는 recents write를 구현하지 않는다.

- immutable audit/outbox retention 기간과 purge authority
- 동일 page 반복 view coalescing 규칙
- offline/multi-device ordering
- view event operation ID와 replay window
- revoke/archive/delete 뒤 보존/숨김 정책
- telemetry와 canonical recent state의 분리

### NP2B-5 — tree interaction

- expanded-node preference
- keyboard/focus state
- drag preview와 drop command는 NP-2C lifecycle command 사용
- tree parent/order 자체는 preference가 아니라 canonical page lifecycle state

## 4. workspace 1310 구현 계약

- version: `2026081310`
- name: `notion_workspace_v2_np2b_navigation_state`
- prerequisite: exact frozen `2026081309`
- 기존 1309 SQL/checksum/object hash는 독립 상수로 동결한다.
- request/startup migration은 금지한다.
- migration은 existing workspace/member/source/projection/attestation/audit/outbox를 byte-preserve한다.
- default state는 row를 fabricate하지 않고 read-time defaults로 제공한다.

### 4.1 `workspace_navigation_states`

구현 필드:

- `workspace_id`, `subject_id` primary key
- `preference_version`, `favorite_version`
- `sidebar_width_px`
- `sidebar_collapsed`
- `created_at`, `updated_at`
- `last_actor_subject_id`, `last_operation_id`
- deferred operation FK와 membership FK

제약:

- active workspace member만 생성/변경 가능
- guest는 workspace shell navigation state를 갖지 않음
- width는 reviewed bounded range만 허용
- version은 해당 command family에서 exact `+1`
- membership revoke 중 state를 즉시 삭제할지 숨겨 보존할지는 characterization 전까지
  **숨겨 보존**으로 고정한다. reactivation 뒤 복원되지만 API는 revoked 동안 404다.

### 4.2 `workspace_sidebar_section_preferences`

후보 필드:

- workspace, subject, section key
- display order
- hidden, collapsed
- item limit
- updated timestamp와 last operation correlation
- primary key `(workspace,subject,section)`
- unique `(workspace,subject,display_order)`

첫 allowlist:

- `favorites`
- `recents`
- `teamspaces`
- `shared`
- `private`

Agents, meetings, inbox, apps 같은 섹션은 이 bounded context가 소유하지 않으며 table에 넣지 않는다.
unsupported section은 저장하지 않고 validation error다.

section preference replace command는 allowlisted 전체 집합을 canonical order로 받고 한 transaction에서
replace한다. omitted section의 의미는 `default`이지 implicit delete가 아니다. explicit `hidden`을 쓴다.

### 4.3 `workspace_page_favorites`

후보 필드:

- workspace, subject, source owner, source page primary key
- stable favorite ordinal
- created/updated timestamp
- last actor/operation correlation
- FK to navigation state
- FK to canonical page-source identity, generation에는 결속하지 않음

favorite는 safe healing/rebind generation을 따라야 하므로 binding version을 key로 쓰지 않는다.
현재 target visibility는 매 read 때 재계산한다.

- add: target이 caller에게 현재 visible active binding이어야 함
- remove: caller가 소유한 preference row는 target이 더 이상 visible하지 않아도 제거 가능
- list: current visibility를 잃은 stale favorite는 존재를 노출하지 않고 생략
- access가 복원됐을 때 stale favorite를 다시 표시할지는 characterization pending이다. 초기 local contract는
  row를 보존하고 current visibility가 회복되면 다시 표시한다.
- binding이 `source_deleted/legacy_unknown`으로 permanent quarantine됐을 때는 별도 cleanup command 전까지
  row를 보존하되 API에서 숨긴다.

## 5. raw SQL과 ledger 불변식

모든 canonical mutation은 operation/audit/outbox와 같은 transaction에서 commit한다.

### connection-scoped context

구현 UDF:

- `workspace_navigation_actor()`
- `workspace_navigation_operation_id()`
- `workspace_navigation_action()`

default는 empty/fail-closed다. navigation mutation handler만 transaction 동안 exact actor/op를 설치하고
`finally`에서 복원한다.

### trigger 계약

- state/section/favorite INSERT/UPDATE/DELETE는 exact context 없으면 거부
- actor는 target subject와 같아야 함
- operation ID는 deferred FK의 immutable `workspace_operations`와 같아야 함
- current active membership 없으면 거부
- version delta, section allowlist/order, favorite target workspace identity를 raw SQL에서도 검사
- `INSERT OR REPLACE`, UPSERT를 통한 immutable correlation 우회 거부
- deep health는 latest row와 operation/audit/outbox action/target/payload byte equality를 검사

operation action 후보:

- `workspace.navigation_preferences_updated`
- `workspace.page_favorite_added`
- `workspace.page_favorite_removed`

required capability는 `workspace.member`, strength `10`이다. replay도 current membership을 다시 확인한다.

## 6. store API

### 6.1 `get_navigation_state(subject, workspace)`

- current active member만 허용; non-member/guest/revoked는 404
- source DB/filesystem/network I/O 0
- default 또는 stored sidebar preferences
- current-visible favorites만 반환
- identifier 외 title/content/path/token은 반환하지 않음
- response: preference/favorite versions, sections, favorite locators, membership epoch
- `Cache-Control: no-store`

### 6.2 `replace_sidebar_preferences(...)`

입력:

- actor/workspace
- `expected_preference_version`
- bounded sidebar width/collapsed
- canonical complete section list
- operation ID

동작:

- exact replay probe 뒤 current membership 재인가
- one writer transaction에서 state CAS와 section replace
- preference version만 `+1`; favorite/recent version은 불변
- operation/audit/outbox atomic
- workspace row version/security epoch는 변경하지 않음

### 6.3 `add_page_favorite(...)`

입력:

- actor/workspace/owner/page
- expected favorite version
- optional exact ordinal 또는 append
- operation ID

동작:

- current active membership과 target visibility를 같은 catalog transaction에서 확인
- duplicate semantic add는 changed=false exact operation으로 수렴
- favorite version은 **실제 state change에서만 +1**한다. duplicate semantic add는 changed=false와
  현재 version을 새 operation/audit/outbox commitment로 기록하고 exact replay는 prior result를 반환한다.
- target absence/invisibility는 404

### 6.4 `remove_page_favorite(...)`

- current member와 caller-owned row만 확인
- target page authorization을 다시 요구하지 않음
- missing row는 changed=false
- target identifier 외 metadata를 반환하지 않음
- exact replay/current membership 재인가

### 6.5 `reorder_page_favorites(...)`

NP2B-1에서는 제외할 수 있다. 구현한다면 full current-visible set이 아니라 caller-owned persisted favorite
전체 exact set을 요구해야 숨겨진 stale row를 우연히 삭제하지 않는다. 안전한 초기안은 one-item move with
bounded rank renumbering이다.

## 7. NP2B-1 구현 증거와 현재 금지 경계

구현 파일:

- `notion_workspace_store.py`: 1310 DDL, route-unregistered store API, exact replay validator,
  raw/service-context trigger와 deep health
- `notion_workspace_navigation_migration.py`: exact frozen 1309→1310 explicit migration
- `tools/migrate_notion_workspace_navigation.py`: dry-run 기본 CLI
- `tests/test_migrate_notion_workspace_navigation.py`
- `tests/test_notion_workspace_navigation.py`

frozen schema:

- 1309 source checksum: `sha256:ecc2a8b40e8d114015e05b1772701dd97bb99ac513db9fd041626408f5f127ec`
- 1309 source actual schema: `sha256:d79ecc5b79faacf3ca4db288e7978b8192df91b8c3f9e1d5a99f0d859e2e06b1`
- 1310 target checksum: `sha256:72058812be6f0b92db756d5648d2cd9a0741d4ad4f4f9c62377d128ca0b581b3`
- 1310 target actual schema: `sha256:d8935af134fa12cdd5f0f803e15cba798dece1831cfeeee7673e0dc0ef7d7b33`

2026-08-14 격리 TEMP에서 새 migration 6개, navigation 14개, store와 합친 32개,
pages/routes 14개, locator/outbox 37개, 1303→1310 migration 구성요소 96개 통과와 1개
platform skip을 확인했다. 이 수치는 live characterization이나 운영 적용 증거가 아니다.
최종 파일 해시와 중복을 제거하지 않은 regression lane은
`docs/notion_parity/checkpoints/np2b1-workspace-navigation-backend-20260814.json`에 고정한다.

NP2B-2는 같은 날 별도 owned-file slice로 진행했다. navigation route/store/migration/machine-contract
focused lane 75개, legacy workspace/Notion route·publication·Second Brain 인접 lane 91개,
store/navigation/migration-chain lane 41개가 격리 TEMP에서 통과했다. workspace overlay는 현재
12 rules, 19 operations, 19 hard operations이며 legacy 59/61/15 surface는 변하지 않았다. 이 증거는
`docs/notion_parity/checkpoints/np2b2-workspace-navigation-routes-20260814.json`에 고정하며 live 적용이나
제품 differential pass를 뜻하지 않는다.

NP2B-2 추가 검증:

- route 4개 등록과 strict machine contract 검증 완료
- workspace overlay contract 12 rules/19 operations/19 hard operations 통과
- navigation focused route, legacy workspace route, auth/Origin/CSRF negative matrix 통과
- independent P0/P1 audit에서 duplicate-key/non-finite acceptance와 전체-body 선materialization을 발견해
  strict parser와 64KiB streaming bound로 닫았다. 현재 P0/P1 잔존은 확인되지 않았다.
- audit/hardening 이후 해시와 분리 실행 evidence는
  `docs/notion_parity/checkpoints/np2b2-workspace-navigation-routes-audit-20260814.json`에 고정한다.

현재 계속 금지되는 항목:

- UI/feature flag 활성화
- Recents write와 retention/purge
- resolver cutover
- request/startup migration
- 운영 catalog migration

## 8. route contract 구현

- `GET /api/notion/workspaces/<workspace_id>/navigation`
- `PATCH /api/notion/workspaces/<workspace_id>/navigation/preferences`
- `PUT /api/notion/workspaces/<workspace_id>/navigation/favorites`
- `DELETE /api/notion/workspaces/<workspace_id>/navigation/favorites`

favorite locator를 URL path에 넣지 않고 JSON body에 둬 access log 노출을 줄인다.

공통:

- Notion host overlay에만 등록
- mutation은 existing session/CSRF/Origin 계약 사용
- strict JSON, unknown key 거부
- `application/json`만 허용하고 64KiB `limit+1` read, duplicate key, non-finite number를 파싱 단계에서 거부
- operation ID required
- 404 non-member/invisible, 409 version conflict, 400 malformed input
- response/cache headers는 existing workspace overlay와 일치
- `app_code.py`, bridge, Second Brain 등록 없음

## 9. privacy와 zero-leak

- GET snapshot은 current-visible favorites만 포함
- hidden favorite 때문에 total count, ordinal gap, teamspace/source owner 존재를 추론할 수 없게 public list를
  dense reindex한다.
- audit/outbox에는 title, icon, body, path, filename, raw public token이 없음
- source owner/page locator는 internal canonical identifier로만 저장
- logs에는 request body/locator를 기록하지 않음
- remove는 stale target metadata를 읽지 않음
- navigation snapshot은 legacy directory scan과 source DB open을 하지 않음

## 10. transaction/fault matrix

| Fault | Expected |
|---|---|
| state insert/update fault | preference/favorite/audit/outbox/op 모두 0 |
| section replace 중간 fault | prior complete section state 유지 |
| favorite row insert/delete fault | version과 ledger 불변 |
| operation/audit/outbox fault | domain row/version rollback |
| response loss after commit | replay before mutation, same result |
| operation ID changed request | conflict, write 0 |
| membership revoke before handler | 404, write 0 |
| membership revoke cannot race inside writer | writer transaction serializes/rechecks |
| teamspace access revoke | favorite row 보존, GET에서 hidden |
| target quarantine | favorite row 보존, GET에서 hidden |
| target safe rebind generation | same favorite identity 유지 |

## 11. migration tests

- exact 1309 source constants/objects
- 1310 target literal checksum/actual hash
- fresh 1310 == migrated 1310
- old 1308→1309 migrator imports/runs with runtime 1310
- existing workspace/member/source/projection/attestation/ledger byte preservation
- no fabricated navigation rows
- dry-run deterministic/read-only
- backup/path/sidecar/link/TOCTOU/ambiguous outcome
- already-current idempotence
- 1303→…→1309→1310 chain
- request/startup import boundary

## 12. API/security tests

- default state without row creation
- member only; guest/non-member/revoked 404
- strict input/width/section/order/item-limit bounds
- preference and favorite family CAS independence
- exact replay and operation hash conflict
- add invisible target 404 with existence leak 0
- remove hidden stale favorite succeeds without target lookup
- teamspace revoke/quarantine hides row and dense-reindexes response
- safe generation rebind preserves favorite
- operation/audit/outbox exact action/payload/auth correlation
- raw SQL/UDF missing/actor mismatch/op mismatch/OR REPLACE rejected
- every fault point rollback
- source resolver/filesystem call count 0 for navigation snapshot
- import does not load app/bridge/auth/config/data_paths/Knowledge/Second Brain unexpectedly

## 13. UI gate

backend/route가 green이어도 UI를 즉시 켜지 않는다.

- initial bootstrap vs local cache precedence
- pending optimistic operation indicator
- stale response/version conflict recovery
- offline action queue 여부
- section keyboard order and focus restore
- sidebar resize keyboard/a11y and narrow viewport
- workspace switch 중 draft flush
- hidden/revoked favorite disappearance
- feature flag default off and kill switch

동결 결정:

- initial bootstrap: legacy local state를 먼저 그릴 수 있으나 enabled server snapshot 수신 즉시 server 값으로
  덮어쓴다.
- pending indicator: navigation mutation 동안 sidebar root에 `aria-busy=true`와 polite status를 노출한다.
- stale recovery: HTTP 409 또는 늦은 응답은 현재 workspace sequence와 version을 다시 읽어 회복한다.
- offline queue: 만들지 않는다. network uncertainty는 동일 operation ID replay 1회까지만 허용한다.
- hidden/revoked favorite: 성공/실패 양쪽 모두 canonical reload를 거쳐 화면에서 제거한다.
- page content: 기존 `/api/notion/pages`를 유지하며 selected canonical workspace는 navigation state만 scope한다.

## 14. recents 결정 대기

Recents는 공식 reference에 존재하지만 implementation semantics는 별도다.

필수 결정:

1. page open마다 immutable audit/outbox를 남길지, privacy-scoped coalesced event를 새로 허용할지
2. retention/purge 기간과 권한
3. max rows와 tie-breaking sequence
4. repeated view coalescing window
5. client timestamp 신뢰 금지와 trusted server clock
6. multi-device ordering/replay
7. archive/delete/revoke 뒤 hidden/retained/deleted 정책
8. telemetry와 user-facing recent state 분리

이 결정 없이 `workspace_page_recents`나 view-event route를 먼저 만들지 않는다.

## 15. stop/no-go

- current store/migration file hash drift 또는 ownership 충돌
- frozen 1309 import/hash mismatch
- navigation API가 source directory scan/DB open을 요구
- guest/private/teamspace visibility 의미가 불명확한 상태에서 existence를 노출
- audit/outbox 없이 preference mutation을 저장
- workspace version/security epoch를 view/pref write마다 올리는 설계
- recents retention 결정을 우회한 고빈도 event 구현
- UI/app/bridge/Second Brain 등록을 backend gate보다 먼저 수행
- live catalog migration 또는 feature flag enable

## 16. xhigh 실행 순서

1. active goal, `AGENTS.md`, xhigh handoff와 이 문서를 다시 읽는다.
2. store/1310 migration/routes/tests의 hash와 다른 개발자 ownership을 확인한다.
3. machine contract가 backend schema/store API/routes/UI implementation true, runtime UI/recents/live migration false인지 확인한다.
4. NP2B-1 checkpoint의 exact hashes와 regression evidence를 재검증한다.
5. NP2B-2/3 route·UI 파일 hashes와 default-off capability/kill-switch를 확인한다.
6. route의 membership/CSRF/Origin/no-store/strict JSON gate를 유지한다.
7. route count, auth isolation, Node UI, app/bridge/Second Brain hard regression을 통과한다.
8. 별도 checkpoint와 P0/P1 review 전에는 UI flag, Recents 또는 tree lifecycle로 넘어가지 않는다.
9. UI flag enable과 Recents는 각각 별도 승인/gate로 남긴다.

## 17. 완료 표현

- 이 문서/contract/verifier: `backend-foundation-synchronized`
- schema/store tests까지: `contract-tested backend foundation`
- route tests까지: `contract-tested API slice`
- UI/a11y/flag까지: `contract-tested vertical slice`
- 승인된 실제 환경 관찰 전: `live-characterized` 아님
- 공식 reference/local 동일 scenario 비교 전: `differential-passed` 아님

NP2B 전체 완료에는 recents, tree interaction, workspace switcher, UI/a11y와 rollout evidence가 모두
필요하다. favorites/sidebar preference 한 slice를 NP-2B 완료로 승격하지 않는다.

NP2B-3 current-tree evidence는
`docs/notion_parity/checkpoints/np2b3-workspace-navigation-default-off-ui-20260814.json`에 고정했다.
상태는 `p0_p1_review_passed_default_off`이며 실제 enable/kill-switch 환경 값은 모두 empty였다.
이 증거는 live characterization 또는 differential pass가 아니다.
