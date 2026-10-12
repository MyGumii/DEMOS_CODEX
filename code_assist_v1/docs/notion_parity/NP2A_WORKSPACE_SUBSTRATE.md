# NP-2A workspace substrate 구현 계약

기준일: 2026-08-13  
상태: `contract-tested`를 향한 첫 vertical slice. Notion live parity 또는 NP-2A 완료를 선언하지 않는다.

> **2026-08-14 scope note:** 이 substrate의 안전·migration·cutover 불변식은 계속 유효하다. 다만
> 현재 Core 제품 순서는 `../NOTION_UI_SECOND_BRAIN_XHIGH_HANDOFF_PLAN.md`를 따른다. Production resolver,
> continuous consumer와 live migration은 계속 금지하지만, isolated/default-off Free Web shell·editor UI는
> 이 substrate 위에서 병렬 개발할 수 있다. NP2C-5a Archive는 optional paid lane이며 Core UI 선행조건이 아니다.

## 목적

기존 `user_data/<owner>/notion/notion.db`는 페이지 본문과 첨부 파일의 legacy source로 유지한다. 신규 workspace catalog는 다음 상태만 canonical하게 소유한다.

- workspace와 row/security version
- active/revoked member와 membership epoch
- page access와 분리된 guest registry
- workspace group과 active member 구성
- open/closed/private teamspace와 membership
- legacy page identity의 workspace/teamspace placement binding
- actor-scoped operation ledger, audit event, durable outbox
- admin-scoped durable healing run, lease/fence, keyset checkpoint와 identifier-free counters
- allowlisted quarantine reason과 source generation에 따른 fail-closed locator visibility
- legacy page incarnation v2 nonce/history에 결속된 immutable catalog attestation, lineage와 resolver-cutover readiness
- NP2C page-tree materialization readiness, generation-fenced canonical node와 lifecycle command saga foundation

Notion page revision과 Second Brain revision, publication snapshot, Agent session binding은 이 catalog로 이전하거나 합치지 않는다.

## 저장 경계

구현: `notion_workspace_store.py`

