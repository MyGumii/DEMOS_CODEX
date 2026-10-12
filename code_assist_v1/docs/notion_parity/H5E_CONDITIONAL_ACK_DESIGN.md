# H5e legacy watermark conditional acknowledgement 설계

기준 시각: 2026-08-13 Asia/Seoul  
상태: **설계 제안, 미승인, 미구현**  
적용 범위: 격리된 legacy owner shard의 명시적 offline/quiesced maintenance command  
운영 상태: live shard migration, watermark 삭제, continuous worker, resolver/UI cutover 모두 실행하지 않음

이 문서는 H5a~H5d에서 만든 비파괴 source→catalog 수렴 기반 다음에 올 수 있는
destructive acknowledgement의 안전 계약을 고정한다. 구현 권한이나 운영 실행 권한을
부여하지 않는다. 기계 판독 요약은
`docs/notion_parity/h5e_conditional_ack_contract.json`에 있다.

## 1. 결정 요약

1. frozen source v3 `2026081303`을 수정하지 않는다. acknowledgement가 필요하면 별도
   forward-only source v4 `2026081304`로 설계한다.
2. v4 첫 구현은 explicit offline command만 허용한다. continuous scheduling은 그 다음의
   별도 승인 slice다.
3. acknowledgement는 page ID만으로 watermark를 삭제하지 않는다. exact source fence,
   incarnation, watermark generation/mask/state, receipt digest, catalog effect와 필요한
   full-sweep completion을 한 요청에 결속한다.
4. catalog effect가 먼저 durable commit된 뒤 source transaction에서 immutable ack evidence와
   conditional DELETE가 함께 commit된다. 두 DB를 하나의 transaction처럼 주장하지 않는다.
5. 같은 owner shard에는 destructive ack 권한을 가진 stable logical consumer가 하나뿐이다.
   fan-out이 필요하면 watermark를 삭제하기 전에 별도 durable inbox와 consumer별 ack schema를
   설계해야 한다.
6. `superseded`, `ignored/no_binding`, pending, source drift, completion debt는 ack 불가다.
7. acknowledgement는 `source_epoch` 또는 `full_sweep_generation`을 증가시키지 않는다.
   새 legacy mutation만 source generation을 증가시킨다.
8. 실패·응답 유실은 immutable source operation/receipt replay로 수렴하고, public report에는
   owner/workspace/page/path/nonce/digest가 포함되지 않는다.

## 2. 현재 H5a~H5d 기반

### H5a — catalog effect receipt

- workspace schema `2026081309`의 healing lease, fencing generation, owner source fence를 재사용한다.
- page effect operation ID는 run fence/version, source epoch, full-sweep generation, catalog
  binding generation에 결속된다.
- immutable operation/audit/outbox 결과는 identifier-free exact source receipt를 가진다.
- effect commit 후 checkpoint가 유실되면 catalog ledger replay가 source resolver보다 먼저다.

### H5b — source-local coordination v3

- source v3 `2026081303`은 owner-local pass/work/operation ledger를 제공한다.
- 한 consumer의 running pass는 하나이며 trusted clock lease, token digest, fence, row-version
  CAS로 보호된다.
- 고정 source identity/epoch/full-sweep generation 아래 최대 32개의 watermark를 immutable
  work receipt로 materialize한다.
- watermark를 삭제하거나 clear하지 않는다.

### H5c — offline effect→terminal→checkpoint 수렴

- catalog effect가 먼저 commit되고 source terminal receipt가 그 뒤 commit된다.
- 양쪽 response loss는 각 DB의 immutable ledger replay로 수렴한다.
- required full sweep은 completed locator-apply run과 exact owner fence를 증명해야 한다.
- 명시한 workspace에 binding이 없다는 사실만으로 global `no_binding`을 추론하지 않는다.

### H5d — multi-workspace no-ack rehearsal

- 한 owner의 page가 여러 workspace에 걸칠 수 있으므로 completion은 terminal work가 실제로
  참조한 distinct workspace/run set 전체와 exact 일치해야 한다.
