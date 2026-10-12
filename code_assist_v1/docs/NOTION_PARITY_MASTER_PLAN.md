# Notion parity 전체 개발 마스터 플랜

기준일: 2026-08-14 (Asia/Seoul)  
상세 기능·근거·시나리오: `docs/NOTION_PARITY_ROADMAP.md`  
상태: 개발 범위와 순서를 확정하는 계획. 전체 동등성 완료 선언이 아니다.

> **2026-08-14 UI-first scope correction:** 현재 제품의 주 목표와 실행 순서는
> `docs/NOTION_UI_SECOND_BRAIN_XHIGH_HANDOFF_PLAN.md`를 따른다. Core 분모는 Free owner / Chrome desktop /
> ko-KR의 합성 workspace UI·interaction과 Second Brain 연결이다. Notion billing·Enterprise 운영 로직은
> Core 범위가 아니며, NP2C-5/5a Business·Enterprise Archive는 historical optional paid-feature lane이다.
> 따라서 Archive live characterization 부재는 Core shell/editor/database/search 개발을 막지 않는다.

## 1. 현재 기준점

“첫 번째 구현 완료”는 **NP-1 런타임·호환 기반의 1차 완료**를 뜻한다. 원자 저장, revision 충돌, idempotency, 기존 DB 무변경 read, 오류·인증 계약, publish 승인 순서와 Second Brain 호환 회귀가 이 범위다.

현재 NP-2A에는 canonical workspace/IAM catalog, locator projection, exact-owner inventory/backfill,
guarded 1303→1304→1305→1306→1307→1308→1309→1310→1311→1312→1313→1314→1315→1316→1317 migration, offline/quiesced healing,
reasoned quarantine, legacy incarnation v2와 immutable catalog attestation/lineage/full-rebind가 들어와 있다.
H5a~H5d의 non-destructive source receipt·pass·work·completion·multi-workspace convergence도 구현됐고,
H5e는 source v4 conditional ack를 설계-only machine contract로만 동결했다.

NP2B-1은 frozen 1309 위에 `2026081310` per-member navigation state를 추가했다. sidebar preference와
generation-stable favorite store API, explicit 1309→1310 migrator, canonical operation/audit/outbox replay와
deep health는 격리 환경에서 contract-tested다. NP2B-2는 이 상태를 Notion host 전용 protected
GET/PATCH/PUT/DELETE route에 연결하고 strict JSON, auth/Origin/CSRF, no-store와 zero-leak contract를
검증했다. frozen workspace 1303→1310 chain과 retained 1309 restore→1310 재적용도 검증했다.
NP2B-3은 server capability와 독립 kill switch가 모두 허용할 때만 동작하는 canonical workspace switcher,
sidebar preference/favorite UI, stale-response fence, exact-operation network replay, draft/preference flush와
keyboard/a11y 상태를 Notion host 정적 UI에 연결했다. 기본값과 실제 환경 flag는 off다. 운영 DB migration,
request/startup migration, Recents,
continuous runtime consumer, resolver cutover는 여전히 비활성이고 `app_code.py`·Second Brain 경계도
유지하므로 NP-2 또는 NP-2B 완료로 보지 않는다.
NP2B-3 격리 증거와 P0/P1 audit는
`docs/notion_parity/checkpoints/np2b3-workspace-navigation-default-off-ui-20260814.json`에 있다.