- catalog 경로는 생성자에 명시적으로 주입한다. `config.py` 또는 live path를 module import 시 읽지 않는다.
- production adapter는 호출 시점의 Notion `USER_DATA_DIR`에서 `.notion-workspace-v2/workspace.db`를 계산한다.
- 일반 read는 SQLite `mode=ro`와 `query_only`를 사용하며, catalog가 없으면 디렉터리나 DB를 만들지 않고 `workspace_substrate_unavailable`이다.
- 최초 workspace 생성만 명시적 bootstrap 경계를 호출한다.
- 이미 존재하는 catalog의 schema/checksum/table 불일치는 request에서 repair하지 않는다. 별도 forward migration 또는 restore가 필요하다.
- 모든 mutation은 `BEGIN IMMEDIATE` 안에서 operation replay/hash, actor membership/role, `base_version`, domain write, security epoch, audit, outbox와 result ledger를 함께 처리한다.
- schema `2026081303`부터 operation ledger는 required capability와 canonical authorization context를 보존하고 raw `UPDATE`/`DELETE`가 차단된다. replay는 현재 IAM으로 원 권한을 다시 확인한 뒤 저장된 결과만 반환한다.
- page source는 `pending`으로 stage한 뒤 legacy authority를 transaction 밖에서 재검증하고 동일 proof generation을 CAS로 `active` 전환한다. stale source는 versioned quarantine으로 숨기며 replay 시 외부 callback을 다시 호출하지 않는다.
- 일반 request readiness는 bounded schema/checksum 검사만 수행한다. 전체 SQLite integrity/FK/semantic 검사는 명시적 `verify_deep_health()` maintenance gate로 분리한다.
- schema `2026081304`는 active page binding generation에 종속된 secret-free locator projection을 추가한다. page summary에는 projection commitment와 counts만, file row에는 opaque `f_<32 hex>` ID만, public row에는 raw token이 아닌 SHA-256 digest와 duplicate boolean만 저장한다. filename, MIME, stored/relative path, raw token은 catalog·result·audit·outbox에 저장하지 않는다.
- projection replace는 coarse IAM을 binding/version 조회보다 먼저 확인하고, 외부 exact-owner revalidation 전후에 IAM·binding generation·workspace CAS를 다시 검사한다. 동일 projection은 version/security epoch를 올리지 않으며, restage/quarantine/delete는 projection을 즉시 무효화한다.
- schema `2026081305`는 immutable parent outbox와 consumer별 delivery 상태를 분리한다. claim은 allowlisted activation event만 최대 32개 lease하며 retry/backoff/dead-letter와 token CAS를 사용한다. 누적 parent scan은 event/aggregate/created/id partial index로 제한하고, superseded binding generation은 legacy DB를 열기 전에 terminal 처리한다.
- 현재 worker는 명시적 offline/quiesced batch다. crash 뒤 effect 재실행은 exactly-once가 아니라 bounded at-least-once와 semantic no-op으로 수렴하며, background thread·startup hook·continuous runtime 등록은 없다.
- `notion_workspace_reconciliation.py`는 workspace 하나와 admin actor를 명시하고 raw binding row를 keyset으로 최대 32개씩 읽는다. inactive row도 cursor를 전진시켜 DB 작업량을 제한하고, active binding만 exact-owner source에서 shadow 비교한다. binding generation과 현재 IAM은 legacy I/O 전후에 확인하며 report에는 식별자·경로·token digest·예외 문자열을 넣지 않는다.
- `notion_workspace_reconciliation.py`의 raw shadow pass 자체는 read-only이며 operation/audit/outbox/checkpoint를 만들지 않는다. continuation cursor는 내부 typed DTO이고 HTTP token이나 운영 로그로 노출하지 않는다.
- schema `2026081306`과 `notion_workspace_healing.py`는 bounded shadow 결과를 위한 durable run/checkpoint foundation을 추가한다. workspace admin, trusted clock 기반 lease expiry, fencing generation, row-version CAS, persisted keyset cursor와 exact replay를 재검증하며 audit/outbox에는 식별자 없는 counter만 기록한다. 1306의 shadow mode 자체는 locator projection을 변경하지 않는다.
- schema `2026081307`은 exact owner/source generation fence와 secret-free receipt에 묶인 locator-apply mode를 추가한다. offline/quiesced batch는 source capture와 revalidation을 통과한 page effect만 commit하며 locator replace, 관련 version·audit·outbox와 durable effect replay를 같은 catalog transaction에 묶는다. request hook이나 continuous runtime worker는 없다.
- schema `2026081308`은 allowlisted quarantine reason과 source generation을 binding에 추가한다. owner별 첫 사용에서 full integrity/FK preflight를 수행한 integrity attestation을 batch 안에서 재사용하고, 이후 exact identity/schema/source fence를 다시 확인한다. typed permanent outcome은 binding을 quarantine하고 기존 file/public locator를 같은 effect transaction에서 제거한다. transient failure와 revalidation 중 source drift는 checkpoint cursor를 전진시키지 않는다.
- legacy change tracking v2 `2026081302`는 page incarnation nonce와 generation history를 source shard에 append-only로 보존한다. workspace schema `2026081309`는 exact v2 receipt와 full source fence를 immutable attestation으로 기록하고 lineage rollback/nonce-generation ABA를 거부한다. active review와 quarantined→pending→active full rebind는 operation/audit/outbox/projection commitment와 한 transaction에서 결속된다. `bound_by_subject_id`는 binder provenance로 유지하고 실제 source proof actor는 reviewed generation commitment에서 별도로 도출한다.
- resolver readiness는 pending binding이 0이고 모든 active generation의 canonical authorization/result/attestation/audit/outbox와 현재 locator projection SHA/count가 정확히 일치할 때만 true다. malformed ledger replay는 current IAM을 재확인한 뒤 source I/O 전에 fail-closed한다. internal store API와 exact-sentinel offline attestation/full-rebind orchestration까지 있으며 runtime resolver와 UI는 아직 연결하지 않았다.
- schema `2026081310`은 per-member navigation preference/favorite state를 추가한다. schema `2026081311`은 기존 workspace마다 `unmaterialized` page-tree state만 만들고, full owner+page parent identity와 binding/incarnation/change receipt에 결속된 canonical node 및 prepared→source_applied→finalized/failed/manual_audit lifecycle saga DDL을 추가한다. schema `2026081312`는 최대 300초 command lease, exact claim/reclaim/heartbeat fencing, live source-applied/finalize 전이와 command↔workspace-operation namespace guard를 추가하고 pre-kernel 1311 command row가 있으면 migration을 거부한다. schema `2026081313`은 lifecycle-only service context와 exact command/workspace/security/tree-state CAS를 추가하고, changed rename의 proof·incarnation attestation·full locator·tree node 갱신과 workspace/security bump를 한 transaction에 commit한 뒤 tree를 `partial`로 내린다. Exact no-op은 binding/tree/workspace version을 바꾸지 않고 command/operation/audit/outbox만 확정한다. schema `2026081314`는 create/move/reorder용 immutable structural manifest/effect evidence, order generation, action/scope/proof/raw-SQL guards와 exact result/deep-health validation을 추가한다. schema `2026081315`는 null-safe root manifest/effect authority와 complete structural finalizer authority를 추가한다. schema `2026081316`은 duplicate/Trash/restore canonical action/state, immutable bounded subtree manifest/effect/object mapping과 fail-closed binding/tree transition guards를 추가한다. schema `2026081317`은 complete transitioned/unchanged subtree effect authority와 atomic duplicate/Trash/restore catalog finalization authority를 추가한다. 각 migration은 node, command, source receipt 또는 성공 evidence를 fabricate하지 않는다. NP2C-2a offline initial materializer는 exact sentinel/quiescence 아래서 active attested binding 최대 1,000개를 owner별 source snapshot 2회(full integrity 1회)로 검증하고 state/nodes/operation/audit/outbox를 원자 commit한다. NP2C-3d는 exact sentinel/quiescence 아래 frozen v3 rename adapter와 1313 finalizer를 조합하는 offline rehearsal만 추가한다. NP2C-3e1은 새 source DDL 없이 frozen v3 ledger 위에 route-unregistered create/move/reorder source adapter를 추가했고 3e2는 explicit frozen 1313→1314 catalog guard migration을 추가했다. NP2C-3e3은 page별 logical watermark coalescing을 포함한 source mutation adapter, 1315 atomic finalizer와 exact-sentinel replay/rollback rehearsal을 추가했다. NP2C-4a는 duplicate/Trash/restore와 official Archive 구분, deterministic file convergence, exact cohort/restore placement와 candidate 1316 필요성을 설계 동결했고 NP2C-4b는 같은 frozen v3 ledger 위 route-unregistered duplicate/Trash/restore source adapter와 recoverable file protocol을 추가했다. NP2C-4c는 1316 guard schema/migration을, NP2C-4d는 1317 atomic catalog finalizer/exact-sentinel rehearsal을 추가했다. Route/UI, resolver, live migration, watermark ack와 purge는 없다.
- `notion_legacy_change_migration.py`는 명시한 owner shard에만 secret-free coalesced watermark를 설치한다. page/block/file/public/share/lifecycle 변경은 원 legacy transaction과 함께 기록되고, 상속 share·public·page-tree 영향은 descendant까지 표시한다. source epoch는 keyset batch 사이 drift를 차단하고 full-sweep generation은 relation/mention 같은 간접 의존성을 보수적으로 다시 검사할 근거다.
- `notion_legacy_changes.py`는 quiesce 확인과 exact owner path를 요구하며 한 batch에서 최대 32개 watermark만 읽는다. 첫 batch의 integrity/FK preflight 뒤 cursor는 source identity와 epoch에 묶이며, resume 중 source가 바뀌면 fail-closed한다. 이 reader 자체는 watermark를 ack하거나 catalog를 변경하지 않는다.
- H5a는 새 schema 없이 기존 locator-apply run의 admin lease/CAS와 immutable owner source fence를 consumer foundation으로 재사용한다. page effect operation ID는 source epoch·full-sweep generation·expected binding generation을 포함한 domain-separated hash이고, effect result/audit/outbox에는 identifier-free exact source receipt를 남긴다. effect commit 뒤 checkpoint가 유실되면 현재/직전 binding generation의 ledger를 source resolver보다 먼저 조회해 source I/O 없이 수렴한다. 이는 consumer shadow의 영속 replay 기반일 뿐 watermark DELETE/ack 또는 continuous loop가 아니다.
- H5b legacy source `2026081303`은 owner-local pass/work/operation ledger를 additive하게 설치한다. running pass 하나를 trusted-clock lease/token digest/fencing generation/row-version으로 claim·reclaim하고, exact source identity·epoch·full-sweep generation 아래 최대 32개 watermark/incarnation receipt를 immutable pending work로 materialize한다. latest incarnation은 selected page별 primary-key lookup이며 매 batch 전체 history 집계는 하지 않는다. report는 opaque pass ID와 identifier-free counters만 포함한다.
- H5b v2→v3 migrator는 exact frozen v2 source, dry-run checksum, verified backup, copy-on-write/handle-relative replace를 요구한다. source watermark를 삭제하거나 clear하지 않고 pass/work도 자동 처리하지 않는다. `full_sweep_required`는 후속 catalog sweep obligation이며 completion/ack 증거가 아니다. request/startup/runtime worker 등록과 운영 shard apply는 없다.
- H5c는 source v3 schema를 바꾸지 않고 materialized pending work를 기존 workspace locator-apply effect와 결속한다. catalog effect를 먼저 commit한 뒤 exact source receipt를 durable `applied` terminal row와 immutable operation으로 기록하며, effect→source-terminal 및 source-terminal→catalog-checkpoint crash는 양쪽 ledger replay로 수렴한다. H5e 감사 뒤 catalog generation `superseded`는 같은 source change가 새 binding에 다시 적용돼야 할 수 있으므로 source-terminal로 기록하지 않고 pending으로 보존한다. source transaction을 catalog I/O 동안 유지하지 않는다.
- required full sweep completion은 current workspace admin, completed locator-apply run, exact owner source fence, zero pending work를 모두 요구한다. completion operation은 quiescence/catalog I/O보다 먼저 replay되고, 없으면 다음 pass start가 fail-closed한다. explicit workspace 밖 work는 pending으로 남고 이 증거는 watermark ack나 resolver readiness가 아니다.
- H5d multi-workspace completion은 terminal `applied` work가 참조한 distinct workspace set과 1~32개의 completed locator-run evidence를 exact 일치시킨다. missing/duplicate workspace와 `ignored/no_binding` 또는 `superseded` debt는 completion을 거부한다. lag report는 identifier를 포함하지 않고 pending/superseded와 required-completion debt를 분리하며, process/store restart 뒤 replay와 no-ack repeated-pass duplicate 수렴을 검증했다.
- H5e-0은 latest incarnation마다 current watermark가 존재하고 materialized work보다 current watermark generation/mask가 뒤로 가지 않는 source semantic inspection을 추가했다. candidate source v4 `2026081304`의 stable single ack consumer, immutable ack batch/receipt, exact source fence·incarnation·watermark·catalog-effect·completion 조건, conditional DELETE, crash/restore/redaction gate는 `H5E_CONDITIONAL_ACK_DESIGN.md`와 machine contract에 설계만 고정했다. v4 schema/API와 destructive ack는 미승인·미구현이다.
- actor가 workspace member가 아니면 존재 여부를 노출하지 않는 404이고, member지만 capability가 부족하면 403이다.
- workspace owner와 애플리케이션 전역 `admin/user` role은 별개다.