- restart 뒤 completion replay가 catalog callback보다 먼저다.
- no-ack 다음 pass는 동일 receipt를 duplicate로 수렴시키고 watermark를 보존한다.

### H5e 감사에서 바로잡은 의미

catalog binding generation의 `superseded`는 coalesced source change가 소멸했다는 뜻이 아니다.
새 binding generation에 같은 source change를 다시 적용해야 할 수 있다. 따라서 현재
orchestration은 `superseded` effect를 source work terminal로 기록하지 않고 pending으로 남긴다.
completion과 lag 계산도 `superseded`를 acknowledgement 불가로 취급한다. source v4는 frozen v3에
이미 terminal `superseded` row가 존재하면 자동 ack하거나 자동 변환하지 않고 fail-closed해야 한다.

## 3. 위협 모델과 비목표

### 보호할 실패

- source mutation과 ack가 경쟁하는 경우
- catalog effect commit 뒤 source ack 전 crash
- source ack commit 뒤 응답 또는 catalog checkpoint 유실
- lease/token/fence가 source I/O 도중 만료되거나 교체되는 경우
- source DB의 path, inode, link count, epoch, full-sweep generation이 달라지는 경우
- 동일 page ID의 delete→recreate, incarnation 변경, archive/restore
- stale watermark generation 또는 mask 일부만 보고 삭제하는 경우
- 여러 workspace 중 일부 run만 완료한 경우
- 다른 consumer가 shared watermark를 먼저 삭제하는 경우
- operation ID 재사용, malformed ledger, raw unconfigured SQLite writer
- backup restore나 DB replacement로 generation/ack history가 되감긴 경우

### 이 slice가 해결하지 않는 것

- 악성 코드가 이미 설정된 application connection과 UDF context를 탈취한 경우
- 여러 independent downstream consumer의 fan-out
- 운영 스케줄러, daemon, startup hook, request-time migration
- resolver, file/public serving, UI, feature flag, canary enable
- source reset/rebase를 자동 승인하는 절차
- live user data migration 또는 실제 사용자 shard ack

## 4. source v4 후보 schema

정확한 SQL과 checksum은 구현 review에서 별도로 동결한다. 다음 이름과 필드는 설계 계약이며,
구현 중 임의 축소해서는 안 된다.

### 4.1 migration lineage

- source migration version: `2026081304`
- candidate name: `notion_legacy_change_ack_v4`
- prerequisite: exact frozen v3 marker/checksum/object hash
- v1→v2→v3 object와 marker는 변경하지 않는다.
- migration은 exact owner/path, quiescence, verified single-link backup, copy-on-write image,
  handle-relative atomic replacement, sidecar absence, ambiguous outcome convergence를 요구한다.
- existing watermark, pass, work, operation을 삭제하거나 acked로 fabricate하지 않는다.

### 4.2 stable ack consumer registry

후보 테이블: `notion_legacy_change_ack_consumer`

- singleton primary key
- canonical consumer name
- registration operation ID와 request hash
- registered timestamp
- immutable UPDATE/DELETE guards
- 두 번째 consumer 등록과 `INSERT OR REPLACE` 거부

registration 자체는 acknowledgement가 아니다. exact owner shard의 destructive ownership을 한
logical consumer에 고정할 뿐이다. consumer 교체는 이 slice에 포함하지 않고 별도 reviewed reset
operation으로 남긴다.

### 4.3 immutable ack batch

후보 테이블: `notion_legacy_change_ack_batches`

- `(consumer, ack_operation_id)` primary key
- source pass ID
- source owner ID
- source epoch, full-sweep generation
- signed device/inode identity와 link count
- item count `1..32`
- required-completion operation ID 또는 `NULL`
- canonical request SHA-256
- identifier-free canonical result JSON
- created/committed timestamp
- immutable UPDATE/DELETE guards
- existing source operation ledger와 exact action/result correlation

한 batch는 source transaction 하나다. response loss 뒤에는 이 batch를 먼저 replay한다.

### 4.4 immutable per-page ack receipt

후보 테이블: `notion_legacy_change_ack_receipts`