NP2C-0은 page tree/lifecycle authority, full owner+page parent identity, bounded saga와 금지 상태를
설계·machine contract로 동결했다. NP2C-1은 frozen 1310 위에 `2026081311` additive backend foundation을
추가한다. 기존 workspace는 tree state만 `unmaterialized`로 생성하며 tree node나 lifecycle receipt를
fabricate하지 않는다. canonical tree node와 lifecycle command saga의 SQL guard, explicit 1310→1311
migrator, deep-health/readiness 및 NP2C-2a offline initial materializer까지 격리 검증됐다. materializer는
exact rehearsal sentinel과 source quiescence 아래서만 unmaterialized→complete를 원자적으로 수행하며,
owner별 source snapshot 2회·full integrity 1회·총 active binding 1,000개의 hard bound를 가진다. command
service의 NP2C-2b disabled rename protocol kernel은 frozen 1312 위에서 fake/temp source effect의 exact replay와
crash 수렴을 검증했다. NP2C-3a는 real rename 구현 전 source pre/post receipt와 version lineage를 동결했다.
Source는 새 migration 없이 frozen v3의 immutable `notion_legacy_change_consumer_operations`를
`np2c.lifecycle` namespace로 재사용하고, content-bearing/cascading `page_save_operations`는 사용하지 않는다.
Changed rename은 revision·watermark·source epoch·full-sweep generation이 각각 정확히 1 증가해야 하며 no-op은
source state를 바꾸지 않고 immutable receipt만 남긴다. NP2C-3b는 이 계약대로 route-unregistered
connection-scoped adapter를 추가해 temporary v3 owner shard에서 changed/no-op, exact generation delta, bounded
page-version snapshot, source commit response-loss replay, rollback, permission/lock/stale pre-state, capability refusal와
title redaction을 검증했다. NP2C-3c는 healing authority를 사칭하지 않는 frozen `2026081313` lifecycle-only
guard, exact 1312→1313 migrator와 atomic catalog finalizer를 추가했다. Changed finalize는 binding proof·incarnation
attestation·complete locator projection·tree node를 한 transaction에서 갱신하고 workspace/security version을 한 번
올린 뒤 tree state를 `partial`로 내려 linked-page proof fan-out의 exact owner full-sweep debt를 보존한다. No-op은
command/operation/audit/outbox만 확정하고 binding/tree/workspace version을 올리지 않는다. NP2C-3d는 이미 고정된
legacy-v3 source adapter와 1313 finalizer를 exact rehearsal sentinel 아래서만 결합했다. 임시 shard/catalog에서
changed/no-op, source commit 뒤 응답 손실, catalog commit 뒤 응답 손실, source commit 뒤 catalog rollback,
raw-title·identifier 비노출과 changed tree의 `partial`/full-sweep debt를 검증했다. finalized catalog replay는
quiescence·sentinel·source path보다 먼저 exact source request를 다시 검증하고 반환한다. 실제 user shard에는
접근하지 않았고 route/UI, runtime command, resolver cutover와 purge는 모두 미구현·비활성이다. 따라서 1311/1312/1313/1314/1315/1316/1317은 NP-2C 완료나
실제 page tree authority 전환을 뜻하지 않는다.