## 현재 API overlay

등록기는 `routes_notion_workspace.py::register_notion_workspace_routes`이며 `app_notion.py`만 등록한다. `app_code.py`와 `/api/code/notion/*` bridge에는 등록하지 않는다.

별도 overlay 계약은 `docs/notion_workspace_route_contract.json`과 `docs/notion_workspace_route_semantics.json`에 9 rules/15 operations/15 hard operations로 고정한다. 기존 NP-1의 59 rules/61 operations/15 Second Brain hard operations는 수정하지 않고 verifier가 양쪽을 함께 검사한다.

| Method | Path | 용도 |
|---|---|---|
| GET, POST | `/api/notion/workspaces` | actor workspace 목록 / 새 workspace 생성 |
| GET | `/api/notion/workspaces/<workspace_id>` | 최소 workspace context와 역할별 관리 projection |
| GET | `/api/notion/workspaces/<workspace_id>/pages` | 명시적으로 binding된 legacy page의 tenant-scoped read-only 목록 |
| PUT, DELETE | `/api/notion/workspaces/<workspace_id>/members/<subject_id>` | member role 설정 / revoke |
| PUT, DELETE | `/api/notion/workspaces/<workspace_id>/guests/<subject_id>` | guest registry 설정 / revoke |
| POST | `/api/notion/workspaces/<workspace_id>/groups` | group 생성 |
| PUT, DELETE | `/api/notion/workspaces/<workspace_id>/groups/<group_id>/members/<subject_id>` | active member group 구성 |
| GET, POST | `/api/notion/workspaces/<workspace_id>/teamspaces` | visible teamspace 목록 / 생성 |
| PUT, DELETE | `/api/notion/workspaces/<workspace_id>/teamspaces/<teamspace_id>/members/<subject_id>` | teamspace member/owner 설정 / revoke |

