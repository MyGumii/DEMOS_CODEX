# Notion parity 실행 로드맵

기준일: 2026-08-13 (Asia/Seoul)  
대상: `code_assist_v1`의 독립 구현 Notion형 작업공간  
상태: 계획 기준선. 이 문서는 현재 기능의 완전 동등성을 선언하지 않는다.

> **2026-08-14 UI-first scope correction:** 현재 실행 우선순위와 Core 완료 분모는
> `docs/NOTION_UI_SECOND_BRAIN_XHIGH_HANDOFF_PLAN.md`가 정한다. Free owner / Chrome desktop / ko-KR의
> 합성 workspace UI·interaction과 Second Brain 연결이 Core다. Business/Enterprise Archive와 plan transition은
> conditional/moving lane이며 Core의 선행조건이 아니다. Production IAM/resolver cutover gate는 유지하되,
> isolated/default-off shell·editor vertical slice는 NP-1 안전 API 위에서 병렬 진행할 수 있다.

> `NP-*`는 **Notion parity 전용 단계 ID**다. `docs/knowledge_spaces/`의 Knowledge Spaces `P0`~`P8`과 서로 다른 계획이며, 어느 한쪽의 완료가 다른 쪽의 완료를 뜻하지 않는다.

## 1. 목표와 완료 정의

목표는 2026-08-12에 공식 공개된 Notion의 사용자 관찰 가능 계약을 독립 제품의 용어와 시각 체계로 재현하면서, 이미 동작하는 Second Brain 연결 계약을 깨뜨리지 않는 것이다. “완벽 동일”은 슬로건이 아니라, 시나리오별로 아래 네 상태를 누적 충족하고 지원 플랜·플랫폼·기준 빌드가 기록된 경우에만 사용한다.

| 상태 | 판정 기준 | 허용되는 주장 |
|---|---|---|
| `exists` | 등록된 라우트나 도달 가능한 코드 경로를 저장소에서 확인했다. | “코드 경로가 존재한다.” |
| `contract-tested` | 격리 임시 경로에서 명시적 입력·출력·실패·권한 계약을 자동화해 통과했다. | “저장소 계약을 검증했다.” |
| `live-characterized` | 로그인된 공식 제품에서 동일 시나리오를 실행하고 날짜, 플랜, 계정 역할, 플랫폼, 앱/브라우저 버전, locale, 입력, 관찰 결과와 비식별 증거를 남겼다. | “공식 제품의 당시 동작을 관찰했다.” |
| `differential-passed` | 같은 시나리오를 양쪽에서 실행해 구조·상태 전이·키보드·초점·오류·접근성·시각 허용치 차이가 승인 범위 이내다. | 해당 고정 기준에 대한 “동작 동등” |

상태는 순서가 있는 누적 상태다. `exists`가 곧 `contract-tested`가 아니며, 자동 테스트가 통과해도 `live-characterized`와 `differential-passed`를 대신하지 못한다. 공식 SaaS가 바뀌면 관련 시나리오는 자동으로 이전 단계로 강등해 재검증한다.

현재 사실 기준:

- [x] 원자 페이지 저장의 1차 소스와 격리 계약 테스트가 존재한다. 신규 격리 DB는 `capabilities.atomic_save=true`이고, 성공·stale revision `409`·동일 멱등 재시도·같은 `operation_id`의 다른 요청 hash 충돌을 다룬다.
- [x] 기존 미이관 DB의 일반 읽기가 스키마를 바꾸지 않고 `capabilities.atomic_save=false`를 반환하며 원자 저장이 `503 atomic_save_unavailable`인 계약 테스트가 존재한다.
- [x] 명시적 원자 저장 migrator의 dry-run/apply 가드 테스트가 격리 경로에 존재한다.
- [ ] 운영 사용자 DB 원자 저장 마이그레이션은 실행하지 않았다.
- [ ] 전체 Notion 기능의 `live-characterized` 증거 세트는 아직 없다.
- [ ] 전체 기능의 `differential-passed` 판정은 아직 없다.
- [ ] 실시간 공동 편집, 네이티브 오프라인, 전체 IAM/Enterprise, 외부 서비스 생태계, Notion 고유 AI 결과의 동등성은 아직 입증되지 않았다.

기존 `docs/NOTION_PARITY_SPEC.md`의 기능표나 과거 테스트 수치는 참고 inventory이지 이 로드맵의 상태 증거가 아니다. 상태는 시나리오 ID에 연결된 최신 산출물로만 승격한다.

## 2. 공식 기준선과 변동 규칙

기능 계약의 우선순위는 공식 도움말 → 공식 개발자 문서/변경 기록 → 날짜가 있는 공식 릴리스 → 승인된 live characterization 순이다. 문서끼리 충돌하면 숨기지 않고 `reference-conflict`로 기록하고, UI parity와 공개 API parity를 별도 판정한다.

핵심 공식 기준:

- 편집기와 블록: [Writing & editing basics](https://www.notion.com/help/guides/writing-and-editing-basics), [What is a block?](https://www.notion.com/help/what-is-a-block), [Keyboard shortcuts](https://www.notion.com/help/keyboard-shortcuts)
- 탐색과 페이지: [Navigate with the sidebar](https://www.notion.com/help/navigate-with-the-sidebar), [Search](https://www.notion.com/en-gb/help/search), [Duplicate, delete & restore](https://www.notion.com/help/duplicate-delete-and-restore-content)
- 데이터베이스: [Introduction to databases](https://www.notion.com/help/intro-to-databases), [Database views](https://www.notion.com/help/category/database-views/all), [Views, filters, sorts & groups](https://www.notion.com/help/views-filters-and-sorts), [Database properties](https://www.notion.com/help/database-properties)
- 협업과 권한: [Sharing & permissions](https://www.notion.com/help/category/sharing-and-collaboration), [Comments, mentions & reactions](https://www.notion.com/help/comments-mentions-and-reminders), [Inbox & notifications](https://www.notion.com/help/updates-and-notifications)
- 배포와 이식: [Import data](https://www.notion.com/help/import-data-into-notion), [Export content](https://www.notion.com/help/export-your-content), [Use pages offline](https://www.notion.com/help/use-pages-offline), [Mobile](https://www.notion.com/help/notion-for-mobile)
- 개발자 기준: [API introduction](https://developers.notion.com/reference/intro), [Working with views](https://developers.notion.com/guides/data-apis/working-with-views), [API changelog](https://developers.notion.com/page/changelog)
- 2026 변동 기준: [Developer Platform 3.5](https://www.notion.com/releases/2026-05-13), [Notion 3.6](https://www.notion.com/releases/2026-07-01), [High contrast mode](https://www.notion.com/releases/2026-07-30), [Meeting Notes trigger](https://www.notion.com/releases/2026-07-31), [Custom Agent share context](https://www.notion.com/releases/2026-08-07)
- 종료 기준: [Notion Mail shutdown](https://www.notion.com/en-gb/help/notion-mail-inbox-is-going-away-what-to-do-next), [workspace PDF export phase-out](https://www.notion.com/help/export-your-content)

2026-08-12에 반드시 별도 표본으로 관리할 변동 사항:

- API 최신 버전은 `2026-03-11`이며 `after → position`, `archived → in_trash`, `transcription → meeting_notes` 파괴적 변경과 view API가 추가됐다.
- 도움말에는 Feed view가 있지만 공개 View API의 지원 enum과 완전히 일치하지 않을 수 있다. Map/Place, Form/Button, linked views도 UI와 API 지원 폭이 다르므로 UI 관찰 결과를 API 계약으로 추론하지 않는다.
- Developer Platform의 CLI와 Workers는 public beta, External Agents API는 private beta, Agent SDK는 private alpha다. 모두 moving track이다.
- 2026-07-01에는 External Agents, 대화형 HTML 블록, AI Meeting Notes 발화자 라벨, Office 파일 입출력과 Outlook 도구가 발표됐다.
- 2026-07-30 high contrast는 web/desktop 범위다. 2026-07-16 Agent calendar 도구는 desktop 우선이므로 플랫폼 차이를 합치지 않는다.
- Notion Mail inbox는 2026-09-22 종료 예정이므로 새 독립 Mail 클론을 parity 필수 범위로 만들지 않는다.
- Business/Enterprise 전체 workspace PDF export는 2026-08-31까지 단계적 종료 예정이다. 개별 페이지 PDF와 HTML/Markdown/CSV 계약은 별도다.
- Archive와 Presentation처럼 beta/점진 배포 기능은 채택 여부를 재검토하는 moving track으로 둔다.

## 3. Bounded context와 소유권

Notion parity가 Second Brain 내부 상태를 직접 소유하지 않도록 다음 경계를 유지한다.

| Context | 소유 상태 | 허용된 호출 | 금지된 결합 |
|---|---|---|---|
| Parity Reference & Evidence | 기준 URL, 기준 날짜/빌드, 시나리오, 관찰·diff 결과 | 모든 NP gate에 읽기 제공 | 제품 런타임에서 공식 사이트 scraping |
| Workspace & IAM | workspace shell, page tree, favorites, trash, local share/role policy | Document/Database 접근 판정 | Second Brain 사용자·조직을 별도 복제해 두 진실 만들기 |
| Document Core | page, block, file, revision, atomic save, history | Editor, Search, Export, Publication snapshot | thought/item을 page row로 암묵 변환 |
| Editor Runtime | selection, focus, command, undo/redo, drag/drop, slash/inline menu | Document command만 호출 | DB나 Second Brain 파일 직접 쓰기 |
| Database Engine | data source, schema/property, rows, formula/rollup, saved view | Document page 참조, view query | 표시 순서를 canonical row 진실로 취급 |
| Collaboration | comment/thread/reaction, notification, presence, realtime operation | IAM 권한과 Document revision 사용 | ACL 우회, stale overwrite, 알림을 canonical truth로 취급 |
| Discovery & Workflows | search, backlinks, mentions, reminders, templates, import/export | 각 source context의 공개 query/command | 읽기 요청에서 schema 변경·reindex 부작용 |
| Public Experience | public link, form submit, Sites-like renderer | 명시적 published snapshot/permission | 인증 화면의 내부 데이터 누출 |
| Platform Sync | device cache, offline manifest, sync/conflict queue | versioned Document/Database commands | 서버 revision을 건너뛴 last-write-wins |
| Automation & Connections | trigger, action, webhook, external adapter | idempotent service command | 외부 응답 중 열린 DB transaction, 비밀 저장 누출 |
| AI & Agents | prompt/context policy, proposed action, meeting artifacts | 권한 검증된 read, 승인된 command | 모델이 권한·위험 등급·최종 파괴 결정을 선택 |
| Second Brain Core | source import, candidates, thoughts, graph, publication, session binding | 기존 어댑터/서비스 계약만 | Notion parity migration에 편승한 schema/write 전환 |

의존 방향은 `UI → context service → repository`이며 브라우저 payload나 모델 출력이 저장소를 직접 갱신하지 않는다. 공통 사용자 identity는 인증 주체 ID로 연결하되, 각 context의 revision·lifecycle은 독립이다.

## 4. Second Brain 불변 계약

모든 NP pull request와 배포 gate는 아래를 회귀 검증한다. Notion 유사 UI를 맞추기 위해 이 계약을 바꾸려면 별도 ADR와 사용자 승인이 필요하다.

- [ ] 기존 `/api/code/knowledge/*` source import, chat promote, inbox review/action, thought CRUD/list, graph, pin/relationship 계약을 유지한다.
- [ ] Notion `publish_preview`와 `publish_second_brain`은 현재 인증 사용자, 명시적 target, 안정적 source identity와 immutable snapshot/provenance를 보존한다.
- [ ] `개인`, `현재 파트`, `개인 + 현재 파트` 정책을 도입/연결할 때 `both`는 한 transaction에서 서로 다른 item/revision history를 만들고 이후 자동 동기화하지 않는다.
- [ ] 기존 개인 지식·채팅·Notion·인증 데이터를 추론으로 파트 지식에 배정하지 않는다. active part membership은 최대 하나이고, 미배정 사용자는 개인 범위만 갖는다.
- [ ] Agent conversation은 정확히 하나의 concrete knowledge space와 scope epoch에 묶인다. 범위 전환은 새 대화이며 기존 대화를 mutate하지 않는다.
- [ ] Notion page revision과 Second Brain knowledge revision을 하나의 revision으로 합치지 않는다. publication은 snapshot/lineage 계약이다.
- [ ] 기존 session save/load/list/rename/delete, Notion page attach/detach, chat import, Second Brain template route의 method/path/response compatibility를 유지한다.
- [ ] 충돌 시 사용자 초안을 보존하고 강제 overwrite하지 않는다. 공유 지식 편집은 lease/fencing/CAS를 우회하지 않는다.
- [ ] LLM/OCR/network는 DB transaction 밖에서 수행하고 commit 직전에 authority와 revision을 재검증한다.
- [ ] canonical mutation, audit, outbox는 같은 transaction이다. semantic merge/edge/deactivate/purge를 모델이 자동 실행하지 않는다.
- [ ] deactivate는 되돌릴 수 있고, 영구 purge는 관리자 전용이며 90일은 자동 삭제가 아니라 eligibility다.
- [ ] 장기 dual-write를 만들지 않는다. canonical state와 재생성 가능한 projection을 구분한다.
- [ ] 테스트는 `CODE_ASSIST_DATA_ROOT`와 `CODE_ASSIST_TEST_DATA_ROOT`로 격리하고 live `user_data/`, `knowledge/`, `.runtime`, `sessions/`, `workspace/`를 쓰거나 마이그레이션하지 않는다.
- [ ] Knowledge Core part writes와 실제 migration은 이 로드맵에 의해 활성화되지 않는다.

회귀 증거의 현재 기준 경로는 `tests/test_notion_atomic_save_compat.py`, `tests/test_notion_routes.py`, `tests/knowledge_spaces/regression/`이다. 경로가 존재한다는 사실만으로 모든 체크박스가 완료된 것은 아니며, 각 NP gate 실행 결과와 evidence hash가 있어야 체크한다.

## 5. NP-0~NP-11 실행 계획

### NP-0 — 기준선·검증 플랫폼

범위: 공식 기능 taxonomy, 플랜/역할/플랫폼 matrix, 시나리오 DSL, live characterization, network/state/DOM/a11y/visual capture, deterministic normalization, differential runner, 결과 보존, 법무 검토.

의존성: 없음. 모든 후속 NP 단계가 의존한다.

- [x] `docs/notion_parity_manifest.json`에 기준일, 11개 baseline profile, NP-0~NP-11 taxonomy, 기능 상태·동등성 vocabulary, Second Brain hard gate를 machine-readable하게 고정했다.
- [x] `tools/verify_notion_parity_manifest.py`와 격리 단위 테스트가 phase/profile/evidence/status의 누락·과장·경로 탈출을 차단한다.
- [x] `docs/notion_parity/catalog/`에 schema와 NP-2/NP-3 첫 6개 시나리오, 공식 source, profile, 열린 reference conflict, Second Brain hard gate를 machine-readable하게 고정했다. 모든 live/differential claim은 아직 false다.
- [ ] `tests/notion_parity/contracts/`에 저장소 계약 runner를 만든다.
- [x] `tools/notion_parity/differential.py`와 policy에 state, keyboard/focus, a11y, semantic visual, error recovery, geometry tolerance, latency 비교의 deterministic core를 구현했다. 실제 공식 제품과 로컬 앱에 같은 action을 재생하는 runner는 아직 없다.
- [x] `docs/notion_parity/EVIDENCE_POLICY.md`와 `tools/notion_parity/evidence.py`에 evidence 경로·hash·상태 승격·민감정보 redaction을 fail-closed로 검증하는 정책을 구현했다.
- [ ] 승인된 live capture 도구를 만들고 서비스 약관에 맞는 수동 로그인 세션만 사용한다.
- [ ] `artifacts/notion_parity/<baseline>/<scenario>/`에 비식별 증거·hash·환경 manifest를 출력하고 민감 콘텐츠는 commit하지 않는다.
- [x] 픽셀·브랜드 복제가 아닌 semantic token, geometry tolerance, focus order, accessible name/state, keyboard result 비교 정책을 코드와 기본 policy에 고정했다.
- [x] 공식 문서 충돌과 점진 배포를 `reference-conflict`/`unknown`으로 표현하고, 근거 없는 상태 승격을 catalog verifier가 거부한다.

Gate NP-0: taxonomy 누락 감사 0, 모든 시나리오에 source/plan/platform/role/date 존재, redaction 검증, runner repeatability, 법무·보안 승인. manifest, 첫 catalog, evidence validator와 deterministic comparator core는 구현됐지만 live capture/action runner, 실행 artifact, 전체 taxonomy catalog, 법무·보안 승인은 남아 있으므로 NP-0 전체를 완료로 표시하지 않는다.

### NP-1 — 런타임·호환 경계

범위: 앱 factory/route registration, 인증·cookie/CSRF/Origin, per-user data root, Notion/Code 앱 공존, schema capability, transaction/idempotency/error envelope, health/readiness, logging/metrics, 기존 DB 무변경 open.

의존성: NP-0. NP-2~NP-10의 기반이다.

- [x] `docs/notion_route_contract.json`과 검증기로 main 50개·bridge 9개 route의 method/path/endpoint 및 15개 atomic/Second Brain hard route를 동결했다.
- [x] `docs/notion_route_semantics.json`과 strict verifier로 59개 rule/61개 operation의 auth, transport, required field, success status/content type/key, error profile 및 15개 hard operation을 동결했다. 이는 현재 handler characterization이며 Notion 제품 parity 주장이 아니다.
- [x] 원자 저장 1차 구현과 격리 source/contract test가 있다.
- [x] 신규 DB의 `atomic_save=true`, legacy DB의 무변경 read와 `/save` 503 계약이 테스트되어 있다.
- [x] dry-run 기본, exact user/path, backup confirmation, checksum을 요구하는 명시적 migrator와 격리 테스트가 있다.
- [x] 이번 NP-1 개발·검증에서는 운영 DB에 migrator를 실행하지 않았다.
- [x] store의 일반 read connection을 기존 DB의 read-only/query-only open으로 분리하고, DDL·tree repair·디렉터리 생성을 명시적 bootstrap/maintenance 경계로 옮겼다.
- [x] 두 publish route가 하나의 불변 `PageSourceSnapshot`/legacy publication adapter를 사용하면서 기존 URL·응답·권한·잠금·파일명·importer metadata 계약을 유지한다. 새 UI 승인은 preview snapshot hash를 보내며 불일치 시 target write 전 409로 중단하고, 기존 무본문 호출은 호환 유지한다.
- [x] main/side-peek dirty save → preview → 명시 승인 순서와 save conflict/preview failure/cancel의 no-publish 계약을 독립 JavaScript 회귀로 고정했다.
- [x] `app_notion.py` 시작 시 모든 사용자 DB를 열고 tree repair하던 동작을 제거하고, exact user/path·dry-run·exclusive backup·fingerprint recheck·명시 확인을 요구하는 `tools/repair_notion_workspace_tree.py` maintenance 경계와 격리 테스트로 이전했다. 운영 CLI는 실행하지 않았다.
- [x] unsafe main/bridge route의 unauthenticated, missing Origin, missing CSRF, cross-origin, valid same-origin 요청과 public route 예외/near-miss를 독립 negative matrix로 고정했다.
- [x] Notion route의 malformed/non-object JSON, validation, 404/405/413, domain conflict 및 unhandled 오류를 stable code/message/no-store envelope로 통합했다. filename-bearing OS 오류·public renderer·file delivery·AI provider 진단은 내부 path/exception을 반사하지 않는다.
- [x] 누락/wrong-type `blocks`가 전체 삭제로, 문자열 `false`가 true로, invalid share role·chat mode·block type이 다른 값으로 조용히 보정되지 않도록 mutation 입력을 fail-closed로 검증한다.
- [x] Inbox/Search/Overview/AI의 선택적 source 실패는 기존 성공 payload를 유지하면서 `degraded`와 `degraded_sources`로 정상 empty와 구분한다. 명시한 AI `page_id`를 읽지 못하면 다른 페이지 context로 답하지 않는다.
- [x] atomic save의 block write 중간 실패와 operation-ledger insert 실패를 주입해 metadata·blocks·revision·versions·idempotency ledger가 모두 rollback됨을 격리 DB에서 검증했다.
- [ ] legacy PATCH page + PUT blocks와 atomic POST save의 capability negotiation 및 지원 종료 조건을 문서·telemetry로 고정한다.
- [x] publish/session/template/chat/thought의 method/path/delegation/response, auth, immutable snapshot 및 UI 승인 순서를 NP-1 회귀 suite에 포함했다.
- [ ] 모든 mutation에 owner/actor, authorization, idempotency, bounded transaction, stable error code 원칙을 적용한다.

Gate NP-1: 새/legacy 임시 DB 계약, route snapshot, auth negative matrix, crash/fault injection, Second Brain 불변 suite가 통과하고 live data metadata diff가 0이다. 운영 migration은 별도 승인 전까지 gate 항목이 아니다.

### NP-2 — Workspace·IAM

범위: workspace switcher/settings, sidebar resize/collapse/reorder, Favorites/Shared/Private/Teamspaces, page/subpage tree, breadcrumb, recents, create/rename/move/duplicate/lock/archive/restore/permanent-delete policy, guests/members/groups, page/teamspace permissions, public link permission inheritance.

의존성: NP-0, NP-1. NP-5·NP-7·NP-10이 권한 결과에 의존한다.

- [ ] sidebar의 hover, context menu, drag target, keyboard navigation, focus restore, narrow viewport를 characterise한다.
- [ ] workspace/member/guest/group/teamspace와 page-level capability matrix를 plan·role별로 고정한다.
- [x] 독립 canonical workspace catalog의 첫 backend slice로 workspace/member/guest/group/teamspace, membership/security epoch, CAS/idempotency, audit/outbox와 legacy page identity binding을 구현했다. legacy page resolver와 UI는 아직 이 catalog로 cutover하지 않았다.
- [x] NP-2A schema `2026081303`에서 raw SQL IAM/source/ledger 불변식, 원 required-capability replay 재인가, page source pending→revalidate→CAS activation/quarantine, bounded readiness와 explicit deep-health를 계약 테스트로 보강했다. 이전 catalog schema는 request에서 자동 변환하지 않고 fail-closed다.
- [x] exact-owner legacy DB만 여는 read-only inventory와 guarded page locator backfill CLI를 구현했다. deterministic secret-free plan, source/catalog drift·workspace version·backup·quiesce gate, 페이지별 pending→revalidate→CAS와 부분 실패 후 새 승인 재개를 임시 DB에서 검증했으며 운영 migration은 실행하지 않았다.
- [x] schema `2026081304`에 active page binding generation별 secret-free file/public locator projection과 동기식 exact-owner projector/shadow counter를 구현했다. opaque file ID와 token digest만 저장하고 stale proof·unsafe path·권한/CAS conflict는 write 0으로 실패한다.
- [x] schema `2026081305`에 immutable parent outbox와 consumer별 lease/retry/dead-letter/CAS delivery 상태, allowlist partial claim index와 세대 fencing을 구현했다. 활성화 이벤트를 처리하는 offline/quiesced worker는 bounded at-least-once·state-idempotent로 동작하고 superseded generation은 즉시 terminal 처리한다. legacy source mutation event와 continuous runtime 연결은 아직 없다.
- [x] workspace 하나를 명시한 admin-scoped keyset으로 raw binding row를 최대 32개씩 읽는 read-only reconciliation을 구현했다. inactive row도 cursor를 전진시키며, exact-owner source와 binding generation/IAM을 I/O 전후에 확인하고 identifier-free counter만 반환한다. 전체 legacy DB fingerprint와 catalog ledger write는 shadow pass에서 수행하지 않는다.
- [x] legacy owner shard의 page/block/file/public/share/lifecycle 변경을 동일 SQLite transaction의 coalesced watermark로 기록하는 explicit migration과 최대 32개 read-only reader를 구현했다. 상속 share·public·page-tree 영향은 descendant까지 표시하고 source/full-sweep generation으로 batch 사이 drift와 간접 의존성 full sweep 필요를 fencing한다. 운영 shard에는 설치하지 않았고 destructive watermark ack와 continuous runtime consumer는 연결하지 않았다.
- [x] schema `2026081306`에 workspace admin-scoped durable healing run/checkpoint foundation을 구현했다. trusted clock 기반 lease expiry, fencing generation, row-version CAS, persisted keyset cursor, exact replay와 identifier-free counter가 crash-safe resume를 제공한다. 1306의 shadow mode는 bounded reconciliation 결과만 기록하며 locator projection을 고치는 apply가 아니다.
- [x] schema `2026081307`에 exact owner/source generation fence와 secret-free receipt에 묶인 offline/quiesced healing apply를 구현했다. 각 page effect는 capture 뒤 source fence와 재검증을 통과해야 하며 locator replace, version·audit·outbox와 durable effect replay를 한 catalog transaction으로 수렴시킨다. continuous runtime worker나 watermark ack는 아니다.
- [x] schema `2026081308`에 allowlisted quarantine reason과 source generation, batch owner별 integrity attestation을 구현했다. 영구 legacy-permission/unsafe-path/ambiguous/oversize outcome은 binding quarantine과 기존 file/public locator 제거를 같은 effect transaction에서 수행하며 transient 또는 revalidation 중 drift는 cursor를 전진시키지 않는다. archive 복원은 1308 단독으로 delete+same-ID recreation ABA와 구별할 proof가 없어 자동 재활성화하지 않는다. 이 identity gap은 다음 항목의 legacy incarnation v2와 1309 attestation/rebind 계약으로 닫는다.
- [x] legacy source incarnation v2 `2026081302`와 workspace schema `2026081309`를 격리 구현했다. source는 page lifetime별 nonce/generation history를 보존하고, catalog는 full fence receipt·lineage·review operation을 append-only attestation으로 결속한다. active explicit review와 quarantined binding의 atomic full-rebind store API, exact replay-before-source-I/O, proof actor와 binder provenance 분리, canonical commitment 기반 readiness/deep-health를 계약 테스트로 고정했다. exact-sentinel full-rebind offline wrapper도 source drift rollback과 response-loss replay까지 검증했으며 운영 적용은 하지 않았다.
- [x] H5a non-destructive foundation으로 page healing effect ID를 run fence/version, source epoch, full-sweep generation, binding generation에 결속하고 identifier-free exact source receipt를 immutable operation result/audit/outbox에 영속화했다. effect commit 뒤 checkpoint 실패 재시도는 catalog owner fence와 current/previous binding generation으로 ledger를 source I/O 전에 찾아 counter/checkpoint를 한 번만 수렴시킨다. source watermark DELETE/ack와 continuous scan loop는 포함하지 않는다.
- [x] H5b source-local coordination foundation으로 legacy source `2026081303`에 owner pass/work/operation ledger를 추가했다. trusted-clock lease, token digest, fencing generation, row-version CAS와 exact operation replay로 start/materialize/reclaim을 보호하고, fixed source epoch/full-sweep/file identity 아래 최대 32개 v2 incarnation receipt만 durable work로 복사한다. exact v2→v3 dry-run/backup/atomic-replace migrator와 legacy reader successor 호환성도 검증했다. watermark DELETE/ack, catalog effect claim loop, catalog-wide full-sweep completion과 continuous runtime 등록은 포함하지 않는다.
- [x] H5c disabled/offline convergence로 materialized source work와 catalog locator-apply effect를 exact receipt/fence로 결속했다. catalog effect를 먼저 commit하고 source terminal receipt를 별도 transaction으로 CAS하며, 두 transaction 사이 crash와 catalog checkpoint response loss는 각 immutable operation ledger replay로 source I/O·중복 counter 없이 수렴한다. zero pending, completed locator run, exact owner fence를 모두 확인한 full-sweep completion operation이 있어야 required sweep 뒤 다음 pass를 시작할 수 있다. 명시한 workspace에 없는 work는 pending으로 남고 watermark DELETE/clear/ack, continuous runtime worker, resolver/UI 등록은 없다.
- [x] H5d no-ack rehearsal로 한 owner의 `applied` terminal work가 참조한 모든 explicit workspace의 completed locator-run set을 exact하게 결속했다. 1~32 run을 canonical order/unique workspace로 제한하고 누락·중복·`ignored/no_binding` coverage를 거부한다. H5e 감사에서 `superseded` generation은 새 binding에 다시 적용될 수 있으므로 source-terminal/completion 대상이 아니라 pending retry debt로 바로잡았다. identifier-free lag는 pending/superseded 또는 required-completion 미기록을 구분하며, 두 workspace 처리·source/catalog store restart·replay-before-callback·다음 no-ack pass의 durable duplicate 수렴을 검증했다. destructive ack/runtime 등록은 여전히 없다.
- [x] H5e-0 design freeze로 latest incarnation↔watermark coverage와 materialized work보다 current watermark generation/mask가 뒤로 가지 않는 semantic inspector를 추가하고, source v4 `2026081304` 후보의 single-consumer registration, immutable ack batch/receipt, exact conditional DELETE, crash/restore/redaction/rollout gate를 문서와 read-only machine verifier로 고정했다. contract 상태는 `proposed_not_authorized`, `implemented=false`, `destructive_ack_enabled=false`이며 source v4 또는 ack API는 아직 존재하지 않는다.
- [x] NP2B-0 design freeze 뒤 NP2B-1 workspace `2026081310` backend foundation을 구현했다. per-member sidebar/section preference와 generation-stable favorite identity, current-visibility filtering, independent CAS, exact operation/audit/outbox replay, raw SQL/deep-health guard와 explicit frozen 1309→1310 migrator를 격리 검증했다. NP2B-2 navigation route overlay도 strict JSON/auth/CSRF/no-store/zero-leak machine contract로 검증했다. NP2B-3은 server capability와 kill switch 모두를 요구하는 default-off UI, member-only workspace switch, server snapshot precedence, serialized optimistic mutation, exact replay/reload/rollback, draft/preference flush, section keyboard/focus와 responsive resize를 contract-tested했다. 실제 flag/live migration은 비활성이고 Recents write는 retention 결정 전까지 금지한다.
- [x] NP2C-0 design/machine contract와 NP2C-1 workspace `2026081311` backend foundation을 구현했다. frozen 1310→1311 migration은 기존 workspace마다 `unmaterialized` tree state만 만들고 tree node/lifecycle receipt는 fabricate하지 않는다. generation-fenced node와 lifecycle saga의 raw/service-context guard, deep-health와 admin readiness는 격리 검증했다. 후속 `2026081312`는 claim/reclaim/heartbeat의 최대 300초 lease·fencing과 live source-applied/finalize 전이, command↔workspace-operation namespace를 raw SQL guard로 강화하고 pre-kernel 1311 command row를 fail-closed 거부한다.
- [x] NP2C-2a offline initial materializer를 구현했다. replay가 sentinel/source path보다 먼저이고, owner별 exact snapshot 2회 중 full integrity는 1회만 수행한다. active attested binding 최대 1,000개를 parent-first/dense ordinal snapshot으로 만들며 hidden parent·cycle·source/binding drift는 write 0으로 거부한다. state/nodes/operation/audit/outbox는 한 catalog transaction이다. source mutation, watermark ack, runtime registration, resolver cutover는 없다.
- [x] NP2C-2b disabled lifecycle command kernel을 1312 invariant 위에 구현했다. internal store API는 prepare/claim/reclaim/write-free lease acknowledgement/record-source/finalize/fail/manual-audit와 exact replay를 제공하고, disabled orchestration은 fake/temp source ledger로 source commit 후 response loss와 catalog finalize outbox fault를 수렴시킨다. finalize는 current admin, live lease/fence, tree/binding/incarnation generation, exact source receipt를 재검증하고 command/operation/audit/outbox를 한 transaction에 commit한다. 실제 legacy rename/title/pre-state나 tree·binding·locator projection 갱신은 하지 않으며 source adapter, route/UI, runtime command, resolver와 permanent delete는 계속 비활성이다.
- [x] NP2C-3a rename source-adapter/receipt 설계를 동결하고 NP2C-3b를 machine contract v7로 구현했다. 기존 `page_save_operations`는 full page/block 결과, page-delete cascade, immutable row/schema authentication 부재 때문에 거부한다. 새 source DDL 없이 frozen v3 immutable operation ledger를 `np2c.lifecycle` namespace로 재사용하며, route-unregistered connection-scoped adapter가 temporary exact owner shard에서 request conflict, no-op, changed rename, exact response-loss replay와 rollback을 검증했다. Changed rename은 하나의 page UPDATE로 revision·page watermark·source epoch·full-sweep generation을 각각 정확히 1 증가시키고 incarnation을 보존한다. 10,000 block/16 MiB version snapshot, 256-hop permission bound, atomic-revision capability, permission/lock/stale-state와 raw-title 비노출도 fail closed다. Catalog proof/attestation/locator/tree-node 갱신에는 lifecycle 전용 frozen 1313 guard가 필요하며, linked-reference proof fan-out 때문에 exact owner full sweep 전까지 tree를 `partial`로 유지한다. 이 3b checkpoint에서는 1313 migration/finalizer가 없었고, 바로 다음 3c 항목에서 구현됐다. runtime route/UI와 real user shard apply는 계속 없다.
- [x] NP2C-3c workspace `2026081313` lifecycle catalog finalization을 구현했다. Frozen 1312와 독립인 exact 1312→1313 migrator는 pre-finalizer lifecycle command row를 거부하고 lifecycle-only service context와 active-proof refresh guard를 추가한다. Changed finalize는 source receipt를 catalog write 전후에 재검증하고 binding proof/generation, owner-canonical incarnation attestation, full locator projection, tree node, workspace row/security version, command/operation/audit/outbox를 한 transaction에 commit한 뒤 tree를 `partial`로 내린다. No-op은 binding/tree/workspace version을 올리지 않는다. Legacy v1 finalizer와 unconfigured raw writer는 DB level에서 거부된다. Source와 catalog를 결합하는 sentinel rehearsal, route/UI/runtime command, resolver와 real user shard/catalog apply는 아직 없다.
- [x] NP2C-3d route-unregistered exact-sentinel rename rehearsal을 구현했다. Frozen legacy-v3 source adapter와 1313 catalog finalizer만 조합하고 새 schema·route는 만들지 않는다. New effect는 quiescence·sentinel·exact source/catalog containment를 먼저 요구하며, finalized catalog replay는 이 모든 filesystem/source I/O 전에 exact source request를 검증해 반환한다. 임시 fixture에서 changed/no-op, source commit 응답 손실, catalog commit 응답 손실, source commit 뒤 catalog transaction rollback, 양쪽 immutable receipt replay, raw title/identifier 비노출, target binding/projection/tree refresh와 `partial` full-sweep debt를 검증했다. Cross-database exactly-once가 아니라 at-least-once/state-idempotent saga이며 runtime command·resolver·worker·ack·live apply는 계속 비활성이다.
- [x] NP2C-3e1 create/move/reorder source adapter를 machine contract v11로 구현했다. Legacy source는 새 schema 없이 frozen v3 operation ledger의 action별 receipt/post-manifest digest를 사용하고 H5e source v4 예약을 유지한다. Route-unregistered adapter는 replay-derived `p_<32hex>` create identity, exact parent edit/lock와 rollback, resulting parent 기반 move/reorder 분류, old/new sibling·subtree·parent-block prestate CAS, source/structure changed 분리, immutable response-loss replay와 secret-free result를 temporary shard에서 검증했다. Bounds는 old/new 각 10,000 sibling, 1,000-node subtree, 최대 21,002 page commitments와 256 MiB aggregate다. 이 slice가 증명한 frozen 1313의 표현력 부족은 바로 다음 3e2의 1314 effect-evidence/order-generation/multi-proof guard schema로 닫혔다. Catalog finalizer·rehearsal은 아직 미구현이고 runtime/live apply는 계속 비활성이다.
- [x] NP2C-3e2 workspace `2026081314` structural effect guard foundation을 machine contract v12로 구현했다. Frozen 1313을 독립 보존하는 explicit 1313→1314 migrator/CLI는 verified backup과 exact source/target schema를 요구하고 live command·invalid tree를 거부한다. 1314는 generation-scoped sibling order, immutable structural manifest/effect rows, create/move/reorder action-shape·scope·proof guards, fail-closed command/source-receipt UDF, exact structural operation result validator와 deep-health correlation을 추가하며 source receipt나 command를 fabricate하지 않는다. Legacy-v1 disabled orchestration은 1313+에서 source/catalog I/O 전 거부된다. Atomic structural catalog finalizer와 sentinel rehearsal은 NP2C-3e3로 남고 route/UI/runtime/live apply는 비활성이다.
- [x] NP2C-3e3 workspace `2026081315` structural finalization authority와 exact-sentinel source↔catalog rehearsal을 machine contract v13으로 구현했다. Frozen 1314를 독립 보존하는 explicit 1314→1315 migrator/CLI는 live command를 거부하고 root parent를 null-safe하게 검증하며 기존 evidence를 보존한다. Route-unregistered source adapter는 한 command 내부 여러 page/block DML을 페이지별 한 logical watermark generation으로 transaction 안에서 coalesce하고 immutable v3 receipt를 남긴다. Catalog finalizer는 complete old/new sibling scope와 subtree, binding/projection/attestation/tree, workspace/security/tree versions 및 command/operation/audit/outbox를 한 transaction에 수렴시킨다. Finalized replay는 sentinel/path/source I/O보다 먼저 검증되며 temporary fixture의 create/reorder/cross-parent move, lease expiry, rollback, source/catalog response-loss와 identifier-free report가 통과했다. Runtime route/UI/worker/resolver/live migration은 비활성이다.
- [x] NP2C-4a duplicate/Trash/restore contract design을 machine contract v14로 동결했다. 최신 공식 동작에 맞춰 Archive와 Delete/Recycle Bin을 분리하고 현재 `archive_page`/`/trash` 호환 경로는 canonical `trash`로 정의했다. Same-owner duplicate는 replay-stable page/block/file mapping과 contained hash-verified byte staging, Trash는 exact active transition cohort, restore는 same-incarnation cohort와 original 또는 reviewed fallback placement를 요구한다. 이 historical 4a checkpoint 당시 frozen 1315는 multi-page duplicate와 lifecycle transitions를 표현하지 못해 candidate 1316이 필요했고 DDL/migration/source adapter/finalizer/rehearsal은 아직 없었다. 후속 4b/4c가 source adapter와 guard schema를 구현했다. Public duplicate는 NP2E, official Archive/unarchive는 source-lineage gate, purge는 retention/legal gate로 유지한다.
- [x] NP2C-4b route-unregistered source-v3 duplicate/Trash/restore adapter와 deterministic file recovery를 machine contract v15로 구현·검증했다. Exact replay는 quiescence/page/path/file I/O보다 먼저 실행되고, duplicate는 bounded replay-derived identities·fresh incarnations·internal-reference remap·external-reference refusal·contained exclusive staging/no-overwrite publish/exact-existing recovery를 사용한다. Trash는 exact active cohort만 전환하고 restore는 prior Trash receipt/same incarnation/original 또는 reviewed fallback placement만 허용한다. Focused 18개와 인접 source-v3 회귀 47개가 temporary shard에서 통과했으며 live data/runtime 경로는 사용하지 않았다.
- [x] NP2C-4c workspace `2026081316` lifecycle-subtree guard schema를 machine contract v16으로 구현·검증했다. Frozen 1315를 독립 보존하는 explicit 1315→1316 migrator/CLI는 live·historical duplicate/Trash/restore command와 ambiguous non-active tree history를 거부하고 evidence나 성공을 fabricate하지 않는다. 1316은 canonical action/state, immutable bounded subtree manifest/effect/object mapping, duplicate pending binding와 exact Trash/restore binding/tree transition, fail-closed command/source-receipt/source-generation UDF, exact operation/audit/outbox/deep-health correlation을 추가한다. Runtime·live migration은 비활성이다.
- [x] NP2C-4d workspace `2026081317` atomic lifecycle-subtree finalization authority를 machine contract v17로 구현·검증했다. Frozen 1316을 독립 보존하는 explicit 1316→1317 migrator/CLI는 live command/evidence를 거부한다. 1317은 최대 11,004개의 transitioned/unchanged parent·sibling effect와 active→active proof refresh를 fail closed로 허용한다. Route-unregistered finalizer는 duplicate/Trash/restore의 binding/projection/attestation/tree/subtree evidence와 operation/audit/outbox를 한 catalog transaction에 수렴시키고, exact-sentinel orchestration은 finalized replay를 sentinel/path/quiescence/source I/O보다 먼저 수행한다. Runtime·route/UI·resolver·live migration·watermark ack는 비활성이다.
- [x] NP2C-4e independent audit와 bounded adversarial rehearsal을 machine contract v18로 완료했다. Source commit 뒤 catalog acknowledgement 또는 finalizer failure는 immutable source operation replay를 precondition recapture보다 먼저 수행해 source 재적용 없이 수렴한다. Catalog fault phases `after_evidence|after_bindings|after_tree`는 전부 원자 rollback되고, source/catalog exact bound 11,004와 11,005 pre-parse refusal, identifier/path/digest/lease-secret redaction, deep-health와 adjacent lifecycle/tree regressions을 별도 checkpoint에 고정했다. Full-owner receipt finalization은 tree를 `complete`로 유지하고 `full_sweep_required=true`는 watermark/cutover debt로 남긴다.
- [x] NP2C-5 official Archive/unarchive reference contract를 machine contract v19로 설계 동결했다. Official Archive는 active binding/locator와 IAM을 유지하되 default sidebar/search/database view에서 숨는 canonical state이며 legacy `pages.archived`/`archive_page`/historical `action='archive'` Trash 의미를 재사용하지 않는다. H5e source v4 `2026081304`를 exact predecessor로 보존한 candidate source v5 `2026081305`와 candidate workspace `2026081318`을 필요하지만 미구현으로 고정했다. 다음은 승인된 Business/Enterprise fixture의 5a live-characterization matrix이며 DDL/runtime/live migration은 미승인이다.
- [x] NP2C-5a 승인·실행·완료판정 준비 harness를 별도 matrix v1, strict verifier/adversarial tests, external authorization inspection/preflight, evidence-bundle acceptance verifier와 runbook으로 고정했다. `--inspect-only`는 record 구조/redaction과 canonical digest 후보만 검증하고 authorization 관련 boolean을 모두 false로 유지한다. 사용자가 exact digest를 별도 채널에서 승인한 다음 current-time preflight가 최대 24시간 승인 창, Business/Enterprise 합성 tenant, 독립 reviewer, public/plan-transition 승인과 normal-UI-only를 확인하며, record 한 field의 변경도 승인 digest를 무효화한다. 완료 verifier는 같은 승인 digest와 repository 밖 exact root의 명시 파일만 읽어 fixture/action/result/redaction bytes와 canonical decision을 모든 exact scenario/profile pair에 결속하고 unknown/conflict를 거부한다. 모든 단계는 implementation/completion 권한을 거부한다. 상태는 `prepared_not_authorized_not_executed`이며 사용자 승인 digest·tenant·operator·reviewer·bundle이 없어 live observation과 5a completion은 0이다.
- [ ] 권한 상속·명시 공유·public access가 검색, mention, relation, comment, API 결과에서 동일하게 차단되는지 검증한다.
- [x] 첫 zero-leak 보강으로 shared/public payload와 public duplicate의 page reference hydration이 접근 불가능한 sibling의 제목·아이콘·ID를 노출하지 않도록 하고 격리 보안 테스트를 추가했다. 이는 통합 capability resolver 완료를 뜻하지 않는다.
- [ ] destructive UI는 preview/confirmation/audit/복구 가능성을 보장하고 Second Brain purge 정책과 혼동하지 않는다.

NP-2는 다음 vertical slice 순서로 구현한다. 각 slice는 데이터 모델, API, UI, negative/security/a11y test를 함께 끝내며 Second Brain 회귀를 필수 gate로 둔다.

1. **NP-2A canonical workspace substrate와 NP2B-1/2 navigation API** — workspace/IAM, hardened source proof, locator projection, guarded 1303→1309 migration, offline healing/quarantine, incarnation attestation/rebind와 H5a~H5d non-destructive convergence는 구현됐다. NP2B-1은 그 frozen 1309 위에 explicit 1310 navigation migration과 sidebar preference/favorite store API를 추가했고, NP2B-2는 Notion host 전용 protected route overlay를 연결했다. NP2C-1은 frozen 1310 위에 explicit 1311 tree/saga backend schema만 추가했다. conditional ack와 continuous scheduling은 destructive semantics·restore fault suite의 별도 승인을 요구하며 navigation runtime flag/Recents와 tree command도 별도 gate다. 그 전에는 directory-scan resolver cutover를 연결하지 않으며 `app_code.py`와 Second Brain source/data 경계는 변경하지 않는다.
2. **NP-2B sidebar와 page tree** — 실제 workspace switch, Teamspaces/Shared/Private/Favorites/Recents, 서버 저장 preference와 view-event 기반 recent, tree keyboard/focus/drag를 구현한다.
3. **NP-2C atomic lifecycle와 Trash** — create/rename/move/duplicate/trash/restore를 idempotent audited command로 통합하고, 별도 Archive/unarchive capability와 retention·영구 삭제·file 복구 정책을 분리한다.
4. **NP-2D unified capability resolver** — user/group/teamspace/workspace/public grant의 broadest effective access와 Full access/edit/comment/view 및 DB 세부 capability를 하나의 판정기로 통합한다. list/search/relation/mention/backlink/file/export/AI/session context가 같은 판정을 사용한다.
5. **NP-2E public subtree projection** — 공개 root와 effective-public descendant 탐색·asset·duplicate 범위를 일치시키고, 숨겨진 descendant와 private relation을 anonymous crawl로 fail-closed 검증한다.

Gate NP-2: 역할별 leakage 0, tree lifecycle differential pass, route/IAM 회귀와 a11y keyboard pass.

### NP-3 — Document·Editor Core

범위: page header/icon/cover/display settings, block tree, text/headings/lists/to-do/toggle/quote/callout/divider/code/equation/media/file/bookmark/embed/table/columns/synced block/table of contents/breadcrumb/button/page/tab/HTML block, rich text, links/mentions/reminders, slash/inline/color/link menus, Markdown shortcuts, Enter/Backspace/Tab, drag/drop, multi-select, clipboard, undo/redo, IME, focus/caret, autosave/conflict/history.

의존성: NP-1 atomic foundation, NP-2 ownership. NP-4·NP-5·NP-6·NP-8이 사용한다.

- [ ] block·rich-text schema와 unsupported/degraded block round-trip 정책을 동결한다.
- [ ] slash/`@`/`[[`/inline menu의 query, keyboard selection, dismiss, focus return을 characterise한다.
- [ ] block create/convert/nest/outdent/reorder/duplicate/delete, multi-select, clipboard의 selection semantics를 differential test한다.
- [ ] Korean IME composition, grapheme, bidi, paste sanitization, large document, virtualization과 a11y를 검증한다.
- [ ] page/header/layout, columns collapse, simple table merged cells, synced blocks, tabs, HTML sandbox의 platform별 차이를 기록한다.
- [ ] autosave debounce, retry, stale revision, operation reuse, history restore, side/center peek draft 보존을 검증한다.

Gate NP-3: editor command matrix와 block round-trip, crash/reload/conflict, keyboard/focus/a11y, Second Brain publication snapshot regression이 differential pass.

### NP-4 — Database Engine

범위: database/data source/page 관계, inline/full-page/linked database, schema/property 편집, title/rich text/number/select/multi-select/status/date/person/files/checkbox/URL/email/phone/formula/relation/rollup/created/edited/unique ID/place/verification, templates/layouts, table/board/timeline/calendar/list/gallery, saved views, filter/sort/group/sub-group, quick filter, property visibility, row/page edit, side/center/full peek.

의존성: NP-1~NP-3. NP-6·NP-7·NP-9·NP-10이 의존한다.

- [ ] canonical data source와 view-local configuration을 분리하고 stable ID/rename/delete semantics를 동결한다.
- [ ] 모든 property의 null/empty/coercion/validation/display/edit/filter/sort/group 계약을 표본화한다.
- [ ] formula syntax/type/error/dependency recalculation과 relation/rollup cycle·permission·deleted target를 검증한다.
- [ ] 각 view의 create/switch/reorder/duplicate/delete/copy link, view-local settings와 개인별 tab display preference를 검증한다.
- [ ] table resize/reorder/freeze, board drag status update, timeline span resize, calendar date move, gallery cover와 peek default를 characterise한다.
- [ ] linked view와 source permission, pagination/cache/concurrent mutation을 UI/API별로 따로 검증한다.

Gate NP-4: property conformance, formula/rollup corpus, view state isolation, concurrency/restart, plan·permission negative matrix가 differential pass.

### NP-5 — Realtime·협업

범위: multi-user document/database editing, presence/cursors/selections, operation order and convergence, reconnect, comments/page discussion/inline/block threads, replies/reactions/resolve/reopen/delete, suggested edits where applicable, mentions/reminders, following/notify settings, Inbox grouping/read/archive/reply/badges/push/email/mobile actions.

의존성: NP-1 revision/idempotency, NP-2 IAM, NP-3/NP-4 mutation model.

- [ ] transport/session/reconnect/resume protocol과 server authority를 설계하고 presence를 canonical content에서 분리한다.
- [ ] concurrent text and non-text operation corpus에 대해 convergence, causality, duplicate/out-of-order delivery를 검증한다.
- [ ] permission revoke, account disable, page move/archive 중 열린 editor를 즉시 fence한다.
- [ ] comment anchor가 text/block edit, duplicate, archive/restore에서 어떻게 유지·orphan되는지 characterise한다.
- [ ] Inbox grouping, unread counts, notification preferences, mention/reminder delivery를 역할·플랫폼별로 검증한다.
- [ ] offline와 realtime이 만나는 conflict policy는 NP-8과 공동 gate로 운영한다.

Gate NP-5: 다중 브라우저 convergence와 no-stale-overwrite, revoke latency, comment/Inbox differential, reconnect/fault/load pass. 로컬 단일 사용자 기능으로 이 gate를 대체할 수 없다.

### NP-6 — 탐색·업무 기능

범위: global search, database search, quick find, filters/sorts, keyboard results, preview/open, recents, backlinks, page links, mentions, reminders, favorites, page templates/database templates, buttons, page verification, wiki/home, feed, dashboards, charts, maps, layouts, presentation/archive beta, print.

의존성: NP-2~NP-5. 검색은 권한·index freshness를 상속한다.

- [ ] title/body/property/comment 범위, ranking, typo/tokenization, Korean search, recent/history behavior를 characterise한다.
- [ ] 결과 preview, keyboard focus, open-in-peek/full, inaccessible/deleted item 누출 방지를 검증한다.
- [ ] backlinks/link/mention/reminder lifecycle과 rename/move/archive 영향을 검증한다.
- [ ] template apply/duplicate와 button action을 deterministic command 및 idempotency 계약으로 만든다.
- [ ] chart aggregation, dashboard grid/widget, map/place, feed ordering, wiki/verification을 각각 독립 track으로 검증한다.
- [ ] presentation/archive beta는 고정 core gate를 막지 않는 moving track으로 유지한다.

Gate NP-6: deterministic search corpus recall/precision, ACL leakage 0, index recovery, feature별 interaction differential 및 a11y pass.

### NP-7 — Forms·Sharing·Sites

범위: database-backed forms, field mapping/required/type/conditional logic, anonymous/authenticated submit, response lifecycle, share menu, people/groups/guests permissions, web link/duplicate allowance, public page subtree, publish/unpublish, site navigation/favicon/share preview/SEO/custom domain/analytics, public files and embeds.

의존성: NP-2 IAM, NP-4 DB, NP-5 notifications, NP-6 discovery.

- [ ] form builder/preview/submit/error/success, duplicate submission, rate/abuse protection, locale/timezone/file upload을 검증한다.
- [ ] public renderer가 unpublished/private/ancestor-restricted content와 file URL을 누출하지 않게 한다.
- [ ] share dialog의 role changes, invite lifecycle, copy link, public toggle와 duplicate subtree semantics를 characterise한다.
- [ ] Sites 유료 기능과 custom-domain add-on을 core sharing과 분리하고 플랜별 UI를 기록한다.
- [ ] 검색 엔진 indexing, preview metadata, analytics consent, domain ownership과 rollback을 별도 운영 gate로 둔다.

Gate NP-7: anonymous/authenticated threat matrix, permission revocation, form correctness, public crawl/cache invalidation, plan differential pass.

### NP-8 — Mobile·Offline

범위: responsive web, iOS/Android navigation, no-hover controls, bottom navigation, mobile Inbox gestures, widgets/share extension/camera/voice, desktop/mobile offline download manager, manual/automatic downloads, first-view DB cache, offline create/edit, sync queue, conflict UI, platform feature gaps.

의존성: NP-1~NP-5, 특히 revision/convergence. 네이티브 앱은 별도 제품 트랙이다.

- [ ] web/desktop/iOS/Android support matrix와 device/OS minimum을 고정한다.
- [ ] mobile은 columns를 single column으로 collapse하고 multi-block selection/import/settings 제약 등 실제 차이를 같은 동작으로 오판하지 않는다.
- [ ] offline은 web 미지원, 모든 plan의 수동 page download, paid plan의 recents/favorites 자동 download를 plan별로 검증한다.
- [ ] database offline은 첫 view 첫 50 rows, subpage는 자동 download되지 않는 기준을 표본화한다.
- [ ] embed/AI/form/button 등 offline unavailable UI와 share/permission offline 금지를 검증한다.
- [ ] text/non-text 충돌, reconnect ordering, partial upload, clock drift, storage pressure와 recovery를 검증한다.
- [ ] keyboard/screen reader/reduced motion/high contrast/touch target/dynamic type를 플랫폼별 gate로 둔다.

Gate NP-8: supported device matrix, airplane-mode/restart/storage fault, conflict/no-loss, sync observability, mobile/a11y differential pass. responsive web만으로 native parity를 주장하지 않는다.

### NP-9 — 자동화·연결

범위: database automations, triggers/schedules/buttons/webhooks, actions, templates, Slack/GitHub/Google Drive/Figma/Zapier/Outlook/Calendar 등 connections, sync databases, OAuth/token lifecycle, MCP, Developer Platform CLI/Workers, usage credits.

의존성: NP-1 IAM/idempotency, NP-4 DB, NP-5 audit/notification, NP-7 public callbacks.

- [ ] trigger/action catalog와 plan limitation을 공식 기준으로 고정하고 exactly-once 효과 대신 idempotent-at-least-once 계약을 명시한다.
- [ ] retry/backoff/dead-letter/cancel/replay, operation ID, audit/outbox와 secret rotation을 검증한다.
- [ ] 각 외부 공급자는 독립 adapter/contract test/kill switch/data-processing review를 갖는다.
- [ ] OAuth consent/revoke/token expiry/scope reduction과 external deletion을 검증한다.
- [ ] API와 UI의 linked views, Feed/Map/Place/Form/Button 지원 차이를 `reference-conflict`로 유지한다.
- [ ] Workers/CLI/sync는 beta moving track이며 Business/Enterprise credits/usage dashboard 변화를 감시한다.

Gate NP-9: sandbox credentials, webhook signature/replay protection, retry chaos, secret redaction, provider별 contract와 legal/privacy 승인이 있어야 한다. 실제 외부 계정이 없는 mock만으로 live parity를 주장하지 않는다.

### NP-10 — AI·Agents·Enterprise

범위: workspace Q&A/search/write/transform, page context actions, model routing, citations, AI Meeting Notes/transcription/speaker labels, Custom Agents/tools/triggers/sharing, External Agents, HTML blocks, file read/write, enterprise search/connections, SAML/SCIM/domain/guest controls/content search/audit/security/retention/eDiscovery/DLP/analytics.

의존성: NP-1~NP-9의 권한·데이터·automation 기반.

- [ ] AI 결과는 exact wording이 아니라 권한, tool call, citation/provenance, structured action, refusal, latency/error envelope로 평가한다.
- [ ] prompt injection, inaccessible context, stale permission, exfiltration, destructive action과 human approval을 adversarial test한다.
- [ ] Meeting Notes consent/recording/locale/speaker-label correction/artifact lifecycle을 platform별로 검증한다.
- [ ] Custom Agent share-menu context, meeting-summary trigger, calendar/Outlook tools, people directory, audit log를 plan별로 characterise한다.
- [ ] External Agents API(private beta), Agent SDK(private alpha), Workers(public beta), interactive HTML blocks를 core와 분리한다.
- [ ] HTML은 sandbox/CSP/network/storage/clipboard/a11y 경계가 통과하기 전 실행하지 않는다.
- [ ] Enterprise 기능은 실제 승인된 tenant와 관리자 역할 없이는 `live-characterized`로 올리지 않는다.

Gate NP-10: permission/citation/action eval, red-team, data retention/privacy, enterprise role matrix와 감사 증거 통과. 공급자 고유 모델의 bit-for-bit 결과는 제외한다.

### NP-11 — 이관·Pilot·최종 판정

범위: legacy Notion DB capability migration, data reconciliation, feature flags, canary/pilot, support/telemetry, rollback/restore, full acceptance, moving release rebaseline, deprecation/sunset, 사용자 문서와 법무 sign-off.

의존성: 채택하는 모든 NP gate. NP-11은 미완료 트랙을 숨기는 마감 단계가 아니다.

- [ ] 지원 범위 manifest를 `core`, `plan/platform conditional`, `moving`, `separate product`, `excluded`로 동결한다.
- [ ] scenario state audit에서 필수 항목이 모두 `differential-passed`이고 unresolved reference conflict가 0인지 확인한다.
- [ ] exact 사용자/DB 목록, backup/restore drill, checksum, change window, owner sign-off가 있는 migration plan을 승인한다.
- [ ] internal → allowlisted users → one workspace → staged wider rollout 순서로 canary하고 kill switch와 support runbook을 검증한다.
- [ ] 데이터·권한·revision·files·public links·comments·views·Second Brain publication/session bindings를 reconcile한다.
- [ ] moving feature와 sunset calendar를 release watch에 연결하고 회귀 budget/SLO를 정한다.
- [ ] 제품 명칭, visual design, help text, attribution과 마케팅 문구에 대한 법무/브랜드 검토를 완료한다.

Gate NP-11: restore drill, migration idempotency, no-loss reconciliation, performance/accessibility/security, pilot soak, full differential report, Second Brain 불변 suite, 운영·법무 승인. 그 전에는 “완벽 동일”을 출시 문구로 쓰지 않는다.

## 6. 단계 의존성과 병렬화

```text
NP-0 ──> NP-1 ──> NP-2 ──> NP-3 ──> NP-4
                 │        │        ├──> NP-6 ──> NP-7
                 │        └──> NP-5 ┘             │
                 │                 └──> NP-8 <─────┘
                 └────────────────────> NP-9 ──> NP-10

채택된 모든 gate ───────────────────────────────> NP-11
Second Brain 불변 계약 ────────────────────────> 모든 NP gate
```

NP-0 이후 공식 기준 조사와 repository contract test는 병렬화할 수 있다. UI-first 계획에 정의된 isolated/default-off Core shell과 editor는 NP-1 안전 API 위에서 즉시 vertical slice로 진행할 수 있다. 다만 UI를 먼저 흉내 내어 데이터·권한·충돌 의미를 거짓으로 고정하거나 server authorization을 client concealment로 대체해서는 안 된다. NP-6의 검색과 NP-7 public renderer도 제한된 read-only vertical slice로 조기 실험할 수 있지만, 각각 채택된 권한·데이터 계약을 우회할 수 없다.

## 7. 산출물·명령 계약

아래 표는 현재 구현된 초기 원장과 후속 계약 경로를 구분한다. `[ ]` 경로는 아직 존재한다고 주장하지 않는다.

| 목적 | 계약 경로 | 현재 |
|---|---|---|
| 본 실행 계획 | `docs/NOTION_PARITY_ROADMAP.md` | 작성됨 |
| 기존 기능 참고 명세 | `docs/NOTION_PARITY_SPEC.md` | 존재, 상태 증거로 단독 사용 금지 |
| 초기 기준선 manifest | `docs/notion_parity_manifest.json` | [x] 존재, live/differential 완료 주장은 없음 |
| manifest 검증기 | `tools/verify_notion_parity_manifest.py` | [x] 존재 |
| manifest 검증 테스트 | `tests/test_verify_notion_parity_manifest.py` | [x] 존재 |
| NP-1 route 계약 | `docs/notion_route_contract.json` | [x] 59 rules, 15 hard routes |
| NP-1 route 검증기 | `tools/verify_notion_route_contract.py` | [x] 실제 Flask `url_map`과 비교 |
| NP-1 route 검증 테스트 | `tests/test_verify_notion_route_contract.py` | [x] 존재 |
| NP-1 route 의미 계약 | `docs/notion_route_semantics.json` | [x] 59 rules, 61 operations, 15 hard operations |
| NP-1 route 의미 검증기 | `tools/verify_notion_route_semantics.py` | [x] strict manifest/route-surface 검증 |
| NP-1 route 의미 검증 테스트 | `tests/test_verify_notion_route_semantics.py` | [x] 존재 |
| NP-1 auth negative matrix | `tests/test_notion_auth_contract.py` | [x] 존재 |
| NP-1 HTTP·입력·부분실패 계약 | `tests/test_notion_http_contract.py` | [x] 존재 |
| NP-1 tree maintenance CLI | `tools/repair_notion_workspace_tree.py` | [x] dry-run 기본, 운영 실행 안 함 |
| NP-1 tree maintenance 테스트 | `tests/test_repair_notion_workspace_tree_cli.py` | [x] 존재 |
| publication UI 계약 테스트 | `tests/test_notion_publication_ui.js` | [x] 존재 |
| 기능/시나리오 catalog schema/첫 catalog | `docs/notion_parity/catalog/schema.json`, `docs/notion_parity/catalog/workspace_editor_core.json` | [x] NP-2/NP-3 첫 6 scenarios, live/diff claim 없음 |
| catalog 검증기/테스트 | `tools/verify_notion_parity_catalog.py`, `tests/test_verify_notion_parity_catalog.py` | [x] 존재 |
| evidence 정책/검증기 | `docs/notion_parity/EVIDENCE_POLICY.md`, `tools/notion_parity/evidence.py` | [x] 민감정보·경로·hash·상태 승격 fail-closed |
| differential core/policy | `tools/notion_parity/differential.py`, `docs/notion_parity/policies/default_differential.json` | [x] deterministic semantic comparator; live action runner는 미구현 |
| evidence/differential 테스트 | `tests/test_notion_evidence_policy.py`, `tests/test_notion_differential.py` | [x] 존재 |
| NP-2A 구현 계약 | `docs/notion_parity/NP2A_WORKSPACE_SUBSTRATE.md` | [x] 첫 substrate와 잔여 cutover 경계 명시 |
| NP-2A canonical store | `notion_workspace_store.py` | [x] injected path, read-only GET, CAS/idempotency/audit/outbox |
| NP-2A P1 store hardening | `tests/test_notion_workspace_store_p1.py` | [x] schema 2026081303, DB 불변식·권한 replay·source lifecycle·deep-health |
| NP-2A route overlay | `routes_notion_workspace.py` | [x] Notion host 전용 workspace/IAM API |
| NP-2A store/route 테스트 | `tests/test_notion_workspace_store.py`, `tests/test_notion_workspace_routes.py` | [x] 존재 |
| NP-2A/NP2B-2 route overlay 계약/의미 | `docs/notion_workspace_route_contract.json`, `docs/notion_workspace_route_semantics.json` | [x] 12 rules, 19 operations, 19 hard operations |
| NP-2A route overlay 검증기/테스트 | `tools/verify_notion_workspace_route_contract.py`, `tests/test_verify_notion_workspace_route_contract.py` | [x] legacy 59/61/15 불변과 app_code 미등록 검증 |
| NP-2A auth/host isolation | `tests/test_notion_workspace_auth_isolation.py` | [x] CSRF/Origin/cookie와 Notion-only host 등록 검증 |
| NP-2A locator 기반 page projection | `notion_workspace_pages.py`, `tests/test_notion_workspace_pages.py` | [x] 명시 binding read-only; runtime file/public resolver cutover·UI는 미완료 |
| NP-2A exact-owner inventory/backfill | `notion_workspace_inventory.py`, `tools/migrate_notion_workspace_catalog.py`, `tests/test_migrate_notion_workspace_catalog.py` | [x] dry-run 기본, secret-free plan, guarded apply/resume; 임시 DB만 실행 |
| NP-2A secret-free locator projection | `notion_workspace_store.py`, `tests/test_notion_workspace_locator_projection.py` | [x] schema 2026081304, active generation·opaque file ID·token digest·atomic replace |
| NP-2A synchronous projector/shadow | `notion_workspace_projection.py`, `tests/test_notion_workspace_projection.py` | [x] exact-owner RO capture, unsafe path/stale proof·superseded generation 차단, identifier-free counters; runtime 미연결 |
| NP-2A outbox delivery/worker | `notion_workspace_store.py`, `notion_workspace_outbox.py`, `tests/test_notion_workspace_outbox_delivery.py`, `tests/test_notion_workspace_outbox.py` | [x] schema 2026081305, bounded lease/retry/dead-letter, generation fence, offline/quiesced activation-event 처리 |
| NP-2A/NP2B-1/NP2C guarded migration chain | 기존 migration modules, `notion_workspace_incarnation_migration.py`, `notion_workspace_navigation_migration.py`, `notion_workspace_page_tree_migration.py`, `notion_workspace_lifecycle_kernel_migration.py`, `notion_workspace_lifecycle_finalize_migration.py`, `notion_workspace_structural_effect_migration.py`, `notion_workspace_structural_finalize_migration.py`, `notion_workspace_lifecycle_subtree_migration.py`, `notion_workspace_lifecycle_subtree_finalize_migration.py`와 대응 tests | [x] frozen 1303→1304→1305→1306→1307→1308→1309→1310→1311→1312→1313→1314→1315→1316→1317, verified backup, rollback 및 ambiguous commit 수렴; 운영 미실행 |
| NP2C-3e1/3e2/3e3 structural source, catalog guards와 offline finalizer | `notion_legacy_page_structural.py`, `notion_workspace_structural_effect_migration.py`, `notion_workspace_structural_finalize_migration.py`, `notion_workspace_page_structural_rehearsal.py`, focused tests, NP2C machine contract/verifier | [x] machine contract v13, bounded create/move/reorder frozen-v3 exact replay, page-level logical watermark coalescing, 1314 effect/order/proof guards, 1315 atomic finalizer와 exact-sentinel replay/rollback을 temporary shard/catalog에서 검증; runtime 미등록 |
| NP2C-4a duplicate/Trash/restore design | `docs/notion_parity/NP2C_DUPLICATE_TRASH_RESTORE_DESIGN.md`, machine contract v14, verifier/adversarial tests | [x] 당시 Archive≠Trash 용어, deterministic duplicate IDs/files, exact Trash cohort, same-incarnation restore placement와 candidate 1316 필요성을 설계 동결; 후속 4b/4c 구현과 별개인 historical gate |
| NP2C-4b duplicate/Trash/restore source adapter | `notion_legacy_page_duplicate_trash_restore_contract.py`, `notion_legacy_page_duplicate_trash_restore.py`, focused tests, machine contract v15 | [x] route-unregistered exact-owner v3 receipt, replay-derived subtree/file identities, recoverable byte staging, exact Trash cohort/restore placement; 이 historical 4b checkpoint에는 finalizer/rehearsal/runtime이 없었음 |
| NP2C-4c lifecycle-subtree catalog guards | `notion_workspace_lifecycle_subtree_migration.py`, focused tests, machine contract v16 | [x] frozen 1315→1316, canonical lifecycle action/state, immutable bounded subtree evidence/mapping, fail-closed raw-SQL binding/tree guards와 deep-health correlation; historical guard checkpoint |
| NP2C-4d lifecycle-subtree atomic finalizer/rehearsal | `notion_workspace_lifecycle_subtree_finalize_migration.py`, `notion_workspace_page_lifecycle_subtree_finalize.py`, `notion_workspace_page_lifecycle_subtree_orchestration.py`, focused tests, machine contract v17 | [x] frozen 1316→1317, transitioned/unchanged effect authority, atomic duplicate/Trash/restore catalog convergence, finalized replay-before-path와 exact-sentinel identifier-free rehearsal; runtime/live migration 미등록 |
| NP2C-4e source replay/adversarial audit | source adapter replay probe, lifecycle-subtree orchestration/finalizer focused tests, exact-bound test, machine contract v18, checkpoint | [x] source-commit/catalog-ACK·finalizer-loss 수렴, three-phase catalog rollback, 11,004 parity/11,005 pre-parse refusal, report redaction와 deep-health; runtime/live migration 미등록 |
| NP2C-5 official Archive source-contract design | `docs/notion_parity/NP2C_OFFICIAL_ARCHIVE_DESIGN.md`, machine contract v19, adversarial verifier tests, checkpoint | [x] Archive≠Trash, Business/Enterprise+edit gate, active binding/locator, default-hidden visibility, source v4→candidate v5/workspace 1318 lineage와 미확정 live matrix를 동결; implementation/runtime/live migration false |
| NP2C-5a live-characterization preparation | `np2c_archive_live_characterization_matrix.json`, authorization inspection/preflight, matrix/evidence-bundle strict verifiers and adversarial tests, `NP2C_ARCHIVE_LIVE_CHARACTERIZATION_RUNBOOK.md`, preparation checkpoints | [x] non-authorizing candidate-digest inspection → out-of-band exact user approval → ≤24h current-time preflight와 16 scenarios/11 profiles external-root exact-file/hash/redaction/reviewer/canonical-decision acceptance 준비; actual user approval·live execution·evidence·completion·implementation false |
| NP-2A bounded reconciliation | `notion_workspace_reconciliation.py`, `tests/test_notion_workspace_reconciliation_store.py`, `tests/test_notion_workspace_reconciliation_security.py` | [x] workspace-scoped keyset≤32, exact-owner read, generation/IAM pre·post fence, identifier-free·ledger-free shadow |
| NP-2A durable healing checkpoint | `notion_workspace_healing.py`, `notion_workspace_store.py`, `tests/test_notion_workspace_healing_store.py` | [x] schema 2026081306 foundation, trusted clock lease/fencing, row-version CAS, persisted keyset cursor, exact replay와 identifier-free counters |
| NP-2A source-fenced healing apply | `notion_workspace_healing_apply_migration.py`, `notion_workspace_healing.py`, `tests/test_migrate_notion_workspace_healing_apply.py`, `tests/test_notion_workspace_healing_apply.py` | [x] schema 2026081307, exact source fence/receipt, atomic page effect와 replay; offline/quiesced only |
| NP-2A reasoned quarantine | `notion_workspace_quarantine_migration.py`, `notion_workspace_projection.py`, `notion_legacy_changes.py`, `tests/test_migrate_notion_workspace_quarantine.py` | [x] schema 2026081308, owner integrity attestation, permanent outcome의 atomic locator 제거; archive restore는 explicit rebind pending |
| NP-2A legacy change watermark | `notion_legacy_change_migration.py`, `notion_legacy_changes.py`, `tools/migrate_notion_legacy_change_tracking.py`, `tests/test_migrate_notion_legacy_change_tracking.py`, `tests/test_notion_legacy_changes.py` | [x] explicit exact-owner migration, transaction-local coalescing, subtree/full-sweep generation, epoch-fenced keyset≤32; 운영 미설치·destructive ack/runtime consumer 미연결 |
| NP-2A source incarnation/attestation | `notion_legacy_incarnation_migration.py`, `notion_workspace_incarnation_migration.py`, `notion_workspace_incarnation_orchestration.py`, incarnation focused tests | [x] legacy v2 nonce/history와 schema 1309 attestation/lineage/readiness, exact replay, atomic rebind와 offline wrapper; 운영 적용 미완료 |
| NP-2A/NP2B-1/NP2C restored migration rehearsal | `tests/test_notion_workspace_migration_chain_rehearsal.py` | [x] frozen 1303→1317 순차 apply, retained 1316 restore→1317 재적용, fresh/migrated object equality·deep health; 임시 DB만 사용 |
| NP-2A watermark consumer H5a | `notion_workspace_healing.py`, `notion_workspace_store.py`, `tests/test_notion_workspace_healing_apply.py` | [x] source/full-sweep/binding-fenced effect ID, immutable identifier-free receipt, effect-commit/checkpoint-loss source-I/O-free replay; destructive ack/runtime loop 제외 |
| NP-2A watermark consumer H5b | `notion_legacy_change_consumer_contract.py`, `notion_legacy_change_consumer.py`, `notion_legacy_change_consumer_migration.py`, `tests/test_notion_legacy_change_consumer.py` | [x] source v3 durable pass/work/operation ledger, bounded ≤32 receipt materialization, trusted-clock lease/reclaim, exact v2→v3 migration; non-destructive |
| NP-2A watermark consumer H5c | `notion_legacy_change_consumer_orchestration.py`, `notion_workspace_healing.py`, `notion_workspace_store.py`, `tests/test_notion_legacy_change_consumer_orchestration.py` | [x] offline catalog-effect→source-terminal ordering, crash replay, exact completed-run full-sweep evidence와 next-pass gate; unbound pending 보존, DELETE/ack/runtime/resolver 제외 |
| NP-2A watermark consumer H5d | `notion_legacy_change_consumer.py`, `notion_legacy_change_consumer_orchestration.py`, `tests/test_notion_legacy_change_consumer_orchestration.py` | [x] exact multi-workspace completed-run set, identifier-free lag, source/catalog restart replay, no-ack repeated-pass duplicate 수렴; destructive ack/runtime 제외 |
| NP-2A watermark consumer H5e design | `docs/notion_parity/H5E_CONDITIONAL_ACK_DESIGN.md`, `docs/notion_parity/h5e_conditional_ack_contract.json`, `tools/verify_notion_h5e_ack_contract.py`, focused verifier test | [x] superseded pending 의미, source v4 후보와 exact eligibility/transaction/crash/restore/single-consumer/redaction gate; 미승인·미구현·ack disabled |
| NP-2B navigation backend/routes | design/contract/verifier, `notion_workspace_navigation_migration.py`, navigation store/route tests | [x] 1310 sidebar preference/favorites backend와 protected route overlay, visibility/replay/ledger/fault/privacy/auth/CSRF gate; UI/recents/live migration 비활성 |
| NP-2C page-tree/lifecycle foundation | `NP2C_PAGE_TREE_LIFECYCLE_DESIGN.md`, machine contract/verifier, `notion_workspace_page_tree_migration.py`, `notion_workspace_page_tree.py`, focused tests | [x] 1311 foundation + offline initial exact-source materializer; command/source mutation adapter/routes/UI/purge 비활성 |
| 기준선 manifest | `docs/notion_parity/baselines/<YYYY-MM-DD>.json` | [ ] 생성 필요 |
| contract tests | `tests/notion_parity/contracts/` | [ ] 생성 필요 |
| live/differential tests | `tests/notion_parity/differential/` | [ ] 생성 필요 |
| live capture/action runner | `tools/notion_parity/` | [ ] comparator/evidence core만 존재; capture/replay 필요 |
| 비식별 실행 증거 | `artifacts/notion_parity/<run-id>/` | [ ] 생성 필요, 기본 비커밋 |
| 원자 저장 source tests | `tests/test_notion_atomic_save_compat.py` | [x] 존재 |
| 원자 저장 migration tests | `tests/test_notion_atomic_save_migration_cli.py` | [x] 존재 |
| 원자 저장 browser runtime test | `tests/test_notion_atomic_save.js` | [x] 존재 |
| Second Brain 회귀 | `tests/knowledge_spaces/regression/` | [x] 경로 존재, NP별 실행 증거 별도 필요 |

모든 명령은 저장소 루트 `G:\AI_Agent\DEMOS_CODEX`에서 실행하고 테스트 전용 임시 root만 사용한다. 명령 구현이 없는 future 계약을 현재 CI 명령으로 제시하지 않는다.

현재 실행 가능한 표적 검증:

```powershell
.\venv\Scripts\python.exe -m code_assist_v1.tools.verify_notion_parity_manifest
.\venv\Scripts\python.exe -m code_assist_v1.tools.verify_notion_parity_catalog --compact
.\venv\Scripts\python.exe -m code_assist_v1.tools.verify_notion_route_contract --compact
.\venv\Scripts\python.exe -m code_assist_v1.tools.verify_notion_route_semantics --compact
.\venv\Scripts\python.exe -m code_assist_v1.tools.verify_notion_workspace_route_contract --compact

.\venv\Scripts\python.exe -m pytest `
  .\code_assist_v1\tests\test_verify_notion_parity_manifest.py `
  .\code_assist_v1\tests\test_verify_notion_parity_catalog.py `
  .\code_assist_v1\tests\test_verify_notion_route_contract.py `
  .\code_assist_v1\tests\test_verify_notion_route_semantics.py `
  .\code_assist_v1\tests\test_verify_notion_workspace_route_contract.py `
  .\code_assist_v1\tests\test_notion_evidence_policy.py `
  .\code_assist_v1\tests\test_notion_differential.py `
  .\code_assist_v1\tests\test_notion_atomic_save_compat.py `
  .\code_assist_v1\tests\test_notion_atomic_save_migration_cli.py `
  .\code_assist_v1\tests\test_notion_workspace_store.py `
  .\code_assist_v1\tests\test_notion_workspace_routes.py `
  .\code_assist_v1\tests\test_notion_workspace_auth_isolation.py -q

Set-Location .\code_assist_v1
node tests\test_notion_atomic_save.js
node tests\test_notion_publication_ui.js
node tests\test_notion_editor_core.js
node tests\test_notion_ui_runtime.js
```

Second Brain/Knowledge Spaces regression은 marker 등록과 fixture 격리를 먼저 확인한 뒤 다음 계약으로 실행한다.

```powershell
.\venv\Scripts\python.exe -m pytest `
  .\code_assist_v1\tests\knowledge_spaces\regression -q
```

NP-0 scenario catalog와 live/differential 도구가 구현된 뒤에만 다음 명령 이름을 실제 CLI로 확정한다.

```text
python -m code_assist_v1.tools.notion_parity catalog audit
python -m code_assist_v1.tools.notion_parity characterize --scenario <ID>
python -m code_assist_v1.tools.notion_parity differential --baseline <ID> --scenario <ID>
python -m code_assist_v1.tools.notion_parity report --run <ID>
```

이 네 명령은 **아직 없는 도구의 인터페이스 제안**이며 실행 가능 또는 완료로 표시하지 않는다. 최종 도구는 stdout에 콘텐츠를 노출하지 않고 run ID, manifest hash, pass/fail/unknown과 artifact path만 출력해야 한다.

각 시나리오 증거의 최소 필드:

```json
{
  "scenario_id": "NP3-EDITOR-ENTER-001",
  "state": "live-characterized",
  "reference_date": "2026-08-12",
  "reference_source": "https://www.notion.com/help/...",
  "plan": "Free|Plus|Business|Enterprise|unknown",
  "role": "owner|member|guest|anonymous",
  "platform": "web|windows|macos|ios|android",
  "app_or_browser_version": "exact",
  "locale": "ko-KR",
  "input_hash": "sha256:...",
  "result_hash": "sha256:...",
  "evidence": ["relative/redacted/path"],
  "reference_conflicts": [],
  "reviewer": "...",
  "reviewed_at": "..."
}
```

## 8. 원자 저장 migration·rollout·rollback

현재 원자 저장은 “1차 source/test 완료, 운영 migration 미실행”이다. 신규 DB bootstrap과 기존 DB migration을 동일한 사건으로 취급하지 않는다.

### 적용 전

- [x] 신규 격리 DB에서 atomic schema와 capability가 생성된다.
- [x] legacy 격리 DB에서 ordinary GET이 schema/file state를 바꾸지 않는다.
- [x] legacy capability false에서 atomic `/save`는 503이며, legacy 경로는 호환을 위해 유지된다.
- [x] migrator 기본 모드는 SQLite read-only dry-run이다.
- [ ] 정확한 운영 user ID와 resolved `notion.db` 목록을 승인받는다.
- [ ] writes를 중지하고 `notion.db`와 존재하는 `-wal`/`-shm`을 포함한 application-consistent backup을 만든다.
- [ ] 별도 위치에서 restore drill과 page/block/file/revision checksum을 검증한다.
- [ ] dry-run 출력의 migration checksum과 target path를 change record에 고정한다.

명시적 dry-run 계약:

```powershell
.\venv\Scripts\python.exe -m code_assist_v1.tools.migrate_notion_atomic_save `
  --user-id EXACT_SANITIZED_USER_ID `
  --expected-db-path 'EXACT_RESOLVED_NOTION_DB_PATH'
```

적용은 승인된 maintenance window에서만 하며, dry-run에 표시된 checksum을 그대로 사용한다.

```powershell
.\venv\Scripts\python.exe -m code_assist_v1.tools.migrate_notion_atomic_save `
  --user-id EXACT_SANITIZED_USER_ID `
  --expected-db-path 'EXACT_RESOLVED_NOTION_DB_PATH' `
  --apply `
  --backup-confirmed `
  --confirm-checksum 'EXACT_DRY_RUN_CHECKSUM'
```

이 문서 작성 시점에 위 apply를 운영 DB에 실행하지 않았다.

### Canary와 soak

1. 테스트 fixture와 복원한 비운영 snapshot에서 dry-run/apply/repeat apply/verify를 수행한다.
2. 승인된 단일 canary 사용자에서 writes를 quiesce하고 backup/restore proof를 연결한다.
3. capability true 이후 page GET, atomic save, stale conflict, retry, files/history/publication/session binding을 smoke-test한다.
4. error rate, `409`, `503`, retry, latency, backup freshness를 정한 soak 기간 관찰한다.
5. 사용자 batch마다 같은 evidence를 생성하며 자동으로 다음 DB를 추론하지 않는다.

### Rollback

- 첫 atomic write 전 실패: 앱을 중지하고 검증된 pre-migration DB/sidecar snapshot을 복원한 뒤 이전 경로로 시작한다.
- 첫 atomic write 후 실패: 즉시 atomic/legacy writes를 모두 중지하고 incident DB를 보존한다. 구버전 binary를 변경된 DB에 단순 연결하지 않는다.
- reverse DDL을 현장에서 실행하지 않는다. 검증된 backup restore 또는 사전 검증된 forward fix만 사용한다.
- rollback 중 dual-write로 두 진실을 만들지 않는다. operation ledger/revision/audit 증거를 보존하고 reconciliation 후 traffic을 연다.
- Second Brain publication/session data는 별도 canonical 경계다. Notion DB 복원 범위와 함께 명시적으로 reconcile하고 암묵 롤백하지 않는다.

## 9. Moving beta·sunset·별도 트랙

| 분류 | 항목 | 처리 |
|---|---|---|
| Moving | Workers, CLI, External Agents API, Agent SDK, sync, HTML block, Agent tools | core 출시 gate와 분리; 30일 또는 공식 release event마다 re-characterize |
| Moving | Archive, Presentation, 점진 배포 UI | allowlisted account에서만 관찰; 제공되지 않으면 `unknown`, fail 아님 |
| Sunset | Notion Mail inbox, 2026-09-22 종료 | 새 클론 범위 제외; 기존 Outlook/메일 연결 호환과 데이터 export 안내만 별도 track |
| Sunset | Business/Enterprise 전체 workspace PDF export, 2026-08-31까지 종료 | 새로운 필수 parity에서 제외; 개별 page PDF와 HTML/Markdown/CSV는 유지 |
| Separate product | Notion Calendar native app, native iOS/Android, Web Clipper/widgets | web parity와 별도 예산·배포·스토어·device matrix |
| Separate adapter | Slack, Drive, GitHub, Figma, Zapier, Outlook 등 | 실제 공급자별 계약·보안·OAuth·sandbox 승인 없이는 parity 주장 금지 |
| Separate enterprise | SAML/SCIM/DLP/eDiscovery/domain/content search/admin analytics | 승인된 Enterprise tenant와 보안/법무 프로그램이 있어야 착수 |
| Excluded | 고유 AI 모델의 exact 문장/확률적 결과 | capability·권한·tool/citation/error eval로 대체 |
| Excluded | Notion API를 이용한 Notion 경쟁/실질 복제 | Developer Terms 위반 위험으로 금지 |
| Excluded | Notion 이름·로고·아이콘·카피·고유 visual identity/pixel clone | 독립 제품명·design tokens·문구 사용 |

release watch는 매주 공식 [Releases](https://www.notion.com/releases), Help Center, [API changelog](https://developers.notion.com/page/changelog)을 확인하고 다음을 수행한다.

- 새 기능: taxonomy에 `candidate`로 추가하고 plan/platform availability를 확인한 뒤에만 필수 범위로 승격한다.
- 변경: 영향 시나리오를 `live-characterized` 이하로 강등하고 기존 differential pass에 `superseded` 표시를 한다.
- sunset: 데이터 보존/export/user notice/feature flag removal 날짜를 등록하고 신규 투자 중단을 승인한다.
- 문서 충돌: UI와 API를 별도 scenario로 분리하고 live 결과를 기록하되 비공개 protocol을 역공학하지 않는다.

## 10. 법적·브랜드·API 경계

이 로드맵은 법률 자문이 아니다. 개발과 출시 전에 담당 법무 검토가 필요하다.

- [Notion Developer Terms §3.1](https://www.notion.so/Developer-Terms-ba4131408d0844e08330da2cbb225c20)은 API를 사용해 서비스를 복제·경쟁하거나 Notion 서비스를 실질적으로 복제하는 integration을 만드는 것을 제한한다. 따라서 이 독립 구현의 parity 연구/구현에 Notion API를 데이터 수집·복제 수단으로 사용하지 않는다.
- scraping, private endpoints, reverse engineering, rate-limit/security 우회, 서비스 취약성 시험은 금지한다. 공식 문서와 승인된 사용자의 정상 UI 조작으로 공개 동작만 관찰한다.
- [Notion Trademark Usage Guidelines](https://www.notion.so/Notion-Trademark-Usage-Guidelines-9826313c686a4f6e9d8a48347162714b)은 Notion 명칭을 app/product/domain/source identifier로 쓰거나, logo/visual identity/look-and-feel을 혼동 가능하게 모방하는 것을 제한한다.
- 내부 engineering ID의 `notion`은 현재 코드 호환 표식으로만 취급하고, 출시 제품명·도메인·logo에는 사용하지 않는다. “Notion 호환/대체” 같은 외부 표현도 법무 검토 없이는 쓰지 않는다.
- 아이콘, illustration, animation, help text, onboarding copy, template 콘텐츠, screenshot을 복사하지 않는다. 동작 결과는 독립 design system과 독자 문구로 제공한다.
- 공식 API를 상호운용 adapter로 제공할 경우 별도 제품/법무 track에서 최신 version header, capability, rate limit, payload/pagination/cache, OAuth consent, user deletion과 Developer Terms를 준수한다. 내부 API emulation과 Notion API 상호운용을 한 scope로 합치지 않는다.
- 플랜 제한을 우회하거나 유료/Enterprise 기능을 무료 계정 API 조합으로 노출하지 않는다.

출시 법무 gate:

- [ ] 제품명·domain·logo·icons·design token에 Notion mark 또는 confusing similarity가 없다.
- [ ] 공식 API 없이 독립 구현한 범위와 공식 API integration 범위가 기술·마케팅에서 분리된다.
- [ ] 모든 reference capture에 계정 권한, 개인정보 redaction, 보존 기간과 삭제 절차가 있다.
- [ ] third-party library/font/icon/media/template 라이선스 SBOM과 attribution을 검토한다.
- [ ] 외부 문구가 인증·제휴·공식 호환성 또는 전체 동등성을 암시하지 않는다.

## 11. Phase 공통 gate와 완료 체크리스트

각 NP phase는 아래 공통 조건을 모두 만족해야 `differential-passed`로 닫는다.

- [ ] 범위의 모든 scenario가 catalog에 있고 source·date·plan·role·platform이 연결됐다.
- [ ] happy path뿐 아니라 권한 거부, stale state, retry, duplicate, cancel, reload, network loss, large/empty/invalid input이 있다.
- [ ] deterministic contract tests가 격리 임시 경로에서 통과하고 live mutable root metadata diff가 0이다.
- [ ] 공식 reference를 정상 UI로 live-characterize했으며 비식별 evidence와 hash가 있다.
- [ ] differential comparison이 state transition, focus/keyboard, error/recovery, responsive/platform, a11y와 승인된 visual tolerance를 통과했다.
- [ ] 이전 NP gate와 Second Brain 불변 suite가 회귀 없이 통과했다.
- [ ] schema/data migration이 필요한 경우 dry-run, backup, restore, idempotency, reconciliation, rollback이 검증됐다.
- [ ] flags의 safe default는 off이고 readiness/telemetry/kill switch가 있다.
- [ ] security/privacy/abuse/legal/brand 검토와 소유자가 서명했다.
- [ ] 미지원·조건부·moving·excluded 항목이 사용자 문서에 정확히 노출된다.

최종 parity 판정 보고서는 기능 개수의 비율이 아니라 scenario별 네 상태와 미해결 목록을 제시한다. 하나라도 필수 시나리오가 `exists`, `contract-tested`, `live-characterized`, `unknown`, `reference-conflict`에 머물면 그 범위는 전체 동등이 아니다.

## 12. 다음 실행 순서

현재 Core lane은 아래 순서를 먼저 따른다. 이어지는 기존 번호 목록은 implementation/history inventory로
보존하고, 그 안의 NP2C-5a “다음” 문장은 Archive optional lane에만 적용한다.

1. UI-first scope checkpoint와 Core Free Web scenario overlay/verifier를 고정한다.
2. 실제 browser action/capture/redaction harness를 만들고 synthetic Free/local smoke를 통과한다.
3. `UI1-FREE-DESKTOP-GOLDEN-JOURNEY`로 shell·save/reload·Second Brain approval 계약을 고정한다.
4. workspace-scoped navigation과 page tree, editor/IME, database, search/share 순으로 vertical slice를 진행한다.
5. production resolver/migration/flag cutover는 기존 안전 gate와 별도 승인을 유지한다.
6. Business/Enterprise Archive는 conditional branch로만 진행한다.

1. NP-0의 첫 catalog/evidence/comparator를 CI에 고정하고, 승인된 브라우저가 준비되면 Free web/desktop 표본의 live capture/action runner를 연결한다.
2. NP-2A workspace 1303→1309와 legacy source v1→incarnation v2→consumer v3 migration chain, retained snapshot restore, exact-owner inventory/backfill, locator projection, offline healing/quarantine, attestation/rebind와 H5a~H5d non-destructive convergence를 격리 검증했다. NP2B-1/2/3은 frozen 1309→1310 navigation backend, protected route와 default-off UI를 추가했다. NP2C-0/1은 frozen 1310→1311 tree/saga backend foundation을 추가했다. H5e-0은 superseded-pending 의미와 source v4 conditional-ack 설계/machine gate만 고정했다. source v4, source-generation conditional ack, continuous runtime scheduling, navigation UI flag/Recents, tree runtime command는 미승인·비활성이다. 승인 전에는 destructive ack, resolver cutover 또는 UI flag enable을 하지 않는다.
3. NP2C-2a offline initial materializer, NP2C-2b disabled command kernel, NP2C-3a design gate, NP2C-3b source-v3 temp-shard rename adapter, NP2C-3c frozen 1312→1313 lifecycle catalog guard/finalizer와 NP2C-3d exact-sentinel end-to-end rehearsal이 완료됐다. 3d는 source changed/no-op, source/catalog 양쪽 response-loss, catalog rollback, title 비노출, tree `partial` 및 full-sweep debt를 임시 fixture에서 검증했다. NP2C-3e1 create·move·reorder legacy-v3 source adapter, NP2C-3e2 frozen 1313→1314 structural effect schema/guards와 NP2C-3e3 frozen 1314→1315 finalizer/rehearsal까지 완료됐다. NP2C-4a는 duplicate/Trash/restore design과 candidate 1316 필요성을 machine contract v14로 동결했고 NP2C-4b는 source-v3 adapter/file recovery를 machine contract v15로 검증했다. NP2C-4c는 frozen 1315→1316 lifecycle-subtree guards를 machine contract v16으로, NP2C-4d는 frozen 1316→1317 atomic catalog finalizer/exact-sentinel rehearsal을 machine contract v17로 구현·검증했다. NP2C-4e는 source-ledger-first recovery, catalog rollback, exact bounds, redaction와 deep-health를 machine contract v18로 독립 검증했다. NP2C-5는 official Archive reference/source/catalog contract를 machine contract v19로 설계 동결했고 5a preparation harness는 matrix/runbook과 external evidence-bundle acceptance verifier까지 준비했다. 다음은 여전히 `NP2C-5a` 승인된 Business/Enterprise live characterization이며 purge는 별도 retention/legal gate다. Recents는 immutable audit/outbox retention과 coalescing 결정을 먼저 닫는다.
4. NP-2D unified capability resolver의 zero-leak corpus를 통과한 뒤 NP-2E public subtree projection을 연다.
5. 같은 기준으로 NP-3 editor 핵심 시나리오를 live-characterize하고 differential runner에 연결한 뒤 block/keyboard/IME backlog를 구현한다.
6. NP-4 database/property/view corpus를 구축하고 NP-5 realtime 설계를 별도 위험 review로 시작한다.
7. NP-6/NP-7을 권한·public leakage gate와 함께 진행하고 NP-8 native/offline은 별도 예산으로 승인한다.
8. NP-9/NP-10의 외부·AI·Enterprise 범위는 법무/보안/실계정 조건이 갖춰진 adapter 단위로만 시작한다.
9. NP-11 전까지 운영 Notion DB migration, broad feature flags, Knowledge Core part writes를 실행하지 않는다.