NP2C-3e는 action별 계약을 동결한 뒤 3e1 legacy-v3 source adapter와 3e2 workspace `2026081314` structural guard schema/migration을 machine contract v12로 구현했다. Route-unregistered adapter는 exact prestate/post-manifest, move/reorder action 분류, bounded page/version/parent/sibling/subtree evidence, immutable response-loss replay와 rollback을 temporary owner shard에서 검증한다. 새 source DDL 없이 frozen v3 operation ledger를 재사용하고 H5e source v4 예약을 유지한다. 1314는 immutable structural manifest/effect rows, generation-scoped sibling order, action-specific raw-SQL guards, lifecycle-only create/multi-proof authority와 exact result/deep-health validation을 추가한다. NP2C-3e3 machine contract v13과 workspace `2026081315`는 null-safe root authority와 complete structural finalization authority를 추가한다. Offline exact-sentinel orchestration은 create/move/reorder source mutation, 페이지별 logical watermark coalescing, source receipt, binding/projection/attestation/tree 갱신과 operation/audit/outbox를 replay-safe하게 수렴시킨다. Explicit 1314→1315 migrator는 live command를 거부하고 기존 evidence를 보존한다. Temporary shard/catalog에서 source commit replay, catalog replay-before-path, lease expiry, rollback과 identifier-free report를 검증했으며 runtime route/UI/worker/resolver는 여전히 비활성이다. NP2C-4a machine contract v14는 현재 `archive_page`가 HTTP Delete/Trash 호환 동작임을 공식 Archive와 분리하고, deterministic duplicate file staging, exact Trash cohort, same-incarnation restore와 reviewed fallback placement를 설계 동결했다. NP2C-4b machine contract v15는 이 설계를 route-unregistered source-v3 adapter로 구현해 replay-derived page/block/file/version identity, exact file staging/recovery, source/path drift refusal, exact Trash cohort와 explicit restore placement를 temporary shard에서 검증했다. NP2C-4c machine contract v16과 workspace `2026081316`은 canonical duplicate/Trash/restore action/state, immutable subtree manifest/effect/object mapping, fail-closed service context와 raw-SQL lifecycle guards, exact operation/audit/outbox/deep-health correlation을 추가한다. Explicit 1315→1316 migrator는 live·historical lifecycle command와 ambiguous non-active history를 거부하고 evidence나 성공을 fabricate하지 않는다. NP2C-4d machine contract v17과 workspace `2026081317`은 transitioned/unchanged parent·sibling effect를 모두 표현하는 11,004-row authority, atomic duplicate/Trash/restore catalog finalizer와 exact-sentinel offline rehearsal을 추가한다. Binding/projection/attestation/tree/evidence/operation/audit/outbox는 한 catalog transaction에서 수렴하며 replay가 path·quiescence·source I/O보다 먼저다. NP2C-4e machine contract v18은 source commit 뒤 catalog acknowledgement 또는 finalizer가 실패한 재시도에서 source immutable ledger replay를 precondition recapture보다 먼저 수행하도록 복구 경로를 닫았다. Catalog fault phase `after_evidence|after_bindings|after_tree`, exact 11,004-effect source/catalog bound parity, 11,005 pre-parse refusal, identifier/path/digest/lease-secret redaction과 deep-health를 독립 회귀로 고정했다. Full-owner receipt로 finalization한 tree는 `complete`를 유지하며 `full_sweep_required=true`는 watermark/cutover debt다. NP2C-5 machine contract v19는 공식 Archive를 접근 가능한 별도 상태로 설계하고 legacy Trash bit/함수/역사와 분리했다. H5e가 예약한 미구현 source v4 `2026081304`를 건너뛰거나 재사용하지 않고 candidate Archive source v5 `2026081305`와 workspace `2026081318`을 필요하지만 미구현인 후속으로 고정했다. Runtime route/UI/worker/resolver, live characterization/migration, destructive acknowledgement와 Archive 구현/purge는 계속 비활성이다.

공식 제품과의 live characterization 및 differential pass는 아직 시작 전이다. 따라서 현재 가장 높은 표현은 격리 환경의 `contract-tested`이며 “Notion과 완전히 동일”이라는 출시 주장은 할 수 없다.

참고: 아래 schema 연혁에서 “1310 route 비등록”이라는 표현은 NP2B-1 backend checkpoint를 뜻한다.
현재 tree는 NP2B-2 protected navigation route와 NP2B-3 default-off UI 코드를 등록했다. UI feature flag,
Recents/resolver/live migration은 계속 비활성이다. 1311 page-tree foundation에는 runtime route나 UI가 없다.

## 2. 제품 범위

| 영역 | 포함 기능 | 완료 단계 |
|---|---|---|
| 검증 플랫폼 | 기준 계정/플랜/역할/플랫폼, 시나리오 catalog, DOM·상태·키보드·a11y·visual evidence, differential runner | NP-0 |
| 런타임 기반 | atomic save, revision/CAS, idempotency, 인증/CSRF, 오류 계약, migration/readiness/rollback, 앱 경계 | NP-1 |
| Workspace·IAM | workspace, teamspace, member/guest/group, sidebar/tree, favorites/recents, lifecycle/Trash, capability, public subtree | NP-2 |
| 문서·편집기 | page 설정, block/rich text, slash/inline menu, drag/multi-select, clipboard, undo/redo, IME, autosave/history | NP-3 |
| Database | data source, typed property, relation/rollup/formula, template/layout, table/board/list/gallery/calendar/timeline와 saved view | NP-4 |
| 협업 | realtime convergence, presence, comments/thread/reaction, mentions/reminders, Inbox/notifications, reconnect/revoke fencing | NP-5 |
| 탐색·업무 | search, backlinks, template/button, wiki/home, feed, chart/dashboard/map, print와 moving beta | NP-6 |
| Forms·공유·Sites | form builder/submit, share lifecycle, public renderer/files/subtree, Sites/SEO/domain/analytics | NP-7 |
| 플랫폼 | responsive web, desktop/mobile 차이, native iOS/Android, offline cache/sync/conflict, 접근성 | NP-8 |
| 자동화·연결 | database automation, webhook, OAuth, 외부 provider adapter, sync DB, developer platform | NP-9 |
| AI·Enterprise | AI read/write/Q&A, citations/actions, Meeting Notes, Agents, HTML sandbox, SAML/SCIM/DLP/audit/eDiscovery | NP-10 |
| 이관·출시 | migration/reconciliation, feature flags, canary/pilot, telemetry/support, restore/rollback, 법무·브랜드 승인 | NP-11 |