모든 unsafe 요청은 기존 Notion host의 cookie authentication, same-origin, CSRF gate를 그대로 사용한다. target subject는 공통 auth registry의 active ID인지 route 경계에서 검증하지만, auth 계정 정보 자체를 catalog에 복제하지 않는다.

## 의도적으로 아직 연결하지 않은 범위

- exact-owner dry-run/backfill 도구의 운영 DB 실행 또는 자동 실행
- workspace-scoped page create/move와 legacy resolver의 locator cutover
- offline/quiesced healing rehearsal과 quarantined binding의 explicit rebind 운영 흐름
- destructive watermark ack와 continuous runtime consumer/worker
- runtime file/public-token resolver cutover
- `_known_workspace_users()` directory scan 제거
- 기존 owner-filter workspace switcher 교체
- navigation UI feature flag enable, view recents와 workspace switcher 운영 사용. NP2B-1/2/3 코드는 default-off다.
- source↔catalog runtime orchestration, official Archive/unarchive source lifecycle, route/UI와 permanent purge (offline initial materializer, 1312 kernel, rename source adapter, 1313 catalog finalizer, exact-sentinel 3d rehearsal, route-unregistered 3e1 create/move/reorder source adapter, 1314 structural guards, 1315 atomic finalizer/rehearsal, 4a design, 4b source adapter/file recovery, frozen 4c 1316 guards와 frozen 4d 1317 duplicate/Trash/restore atomic finalizer/exact-sentinel rehearsal은 구현됨; 모두 runtime 미등록)
- broadest-access capability resolver와 group/teamspace page grants
- guest의 page-specific invitation/share lifecycle
- public subtree projection