- consumer, ack operation ID, pass ID, page ID
- source owner ID
- full source fence: epoch, full sweep, device, inode, link count
- watermark change generation, mask, source-exists/source-active
- incarnation ID, incarnation generation, retired flag
- canonical source receipt SHA-256
- catalog effect workspace ID, binding generation, operation ID, disposition
- full-sweep completion reference when required
- ack timestamp
- batch FK와 original immutable work FK
- exact work receipt당 unique constraint
- immutable UPDATE/DELETE and `OR REPLACE` guards

이 row는 source content, title, path, file name, public token, raw lease token을 저장하지 않는다.

### 4.5 watermark mutation guards

v4는 `notion_page_change_watermarks`의 direct INSERT/UPDATE/DELETE를 guard한다.

- ordinary legacy mutation path는 configured writer의
  `notion_change_tracking_mutation_context()`가 exact integer `1`일 때만 기존 trigger의
  INSERT/UPSERT를 허용한다.
- unconfigured raw writer는 UDF 부재로 fail-closed한다.
- DELETE는 ordinary mutation context에서 항상 거부한다.
- ack DELETE는 `notion_change_consumer_ack_operation_id()`와 stable consumer context가 현재
  transaction의 exact ack batch/receipt와 일치할 때만 허용한다.
- DELETE trigger는 `OLD.page_id/change_generation/change_mask/source_exists`와 receipt를 exact
  비교한다.
- ack service는 context UDF를 transaction 범위에만 설치하고 `finally`에서 fail-closed default로
  복원한다.

SQLite trigger는 trusted application connection에서 임의 SQL을 실행하는 악성 코드까지
격리하지 못한다. 그 위협을 포함하려면 `set_authorizer` 또는 custom connection boundary가 별도
필요하다. v4 최소 계약은 unconfigured/raw writer와 accidental future code를 fail-closed하는 것이다.

### 4.6 successor inspector

frozen v3 inspector의 default no-ack 의미를 약화하지 않는다. v4 inspector는 다음 successor
의미를 별도로 인증한다.

- v1+v2+v3+v4 exact marker/object/trigger allowlist
- latest incarnation마다 current watermark 또는 exact latest ack receipt가 하나 이상 존재
- watermark와 ack receipt가 함께 있으면 watermark가 ack 이후의 새 mutation임을 source epoch와
  receipt로 증명
- applied work가 ack됐다면 exact ack receipt와 operation이 존재
- pending/ignored/superseded work에는 ack receipt가 없음
- ack receipt보다 current watermark generation/mask/state가 뒤로 가지 않음
- full-sweep-required batch는 exact single/set completion evidence를 참조
- operation/result/request hash가 canonical byte equality를 만족
- watermark DELETE 수와 ack receipt 수가 exact 일치

v4 reader는 watermark가 없는 settled page를 곧바로 corrupt로 보지 않고 valid ack receipt를
successor evidence로 인식해야 한다. 기존 v1/v2/v3 reader의 default 계약은 그대로 둔다.

## 5. acknowledgement eligibility

ack batch의 모든 page가 아래 조건을 만족해야 한다. 한 항목이라도 실패하면 transaction 전체를
rollback하고 watermark를 하나도 삭제하지 않는다.

1. caller가 explicit destructive-ack approval과 source quiescence를 제공했다.
2. exact operation replay가 없고, registered logical consumer가 caller consumer와 같다.
3. source path가 승인 sentinel 아래의 exact owner shard이며 regular single-link file이다.
4. current DB identity가 pass의 device/inode/link count와 같다.
5. source state의 epoch/full-sweep generation이 pass와 exact 일치한다.
6. pass가 `materialized`이고 work materialization이 끝났다.
7. pass에 pending, ignored, superseded work가 없다.
8. 선택한 work status는 `applied`이며 catalog disposition이 ack allowlist에 있다.
9. work의 immutable page/incarnation/watermark/source receipt가 current watermark와 exact 같다.
10. current incarnation은 work receipt와 exact 같고 lifecycle state도 일치한다.
11. `full_sweep_required=true`이면 exact single/multi-workspace completion evidence가 존재한다.
12. completion의 workspace/run set이 applied work가 참조한 distinct workspace set과 같다.
13. catalog effect operation/result/source receipt가 source work receipt와 exact 같다.
14. operation ID와 request hash가 full fence와 모든 selected receipt를 commit한다.
15. batch size는 `1..32`이고 page/work 순서는 canonical하다.