## 3. 권장 릴리스 묶음과 임계 경로

### R0 — 검증·호환 기반

- 대상: NP-0의 최소 실행 runner 보완 + NP-1 유지보수 + 지원 범위 manifest 동결.
- 현재: NP-1 1차 구현 완료, NP-0은 comparator/evidence core까지만 완료.
- 종료 조건: `core`, `plan/platform conditional`, `moving`, `separate product`, `excluded` 분모가 정해지고, 같은 시나리오를 공식 기준 계정과 로컬에서 재생해 비식별 evidence hash를 남길 수 있다. 기존 59/61/15 계약과 Second Brain hard gate가 계속 통과한다.

NP-0은 개발 착수에 필요한 최소 harness와 릴리스마다 계속 수행하는 characterization/rebaseline lane으로 나눈다. live evidence 없이 어떤 단계도 `differential-passed`로 승격하지 않는다.

### R1 — 안전한 Workspace

- 대상: NP-2A~NP-2E.
- 순서: canonical locator/cutover → sidebar/tree → lifecycle/Trash → 통합 capability → public subtree.
- 핵심 결과: 실제 workspace 전환, teamspace/member/guest/group, server-backed favorites/recents, atomic tree lifecycle, 모든 read/write 경로의 단일 권한 판정, 비공개 metadata 누출 0.
- 종료 조건: authorization 경로에서 사용자 디렉터리 scan 0, tenant/role canary 누출 0, revoke 즉시 반영, tree keyboard/a11y와 destructive recovery 통과.

NP-2A의 DB 불변식, 원 required-capability replay, source proof lifecycle, bounded readiness와 exact-owner backfill은 `2026081303`에서, secret-free locator generation과 동기식 projector는 `2026081304`에서, immutable parent outbox와 consumer별 lease/retry/dead-letter·세대 fencing·bounded claim은 `2026081305`에서 격리 검증됐다. `2026081306`은 admin-scoped durable healing run을, `2026081307`은 owner/source fence와 원자적 locator healing effect를, `2026081308`은 reasoned quarantine과 영구 outcome의 원자적 locator 제거를 추가한다. `2026081309`는 legacy incarnation v2 receipt에 결속된 append-only catalog attestation, lineage guard, exact commitment readiness와 explicit atomic rebind를 추가한다. `2026081310`은 NP2B-1 per-member sidebar preference와 generation-stable favorite state를 추가하고, `2026081311`은 NP2C-1 unmaterialized tree state, generation-fenced node, lifecycle saga의 backend schema를 추가한다. `2026081312`는 frozen 1311 위에서 command claim/reclaim/heartbeat lease·fencing 전이와 command↔workspace-operation namespace를 raw SQL/deep-health 수준으로 닫으며, 정식 kernel 이전의 1311 command row가 한 건이라도 있으면 migration을 거부한다. 이 migration들은 tree materialization, command API, route/UI/Recents/resolver를 활성화하지 않으며 NP2B-2/3 runtime overlay는 별도 default-off gate를 따른다. workspace 1303→1312와 legacy source v1→v2→consumer v3 explicit migration은 exact path, frozen schema/hash, verified backup, quiesce 확인, rollback 및 ambiguous replacement outcome 수렴 계약으로 구현했으며 운영 catalog/shard에는 적용하지 않았다. reconciliation은 계속 read-only이고 healing/review/rebind도 명시적 offline/quiesced 경계에만 존재한다. H5b source pass는 고정 source fence 아래 최대 32개 watermark/incarnation receipt를 immutable work로 materialize하고 crash lease를 reclaim한다. H5c offline orchestration은 catalog effect를 먼저 commit한 뒤 exact source work를 terminalize하고, response loss를 양쪽 immutable ledger로 수렴시키며, zero-pending·completed locator run·exact owner fence를 만족해야만 full-sweep completion을 기록하고 다음 pass를 허용한다. H5d는 여러 workspace의 completed-run set을 applied terminal work coverage와 exact 일치시키고 restart/no-ack repeated pass에서도 identifier-free lag가 수렴함을 검증했다. H5e-0은 superseded work를 pending으로 보존하고 current watermark가 materialized receipt보다 뒤로 가지 않는 invariant를 추가했다. source v4 `2026081304`와 conditional DELETE/ack는 설계 제안일 뿐 구현되지 않았으며, 별도 승인 뒤에도 no-ack schema/migrator→explicit offline ack→continuous scheduling 순서로 분리한다. 1312 invariant 위의 NP2C-2b disabled `rename` protocol kernel은 prepare→source receipt→atomic finalize와 fail/manual-audit, exact replay, source response-loss 및 catalog-finalize crash 수렴을 fake/temp source로 검증했다. 실제 legacy rename, title/source pre-state, tree·binding·locator 갱신과 runtime 등록은 아직 없다.