따라서 legacy page/share/session API는 그대로 동작하며, 신규 catalog와 legacy DB의 cross-database mutation을 원자적이라고 주장하지 않는다.

## 다음 sub-slice

Core UI의 다음 sub-slice는 `../NOTION_UI_SECOND_BRAIN_XHIGH_HANDOFF_PLAN.md`의 실제 browser golden
journey와 workspace-scoped shell/editor다. 아래 항목은 substrate·production cutover·optional Archive의
잔여 원장이다. NP2C-5a Business/Enterprise characterization은 Archive lane 안에서만 다음 gate이며
Free synthetic UI 개발을 막지 않는다.

exact-owner page inventory/backfill 도구는 `notion_workspace_inventory.py`와
`tools/migrate_notion_workspace_catalog.py`에 구현했다. 기본 동작은 read-only
dry-run이고, 제목·본문·raw public token을 출력하지 않는 deterministic plan을
만든다. Apply는 exact source/catalog path, plan checksum, workspace version,
외부 백업 및 source quiesce 확인을 요구하며 verified catalog backup 뒤에
pending→source revalidation→CAS activation을 페이지별로 수행한다. 부분 실패는
cross-DB atomic으로 포장하지 않고, 새 dry-run과 새 checksum/version 승인을 거쳐
재개한다. 이 도구는 임시 DB에서만 검증했으며 운영 DB에는 실행하지 않았다.

secret-free projection substrate와 동기식 exact-owner projector는
`notion_workspace_store.py`와 `notion_workspace_projection.py`에 구현했다.
Projector는 exact owner DB를 `mode=ro/query_only`로 열고 active page proof를
binding proof와 대조한 뒤, 안전한 regular attachment의 opaque ID와 public token
digest만 CAS replace한다. 파일 path traversal/symlink/junction, stale page proof,
권한 revoke, generation conflict는 write 0으로 실패한다. 이 경로는 명시적 source
quiesce 확인을 요구하는 동기식 내부 경로이며 HTTP resolver가 아니다. 별도의 1305
offline worker가 같은 projection API를 activation event에서 호출하지만 continuous runtime은 아니다.

`notion_workspace_healing.py::heal_one_locator_batch`도 명시적 offline/quiesced
경로다. durable effect replay를 먼저 확인하고 owner별 integrity attestation, source
generation fence와 commit 직전 revalidation을 통과한 page만 apply한다. 영구 source
outcome은 reasoned quarantine과 locator 제거로 수렴하지만 archive restore는 자동으로
active 상태를 되살리지 않는다.