ack allowlist는 catalog에서 실제로 durable effect를 commit한 disposition만 포함한다.
`superseded`, `no_binding`, permission/unsafe/ambiguous/oversize를 단순 counter로만 처리한 결과는
절대 allowlist에 넣지 않는다. reasoned quarantine처럼 locator 제거가 catalog transaction에
durable하게 commit된 결과만 별도 reviewed disposition으로 허용할 수 있다.

## 6. stable operation identity

operation ID는 domain-separated hash로 만들며 최소한 다음을 포함한다.

- contract domain/version
- consumer
- source owner
- source epoch/full-sweep generation
- source device/inode/link count
- pass ID
- sorted list of page ID, change generation, change mask, source state, incarnation ID/generation,
  receipt SHA-256, catalog effect workspace/binding/operation/disposition
- required completion operation ID

새 mutation은 watermark generation이 다시 `1`에서 시작할 수 있다. source epoch와 incarnation,
receipt를 포함하지 않은 `(owner,page,generation)` operation ID는 충돌하므로 금지한다.

## 7. transaction protocol

### 7.1 pre-I/O replay

1. scalar/grammar/batch-limit validation
2. immutable ack batch operation lookup
3. current operator authorization을 거친 exact replay validation
4. replay면 source resolver, sentinel, DB open 없이 identifier-free prior result 반환

### 7.2 read/capture phase

1. destructive approval과 quiescence 확인
2. sentinel/root/owner/exact path 확인
3. source v4 contract full integrity preflight를 batch/session당 한 번 수행
4. pass/work/completion/catalog-effect inputs를 read-only로 검증
5. canonical batch request와 operation ID 계산

catalog read는 source write transaction 밖에서 수행한다. cross-DB lock을 중첩하지 않는다.

### 7.3 source commit phase

`BEGIN IMMEDIATE` 뒤 다음을 다시 확인한다.

1. v4 contract와 registered consumer
2. source identity/state fence
3. pass/work 상태와 exact receipt
4. current watermark exact equality
5. completion/operation correlation
6. source clock과 ack context

그 뒤 같은 transaction에서 다음 순서로 쓴다.

1. immutable ack operation/batch insert 또는 exact conflict 확인
2. page별 immutable ack receipt insert
3. page별 `DELETE FROM notion_page_change_watermarks WHERE page_id=? AND
   change_generation=? AND change_mask=? AND source_exists=?`
4. affected row가 각 page마다 exact `1`인지 확인
5. contract semantic postcheck
6. commit

ack는 change-tracking mutation이 아니므로 source epoch/full-sweep generation을 수정하지 않는다.
source mutation이 먼저 commit되면 exact recheck/DELETE가 실패한다. ack가 먼저 commit되면 뒤의
source mutation trigger가 새 watermark를 다시 만든다.

### 7.4 response

public result는 다음 정도의 identifier-free 값만 허용한다.

- `acknowledged_count`
- `replayed`
- `pending_count`
- `full_sweep_required`
- `full_sweep_completed`
- `lagging`
- stable sanitized status/failure code

## 8. crash/fault matrix

| Fault point | Expected durable state | Retry behavior |
|---|---|---|
| catalog effect 전 | source work pending, watermark 존재 | effect부터 재시도 |
| catalog effect commit 후 source terminal 전 | catalog ledger만 존재 | source I/O 전 effect replay, terminal 기록 |
| source terminal 후 completion 전 | applied work, watermark 존재 | completion 또는 잔여 effect 진행 |
| completion 전 response loss | completion 없음 또는 commit 여부 불명 | source ledger로 exact convergence |
| ack operation insert 전 | watermark/ack receipt 변화 없음 | 전체 ack 재시도 |
| ack operation/receipt insert 중 fault | transaction rollback | receipt 0, DELETE 0 |
| 일부 DELETE 뒤 fault | transaction rollback | 모든 watermark 복구, receipt 0 |
| commit 뒤 response loss | receipt와 DELETE 모두 durable | pre-I/O replay, source open 0 |
| concurrent source mutation 먼저 commit | generation/fence mismatch | DELETE 0, 새 pass 필요 |
| ack commit 뒤 source mutation | ack receipt 유지, 새 watermark 생성 | 다음 pass가 새 epoch receipt 처리 |
| lease/fence expiry | write 0 | fresh lease/pass로 재시도 |
| DB/path/identity replacement | fail-closed | explicit restore/reset audit 필요 |