현재 runtime 후속선은 이 문단의 1312 기술보다 앞선다. Frozen `2026081313`은 lifecycle-only service context와 changed/no-op rename catalog finalizer를 추가해 proof·incarnation attestation·locator·tree-node·workspace version을 한 transaction에 수렴시키고 changed tree를 `partial`로 표시한다. NP2C-3d exact-sentinel offline source↔catalog rehearsal도 contract-tested됐다. 현재 workspace chain은 1303→1304→1305→1306→1307→1308→1309→1310→1311→1312→1313→1314→1315→1316→1317이다. NP2C-3e1 action-specific legacy-v3 source adapter, NP2C-3e2 structural effect guard, NP2C-3e3 atomic catalog finalizer와 exact-sentinel rehearsal까지 완료됐다. NP2C-4a duplicate/Trash/restore 설계, NP2C-4b route-unregistered temporary-shard source adapter/file recovery, NP2C-4c frozen 1316 lifecycle-subtree guard schema와 NP2C-4d frozen 1317 atomic catalog finalizer/exact-sentinel offline rehearsal이 machine contract v17로 검증됐다. NP2C-4e는 source-ledger-first recovery와 rollback/bounds/redaction/deep-health 독립 감사를 machine contract v18로 닫았다. NP2C-5는 공식 Archive의 reference-only source/catalog contract를 machine contract v19로 동결했다. 다음 gate는 runtime 등록이 아니라 승인된 Business/Enterprise fixture의 `NP2C-5a` live-characterization matrix다.

NP2C-5a의 실행 준비물로 별도 matrix v1, strict verifier/adversarial tests와 runbook을 추가했다. Matrix는 16개 시나리오와 11개 plan/role/platform profile, evidence-policy 필수 manifest, public/nested/plan-loss no-inference 규칙을 고정한다. External-root `--inspect-only`는 record 구조와 redaction을 검증해 canonical authorization digest 후보만 표시하며 `valid`, characterization 권한과 digest match를 항상 false로 둔다. 사용자가 그 exact digest를 별도 채널에서 승인한 뒤에만 current-time authorization preflight가 최대 24시간 유효 창, Business/Enterprise 합성 tenant, 독립 operator/reviewer와 public-link/plan-transition 위험 승인을 확인한다. Record mutation은 이전 승인을 무효화하고 implementation/completion 권한은 두 모드 모두 항상 false다. 별도 completion acceptance verifier는 같은 승인 digest와 명시 파일만 읽고 모든 scenario/profile pair, distinct path의 exact fixture/action/manifest/result/redaction bytes와 canonical semantic decision을 결속하며 unknown/conflict 또는 구현 권한을 fail closed한다. 상태는 여전히 `prepared_not_authorized_not_executed`다. 사용자 승인 digest, tenant/operator/reviewer/외부 redacted evidence root와 bundle이 없으므로 live observation은 0건이고 machine contract v19, source v4/v5, workspace 1318과 runtime 상태는 변하지 않았다.

### R2 — 문서 작성

- 대상: NP-3.
- 순서: canonical block/rich-text schema → command model → selection/focus/keyboard → 복합 block/media/layout → history/conflict/performance.
- 핵심 결과: 입력 방법이나 peek 형태와 무관하게 같은 command와 revision 결과를 만들고, unsupported block도 무손실 round-trip 또는 명시적 degraded 상태를 갖는다.
- 종료 조건: block corpus, Korean IME, clipboard/sanitization, undo/redo, crash/reload, stale conflict, publication snapshot이 differential pass.