legacy incarnation v2와 catalog 1309의 guarded migrator는
`notion_legacy_incarnation_migration.py`와 `notion_workspace_incarnation_migration.py`에
구현됐다. `notion_workspace_incarnation_orchestration.py`는 exact sentinel과 quiescence를
먼저 확인하고 한 pinned source snapshot에서 receipt·proof·locator를 캡처한 뒤 explicit
attestation을 수행한다. quarantined binding을 owner-canonical proof로 원자적 rebind하는
store API와 동일한 sentinel/path/replay 경계를 제공하는 상위 offline wrapper도 구현됐다.

legacy source의 다음 additive step은
`notion_legacy_change_consumer_migration.py`와
`tools/migrate_notion_legacy_change_consumer.py`에 구현됐다. exact incarnation-v2 source만
받아 frozen source version `2026081303`의 pass/work/operation ledger를 설치하며 dry-run,
verified backup, quiescence와 atomic image replacement를 요구한다. 운영 shard에는 적용하지
않았고 consumer service는 connection-scoped contract일 뿐 request/startup에 등록되지 않는다.

그 다음 destructive 후보인 source v4 `2026081304`는 아직 구현하지 않는다.
`H5E_CONDITIONAL_ACK_DESIGN.md`는 frozen v3를 수정하지 않는 forward migration, 한 owner당
stable logical ack consumer 하나, exact source/work/catalog/completion receipt, immutable ack
evidence와 조건부 watermark DELETE를 정의한다. machine contract는 명시적으로
`proposed_not_authorized`, `implemented=false`, `destructive_ack_enabled=false`다.

guarded schema migration은 `notion_workspace_schema_migration.py`와
`tools/migrate_notion_workspace_schema.py`에 구현했다. dry-run은 exact catalog와 frozen
1303 schema/checksum/object allowlist의 논리 fingerprint를 고정한다. apply는 그 plan
checksum, writer quiesce, 별도 single-link SQLite backup을 요구하고 backup의 integrity,
foreign key와 logical snapshot을 검증한 뒤 `BEGIN IMMEDIATE` 한 transaction에서 1304
projection DDL과 marker를 함께 설치한다. 알 수 없는 1303 변형, symlink/hard-link alias,
source drift와 fault injection은 fail-closed/rollback한다. 이 CLI는 운영 경로에 실행하지
않았으며 request-time migration으로 호출되지 않는다.

후속 `notion_workspace_outbox_migration.py`와
`notion_workspace_healing_migration.py`, `notion_workspace_healing_apply_migration.py`,
`notion_workspace_quarantine_migration.py`, `notion_workspace_incarnation_migration.py`,
`notion_workspace_navigation_migration.py`, `notion_workspace_page_tree_migration.py`,
`notion_workspace_lifecycle_kernel_migration.py`, `notion_workspace_lifecycle_finalize_migration.py`,
`notion_workspace_structural_effect_migration.py`, `notion_workspace_structural_finalize_migration.py`,
`notion_workspace_lifecycle_subtree_migration.py`는 같은 exact-path·frozen-schema/hash·verified
backup·quiesce·single-transaction·ambiguous-commit 수렴 계약으로 각각 1304→1305,
1305→1306, 1306→1307, 1307→1308, 1308→1309, 1309→1310, 1310→1311, 1311→1312, 1312→1313, 1313→1314, 1314→1315, 1315→1316, 1316→1317을 설치한다. 각 CLI는 명시적 dry-run/apply
경계이며 운영 catalog에는 실행하지 않았다.