필수 fault injection은 operation insert, receipt insert, first/middle/last delete, semantic postcheck,
before commit, commit-return ambiguity, after commit/response loss를 포함한다.

## 9. backup, restore, downgrade

- destructive ack 전 exact source backup과 restore rehearsal이 필수다.
- v4 apply/ack는 sidecar가 없는 quiesced source에서만 허용한다.
- pre-ack backup으로 restore하면 source ack history와 watermark가 함께 되감긴다. catalog effect는
  별도 DB에 남을 수 있으므로 자동 재개하지 않고 source identity/generation lineage conflict로
  fail-closed한다.
- 같은 inode의 raw rollback도 source epoch, full-sweep, incarnation, receipt 또는 catalog fence
  regression으로 거부해야 한다.
- source reset/rebase는 별도 stronger audited operation이다. H5e에 포함하지 않는다.
- v4 ack 뒤 v3 binary로 downgrade하지 않는다. v3 default inspector는 ack로 사라진 watermark를
  invalid로 보는 것이 정상이다. rollback은 verified restore 또는 forward fix다.

## 10. boundedness와 운영 지표

- batch `1..32`, SQL은 key/covering index를 사용한다.
- full integrity/FK check는 page마다 반복하지 않고 owner session/pass 시작에 한 번 수행한다.
- page별 catalog callback은 ack transaction 전에 끝낸다.
- source writer lock 아래 network/filesystem/catalog I/O를 하지 않는다.
- 최소 지표: materialized, pending, applied, superseded, ignored, completion debt, ack eligible,
  acknowledged, replayed, source drift, lease conflict, restore/reset required.
- 로그와 report는 identifier-free다. page/workspace/owner/path/nonce/digest는 내부 evidence에만 두고
  일반 telemetry에 내보내지 않는다.

## 11. 구현 slice와 승인 gate

### H5e-0 — 현재 v3 invariant freeze

- [x] `superseded` effect를 source-terminal로 만들지 않음
- [x] completion/lag에서 ignored/superseded ack 불가
- [x] latest incarnation↔watermark coverage와 work≤current-watermark semantic inspection
- [x] focused/legacy source regression 재검증
- [ ] 이 문서와 machine contract review 승인

### H5e-1 — source v4 schema/migrator, 여전히 no-ack

- frozen v3→v4 migration, exact hashes, backup/replace/restore
- ack tables/guards/inspector 설치
- existing watermark/pass/work byte preservation
- raw writer negative tests
- terminal superseded v3 fixture는 fail-closed 또는 별도 reviewed repair 요구
- ack API는 아직 호출 불가/feature disabled

### H5e-2 — explicit offline ack API

- exact replay-first source store API
- batch≤32 conditional ack transaction
- full fault/response-loss/concurrency/restore tests
- identifier-free result/redaction tests
- no scheduler, no route, no startup import

### H5e-3 — sanitized snapshot rehearsal

- v1→v2→v3→v4 migration
- no-ack baseline→effect/completion→ack→new mutation→new watermark
- multi-workspace exact completion set
- crash/restore/source replacement drill
- rollback/forward-fix runbook

### H5e-4 — 운영 실행 승인, 기본 거부

필수 승인 주체와 evidence가 없으면 실행하지 않는다.

- 데이터 소유자/운영자 승인
- exact owner shard allowlist
- current verified backup와 restore timing
- source write quiescence
- dry-run diff와 expected counts
- catalog run/effect/completion evidence
- one logical consumer registration
- kill switch와 on-call owner
- redacted audit artifact