### R3 — 구조화 데이터와 다중 사용자

- 대상: NP-4와 NP-5.
- 순서: typed data source/property/query engine을 먼저 고정하고, 그 command/event 모델 위에 realtime을 연결한다.
- 병렬 가능: DB view UI와 collaboration transport 설계. 다만 collaboration content mutation은 NP-4 command가 안정되기 전 활성화하지 않는다.
- 종료 조건: formula/relation/rollup 및 view-local state conformance, 다중 브라우저 convergence, reconnect/out-of-order/revoke fault test 통과.

### R4 — 업무 흐름과 외부 공개

- 대상: NP-6과 NP-7.
- 순서: ACL-aware search/index → links/reminders/templates/buttons → forms → sharing/public renderer → Sites.
- 병렬 가능: read-only search 실험과 public renderer threat corpus. 둘 다 NP-2 capability와 NP-4 data contract를 우회하지 않는다.
- 종료 조건: 검색/AI/export/public crawl의 비공개 canary 누출 0, form abuse·duplicate·file 검증, unpublish/cache invalidation, plan별 UI differential pass.

이 지점이 **독립 제품의 Core Web parity 후보**다. native/offline, 외부 공급자, Enterprise 및 고유 AI 결과는 아래 확장 릴리스로 분리한다.

### R5 — 플랫폼·생태계·지능 기능

- 대상: NP-8, NP-9, NP-10.
- NP-8은 native app/offline 전용 제품 트랙으로 별도 예산·배포·device lab이 필요하다.
- NP-9는 provider별 OAuth/security/legal 승인과 sandbox contract가 있는 adapter만 연다.
- NP-10은 권한·citation·tool action·human approval을 평가하며 고유 모델의 문장이나 확률적 결과를 bit-for-bit 복제하지 않는다.
- 종료 조건: device/offline no-loss, webhook/OAuth chaos, AI adversarial/permission eval, Enterprise 실제 승인 tenant의 role/audit matrix 통과.

### R6 — 이관·Pilot·최종 판정

- 대상: NP-11.
- 순서: immutable inventory → rehearsal → backup/restore drill → internal → allowlist → one workspace → staged rollout.
- 장기 dual-write는 금지한다. ID/token/session binding을 보존하고 reconcile 가능한 projection만 사용한다.
- 종료 조건: 채택 범위의 모든 필수 scenario가 `differential-passed`, unresolved conflict 0, restore/no-loss/security/a11y/performance/pilot soak 및 운영·법무 승인이 완료된다.

## 4. 모든 단계의 공통 완료 정의

기능 하나의 상태는 다음 순서로만 승격한다.

1. `exists`: 실행 경로가 존재한다.
2. `contract-tested`: 격리 DB에서 입력·출력·실패·권한 계약이 자동 검증된다.
3. `live-characterized`: 승인된 기준 계정/플랜/역할/플랫폼에서 공식 제품 동작을 관찰하고 evidence를 남긴다.
4. `differential-passed`: 같은 action을 양쪽에 재생해 state, keyboard/focus, error/recovery, a11y와 허용된 visual geometry가 통과한다.

각 vertical slice는 backend, API, UI, migration, negative/security, a11y, observability, rollback을 함께 끝낸다. happy path만 구현하거나 화면만 비슷하게 만든 상태는 완료가 아니다.

## 5. Second Brain 불변 게이트

모든 릴리스에서 다음 연결은 기존 동작과 응답 호환을 유지한다.

- Notion → Second Brain: dirty save → preview → 사용자 승인 → source hash 재검증 → immutable snapshot publish.
- 대화 context: page attach/detach/list와 매 turn live permission 재검증. durable binding과 publication은 별개다.
- Second Brain → Notion: chat full/summary/decisions 저장, thought import, Second Brain template.
- overview: 기존 page/session/source/thought/calendar/attachment projection.
- Notion page revision과 knowledge revision은 결합하지 않는다.
- Notion migration이 Knowledge Core/part write, thought lifecycle, deactivate/purge를 암묵적으로 실행하지 않는다.
- LLM/network/OCR은 DB transaction 밖에서 수행하고 commit 직전에 권한과 source revision을 다시 확인한다.