1. schema `2026081303`→`2026081304`→`2026081305`→`2026081306`→`2026081307`→`2026081308`→`2026081309`→`2026081310`→`2026081311`→`2026081312`→`2026081313`→`2026081314`→`2026081315`→`2026081316`→`2026081317` exact-path·frozen-schema·verified-backup·checksum forward migration과 ambiguous commit outcome 수렴은 구현됐다. request-time 자동 변환은 없고 운영 catalog에는 실행하지 않았다.
2. activation event의 outbox delivery lease/retry/dead-letter와 offline/quiesced projector는 구현됐다. workspace-scoped bounded read-only shadow reconciliation도 구현됐다.
3. 1306 durable run/checkpoint와 1307 source-fenced atomic healing effect는 구현됐다. healing apply는 offline/quiesced batch에서만 호출하며 runtime worker 등록은 없다.
4. 1308 reasoned quarantine과 permanent outcome의 atomic locator 제거, legacy incarnation v2와 1309 immutable attestation/lineage/readiness 및 atomic store-level rebind는 구현됐다. proof actor와 historical binder provenance는 분리되고 exact replay는 shared commitment validator를 통과한다.
5. legacy page/file/public/share/lifecycle 변경을 같은 legacy transaction에서 기록하는 shard-local watermark와 bounded read-only reader는 구현됐다. destructive watermark ack와 continuous runtime consumer는 없다.
6. exact sentinel 아래에서만 실행되는 격리 offline rehearsal과 incarnation attestation/full-rebind orchestration은 정상 stale locator heal, 영구 quarantine의 locator 제거, exact replay의 source-I/O 우회와 identifier-free report를 검증한다. frozen 1303→1317 chain과 retained prior-version snapshot의 다음 migration 재적용도 임시 DB에서 검증한다. NP2C-3d는 같은 경계에서 rename source와 catalog finalizer를 결합해 changed/no-op, 양쪽 response-loss와 catalog rollback을 검증했다. NP2C-3e3은 create/move/reorder source adapter와 1315 finalizer를 결합해 full affected scope, atomic binding/projection/attestation/tree update, source/catalog replay와 rollback을 검증했다. NP2C-4c는 1316 lifecycle-subtree raw guards/evidence schema를 추가했고, NP2C-4d는 1317 atomic duplicate/Trash/restore catalog finalizer와 exact-sentinel identifier-free rehearsal을 추가했다. Runtime, live apply, resolver와 source ack는 여전히 없다.
7. H5a source-fenced effect receipt와 effect-commit/checkpoint-loss replay, H5b source-local durable pass/work/ledger와 bounded non-destructive keyset materialization·lease reclaim, H5c disabled/offline catalog-effect→source-terminal 수렴과 exact full-sweep completion next-pass gate, H5d multi-workspace no-ack/restart/lag rehearsal은 구현됐다. H5e-0은 superseded-pending 의미와 source v4 conditional-ack 설계/machine gate만 고정했다. source v4, source-generation conditional ack와 continuous scheduling은 명시적 승인·fault/restore review 전까지 금지한다. 그 승인이 닫힌 이후에만 legacy resolver를 fail-closed locator resolver로 전환하며 index miss에서 directory scan fallback은 두지 않는다.
8. draft flush와 stale-response fencing이 있는 workspace switcher는 resolver gate 뒤 feature flag 아래 연결한다.
9. NP2B-1은 workspace 1310 sidebar/section preference와 favorite backend를 source DB open/directory scan 0, current-member/current-visibility filter, independent preference/favorite CAS, atomic operation/audit/outbox 계약으로 구현했다. route/UI, live migration과 고빈도 Recents는 별도 gate다.
10. NP2C-1은 workspace 1311 unmaterialized tree state, generation-fenced node와 lifecycle saga schema/deep-health/readiness foundation을 구현했다. NP2C-2a는 exact-source offline initial materializer와 immutable replay/event commitment를 구현했고, NP2C-2b/3b/3c/3d는 1312 disabled command kernel, route-unregistered v3 source rename adapter, 1313 atomic catalog finalizer와 exact-sentinel end-to-end rehearsal을 각각 구현했다. NP2C-3e1/3e2 machine contract v12는 create/move/reorder의 source-v3 action receipts, pre-write bounds, response-loss replay/rollback과 frozen 1314 structural effect/order/proof/deep-health guards를 temporary shard/catalog에서 구현·검증했다. NP2C-3e3 machine contract v13은 frozen 1315 finalization authority, atomic multi-page catalog finalizer와 exact-sentinel source↔catalog rehearsal을 구현·검증했다. NP2C-4a machine contract v14는 duplicate/Trash/restore 설계와 candidate 1316 필요성을 동결했고 NP2C-4b machine contract v15는 route-unregistered source-v3 adapter/file recovery를 temporary shard에서 구현·검증했다. NP2C-4c machine contract v16은 frozen 1316 lifecycle-subtree schema/guards를, NP2C-4d machine contract v17은 frozen 1317 atomic catalog finalizer/exact-sentinel rehearsal을 구현·검증했다. NP2C-4e machine contract v18은 source-ledger-first recovery, catalog three-phase rollback, exact 11,004/11,005 bound, redaction와 deep-health 독립 감사를 닫았다. NP2C-5 machine contract v19는 official Archive를 legacy Trash와 분리하고 H5e source v4 뒤 candidate source v5 `2026081305` 및 workspace `2026081318` 계약을 설계 동결했다. NP2C-5a preparation matrix/runbook, non-authorizing external record inspection, ≤24h authorization preflight와 evidence-bundle verifier는 candidate digest inspection → out-of-band exact user approval → current-time preflight의 순서와 16 scenarios/11 profiles, exact artifact hashes, evidence/no-inference acceptance를 고정했지만 actual approval, live execution과 completion은 없다. 다음 gate는 여전히 `NP2C-5a` 승인된 live characterization이다. Official Archive/unarchive 구현, route/UI, runtime enable과 purge는 아직 별도다.