### H5f — continuous scheduling, 별도 프로젝트

H5e가 통과해도 continuous worker를 자동 승인하지 않는다. scheduler에는 process lifecycle,
backoff, fairness, clock, shard discovery 금지/allowlist, telemetry, kill switch, service account,
upgrade/downgrade와 host restart evidence가 추가로 필요하다.

## 12. 검증 목록

### schema/contract

- fresh v4와 migrated v4 exact object equality
- v3 literals 불변
- partial/rogue/future schema 거부
- no fabricated consumer/batch/receipt
- direct UPDATE/DELETE/OR REPLACE/FK-off/UDF-missing 거부
- exact trigger allowlist와 deep semantic inspector

### eligibility

- applied exact receipt만 성공
- pending/ignored/superseded/no-binding 거부
- source epoch/full-sweep/device/inode/link drift 거부
- generation/mask/state/incarnation/receipt drift 거부
- required completion 누락/partial workspace set 거부
- catalog effect ledger tamper 거부
- second consumer 거부

### idempotency/concurrency

- same operation exact replay source I/O 0
- same operation changed request conflict
- source mutation before/during/after ack
- ack commit/response loss
- process restart replay
- new mutation의 generation reset `1`이 이전 ack와 충돌하지 않음

### privacy/bounds

- 0/33 batch fail-before-path
- ≤32 exact rows
- public result/log에 owner/workspace/page/path/title/file/token/nonce/digest 0
- per-page integrity scan 0
- database busy/latency/lag counters

### regression

- legacy source v1/v2/v3 readers/migrations
- H5a~H5d effect/completion/restart/no-ack
- workspace 1303→1309 chain
- projection/healing/quarantine/incarnation/rebind/rehearsal
- Notion route/auth/publication/editor contracts
- Second Brain/Knowledge hard boundary after fixture isolation confirmation

## 13. stop/no-go 조건

다음 중 하나라도 참이면 구현 또는 실행을 중단한다.

- user/operations의 destructive ack 명시 승인이 없음
- `AGENTS.md` 또는 다른 개발자의 shared file ownership 충돌
- live/relative/UNC/reparse path 또는 sentinel 밖 DB
- source v3/v4 frozen checksum/object mismatch
- backup/restore proof 없음
- quiescence 또는 exact file identity 불명
- pending/ignored/superseded/no-binding work 존재
- partial workspace completion
- catalog/source receipt mismatch
- secret/identifier가 public evidence로 유출
- request/startup/runtime 등록이 필요해짐
- Git provenance 부재를 무시하고 commit/landed를 주장해야 함

## 14. xhigh 인계 시 첫 실행 순서

1. `code_assist_v1/AGENTS.md`, active goal, xhigh handoff, 이 문서를 다시 읽는다.
2. 다른 개발자의 file ownership과 핵심 source/doc SHA-256를 재확인한다.
3. H5e-0 focused lane을 serial 재실행한다.
4. machine contract에서 `authorized=false`, `implemented=false`를 확인한다.
5. destructive ack 승인이 없으면 H5e-1 code를 시작하지 않는다.
6. 승인이 있더라도 먼저 v4 schema/migrator no-ack slice만 구현한다.
7. schema/fault/restore gate 뒤 별도 review에서 H5e-2 offline API를 허가한다.
8. live shard, continuous worker, resolver/UI는 계속 건드리지 않는다.

## 15. 완료 정의

이 문서 작성과 verifier 통과는 H5e 완료가 아니다. H5e는 다음이 모두 충족될 때만
`contract-tested` 후보가 된다.

- reviewed v4 frozen schema/migration
- exact offline ack API와 crash/restore evidence
- current-tree source/workspace/route/Second Brain regressions
- identifier-free evidence와 operator runbook
- explicit destructive-operation approval

운영 shard에서 실행하고 canary/soak/restore까지 완료하기 전에는 `live-characterized`가 아니며,
공식 Notion과 비교 evidence가 없으므로 `differential-passed`도 아니다.