관련 hard route, publication UI, session/context, template/chat/thought 및 Knowledge regression이 하나라도 실패하면 해당 slice는 병합·출시하지 않는다.

## 6. 명시적 분리·제외 범위

- Notion API를 복제 수단으로 사용하는 구현은 하지 않는다. 공개 문서와 승인된 정상 UI 관찰을 기준으로 독립 구현한다.
- Notion 명칭·로고·아이콘·문구·visual identity의 복제는 범위가 아니다. 동작과 접근성 계약은 맞추되 독립 브랜드/디자인 시스템을 사용한다.
- Notion Mail inbox는 종료 예정 기능이므로 장기 parity 대상에서 제외하고 데이터 export/전환 안내만 별도 관리한다.
- Calendar native app, iOS/Android, Web Clipper/widgets는 Core Web과 별도 제품 트랙이다.
- moving beta/alpha와 플랜 한정 Enterprise 기능은 조건부 manifest로 관리하며 검증 계정이 없으면 `unknown`으로 남긴다.
- 고유 AI 모델의 exact wording은 제외하고 권한, citation, tool call, approval, refusal와 오류 계약을 평가한다.

## 7. 착수 순서

현재 Core UI lane의 권위 순서는 다음과 같다. 아래의 기존 번호 목록은 backend/history 원장으로 보존하며,
그 안의 NP2C-5a “다음” 문장은 Archive optional lane에만 적용한다.

1. UI-first scope/hash checkpoint와 Core scenario denominator를 고정한다.
2. 두 Free synthetic workspace와 isolated local fixture를 위한 실제 browser/evidence harness를 만든다.
3. `UI1-FREE-DESKTOP-GOLDEN-JOURNEY`를 실행한다.
4. workspace-scoped shell/sidebar/page tree를 default-off vertical slice로 닫는다.
5. editor command/IME/caret/save-recovery와 Second Brain hard regression을 닫는다.
6. database/search/basic sharing, responsive/a11y/performance 순으로 확장한다.
7. Business/Enterprise Archive는 사용자가 optional pack을 채택할 때만 별도 진행한다.