## Gate

- missing catalog GET의 filesystem diff 0
- create/member/guest/group/teamspace의 CAS, same-operation replay, hash conflict
- unauthorized/no-member 404, insufficient capability 403, disabled/nonexistent subject 거부
- 마지막 workspace/teamspace owner 보존
- guest group/teamspace membership 거부
- cross-workspace placement 거부
- domain/audit/outbox/operation ledger fault rollback
- raw SQL에서도 member/guest·parent membership·sole owner·page-source identity/lifecycle·operation-ledger 불변식 유지
- source stage/activate/quarantine의 CAS와 callback-free exact replay
- bounded request readiness와 명시적 deep-health 분리
- immutable parent outbox와 consumer delivery transition, lease expiry/reclaim, retry/dead-letter, stale token의 write 0
- crash-after-effect-before-ack의 state-idempotent 수렴과 superseded generation의 첫 시도 terminal 처리
- guarded 1303→1304→1305→1306→1307→1308→1309→1310→1311→1312→1313→1314→1315→1316→1317 migration의 source/target/indeterminate commit outcome 분류
- durable healing run의 trusted-clock lease expiry/reclaim, stale token/fence/version write 0, exact checkpoint/effect replay, bounded cursor와 identifier-free counter
- page effect operation identity의 source epoch·full-sweep·binding generation 결속, identifier-free exact receipt ledger, checkpoint-loss source-I/O-free replay
- source pending work의 exact terminal receipt, catalog/source 양쪽 response-loss replay, completed-run full-sweep evidence와 required-sweep next-pass gate
- terminal workspace set과 completed-run set의 exact coverage, identifier-free lag, restart/no-ack repeated-pass duplicate 수렴
- superseded source work의 pending 보존, completion/ack 거부, current watermark coverage·generation/mask regression 탐지
- H5e machine contract의 `proposed_not_authorized`·`implemented=false`·`destructive_ack_enabled=false`
- source-fenced healing의 capture/revalidation drift write 0, permanent outcome의 reasoned quarantine·atomic file/public locator 제거, owner별 integrity preflight 1회
- legacy v2 nonce/history와 1309 attestation lineage, exact readiness/replay commitment, binder/proof-actor 분리
- full-rebind store transaction과 상위 offline wrapper는 구현됐지만 운영 migration·resolver cutover는 미실행
- retained 1310 restore point의 1311 재적용과 fresh/migrated schema equality는 임시 DB에서만 검증
- 1311 migration이 기존 workspace에는 unmaterialized state만 만들고 node/command evidence를 fabricate하지 않음
- exact-owner 인벤토리의 read-only filesystem diff 0, legacy proof 동등성, secret-free deterministic checksum
- apply 전 source/catalog drift·workspace version·backup·source quiesce 확인과 부분 실패 후 무중복 재승인 재개
- projection schema에 raw token·filename·path 0, opaque file ID grammar, global token digest uniqueness, active generation FK/trigger와 secret-free commitment/count
- synchronous projector의 exact-owner/no-scan read, attachment containment, stale proof·revoke·CAS fault write 0, identifier-free shadow counters
- 기존 59 route/61 operation/15 Second Brain hard contract 불변
- Notion-host auth/CSRF 통과, Code host route 미등록
- 기존 Notion, publication/session/template/chat/thought, Knowledge regression 통과
- 운영 `user_data`, Knowledge Core·Second Brain source/data, `app_code.py`, `auth.py`, `config.py` 무변경