1. 현재 workspace schema `2026081317`과 frozen `2026081316`/`2026081315`/`2026081314`/`2026081313`/`2026081312`/`2026081311`/`2026081310`/NP-2A `2026081309` 계약을 회귀 동결하고 1303→1304→1305→1306→1307→1308→1309→1310→1311→1312→1313→1314→1315→1316→1317 어느 단계에도 request-time 자동 migration을 허용하지 않는다.
2. explicit migration chain, offline/quiesced activation-event projector와 source-fenced healing apply, bounded reconciliation, reasoned quarantine, shard-local watermark·incarnation v2, catalog attestation/lineage/readiness와 atomic store-level rebind 계약을 회귀 동결한다. exact sentinel attestation/full-rebind orchestration, restored-snapshot rehearsal, H5a source-fenced effect receipt와 checkpoint-loss replay, H5b legacy source `2026081303` durable pass/work/operation ledger와 bounded non-destructive keyset materialization, H5c disabled/offline effect→source-terminal 수렴과 full-sweep completion gate, H5d multi-workspace no-ack/restart/lag rehearsal은 구현됐다. H5e-0의 superseded-pending 의미와 machine-readable conditional-ack 설계는 검토 가능하지만 `authorized=false`, `implemented=false`다. 별도 승인 없이는 source v4, destructive ack, continuous scheduling, legacy directory-scan resolver cutover 또는 NP2B UI flag enable을 시작하지 않는다.
3. NP2B-1은 workspace `2026081310`의 sidebar preference/favorites schema, explicit 1309→1310 migrator, store API, exact replay/deep-health와 focused regression을 contract-tested backend foundation으로 구현했다. NP2B-2는 Notion host 전용 GET/PATCH/PUT/DELETE navigation route, strict JSON, member/visibility 404, no-store, Origin/CSRF와 12 rules/19 operations/19 hard operations machine contract를 별도 slice로 검증했다. NP2B-3은 default-off capability/kill-switch, server snapshot precedence, serialized optimistic mutation, replay/reload/rollback, draft flush, section keyboard/focus와 responsive resize 계약을 구현·검증했다. 실제 flag는 켜지 않았다. 고빈도 Recents는 immutable audit/outbox retention 결정 전까지 구현하지 않는다. NP2C-0/1은 1311 tree/saga backend foundation을, NP2C-2a는 exact-source offline initial materializer를 구현했다. frozen 1311을 유지하는 1312 lifecycle lease/fence/operation-namespace trigger hardening과 disabled NP2C-2b `rename` protocol kernel도 구현·격리 검증했다. NP2C-3a 설계, NP2C-3b rename source adapter, NP2C-3c frozen 1312→1313 lifecycle authority/finalizer와 NP2C-3d exact-sentinel source↔catalog response-loss rehearsal까지 구현·격리 검증했다. NP2C-3e1은 create/move/reorder의 bounded exact source manifest와 frozen-v3 receipt adapter를 temporary shard에서 구현했고 3e2는 frozen 1313→1314 schema/guards를 추가했다. 3e3은 frozen 1314→1315 finalization authority, atomic catalog finalizer와 exact-sentinel source↔catalog rehearsal을 구현·격리 검증했다. NP2C-4a는 duplicate/Trash/restore의 identity, deterministic file convergence, exact Trash cohort, explicit restore placement와 candidate 1316 concerns를 설계 동결했고, 4b는 source-v3 temporary-shard adapter와 recoverable file protocol을 구현·검증했다. 4c는 frozen 1315→1316 migration과 lifecycle-subtree schema/guards를, 4d는 frozen 1316→1317 finalization authority와 atomic catalog finalizer/exact-sentinel rehearsal을 구현·격리 검증했다. 4e는 source commit 뒤 catalog ACK/finalizer failure recovery, three-phase catalog rollback, exact effect bound와 pre-parse overflow, report redaction 및 deep-health를 machine contract v18과 별도 checkpoint로 닫았다. NP2C-5는 candidate source v5/workspace 1318 lineage, active binding/locator 보존, visibility/IAM, cohort/replay/bounds와 미확정 live matrix를 machine contract v19로 설계 동결했다. 다음은 `NP2C-5a` 승인된 live characterization이며 runtime route나 UI를 먼저 연결하지 않는다.
4. NP-2D zero-leak capability corpus를 통과한 뒤 NP-2E public subtree를 연다.
5. NP-3 → NP-4 순으로 canonical content/data command를 고정한다.
6. NP-5 transport 설계는 병렬로 하되 mutation 활성화는 NP-3/NP-4 gate 뒤로 둔다.
7. NP-6/NP-7로 Core Web 후보를 완성하고, NP-8~NP-10은 승인된 별도 트랙으로 진행한다.
8. NP-11 전에는 운영 DB migration, broad feature flag, Knowledge Core part write를 실행하지 않는다.

달력 일정은 팀 규모, 지원 플랜, web만인지 native까지인지, Enterprise 실계정 제공 여부가 정해진 뒤 산정한다. 현재는 위 의존성과 gate가 일정 압축보다 우선한다.

## 8. 전 단계 공통 작업선

- **File/asset:** content hash, quota, MIME/size validation, malware policy, signed/public download, dedup, Trash 복구와 orphan 정리.
- **Import/export:** Markdown, HTML, CSV, PDF, ZIP과 asset·permission·부분 실패·round-trip 계약.
- **Settings/plan:** workspace/account setting, feature entitlement, usage/quota와 downgrade 동작.
- **Locale/time:** Korean/English, timezone/DST, number/date formatting, collation/search와 IME.
- **Performance/operations:** large workspace/document/database corpus, latency/SLO, metrics/log/audit, backup/restore, readiness, kill switch.
- **Platform/accessibility:** browser/OS/device 지원 matrix, keyboard/screen reader, touch, reduced motion, high contrast.
- **Migration:** schema가 바뀌는 모든 milestone에 dry-run, checksum, reconcile, rollback을 함께 구현한다. NP-11까지 미루지 않는다.

NP-7은 Sharing/Public과 Forms/Sites, NP-8은 responsive web과 native/offline, NP-9는 automation engine과 공급자별 connector, NP-10은 AI/Agents와 Enterprise compliance를 각각 독립 feature pack으로 추적한다.
